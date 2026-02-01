"""
Unit tests for balance checking logic in hft_trader.py

Tests verify that the trader correctly checks USDC balance and allowance
before attempting to execute orders, preventing failed trades due to
insufficient funds.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.hft_trader import LastSecondTrader


@pytest.fixture
def mock_trader():
    """Create a trader instance with mocked dependencies for testing."""
    with patch("src.hft_trader.load_dotenv"), patch("src.hft_trader.ClobClient"):
        # Create trader with known parameters
        end_time = datetime.now(timezone.utc).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        trader = LastSecondTrader(
            condition_id="test_condition_123",
            token_id_yes="token_yes_456",
            token_id_no="token_no_789",
            end_time=end_time,
            trade_size=10.0,
            dry_run=False,  # Need to test with client
            title="Bitcoin Test Market",
        )

        # Mock the CLOB client
        trader.client = MagicMock()
        trader.market_name = "BTC"
        trader.logger = None  # Disable file logging in tests

        return trader


@pytest.mark.asyncio
async def test_balance_check_sufficient_funds(mock_trader):
    """Test that balance check passes when both balance and allowance are sufficient."""
    # Mock API response with sufficient funds
    mock_trader.client.get_balance_allowance = MagicMock(
        return_value={"balance": 100.0, "allowance": 100.0}
    )

    result = await mock_trader._check_balance()

    assert result is True
    mock_trader.client.get_balance_allowance.assert_called_once()


@pytest.mark.asyncio
async def test_balance_check_insufficient_balance(mock_trader):
    """Test that balance check fails when USDC balance is below trade size."""
    # Mock API response with insufficient balance
    mock_trader.client.get_balance_allowance = MagicMock(
        return_value={"balance": 5.0, "allowance": 100.0}
    )

    result = await mock_trader._check_balance()

    assert result is False
    mock_trader.client.get_balance_allowance.assert_called_once()


@pytest.mark.asyncio
async def test_balance_check_insufficient_allowance(mock_trader):
    """Test that balance check fails when allowance is below trade size."""
    # Mock API response with insufficient allowance
    mock_trader.client.get_balance_allowance = MagicMock(
        return_value={"balance": 100.0, "allowance": 5.0}
    )

    result = await mock_trader._check_balance()

    assert result is False
    mock_trader.client.get_balance_allowance.assert_called_once()


@pytest.mark.asyncio
async def test_balance_check_zero_balance(mock_trader):
    """Test that balance check fails when balance is zero."""
    mock_trader.client.get_balance_allowance = MagicMock(
        return_value={"balance": 0.0, "allowance": 100.0}
    )

    result = await mock_trader._check_balance()

    assert result is False


@pytest.mark.asyncio
async def test_balance_check_no_client(mock_trader):
    """Test that balance check fails gracefully when CLOB client is not initialized."""
    # Simulate dry-run mode where client is None
    mock_trader.client = None

    result = await mock_trader._check_balance()

    assert result is False


@pytest.mark.asyncio
async def test_balance_check_api_error(mock_trader):
    """Test that balance check handles API errors gracefully."""
    # Mock API exception
    mock_trader.client.get_balance_allowance = MagicMock(
        side_effect=Exception("Network error")
    )

    result = await mock_trader._check_balance()

    assert result is False


@pytest.mark.asyncio
async def test_check_trigger_stops_on_insufficient_balance(mock_trader):
    """Test that check_trigger stops trading when balance check fails."""
    # Setup trigger conditions (all met except balance)
    mock_trader.winning_side = "YES"
    mock_trader.orderbook.best_ask_yes = 0.98
    mock_trader.orderbook.best_ask_no = 0.02
    mock_trader.TRIGGER_THRESHOLD = 90.0

    # Mock insufficient balance
    mock_trader.client.get_balance_allowance = MagicMock(
        return_value={"balance": 1.0, "allowance": 100.0}
    )

    # Mock execute_order to track if it's called
    mock_trader.execute_order = AsyncMock()

    # Trigger should NOT execute order
    await mock_trader.check_trigger(time_remaining=85.0)

    # Verify order was NOT executed
    mock_trader.execute_order.assert_not_called()

    # Verify trader stopped trying
    assert mock_trader.order_executed is True
    assert hasattr(mock_trader, "_balance_checked")


@pytest.mark.asyncio
async def test_check_trigger_proceeds_with_sufficient_balance(mock_trader):
    """Test that check_trigger executes order when balance is sufficient."""
    # Setup trigger conditions (all met including balance)
    mock_trader.winning_side = "YES"
    mock_trader.orderbook.best_ask_yes = 0.98
    mock_trader.orderbook.best_ask_no = 0.02
    mock_trader.TRIGGER_THRESHOLD = 90.0

    # Mock sufficient balance
    mock_trader.client.get_balance_allowance = MagicMock(
        return_value={"balance": 100.0, "allowance": 100.0}
    )

    # Mock execute_order
    mock_trader.execute_order = AsyncMock()

    # Trigger should execute order
    await mock_trader.check_trigger(time_remaining=85.0)

    # Verify order WAS executed
    mock_trader.execute_order.assert_called_once()


@pytest.mark.asyncio
async def test_balance_check_only_runs_once(mock_trader):
    """Test that balance check is only performed once per market."""
    # Setup trigger conditions
    mock_trader.winning_side = "YES"
    mock_trader.orderbook.best_ask_yes = 0.98
    mock_trader.orderbook.best_ask_no = 0.02
    mock_trader.TRIGGER_THRESHOLD = 90.0

    # Mock sufficient balance
    mock_trader.client.get_balance_allowance = MagicMock(
        return_value={"balance": 100.0, "allowance": 100.0}
    )

    # Mock execute_order
    mock_trader.execute_order = AsyncMock()

    # Call check_trigger twice
    await mock_trader.check_trigger(time_remaining=85.0)
    await mock_trader.check_trigger(time_remaining=84.0)

    # Balance check should only be called once
    assert mock_trader.client.get_balance_allowance.call_count == 1


@pytest.mark.asyncio
async def test_balance_check_edge_case_exact_amount(mock_trader):
    """Test balance check passes when balance exactly matches trade size."""
    # Mock API response with exact balance needed
    mock_trader.client.get_balance_allowance = MagicMock(
        return_value={"balance": 10.0, "allowance": 10.0}
    )

    result = await mock_trader._check_balance()

    # Should pass with exact amount
    assert result is True
