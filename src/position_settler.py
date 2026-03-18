"""
Position Settler for Polymarket Trading Bot.

This module handles:
- Fetching open positions from CLOB API
- Checking market resolution status
- Redeeming winning tokens for USDC
- Logging P&L to CSV

Usage (standalone):
    # Run once and exit
    uv run python -m src.position_settler --once

    # Continuous mode (every 5 minutes)
    uv run python -m src.position_settler --daemon

    # Custom check interval (seconds)
    uv run python -m src.position_settler --daemon --interval 300

    # Resolve dry-run positions
    uv run python -m src.position_settler --resolve-dryrun --live
"""

import argparse
import asyncio
import csv
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    AssetType,
    BalanceAllowanceParams,
    MarketOrderArgs,
    OrderType,
    TradeParams,
)
from py_clob_client.order_builder.constants import SELL

from src.trading.trade_db import TradeDatabase


def _create_clob_client(logger: logging.Logger) -> ClobClient:
    """Create and configure a CLOB client from environment variables.

    Raises SystemExit if PRIVATE_KEY is missing.
    """
    from dotenv import load_dotenv

    load_dotenv()

    private_key = os.getenv("PRIVATE_KEY")
    chain_id = int(os.getenv("POLYGON_CHAIN_ID", "137"))
    host = os.getenv("CLOB_HOST", "https://clob.polymarket.com")
    funder = os.getenv("POLYMARKET_PROXY_ADDRESS")

    if not private_key:
        logger.error("Missing PRIVATE_KEY in .env")
        sys.exit(1)

    if "clob.polymarket.com" not in host:
        logger.warning(
            "CLOB_HOST should be https://clob.polymarket.com (overriding)"
        )
        host = "https://clob.polymarket.com"

    signature_type = 2 if funder else 0

    client = ClobClient(
        host=host,
        key=private_key,
        chain_id=chain_id,
        signature_type=signature_type,
        funder=funder or "",
    )

    api_creds = client.create_or_derive_api_creds()
    client.set_api_creds(api_creds)

    logger.info(f"CLOB client initialized ({host})")
    if funder:
        logger.info(f"  Proxy wallet: {funder}")

    return client


