"""Tests for the health watchdog and get_last_trade_timestamp."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from src.alerts import AlertManager
from src.trading.trade_db import TradeDatabase
from src.watchdog import watchdog_loop


@pytest_asyncio.fixture
async def db(tmp_path):
    """Create a temporary TradeDatabase instance."""
    db_path = str(tmp_path / "watchdog_test.db")
    tdb = await TradeDatabase.initialize(db_path)
    yield tdb
    await tdb.close()


# ---------------------------------------------------------------------------
# get_last_trade_timestamp
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_last_trade_timestamp_no_trades(db: TradeDatabase):
    """Returns None when no trades exist."""
    result = await db.get_last_trade_timestamp()
    assert result is None


@pytest.mark.asyncio
async def test_get_last_trade_timestamp_returns_latest(db: TradeDatabase):
    """Returns the most recent trade timestamp."""
    ts_old = time.time() - 3600
    ts_new = time.time()
    for ts in (ts_old, ts_new):
        await db.insert_trade(
            timestamp=ts,
            timestamp_iso="2026-03-15T10:00:00Z",
            market_name="BTC",
            condition_id="c1",
            action="buy",
            side="YES",
            price=0.50,
            amount=1.0,
        )
    result = await db.get_last_trade_timestamp()
    assert result is not None
    assert abs(result - ts_new) < 0.01


# ---------------------------------------------------------------------------
# watchdog_loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchdog_fires_alert_when_stale(db: TradeDatabase):
    """Watchdog fires an alert when no trades exist past threshold."""
    alert_mgr = AlertManager()  # no channels, but we mock broadcast
    alert_mgr.broadcast_alert = AsyncMock()

    # Insert an old trade (5 hours ago)
    old_ts = time.time() - 5 * 3600
    await db.insert_trade(
        timestamp=old_ts,
        timestamp_iso="2026-03-15T05:00:00Z",
        market_name="BTC",
        condition_id="c1",
        action="buy",
        side="YES",
        price=0.50,
        amount=1.0,
    )

    # Patch sleep to run only one iteration
    call_count = 0

    async def _fake_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise asyncio.CancelledError()

    with patch("src.watchdog.asyncio.sleep", side_effect=_fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await watchdog_loop(
                db,
                alert_mgr,
                threshold_hours=4.0,
                context={"strategy": "convergence", "version": "v1", "mode": "live"},
            )

    alert_mgr.broadcast_alert.assert_called_once()
    msg = alert_mgr.broadcast_alert.call_args[0][0]
    assert "WATCHDOG" in msg
    assert "No trades in" in msg
    assert "threshold: 4h" in msg


@pytest.mark.asyncio
async def test_watchdog_no_alert_when_recent_trade(db: TradeDatabase):
    """Watchdog does NOT fire when last trade is within threshold."""
    alert_mgr = AlertManager()
    alert_mgr.broadcast_alert = AsyncMock()

    # Insert a recent trade (1 hour ago)
    recent_ts = time.time() - 1 * 3600
    await db.insert_trade(
        timestamp=recent_ts,
        timestamp_iso="2026-03-15T09:00:00Z",
        market_name="BTC",
        condition_id="c1",
        action="buy",
        side="YES",
        price=0.50,
        amount=1.0,
    )

    call_count = 0

    async def _fake_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise asyncio.CancelledError()

    with patch("src.watchdog.asyncio.sleep", side_effect=_fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await watchdog_loop(
                db,
                alert_mgr,
                threshold_hours=4.0,
            )

    alert_mgr.broadcast_alert.assert_not_called()


@pytest.mark.asyncio
async def test_watchdog_suppresses_repeated_alerts(db: TradeDatabase):
    """Watchdog fires only once per staleness event."""
    alert_mgr = AlertManager()
    alert_mgr.broadcast_alert = AsyncMock()

    old_ts = time.time() - 5 * 3600
    await db.insert_trade(
        timestamp=old_ts,
        timestamp_iso="2026-03-15T05:00:00Z",
        market_name="BTC",
        condition_id="c1",
        action="buy",
        side="YES",
        price=0.50,
        amount=1.0,
    )

    call_count = 0

    async def _fake_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            raise asyncio.CancelledError()

    with patch("src.watchdog.asyncio.sleep", side_effect=_fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await watchdog_loop(db, alert_mgr, threshold_hours=4.0)

    # Should fire exactly once despite 3 iterations
    assert alert_mgr.broadcast_alert.call_count == 1


@pytest.mark.asyncio
async def test_watchdog_rearms_after_new_trade(db: TradeDatabase):
    """Watchdog re-arms after a new trade and can fire again."""
    alert_mgr = AlertManager()
    alert_mgr.broadcast_alert = AsyncMock()

    old_ts = time.time() - 5 * 3600
    await db.insert_trade(
        timestamp=old_ts,
        timestamp_iso="2026-03-15T05:00:00Z",
        market_name="BTC",
        condition_id="c1",
        action="buy",
        side="YES",
        price=0.50,
        amount=1.0,
    )

    call_count = 0

    async def _fake_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Simulate a new trade arriving (still old enough to re-trigger)
            await db.insert_trade(
                timestamp=time.time() - 5 * 3600 + 1,  # slightly newer but still stale
                timestamp_iso="2026-03-15T05:00:01Z",
                market_name="ETH",
                condition_id="c2",
                action="buy",
                side="YES",
                price=0.50,
                amount=1.0,
            )
        if call_count >= 3:
            raise asyncio.CancelledError()

    with patch("src.watchdog.asyncio.sleep", side_effect=_fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await watchdog_loop(db, alert_mgr, threshold_hours=4.0)

    # Should fire twice: once for first staleness, re-arm on new trade, fire again
    assert alert_mgr.broadcast_alert.call_count == 2


@pytest.mark.asyncio
async def test_watchdog_no_trades_uses_startup_baseline(db: TradeDatabase):
    """When no trades exist, watchdog uses startup time as baseline."""
    alert_mgr = AlertManager()
    alert_mgr.broadcast_alert = AsyncMock()

    call_count = 0

    async def _fake_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise asyncio.CancelledError()

    with patch("src.watchdog.asyncio.sleep", side_effect=_fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await watchdog_loop(db, alert_mgr, threshold_hours=4.0)

    # Bot just started, no trades, 0 hours elapsed — should NOT alert
    alert_mgr.broadcast_alert.assert_not_called()
