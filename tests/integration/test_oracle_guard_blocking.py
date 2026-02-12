"""
Test Oracle Guard blocking when oracle volatility exceeds 0.2%.

This test mocks:
- Oracle data with volatility spike (>0.2%)
- Oracle guard blocking
- Alert sending
- Trade not executed
"""

import pytest
from dataclasses import replace
from unittest.mock import AsyncMock

from src.clob_types import OrderBook
from src.oracle_tracker import OracleTracker


@pytest.mark.asyncio
async def test_oracle_guard_blocking_volatility_spike(integration_trader):
    """
    Test that Oracle Guard blocks trades when volatility exceeds 0.2%.

    Setup:
    - Oracle volatility > 0.002 (0.2%)
    - Guard enabled
    - Expected: trade blocked, alert sent
    """
    integration_trader.oracle_guard.guard_enabled = True
    integration_trader.oracle_guard.enabled = True

    # Create oracle tracker with high volatility
    oracle_tracker = OracleTracker(window_seconds=60.0)

    # Add price points with high volatility
    # Vol > 0.2% means std dev of % returns > 0.002
    from datetime import datetime, timezone

    now = int(datetime.now(timezone.utc).timestamp() * 1000)

    # Add points with high variance to create volatility spike
    for i, price_pct in enumerate([0.00, 0.30, -0.25, 0.55, 0.80]):
        oracle_tracker.update(ts_ms=now - (5 - i) * 1000, price=100.0 + price_pct)
        oracle_tracker.update(ts_ms=now - (4 - i) * 1000, price=100.0 + price_pct)

    # Create snapshot with high volatility
    snapshot = oracle_tracker.update(ts_ms=now, price=100.5)
    # Use dataclasses.replace to modify frozen dataclass
    snapshot = replace(snapshot, n_points=6, vol_pct=0.003)  # 0.3% > 0.2% threshold

    # Set oracle snapshot on trader
    integration_trader.oracle_guard.tracker = oracle_tracker
    integration_trader.oracle_guard.snapshot = snapshot
    integration_trader.oracle_guard.last_update_ts = now / 1000.0

    # Set up orderbook with winning side
    integration_trader.orderbook = OrderBook()
    integration_trader.orderbook.best_ask_yes = 0.80
    integration_trader.orderbook.best_bid_yes = 0.79
    integration_trader.orderbook.best_ask_no = 0.20
    integration_trader.orderbook.best_bid_no = 0.19
    integration_trader.orderbook.update()
    integration_trader._update_winning_side()

    # Mock alert dispatcher to verify alert is sent
    integration_trader.alert_dispatcher.is_enabled = lambda: True
    integration_trader.alert_dispatcher.send_oracle_guard_block = AsyncMock()

    # Mock order_execution to verify no trade is executed
    integration_trader.order_execution.execute_order_for = AsyncMock(wraps=integration_trader.order_execution.execute_order_for)

    # Run check_trigger with time within window
    time_remaining = 30.0
    await integration_trader.check_trigger(time_remaining)

    # Verify trade was blocked (oracle_guard should block)
    integration_trader.order_execution.execute_order_for.assert_not_called()
    assert integration_trader.order_executed is False
    assert integration_trader.position_open is False

    # Verify oracle guard block was logged
    # Guard should increment block count
    assert integration_trader.oracle_guard.block_count > 0

    # Verify alert was sent
    integration_trader.alert_dispatcher.send_oracle_guard_block.assert_called_once()