class PositionSettler:
    """
    Handles position settlement and P&L tracking.

    Features:
    - Fetch open positions via CLOB API
    - Check market resolution status
    - Redeem winning tokens
    - Log P&L to CSV
    """

    def __init__(
        self,
        dry_run: bool = True,
        *,
        logger: Optional[logging.Logger] = None,
        client: Optional[ClobClient] = None,
        trade_db: Optional[Any] = None,
    ):
        """
        Initialize position settler.

        Args:
            dry_run: If True, don't execute redeem operations (default: True)
            logger: Pre-configured logger. If None, creates one via logging_config.
            client: Pre-configured ClobClient. If None and not dry_run, creates one.
            trade_db: TradeDatabase instance for dry-run position lookups. If None,
                      creates a temporary one when needed.
        """
        self.dry_run = dry_run
        self._trade_db = trade_db
        # Fix 3: cache token_ids that returned 404 or are confirmed losers; token_id -> expiry
        self._stale_tokens: dict[str, float] = {}

        # Logger: use provided or create via centralized config
        if logger is not None:
            self.logger = logger
        else:
            from src.logging_config import setup_logger
            self.logger = setup_logger("settler", "settler.log", console_prefix="[SETTLER]")

        # Client: use provided or create (skip in dry-run)
        if client is not None:
            self.client = client
        elif not dry_run:
            try:
                self.client = _create_clob_client(self.logger)
            except Exception as e:
                self.logger.error(f"Failed to initialize CLOB client: {e}", exc_info=True)
                sys.exit(1)
        else:
            self.logger.info("Dry run mode: Skipping CLOB client initialization")
            self.client = None

        self.logger.info("=" * 80)
        self.logger.info("Position Settler Initialized")
        self.logger.info("=" * 80)
        self.logger.info(f"Mode: {'DRY RUN' if self.dry_run else '🔴 LIVE 🔴'}")
        self.logger.info("=" * 80)

    async def get_open_positions(self) -> list[dict[str, Any]]:
        """
        Fetch all open positions by:
        1. Getting trade history from CLOB API
        2. Extracting token_ids from trades
        3. Checking balance for each token via get_balance_allowance()
        4. Getting current prices to identify sellable positions

        Returns:
            List of position dicts with token_id, balance, market price, etc.
        """
        try:
            self.logger.info("Fetching open positions...")

            if self.dry_run:
                self.logger.info("Dry run mode: Simulating positions check")
                return []

            if self.client is None:
                self.logger.error("Client not initialized")
                return []

            proxy = os.getenv("POLYMARKET_PROXY_ADDRESS")
            address = proxy or self.client.get_address() or ""
            self.logger.debug(f"Fetching trades for address: {address}")
            trades = await asyncio.to_thread(
                self.client.get_trades,
                params=TradeParams(maker_address=address),
            )

            if not trades:
                self.logger.info("No trade history found")
                return []

            self.logger.info(f"Found {len(trades)} historical trades")

            # Step 2: Extract unique token_ids from recent BUY orders only.
            # Historical trades for resolved markets generate 404s on balance
            # queries — skip any trade older than RECENT_TRADE_DAYS.
            RECENT_TRADE_DAYS = 30
            recent_cutoff = time.time() - RECENT_TRADE_DAYS * 86400

            token_ids: set[str] = set()
            token_entry_prices: dict[str, list[float]] = {}
            token_condition_map: dict[str, str] = {}  # token_id -> condition_id
            skipped_old = 0
            for trade in trades:
                if trade.get("side") == "BUY":
                    # Filter out old trades — try several timestamp field names
                    trade_ts = trade.get("created_at") or trade.get("timestamp") or ""
                    if trade_ts:
                        try:
                            if isinstance(trade_ts, (int, float)):
                                ts_epoch = float(trade_ts)
                                # Handle millisecond timestamps
                                if ts_epoch > 1e12:
                                    ts_epoch /= 1000
                            else:
                                ts_epoch = datetime.fromisoformat(
                                    str(trade_ts).replace("Z", "+00:00")
                                ).timestamp()
                            if ts_epoch < recent_cutoff:
                                skipped_old += 1
                                continue
                        except Exception:
                            pass  # unparseable timestamp — include the trade

                    token_id = trade.get("asset_id")
                    if token_id:
                        token_ids.add(token_id)
                        cid = trade.get("market", "")
                        if cid and token_id not in token_condition_map:
                            token_condition_map[token_id] = cid
                        price = float(trade.get("price", 0) or 0)
                        if price > 0:
                            token_entry_prices.setdefault(token_id, []).append(price)

            if skipped_old:
                self.logger.info(
                    f"Skipped {skipped_old} buy trades older than {RECENT_TRADE_DAYS}d "
                    f"(resolved markets)"
                )
            self.logger.info(f"Tracking {len(token_ids)} unique tokens from recent buy orders")

            positions = []
            _stale_ttl = 86400  # 24 h
            _now = time.time()
            for token_id in token_ids:
                # Fix 3: skip tokens cached as stale/resolved-loser
                stale_until = self._stale_tokens.get(token_id, 0)
                if stale_until > _now:
                    self.logger.debug(
                        f"Skipping stale/resolved token {token_id} "
                        f"(cached for {int((stale_until - _now) / 3600)}h more)"
                    )
                    continue

                try:
                    balance_info_raw = await asyncio.to_thread(
                        self.client.get_balance_allowance,
                        params=BalanceAllowanceParams(
                            asset_type=AssetType.CONDITIONAL,  # type: ignore
                            token_id=token_id,
                        ),
                    )
                    balance_info: dict[str, Any] = balance_info_raw  # type: ignore

                    balance = float(balance_info.get("balance", 0))

                    if balance > 0.01:
                        try:
                            price_info_raw = await asyncio.to_thread(
                                self.client.get_price, token_id, "BUY"
                            )
                            price_info: dict[str, Any] = price_info_raw  # type: ignore
                            current_price = float(price_info.get("price", 0))
                        except Exception as e:
                            self.logger.warning(
                                f"Failed to get price for {token_id}: {e}"
                            )
                            current_price = 0.0

                        # Fix 3 + Fix 1: price=0 on a resolved market → check if loser
                        if current_price == 0.0:
                            condition_id = token_condition_map.get(token_id, "")
                            losing = await self._is_losing_resolved_token(token_id, condition_id)
                            if losing:
                                self.logger.info(
                                    f"Skipping losing resolved token {token_id} "
                                    f"(condition {condition_id})"
                                )
                                self._stale_tokens[token_id] = _now + _stale_ttl
                                continue

                        prices = token_entry_prices.get(token_id, [])
                        avg_entry = sum(prices) / len(prices) if prices else 0.0
                        positions.append(
                            {
                                "token_id": token_id,
                                "condition_id": token_condition_map.get(token_id, ""),
                                "balance": balance,
                                "current_price": current_price,
                                "estimated_value": balance * current_price,
                                "entry_price": avg_entry,
                            }
                        )

                        self.logger.info(
                            f"  Position: {balance:.2f} tokens @ ${current_price:.3f} (~${balance * current_price:.2f})"
                        )

                except Exception as e:
                    err_str = str(e)
                    self.logger.warning(f"Failed to check balance for {token_id}: {e}")
                    # Fix 3: cache 404/not-found errors to avoid repeated queries
                    if "404" in err_str or "not found" in err_str.lower():
                        self._stale_tokens[token_id] = _now + _stale_ttl
                    continue

            self.logger.info(f"Found {len(positions)} open positions with balance > 0")
            return positions

        except Exception as e:
            self.logger.error(f"Error fetching positions: {e}", exc_info=True)
            return []

    async def _is_losing_resolved_token(self, token_id: str, condition_id: str) -> bool:
        """Return True if token_id is the losing side of a resolved market.

        Calls the Polymarket CLOB REST API. Returns False on any error so
        we never accidentally discard a winning token.
        """
        if not condition_id:
            return False
        try:
            import requests as _req
            r = await asyncio.to_thread(
                lambda: _req.get(
                    f"https://clob.polymarket.com/markets/{condition_id}",
                    timeout=5,
                )
            )
            if not r.ok:
                return False
            data = r.json()
            if not data.get("closed") or not data.get("outcome"):
                return False  # market not yet resolved
            winning_outcome = data["outcome"]
            for t in data.get("tokens", []):
                if t.get("outcome", "").lower() == winning_outcome.lower():
                    # Found the winning token — if it's not ours, we lost
                    return t.get("token_id") != token_id
        except Exception as e:
            self.logger.debug(f"_is_losing_resolved_token check failed for {token_id}: {e}")
        return False

    async def sell_position_if_profitable(
        self, position: dict[str, Any]
    ) -> dict[str, Any] | None:
        """
        Sell position if current price >= 0.999 (profitable exit).

        Args:
            position: Position dict with token_id, balance, current_price

        Returns:
            Sell transaction info if successful, None otherwise
        """
        token_id = position["token_id"]
        balance = position["balance"]
        current_price = position["current_price"]

        SELL_THRESHOLD = 0.999

        if current_price < SELL_THRESHOLD:
            self.logger.debug(
                f"Price ${current_price:.4f} below sell threshold ${SELL_THRESHOLD:.4f} - holding"
            )
            return None

        self.logger.info(
            f"💰 Price ${current_price:.4f} >= ${SELL_THRESHOLD:.4f} - SELLING {balance:.2f} tokens"
        )

        if self.dry_run:
            self.logger.info(
                f"DRY RUN: Would sell {balance:.2f} tokens @ ${current_price:.4f} (~${balance * current_price:.2f} revenue)"
            )
            return {"status": "dry_run", "token_id": token_id, "price": current_price}

        try:
            if self.client is None:
                self.logger.error("Client not initialized")
                return None

            order_args = MarketOrderArgs(
                token_id=token_id,
                amount=balance,
                side=SELL,
                order_type=OrderType.FOK,  # type: ignore
            )

            signed_order = await asyncio.to_thread(
                self.client.create_market_order, order_args
            )
            result_raw = await asyncio.to_thread(
                self.client.post_order,
                signed_order,
                orderType=OrderType.FOK,  # type: ignore
            )
            result: dict[str, Any] = result_raw  # type: ignore

            if result.get("success") or result.get("orderID"):
                self.logger.info(
                    f"✅ Successfully sold {balance:.2f} tokens @ ${current_price:.4f} (~${balance * current_price:.2f})"
                )
                entry_price_val = (
                    position.get("entry_price")
                    or await self._lookup_entry_price_from_db(position)
                    or 0.0
                )
                pnl = self.calculate_pnl(
                    position,
                    entry_price=entry_price_val,
                    exit_price=current_price,
                )
                await self.log_pnl_to_csv(
                    position=position,
                    pnl=pnl,
                    condition_id=position.get("condition_id", "N/A"),
                    market_title=position.get("market_title", "N/A"),
                )
                return result
            else:
                self.logger.warning(f"Failed to sell position: {result}")
                return None

        except Exception as e:
            self.logger.error(f"Error selling position {token_id}: {e}", exc_info=True)
            return None

    async def check_market_resolution(self, condition_id: str) -> str | None:
        """
        Check if market is resolved and get winning outcome.

        Args:
            condition_id: Market condition ID

        Returns:
            Winning outcome index ("0" or "1") if resolved, None if pending
        """
        try:
            if self.client is None:
                self.logger.error("Client not initialized")
                return None

            market_info_raw = await asyncio.to_thread(
                self.client.get_market, condition_id
            )
            market_info: dict[str, Any] = market_info_raw  # type: ignore

            if not market_info:
                self.logger.warning(f"Market {condition_id} not found")
                return None

            closed = market_info.get("closed", False)
            outcome = market_info.get("outcome")

            if closed and outcome is not None:
                self.logger.info(
                    f"Market {condition_id} resolved with outcome: {outcome}"
                )
                return str(outcome)
            else:
                self.logger.debug(f"Market {condition_id} not yet resolved")
                return None

        except Exception as e:
            self.logger.error(
                f"Error checking market resolution for {condition_id}: {e}",
                exc_info=True,
            )
            return None

    async def redeem_position(
        self, token_id: str, condition_id: str
    ) -> dict[str, Any] | None:
        """
        Redeem winning tokens for USDC.

        Args:
            token_id: Token ID to redeem
            condition_id: Market condition ID

        Returns:
            Redemption transaction info if successful, None otherwise
        """
        if self.dry_run:
            self.logger.info(f"DRY RUN: Would redeem token {token_id}")
            return {"status": "dry_run", "token_id": token_id}

        try:
            self.logger.info(f"Redeeming token {token_id} for condition {condition_id}")

            if self.client is None:
                self.logger.error("Client not initialized")
                return None

            result = await asyncio.to_thread(self.client.redeem_position, token_id)  # type: ignore

            if result:
                self.logger.info(f"Successfully redeemed token {token_id}")
                return result
            else:
                self.logger.warning(f"Redemption failed for token {token_id}")
                return None

        except Exception as e:
            self.logger.error(f"Error redeeming token {token_id}: {e}", exc_info=True)
            return None

    def calculate_pnl(
        self,
        position: dict[str, Any],
        entry_price: float = 0.99,
        exit_price: float = 1.0,
    ) -> dict[str, float]:
        """
        Calculate P&L for a position.

        Args:
            position: Position dict from API
            entry_price: Assumed entry price (default: 0.99)
            exit_price: Realized exit price (default: 1.00)

        Returns:
            Dict with cost, exit_value, profit_loss, roi_percent
        """
        size = float(position.get("size", position.get("balance", 0)))
        cost = size * entry_price
        exit_value = size * exit_price

        profit_loss = exit_value - cost
        roi_percent = (profit_loss / cost * 100) if cost > 0 else 0.0

        return {
            "tokens": size,
            "entry_price": round(entry_price, 6),
            "cost": round(cost, 2),
            "exit_value": round(exit_value, 2),
            "profit_loss": round(profit_loss, 2),
            "roi_percent": round(roi_percent, 2),
        }

    async def log_pnl_to_csv(
        self,
        position: dict[str, Any],
        pnl: dict[str, float],
        condition_id: str,
        market_title: str = "N/A",
    ):
        """
        Log P&L data to CSV file.

        Args:
            position: Position dict
            pnl: P&L calculation dict
            condition_id: Market condition ID
            market_title: Market title
        """
        log_dir = Path("log")
        log_dir.mkdir(exist_ok=True)
        csv_path = log_dir / "pnl.csv"

        file_exists = csv_path.exists()

        try:
            with open(csv_path, "a", newline="") as csvfile:
                fieldnames = [
                    "timestamp",
                    "market_title",
                    "condition_id",
                    "token_id",
                    "side",
                    "tokens_bought",
                    "entry_price",
                    "cost",
                    "exit_value",
                    "profit_loss",
                    "roi_percent",
                ]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

                if not file_exists:
                    writer.writeheader()

                writer.writerow(
                    {
                        "timestamp": datetime.now(timezone.utc).strftime(
                            "%Y-%m-%d %H:%M:%S UTC"
                        ),
                        "market_title": market_title,
                        "condition_id": condition_id,
                        "token_id": position.get("token_id")
                        or position.get("asset_id", "N/A"),
                        "side": position.get("side", "UNKNOWN"),
                        "tokens_bought": pnl["tokens"],
                        "entry_price": pnl.get("entry_price", 0.99),
                        "cost": pnl["cost"],
                        "exit_value": pnl["exit_value"],
                        "profit_loss": f"{'+' if pnl['profit_loss'] >= 0 else ''}{pnl['profit_loss']}",
                        "roi_percent": f"{'+' if pnl['roi_percent'] >= 0 else ''}{pnl['roi_percent']}%",
                    }
                )

            self.logger.info(f"P&L logged to {csv_path}")

        except Exception as e:
            self.logger.error(f"Error logging P&L to CSV: {e}", exc_info=True)

    async def _lookup_entry_price_from_db(self, position: dict) -> float:
        """Look up average BUY price from local DB trades table using condition_id.

        Returns 0.0 if condition_id is missing or no trades found.
        """
        condition_id = position.get("condition_id", "")
        if not condition_id:
            return 0.0
        try:
            for db_path in self._get_db_paths():
                try:
                    _db = await TradeDatabase.initialize(db_path)
                    try:
                        price = await _db.get_avg_entry_price_for_condition(condition_id)
                        if price > 0:
                            self.logger.debug(
                                f"DB entry_price lookup: {condition_id} → {price:.4f} ({db_path})"
                            )
                            return price
                    finally:
                        await _db.close()
                except Exception as e:
                    self.logger.debug(f"DB lookup error for {db_path}: {e}")
        except Exception as e:
            self.logger.debug(f"entry_price DB lookup failed: {e}")
        return 0.0

    async def process_positions(self):
        """
        Main processing loop:
        1. Check all open positions
        2. Try to sell if price >= 0.999 (near certain win)
        3. Otherwise hold for market resolution and claim
        """
        self.logger.info("Starting position processing...")

        positions = await self.get_open_positions()

        if not positions:
            self.logger.info("No positions to process")
            return

        self.logger.info(f"Processing {len(positions)} position(s)...")

        processed = 0
        sold = 0
        held = 0

        for position in positions:
            try:
                token_id = position.get("token_id")
                balance = position.get("balance", 0)
                current_price = position.get("current_price", 0)

                if not token_id or balance <= 0:
                    self.logger.warning(f"Invalid position data: {position}")
                    continue

                self.logger.info(
                    f"Position {processed + 1}/{len(positions)}: {balance:.2f} tokens @ ${current_price:.4f}"
                )

                sell_result = await self.sell_position_if_profitable(position)

                if sell_result:
                    sold += 1
                    self.logger.info("✅ Position sold profitably")
                else:
                    held += 1
                    self.logger.info(
                        f"📊 Holding position (price ${current_price:.4f} < $0.999 threshold)"
                    )

                processed += 1

            except Exception as e:
                self.logger.error(f"Error processing position: {e}", exc_info=True)
                continue

        self.logger.info(
            f"Summary: Processed {processed} position(s) - Sold: {sold}, Held: {held}"
        )

        # Also check dry-run position resolution
        await self.check_dryrun_resolution()

    @staticmethod
    def _get_db_paths() -> list[str]:
        """Return list of DB paths to scan.

        Priority:
        1. SETTLER_DB_PATHS env var (comma-separated)
        2. Auto-scan data/*.db
        3. Fallback: data/trades.db
        """
        env_paths = os.getenv("SETTLER_DB_PATHS", "")
        if env_paths:
            return [p.strip() for p in env_paths.split(",") if p.strip()]
        data_dir = Path("data")
        if data_dir.exists():
            found = sorted(str(p) for p in data_dir.glob("*.db"))
            if found:
                return found
        return ["data/trades.db"]

    async def _check_db_for_dryrun(self, _db: Any, already_redeemed: set[str] | None = None) -> None:
        """Process dry-run resolution for a single TradeDatabase instance."""
        from src.trading.dry_run_simulator import DryRunSimulator

        open_positions = await _db.get_open_dry_run_positions()
        if not open_positions:
            return

        condition_ids = {p["condition_id"] for p in open_positions}
        self.logger.info(
            f"Checking resolution for {len(open_positions)} dry-run positions "
            f"across {len(condition_ids)} markets"
        )

        resolved_all: list[dict] = []

        if self.client is not None:
            sim = DryRunSimulator(
                db=_db,
                market_name="resolver",
                condition_id="resolver",
                dry_run=True,
            )
            resolved_all = await sim.resolve_all_markets(self.client)
            for r in resolved_all:
                self.logger.info(
                    f"  Resolved dry-run #{r['id']}: {r['status']} PnL=${r['pnl']:.4f}"
                )
        else:
            for cid in condition_ids:
                outcome = await self.check_market_resolution(cid)
                if outcome is None:
                    continue

                winning_side = "YES" if str(outcome) == "0" else "NO"
                sim = DryRunSimulator(
                    db=_db, market_name="resolver",
                    condition_id=cid, dry_run=True,
                )
                resolved = await sim.resolve_position(cid, winning_side, winning_side)
                resolved_all.extend(resolved)
                for r in resolved:
                    self.logger.info(
                        f"  Resolved dry-run #{r['id']}: {r['status']} PnL=${r['pnl']:.4f}"
                    )

        if resolved_all:
            db_path = getattr(_db, "_db_path", None)
            await self._send_resolution_alerts(resolved_all, db_path=db_path)

        wins = [r for r in resolved_all if r.get("status") == "resolved_win"]
        if wins and not self.dry_run:
            await self._auto_redeem_wins(_db, already_redeemed)

    async def check_dryrun_resolution(self, db: Optional[Any] = None):
        """Check and resolve any settled dry-run positions.

        After resolution, attempts auto-redemption of winning positions on-chain.

        Args:
            db: TradeDatabase instance. Uses self._trade_db if not provided,
                or iterates all paths from SETTLER_DB_PATHS / data/*.db scan.
        """
        # Fix 4: shared set prevents redeeming the same condition_id twice
        # across multiple DB files in a single settlement cycle.
        already_redeemed: set[str] = set()
        try:
            if db is not None:
                await self._check_db_for_dryrun(db, already_redeemed)
            elif self._trade_db is not None:
                await self._check_db_for_dryrun(self._trade_db, already_redeemed)
            else:
                for db_path in self._get_db_paths():
                    _db = await TradeDatabase.initialize(db_path)
                    try:
                        await self._check_db_for_dryrun(_db, already_redeemed)
                    except Exception as e:
                        self.logger.error(
                            f"Error processing DB {db_path}: {e}", exc_info=True
                        )
                    finally:
                        await _db.close()
        except Exception as e:
            self.logger.error(f"Error checking dry-run resolution: {e}", exc_info=True)

    def _parse_db_context(self, db_path: str | None) -> dict:
        """Parse strategy/version/mode from DB filename (format: {strategy}-{version}-{mode}.db)."""
        if db_path:
            stem = Path(db_path).stem  # e.g. "convergence-v1-live"
            parts = stem.split("-", 2)
            if len(parts) == 3:
                return {"strategy": parts[0], "version": parts[1], "mode": parts[2]}
        return {
            "strategy": "settler",
            "version": "v1",
            "mode": "dryrun" if self.dry_run else "live",
        }

    def _get_alert_manager(self):
        """Lazily create AlertManager for notifications (fallback, no db context)."""
        if hasattr(self, "_alert_manager"):
            return self._alert_manager
        self._alert_manager = self._create_alert_manager_for_context(
            self._parse_db_context(None)
        )
        return self._alert_manager

    def _create_alert_manager_for_context(self, context: dict):
        """Create a new AlertManager with the given context dict."""
        telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if not telegram_bot_token or not telegram_chat_id:
            return None
        from src.alerts import TelegramAlertSender, AlertManager
        telegram = TelegramAlertSender(telegram_bot_token, telegram_chat_id, context=context)
        return AlertManager(telegram=telegram)

    async def _send_resolution_alerts(self, resolved: list[dict], db_path: str | None = None) -> None:
        """Send Telegram alerts for each resolved position."""
        context = self._parse_db_context(db_path)
        alert_mgr = self._create_alert_manager_for_context(context)
        if not alert_mgr:
            return
        try:
            for r in resolved:
                await alert_mgr.send_resolution_alert(r)
        except Exception as e:
            self.logger.warning(f"Resolution alert error: {e}")

    async def _auto_redeem_wins(self, db: Any, already_redeemed: set[str] | None = None) -> None:
        """Attempt on-chain redemption of winning positions.

        Only runs in live mode (not dry_run). Requires PRIVATE_KEY in env.
        already_redeemed is mutated in-place for cross-DB dedup (Fix 4).
        """
        try:
            from src.trading.auto_redeem import AutoRedeemer, redeem_resolved_wins

            private_key = os.getenv("PRIVATE_KEY")
            proxy_address = os.getenv("POLYMARKET_PROXY_ADDRESS")

            if not private_key:
                self.logger.warning("Cannot auto-redeem: PRIVATE_KEY not set")
                return

            redeemer = AutoRedeemer(
                private_key=private_key,
                proxy_address=proxy_address,
                dry_run=self.dry_run,
                logger_=self.logger,
            )

            results = await redeem_resolved_wins(db, redeemer, self.client, already_redeemed)
            if results:
                self.logger.info(
                    f"Auto-redeemed {len(results)} winning position(s)"
                )
                # Send redeem notifications
                alert_mgr = self._get_alert_manager()
                if alert_mgr:
                    for r in results:
                        if r.get("status") == "success":
                            try:
                                await alert_mgr.send_redeem_alert(r)
                            except Exception as e:
                                self.logger.warning(f"Redeem alert error: {e}")
        except Exception as e:
            self.logger.error(f"Auto-redeem error: {e}", exc_info=True)

    async def run(self, interval: int = 300):
        """
        Run settler in daemon mode.

        Args:
            interval: Check interval in seconds (default: 300 = 5 minutes)
        """
        self.logger.info("Position settler started")

        try:
            while True:
                await self.process_positions()

                self.logger.info(f"Sleeping {interval}s until next check...")
                await asyncio.sleep(interval)

        except KeyboardInterrupt:
            self.logger.info("Received shutdown signal")
        except Exception as e:
            self.logger.error(f"Fatal error in settler: {e}", exc_info=True)
        finally:
            self.logger.info("Position settler shut down")

    async def run_once(self):
        """Run position processing once and exit."""
        self.logger.info("Running position settler once...")
        await self.process_positions()
        self.logger.info("Position settler finished (run-once mode)")


