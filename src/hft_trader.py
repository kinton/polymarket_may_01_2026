"""
High-Frequency Trading Script for Polymarket 15-minute markets.

This script connects to Polymarket's CLOB WebSocket to stream real-time market data
and executes a late-window trading strategy (final 120s by default).

Strategy:
- Monitor Level 1 order book (best bid/ask) via WebSocket
- Track the winning side (price > 0.50)
- When time remaining <= TRIGGER_THRESHOLD seconds (default 120s, but > 0):
  - Check if winning side is available below $0.99
  - Execute Fill-or-Kill (FOK) order at $0.99

Safety Features:
- DRY_RUN mode (default: True) - only prints intended actions
- Configurable trade size (default: 1)
- No execution until explicitly enabled

Usage:
    # Dry run mode (safe, no real trades)
    python hft_trader.py --condition-id <CONDITION_ID> --token-id <TOKEN_ID> --end-time <ISO_TIME>

    # Live trading (DANGER!)
    python hft_trader.py --condition-id <CONDITION_ID> --token-id <TOKEN_ID> --end-time <ISO_TIME> --live

Requirements:
    - py-clob-client
    - websockets
    - python-dotenv
    - Environment variables: PRIVATE_KEY, POLYGON_CHAIN_ID, CLOB_HOST, CLOB_API_KEY, CLOB_SECRET, CLOB_PASSPHRASE
"""

import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from math import gcd
from typing import Any

import aiohttp
import websockets
from dotenv import load_dotenv

from src.clob_types import (
    BUY_PRICE,
    CLOB_WS_URL,
    MIN_CONFIDENCE,
    MIN_TRADE_USDC,
    PRICE_TIE_EPS,
    TRIGGER_THRESHOLD,
    OrderBook,
)
from src.market_parser import (
    determine_winning_side,
    extract_best_ask_with_size_from_book,
    extract_best_bid_with_size_from_book,
    get_winning_token_id,
)
from src.oracle_tracker import OracleSnapshot, OracleTracker
from src.updown_prices import (
    EventPageClient,
    RtdsClient,
    guess_chainlink_symbol,
    parse_market_window,
)

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import (
        AssetType,
        BalanceAllowanceParams,
        CreateOrderOptions,
        MarketOrderArgs,
        OrderType,
    )
except ImportError:
    print("Error: py-clob-client not installed. Run: uv pip install py-clob-client")
    exit(1)

EXCHANGE_CONTRACT = "0xC5d563A36AE78145C45a50134d48A1215220f80a"


