"""Dry-run simulator — records all trading decisions to SQLite.

Integrates with LastSecondTrader to record buy/skip decisions
and simulate stop-loss/take-profit on virtual positions.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from src.clob_types import (
    STOP_LOSS_ABSOLUTE,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TRAILING_STOP_PCT,
)
from src.trading.trade_db import TradeDatabase

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DryRunSimulator:
    """Records trade decisions and simulates virtual positions in SQLite."""

    def __init__(
        self,
        db: TradeDatabase,
        market_name: str,
        condition_id: str,
        dry_run: bool = True,
    ) -> None:
        self._db = db
        self.market_name = market_name
        self.condition_id = condition_id
        self.dry_run = dry_run
        # Cache open position ids for stop-loss checking
        self._open_position_ids: list[int] = []

    # -- decision recording --------------------------------------------------

    async def record_buy(
        self,
        *,
        side: str,
        price: float,
        amount: float,
        confidence: float | None = None,
        time_remaining: float | None = None,
        reason: str = "trigger",
        oracle_snap: Any | None = None,
    ) -> int:
        """Record a buy decision and open a virtual position."""
        logger.info(
            "[%s] DryRunSim.record_buy: side=%s price=%.4f amount=%.2f reason=%s time_remaining=%.2f",
            self.market_name, side, price, amount, reason,
            time_remaining if time_remaining is not None else -1,
        )
        now = time.time()
        oracle_kwargs = _extract_oracle(oracle_snap)

        # Record decision
        await self._db.insert_trade_decision(
            timestamp=now,
            timestamp_iso=_now_iso(),
            market_name=self.market_name,
            condition_id=self.condition_id,
            action="buy",
            side=side,
            price=price,
            amount=amount,
            confidence=confidence,
            time_remaining=time_remaining,
            reason=reason,
            dry_run=self.dry_run,
            **oracle_kwargs,
        )

        # Record in trades table too
        trade_id = await self._db.insert_trade(
            timestamp=now,
            timestamp_iso=_now_iso(),
            market_name=self.market_name,
            condition_id=self.condition_id,
            action="buy",
            side=side,
            price=price,
            amount=amount,
            reason=reason,
            dry_run=self.dry_run,
        )

        # Open virtual position
        stop_loss = max(price * (1 - STOP_LOSS_PCT), STOP_LOSS_ABSOLUTE)
        take_profit = price * (1 + TAKE_PROFIT_PCT)
        trailing = max(price * (1 - TRAILING_STOP_PCT), STOP_LOSS_ABSOLUTE)

        pos_id = await self._db.open_dry_run_position(
            trade_id=trade_id,
            condition_id=self.condition_id,
            market_name=self.market_name,
            side=side,
            entry_price=price,
            amount=amount,
            trailing_stop=trailing,
            stop_loss_price=stop_loss,
            take_profit_price=take_profit,
            opened_at=now,
        )
        self._open_position_ids.append(pos_id)
        return pos_id

    async def record_skip(
        self,
        *,
        reason: str,
        reason_detail: str | None = None,
        side: str | None = None,
        price: float | None = None,
        confidence: float | None = None,
        time_remaining: float | None = None,
        oracle_snap: Any | None = None,
    ) -> int:
        """Record a skip (no-buy) decision."""
        logger.info(
            "[%s] DryRunSim.record_skip: reason=%s side=%s price=%s time_remaining=%s",
            self.market_name, reason, side,
            f"{price:.4f}" if price is not None else "None",
            f"{time_remaining:.2f}" if time_remaining is not None else "None",
        )
        oracle_kwargs = _extract_oracle(oracle_snap)
        return await self._db.insert_trade_decision(
            timestamp=time.time(),
            timestamp_iso=_now_iso(),
            market_name=self.market_name,
            condition_id=self.condition_id,
            action="skip",
            side=side,
            price=price,
            confidence=confidence,
            time_remaining=time_remaining,
            reason=reason,
            reason_detail=reason_detail,
            dry_run=self.dry_run,
            **oracle_kwargs,
        )

    # -- virtual position simulation -----------------------------------------

    async def check_virtual_positions(self, current_price: float) -> list[dict]:
        """Check open virtual positions for stop-loss/take-profit triggers.

        Returns list of closed positions with details.
        """
        closed: list[dict] = []
        positions = await self._db.get_open_dry_run_positions()

        for pos in positions:
            # Only process positions for our condition
            if pos["condition_id"] != self.condition_id:
                continue

            entry = pos["entry_price"]
            amount = pos["amount"]
            stop_loss = pos.get("stop_loss_price") or max(
                entry * (1 - STOP_LOSS_PCT), STOP_LOSS_ABSOLUTE
            )
            take_profit = pos.get("take_profit_price") or entry * (1 + TAKE_PROFIT_PCT)
            trailing = pos.get("trailing_stop") or max(
                entry * (1 - TRAILING_STOP_PCT), STOP_LOSS_ABSOLUTE
            )

            effective_stop = max(stop_loss, trailing)
            now = time.time()

            if current_price <= effective_stop:
                # Stop-loss triggered
                pnl = (current_price - entry) / entry * amount
                pnl_pct = (current_price - entry) / entry * 100
                status = "trailing_stop" if trailing > stop_loss else "stop_loss"
                await self._db.close_dry_run_position(
                    pos["id"],
                    exit_price=current_price,
                    status=status,
                    close_reason=f"{status} at ${current_price:.4f}",
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    closed_at=now,
                )
                # Record sell trade
                await self._db.insert_trade(
                    timestamp=now,
                    timestamp_iso=_now_iso(),
                    market_name=pos["market_name"],
                    condition_id=pos["condition_id"],
                    action="sell",
                    side=pos["side"],
                    price=current_price,
                    amount=amount,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    reason=status,
                    dry_run=True,
                )
                closed.append({"id": pos["id"], "status": status, "pnl": pnl})

            elif current_price >= take_profit:
                # Take-profit triggered
                pnl = (current_price - entry) / entry * amount
                pnl_pct = (current_price - entry) / entry * 100
                await self._db.close_dry_run_position(
                    pos["id"],
                    exit_price=current_price,
                    status="take_profit",
                    close_reason=f"take_profit at ${current_price:.4f}",
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    closed_at=now,
                )
                await self._db.insert_trade(
                    timestamp=now,
                    timestamp_iso=_now_iso(),
                    market_name=pos["market_name"],
                    condition_id=pos["condition_id"],
                    action="sell",
                    side=pos["side"],
                    price=current_price,
                    amount=amount,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    reason="take_profit",
                    dry_run=True,
                )
                closed.append({"id": pos["id"], "status": "take_profit", "pnl": pnl})

            else:
                # Update trailing stop if price moved up
                new_trailing = max(
                    current_price * (1 - TRAILING_STOP_PCT), STOP_LOSS_ABSOLUTE
                )
                if new_trailing > trailing:
                    await self._db._db.execute(
                        "UPDATE dry_run_positions SET trailing_stop=? WHERE id=?",
                        (new_trailing, pos["id"]),
                    )
                    await self._db._db.commit()

        return closed


    # -- market resolution ---------------------------------------------------

    async def resolve_position(
        self, condition_id: str, outcome: str, winning_side: str
    ) -> list[dict]:
        """
        Resolve open dry-run positions after market settles.

        For binary Polymarket markets:
        - If we bought the winning side → PnL = ($1.00 - entry_price) * amount
        - If we bought the losing side → PnL = -entry_price * amount

        Args:
            condition_id: Market condition ID
            outcome: Outcome string (e.g. "YES", "NO", or token outcome name)
            winning_side: The side that resolved to $1.00

        Returns:
            List of resolved position dicts with PnL
        """
        positions = await self._db.get_open_dry_run_positions()
        resolved: list[dict] = []
        now = time.time()

        for pos in positions:
            if pos["condition_id"] != condition_id:
                continue

            entry = pos["entry_price"]
            amount = pos["amount"]
            side = pos["side"]

            if side.upper() == winning_side.upper():
                exit_price = 1.0
                pnl = (1.0 - entry) * amount
                pnl_pct = (1.0 - entry) / entry * 100 if entry > 0 else 0
                status = "resolved_win"
            else:
                exit_price = 0.0
                pnl = -entry * amount
                pnl_pct = -100.0
                status = "resolved_loss"

            await self._db.close_dry_run_position(
                pos["id"],
                exit_price=exit_price,
                status=status,
                close_reason=f"{status}: market resolved {outcome}, winning_side={winning_side}",
                pnl=pnl,
                pnl_pct=pnl_pct,
                closed_at=now,
            )

            await self._db.insert_trade(
                timestamp=now,
                timestamp_iso=_now_iso(),
                market_name=pos["market_name"],
                condition_id=pos["condition_id"],
                action="sell",
                side=pos["side"],
                price=exit_price,
                amount=amount,
                pnl=pnl,
                pnl_pct=pnl_pct,
                reason=status,
                dry_run=True,
            )

            resolved.append({
                "id": pos["id"],
                "side": side,
                "entry_price": entry,
                "exit_price": exit_price,
                "amount": amount,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "status": status,
            })

            logger.info(
                "[%s] Resolved position #%d: %s side=%s entry=%.4f pnl=%.4f",
                pos["market_name"], pos["id"], status, side, entry, pnl,
            )

        return resolved

    async def void_positions(self, condition_id: str, reason: str = "voided") -> list[dict]:
        """
        Void (annul) positions for a market — e.g. dispute resulted in 50-50 refund.

        All positions closed with PnL = 0 (full refund of entry).

        Args:
            condition_id: Market condition ID
            reason: Reason string for the void

        Returns:
            List of voided position dicts
        """
        positions = await self._db.get_open_dry_run_positions()
        voided: list[dict] = []
        now = time.time()

        for pos in positions:
            if pos["condition_id"] != condition_id:
                continue

            entry = pos["entry_price"]
            amount = pos["amount"]

            await self._db.close_dry_run_position(
                pos["id"],
                exit_price=entry,  # refund at entry price
                status="voided",
                close_reason=f"voided: {reason}",
                pnl=0.0,
                pnl_pct=0.0,
                closed_at=now,
            )

            await self._db.insert_trade(
                timestamp=now,
                timestamp_iso=_now_iso(),
                market_name=pos["market_name"],
                condition_id=pos["condition_id"],
                action="sell",
                side=pos["side"],
                price=entry,
                amount=amount,
                pnl=0.0,
                pnl_pct=0.0,
                reason="voided",
                dry_run=True,
            )

            voided.append({
                "id": pos["id"],
                "side": pos["side"],
                "entry_price": entry,
                "exit_price": entry,
                "amount": amount,
                "pnl": 0.0,
                "pnl_pct": 0.0,
                "status": "voided",
            })

            logger.info(
                "[%s] Voided position #%d: side=%s entry=%.4f (refund)",
                pos["market_name"], pos["id"], pos["side"], entry,
            )

        return voided

    async def resolve_all_markets(self, clob_client) -> list[dict]:
        """Check all open positions, query market resolution via API, resolve settled ones.

        Handles three outcomes:
        1. Normal resolution — tokens[].winner=True → resolve win/loss
        2. 50-50 / voided — is_50_50_outcome=True or no winner found → void (refund)
        3. Not yet resolved / disputed — skip (closed=False or accepting_orders=True)

        Args:
            clob_client: py_clob_client.client.ClobClient instance

        Returns:
            List of all resolved/voided position dicts
        """
        import asyncio as _asyncio
        from collections import defaultdict

        positions = await self._db.get_open_dry_run_positions()
        if not positions:
            logger.info("No open dry-run positions to resolve")
            return []

        # Group by condition_id
        by_condition: dict[str, list[dict]] = defaultdict(list)
        for pos in positions:
            by_condition[pos["condition_id"]].append(pos)

        all_resolved: list[dict] = []

        for cid in by_condition:
            try:
                market_info = await _asyncio.to_thread(clob_client.get_market, cid)
            except Exception as e:
                logger.warning("Failed to fetch market %s: %s", cid, e)
                continue

            if not market_info:
                continue

            closed = market_info.get("closed", False)
            accepting_orders = market_info.get("accepting_orders", True)

            # Skip markets that are still active or accepting orders (possibly disputed)
            if not closed or accepting_orders:
                logger.debug(
                    "Market %s not finalized (closed=%s, accepting_orders=%s)",
                    cid, closed, accepting_orders,
                )
                continue

            tokens = market_info.get("tokens", [])
            is_50_50 = market_info.get("is_50_50_outcome", False)

            # Find the winning token using tokens[].winner field (most reliable)
            winning_token = None
            for tok in tokens:
                if tok.get("winner") is True:
                    winning_token = tok
                    break

            # Case 1: 50-50 outcome or no winner → voided market (refund)
            if is_50_50 or (closed and winning_token is None):
                reason = "50-50 resolution" if is_50_50 else "no winner determined (possible dispute/void)"
                logger.info("Market %s voided: %s", cid, reason)
                voided = await self.void_positions(cid, reason)
                all_resolved.extend(voided)
                continue

            # Case 2: Normal resolution with a clear winner
            winning_side = winning_token.get("outcome", "YES")
            outcome_str = winning_side

            resolved = await self.resolve_position(cid, outcome_str, winning_side)
            all_resolved.extend(resolved)

        logger.info(
            "Resolved/voided %d positions across %d markets",
            len(all_resolved), len(by_condition),
        )
        return all_resolved


def _extract_oracle(snap: Any | None) -> dict[str, Any]:
    """Extract oracle fields from an OracleSnapshot for DB insertion."""
    if snap is None:
        return {
            "oracle_price": None,
            "oracle_z": None,
            "oracle_vol": None,
            "oracle_delta": None,
            "oracle_n_points": None,
        }
    return {
        "oracle_price": getattr(snap, "price", None),
        "oracle_z": getattr(snap, "zscore", None),
        "oracle_vol": getattr(snap, "vol_pct", None),
        "oracle_delta": getattr(snap, "delta", None),
        "oracle_n_points": getattr(snap, "n_points", None),
    }
