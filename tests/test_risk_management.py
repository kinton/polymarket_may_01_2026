"""
Unit tests for risk management limits in hft_trader.py

Tests verify that:
1. Maximum capital percentage per trade is enforced (5%)
2. Daily loss limit is enforced (10%)
3. Daily trade count limit is enforced (20 trades)
4. Daily state is properly tracked and persisted
5. Day reset creates new tracking entries
6. Integration with check_trigger() prevents trades when limits exceeded
"""

import json
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.clob_types import (
    EXCHANGE_CONTRACT,
    MAX_CAPITAL_PCT_PER_TRADE,
    MAX_DAILY_LOSS_PCT,
    MAX_TOTAL_TRADES_PER_DAY,
)
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
            trade_size=1.0,
            dry_run=False,  # Need to test with client
            title="Bitcoin Test Market",
        )

        # Mock the CLOB client
        trader.client = MagicMock()
        trader.market_name = "BTC"
        trader.logger = None  # Disable file logging in tests

        return trader


@pytest.fixture
def cleanup_daily_limits(mock_trader):
    """Ensure daily_limits.json is cleaned up before and after each test."""
    path = mock_trader._get_daily_limits_path()

    # Clean up before test
    if os.path.exists(path):
        os.remove(path)

    yield  # Run the test

    # Clean up after test
    if os.path.exists(path):
        os.remove(path)


def test_track_daily_pnl_multiple_trades(mock_trader, cleanup_daily_limits):
    """Test that tracking accumulates PnL across multiple trades."""
    mock_trader._track_daily_pnl(10.0, 1.5)
    mock_trader._track_daily_pnl(10.0, -0.5)
    mock_trader._track_daily_pnl(10.0, 2.0)

    path = mock_trader._get_daily_limits_path()
    with open(path, "r") as f:
        data = json.load(f)

    assert data["total_trades"] == 3
    assert data["current_pnl"] == 3.0  # 1.5 - 0.5 + 2.0


def test_track_daily_pnl_new_day_resets(mock_trader, cleanup_daily_limits):
    """Test that a new day creates a fresh tracking entry."""
    # Simulate a trade on one day
    with patch("src.trading.risk_manager.datetime") as mock_datetime:
        mock_datetime.now = MagicMock(
            return_value=datetime(2026, 2, 10, 12, 0, 0, tzinfo=timezone.utc)
        )
        mock_trader._track_daily_pnl(10.0, 1.5)

    # Now simulate a trade on the next day
    with patch("src.trading.risk_manager.datetime") as mock_datetime:
        mock_datetime.now = MagicMock(
            return_value=datetime(2026, 2, 11, 12, 0, 0, tzinfo=timezone.utc)
        )
        mock_trader._track_daily_pnl(10.0, 2.0)

    path = mock_trader._get_daily_limits_path()
    with open(path, "r") as f:
        data = json.load(f)

    # Should have data for today (2/11), not yesterday (2/10)
    assert data["date"] == "2026-02-11"
    assert data["total_trades"] == 1
    assert data["current_pnl"] == 2.0


def test_track_daily_pnl_sets_initial_balance(mock_trader, cleanup_daily_limits):
    """Test that initial balance is captured on first trade."""
    mock_trader.client.get_balance_allowance = MagicMock(
        return_value={"balance": int(500 * 1e6), "allowances": {}}
    )

    mock_trader._track_daily_pnl(10.0, 1.5)

    path = mock_trader._get_daily_limits_path()
    with open(path, "r") as f:
        data = json.load(f)

    assert data["initial_balance"] == 500.0


# Test daily limits check
def test_check_daily_limits_no_file(mock_trader, cleanup_daily_limits):
    """Test that limits pass when no tracking file exists."""
    result = mock_trader._check_daily_limits()
    assert result is True


def test_check_daily_limits_new_day_resets(mock_trader, cleanup_daily_limits):
    """Test that a new day resets the limits."""
    path = mock_trader._get_daily_limits_path()

    # Create file from yesterday
    with open(path, "w") as f:
        json.dump(
            {
                "date": "2026-02-09",
                "initial_balance": 500.0,
                "current_pnl": -100.0,  # Would exceed limit
                "total_trades": 25,  # Would exceed limit
            },
            f,
        )

    # Should pass since it's a new day
    result = mock_trader._check_daily_limits()
    assert result is True


