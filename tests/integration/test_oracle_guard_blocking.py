"""
Test Oracle Guard blocking when oracle volatility exceeds 0.2%.

This test mocks:
- Oracle data with volatility spike (>0.2%)
- Oracle guard blocking
- Alert sending
- Trade not executed
"""

import pytest

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
    integration_trader.oracle_guard_enabled = True
    integration_trader.oracle_enabled = True

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
    snapshot.n_points = 6
    snapshot.vol_pct = 0.003  # 0.3% > 0.2% threshold

    # Set oracle snapshot on trader
    integration_trader.oracle_tracker = oracle_tracker
    integration_trader.oracle_snapshot = snapshot
    integration_trader.last_oracle_update_ts = now / 1000.0

    # Mock alert manager to verify alert is sent
    integration_trader.alert_manager = MagicMock()

    # Mock execute_sell to verify no trade is executed
    integration_trader.execute_sell = AsyncMock()

    # Run check_trigger with time within window
    time_remaining = 30.0
    await integration_trader.check_trigger(time_remaining)

    # Verify trade was blocked (oracle_guard should block)
    integration_trader.execute_sell.assert_not_called()
    assert integration_trader.order_executed is False
    assert integration_trader.position_open is False

    # Verify oracle guard block was logged
    # Guard should increment block count
    assert integration_trader._oracle_guard_block_count > 0

    # Verify alert was sent
    if integration_trader.alert_manager:
        integration_trader.alert_manager.send_oracle_guard_block.assert_called_once()


@pytest.mark.asyncio
async def test_oracle_guard_allows_trade_when_volatility_low(integration_trader):
    """
    Test that Oracle Guard allows trades when volatility is below threshold.

    Setup:
    - Oracle volatility = 0.001 (0.1% < 0.2% threshold)
    - Guard enabled
    - Expected: trade executes, no block
    """
    integration_trader.oracle_guard_enabled = True
    integration_trader.oracle_enabled = True

    # Create oracle tracker with low volatility
    oracle_tracker = OracleTracker(window_seconds=60.0)

    # Add stable price points with low variance
    from datetime import datetime, timezone

    now = int(datetime.now(timezone.utc).timestamp() * 1000)

    for i, price in enumerate([100.0, 100.1, 100.05, 99.95]):
        oracle_tracker.update(ts_ms=now - (3 - i) * 1000, price=price)

    # Create snapshot with low volatility
    snapshot = oracle_tracker.update(ts_ms=now, price=100.0)
    snapshot.n_points = 4
    snapshot.vol_pct = 0.001  # 0.1% < 0.2% threshold

    integration_trader.oracle_tracker = oracle_tracker
    integration_trader.oracle_snapshot = snapshot
    integration_trader.last_oracle_update_ts = now / 1000.0

    # Mock alert manager
    integration_trader.alert_manager = MagicMock()

    # Mock execute_sell to verify trade executes
    integration_trader.execute_sell = AsyncMock(return_value="order_123")

    # Set up position for trade to execute
    integration_trader.orderbook = OrderBook()
    integration_trader.orderbook.best_ask_yes = 0.75
    integration_trader.orderbook.best_bid_yes = 0.74
    integration_trader._update_winning_side()

    # Run check_trigger
    time_remaining = 30.0
    await integration_trader.check_trigger(time_remaining)

    # Verify trade was allowed (oracle_guard should NOT block)
    integration_trader.execute_sell.assert_called_once()
    assert integration_trader.order_executed is True

    # Verify alert was NOT sent for blocking
    if integration_trader.alert_manager:
        integration_trader.alert_manager.send_oracle_guard_block.assert_not_called()


@pytest.mark.asyncio
async def test_oracle_guard_zscore_blocking(integration_trader):
    """
    Test that Oracle Guard blocks trades when Z-score is below 0.75.

    Setup:
    - Oracle Z-score = 0.5 (< 0.75 threshold)
    - Guard enabled
    - Expected: trade blocked
    """
    integration_trader.oracle_guard_enabled = True
    integration_trader.oracle_enabled = True

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
    snapshot.zscore = 0.5  # Below 0.75 threshold

    integration_trader.oracle_tracker = oracle_tracker
    integration_trader.oracle_snapshot = snapshot
    integration_trader.last_oracle_update_ts = now / 1000.0

    # Mock alert manager
    integration_trader.alert_manager = MagicMock()

    # Mock execute_sell
    integration_trader.execute_sell = AsyncMock()

    # Run check_trigger
    time_remaining = 30.0
    await integration_trader.check_trigger(time_remaining)

    # Verify trade was blocked (zscore too low)
    integration_trader.execute_sell.assert_not_called()

    # Verify alert was sent
    if integration_trader.alert_manager:
        integration_trader.alert_manager.send_oracle_guard_block.assert_called_once()
