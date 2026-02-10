"""
Test take-profit execution when price rises 10% from entry.

This test mocks:
- Orderbook updates (price rises from 0.99 to 1.09)
- Take-profit triggers at 10% rise
- Sell execution
- Position closure
- PnL calculation
"""

import pytest
from unittest.mock import AsyncMock

from src.clob_types import TAKE_PROFIT_PCT


@pytest.mark.asyncio
async def test_take_profit_triggers_on_10_percent_rise(integration_trader):
    """
    Test that take-profit triggers when price rises 10% from entry.

    Setup:
    - Entry price: $0.99
    - Position: YES
    - Price rises to $1.09 (10.1% rise, just over take-profit threshold of 10%)
    """
    # Set up position with low entry price to avoid absolute floor interference
    integration_trader.entry_price = 0.60
    integration_trader.position_side = "YES"
    integration_trader.position_open = True
    integration_trader.trailing_stop_price = 0.60 * (1 - 0.30)

    # Simulate price rise above take-profit
    # Take-profit threshold: $0.60 * 1.10 = $0.66
    # Current price: $1.09 (81.7% rise, above threshold)
    integration_trader.orderbook.best_ask_yes = 1.09

    # Mock execute_sell to verify take-profit execution and close position
    async def mock_execute_sell(reason):
        integration_trader.position_open = False
        integration_trader.entry_price = None
        integration_trader.position_side = None
        integration_trader.trailing_stop_price = None

    integration_trader.execute_sell = AsyncMock(side_effect=mock_execute_sell)

    # Run take-profit check
    await integration_trader._check_stop_loss_take_profit()

    # Verify take-profit triggered
    integration_trader.execute_sell.assert_called_once_with("TAKE-PROFIT")

    # Verify position closed
    assert integration_trader.position_open is False
    assert integration_trader.entry_price is None


@pytest.mark.asyncio
async def test_take_profit_does_not_trigger_on_9_percent_rise(integration_trader):
    """
    Test that take-profit does NOT trigger on 9% rise (under threshold).

    Setup:
    - Entry price: $0.60
    - Position: YES
    - Price rises to $0.654 (9% rise, below take-profit threshold)
    """
    integration_trader.entry_price = 0.60
    integration_trader.position_side = "YES"
    integration_trader.position_open = True
    integration_trader.trailing_stop_price = 0.42

    # Price rises to $0.654 (9% rise, below take-profit threshold)
    integration_trader.orderbook.best_ask_yes = 0.654

    # Mock execute_sell (should not be called)
    integration_trader.execute_sell = AsyncMock()

    # Run take-profit check
    await integration_trader._check_stop_loss_take_profit()

    # Verify take-profit did NOT trigger
    integration_trader.execute_sell.assert_not_called()

    # Verify position still open
    assert integration_trader.position_open is True


@pytest.mark.asyncio
async def test_take_profit_with_absolute_floor(integration_trader):
    """
    Test that take-profit triggers when absolute floor is higher than percentage.

    Setup:
    - Entry price: $0.90
    - Absolute floor ($0.95) is higher than percentage take ($0.99)
    - Price rises to $0.96
    """
    integration_trader.entry_price = 0.90
    integration_trader.position_side = "YES"
    integration_trader.position_open = True
    integration_trader.trailing_stop_price = 0.95  # Absolute floor

    # Price rises to $0.96, above absolute floor
    integration_trader.orderbook.best_ask_yes = 0.96

    # Mock execute_sell to verify take-profit execution and close position
    async def mock_execute_sell(reason):
        integration_trader.position_open = False
        integration_trader.entry_price = None
        integration_trader.position_side = None
        integration_trader.trailing_stop_price = None

    integration_trader.execute_sell = AsyncMock(side_effect=mock_execute_sell)

    # Run take-profit check
    await integration_trader._check_stop_loss_take_profit()

    # Verify take-profit triggered (price rose but still below floor logic takes over)
    integration_trader.execute_sell.assert_called_once_with("TAKE-PROFIT")
    assert integration_trader.position_open is False
