"""Tests for TradeDatabase — SQLite storage module."""

from __future__ import annotations

import json
import os
import time

import pytest
import pytest_asyncio

from src.trading.trade_db import TradeDatabase


@pytest_asyncio.fixture
async def db(tmp_path):
    """Create a temporary TradeDatabase instance."""
    db_path = str(tmp_path / "test_trades.db")
    tdb = await TradeDatabase.initialize(db_path)
    yield tdb
    await tdb.close()


# ---------------------------------------------------------------------------
# Lifecycle & migrations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initialize_creates_db(tmp_path):
    path = str(tmp_path / "new.db")
    assert not os.path.exists(path)
    tdb = await TradeDatabase.initialize(path)
    assert os.path.exists(path)
    await tdb.close()


@pytest.mark.asyncio
async def test_migrate_idempotent(tmp_path):
    """Running migrate() twice should not fail."""
    path = str(tmp_path / "idem.db")
    tdb = await TradeDatabase.initialize(path)
    await tdb.migrate()  # second call
    await tdb.close()


@pytest.mark.asyncio
async def test_wal_mode(tmp_path):
    path = str(tmp_path / "wal.db")
    tdb = await TradeDatabase.initialize(path)
    async with tdb._db.execute("PRAGMA journal_mode") as cur:
        row = await cur.fetchone()
        assert row[0] == "wal"
    await tdb.close()


# ---------------------------------------------------------------------------
# Trades CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_and_get_trade(db: TradeDatabase):
    tid = await db.insert_trade(
        timestamp=time.time(),
        timestamp_iso="2026-02-14T15:00:00Z",
        market_name="BTC",
        condition_id="cond_123",
        action="buy",
        side="YES",
        price=0.95,
        amount=1.1,
        order_id="ord_1",
        status="filled",
        reason="trigger",
        dry_run=False,
    )
    assert tid is not None and tid > 0

    trades = await db.get_trades()
    assert len(trades) == 1
    assert trades[0]["market_name"] == "BTC"
    assert trades[0]["price"] == 0.95
    assert trades[0]["dry_run"] == 0


@pytest.mark.asyncio
async def test_get_trades_filter_market(db: TradeDatabase):
    for name in ("BTC", "ETH", "BTC"):
        await db.insert_trade(
            timestamp=time.time(), timestamp_iso="2026-02-14T15:00:00Z",
            market_name=name, condition_id="c1", action="buy",
            side="YES", price=0.9, amount=1.0,
        )
    btc = await db.get_trades(market="BTC")
    assert len(btc) == 2


@pytest.mark.asyncio
async def test_get_trades_filter_date(db: TradeDatabase):
    await db.insert_trade(
        timestamp=time.time(), timestamp_iso="2026-02-14T10:00:00Z",
        market_name="X", condition_id="c1", action="buy",
        side="YES", price=0.9, amount=1.0,
    )
    await db.insert_trade(
        timestamp=time.time(), timestamp_iso="2026-02-13T10:00:00Z",
        market_name="X", condition_id="c1", action="buy",
        side="YES", price=0.9, amount=1.0,
    )
    result = await db.get_trades(date="2026-02-14")
    assert len(result) == 1


@pytest.mark.asyncio
async def test_get_trades_limit(db: TradeDatabase):
    for i in range(10):
        await db.insert_trade(
            timestamp=time.time(), timestamp_iso="2026-02-14T15:00:00Z",
            market_name="X", condition_id="c1", action="buy",
            side="YES", price=0.9, amount=1.0,
        )
    result = await db.get_trades(limit=3)
    assert len(result) == 3


# ---------------------------------------------------------------------------
# Positions CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_and_load_position(db: TradeDatabase):
    state = {
        "market_name": "ETH",
        "side": "YES",
        "entry_price": 0.90,
        "trailing_stop_price": 0.85,
        "is_open": True,
        "opened_at": time.time(),
    }
    await db.save_position("cond_abc", state)
    loaded = await db.load_position("cond_abc")
    assert loaded is not None
    assert loaded["market_name"] == "ETH"
    assert loaded["entry_price"] == 0.90


@pytest.mark.asyncio
async def test_save_position_upsert(db: TradeDatabase):
    state = {"market_name": "X", "side": "YES", "entry_price": 0.5, "opened_at": time.time()}
    await db.save_position("c1", state)
    state["entry_price"] = 0.6
    await db.save_position("c1", state)
    loaded = await db.load_position("c1")
    assert loaded["entry_price"] == 0.6


@pytest.mark.asyncio
async def test_close_position(db: TradeDatabase):
    state = {"market_name": "X", "side": "YES", "entry_price": 0.5, "is_open": True, "opened_at": time.time()}
    await db.save_position("c1", state)
    await db.close_position("c1", "stop-loss")
    loaded = await db.load_position("c1")
    assert loaded["is_open"] == 0
    assert loaded["close_reason"] == "stop-loss"


@pytest.mark.asyncio
async def test_get_open_positions(db: TradeDatabase):
    t = time.time()
    await db.save_position("c1", {"market_name": "A", "side": "YES", "entry_price": 0.5, "is_open": True, "opened_at": t})
    await db.save_position("c2", {"market_name": "B", "side": "NO", "entry_price": 0.6, "is_open": True, "opened_at": t})
    await db.close_position("c1", "take-profit")
    open_pos = await db.get_open_positions()
    assert len(open_pos) == 1
    assert open_pos[0]["condition_id"] == "c2"


