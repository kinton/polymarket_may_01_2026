"""
Unit tests for stop-loss, take-profit, and trailing-stop logic in HFT trader.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.clob_types import (
    STOP_LOSS_ABSOLUTE,
    STOP_LOSS_CHECK_INTERVAL_S,
    STOP_LOSS_PCT,
    TAKE_PROFIT_CHECK_INTERVAL_S,
    TAKE_PROFIT_PCT,
    TRAILING_STOP_PCT,
)
from src.hft_trader import LastSecondTrader


@pytest.fixture
def trader():
    """Create a trader instance for testing."""
    end_time = datetime.now(timezone.utc).replace(microsecond=0)
    trader = LastSecondTrader(
        condition_id="test_condition",
        token_id_yes="token_yes",
        token_id_no="token_no",
        end_time=end_time,
        dry_run=True,
        trade_size=1.0,
        title="BTC Up or Down",
        slug="btc-up-or-down",
    )
    return trader


class TestStopLoss:
    """Test stop-loss mechanism."""

    @pytest.mark.asyncio
    async def test_stop_loss_triggers_on_30_percent_drop(self, trader):
        """Test that stop-loss triggers when price drops 30% from entry."""
        # Set up position
        trader.entry_price = 0.90
        trader.position_side = "YES"
        trader.position_open = True
        trader.trailing_stop_price = 0.90 * (1 - STOP_LOSS_PCT)  # 0.63

        # Set current price below stop-loss threshold (31% drop)
        trader.orderbook.best_ask_yes = 0.62

        # Mock execute_sell - both the trader method and the stop_loss_manager callback
        mock_sell = AsyncMock()
        trader.execute_sell = mock_sell
        trader.stop_loss_manager.set_sell_callback(mock_sell)

        # Run check
        await trader._check_stop_loss_take_profit()

        # Verify sell was called
        mock_sell.assert_called_once_with("STOP-LOSS")

    @pytest.mark.asyncio
    async def test_stop_loss_does_not_trigger_on_29_percent_drop(self, trader):
        """Test that stop-loss does NOT trigger on 29% drop (under threshold)."""
        # Set up position
        trader.entry_price = 0.90
        trader.position_side = "YES"
        trader.position_open = True
        trader.trailing_stop_price = 0.90 * (1 - STOP_LOSS_PCT)  # 0.63

        # Set current price above stop-loss threshold (29% drop)
        trader.orderbook.best_ask_yes = 0.64

        # Mock execute_sell - both the trader method and the stop_loss_manager callback
        mock_sell = AsyncMock()
        trader.execute_sell = mock_sell
        trader.stop_loss_manager.set_sell_callback(mock_sell)

        # Run check
        await trader._check_stop_loss_take_profit()

        # Verify sell was NOT called
        mock_sell.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_loss_uses_absolute_floor(self, trader):
        """Test that stop-loss uses absolute floor when it's higher than percentage."""
        # Set up position with low entry price
        trader.entry_price = 0.96
        trader.position_side = "YES"
        trader.position_open = True

        # Absolute floor (0.95) is higher than percentage stop (0.96 * 0.7 = 0.672)
        trader.trailing_stop_price = STOP_LOSS_ABSOLUTE  # 0.95

        # Set current price below absolute floor
        trader.orderbook.best_ask_yes = 0.94

        # Mock execute_sell - both the trader method and the stop_loss_manager callback
        mock_sell = AsyncMock()
        trader.execute_sell = mock_sell
        trader.stop_loss_manager.set_sell_callback(mock_sell)

        # Run check
        await trader._check_stop_loss_take_profit()

        # Verify sell was called
        mock_sell.assert_called_once_with("STOP-LOSS")

    @pytest.mark.asyncio
    async def test_stop_loss_throttled_by_interval(self, trader):
        """Test that stop-loss checks are throttled by interval."""
        # Set up position
        trader.entry_price = 0.90
        trader.position_side = "YES"
        trader.position_open = True
        trader.trailing_stop_price = 0.63

        # Set current price below stop-loss threshold
        trader.orderbook.best_ask_yes = 0.62

        # Mock execute_sell - both the trader method and the stop_loss_manager callback
        mock_sell = AsyncMock()
        trader.execute_sell = mock_sell
        trader.stop_loss_manager.set_sell_callback(mock_sell)

        # First check - should trigger
        await trader._check_stop_loss_take_profit()
        assert mock_sell.call_count == 1

        # Check again immediately - should not trigger because position is already closed
        # (stop_loss_manager closes position on first trigger)
        await trader._check_stop_loss_take_profit()
        assert mock_sell.call_count == 1  # Still 1, not 2


