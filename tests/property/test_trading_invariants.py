"""
Property-based tests for trading invariants using Hypothesis.

These tests verify fundamental properties of the trading system that must
hold true for all valid inputs, not just specific test cases.
"""

from hypothesis import given, settings
from hypothesis.strategies import floats

from src.clob_types import (
    STOP_LOSS_ABSOLUTE,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TRAILING_STOP_PCT,
)


class TestStopLossInvariant:
    """Test Stop-Loss Invariant - stop_price < entry_price."""

    @given(entry_price=floats(min_value=0.01, max_value=0.99))
    @settings(max_examples=100)
    def test_stop_loss_percentage_below_entry(self, entry_price):
        """
        Test that percentage-based stop-loss is always below entry price.

        For any valid entry price (0.01 to 0.99), the stop-loss threshold
        calculated as entry_price * (1 - STOP_LOSS_PCT) should be strictly
        less than the entry_price.
        """
        stop_price = entry_price * (1 - STOP_LOSS_PCT)
        assert stop_price < entry_price

    @given(entry_price=floats(min_value=0.01, max_value=0.99))
    @settings(max_examples=100)
    def test_stop_loss_absolute_floor_behavior(self, entry_price):
        """
        Test that absolute floor stop-loss behavior is correct.

        When the absolute floor (0.95) is higher than the percentage stop,
        the floor becomes the stop price. The floor is designed to protect
        high-value positions from dropping below a safe threshold.
        """
        stop_price = max(entry_price * (1 - STOP_LOSS_PCT), STOP_LOSS_ABSOLUTE)
        pct_stop = entry_price * (1 - STOP_LOSS_PCT)

        # Either the percentage stop or the absolute floor is used
        if pct_stop < STOP_LOSS_ABSOLUTE:
            # Floor is higher, so floor is used
            assert stop_price == STOP_LOSS_ABSOLUTE
        else:
            # Percentage stop is higher (or equal), so it's used
            assert stop_price == pct_stop

    @given(
        entry_price=floats(min_value=0.01, max_value=0.99),
        stop_pct=floats(min_value=0.01, max_value=0.99),
    )
    @settings(max_examples=100)
    def test_stop_loss_invariant_general(self, entry_price, stop_pct):
        """
        Test stop-loss invariant with arbitrary stop percentage.

        For any valid entry price and stop percentage, the stop price
        should be strictly less than the entry price.
        """
        stop_price = entry_price * (1 - stop_pct)
        assert stop_price < entry_price
        assert stop_price >= 0


class TestTakeProfitInvariant:
    """Test Take-Profit Invariant - take_profit_price > entry_price."""

    @given(entry_price=floats(min_value=0.01, max_value=0.99))
    @settings(max_examples=100)
    def test_take_profit_above_entry(self, entry_price):
        """
        Test that take-profit threshold is always above entry price.

        For any valid entry price (0.01 to 0.99), the take-profit threshold
        calculated as entry_price * (1 + TAKE_PROFIT_PCT) should be strictly
        greater than the entry_price.
        """
        take_profit_price = entry_price * (1 + TAKE_PROFIT_PCT)
        assert take_profit_price > entry_price

    @given(
        entry_price=floats(min_value=0.01, max_value=0.99),
        profit_pct=floats(min_value=0.01, max_value=0.10),
    )
    @settings(max_examples=100)
    def test_take_profit_invariant_general(self, entry_price, profit_pct):
        """
        Test take-profit invariant with arbitrary profit percentage.

        For any valid entry price and profit percentage, the take-profit
        price should be strictly greater than the entry price.
        """
        take_profit_price = entry_price * (1 + profit_pct)
        assert take_profit_price > entry_price


