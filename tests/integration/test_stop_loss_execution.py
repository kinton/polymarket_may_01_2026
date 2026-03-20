"""
Test stop-loss execution when price drops 30% from entry.

This test mocks:
- Orderbook updates (price drops from 0.99 to 0.69)
- Stop-loss triggers at 30% drop
- Sell execution
- Position closure
- PnL calculation
"""

import pytest
from unittest.mock import AsyncMock

from src.clob_types import (
    STOP_LOSS_PCT,
)


@pytest.mark.asyncio
async def test_stop_loss_triggers_on_30_percent_drop(integration_trader):
    """
    Test that stop-loss triggers when price drops 50% from entry.

    Setup:
    - Entry price: $0.99
    - Position: YES
    - Price drops to $0.49 (51% drop, below stop-loss threshold of 50%)
    """
    # Set up position
    integration_trader.entry_price = 0.99
    integration_trader.position_side = "YES"
    integration_trader.position_open = True
    integration_trader.trailing_stop_price = 0.99 * (1 - STOP_LOSS_PCT)  # $0.495

    # Simulate price drop below stop-loss
    # Stop-loss threshold: $0.99 * 0.50 = $0.495
    # Current price: $0.49 (51% drop)
    integration_trader.orderbook.best_ask_yes = 0.49

    # Mock order_execution.execute_sell to verify stop-loss execution and close position
    async def mock_execute_sell(reason, current_price):
        integration_trader.position_open = False
        integration_trader.entry_price = None
        integration_trader.position_side = None
        integration_trader.trailing_stop_price = None

    integration_trader.order_execution.execute_sell = AsyncMock(side_effect=mock_execute_sell)

    # Run stop-loss check
    await integration_trader._check_stop_loss_take_profit()

    # Verify stop-loss triggered
    integration_trader.order_execution.execute_sell.assert_called_once_with("STOP-LOSS", 0.49)

    # Verify position closed
    assert integration_trader.position_open is False
    assert integration_trader.entry_price is None


@pytest.mark.asyncio
async def test_stop_loss_does_not_trigger_on_29_percent_drop(integration_trader):
    """
    Test that stop-loss does NOT trigger on 29% drop (under threshold).

    Setup:
    - Entry price: $0.99
    - Position: YES
    - Price drops to $0.70 (29% drop, above stop-loss threshold)
    """
    integration_trader.entry_price = 0.99
    integration_trader.position_side = "YES"
    integration_trader.position_open = True
    integration_trader.trailing_stop_price = 0.693

    # Price drops to $0.70 (29% drop, above stop-loss)
    integration_trader.orderbook.best_ask_yes = 0.70

    # Mock order_execution.execute_sell (should not be called)
    integration_trader.order_execution.execute_sell = AsyncMock()

    # Run stop-loss check
    await integration_trader._check_stop_loss_take_profit()

    # Verify stop-loss did NOT trigger
    integration_trader.order_execution.execute_sell.assert_not_called()

    # Verify position still open
    assert integration_trader.position_open is True


@pytest.mark.asyncio
async def test_stop_loss_uses_percentage_only(integration_trader):
    """
    Test that stop-loss uses percentage only (no absolute floor).

    Setup:
    - Entry price: $0.30 (NO token, typical entry)
    - -50% stop = $0.15
    - Price drops to $0.14
    """
    integration_trader.entry_price = 0.30
    integration_trader.position_side = "NO"
    integration_trader.position_open = True
    integration_trader.trailing_stop_price = 0.30 * (1 - STOP_LOSS_PCT)  # $0.15

    # Price drops below -50% stop
    integration_trader.orderbook.best_ask_no = 0.14

    # Mock order_execution.execute_sell to verify stop-loss execution and close position
    async def mock_execute_sell(reason, current_price):
        integration_trader.position_open = False
        integration_trader.entry_price = None
        integration_trader.position_side = None
        integration_trader.trailing_stop_price = None

    integration_trader.order_execution.execute_sell = AsyncMock(side_effect=mock_execute_sell)

    # Run stop-loss check
    await integration_trader._check_stop_loss_take_profit()

    # Verify stop-loss triggered
    integration_trader.order_execution.execute_sell.assert_called_once_with("STOP-LOSS", 0.14)
    assert integration_trader.position_open is False