class TestTakeProfit:
    """Test take-profit mechanism."""

    @pytest.mark.asyncio
    async def test_take_profit_triggers_on_10_percent_rise(self, trader):
        """Test that take-profit triggers when price rises 10% from entry."""
        # Set up position with low entry price so absolute floor doesn't interfere
        trader.entry_price = 0.60
        trader.position_side = "YES"
        trader.position_open = True
        trader.trailing_stop_price = 0.60 * (1 - STOP_LOSS_PCT)  # 0.42

        # Set current price above take-profit threshold (10% rise)
        trader.orderbook.best_ask_yes = 0.67  # 0.60 * 1.10 = 0.66

        # Mock execute_sell
        mock_sell = AsyncMock()
        trader.execute_sell = mock_sell
        trader.stop_loss_manager.set_sell_callback(mock_sell)

        # Run check
        await trader._check_stop_loss_take_profit()

        # Verify sell was called
        mock_sell.assert_called_once_with("TAKE-PROFIT")

    @pytest.mark.asyncio
    async def test_take_profit_does_not_trigger_on_9_percent_rise(self, trader):
        """Test that take-profit does NOT trigger on 9% rise (under threshold)."""
        # Set up position with low entry price
        trader.entry_price = 0.60
        trader.position_side = "YES"
        trader.position_open = True
        trader.trailing_stop_price = 0.60 * (1 - STOP_LOSS_PCT)  # 0.42

        # Set current price below take-profit threshold (9% rise)
        trader.orderbook.best_ask_yes = 0.65  # 0.60 * 1.09 = 0.654

        # Mock execute_sell
        mock_sell = AsyncMock()
        trader.execute_sell = mock_sell
        trader.stop_loss_manager.set_sell_callback(mock_sell)

        # Run check
        await trader._check_stop_loss_take_profit()

        # Verify sell was NOT called
        mock_sell.assert_not_called()

    @pytest.mark.asyncio
    async def test_take_profit_throttled_by_interval(self, trader):
        """Test that take-profit checks are throttled by interval."""
        # Set up position with low entry price
        trader.entry_price = 0.60
        trader.position_side = "YES"
        trader.position_open = True
        trader.trailing_stop_price = 0.60 * (1 - STOP_LOSS_PCT)  # 0.42

        # Set current price above take-profit threshold
        trader.orderbook.best_ask_yes = 0.67

        # Mock execute_sell
        mock_sell = AsyncMock()
        trader.execute_sell = mock_sell
        trader.stop_loss_manager.set_sell_callback(mock_sell)

        # First check - should trigger
        await trader._check_stop_loss_take_profit()
        assert trader.execute_sell.call_count == 1

        # Reset mock and check again immediately - should not trigger due to throttle
        mock_sell = AsyncMock()
        trader.execute_sell = mock_sell
        trader.stop_loss_manager.set_sell_callback(mock_sell)
        await trader._check_stop_loss_take_profit()
        mock_sell.assert_not_called()


class TestTrailingStop:
    """Test trailing-stop mechanism."""

    @pytest.mark.asyncio
    async def test_trailing_stop_uses_absolute_floor(self, trader):
        """Test that trailing stop uses absolute floor when price is below floor."""
        # Set up position
        trader.entry_price = 0.60
        trader.position_side = "YES"
        trader.position_open = True
        trader.trailing_stop_price = 0.60 * (1 - STOP_LOSS_PCT)  # 0.42

        # Set current price (trailing stop would be 0.66 * 0.95 = 0.627)
        # But absolute floor 0.95 is higher, so floor is used
        trader.orderbook.best_ask_yes = 0.66

        # Mock execute_sell (should not be called, just stop raised to floor)
        mock_sell = AsyncMock()
        trader.execute_sell = mock_sell
        trader.stop_loss_manager.set_sell_callback(mock_sell)

        # Run check
        await trader._check_stop_loss_take_profit()

        # Verify stop was raised to absolute floor (0.95)
        assert trader.trailing_stop_price == STOP_LOSS_ABSOLUTE
        # Verify sell was not called
        mock_sell.assert_not_called()

    @pytest.mark.asyncio
    async def test_trailing_stop_never_lowers(self, trader):
        """Test that trailing stop never lowers when price drops."""
        # Set up position with NO token side and high initial stop
        trader.entry_price = 0.60
        trader.position_side = "NO"
        trader.position_open = True
        trader.trailing_stop_price = 0.627  # Previously raised (above floor)

        # Set current price lower (should NOT lower the stop)
        # new_trailing_stop = 0.55 * 0.95 = 0.5225 < 0.627 (should not lower)
        trader.orderbook.best_ask_no = 0.55

        # Mock execute_sell
        mock_sell = AsyncMock()
        trader.execute_sell = mock_sell
        trader.stop_loss_manager.set_sell_callback(mock_sell)

        # Run check
        await trader._check_stop_loss_take_profit()

        # Verify stop was NOT lowered
        assert trader.trailing_stop_price == 0.627

    @pytest.mark.asyncio
    async def test_trailing_stop_respects_absolute_floor(self, trader):
        """Test that trailing stop respects absolute floor."""
        # Set up position with high entry price
        trader.entry_price = 0.92
        trader.position_side = "YES"
        trader.position_open = True
        trader.trailing_stop_price = 0.92 * (1 - STOP_LOSS_PCT)  # 0.644

        # Set current price high enough to hit absolute floor
        trader.orderbook.best_ask_yes = 0.97

        # Mock execute_sell
        mock_sell = AsyncMock()
        trader.execute_sell = mock_sell
        trader.stop_loss_manager.set_sell_callback(mock_sell)

        # Run check
        await trader._check_stop_loss_take_profit()

        # Verify stop is capped at absolute floor
        assert trader.trailing_stop_price == STOP_LOSS_ABSOLUTE  # 0.95


