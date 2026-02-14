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