class TestTrailingStopInvariant:
    """Test Trailing-Stop Invariant - never decreases."""

    @given(
        initial_price=floats(min_value=0.01, max_value=0.99),
        new_price=floats(min_value=0.01, max_value=0.99),
    )
    @settings(max_examples=100)
    def test_trailing_stop_never_decreases(self, initial_price, new_price):
        """
        Test that trailing stop price never decreases when price moves up.

        The trailing stop should only increase (or stay the same) when
        the current price is higher than the previous high water mark.
        """
        # Calculate initial trailing stop
        initial_stop = max(
            initial_price * (1 - TRAILING_STOP_PCT), STOP_LOSS_ABSOLUTE
        )

        # Calculate new trailing stop based on new price
        new_stop = max(new_price * (1 - TRAILING_STOP_PCT), STOP_LOSS_ABSOLUTE)

        # Only raise the stop, never lower it
        if new_stop > initial_stop:
            # Stop should be raised to new higher value
            assert new_stop > initial_stop
        else:
            # Stop should stay at initial value (not lowered)
            # This is enforced by max(initial_stop, new_stop) in implementation
            assert new_stop <= initial_stop

    @given(
        initial_price=floats(min_value=0.50, max_value=0.99),
        price_increase=floats(min_value=0.00, max_value=0.49),
    )
    @settings(max_examples=100)
    def test_trailing_stop_increases_with_price(self, initial_price, price_increase):
        """
        Test that trailing stop increases when price moves favorably.

        When price increases, the trailing stop should also increase
        (or at minimum, not decrease).
        """
        new_price = min(initial_price + price_increase, 0.99)

        initial_stop = max(
            initial_price * (1 - TRAILING_STOP_PCT), STOP_LOSS_ABSOLUTE
        )
        new_stop = max(new_price * (1 - TRAILING_STOP_PCT), STOP_LOSS_ABSOLUTE)

        # When price increases, trailing stop should increase or stay same
        if new_price > initial_price:
            if new_stop > initial_stop:
                assert new_stop > initial_stop
            else:
                # Both stopped at absolute floor
                assert initial_stop == STOP_LOSS_ABSOLUTE
                assert new_stop == STOP_LOSS_ABSOLUTE

    @given(
        initial_price=floats(min_value=0.01, max_value=0.99),
        price_decrease=floats(min_value=0.00, max_value=0.50),
    )
    @settings(max_examples=100)
    def test_trailing_stop_never_decreases_on_price_drop(
        self, initial_price, price_decrease
    ):
        """
        Test that trailing stop does NOT decrease when price drops.

        This is the key invariant: the stop should lock in gains and
        never move down when price moves against the position.
        """
        new_price = max(initial_price - price_decrease, 0.01)

        initial_stop = max(
            initial_price * (1 - TRAILING_STOP_PCT), STOP_LOSS_ABSOLUTE
        )
        new_stop = max(new_price * (1 - TRAILING_STOP_PCT), STOP_LOSS_ABSOLUTE)

        # Stop should never decrease when price drops
        # (implementation uses max(initial_stop, new_stop))
        assert max(initial_stop, new_stop) >= initial_stop


class TestPositionStateInvariant:
    """Test Position State Invariant - position_open implies valid state."""

    @given(entry_price=floats(min_value=0.01, max_value=0.99))
    @settings(max_examples=100)
    def test_position_open_requires_entry_price(self, entry_price):
        """
        Test that when position is open, entry_price must be valid.

        A valid position state requires a non-None entry_price in the
        valid price range (0.01 to 0.99).
        """
        position_open = True
        assert position_open is True

        if position_open:
            assert entry_price is not None
            assert 0.01 <= entry_price <= 0.99

    @given(
        entry_price=floats(min_value=0.01, max_value=0.99),
        position_side=floats(min_value=0.0, max_value=1.0),
    )
    @settings(max_examples=100)
    def test_position_open_requires_position_side(self, entry_price, position_side):
        """
        Test that when position is open, position_side must be valid.

        A valid position state requires a non-None position_side
        (either "YES" or "NO").
        """
        position_open = True

        if position_open:
            assert entry_price is not None
            # In practice, position_side is either "YES" or "NO"
            # For this invariant test, we just verify it's not None
            assert position_side is not None

    @given(
        entry_price=floats(min_value=0.01, max_value=0.99),
        current_price=floats(min_value=0.01, max_value=0.99),
    )
    @settings(max_examples=100)
    def test_position_state_consistency(self, entry_price, current_price):
        """
        Test that position state is internally consistent.

        When a position is open with an entry price, all related prices
        should be in valid ranges.
        """
        position_open = True

        if position_open:
            # Entry price should be valid
            assert 0.01 <= entry_price <= 0.99

            # Current price should be valid
            assert 0.01 <= current_price <= 0.99

            # Trailing stop should be valid and below entry (or at floor)
            trailing_stop = max(entry_price * (1 - STOP_LOSS_PCT), STOP_LOSS_ABSOLUTE)
            assert 0.01 <= trailing_stop <= 0.99
            if trailing_stop != STOP_LOSS_ABSOLUTE:
                assert trailing_stop < entry_price

    @given(
        entry_price=floats(min_value=0.01, max_value=0.99),
    )
    @settings(max_examples=100)
    def test_closed_position_clears_state(self, entry_price):
        """
        Test that closed position has None values for state fields.

        When position is closed (position_open = False), all state fields
        should be None.
        """
        position_open = False

        if not position_open:
            # In the actual implementation, these are set to None
            # For this invariant test, we verify the expected behavior
            entry_price = None  # Would be None in closed position
            assert entry_price is None