async def resolve_dryrun_positions(settler: PositionSettler):
    """Resolve all open dry-run positions against market outcomes across all configured DBs."""
    from src.trading.dry_run_simulator import DryRunSimulator

    settler.logger.info("Starting dry-run position resolution...")

    async def _resolve_single_db(db: Any) -> None:
        positions = await db.get_open_dry_run_positions()
        if not positions:
            settler.logger.info("No open dry-run positions to resolve")
            return

        condition_ids = {pos["condition_id"] for pos in positions}
        settler.logger.info(
            "Found %d open positions across %d markets",
            len(positions), len(condition_ids),
        )

        if settler.client is None:
            settler.logger.info("No CLOB client (dry-run mode) — checking via API requires --live")
            for cid in condition_ids:
                cid_positions = [p for p in positions if p["condition_id"] == cid]
                settler.logger.info(
                    "  Market %s: %d open positions", cid, len(cid_positions)
                )
            return

        sim = DryRunSimulator(
            db=db,
            market_name="resolver",
            condition_id="resolver",
            dry_run=True,
        )
        resolved = await sim.resolve_all_markets(settler.client)

        if resolved:
            total_pnl = sum(r["pnl"] for r in resolved)
            wins = sum(1 for r in resolved if r["status"] == "resolved_win")
            losses = sum(1 for r in resolved if r["status"] == "resolved_loss")
            settler.logger.info(
                "Resolved %d positions: %d wins, %d losses, total PnL: $%.4f",
                len(resolved), wins, losses, total_pnl,
            )
        else:
            settler.logger.info("No positions were resolved (markets may still be open)")

    if settler._trade_db is not None:
        await _resolve_single_db(settler._trade_db)
    else:
        for db_path in PositionSettler._get_db_paths():
            settler.logger.info(f"Scanning DB: {db_path}")
            _db = await TradeDatabase.initialize(db_path)
            try:
                await _resolve_single_db(_db)
            except Exception as e:
                settler.logger.error(f"Error resolving DB {db_path}: {e}", exc_info=True)
            finally:
                await _db.close()


