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
from src.clob_types import EXCHANGE_CONTRACT


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
            trade_size=1.5,
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
    # With $100 balance: required = max($1.5, $1.0, 5%_balance=$5.00) = $5.00
    mock_trader.client.get_balance_allowance = MagicMock(
        return_value={
            "balance": int(100 * 1e6),
            "allowances": {EXCHANGE_CONTRACT: int(100 * 1e6)},
        }
    )

    result = await mock_trader._check_balance()

    assert result is True
    # Dynamic sizing: max(MIN_TRADE_USDC=$1.0, trade_size=$1.5, 5%_balance=$5.0) = $5.0
    assert mock_trader._planned_trade_amount == 5.00
    mock_trader.client.get_balance_allowance.assert_called_once()


@pytest.mark.asyncio
async def test_balance_check_insufficient_balance(mock_trader):
    """Test that balance check fails when USDC balance is below trade size."""
    # Mock API response with insufficient balance
    # With $1.0 balance: required = max($1.0, $1.5, $0.05) = $1.5 (5% is negligible)
    mock_trader.client.get_balance_allowance = MagicMock(
        return_value={
            "balance": int(1.0 * 1e6),
            "allowances": {EXCHANGE_CONTRACT: int(100 * 1e6)},
        }
    )

    result = await mock_trader._check_balance()

    assert result is False
    # Should calculate required = $1.50 (max of MIN=$1.0, trade_size=$1.5, 5%=$0.05)
    assert mock_trader._planned_trade_amount == 1.50
    mock_trader.client.get_balance_allowance.assert_called_once()


@pytest.mark.asyncio
async def test_balance_check_insufficient_allowance(mock_trader):
    """Test that balance check fails when allowance is below trade size."""
    # Mock API response with insufficient allowance
    mock_trader.client.get_balance_allowance = MagicMock(
        return_value={
            "balance": int(100 * 1e6),
            "allowances": {"0x4b2...4a44": int(1.0 * 1e6)},
        }
    )

    result = await mock_trader._check_balance()

    assert result is False
    mock_trader.client.get_balance_allowance.assert_called_once()


@pytest.mark.asyncio
async def test_balance_check_zero_balance(mock_trader):
    """Test that balance check fails when balance is zero."""
    mock_trader.client.get_balance_allowance = MagicMock(
        return_value={"balance": 0, "allowances": {"0x4b2...4a44": int(100 * 1e6)}}
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
    mock_trader.orderbook.best_ask_yes = 0.89
    mock_trader.orderbook.best_ask_no = 0.11
    mock_trader.TRIGGER_THRESHOLD = 90.0

    # Mock insufficient balance
    mock_trader.client.get_balance_allowance = MagicMock(
        return_value={
            "balance": int(1.0 * 1e6),
            "allowances": {EXCHANGE_CONTRACT: int(100 * 1e6)},
        }
    )

    # Mock execute_order to track if it's called
    mock_trader.execute_order = AsyncMock()

    # Trigger should NOT execute order
    await mock_trader.check_trigger(time_remaining=85.0)

    # Verify order was NOT executed
    mock_trader.execute_order.assert_not_called()

    # Verify trader stopped trying
    assert mock_trader.order_executed is True
    assert "balance_checked" in mock_trader._logged_warnings


@pytest.mark.asyncio
async def test_check_trigger_proceeds_with_sufficient_balance(mock_trader):
    """Test that check_trigger executes order when balance is sufficient."""
    # Setup trigger conditions (all met including balance)
    mock_trader.winning_side = "YES"
    mock_trader.orderbook.best_ask_yes = 0.89
    mock_trader.orderbook.best_ask_no = 0.11
    mock_trader.TRIGGER_THRESHOLD = 90.0

    # Mock sufficient balance
    mock_trader.client.get_balance_allowance = MagicMock(
        return_value={
            "balance": int(100 * 1e6),
            "allowances": {EXCHANGE_CONTRACT: int(100 * 1e6)},
        }
    )

    # Mock execute_order - need to mock the order_execution manager's method
    mock_trader.order_execution.execute_order_for = AsyncMock(return_value=True)

    # Trigger should execute order
    await mock_trader.check_trigger(time_remaining=85.0)

    # Verify order WAS executed
    mock_trader.order_execution.execute_order_for.assert_called_once()


@pytest.mark.asyncio
async def test_balance_check_only_runs_once(mock_trader):
    """Test that balance check is only performed once per market."""
    # Setup trigger conditions
    mock_trader.winning_side = "YES"
    mock_trader.orderbook.best_ask_yes = 0.89
    mock_trader.orderbook.best_ask_no = 0.11
    mock_trader.TRIGGER_THRESHOLD = 90.0

    # Mock sufficient balance
    mock_trader.client.get_balance_allowance = MagicMock(
        return_value={
            "balance": int(100 * 1e6),
            "allowances": {EXCHANGE_CONTRACT: int(100 * 1e6)},
        }
    )

    # Mock execute_order to mark executed to prevent repeated calls in test
    # Call mark_executed() and also set the internal order_executed attribute directly
    # to ensure the mock is_executed() returns True
    async def _exec_once(side, winning_ask):
        mock_trader.order_execution.mark_executed()
        # Also set the attribute directly on the mock to ensure it works
        mock_trader.order_execution.order_executed = True
        return True

    mock_trader.order_execution.execute_order_for = AsyncMock(side_effect=_exec_once)

    # Call check_trigger twice
    await mock_trader.check_trigger(time_remaining=85.0)
    await mock_trader.check_trigger(time_remaining=84.0)

    # check_risk_limits() was removed (2026-02-12), so check_balance() only calls get_balance_allowance once
    # Expected: 1 call from check_balance() (subsequent calls skip due to order_executed)
    assert mock_trader.client.get_balance_allowance.call_count == 1


@pytest.mark.asyncio
async def test_balance_check_edge_case_exact_amount(mock_trader):
    """Test balance check passes when balance exactly matches trade size."""
    # Mock API response with exact balance needed
    mock_trader.client.get_balance_allowance = MagicMock(
        return_value={
            # Exactly $1.50 balance and allowance (trade_size param)
            "balance": int(1.5 * 1e6),
            "allowances": {EXCHANGE_CONTRACT: int(1.5 * 1e6)},
        }
    )

    result = await mock_trader._check_balance()

    # Should pass with exact amount
    assert result is True