class TestPriorityAndIntegration:
    """Test priority and integration with Oracle Guard."""

    @pytest.mark.asyncio
    async def test_stop_loss_has_priority_over_oracle_guard(self, trader):
        """Test that stop-loss executes even if oracle guard would block."""
        # Set up position
        trader.entry_price = 0.90
        trader.position_side = "YES"
        trader.position_open = True
        trader.trailing_stop_price = 0.63

        # Set current price below stop-loss
        trader.orderbook.best_ask_yes = 0.62

        # Mock oracle guard to block (but stop-loss should override)
        trader.oracle_enabled = True
        trader.oracle_guard_enabled = True

        # Mock execute_sell
        mock_sell = AsyncMock()
        trader.execute_sell = mock_sell
        trader.stop_loss_manager.set_sell_callback(mock_sell)

        # Run check - should sell despite oracle guard
        await trader._check_stop_loss_take_profit()

        # Verify sell was called (stop-loss has priority)
        mock_sell.assert_called_once_with("STOP-LOSS")

    @pytest.mark.asyncio
    async def test_take_profit_has_priority_over_oracle_guard(self, trader):
        """Test that take-profit executes even if oracle guard would block."""
        # Set up position with low entry price so absolute floor doesn't interfere
        trader.entry_price = 0.60
        trader.position_side = "YES"
        trader.position_open = True
        trader.trailing_stop_price = 0.60 * (1 - STOP_LOSS_PCT)  # 0.42

        # Set current price above take-profit
        trader.orderbook.best_ask_yes = 0.67  # 0.60 * 1.10 = 0.66

        # Mock oracle guard to block (but take-profit should override)
        trader.oracle_enabled = True
        trader.oracle_guard_enabled = True

        # Mock execute_sell
        mock_sell = AsyncMock()
        trader.execute_sell = mock_sell
        trader.stop_loss_manager.set_sell_callback(mock_sell)

        # Run check - should sell despite oracle guard
        await trader._check_stop_loss_take_profit()

        # Verify sell was called (take-profit has priority)
        mock_sell.assert_called_once_with("TAKE-PROFIT")

    @pytest.mark.asyncio
    async def test_no_checks_when_position_closed(self, trader):
        """Test that no stop-loss/take-profit checks happen when position is closed."""
        # No position open
        trader.position_open = False

        # Mock execute_sell
        mock_sell = AsyncMock()
        trader.execute_sell = mock_sell
        trader.stop_loss_manager.set_sell_callback(mock_sell)

        # Run check - should do nothing
        await trader._check_stop_loss_take_profit()

        # Verify sell was never called
        mock_sell.assert_not_called()


class TestPositionTracking:
    """Test position tracking after buy orders."""

    @pytest.mark.asyncio
    async def test_position_tracked_after_dry_run_buy(self, trader):
        """Test that position state is set after dry run buy."""
        # Set up orderbook with low price so absolute floor takes precedence
        trader.orderbook.best_ask_yes = 0.80

        # Execute buy
        await trader.execute_order_for("YES")

        # Verify position state
        assert trader.position_open is True
        assert trader.entry_price == 0.80
        assert trader.position_side == "YES"
        # Absolute floor (0.95) should be used since 0.80 * 0.70 = 0.56 < 0.95
        assert trader.trailing_stop_price == STOP_LOSS_ABSOLUTE

    @pytest.mark.asyncio
    async def test_position_closed_after_sell(self, trader):
        """Test that position state is cleared after sell."""
        # Set up position
        trader.entry_price = 0.90
        trader.position_side = "YES"
        trader.position_open = True
        trader.trailing_stop_price = 0.63
        trader.orderbook.best_ask_yes = 0.85

        # Execute sell
        await trader.execute_sell("TEST_REASON")

        # Verify position state cleared
        assert trader.position_open is False
        assert trader.entry_price is None
        assert trader.position_side is None
        assert trader.trailing_stop_price is None


class TestConstants:
    """Test that constants are properly defined."""

    def test_stop_loss_constants(self):
        """Verify stop-loss constants."""
        assert STOP_LOSS_PCT == 0.30
        assert STOP_LOSS_ABSOLUTE == 0.95
        assert STOP_LOSS_CHECK_INTERVAL_S == 1.0

    def test_take_profit_constants(self):
        """Verify take-profit constants."""
        assert TAKE_PROFIT_PCT == 0.10
        assert TAKE_PROFIT_CHECK_INTERVAL_S == 1.0

    def test_trailing_stop_constants(self):
        """Verify trailing-stop constants."""
        assert TRAILING_STOP_PCT == 0.05
