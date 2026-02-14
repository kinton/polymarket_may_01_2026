"""SQLite-backed persistence backends for positions and events.

Drop-in replacements for JSON-based PositionPersister and JSONL-based EventRecorder.
Backed by TradeDatabase (aiosqlite).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from src.trading.trade_db import TradeDatabase

logger = logging.getLogger(__name__)


class SQLitePositionPersister:
    """Position persister using SQLite via TradeDatabase.

    Same interface as PositionPersister (save/load/remove/exists)
    but synchronous methods run the async DB calls via asyncio.
    """

    def __init__(
        self,
        condition_id: str,
        trade_db: TradeDatabase,
        market_name: str = "",
        logger: logging.Logger | None = None,
    ) -> None:
        self.condition_id = condition_id
        self._db = trade_db
        self._market_name = market_name
        self._logger = logger

    def _run(self, coro: Any) -> Any:
        """Run an async coroutine from sync context."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        # If already in an event loop, create a task
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result(timeout=5)

    def save(self, state: dict[str, Any]) -> None:
        """Save position state to SQLite."""
        db_state = {
            "market_name": state.get("market_name", self._market_name),
            "side": state.get("side", "YES"),
            "entry_price": state.get("entry_price", 0),
            "trailing_stop_price": state.get("trailing_stop_price"),
            "is_open": True,
            "opened_at": state.get("timestamp", time.time()),
        }
        try:
            self._run(self._db.save_position(self.condition_id, db_state))
            if self._logger:
                self._logger.debug("Position state saved to SQLite: %s", self.condition_id[:16])
        except Exception as e:
            if self._logger:
                self._logger.error("Failed to save position to SQLite: %s", e)

    def load(self) -> dict[str, Any] | None:
        """Load position state from SQLite."""
        try:
            row = self._run(self._db.load_position(self.condition_id))
            if row and row.get("is_open"):
                if self._logger:
                    self._logger.info("Restored position from SQLite: %s", self.condition_id[:16])
                return row
            return None
        except Exception as e:
            if self._logger:
                self._logger.warning("Failed to load position from SQLite: %s", e)
            return None

    def remove(self) -> None:
        """Close position in SQLite."""
        try:
            self._run(self._db.close_position(self.condition_id, "closed"))
            if self._logger:
                self._logger.debug("Position closed in SQLite: %s", self.condition_id[:16])
        except Exception as e:
            if self._logger:
                self._logger.warning("Failed to close position in SQLite: %s", e)

    def exists(self) -> bool:
        """Check if an open position exists in SQLite."""
        try:
            row = self._run(self._db.load_position(self.condition_id))
            return row is not None and bool(row.get("is_open"))
        except Exception:
            return False


class SQLiteEventRecorder:
    """Event recorder using SQLite via TradeDatabase.

    Same recording interface as EventRecorder but writes to the events table.
    """

    def __init__(
        self,
        trade_db: TradeDatabase,
        market_name: str = "unknown",
        condition_id: str = "",
    ) -> None:
        self._db = trade_db
        self._market_name = market_name
        self._condition_id = condition_id
        self._session_id = str(uuid.uuid4())
        self._event_count = 0
        self._closed = False

        # Write session_start
        self._run(self._db.insert_event(
            session_id=self._session_id,
            timestamp=time.time(),
            event_type="session_start",
            condition_id=condition_id or None,
            market_name=market_name,
            data_json=json.dumps({
                "market_name": market_name,
                "condition_id": condition_id,
                "start_time": datetime.now(timezone.utc).isoformat(),
            }),
        ))
        self._event_count += 1

    def _run(self, coro: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result(timeout=5)

    def _write_event(self, event_type: str, data: dict) -> None:
        if self._closed:
            return
        try:
            self._run(self._db.insert_event(
                session_id=self._session_id,
                timestamp=time.time(),
                event_type=event_type,
                condition_id=self._condition_id or None,
                market_name=self._market_name or None,
                data_json=json.dumps(data),
            ))
            self._event_count += 1
        except Exception as e:
            logger.warning("Failed to write event to SQLite: %s", e)

    def record_book_update(
        self, side: str,
        best_ask: float | None, best_ask_size: float | None,
        best_bid: float | None, best_bid_size: float | None,
    ) -> None:
        self._write_event("book_update", {
            "side": side, "best_ask": best_ask, "best_ask_size": best_ask_size,
            "best_bid": best_bid, "best_bid_size": best_bid_size,
        })

    def record_trigger_check(
        self, time_remaining: float, winning_side: str | None,
        winning_ask: float | None, executed: bool = False, reason: str = "",
    ) -> None:
        self._write_event("trigger_check", {
            "time_remaining": time_remaining, "winning_side": winning_side,
            "winning_ask": winning_ask, "executed": executed, "reason": reason,
        })

    def record_trade(
        self, action: str, side: str, price: float, size: float,
        success: bool, order_id: str = "", reason: str = "",
    ) -> None:
        self._write_event("trade", {
            "action": action, "side": side, "price": price, "size": size,
            "success": success, "order_id": order_id, "reason": reason,
        })

    def record_price_change(
        self, side: str, old_price: float | None, new_price: float | None,
    ) -> None:
        self._write_event("price_change", {
            "side": side, "old_price": old_price, "new_price": new_price,
        })

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def event_count(self) -> int:
        return self._event_count

    def close(self) -> None:
        if self._closed:
            return
        self._write_event("session_end", {"total_events": self._event_count})
        self._closed = True

    def __enter__(self) -> "SQLiteEventRecorder":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
