"""
Unit tests for balance checking logic in hft_trader.py

Tests verify that the trader correctly checks USDC balance and allowance
before attempting to execute orders, preventing failed trades due to
insufficient funds.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

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