async def main():
    """Main entry point with CLI argument parsing."""
    parser = argparse.ArgumentParser(
        description="Polymarket Position Settler - Redeem winnings and track P&L"
    )

    parser.add_argument(
        "--live",
        action="store_true",
        help="Live mode - execute actual redemptions (default: dry run)",
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit (default: continuous daemon mode)",
    )

    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run in daemon mode (continuous loop)",
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="Check interval in seconds (default: 300 = 5 minutes)",
    )

    parser.add_argument(
        "--resolve-dryrun",
        action="store_true",
        help="Resolve all open dry-run positions against market outcomes and exit",
    )

    args = parser.parse_args()

    # Safety warning for live mode
    if args.live:
        print("\n" + "=" * 80)
        print("🔴 WARNING: LIVE MODE ENABLED 🔴")
        print("=" * 80)
        print("This will execute REAL redemptions!")
        print("Press Ctrl+C within 5 seconds to cancel...")
        print("=" * 80 + "\n")
        await asyncio.sleep(5)

    # Create settler instance (standalone mode — no injected dependencies)
    settler = PositionSettler(dry_run=not args.live)

    # Run mode
    if args.resolve_dryrun:
        await resolve_dryrun_positions(settler)
    elif args.once:
        await settler.run_once()
    else:
        await settler.run(interval=args.interval)


if __name__ == "__main__":
    asyncio.run(main())
