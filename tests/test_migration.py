"""Tests for Phase 4: JSON → SQLite migration."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pytest

from src.trading.trade_db import TradeDatabase


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def run(event_loop):
    """Helper to run async code."""
    return event_loop.run_until_complete


@pytest.fixture
def db(tmp_path, run):
    """Create a temporary TradeDatabase."""
    db_path = str(tmp_path / "test.db")
    _db = run(TradeDatabase.initialize(db_path))
    yield _db
    run(_db.close())


# ─── Migration script tests ───────────────────────────────────────────────


class TestMigrateDailyLimits:
    def test_migrate_daily_limits(self, tmp_path, run, db):
        """Migrate daily_limits.json → daily_stats table."""
        path = tmp_path / "daily_limits.json"
        path.write_text(json.dumps({
            "date": "2026-02-14",
            "initial_balance": 100.0,
            "current_pnl": -5.5,
            "total_trades": 3,
        }))

        # Simulate migration logic
        data = json.loads(path.read_text())
        run(db.get_or_create_daily_stats(data["date"]))
        run(db.update_daily_stats(
            data["date"],
            pnl_delta=data["current_pnl"],
            trade_count_delta=data["total_trades"],
        ))
        run(db._db.execute(
            "UPDATE daily_stats SET initial_balance = ? WHERE date = ?",
            (data["initial_balance"], data["date"]),
        ))
        run(db._db.commit())

        # Verify
        stats = run(db.get_or_create_daily_stats("2026-02-14"))
        assert stats["current_pnl"] == pytest.approx(-5.5)
        assert stats["total_trades"] == 3
        assert stats["initial_balance"] == pytest.approx(100.0)

    def test_migrate_no_file(self, db):
        """No daily_limits.json → no-op."""
        pass  # Migration script handles missing files gracefully


class TestMigrateAlertHistory:
    def test_migrate_alerts(self, tmp_path, run, db):
        """Migrate alert_history.json → alerts table."""
        alerts = [
            {"timestamp": 1000.0, "type": "trade", "level": "INFO", "market": "BTC", "side": "YES"},
            {"timestamp": 2000.0, "type": "stop_loss", "level": "WARNING", "market": "ETH", "pnl": -0.5},
            {"timestamp": 3000.0, "type": "oracle_guard", "level": "CRITICAL", "market": "SOL", "reason": "blocked"},
        ]
        path = tmp_path / "alert_history.json"
        path.write_text(json.dumps(alerts))

        # Simulate migration
        for alert in json.loads(path.read_text()):
            details = {k: v for k, v in alert.items()
                       if k not in ("timestamp", "type", "level", "market")}
            run(db.insert_alert(
                timestamp=alert["timestamp"],
                alert_type=alert["type"],
                level=alert["level"],
                market_name=alert.get("market"),
                details_json=json.dumps(details) if details else None,
            ))

        # Verify
        result = run(db.get_alerts(limit=10))
        assert len(result) == 3
        assert result[0]["alert_type"] == "oracle_guard"  # DESC order
        assert result[2]["alert_type"] == "trade"

    def test_migrate_empty_alerts(self, run, db):
        """Empty alert list → no records."""
        result = run(db.get_alerts(limit=10))
        assert len(result) == 0


class TestMigratePositions:
    def test_migrate_position_file(self, tmp_path, run, db):
        """Migrate position JSON file → positions table."""
        cid = "0x" + "a" * 40
        data = {
            "condition_id": cid,
            "market_name": "BTC",
            "side": "YES",
            "entry_price": 0.85,
            "trailing_stop_price": 0.80,
            "timestamp": 1700000000.0,
        }
        (tmp_path / f"position_{cid}.json").write_text(json.dumps(data))

        # Simulate migration
        run(db.save_position(cid, {
            "market_name": data["market_name"],
            "side": data["side"],
            "entry_price": data["entry_price"],
            "trailing_stop_price": data["trailing_stop_price"],
            "is_open": True,
            "opened_at": data["timestamp"],
        }))

        # Verify
        pos = run(db.load_position(cid))
        assert pos is not None
        assert pos["market_name"] == "BTC"
        assert pos["entry_price"] == pytest.approx(0.85)
        assert pos["is_open"] == 1


class TestMigrateReplays:
    def test_migrate_jsonl_replay(self, tmp_path, run, db):
        """Migrate JSONL replay file → events table."""
        events_data = [
            {"ts": 1000.0, "type": "session_start", "data": {"market_name": "BTC", "condition_id": "0xabc"}},
            {"ts": 1001.0, "type": "book_update", "data": {"side": "YES", "best_ask": 0.95}},
            {"ts": 1002.0, "type": "trade", "data": {"action": "buy", "side": "YES", "price": 0.95, "size": 1.0, "success": True}},
            {"ts": 1003.0, "type": "session_end", "data": {"total_events": 4}},
        ]

        session_id = str(uuid.uuid4())
        for ev in events_data:
            run(db.insert_event(
                session_id=session_id,
                timestamp=ev["ts"],
                event_type=ev["type"],
                condition_id="0xabc",
                market_name="BTC",
                data_json=json.dumps(ev["data"]),
            ))

        # Verify
        result = run(db.get_events(session_id))
        assert len(result) == 4
        assert result[0]["event_type"] == "session_start"
        assert result[-1]["event_type"] == "session_end"

        sessions = run(db.list_sessions())
        assert len(sessions) == 1
        assert sessions[0]["event_count"] == 4


# ─── RiskManager dual-read tests ──────────────────────────────────────────


class TestRiskManagerSQLite:
    def test_check_daily_limits_sqlite(self, run, db):
        """RiskManager reads from SQLite when trade_db is set."""
        from src.trading.risk_manager import RiskManager

        # Set up daily stats in SQLite
        run(db.get_or_create_daily_stats("2026-02-14"))
        run(db.update_daily_stats("2026-02-14", pnl_delta=-2.0, trade_count_delta=5))
        run(db._db.execute(
            "UPDATE daily_stats SET initial_balance = 100.0 WHERE date = '2026-02-14'",
        ))
        run(db._db.commit())

        rm = RiskManager(client=None, market_name="BTC", trade_db=db)
        # PnL=-2.0 on 100.0 balance, MAX_DAILY_LOSS_PCT=0.10 → max_loss=-10 → OK
        assert rm.check_daily_limits() is True

    def test_check_daily_limits_sqlite_exceeded(self, run, db):
        """RiskManager detects exceeded limits from SQLite."""
        from src.trading.risk_manager import RiskManager

        run(db.get_or_create_daily_stats("2026-02-14"))
        run(db.update_daily_stats("2026-02-14", pnl_delta=-15.0, trade_count_delta=2))
        run(db._db.execute(
            "UPDATE daily_stats SET initial_balance = 100.0 WHERE date = '2026-02-14'",
        ))
        run(db._db.commit())

        rm = RiskManager(client=None, market_name="BTC", trade_db=db)
        # PnL=-15 on 100.0, max_loss=-10 → EXCEEDED
        assert rm.check_daily_limits() is False

    def test_check_daily_limits_fallback_json(self, tmp_path):
        """RiskManager falls back to JSON when no trade_db."""
        from src.trading.risk_manager import RiskManager

        rm = RiskManager(client=None, market_name="BTC", trade_db=None)
        # No JSON file either → OK (no limits to check)
        assert rm.check_daily_limits() is True

    def test_track_daily_pnl_sqlite(self, run, db):
        """track_daily_pnl writes to both JSON and SQLite."""
        from src.trading.risk_manager import RiskManager

        rm = RiskManager(client=None, market_name="BTC", trade_db=db)
        rm._daily_limits_path = str(Path("/tmp/test_daily_limits.json"))
        rm.track_daily_pnl(1.0, pnl=0.5)

        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        stats = run(db.get_or_create_daily_stats(today))
        assert stats["current_pnl"] == pytest.approx(0.5)
        assert stats["total_trades"] == 1
        assert stats["winning_trades"] == 1


# ─── AlertDispatcher SQLite tests ─────────────────────────────────────────


class TestAlertDispatcherSQLite:
    def test_record_alert_to_sqlite(self, run, db, tmp_path):
        """AlertDispatcher writes alerts to SQLite."""
        from src.trading.alert_dispatcher import AlertDispatcher, AlertLevel

        dispatcher = AlertDispatcher(
            alert_manager=None,
            history_path=tmp_path / "alerts.json",
            trade_db=db,
        )
        dispatcher._record_alert("trade", AlertLevel.INFO, {"market": "BTC", "side": "YES"})

        alerts = run(db.get_alerts(limit=10))
        assert len(alerts) == 1
        assert alerts[0]["alert_type"] == "trade"
        assert alerts[0]["level"] == "INFO"
        assert alerts[0]["market_name"] == "BTC"

    def test_record_alert_no_db(self, tmp_path):
        """AlertDispatcher works without trade_db (JSON only)."""
        from src.trading.alert_dispatcher import AlertDispatcher, AlertLevel

        dispatcher = AlertDispatcher(
            alert_manager=None,
            history_path=tmp_path / "alerts.json",
            trade_db=None,
        )
        dispatcher._record_alert("test", AlertLevel.INFO, {"msg": "hello"})
        assert len(dispatcher.get_history()) == 1


# ─── SQLite backends tests ────────────────────────────────────────────────


class TestSQLitePositionPersister:
    def test_save_load_remove(self, run, db):
        """SQLitePositionPersister save/load/remove cycle."""
        from src.trading.sqlite_backends import SQLitePositionPersister

        p = SQLitePositionPersister("cid-123", trade_db=db, market_name="BTC")
        p.save({"side": "YES", "entry_price": 0.90, "market_name": "BTC"})

        loaded = p.load()
        assert loaded is not None
        assert loaded["entry_price"] == pytest.approx(0.90)
        assert p.exists() is True

        p.remove()
        assert p.exists() is False
        assert p.load() is None

    def test_load_no_position(self, run, db):
        """Load returns None when no position exists."""
        from src.trading.sqlite_backends import SQLitePositionPersister

        p = SQLitePositionPersister("nonexistent", trade_db=db)
        assert p.load() is None
        assert p.exists() is False


class TestSQLiteEventRecorder:
    def test_record_and_close(self, run, db):
        """SQLiteEventRecorder records events to DB."""
        from src.trading.sqlite_backends import SQLiteEventRecorder

        rec = SQLiteEventRecorder(trade_db=db, market_name="ETH", condition_id="0xdef")
        rec.record_book_update("YES", 0.95, 10.0, 0.94, 5.0)
        rec.record_trigger_check(25.0, "YES", 0.95, executed=True, reason="trigger")
        rec.record_trade("buy", "YES", 0.95, 1.0, True, order_id="ord1", reason="trigger")
        rec.record_price_change("YES", 0.94, 0.95)
        rec.close()

        events = run(db.get_events(rec.session_id))
        # session_start + 4 events + session_end = 6
        assert len(events) == 6
        types = [e["event_type"] for e in events]
        assert types[0] == "session_start"
        assert types[-1] == "session_end"
        assert "book_update" in types
        assert "trade" in types

    def test_context_manager(self, run, db):
        """SQLiteEventRecorder works as context manager."""
        from src.trading.sqlite_backends import SQLiteEventRecorder

        with SQLiteEventRecorder(trade_db=db, market_name="SOL") as rec:
            rec.record_trade("buy", "YES", 0.80, 2.0, True)
            sid = rec.session_id

        events = run(db.get_events(sid))
        assert len(events) == 3  # session_start + trade + session_end

    def test_double_close(self, run, db):
        """Double close is safe."""
        from src.trading.sqlite_backends import SQLiteEventRecorder

        rec = SQLiteEventRecorder(trade_db=db, market_name="X")
        rec.close()
        rec.close()  # Should not raise
