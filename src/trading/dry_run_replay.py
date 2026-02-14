"""
Dry-run replay engine for Polymarket trading bot.

Records structured market events during live trading and replays them
to test strategy changes without risking real capital.

Usage:
    # Recording (automatic during trading):
    recorder = EventRecorder("data/replays")
    recorder.record_book_update("YES", 0.95, 10.0, 0.94, 5.0)
    recorder.record_trigger_check(30.0, "YES", 0.95, executed=True)

    # Replay:
    replayer = EventReplayer("data/replays/replay_2026-02-14T05-00-00.jsonl")
    events = replayer.load_events()
    summary = replayer.replay(events, strategy_fn=my_strategy)
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class ReplayEvent:
    """A single recorded event."""

    timestamp: float
    event_type: str
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.timestamp,
            "type": self.event_type,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReplayEvent:
        return cls(
            timestamp=d["ts"],
            event_type=d["type"],
            data=d.get("data", {}),
        )


@dataclass
class ReplaySummary:
    """Summary of a replay run."""

    total_events: int = 0
    book_updates: int = 0
    trigger_checks: int = 0
    trades_executed: int = 0
    trades_skipped: int = 0
    total_pnl: float = 0.0
    decisions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_events": self.total_events,
            "book_updates": self.book_updates,
            "trigger_checks": self.trigger_checks,
            "trades_executed": self.trades_executed,
            "trades_skipped": self.trades_skipped,
            "total_pnl": self.total_pnl,
            "decisions": self.decisions,
        }


# Type alias for strategy function
StrategyFn = Callable[[dict[str, Any]], bool]


def _default_strategy(context: dict[str, Any]) -> bool:
    """Default strategy: buy if ask <= MAX_BUY_PRICE and time <= TRIGGER_THRESHOLD."""
    ask = context.get("winning_ask")
    time_remaining = context.get("time_remaining", float("inf"))
    max_buy = context.get("max_buy_price", 0.99)
    trigger_threshold = context.get("trigger_threshold", 30.0)

    if ask is None:
        return False
    return ask <= max_buy and time_remaining <= trigger_threshold


class EventRecorder:
    """Records structured market events to JSONL files for later replay."""

    def __init__(
        self,
        replay_dir: str = "data/replays",
        market_name: str = "unknown",
        condition_id: str = "",
    ) -> None:
        self._replay_dir = Path(replay_dir)
        self._replay_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        safe_name = market_name.replace(" ", "_")[:50]
        filename = f"replay_{ts}_{safe_name}.jsonl"
        self._filepath = self._replay_dir / filename
        self._file = open(self._filepath, "a")  # noqa: SIM115
        self._event_count = 0

        # Write header event
        self._write_event(ReplayEvent(
            timestamp=time.time(),
            event_type="session_start",
            data={
                "market_name": market_name,
                "condition_id": condition_id,
                "start_time": datetime.now(timezone.utc).isoformat(),
            },
        ))

    def _write_event(self, event: ReplayEvent) -> None:
        """Write a single event to the JSONL file."""
        try:
            line = json.dumps(event.to_dict(), separators=(",", ":"))
            self._file.write(line + "\n")
            self._file.flush()
            self._event_count += 1
        except Exception as e:
            logger.warning("Failed to write replay event: %s", e)

    def record_book_update(
        self,
        side: str,
        best_ask: float | None,
        best_ask_size: float | None,
        best_bid: float | None,
        best_bid_size: float | None,
    ) -> None:
        """Record an orderbook update."""
        self._write_event(ReplayEvent(
            timestamp=time.time(),
            event_type="book_update",
            data={
                "side": side,
                "best_ask": best_ask,
                "best_ask_size": best_ask_size,
                "best_bid": best_bid,
                "best_bid_size": best_bid_size,
            },
        ))

    def record_trigger_check(
        self,
        time_remaining: float,
        winning_side: str | None,
        winning_ask: float | None,
        executed: bool = False,
        reason: str = "",
    ) -> None:
        """Record a trigger check decision point."""
        self._write_event(ReplayEvent(
            timestamp=time.time(),
            event_type="trigger_check",
            data={
                "time_remaining": time_remaining,
                "winning_side": winning_side,
                "winning_ask": winning_ask,
                "executed": executed,
                "reason": reason,
            },
        ))

    def record_trade(
        self,
        action: str,
        side: str,
        price: float,
        size: float,
        success: bool,
        order_id: str = "",
        reason: str = "",
    ) -> None:
        """Record a trade execution."""
        self._write_event(ReplayEvent(
            timestamp=time.time(),
            event_type="trade",
            data={
                "action": action,
                "side": side,
                "price": price,
                "size": size,
                "success": success,
                "order_id": order_id,
                "reason": reason,
            },
        ))

    def record_price_change(
        self,
        side: str,
        old_price: float | None,
        new_price: float | None,
    ) -> None:
        """Record a price change event."""
        self._write_event(ReplayEvent(
            timestamp=time.time(),
            event_type="price_change",
            data={
                "side": side,
                "old_price": old_price,
                "new_price": new_price,
            },
        ))

    @property
    def filepath(self) -> Path:
        return self._filepath

    @property
    def event_count(self) -> int:
        return self._event_count

    def close(self) -> None:
        """Close the replay file."""
        self._write_event(ReplayEvent(
            timestamp=time.time(),
            event_type="session_end",
            data={"total_events": self._event_count},
        ))
        self._file.close()

    def __enter__(self) -> EventRecorder:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class EventReplayer:
    """Replays recorded events through a strategy function."""

    def __init__(self, filepath: str | Path) -> None:
        self._filepath = Path(filepath)
        if not self._filepath.exists():
            raise FileNotFoundError(f"Replay file not found: {self._filepath}")

    def load_events(self) -> list[ReplayEvent]:
        """Load all events from the JSONL file."""
        events: list[ReplayEvent] = []
        with open(self._filepath) as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    events.append(ReplayEvent.from_dict(data))
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning("Skipping malformed line %d: %s", line_num, e)
        return events

    def replay(
        self,
        events: list[ReplayEvent],
        strategy_fn: StrategyFn | None = None,
        max_buy_price: float = 0.99,
        trigger_threshold: float = 30.0,
    ) -> ReplaySummary:
        """
        Replay events through a strategy function.

        Args:
            events: List of recorded events.
            strategy_fn: Function(context) -> bool (should we execute?).
                         Receives dict with: winning_ask, winning_side,
                         time_remaining, max_buy_price, trigger_threshold,
                         orderbook (current state).
            max_buy_price: Max buy price parameter for default strategy.
            trigger_threshold: Trigger threshold for default strategy.

        Returns:
            ReplaySummary with results.
        """
        if strategy_fn is None:
            strategy_fn = _default_strategy

        summary = ReplaySummary()

        # Simulated orderbook state
        orderbook: dict[str, float | None] = {
            "best_ask_yes": None,
            "best_ask_no": None,
            "best_bid_yes": None,
            "best_bid_no": None,
        }

        for event in events:
            summary.total_events += 1

            if event.event_type == "book_update":
                summary.book_updates += 1
                side = event.data.get("side", "").upper()
                key_suffix = "yes" if side == "YES" else "no"
                orderbook[f"best_ask_{key_suffix}"] = event.data.get("best_ask")
                orderbook[f"best_bid_{key_suffix}"] = event.data.get("best_bid")

            elif event.event_type == "trigger_check":
                summary.trigger_checks += 1
                context = {
                    "winning_ask": event.data.get("winning_ask"),
                    "winning_side": event.data.get("winning_side"),
                    "time_remaining": event.data.get("time_remaining", float("inf")),
                    "max_buy_price": max_buy_price,
                    "trigger_threshold": trigger_threshold,
                    "orderbook": dict(orderbook),
                    "original_executed": event.data.get("executed", False),
                }

                should_execute = strategy_fn(context)
                decision = {
                    "timestamp": event.timestamp,
                    "time_remaining": context["time_remaining"],
                    "winning_side": context["winning_side"],
                    "winning_ask": context["winning_ask"],
                    "original_decision": context["original_executed"],
                    "replay_decision": should_execute,
                    "changed": should_execute != context["original_executed"],
                }
                summary.decisions.append(decision)

                if should_execute:
                    summary.trades_executed += 1
                else:
                    summary.trades_skipped += 1

            elif event.event_type == "trade":
                # Track actual PnL from recorded trades for comparison
                if event.data.get("success"):
                    price = event.data.get("price", 0)
                    action = event.data.get("action", "")
                    size = event.data.get("size", 0)
                    if action == "sell":
                        summary.total_pnl += price * size
                    elif action == "buy":
                        summary.total_pnl -= price * size

        return summary

    @staticmethod
    def list_replays(replay_dir: str = "data/replays") -> list[dict[str, Any]]:
        """List available replay files with metadata."""
        replay_path = Path(replay_dir)
        if not replay_path.exists():
            return []

        replays: list[dict[str, Any]] = []
        for f in sorted(replay_path.glob("replay_*.jsonl")):
            stat = f.stat()
            # Read first line for metadata
            meta: dict[str, Any] = {}
            try:
                with open(f) as fh:
                    first_line = fh.readline().strip()
                    if first_line:
                        data = json.loads(first_line)
                        if data.get("type") == "session_start":
                            meta = data.get("data", {})
            except Exception:
                pass

            replays.append({
                "file": str(f),
                "size_bytes": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "market_name": meta.get("market_name", "unknown"),
                "condition_id": meta.get("condition_id", ""),
            })
        return replays


def get_replay_dir() -> str:
    """Get replay directory from environment or default."""
    return os.environ.get("REPLAY_DIR", "data/replays")