@pytest.mark.asyncio
async def test_oracle_guard_allows_trade_when_volatility_low(integration_trader):
    """
    Test that Oracle Guard allows trades when volatility is below threshold.

    Setup:
    - Oracle volatility = 0.001 (0.1% < 0.2% threshold)
    - Guard enabled
    - Expected: trade executes, no block
    """
    integration_trader.oracle_guard.guard_enabled = True
    integration_trader.oracle_guard.enabled = True

    # Create oracle tracker with low volatility
    oracle_tracker = OracleTracker(window_seconds=60.0)

    # Add stable price points with low variance
    from datetime import datetime, timezone

    now = int(datetime.now(timezone.utc).timestamp() * 1000)

    for i, price in enumerate([100.0, 100.1, 100.05, 99.95]):
        oracle_tracker.update(ts_ms=now - (3 - i) * 1000, price=price)

    # Create snapshot with low volatility
    snapshot = oracle_tracker.update(ts_ms=now, price=100.0)
    # Use dataclasses.replace to modify frozen dataclass
    snapshot = replace(snapshot, n_points=4, vol_pct=0.001, zscore=1.5)  # 0.1% < 0.2% threshold, zscore high enough

    integration_trader.oracle_guard.tracker = oracle_tracker
    integration_trader.oracle_guard.snapshot = snapshot
    integration_trader.oracle_guard.last_update_ts = now / 1000.0

    # Mock alert dispatcher
    integration_trader.alert_dispatcher.is_enabled = lambda: True
    integration_trader.alert_dispatcher.send_oracle_guard_block = AsyncMock()

    # Mock order_execution to verify trade executes
    # Use side_effect to ensure mark_executed() is called
    async def mock_execute_order_for(side, winning_ask):
        # Mark as executed before calling real method
        integration_trader.order_execution.mark_executed()
        return True

    integration_trader.order_execution.execute_order_for = AsyncMock(side_effect=mock_execute_order_for)

    # Set up orderbook for trade to execute
    integration_trader.orderbook = OrderBook()
    integration_trader.orderbook.best_ask_yes = 0.75
    integration_trader.orderbook.best_bid_yes = 0.74
    integration_trader.orderbook.best_ask_no = 0.25
    integration_trader.orderbook.best_bid_no = 0.24
    integration_trader.orderbook.update()
    integration_trader._update_winning_side()

    # Run check_trigger
    time_remaining = 30.0
    await integration_trader.check_trigger(time_remaining)

    # Verify trade was allowed (oracle_guard should NOT block)
    integration_trader.order_execution.execute_order_for.assert_called_once()
    assert integration_trader.order_executed is True

    # Verify alert was NOT sent for blocking
    integration_trader.alert_dispatcher.send_oracle_guard_block.assert_not_called()


@pytest.mark.asyncio
async def test_oracle_guard_zscore_blocking(integration_trader):
    """
    Test that Oracle Guard blocks trades when Z-score is below 0.75.

    Setup:
    - Oracle Z-score = 0.5 (< 0.75 threshold)
    - Guard enabled
    - Expected: trade blocked
    """
    integration_trader.oracle_guard.guard_enabled = True
    integration_trader.oracle_guard.enabled = True

    # Create oracle tracker with low Z-score
    oracle_tracker = OracleTracker(window_seconds=60.0)

    # Add price points to create stable volatility
    from datetime import datetime, timezone

    now = int(datetime.now(timezone.utc).timestamp() * 1000)

    # Create stable trend (all prices around 100)
    for i in range(6):
        oracle_tracker.update(ts_ms=now - (5 - i) * 1000, price=100.0 + (i % 3) * 0.01)

    # Create snapshot with low Z-score
    snapshot = oracle_tracker.update(ts_ms=now, price=100.2)
    # Small delta means low Z-score relative to volatility
    # Use dataclasses.replace to modify frozen dataclass
    snapshot = replace(snapshot, zscore=0.5)  # Below 0.75 threshold

    integration_trader.oracle_guard.tracker = oracle_tracker
    integration_trader.oracle_guard.snapshot = snapshot
    integration_trader.oracle_guard.last_update_ts = now / 1000.0

    # Set up orderbook with winning side
    integration_trader.orderbook = OrderBook()
    integration_trader.orderbook.best_ask_yes = 0.80
    integration_trader.orderbook.best_bid_yes = 0.79
    integration_trader.orderbook.best_ask_no = 0.20
    integration_trader.orderbook.best_bid_no = 0.19
    integration_trader.orderbook.update()
    integration_trader._update_winning_side()

    # Mock alert dispatcher
    integration_trader.alert_dispatcher.is_enabled = lambda: True
    integration_trader.alert_dispatcher.send_oracle_guard_block = AsyncMock()

    # Mock order_execution to verify no trade is executed
    integration_trader.order_execution.execute_order_for = AsyncMock(wraps=integration_trader.order_execution.execute_order_for)

    # Run check_trigger
    time_remaining = 30.0
    await integration_trader.check_trigger(time_remaining)

    # Verify trade was blocked (zscore too low)
    integration_trader.order_execution.execute_order_for.assert_not_called()

    # Verify alert was sent
    integration_trader.alert_dispatcher.send_oracle_guard_block.assert_called_once()