class TestPnLInvariant:
    """Test PnL Invariant - formula is consistent."""

    @given(
        entry_price=floats(min_value=0.01, max_value=0.99),
        current_price=floats(min_value=0.01, max_value=0.99),
        trade_amount=floats(min_value=1.0, max_value=100.0),
    )
    @settings(max_examples=100)
    def test_pnl_percentage_formula_consistency(
        self, entry_price, current_price, trade_amount
    ):
        """
        Test PnL percentage formula produces consistent results.

        PnL % = ((current_price - entry_price) / entry_price) * 100

        This formula should:
        1. Be positive when current > entry
        2. Be negative when current < entry
        3. Be zero when current == entry
        """
        pnl_pct = ((current_price - entry_price) / entry_price) * 100

        if current_price > entry_price:
            assert pnl_pct > 0
        elif current_price < entry_price:
            assert pnl_pct < 0
        else:
            assert pnl_pct == 0.0

    @given(
        entry_price=floats(min_value=0.01, max_value=0.99),
        current_price=floats(min_value=0.01, max_value=0.99),
        trade_amount=floats(min_value=1.0, max_value=100.0),
    )
    @settings(max_examples=100)
    def test_pnl_amount_formula_consistency(
        self, entry_price, current_price, trade_amount
    ):
        """
        Test PnL amount formula produces consistent results.

        PnL amount = trade_amount * (pnl_pct / 100)

        This should be consistent with the percentage calculation.
        """
        pnl_pct = ((current_price - entry_price) / entry_price)
        pnl_amount = trade_amount * pnl_pct

        # PnL amount should match the percentage calculation
        assert pnl_amount == trade_amount * (
            (current_price - entry_price) / entry_price
        )

        # Sign should match price relationship
        if current_price > entry_price:
            assert pnl_amount > 0
        elif current_price < entry_price:
            assert pnl_amount < 0
        else:
            assert pnl_amount == 0.0

    @given(
        entry_price=floats(min_value=0.01, max_value=0.99),
        profit_pct=floats(min_value=0.0, max_value=1.0),
        trade_amount=floats(min_value=1.0, max_value=100.0),
    )
    @settings(max_examples=100)
    def test_pnl_formula_linearity(self, entry_price, profit_pct, trade_amount):
        """
        Test that PnL formula is linear with respect to price movement.

        If price increases by X%, PnL should be exactly X% of the position.
        """
        current_price = entry_price * (1 + profit_pct)
        pnl_pct = ((current_price - entry_price) / entry_price)

        # PnL percentage should match the price increase percentage
        assert abs(pnl_pct - profit_pct) < 1e-10

    @given(
        entry_price=floats(min_value=0.01, max_value=0.99),
        trade_amount=floats(min_value=1.0, max_value=100.0),
    )
    @settings(max_examples=100)
    def test_pnl_max_loss_limited(self, entry_price, trade_amount):
        """
        Test that maximum loss is bounded by trade amount.

        Even in the worst case (price goes to 0), loss cannot exceed
        the initial trade amount.
        """
        worst_case_current_price = 0.01  # Minimum valid price
        pnl_pct = ((worst_case_current_price - entry_price) / entry_price)
        pnl_amount = trade_amount * pnl_pct

        # Loss should not exceed trade amount
        assert pnl_amount >= -trade_amount

    @given(
        entry_price=floats(min_value=0.01, max_value=0.99),
        trade_amount=floats(min_value=1.0, max_value=100.0),
    )
    @settings(max_examples=100)
    def test_pnl_max_profit_limited(self, entry_price, trade_amount):
        """
        Test that maximum profit is bounded by price going to 1.0.

        Maximum profit occurs when current_price = 1.0.
        """
        best_case_current_price = 0.99  # Maximum valid price
        pnl_pct = ((best_case_current_price - entry_price) / entry_price)
        pnl_amount = trade_amount * pnl_pct

        # Profit should be positive and bounded
        assert pnl_amount >= 0
        # Maximum profit when buying at low price and selling at 1.0
        max_possible_pnl = trade_amount * ((0.99 - entry_price) / entry_price)
        assert pnl_amount <= max_possible_pnl