def test_check_daily_limits_loss_exceeded(mock_trader, cleanup_daily_limits):
    """Test that loss limit check stops trading when exceeded."""
    path = mock_trader._get_daily_limits_path()

    # Create file with exceeded loss (20% of $100 = $20 max loss, we lost $25)
    with open(path, "w") as f:
        json.dump(
            {
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "initial_balance": 100.0,
                "current_pnl": -25.0,
                "total_trades": 5,
            },
            f,
        )

    result = mock_trader._check_daily_limits()

    assert result is False  # Loss limit exceeded


def test_check_daily_limits_loss_exactly_at_limit(mock_trader, cleanup_daily_limits):
    """Test that loss limit check passes when exactly at limit."""
    path = mock_trader._get_daily_limits_path()

    # Create file with loss exactly at limit (20% of $100 = $20)
    with open(path, "w") as f:
        json.dump(
            {
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "initial_balance": 100.0,
                "current_pnl": -20.0,
                "total_trades": 5,
            },
            f,
        )

    result = mock_trader._check_daily_limits()

    assert result is True  # Exactly at limit should still pass


def test_check_daily_limits_trade_count_exceeded(mock_trader, cleanup_daily_limits):
    """Test that trade count limit stops trading when exceeded."""
    path = mock_trader._get_daily_limits_path()

    # Create file with exceeded trade count
    with open(path, "w") as f:
        json.dump(
            {
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "initial_balance": 100.0,
                "current_pnl": 5.0,
                "total_trades": 101,  # Exceeds MAX_TOTAL_TRADES_PER_DAY (limit is 100)
            },
            f,
        )

    result = mock_trader._check_daily_limits()

    assert result is False  # Trade count limit exceeded


def test_check_daily_limits_trade_count_at_limit(mock_trader, cleanup_daily_limits):
    """Test that trade count limit passes when exactly at limit."""
    path = mock_trader._get_daily_limits_path()

    # Create file with trade count exactly at limit
    with open(path, "w") as f:
        json.dump(
            {
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "initial_balance": 100.0,
                "current_pnl": 5.0,
                "total_trades": MAX_TOTAL_TRADES_PER_DAY,
            },
            f,
        )

    result = mock_trader._check_daily_limits()

    assert result is False  # At limit should stop


def test_check_daily_limits_profit(mock_trader, cleanup_daily_limits):
    """Test that profit doesn't trigger loss limit."""
    path = mock_trader._get_daily_limits_path()

    # Create file with profit
    with open(path, "w") as f:
        json.dump(
            {
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "initial_balance": 100.0,
                "current_pnl": 15.0,  # Profit
                "total_trades": 10,
            },
            f,
        )

    result = mock_trader._check_daily_limits()

    assert result is True


# Test integration with check_trigger
@pytest.mark.asyncio
async def test_check_trigger_stops_on_daily_loss_limit(
    mock_trader, cleanup_daily_limits
):
    """Test that check_trigger stops trading when daily loss limit exceeded."""
    # Setup daily limits file with exceeded loss
    path = mock_trader._get_daily_limits_path()
    with open(path, "w") as f:
        json.dump(
            {
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "initial_balance": 100.0,
                "current_pnl": -15.0,
                "total_trades": 5,
            },
            f,
        )

    # Setup trigger conditions
    mock_trader.winning_side = "YES"
    mock_trader.orderbook.best_ask_yes = 0.89
    mock_trader.orderbook.best_ask_no = 0.11
    mock_trader.TRIGGER_THRESHOLD = 90.0
    mock_trader.client.get_balance_allowance = MagicMock(
        return_value={"balance": int(100 * 1e6), "allowances": {}}
    )
    mock_trader.execute_order = AsyncMock()

    # Trigger should NOT execute order due to daily limit
    await mock_trader.check_trigger(time_remaining=85.0)

    # Verify order was NOT executed
    mock_trader.execute_order.assert_not_called()
    assert mock_trader.order_executed is True


