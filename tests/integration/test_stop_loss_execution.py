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

from src.clob_types import (
    STOP_LOSS_ABSOLUTE,
    STOP_LOSS_PCT,
)


@pytest.mark.asyncio
async def test_stop_loss_triggers_on_30_percent_drop(integration_trader):
    """
    Test that stop-loss triggers when price drops 30% from entry.

    Setup:
    - Entry price: $0.99
    - Position: YES
    - Price drops to $0.69 (31% drop, below stop-loss threshold of 30%)
    """
    # Set up position
    integration_trader.entry_price = 0.99
    integration_trader.position_side = "YES"
    integration_trader.position_open = True
    integration_trader.trailing_stop_price = 0.99 * (1 - STOP_LOSS_PCT)  # $0.693

    # Simulate price drop below stop-loss
    # Stop-loss threshold: $0.99 * 0.70 = $0.693
    # Current price: $0.69 (31% drop)
    integration_trader.orderbook.best_ask_yes = 0.69

    # Mock execute_sell to verify stop-loss execution
    integration_trader.execute_sell = AsyncMock()

    # Run stop-loss check
    await integration_trader._check_stop_loss_take_profit()

    # Verify stop-loss triggered
    integration_trader.execute_sell.assert_called_once_with("STOP-LOSS")

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

    integration_trader.execute_sell = AsyncMock()

    # Run stop-loss check
    await integration_trader._check_stop_loss_take_profit()

    # Verify stop-loss did NOT trigger
    integration_trader.execute_sell.assert_not_called()

    # Verify position still open
    assert integration_trader.position_open is True


@pytest.mark.asyncio
async def test_stop_loss_uses_absolute_floor(integration_trader):
    """
    Test that stop-loss uses absolute floor when it's higher than percentage.

    Setup:
    - Entry price: $0.96
    - Absolute floor ($0.95) is higher than percentage stop ($0.672)
    - Price drops to $0.94
    """
    integration_trader.entry_price = 0.96
    integration_trader.position_side = "YES"
    integration_trader.position_open = True
    integration_trader.trailing_stop_price = STOP_LOSS_ABSOLUTE  # $0.95

    # Price drops to $0.94, below absolute floor of $0.95
    integration_trader.orderbook.best_ask_yes = 0.94

    integration_trader.execute_sell = AsyncMock()

    # Run stop-loss check
    await integration_trader._check_stop_loss_take_profit()

    # Verify stop-loss triggered due to absolute floor
    integration_trader.execute_sell.assert_called_once_with("STOP-LOSS")
    assert integration_trader.position_open is False
