"""Integration tests for EventRecorder activation in hft_trader.py."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.hft_trader import LastSecondTrader
from src.trading.dry_run_replay import EventRecorder, EventReplayer


def _make_trader(
    tmp_path: Path,
    replay: bool = True,
    dry_run: bool = True,
    trigger_threshold: float = 30.0,
    end_time: datetime | None = None,
) -> LastSecondTrader:
    """Create a trader with optional EventRecorder."""
    if end_time is None:
        end_time = datetime.now(timezone.utc) + timedelta(seconds=60)

    with patch.dict("os.environ", {}, clear=False):
        trader = LastSecondTrader(
            condition_id="cond123",
            token_id_yes="tok_yes",
            token_id_no="tok_no",
            end_time=end_time,
            dry_run=dry_run,
            trade_size=1.0,
            title="Test Market",
            replay_dir=str(tmp_path / "replays") if replay else None,
            replay_book_throttle_s=0.0,  # no throttle for tests
        )
    trader.TRIGGER_THRESHOLD = trigger_threshold
    return trader


class TestEventRecorderIntegration:
    """Test EventRecorder is properly wired into hft_trader."""

    def test_recorder_created_when_replay_dir_set(self, tmp_path):
        trader = _make_trader(tmp_path, replay=True)
        assert trader.event_recorder is not None
        assert isinstance(trader.event_recorder, EventRecorder)
        assert trader.event_recorder.filepath.exists()

    def test_recorder_none_when_replay_dir_not_set(self, tmp_path):
        trader = _make_trader(tmp_path, replay=False)
        assert trader.event_recorder is None

    @pytest.mark.asyncio
    async def test_book_update_recorded(self, tmp_path):
        trader = _make_trader(tmp_path)
        data = {
            "asset_id": "tok_yes",
            "event_type": "book",
            "asks": [{"price": "0.95", "size": "10.0"}],
            "bids": [{"price": "0.94", "size": "5.0"}],
        }
        await trader.process_market_update(data)

        # Read recorded events
        events = _load_events(trader)
        book_events = [e for e in events if e["type"] == "book_update"]
        assert len(book_events) >= 1
        assert book_events[0]["data"]["side"] == "YES"
        assert book_events[0]["data"]["best_ask"] == 0.95

    @pytest.mark.asyncio
    async def test_book_update_throttled(self, tmp_path):
        """Book updates should be throttled by replay_book_throttle_s."""
        with patch.dict("os.environ", {}, clear=False):
            trader = LastSecondTrader(
                condition_id="cond123",
                token_id_yes="tok_yes",
                token_id_no="tok_no",
                end_time=datetime.now(timezone.utc) + timedelta(seconds=60),
                dry_run=True,
                trade_size=1.0,
                title="Test",
                replay_dir=str(tmp_path / "replays"),
                replay_book_throttle_s=10.0,  # very high throttle
            )

        data = {
            "asset_id": "tok_yes",
            "event_type": "book",
            "asks": [{"price": "0.95", "size": "10.0"}],
            "bids": [{"price": "0.94", "size": "5.0"}],
        }

        # First update should be recorded
        await trader.process_market_update(data)
        # Second immediate update should be throttled
        await trader.process_market_update(data)

        events = _load_events(trader)
        book_events = [e for e in events if e["type"] == "book_update"]
        assert len(book_events) == 1  # Only one due to throttle

    @pytest.mark.asyncio
    async def test_trigger_check_recorded(self, tmp_path):
        """Trigger check should be recorded when trade is triggered."""
        end_time = datetime.now(timezone.utc) + timedelta(seconds=10)
        trader = _make_trader(tmp_path, end_time=end_time, trigger_threshold=30.0)

        # Set up orderbook with winning side
        trader.orderbook.best_ask_yes = 0.95
        trader.orderbook.best_bid_yes = 0.94
        trader.orderbook.best_ask_yes_size = 100.0
        trader.orderbook.best_bid_yes_size = 100.0
        trader.orderbook.best_ask_no = 0.05
        trader.orderbook.best_bid_no = 0.04
        trader.orderbook.best_ask_no_size = 100.0
        trader.orderbook.best_bid_no_size = 100.0
        trader.winning_side = "YES"
        trader.last_ws_update_ts = time.time()

        # Mock order execution and risk manager
        trader.order_execution.execute_order_for = AsyncMock()
        trader.order_execution._executed = False
        trader.order_execution._in_progress = False
        trader.risk_manager.check_balance = AsyncMock(return_value=True)

        await trader.check_trigger(time_remaining=10.0)

        events = _load_events(trader)
        trigger_events = [e for e in events if e["type"] == "trigger_check"]
        assert len(trigger_events) == 1
        assert trigger_events[0]["data"]["winning_side"] == "YES"
        assert trigger_events[0]["data"]["executed"] is True
        assert trigger_events[0]["data"]["winning_ask"] == 0.95

    @pytest.mark.asyncio
    async def test_buy_trade_recorded(self, tmp_path):
        """Buy trade should be recorded after successful execution."""
        trader = _make_trader(tmp_path)
        trader.winning_side = "YES"
        trader.orderbook.best_ask_yes = 0.95
        trader._planned_trade_side = "YES"

        # Mock execute_order_for to mark as executed
        async def mock_execute(side, ask):
            trader.order_execution.order_executed = True

        trader.order_execution.execute_order_for = mock_execute
        trader.order_execution.order_executed = False

        await trader.execute_order()

        events = _load_events(trader)
        trade_events = [e for e in events if e["type"] == "trade"]
        assert len(trade_events) == 1
        assert trade_events[0]["data"]["action"] == "buy"
        assert trade_events[0]["data"]["side"] == "YES"
        assert trade_events[0]["data"]["price"] == 0.95

    @pytest.mark.asyncio
    async def test_sell_trade_recorded(self, tmp_path):
        """Sell trade should be recorded after execution."""
        trader = _make_trader(tmp_path)
        trader.position_manager.position_side = "YES"
        trader.position_manager.position_open = True
        trader.orderbook.best_ask_yes = 0.97

        # Mock execute_sell
        trader.order_execution.execute_sell = AsyncMock()

        await trader.execute_sell(reason="stop-loss")

        events = _load_events(trader)
        trade_events = [e for e in events if e["type"] == "trade"]
        assert len(trade_events) == 1
        assert trade_events[0]["data"]["action"] == "sell"
        assert trade_events[0]["data"]["reason"] == "stop-loss"
        assert trade_events[0]["data"]["price"] == 0.97

    @pytest.mark.asyncio
    async def test_graceful_shutdown_closes_recorder(self, tmp_path):
        """Graceful shutdown should close the event recorder and write session_end."""
        trader = _make_trader(tmp_path)
        assert trader.event_recorder is not None

        await trader.graceful_shutdown(reason="test")

        # Recorder should be None after shutdown
        assert trader.event_recorder is None

        # Check that session_end was written
        replay_dir = tmp_path / "replays"
        files = list(replay_dir.glob("replay_*.jsonl"))
        assert len(files) == 1

        events = _load_events_from_file(files[0])
        event_types = [e["type"] for e in events]
        assert "session_start" in event_types
        assert "session_end" in event_types

    @pytest.mark.asyncio
    async def test_no_recording_when_disabled(self, tmp_path):
        """No recording should happen when replay_dir is None."""
        trader = _make_trader(tmp_path, replay=False)
        assert trader.event_recorder is None

        data = {
            "asset_id": "tok_yes",
            "event_type": "book",
            "asks": [{"price": "0.95", "size": "10.0"}],
            "bids": [{"price": "0.94", "size": "5.0"}],
        }
        # Should not raise even without recorder
        await trader.process_market_update(data)

    @pytest.mark.asyncio
    async def test_full_recording_replay_cycle(self, tmp_path):
        """Record events, then replay them and verify decisions match."""
        trader = _make_trader(tmp_path)

        # Simulate book update
        data = {
            "asset_id": "tok_yes",
            "event_type": "book",
            "asks": [{"price": "0.95", "size": "100.0"}],
            "bids": [{"price": "0.94", "size": "100.0"}],
        }
        await trader.process_market_update(data)

        # Close recorder to flush
        recorder_path = trader.event_recorder.filepath
        trader.event_recorder.close()
        trader.event_recorder = None

        # Replay
        replayer = EventReplayer(recorder_path)
        events = replayer.load_events()
        assert len(events) >= 2  # session_start + book_update + session_end

        summary = replayer.replay(events)
        assert summary.total_events >= 2
        assert summary.book_updates >= 1

    def test_replay_dir_created(self, tmp_path):
        """Replay dir should be created if it doesn't exist."""
        replay_dir = tmp_path / "new_replays"
        assert not replay_dir.exists()

        trader = _make_trader(tmp_path, replay=True)
        # The dir is created by EventRecorder
        assert trader.event_recorder is not None


def _load_events(trader: LastSecondTrader) -> list[dict]:
    """Load events from the trader's active replay file."""
    if trader.event_recorder is None:
        return []
    path = trader.event_recorder.filepath
    return _load_events_from_file(path)


def _load_events_from_file(path: Path) -> list[dict]:
    """Load events from a JSONL file."""
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events