@pytest.mark.asyncio
async def test_check_trigger_stops_on_trade_count_limit(
    mock_trader, cleanup_daily_limits
):
    """Test that check_trigger stops trading when trade count limit exceeded."""
    # Setup daily limits file with exceeded trade count
    path = mock_trader._get_daily_limits_path()
    with open(path, "w") as f:
        json.dump(
            {
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "initial_balance": 100.0,
                "current_pnl": 5.0,
                "total_trades": 25,
            },
            f,
        )

    # Setup trigger conditions
    mock_trader.winning_side = "YES"
    mock_trader.orderbook.best_ask_yes = 0.89
    mock_trader.orderbook.best_ask_no = 0.11
    mock_trader.TRIGGER_THRESHOLD = 90.0
    mock_trader.client.get_balance_allowance = MagicMock(
        return_value={"balance": int(100 * 1e6), "allowances": {}}
    )
    mock_trader.execute_order = AsyncMock()

    # Trigger should NOT execute order due to trade count limit
    await mock_trader.check_trigger(time_remaining=85.0)

    # Verify order was NOT executed
    mock_trader.execute_order.assert_not_called()
    assert mock_trader.order_executed is True


@pytest.mark.asyncio
async def test_check_trigger_stops_on_capital_limit(mock_trader, cleanup_daily_limits):
    """Test that check_trigger stops trading when capital % limit exceeded."""
    # Setup trigger conditions
    mock_trader.winning_side = "YES"
    mock_trader.orderbook.best_ask_yes = 0.89
    mock_trader.orderbook.best_ask_no = 0.11
    mock_trader.TRIGGER_THRESHOLD = 90.0

    # Mock balance that would make planned trade exceed 5% limit
    mock_trader.client.get_balance_allowance = MagicMock(
        return_value={"balance": int(50 * 1e6), "allowances": {}}  # $50, max 5% = $2.5
    )
    mock_trader._planned_trade_amount = 10.0  # $10 > $2.5 limit
    mock_trader.execute_order = AsyncMock()

    # Trigger should NOT execute order due to capital limit
    await mock_trader.check_trigger(time_remaining=85.0)

    # Verify order was NOT executed
    mock_trader.execute_order.assert_not_called()
    assert mock_trader.order_executed is True


@pytest.mark.asyncio
async def test_check_trigger_proceeds_when_limits_ok(mock_trader, cleanup_daily_limits):
    """Test that check_trigger executes order when all limits are OK."""
    # Setup daily limits file with everything in bounds
    path = mock_trader._get_daily_limits_path()
    with open(path, "w") as f:
        json.dump(
            {
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "initial_balance": 100.0,
                "current_pnl": 5.0,
                "total_trades": 10,
            },
            f,
        )

    # Setup trigger conditions
    mock_trader.winning_side = "YES"
    mock_trader.orderbook.best_ask_yes = 0.89
    mock_trader.orderbook.best_ask_no = 0.11
    mock_trader.TRIGGER_THRESHOLD = 90.0
    mock_trader.client.get_balance_allowance = MagicMock(
        return_value={
            "balance": int(100 * 1e6),
            "allowances": {EXCHANGE_CONTRACT: int(100 * 1e6)},
        }  # $100, max 5% = $5
    )
    mock_trader._planned_trade_amount = 4.0  # $4 < $5 limit
    mock_trader.execute_order = AsyncMock()

    # Trigger should execute order
    await mock_trader.check_trigger(time_remaining=85.0)

    # Verify order WAS executed
    mock_trader.execute_order.assert_called_once()


# Test constants
def test_max_capital_pct_per_trade():
    """Test MAX_CAPITAL_PCT_PER_TRADE constant."""
    assert MAX_CAPITAL_PCT_PER_TRADE == 0.05  # 5%


def test_max_daily_loss_pct():
    """Test MAX_DAILY_LOSS_PCT constant."""
    assert MAX_DAILY_LOSS_PCT == 0.20  # 20% (for $10 capital)


def test_max_total_trades_per_day():
    """Test MAX_TOTAL_TRADES_PER_DAY constant."""
    assert MAX_TOTAL_TRADES_PER_DAY == 100  # Increased from 20 to 100
