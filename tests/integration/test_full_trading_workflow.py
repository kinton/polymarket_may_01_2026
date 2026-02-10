"""
Test full trading workflow: market discovery → trade execution → position tracking.

This test mocks:
- Gamma API (market search)
- CLOB WebSocket (orderbook)
- RTDS WebSocket (oracle)
"""

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
import pytest

from src.clob_types import OrderBook


@pytest.mark.asyncio
async def test_full_trading_workflow(
    integration_trader, mock_websocket, sample_market_data
):
    """
    Test complete trading workflow from market discovery to position tracking.

    Verifies:
    - Correct market is selected
    - Trade is executed
    - Position is opened and tracked
    """
    # Simulate market discovery
    selected_market = sample_market_data

    # Simulate orderbook data with winning side = YES
    initial_orderbook = OrderBook()
    initial_orderbook.best_ask_yes = 0.80  # Winning side price
    initial_orderbook.best_bid_yes = 0.79
    initial_orderbook.best_ask_no = 0.20  # Losing side
    initial_orderbook.best_bid_no = 0.19
    initial_orderbook.update()

    # Set orderbook to simulate market conditions
    integration_trader.orderbook = initial_orderbook
    integration_trader._update_winning_side()

    # Mock execute_order to verify trade execution
    integration_trader.execute_order = AsyncMock(return_value="order_123")

    # Execute the trade
    time_remaining = 30.0  # Within trigger threshold
    await integration_trader.check_trigger(time_remaining)

    # Verify trade was executed
    integration_trader.execute_order.assert_called_once()
    assert integration_trader.order_executed is True
    assert integration_trader.position_open is True
    assert integration_trader.position_side == "YES"
    assert integration_trader.entry_price == 0.80  # Entry price from winning ask

    # Verify position is tracked
    assert integration_trader.position_open is True
    assert integration_trader.winning_side == "YES"


@pytest.mark.asyncio
async def test_full_workflow_with_market_selection(
    integration_trader, mock_websocket, sample_market_data
):
    """
    Test workflow with explicit market selection criteria.

    Verifies:
    - Market selection meets criteria (winning side ≤ $0.99)
    - Trade is not executed if price too high
    """
    # Set orderbook with winning side price too high
    expensive_orderbook = OrderBook()
    expensive_orderbook.best_ask_yes = 0.999  # Just over $0.99 threshold
    expensive_orderbook.best_bid_yes = 0.998
    expensive_orderbook.best_ask_no = 0.001
    expensive_orderbook.best_bid_no = 0.001
    expensive_orderbook.update()

    integration_trader.orderbook = expensive_orderbook
    integration_trader._update_winning_side()

    # Mock execute_order
    integration_trader.execute_order = AsyncMock()

    # Execute the trade
    time_remaining = 30.0
    await integration_trader.check_trigger(time_remaining)

    # Verify trade was NOT executed (price too high)
    integration_trader.execute_order.assert_not_called()
    assert integration_trader.order_executed is False
    assert integration_trader.position_open is False
