"""Tests for dry-run replay engine."""

import json
import tempfile
from pathlib import Path

import pytest

from src.trading.dry_run_replay import (
    EventRecorder,
    EventReplayer,
    ReplayEvent,
    ReplaySummary,
    _default_strategy,
)


class TestReplayEvent:
    def test_to_dict(self):
        event = ReplayEvent(timestamp=1000.0, event_type="book_update", data={"side": "YES"})
        d = event.to_dict()
        assert d["ts"] == 1000.0
        assert d["type"] == "book_update"
        assert d["data"]["side"] == "YES"

    def test_from_dict(self):
        d = {"ts": 1000.0, "type": "trade", "data": {"action": "buy"}}
        event = ReplayEvent.from_dict(d)
        assert event.timestamp == 1000.0
        assert event.event_type == "trade"
        assert event.data["action"] == "buy"

    def test_roundtrip(self):
        original = ReplayEvent(timestamp=42.5, event_type="test", data={"x": 1})
        restored = ReplayEvent.from_dict(original.to_dict())
        assert restored.timestamp == original.timestamp
        assert restored.event_type == original.event_type
        assert restored.data == original.data


class TestEventRecorder:
    def test_creates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = EventRecorder(replay_dir=tmpdir, market_name="Test Market")
            assert recorder.filepath.exists()
            assert recorder.event_count == 1  # session_start
            recorder.close()

    def test_record_book_update(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with EventRecorder(replay_dir=tmpdir, market_name="BTC") as rec:
                rec.record_book_update("YES", 0.95, 10.0, 0.94, 5.0)
                assert rec.event_count == 2  # session_start + book_update

            # Read back
            files = list(Path(tmpdir).glob("*.jsonl"))
            assert len(files) == 1
            lines = files[0].read_text().strip().split("\n")
            # session_start + book_update + session_end
            assert len(lines) == 3
            book = json.loads(lines[1])
            assert book["type"] == "book_update"
            assert book["data"]["best_ask"] == 0.95

    def test_record_trigger_check(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with EventRecorder(replay_dir=tmpdir) as rec:
                rec.record_trigger_check(25.0, "YES", 0.96, executed=True, reason="late window")
            files = list(Path(tmpdir).glob("*.jsonl"))
            lines = files[0].read_text().strip().split("\n")
            trigger = json.loads(lines[1])
            assert trigger["type"] == "trigger_check"
            assert trigger["data"]["time_remaining"] == 25.0
            assert trigger["data"]["executed"] is True

    def test_record_trade(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with EventRecorder(replay_dir=tmpdir) as rec:
                rec.record_trade("buy", "YES", 0.95, 1.1, True, order_id="abc123")
            files = list(Path(tmpdir).glob("*.jsonl"))
            lines = files[0].read_text().strip().split("\n")
            trade = json.loads(lines[1])
            assert trade["type"] == "trade"
            assert trade["data"]["price"] == 0.95
            assert trade["data"]["order_id"] == "abc123"

    def test_record_price_change(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with EventRecorder(replay_dir=tmpdir) as rec:
                rec.record_price_change("YES", 0.90, 0.95)
            files = list(Path(tmpdir).glob("*.jsonl"))
            lines = files[0].read_text().strip().split("\n")
            pc = json.loads(lines[1])
            assert pc["type"] == "price_change"
            assert pc["data"]["old_price"] == 0.90
            assert pc["data"]["new_price"] == 0.95

    def test_context_manager(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with EventRecorder(replay_dir=tmpdir) as rec:
                rec.record_book_update("NO", 0.05, 2.0, 0.04, 1.0)
                filepath = rec.filepath
            # File should have session_end
            lines = filepath.read_text().strip().split("\n")
            last = json.loads(lines[-1])
            assert last["type"] == "session_end"


class TestEventReplayer:
    def _create_replay_file(self, tmpdir: str, events: list[dict]) -> Path:
        filepath = Path(tmpdir) / "test_replay.jsonl"
        with open(filepath, "w") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")
        return filepath

    def test_load_events(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            events = [
                {"ts": 1.0, "type": "session_start", "data": {}},
                {"ts": 2.0, "type": "book_update", "data": {"side": "YES"}},
            ]
            fp = self._create_replay_file(tmpdir, events)
            replayer = EventReplayer(fp)
            loaded = replayer.load_events()
            assert len(loaded) == 2
            assert loaded[0].event_type == "session_start"

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            EventReplayer("/nonexistent/file.jsonl")

    def test_replay_with_default_strategy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            events = [
                {"ts": 1.0, "type": "session_start", "data": {}},
                {"ts": 2.0, "type": "book_update", "data": {"side": "YES", "best_ask": 0.95, "best_bid": 0.94}},
                {"ts": 3.0, "type": "trigger_check", "data": {
                    "time_remaining": 25.0, "winning_side": "YES", "winning_ask": 0.95, "executed": True,
                }},
                {"ts": 4.0, "type": "trigger_check", "data": {
                    "time_remaining": 25.0, "winning_side": "YES", "winning_ask": 1.00, "executed": False,
                }},
            ]
            fp = self._create_replay_file(tmpdir, events)
            replayer = EventReplayer(fp)
            loaded = replayer.load_events()
            summary = replayer.replay(loaded)
            assert summary.total_events == 4
            assert summary.book_updates == 1
            assert summary.trigger_checks == 2
            assert summary.trades_executed == 1  # 0.95 <= 0.99
            assert summary.trades_skipped == 1  # 1.00 > 0.99

    def test_replay_custom_strategy(self):
        """Custom strategy that only buys below 0.90."""
        with tempfile.TemporaryDirectory() as tmpdir:
            events = [
                {"ts": 1.0, "type": "trigger_check", "data": {
                    "time_remaining": 20.0, "winning_side": "YES", "winning_ask": 0.95, "executed": True,
                }},
                {"ts": 2.0, "type": "trigger_check", "data": {
                    "time_remaining": 20.0, "winning_side": "YES", "winning_ask": 0.85, "executed": False,
                }},
            ]
            fp = self._create_replay_file(tmpdir, events)
            replayer = EventReplayer(fp)
            loaded = replayer.load_events()

            def strict_strategy(ctx):
                ask = ctx.get("winning_ask")
                return ask is not None and ask < 0.90

            summary = replayer.replay(loaded, strategy_fn=strict_strategy)
            assert summary.trades_executed == 1  # only 0.85
            assert summary.trades_skipped == 1  # 0.95 too high
            # Check that decision changed from original
            assert summary.decisions[0]["changed"] is True  # was executed, now skipped
            assert summary.decisions[1]["changed"] is True  # was skipped, now executed

    def test_replay_tracks_pnl(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            events = [
                {"ts": 1.0, "type": "trade", "data": {
                    "action": "buy", "side": "YES", "price": 0.95, "size": 1.0, "success": True,
                }},
                {"ts": 2.0, "type": "trade", "data": {
                    "action": "sell", "side": "YES", "price": 1.0, "size": 1.0, "success": True,
                }},
            ]
            fp = self._create_replay_file(tmpdir, events)
            replayer = EventReplayer(fp)
            loaded = replayer.load_events()
            summary = replayer.replay(loaded)
            assert abs(summary.total_pnl - 0.05) < 1e-9  # sold at 1.0, bought at 0.95

    def test_replay_skips_malformed_lines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "bad.jsonl"
            with open(filepath, "w") as f:
                f.write('{"ts":1,"type":"book_update","data":{}}\n')
                f.write("not json\n")
                f.write('{"ts":2,"type":"book_update","data":{}}\n')
            replayer = EventReplayer(filepath)
            loaded = replayer.load_events()
            assert len(loaded) == 2

    def test_list_replays(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with EventRecorder(replay_dir=tmpdir, market_name="BTC Test") as rec:
                rec.record_book_update("YES", 0.95, 10.0, 0.94, 5.0)
            replays = EventReplayer.list_replays(tmpdir)
            assert len(replays) == 1
            assert replays[0]["market_name"] == "BTC Test"

    def test_list_replays_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            replays = EventReplayer.list_replays(tmpdir)
            assert replays == []

    def test_list_replays_nonexistent_dir(self):
        replays = EventReplayer.list_replays("/nonexistent/dir")
        assert replays == []


class TestDefaultStrategy:
    def test_buy_when_conditions_met(self):
        assert _default_strategy({"winning_ask": 0.95, "time_remaining": 25.0}) is True

    def test_skip_when_ask_too_high(self):
        assert _default_strategy({"winning_ask": 1.00, "time_remaining": 25.0}) is False

    def test_skip_when_time_too_early(self):
        assert _default_strategy({"winning_ask": 0.95, "time_remaining": 60.0}) is False

    def test_skip_when_no_ask(self):
        assert _default_strategy({"winning_ask": None, "time_remaining": 25.0}) is False


class TestReplaySummary:
    def test_to_dict(self):
        s = ReplaySummary(total_events=10, trades_executed=3, total_pnl=0.15)
        d = s.to_dict()
        assert d["total_events"] == 10
        assert d["trades_executed"] == 3
        assert d["total_pnl"] == 0.15