@pytest.mark.asyncio
async def test_load_nonexistent_position(db: TradeDatabase):
    assert await db.load_position("nonexistent") is None


# ---------------------------------------------------------------------------
# Orderbook snapshots (buffered)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_buffer_and_flush_orderbook(db: TradeDatabase):
    for i in range(5):
        await db.buffer_orderbook_snapshot(
            timestamp=time.time(), condition_id="c1",
            best_ask_yes=0.95, best_bid_yes=0.94,
        )
    assert len(db._ob_buffer) == 5
    await db.flush_orderbook_buffer()
    assert len(db._ob_buffer) == 0

    async with db._db.execute("SELECT COUNT(*) FROM order_book_snapshots") as cur:
        row = await cur.fetchone()
        assert row[0] == 5


@pytest.mark.asyncio
async def test_buffer_auto_flush(db: TradeDatabase):
    db._ob_buffer_limit = 3
    for i in range(3):
        await db.buffer_orderbook_snapshot(
            timestamp=time.time(), condition_id="c1",
        )
    # Should have auto-flushed at 3
    assert len(db._ob_buffer) == 0
    async with db._db.execute("SELECT COUNT(*) FROM order_book_snapshots") as cur:
        assert (await cur.fetchone())[0] == 3


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_and_get_alerts(db: TradeDatabase):
    aid = await db.insert_alert(
        timestamp=time.time(), alert_type="trade", level="INFO",
        market_name="BTC", details_json=json.dumps({"msg": "bought"}),
    )
    assert aid > 0
    alerts = await db.get_alerts()
    assert len(alerts) == 1
    assert alerts[0]["alert_type"] == "trade"


@pytest.mark.asyncio
async def test_get_alerts_filter_type(db: TradeDatabase):
    t = time.time()
    await db.insert_alert(timestamp=t, alert_type="trade", level="INFO")
    await db.insert_alert(timestamp=t, alert_type="stop_loss", level="WARNING")
    result = await db.get_alerts(alert_type="stop_loss")
    assert len(result) == 1


@pytest.mark.asyncio
async def test_get_alerts_filter_since(db: TradeDatabase):
    await db.insert_alert(timestamp=1000, alert_type="trade", level="INFO")
    await db.insert_alert(timestamp=2000, alert_type="trade", level="INFO")
    result = await db.get_alerts(since=1500)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# Daily stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_create_daily_stats(db: TradeDatabase):
    stats = await db.get_or_create_daily_stats("2026-02-14")
    assert stats["date"] == "2026-02-14"
    assert stats["current_pnl"] == 0
    assert stats["total_trades"] == 0


@pytest.mark.asyncio
async def test_update_daily_stats(db: TradeDatabase):
    await db.update_daily_stats("2026-02-14", pnl_delta=5.0, trade_count_delta=2, winning_delta=1, volume_delta=10.0)
    await db.update_daily_stats("2026-02-14", pnl_delta=-1.0, trade_count_delta=1, losing_delta=1, volume_delta=5.0)
    stats = await db.get_or_create_daily_stats("2026-02-14")
    assert stats["current_pnl"] == 4.0
    assert stats["total_trades"] == 3
    assert stats["winning_trades"] == 1
    assert stats["losing_trades"] == 1
    assert stats["total_volume"] == 15.0


# ---------------------------------------------------------------------------
# Events (replay)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_and_get_events(db: TradeDatabase):
    eid = await db.insert_event(
        session_id="sess_1", timestamp=time.time(),
        event_type="trade", condition_id="c1", market_name="BTC",
        data_json=json.dumps({"action": "buy"}),
    )
    assert eid > 0
    events = await db.get_events("sess_1")
    assert len(events) == 1
    assert events[0]["event_type"] == "trade"


@pytest.mark.asyncio
async def test_list_sessions(db: TradeDatabase):
    t = time.time()
    await db.insert_event(session_id="s1", timestamp=t, event_type="start", data_json="{}")
    await db.insert_event(session_id="s1", timestamp=t + 10, event_type="end", data_json="{}")
    await db.insert_event(session_id="s2", timestamp=t + 20, event_type="start", data_json="{}")
    sessions = await db.list_sessions()
    assert len(sessions) == 2
    assert sessions[0]["session_id"] == "s2"  # most recent first
    assert sessions[1]["event_count"] == 2


@pytest.mark.asyncio
async def test_get_events_empty_session(db: TradeDatabase):
    assert await db.get_events("nonexistent") == []


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_old_snapshots(db: TradeDatabase):
    old_ts = time.time() - 10 * 86400  # 10 days ago
    new_ts = time.time()
    await db.buffer_orderbook_snapshot(timestamp=old_ts, condition_id="c1")
    await db.buffer_orderbook_snapshot(timestamp=new_ts, condition_id="c1")
    await db.flush_orderbook_buffer()
    deleted = await db.cleanup_old_snapshots(days=7)
    assert deleted == 1
    async with db._db.execute("SELECT COUNT(*) FROM order_book_snapshots") as cur:
        assert (await cur.fetchone())[0] == 1