class LastSecondTrader:
    """
    High-frequency trader that monitors market data via WebSocket
    and executes trades in the final window before market close.
    """

    # Configuration
    DRY_RUN = True  # Safety: Default to dry run mode
    TRADE_SIZE = 1  # Default trade size in dollars
    TRIGGER_THRESHOLD = TRIGGER_THRESHOLD
    PRICE_THRESHOLD = 0.50  # Winning side threshold
    BUY_PRICE = BUY_PRICE
    PRICE_TIE_EPS = PRICE_TIE_EPS
    MIN_CONFIDENCE = MIN_CONFIDENCE  # Minimum confidence to buy (e.g. 0.75 = 75%)

    WS_URL = CLOB_WS_URL
    WS_STALE_SECONDS = 2.0  # Require fresh WS data for trigger checks
    MIN_TRADE_USDC = MIN_TRADE_USDC
    BALANCE_RISK_PCT = 0.05
    BALANCE_RISK_SWITCH_USDC = 30.0

    def __init__(
        self,
        condition_id: str,
        token_id_yes: str,
        token_id_no: str,
        end_time: datetime,
        dry_run: bool = True,
        trade_size: float = 1.0,
        title: str | None = None,
        slug: str | None = None,
        trader_logger: Any | None = None,
        oracle_enabled: bool = False,
        oracle_guard_enabled: bool = True,
        oracle_min_points: int = 4,
        oracle_window_s: float = 60.0,
        book_log_every_s: float = 1.0,
        book_log_every_s_final: float = 0.5,
    ):
        """
        Initialize the trader.

        Args:
            condition_id: The market condition ID
            token_id_yes: The YES token ID
            token_id_no: The NO token ID
            end_time: Market end time (timezone-aware datetime)
            dry_run: If True, only print actions without executing
            trade_size: Size of trade in dollars (will buy trade_size/price tokens)
        """
        self.condition_id = condition_id
        self.token_id_yes = token_id_yes
        self.token_id_no = token_id_no
        self.end_time = end_time
        self.dry_run = dry_run
        self.trade_size = trade_size  # dollars to spend
        self.min_trade_usdc = max(self.MIN_TRADE_USDC, round(float(trade_size), 2))
        self.title = title
        self.slug = slug
        self.logger = trader_logger

        # Extract short market name for logging (e.g. "BTC", "ETH", "SOL")
        self.market_name = self._extract_market_name(title)

        # Market state
        self.orderbook = OrderBook()

        # Optional: track oracle (Chainlink) price for "Up or Down" markets via RTDS.
        # This does not touch polymarket.com and is safe from Cloudflare blocks.
        self.oracle_enabled = bool(oracle_enabled)
        self.oracle_guard_enabled = bool(oracle_guard_enabled)

        # We keep "oracle decides trade side" OFF for now. It's easy to re-enable later,
        # but for live trading we want the oracle primarily as a quality gate.
        self.oracle_decide_side = False
        self.oracle_require_side = False
        self.oracle_symbol = guess_chainlink_symbol(self.title or self.market_name)
        self.oracle_stats_window_s = float(oracle_window_s)
        self.oracle_tracker: OracleTracker | None = (
            OracleTracker(window_seconds=self.oracle_stats_window_s)
            if self.oracle_enabled
            else None
        )
        self.oracle_snapshot: OracleSnapshot | None = None
        self.last_oracle_update_ts = 0.0
        self._last_oracle_log_ts = 0.0

        # Oracle guard (strict): enabled whenever oracle is enabled.
        # Defaults are tuned for Chainlink tick frequency (~10-15s).
        self.oracle_min_points = int(oracle_min_points)

        self.oracle_guard_max_stale_s = 20.0
        self.oracle_guard_log_every_s = 5.0
        self.oracle_guard_max_vol_pct = 0.002
        self.oracle_guard_min_abs_z = 0.75
        self.oracle_guard_require_agreement = True
        self.oracle_guard_require_beat = False
        self.oracle_guard_max_reversal_slope = 0.0
        self.oracle_beat_max_lag_ms = 10_000
        # Mapping of oracle direction ("Up"/"Down") to our internal sides ("YES"/"NO").
        # Populated best-effort from Gamma when slug is available.
        self.oracle_up_side: str | None = None
        self.oracle_down_side: str | None = None
        try:
            end_iso = self.end_time.strftime("%Y-%m-%dT%H:%M:%SZ")
            self.oracle_window = parse_market_window(self.title or "", end_iso)
        except Exception:
            self.oracle_window = None
        self.winning_side: str | None = None  # "YES" or "NO"
        self.order_executed = False
        self.order_in_progress = False  # Prevent duplicate orders
        self.order_attempts = 0  # Track retry attempts
        self.max_order_attempts = 3  # Max retries
        self.last_order_attempt_time = 0.0  # Track last attempt timestamp
        # Stable order identity across retries (keeps same order hash)
        self._order_nonce: int | None = None
        self._order_side: str | None = None
        self._order_token_id: str | None = None
        self._order_amount: float | None = None
        self._order_price: float | None = None
        self.ws = None

        # Track last log time to avoid spam
        self.last_log_time = 0.0
        self.last_logged_state = None
        # Orderbook log throttling (wall-clock). Winner flips are always logged immediately.
        self.book_log_every_s = max(0.0, float(book_log_every_s))
        self.book_log_every_s_final = max(0.0, float(book_log_every_s_final))
        self._last_book_log_ts = 0.0
        self._last_logged_winner: str | None = None

        # Track which warnings we've already logged (to avoid spam)
        self._logged_warnings = set()
        self._trigger_lock = asyncio.Lock()
        self.last_ws_update_ts = 0.0
        self._last_stale_log_ts = 0.0
        self._planned_trade_amount: float | None = None
        self._pending_trade_side: str | None = None

        # Oracle guard metrics/log throttling.
        self._oracle_guard_block_count = 0
        self._oracle_guard_reason_counts: dict[str, int] = {}
        self._oracle_guard_last_reason: str | None = None
        self._oracle_guard_last_log_ts = 0.0
        self._oracle_html_beat_attempted = False

        # Initialize CLOB client
        load_dotenv()
        self.client = self._init_clob_client()

        # Log init
        mode = "DRY RUN" if self.dry_run else "🔴 LIVE 🔴"
        self._log(
            f"[{self.market_name}] Trader initialized | {mode} | ${self.trade_size} @ ${self.BUY_PRICE} | Min confidence: {self.MIN_CONFIDENCE * 100:.0f}%"
        )
        if self.oracle_enabled:
            sym = self.oracle_symbol or "unknown"
            parts = [f"oracle_tracking=on ({sym})"]
            if self.oracle_decide_side:
                parts.append("decide_side=on")
            if self.oracle_require_side:
                parts.append("require_side=on")
            if self.oracle_guard_enabled:
                parts.append(
                    f"guard=on (stale<={self.oracle_guard_max_stale_s}s, min_pts>={self.oracle_min_points}, max_vol<={self.oracle_guard_max_vol_pct}, |z|>={self.oracle_guard_min_abs_z})"
                )
            else:
                parts.append("guard=off")
            self._log(f"[{self.market_name}] " + " | ".join(parts))
        else:
            self._log(f"[{self.market_name}] oracle_tracking=off")

    def _extract_market_name(self, title: str | None) -> str:
        """Extract short market name from title for logging."""
        if not title:
            return "UNKNOWN"

        # Extract cryptocurrency name (e.g., "Bitcoin" -> "BTC")
        title_lower = title.lower()
        if "bitcoin" in title_lower or "btc" in title_lower:
            return "BTC"
        elif "ethereum" in title_lower or "eth" in title_lower:
            return "ETH"
        elif "solana" in title_lower or "sol" in title_lower:
            return "SOL"
        elif "xrp" in title_lower or "ripple" in title_lower:
            return "XRP"
        else:
            # Fallback: use first word of title
            return title.split()[0][:8].upper()

    def _get_ask_for_side(self, side: str) -> float | None:
        if side == "YES":
            return self.orderbook.best_ask_yes
        if side == "NO":
            return self.orderbook.best_ask_no
        return None

    def _oracle_recommended_side(self) -> str | None:
        """
        Decide which outcome is currently winning based on oracle price relative
        to price_to_beat (window open).
        """
        snap = self.oracle_snapshot
        if snap is None or snap.price_to_beat is None or snap.delta is None:
            return None
        if self.oracle_up_side is None or self.oracle_down_side is None:
            return None
        return self.oracle_up_side if snap.delta >= 0 else self.oracle_down_side

    def _oracle_quality_ok(
        self, *, trade_side: str, time_remaining: float
    ) -> tuple[bool, str, str]:
        """
        Optional gate: if oracle metrics look unreliable, skip the buy.

        Returns:
            (ok, reason_code, detail). When ok=False, reason_code is stable for counters/log throttling.
        """
        if not self.oracle_enabled or not self.oracle_guard_enabled:
            return True, "", ""

        snap = self.oracle_snapshot
        if snap is None:
            return False, "oracle_snapshot_missing", ""

        staleness_s = time.time() - float(self.last_oracle_update_ts)
        if staleness_s > self.oracle_guard_max_stale_s:
            return False, "oracle_stale", f"{staleness_s:.2f}s"

        if self.oracle_guard_require_beat and snap.price_to_beat is None:
            return False, "price_to_beat_missing", ""

        if snap.n_points < self.oracle_min_points:
            return (
                False,
                "oracle_points_insufficient",
                f"{snap.n_points}<{self.oracle_min_points}",
            )

        if snap.vol_pct is None:
            return False, "oracle_vol_missing", ""

        if snap.vol_pct > self.oracle_guard_max_vol_pct:
            return (
                False,
                "oracle_vol_high",
                f"{snap.vol_pct:.6f}>{self.oracle_guard_max_vol_pct:.6f}",
            )

        # If we missed window start (common for the trader which starts in the last 2 minutes),
        # we won't have price_to_beat, and z-score relative to beat is undefined. In that mode,
        # we still guard on freshness/points/volatility but skip z-score gating.
        if snap.zscore is None:
            if snap.price_to_beat is None and not self.oracle_guard_require_beat:
                _ = time_remaining
                return True, "", ""
            return False, "oracle_z_missing", ""

        if abs(snap.zscore) < self.oracle_guard_min_abs_z:
            return (
                False,
                "oracle_z_low",
                f"{abs(snap.zscore):.2f}<{self.oracle_guard_min_abs_z:.2f}",
            )

        oracle_side = self._oracle_recommended_side()
        if self.oracle_guard_require_agreement and oracle_side is not None and oracle_side != trade_side:
            return (
                False,
                "oracle_disagrees",
                f"oracle={oracle_side}, trade={trade_side}",
            )

        # Optional reversal guard based on slope.
        max_rev = self.oracle_guard_max_reversal_slope
        if max_rev > 0 and snap.slope_usd_per_s is not None:
            # Determine which direction the chosen side implies.
            expected_sign = None
            if self.oracle_up_side is not None and trade_side == self.oracle_up_side:
                expected_sign = 1
            elif self.oracle_down_side is not None and trade_side == self.oracle_down_side:
                expected_sign = -1

            if expected_sign == 1 and snap.slope_usd_per_s < -max_rev:
                return (
                    False,
                    "oracle_reversal_slope",
                    f"{snap.slope_usd_per_s:.2f}<-{max_rev:.2f}",
                )
            if expected_sign == -1 and snap.slope_usd_per_s > max_rev:
                return (
                    False,
                    "oracle_reversal_slope",
                    f"{snap.slope_usd_per_s:.2f}>{max_rev:.2f}",
                )

        _ = time_remaining  # reserved for future guards (e.g. scale thresholds near close)
        return True, "", ""

    def _log(self, message: str) -> None:
        """Log message to both console and file logger."""
        if self.logger:
            self.logger.info(message)
            return
        print(message)

    def _init_clob_client(self) -> ClobClient | None:
        """Initialize the CLOB client for order execution."""
        if self.dry_run:
            self._log("Dry run mode: Skipping CLOB client initialization")
            return None

        try:
            private_key = os.getenv("PRIVATE_KEY")
            chain_id = int(os.getenv("POLYGON_CHAIN_ID", "137"))
            host = os.getenv("CLOB_HOST", "https://clob.polymarket.com")
            # Polymarket proxy wallet address (where USDC balance is)
            funder = os.getenv("POLYMARKET_PROXY_ADDRESS")

            if not private_key:
                self._log("⚠️ Missing PRIVATE_KEY in .env")
                return None

            # Normalize host to avoid Cloudflare blocks on polymarket.com
            if "clob.polymarket.com" not in host:
                self._log(
                    "⚠️ CLOB_HOST should be https://clob.polymarket.com (overriding)"
                )
                host = "https://clob.polymarket.com"

            # signature_type=2 for Polymarket proxy wallets
            # funder = the proxy wallet address that holds the USDC
            signature_type = 2 if funder else 0
            client = ClobClient(
                host=host,
                key=private_key,
                chain_id=chain_id,
                signature_type=signature_type,  # POLY_PROXY when funder is set
                funder=funder or "",
            )

            # Derive API credentials from private key (required for auth)
            api_creds = client.create_or_derive_api_creds()
            client.set_api_creds(api_creds)

            self._log(f"✓ CLOB client initialized ({host})")
            if funder:
                self._log(f"  Proxy wallet: {funder}")
            return client

        except Exception as e:
            self._log(f"❌ CLOB init failed: {e}")
            return None

    def get_time_remaining(self) -> float:
        """
        Calculate time remaining until market close in seconds.

        Returns:
            Seconds remaining (can be negative if market closed)
        """
        now = datetime.now(timezone.utc)
        delta = (self.end_time - now).total_seconds()
        return delta

    async def connect_websocket(self):
        """Connect to Polymarket WebSocket and subscribe to both YES and NO tokens."""
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                # Single WebSocket connection for both tokens
                self.ws = await websockets.connect(
                    self.WS_URL, ping_interval=20, ping_timeout=10
                )
                subscribe_msg = {
                    "assets_ids": [self.token_id_yes, self.token_id_no],
                    "type": "MARKET",
                }
                await self.ws.send(json.dumps(subscribe_msg))

                self._log("✓ WebSocket connected, subscribed to YES+NO tokens")
                return True

            except Exception as e:
                self._log(f"❌ WebSocket connection failed: {e}")
                if attempt < (max_attempts - 1):
                    await asyncio.sleep(2**attempt)
                else:
                    return False

    async def process_market_update(self, data: dict[str, Any]):
        """
        Process incoming market data from WebSocket.

        Args:
            data: Market data from WebSocket (can be array or dict)
        """
        try:
            if not data:
                return

            # Handle array response - extract first element
            if isinstance(data, list):
                if len(data) == 0:
                    return
                data = data[0]  # type: ignore
            if not isinstance(data, dict):
                return

            # Determine which token this data is for
            received_asset_id = data.get("asset_id")
            if not received_asset_id:
                return

            is_yes_data = received_asset_id == self.token_id_yes
            is_no_data = received_asset_id == self.token_id_no

            if not is_yes_data and not is_no_data:
                return

            event_type = data.get("event_type")

            # Extract best bid and ask based on event type
            if event_type == "book":
                asks = data.get("asks", [])
                bids = data.get("bids", [])
                best_ask, best_ask_size = extract_best_ask_with_size_from_book(asks)
                best_bid, best_bid_size = extract_best_bid_with_size_from_book(bids)

                # Filter invalid prices: $0.00 and $1.00+ are not valid
                if best_ask is not None and 0.001 <= best_ask <= 0.999:
                    if is_yes_data:
                        self.orderbook.best_ask_yes = best_ask
                        self.orderbook.best_ask_yes_size = best_ask_size
                    else:
                        self.orderbook.best_ask_no = best_ask
                        self.orderbook.best_ask_no_size = best_ask_size

                if best_bid is not None and 0.001 <= best_bid <= 0.999:
                    if is_yes_data:
                        self.orderbook.best_bid_yes = best_bid
                        self.orderbook.best_bid_yes_size = best_bid_size
                    else:
                        self.orderbook.best_bid_no = best_bid
                        self.orderbook.best_bid_no_size = best_bid_size

            elif event_type == "price_change":
                # price_changes array contains data for BOTH tokens
                changes = data.get("price_changes", [])

                for change in changes:
                    change_asset_id = change.get("asset_id")
                    if not change_asset_id:
                        continue

                    is_yes_change = change_asset_id == self.token_id_yes
                    is_no_change = change_asset_id == self.token_id_no

                    if not is_yes_change and not is_no_change:
                        continue

                    best_ask = change.get("best_ask")
                    best_bid = change.get("best_bid")

                    if best_ask is not None and best_ask != "":
                        try:
                            ask_val = float(best_ask)
                            # Filter invalid prices
                            if 0.001 <= ask_val <= 0.999:
                                if is_yes_change:
                                    self.orderbook.best_ask_yes = ask_val
                                    self.orderbook.best_ask_yes_size = None
                                else:
                                    self.orderbook.best_ask_no = ask_val
                                    self.orderbook.best_ask_no_size = None
                        except (ValueError, TypeError):
                            pass

                    if best_bid is not None and best_bid != "":
                        try:
                            bid_val = float(best_bid)
                            # Filter invalid prices
                            if 0.001 <= bid_val <= 0.999:
                                if is_yes_change:
                                    self.orderbook.best_bid_yes = bid_val
                                    self.orderbook.best_bid_yes_size = None
                                else:
                                    self.orderbook.best_bid_no = bid_val
                                    self.orderbook.best_bid_no_size = None
                        except (ValueError, TypeError):
                            pass

            elif event_type == "best_bid_ask":
                best_ask = data.get("best_ask")
                best_bid = data.get("best_bid")

                if best_ask is not None and best_ask != "":
                    try:
                        val = float(best_ask)
                        # Filter invalid prices
                        if 0.001 <= val <= 0.999:
                            if is_yes_data:
                                self.orderbook.best_ask_yes = val
                                self.orderbook.best_ask_yes_size = None
                            else:
                                self.orderbook.best_ask_no = val
                                self.orderbook.best_ask_no_size = None
                    except (ValueError, TypeError):
                        pass

                if best_bid is not None and best_bid != "":
                    try:
                        val = float(best_bid)
                        # Filter invalid prices
                        if 0.001 <= val <= 0.999:
                            if is_yes_data:
                                self.orderbook.best_bid_yes = val
                                self.orderbook.best_bid_yes_size = None
                            else:
                                self.orderbook.best_bid_no = val
                                self.orderbook.best_bid_no_size = None
                    except (ValueError, TypeError):
                        pass

            # Update derived values and determine winning side
            self.orderbook.update()
            self._update_winning_side()
            self.last_ws_update_ts = time.time()

            # Get time remaining
            time_remaining = self.get_time_remaining()

            # Log current state (throttled). Winner flips are always logged.
            now_ts = time.time()
            in_final_seconds = time_remaining <= 5.0
            interval_s = (
                self.book_log_every_s_final if in_final_seconds else self.book_log_every_s
            )
            winner_changed = (self.winning_side or None) != (self._last_logged_winner or None)
            time_due = (now_ts - self._last_book_log_ts) >= max(0.0, interval_s)
            should_log = winner_changed or time_due

            if should_log:
                # Show both bid and ask for better diagnosis
                yes_ask = self.orderbook.best_ask_yes
                yes_bid = self.orderbook.best_bid_yes
                yes_ask_sz = self.orderbook.best_ask_yes_size
                yes_bid_sz = self.orderbook.best_bid_yes_size
                no_ask = self.orderbook.best_ask_no
                no_bid = self.orderbook.best_bid_no
                no_ask_sz = self.orderbook.best_ask_no_size
                no_bid_sz = self.orderbook.best_bid_no_size

                # Format prices: show "-" if None
                def fmt(p):
                    return f"${p:.2f}" if p is not None else "-"

                def fmt_sz(s):
                    if s is None:
                        return "-"
                    if abs(s - round(s)) < 1e-9:
                        return str(int(round(s)))
                    return f"{s:.4f}".rstrip("0").rstrip(".")

                def fmt_notional(p, s):
                    if p is None or s is None:
                        return "-"
                    return f"${p * s:.2f}"

                msg = "".join(
                    [
                        f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] [{self.market_name}] ",
                        f"Time: {time_remaining:.2f}s | ",
                        f"YES bid: {fmt(yes_bid)} x {fmt_sz(yes_bid_sz)} (= {fmt_notional(yes_bid, yes_bid_sz)}) | ",
                        f"YES ask: {fmt(yes_ask)} x {fmt_sz(yes_ask_sz)} (= {fmt_notional(yes_ask, yes_ask_sz)}) | ",
                        f"NO bid: {fmt(no_bid)} x {fmt_sz(no_bid_sz)} (= {fmt_notional(no_bid, no_bid_sz)}) | ",
                        f"NO ask: {fmt(no_ask)} x {fmt_sz(no_ask_sz)} (= {fmt_notional(no_ask, no_ask_sz)}) | ",
                        f"Winner: {self.winning_side or 'None'}",
                    ]
                )
                self._log(msg)
                self._last_book_log_ts = now_ts
                self._last_logged_winner = self.winning_side

            # Check trigger conditions
            await self.check_trigger(time_remaining)

        except Exception as e:
            self._log(f"Error processing market update: {e}")

    def _update_winning_side(self) -> None:
        """Update winning side based on current orderbook state."""
        self.winning_side = determine_winning_side(
            best_bid_yes=self.orderbook.best_bid_yes,
            best_bid_no=self.orderbook.best_bid_no,
            best_ask_yes=self.orderbook.best_ask_yes,
            best_ask_no=self.orderbook.best_ask_no,
            tie_epsilon=self.PRICE_TIE_EPS,
        )

    def _get_winning_token_id(self) -> str | None:
        """Get token ID for the winning side."""
        if self.winning_side is None:
            return None
        return get_winning_token_id(
            self.winning_side, self.token_id_yes, self.token_id_no
        )

    def _get_winning_ask(self) -> float | None:
        """
        Get best ask price for winning side.

        If direct ask is not available, compute implied price from opposite side:
        - If NO ask = 0.99, implied YES ask ≈ 0.01 (but we can't buy at that)
        - We need actual ask to buy, so return None if no direct ask
        """
        if self.winning_side == "YES":
            # Direct ask available
            if self.orderbook.best_ask_yes is not None:
                return self.orderbook.best_ask_yes
            # Try implied from NO bid: if someone bids 0.99 for NO, YES should be ~0.01
            # But we need someone SELLING YES, not implied price
            return None
        elif self.winning_side == "NO":
            if self.orderbook.best_ask_no is not None:
                return self.orderbook.best_ask_no
            return None
        return None

    def _get_winning_bid(self) -> float | None:
        """Get best bid price for winning side (what buyers are willing to pay)."""
        if self.winning_side == "YES":
            return self.orderbook.best_bid_yes
        elif self.winning_side == "NO":
            return self.orderbook.best_bid_no
        return None

    async def _check_balance(self) -> bool:
        """
        Check if we have sufficient USDC balance and allowance for the trade.

        Returns:
            True if balance and allowance are sufficient, False otherwise
        """
        if not self.client:
            self._log(f"❌ [{self.market_name}] CLOB client not initialized")
            return False

        try:
            # Get balance and allowance for USDC
            # pass a params object — py-clob-client expects a params instance
            # (calling with None causes an AttributeError inside the client)
            params = BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL  # type: ignore  # USDC is collateral asset
            )  # signature_type defaults to -1 and will be filled by client
            balance_data_raw = await asyncio.to_thread(
                self.client.get_balance_allowance, params
            )
            balance_data: dict[str, Any] = balance_data_raw  # type: ignore

            # Extract USDC balance (API returns in 6-decimal units, divide by 1e6 to get dollars)
            usdc_balance = float(balance_data.get("balance", 0)) / 1e6

            # API returns 'allowances' (dict of contract -> allowance), not 'allowance'
            allowances_dict = balance_data.get("allowances", {})

            # Allowance is also in 6-decimal units (micro-USDC), convert to dollars
            usdc_allowance = float(allowances_dict.get(EXCHANGE_CONTRACT, 0)) / 1e6

            # Dynamic sizing:
            # - if balance < $30: use min_trade_usdc (default $1.50)
            # - else: use 5% of balance (but not less than min_trade_usdc)
            if usdc_balance < self.BALANCE_RISK_SWITCH_USDC:
                required_amount = self.min_trade_usdc
            else:
                required_amount = max(
                    self.min_trade_usdc,
                    round(usdc_balance * self.BALANCE_RISK_PCT, 2),
                )
            required_amount = max(round(required_amount, 2), 1.00)
            self._planned_trade_amount = required_amount

            # Check both balance and allowance
            if usdc_balance < required_amount:
                self._log(
                    f"❌ [{self.market_name}] Insufficient balance: ${usdc_balance:.2f} < ${required_amount:.2f}"
                )
                return False

            if usdc_allowance < required_amount:
                self._log(
                    f"❌ [{self.market_name}] Insufficient allowance: ${usdc_allowance:.2f} < ${required_amount:.2f}"
                )
                self._log("   → Run: uv run python approve.py to approve USDC spending")
                return False

            self._log(
                f"✓ [{self.market_name}] Balance check passed: ${usdc_balance:.2f} available (need ${required_amount:.2f})"
            )
            return True

        except Exception as e:
            self._log(f"⚠️  [{self.market_name}] Balance check failed: {e}")
            return False

    async def check_trigger(self, time_remaining: float):
        """
        Check if trigger conditions are met and execute trade if appropriate.

        Trigger conditions:
        1. Time remaining <= TRIGGER_THRESHOLD seconds (but > 0)
        2. Winning side is determined (higher ask price)
        3. Best ask >= MIN_CONFIDENCE (e.g. 0.75 = 75% confidence)
        4. Best ask <= $0.99 (at or better than our limit)
        5. Order not already executed
        6. Order not currently in progress (prevent duplicates)
        7. Sufficient USDC balance and allowance
        """
        async with self._trigger_lock:
            # We sometimes call check_trigger with an explicit time_remaining (e.g. unit
            # tests). Use monotonic time to guard against crossing the market close
            # without depending on get_time_remaining() being consistent with the
            # passed value.
            trigger_start_mono = time.monotonic()

            if self.order_executed or self.order_in_progress or time_remaining <= 0:
                return

            # Check retry limit
            if self.order_attempts >= self.max_order_attempts:
                if "max_attempts" not in self._logged_warnings:
                    self._log(
                        f"⚠️  [{self.market_name}] Max order attempts ({self.max_order_attempts}) reached"
                    )
                    self._logged_warnings.add("max_attempts")
                return

            # Cooldown between attempts (prevent spam)
            current_time = time.time()
            if (
                self.order_attempts > 0
                and (current_time - self.last_order_attempt_time) < 2.0
            ):
                return  # Wait at least 2 seconds between attempts

            if time_remaining > self.TRIGGER_THRESHOLD:
                return

            trade_side = self.winning_side
            oracle_side = self._oracle_recommended_side()
            if self.oracle_enabled and self.oracle_decide_side:
                if oracle_side:
                    trade_side = oracle_side
                elif self.oracle_require_side:
                    if "oracle_side_missing" not in self._logged_warnings:
                        self._log(
                            f"⚠️  [{self.market_name}] Oracle side required but unavailable (missing price_to_beat or mapping)"
                        )
                        self._logged_warnings.add("oracle_side_missing")
                    return

            if trade_side is None:
                if "no_winner" not in self._logged_warnings:
                    self._log(
                        f"⚠️  [{self.market_name}] No trade side at {time_remaining:.3f}s"
                    )
                    self._logged_warnings.add("no_winner")
                return

            winning_ask = self._get_ask_for_side(trade_side)
            if winning_ask is None:
                if "no_ask" not in self._logged_warnings:
                    self._log(
                        f"⚠️  [{self.market_name}] No ask price for {trade_side} at {time_remaining:.3f}s"
                    )
                    self._logged_warnings.add("no_ask")
                return

            # Check minimum confidence: only buy if winning side is ≥ MIN_CONFIDENCE
            # For example, if MIN_CONFIDENCE = 0.75, only buy if ask ≥ $0.75
            if winning_ask < self.MIN_CONFIDENCE:
                if "low_confidence" not in self._logged_warnings:
                    self._log(
                        f"⚠️  [{self.market_name}] Low confidence: ${winning_ask:.2f} < ${self.MIN_CONFIDENCE:.2f} (need ≥{self.MIN_CONFIDENCE * 100:.0f}%)"
                    )
                    self._logged_warnings.add("low_confidence")
                return

            if winning_ask > self.BUY_PRICE + self.PRICE_TIE_EPS:
                if "price_high" not in self._logged_warnings:
                    self._log(
                        f"⚠️  [{self.market_name}] Ask ${winning_ask:.4f} > ${self.BUY_PRICE}"
                    )
                    self._logged_warnings.add("price_high")
                return

            # Optional oracle quality gate: skip buying if metrics look unreliable.
            oracle_ok, oracle_reason, oracle_detail = self._oracle_quality_ok(
                trade_side=trade_side, time_remaining=time_remaining
            )
            if not oracle_ok:
                # Track stats for end-of-run summary.
                self._oracle_guard_block_count += 1
                self._oracle_guard_reason_counts[oracle_reason] = (
                    self._oracle_guard_reason_counts.get(oracle_reason, 0) + 1
                )

                # Log at most once every N seconds, or when the reason changes.
                now = time.time()
                should_log = (
                    oracle_reason != self._oracle_guard_last_reason
                    or (now - self._oracle_guard_last_log_ts) >= self.oracle_guard_log_every_s
                )
                if should_log:
                    snap = self.oracle_snapshot
                    extra = ""
                    if snap is not None:

                        def _fmt_money(v: float | None) -> str:
                            return f"{v:,.2f}" if v is not None else "-"

                        z = f"{snap.zscore:.2f}" if snap.zscore is not None else "-"
                        vol = (
                            f"{snap.vol_pct*100:.4f}%"
                            if snap.vol_pct is not None
                            else "-"
                        )
                        slope = (
                            f"{snap.slope_usd_per_s:.2f}"
                            if snap.slope_usd_per_s is not None
                            else "-"
                        )
                        extra = (
                            f" | oracle={_fmt_money(snap.price)}"
                            f" beat={_fmt_money(snap.price_to_beat)}"
                            f" Δ={_fmt_money(snap.delta)}"
                            f" vol={vol}"
                            f" slope={slope}$/s"
                            f" z={z}"
                            f" n={snap.n_points}"
                        )

                    detail = f" ({oracle_detail})" if oracle_detail else ""
                    self._log(
                        f"🛑 [{self.market_name}] SKIP (oracle_guard): {oracle_reason}{detail} | t={time_remaining:.3f}s | side={trade_side} | ask=${winning_ask:.4f}{extra}"
                    )
                    self._oracle_guard_last_reason = oracle_reason
                    self._oracle_guard_last_log_ts = now
                return

            # Check balance before executing order (only check once per market)
            if "balance_checked" not in self._logged_warnings:
                balance_ok = await self._check_balance()
                self._logged_warnings.add("balance_checked")

                if not balance_ok:
                    self._log(
                        f"❌ [{self.market_name}] FATAL: Insufficient funds. Stopping trader."
                    )
                    self.order_executed = True  # Stop trying to trade
                    return

            # All conditions met - execute trade!
            oracle_note = ""
            if self.oracle_enabled and self.oracle_decide_side and oracle_side:
                snap = self.oracle_snapshot
                if snap is not None and snap.price_to_beat is not None:
                    oracle_note = (
                        f" | oracle={snap.price:,.2f} beat={snap.price_to_beat:,.2f} "
                        f"Δ={snap.delta:,.2f}"
                    )

            self._log(
                f"🎯 [{self.market_name}] TRIGGER at {time_remaining:.3f}s! {trade_side} @ ${winning_ask:.4f}{oracle_note}"
            )

            # Final sanity check in case we crossed close during async work
            elapsed = time.monotonic() - trigger_start_mono
            if (time_remaining - elapsed) <= 0:
                self._log(
                    f"⏰ [{self.market_name}] Market closed before order submission. Skipping."
                )
                self.order_executed = True
                return

            # Snapshot the chosen side so execute_order doesn't depend on concurrently
            # changing self.winning_side.
            self._pending_trade_side = trade_side
            await self.execute_order()

    async def verify_order(self, order_id: str) -> None:
        """
        Verify order status after submission by querying the API.
        """
        if not self.client:
            return

        self._log(f"🔎 [{self.market_name}] Verifying order {order_id}...")
        try:
            # Wait briefly for propagation
            await asyncio.sleep(0.5)

            # Client.get_order returns a dictionary
            order_data_raw = await asyncio.to_thread(self.client.get_order, order_id)
            order_data: dict[str, Any] = order_data_raw  # type: ignore

            # Exact key per py-clob-client documentation
            status = order_data.get("status", "unknown").lower()

            if status == "matched":
                self._log(
                    f"✅ [{self.market_name}] Order {order_id} CONFIRMED FILLED (Status: {status})"
                )
            elif status in ["canceled", "killed"]:
                self._log(
                    f"⚠️  [{self.market_name}] Order {order_id} WAS KILLED/CANCELED (Status: {status})"
                )
            else:
                self._log(f"ℹ️  [{self.market_name}] Order {order_id} status: {status}")

        except Exception as e:
            self._log(f"⚠️  [{self.market_name}] Verification failed: {e}")

    def _calculate_valid_size(self, price: float, target_dollars: float) -> float:
        """
        Calculate a valid order size that satisfies Polymarket's precision requirements.

        NOTE: This function is kept for reference but we now use market orders instead,
        which take amount (dollars) instead of size (tokens), avoiding the precision issue.
        """
        price_cents = int(round(price * 100))
        raw_size = target_dollars / price
        size_cents = int(raw_size * 100)

        divisor = 10000 // gcd(price_cents, 10000)

        if size_cents % divisor != 0:
            size_cents = ((size_cents // divisor) + 1) * divisor

        min_size_cents = int(100 / price) + 1
        if min_size_cents % divisor != 0:
            min_size_cents = ((min_size_cents // divisor) + 1) * divisor

        size_cents = max(size_cents, min_size_cents)
        return size_cents / 100.0

    async def execute_order(self) -> None:
        side = self._pending_trade_side or self.winning_side or "YES"
        self._pending_trade_side = None
        await self.execute_order_for(side)

    async def execute_order_for(self, side: str) -> None:
        """
        Execute Fill-or-Kill (FOK) market order for a specific side.

        Uses MarketOrderArgs which takes 'amount' (dollars to spend) instead of 'size' (tokens).
        This avoids the precision issue where price × size must have ≤2 decimal places.
        """
        winning_ask = self._get_ask_for_side(side)
        winning_token_id = get_winning_token_id(side, self.token_id_yes, self.token_id_no)

        if not winning_token_id:
            self._log(f"❌ [{self.market_name}] Error: No winning token ID available")
            return

        # For market orders: specify amount (dollars) with 2 decimal places
        # Ensure minimum $1.00
        amount = (
            self._planned_trade_amount
            if self._planned_trade_amount is not None
            else max(round(self.trade_size, 2), 1.00)
        )
        price = round(self.BUY_PRICE, 2)  # Worst price we'll accept

        # Keep order identity stable across retries (same nonce => same order hash)
        if self._order_nonce is None:
            # Default CLOB nonce is 0 (used for on-chain cancellations). Using ms epoch
            # can exceed accepted ranges and trigger "invalid nonce".
            self._order_nonce = 0
            self._order_side = side
            self._order_token_id = winning_token_id
            self._order_amount = amount
            self._order_price = price
        else:
            side = self._order_side or side
            winning_token_id = self._order_token_id or winning_token_id
            amount = self._order_amount if self._order_amount is not None else amount
            price = self._order_price if self._order_price is not None else price

        if self.dry_run:
            self._log(f"🔷 [{self.market_name}] DRY RUN - WOULD BUY:")
            self._log(
                f"  Side: {side}, Amount: ${amount}, Max Price: ${price}"
            )
            ask_str = f"{winning_ask:.4f}" if winning_ask is not None else "-"
            self._log(f"  Best Ask: ${ask_str}, Type: FOK MARKET")
            self.order_executed = True
            return

        if not self.client:
            self._log(f"❌ [{self.market_name}] CLOB client not initialized")
            return

        # Set in-progress flag and update attempt tracking
        self.order_in_progress = True
        self.order_attempts += 1
        self.last_order_attempt_time = time.time()

        attempt_msg = (
            f" (attempt {self.order_attempts}/{self.max_order_attempts})"
            if self.order_attempts > 1
            else ""
        )

        try:
            self._log(
                f"🔴 [{self.market_name}] MARKET ORDER{attempt_msg}: {side} ${amount} @ max ${price}"
            )

            # Use MarketOrderArgs - specifies amount (dollars) not size (tokens)
            order_args = MarketOrderArgs(
                token_id=winning_token_id,
                amount=amount,  # Dollars to spend
                price=price,  # Maximum price we'll accept
                side="BUY",
                nonce=self._order_nonce,
            )

            # Create market order
            created_order = await asyncio.to_thread(
                self.client.create_market_order,
                order_args,
                CreateOrderOptions(tick_size="0.01", neg_risk=False),  # type: ignore
            )
            self._log(f"✓ [{self.market_name}] Market order created")

            # Post as Fill-or-Kill
            response = await asyncio.to_thread(
                self.client.post_order,
                created_order,
                OrderType.FOK,  # type: ignore
            )

            self._log(f"✓ [{self.market_name}] FOK order posted: {response}")

            # Calculate executed price
            try:
                taking_amount = float(response.get("takingAmount", 0))  # type: ignore
                making_amount = float(response.get("makingAmount", 0))  # type: ignore
                if taking_amount > 0:
                    executed_price = making_amount / taking_amount
                    self._log(
                        f"💰 [{self.market_name}] Executed: {taking_amount:.6f} tokens @ ${executed_price:.4f} (spent ${making_amount:.2f})"
                    )
            except (KeyError, ValueError, ZeroDivisionError) as e:
                self._log(f"⚠️  [{self.market_name}] Could not calculate price: {e}")

            # Extract and verify
            try:
                order_id = response["orderID"]  # type: ignore
                await self.verify_order(str(order_id))
            except KeyError:
                self._log(
                    f"⚠️  [{self.market_name}] 'orderID' missing in response: {response}"
                )
            except Exception as e:
                self._log(f"⚠️  [{self.market_name}] Verification setup failed: {e}")

            # Set flags only after successful post
            self.order_executed = True
            self.order_in_progress = False

        except Exception as e:
            error_str = str(e)
            self._log(f"❌ [{self.market_name}] Order failed: {error_str}")

            # Clear in-progress flag to allow retries
            self.order_in_progress = False

            # Stop retrying for permanent errors
            if "not enough balance" in error_str or "allowance" in error_str:
                self._log(
                    "  → FATAL: Insufficient balance/allowance. Check wallet funding."
                )
                self.order_executed = True  # Stop retrying
            elif "403" in error_str or "cloudflare" in error_str.lower():
                ray_id = None
                match = re.search(
                    r"Cloudflare Ray ID:\s*<strong[^>]*>([^<]+)</strong>",
                    error_str,
                    re.IGNORECASE,
                )
                if match:
                    ray_id = match.group(1).strip()
                ray_note = f" Ray ID: {ray_id}" if ray_id else ""
                self._log(
                    "  → Cloudflare 403. Verify CLOB_HOST is https://clob.polymarket.com "
                    + "and API creds are set via create_or_derive_api_creds()."
                    + f"{ray_note} Cooldown or IP change may be required."
                )
                self.order_executed = True  # Stop retrying
            elif self.order_attempts < self.max_order_attempts:
                self._log(
                    f"  → Will retry ({self.order_attempts}/{self.max_order_attempts} attempts used)"
                )
            else:
                self._log("  → Max retry attempts reached. Giving up.")
                self.order_executed = True  # Stop retrying

    async def listen_to_market(self):
        """Listen to WebSocket and process market updates until market closes."""
        if self.ws is None:
            self._log("❌ WebSocket not initialized")
            return
        try:
            async for message in self.ws:
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    continue

                if not data or (isinstance(data, list) and len(data) == 0):
                    continue

                # Process market update(s)
                if isinstance(data, list):
                    for update in data:
                        await self.process_market_update(update)
                else:
                    await self.process_market_update(data)

                # Check if market closed
                if self.get_time_remaining() <= 0:
                    self._log(f"⏰ [{self.market_name}] Market closed")
                    break

        except websockets.exceptions.ConnectionClosed:
            self._log(f"⚠️  [{self.market_name}] WebSocket connection closed")
        except Exception as e:
            self._log(f"❌ [{self.market_name}] Error in market listener: {e}")

    async def run(self):
        """Main entry point: Connect and start trading."""
        try:
            connected = await self.connect_websocket()
            if not connected:
                self._log("Failed to connect to WebSocket. Exiting.")
                return

            # Rely on WebSocket events, with a time-based fallback for logging
            tasks = [self.listen_to_market(), self._trigger_check_loop()]
            if self.oracle_enabled:
                tasks.append(self._oracle_price_loop())
            await asyncio.gather(*tasks)

        except KeyboardInterrupt:
            self._log("⚠️  Interrupted by user. Shutting down...")
        finally:
            if self.ws:
                await self.ws.close()

            if self.oracle_enabled:
                top = sorted(
                    self._oracle_guard_reason_counts.items(),
                    key=lambda kv: kv[1],
                    reverse=True,
                )[:3]
                top_s = ", ".join(f"{k}={v}" for k, v in top) if top else "-"
                self._log(
                    f"📊 [{self.market_name}] Oracle guard summary: blocked={self._oracle_guard_block_count} (top: {top_s})"
                )
            self._log("✓ Trader shut down cleanly")

    async def _oracle_price_loop(self) -> None:
        """
        Stream Chainlink oracle prices from RTDS and compute lightweight metrics.

        This is intentionally independent from the CLOB websocket and does not
        hit polymarket.com except a best-effort single HTML fetch for price_to_beat
        when the trader starts late (Cloudflare risk).
        """
        if self.oracle_symbol is None:
            self._log(f"⚠️  [{self.market_name}] Oracle tracking enabled but symbol is unknown")
            return
        if self.oracle_tracker is None:
            return

        start_ms = getattr(self.oracle_window, "start_ms", None) if self.oracle_window else None
        end_ms = getattr(self.oracle_window, "end_ms", None) if self.oracle_window else None
        now_ms = int(time.time() * 1000)
        missed_start = False
        if start_ms is None:
            self._log(
                f"⚠️  [{self.market_name}] Oracle window start not parsed; price_to_beat capture may be unavailable"
            )
            missed_start = True
        else:
            lag_ms = now_ms - start_ms
            if lag_ms > self.oracle_beat_max_lag_ms:
                self._log(
                    f"⚠️  [{self.market_name}] Oracle start missed by {lag_ms/1000:.1f}s (max_lag={self.oracle_beat_max_lag_ms/1000:.1f}s); price_to_beat will be unavailable"
                )
                missed_start = True

        # Best-effort: backfill price_to_beat from the Polymarket event page HTML when
        # the trader starts late (common: last 2 minutes). This has Cloudflare risk,
        # so we do it at most once per market.
        if (
            missed_start
            and not self._oracle_html_beat_attempted
            and self.slug
            and self.oracle_window is not None
            and self.oracle_window.start_iso_z is not None
            and self.oracle_tracker.price_to_beat is None
        ):
            self._oracle_html_beat_attempted = True
            try:
                asset = self.market_name
                cadence = "fifteen"
                if start_ms is not None and end_ms is not None:
                    dur_ms = end_ms - start_ms
                    if abs(dur_ms - 300_000) <= 15_000:
                        cadence = "five"
                    elif abs(dur_ms - 900_000) <= 30_000:
                        cadence = "fifteen"

                async with aiohttp.ClientSession() as session:
                    event_page = EventPageClient(session)
                    open_price, _close_price = await event_page.fetch_past_results(
                        eslug=self.slug,
                        asset=asset,
                        cadence=cadence,
                        start_time_iso_z=self.oracle_window.start_iso_z,
                    )

                if open_price is not None:
                    self.oracle_tracker.price_to_beat = float(open_price)
                    self._log(
                        f"✓ [{self.market_name}] price_to_beat from event HTML: {open_price:,.2f}"
                    )
                else:
                    self._log(
                        f"⚠️  [{self.market_name}] Could not fetch price_to_beat from event HTML (Cloudflare or format change)"
                    )
            except Exception as e:
                self._log(
                    f"⚠️  [{self.market_name}] Event HTML price_to_beat fetch failed: {e}"
                )

        # Best-effort: map oracle direction (Up/Down) to our internal sides (YES/NO)
        # using Gamma market metadata. This is one HTTP call to gamma-api (not polymarket.com).
        if self.slug and (self.oracle_up_side is None or self.oracle_down_side is None):
            try:
                url = f"https://gamma-api.polymarket.com/markets/slug/{self.slug}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=15)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                        else:
                            data = None

                if isinstance(data, dict):
                    outcomes_raw = data.get("outcomes")
                    token_ids_raw = data.get("clobTokenIds")
                    outcomes = (
                        json.loads(outcomes_raw)
                        if isinstance(outcomes_raw, str)
                        else outcomes_raw
                    )
                    token_ids = (
                        json.loads(token_ids_raw)
                        if isinstance(token_ids_raw, str)
                        else token_ids_raw
                    )

                    if (
                        isinstance(outcomes, list)
                        and isinstance(token_ids, list)
                        and len(outcomes) == 2
                        and len(token_ids) == 2
                    ):
                        up_idx = next(
                            (
                                i
                                for i, o in enumerate(outcomes)
                                if isinstance(o, str) and o.strip().lower() == "up"
                            ),
                            None,
                        )
                        down_idx = next(
                            (
                                i
                                for i, o in enumerate(outcomes)
                                if isinstance(o, str) and o.strip().lower() == "down"
                            ),
                            None,
                        )
                        if up_idx is not None and down_idx is not None:
                            up_token = str(token_ids[up_idx])
                            down_token = str(token_ids[down_idx])

                            if up_token == self.token_id_yes:
                                self.oracle_up_side = "YES"
                            elif up_token == self.token_id_no:
                                self.oracle_up_side = "NO"

                            if down_token == self.token_id_yes:
                                self.oracle_down_side = "YES"
                            elif down_token == self.token_id_no:
                                self.oracle_down_side = "NO"

                            if self.oracle_up_side and self.oracle_down_side:
                                self._log(
                                    f"✓ [{self.market_name}] Oracle outcome mapping: Up→{self.oracle_up_side}, Down→{self.oracle_down_side}"
                                )
                            else:
                                self._log(
                                    f"⚠️  [{self.market_name}] Oracle mapping unresolved (token ids mismatch)"
                                )
            except Exception as e:
                self._log(f"⚠️  [{self.market_name}] Oracle mapping fetch failed: {e}")

        self._log(
            f"✓ [{self.market_name}] Oracle tracking enabled (RTDS Chainlink) symbol={self.oracle_symbol}"
        )

        rtds = RtdsClient()
        topics = {"crypto_prices_chainlink"}

        # Loop with reconnects until market close.
        while self.get_time_remaining() > 0:
            try:
                async for tick in rtds.iter_prices(
                    symbol=self.oracle_symbol, topics=topics, seconds=15.0
                ):
                    self.last_oracle_update_ts = time.time()

                    if start_ms is not None:
                        self.oracle_tracker.maybe_set_price_to_beat(
                            ts_ms=tick.ts_ms,
                            price=tick.price,
                            start_ms=start_ms,
                            max_lag_ms=self.oracle_beat_max_lag_ms,
                        )
                    self.oracle_snapshot = self.oracle_tracker.update(
                        ts_ms=tick.ts_ms, price=tick.price
                    )

                    # Log at most once per second to avoid spam.
                    now_ts = time.time()
                    if (now_ts - self._last_oracle_log_ts) >= 1.0:
                        snap = self.oracle_snapshot
                        beat = (
                            f"{snap.price_to_beat:,.2f}"
                            if snap.price_to_beat is not None
                            else "-"
                        )
                        delta = f"{snap.delta:,.2f}" if snap.delta is not None else "-"
                        delta_pct = (
                            f"{snap.delta_pct*100:.4f}%"
                            if snap.delta_pct is not None
                            else "-"
                        )
                        z = f"{snap.zscore:.2f}" if snap.zscore is not None else "-"
                        msg = (
                            f"[{self.market_name}] ORACLE {self.oracle_symbol}={snap.price:,.2f} | "
                            f"beat={beat} | Δ={delta} | Δ%={delta_pct} | z={z}"
                        )
                        self._log(msg)
                        self._last_oracle_log_ts = now_ts

                    if end_ms is not None and tick.ts_ms >= end_ms:
                        return

            except Exception as e:
                # Reconnect with backoff.
                self._log(f"⚠️  [{self.market_name}] Oracle RTDS error: {e}")
                await asyncio.sleep(2.0)

    async def _trigger_check_loop(self):
        """Fallback loop for time-based checks without trading on stale data."""
        while True:
            time_remaining = self.get_time_remaining()
            if time_remaining <= 0:
                break

            # Only check trigger if WS data is fresh
            if (
                self.orderbook.best_ask_yes is not None
                or self.orderbook.best_ask_no is not None
            ):
                now_ts = time.time()
                ws_fresh = (now_ts - self.last_ws_update_ts) <= self.WS_STALE_SECONDS
                if ws_fresh:
                    await self.check_trigger(time_remaining)
                else:
                    # Log stale WS status occasionally for visibility
                    if now_ts - self._last_stale_log_ts >= 5.0:
                        stale_msg = "".join(
                            [
                                f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] ",
                                f"[{self.market_name}] WS stale ({now_ts - self.last_ws_update_ts:.1f}s). ",
                                f"Time: {time_remaining:.2f}s",
                            ]
                        )
                        self._log(stale_msg)
                        self._last_stale_log_ts = now_ts

            await asyncio.sleep(1.0)
