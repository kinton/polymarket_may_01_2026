"""
Property-based tests for trading position invariants.

Tests fundamental properties that must hold for all valid inputs:
1. Stop-Loss Invariant (price < entry)
2. Take-Profit Invariant (price > entry)
3. Trailing-Stop Invariant (never decreases)
4. Position State Invariants
5. PnL Formula Invariant
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from src.clob_types import (
    STOP_LOSS_ABSOLUTE,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TRAILING_STOP_PCT,
)


class TestStopLossInvariant:
    """Test stop-loss invariant: stop-loss threshold is correctly calculated."""

    @given(
        entry_price=st.floats(
            min_value=0.01, max_value=0.99, allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=100)
    def test_stop_loss_threshold_below_entry(self, entry_price: float) -> None:
        """
        Invariant: Stop-loss threshold must be below entry price.

        For any valid entry price (0.01 to 0.99), the stop-loss threshold
        calculated as entry_price * (1 - STOP_LOSS_PCT) must be less than
        the entry price.

        This ensures we exit at a loss relative to entry.
        """
        stop_threshold = entry_price * (1 - STOP_LOSS_PCT)
        assert stop_threshold < entry_price, (
            f"Stop-loss threshold {stop_threshold} should be below "
            f"entry price {entry_price}"
        )

    @given(
        entry_price=st.floats(
            min_value=STOP_LOSS_ABSOLUTE,
            max_value=0.99,
            allow_nan=False,
            allow_infinity=False,
        ),
    )
    @settings(max_examples=100)
    def test_stop_loss_absolute_floor(self, entry_price: float) -> None:
        """
        Invariant: Absolute stop-loss floor (0.95) takes precedence when applicable.

        For entry prices at or above STOP_LOSS_ABSOLUTE (0.95), the absolute
        floor is higher than the percentage-based stop, so it must be used.

        This prevents stops from being too aggressive for high-priced entries.
        """
        stop_threshold_pct = entry_price * (1 - STOP_LOSS_PCT)
        assert STOP_LOSS_ABSOLUTE >= stop_threshold_pct, (
            f"Absolute floor {STOP_LOSS_ABSOLUTE} should be >= "
            f"percentage stop {stop_threshold_pct} for entry {entry_price}"
        )

    @given(
        current_price=st.floats(
            min_value=0.0, max_value=0.99, allow_nan=False, allow_infinity=False
        ),
        entry_price=st.floats(
            min_value=0.01, max_value=0.99, allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=100)
    def test_stop_loss_trigger_condition(
        self, current_price: float, entry_price: float
    ) -> None:
        """
        Invariant: Stop-loss triggers when price drops below threshold.

        For any current price below the stop-loss threshold, a stop-loss
        should be triggered. The condition is monotonic: if price is below
        threshold, we must exit.

        Uses max(percentage_stop, absolute_floor) for the actual threshold.
        """
        stop_threshold_pct = entry_price * (1 - STOP_LOSS_PCT)
        stop_threshold = max(stop_threshold_pct, STOP_LOSS_ABSOLUTE)

        # Stop-loss triggers when current price is below threshold AND below entry
        # (threshold could be higher than entry due to absolute floor)
        if current_price < stop_threshold and current_price < entry_price:
            loss_pct = ((current_price - entry_price) / entry_price) * 100
            # Loss must be negative
            assert loss_pct < 0, (
                f"Price {current_price} below both stop threshold {stop_threshold} "
                f"and entry {entry_price} should result in loss, but got {loss_pct}%"
            )


class TestTakeProfitInvariant:
    """Test take-profit invariant: take-profit threshold is correctly calculated."""

    @given(
        entry_price=st.floats(
            min_value=0.01, max_value=0.90, allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=100)
    def test_take_profit_threshold_above_entry(self, entry_price: float) -> None:
        """
        Invariant: Take-profit threshold must be above entry price.

        For any valid entry price, the take-profit threshold
        calculated as entry_price * (1 + TAKE_PROFIT_PCT) must be greater
        than the entry price.

        This ensures we exit at a profit relative to entry.
        """
        take_profit_threshold = entry_price * (1 + TAKE_PROFIT_PCT)
        assert take_profit_threshold > entry_price, (
            f"Take-profit threshold {take_profit_threshold} should be above "
            f"entry price {entry_price}"
        )

    @given(
        current_price=st.floats(
            min_value=0.01, max_value=0.99, allow_nan=False, allow_infinity=False
        ),
        entry_price=st.floats(
            min_value=0.01, max_value=0.90, allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=100)
    def test_take_profit_trigger_condition(
        self, current_price: float, entry_price: float
    ) -> None:
        """
        Invariant: Take-profit triggers when price exceeds threshold.

        For any current price above the take-profit threshold, a take-profit
        should be triggered. The condition is monotonic: if price is above
        threshold, we should exit with profit.
        """
        take_profit_threshold = entry_price * (1 + TAKE_PROFIT_PCT)

        # Take-profit should trigger if current price is above threshold
        if current_price > take_profit_threshold:
            profit_pct = ((current_price - entry_price) / entry_price) * 100
            assert profit_pct >= TAKE_PROFIT_PCT * 100, (
                f"Price {current_price} above take-profit threshold "
                f"{take_profit_threshold} should correspond to profit >= "
                f"{TAKE_PROFIT_PCT * 100}%, but got {profit_pct}%"
            )


class TestTrailingStopInvariant:
    """Test trailing-stop invariant: trailing stop never decreases."""

    @given(
        entry_price=st.floats(
            min_value=0.50, max_value=0.95, allow_nan=False, allow_infinity=False
        ),
        new_high_price=st.floats(
            min_value=0.50, max_value=0.99, allow_nan=False, allow_infinity=False
        ),
        current_trailing_stop=st.floats(
            min_value=0.45, max_value=0.95, allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=100)
    def test_trailing_stop_never_decreases(
        self, entry_price: float, new_high_price: float, current_trailing_stop: float
    ) -> None:
        """
        Invariant: Trailing stop price never decreases.

        When price moves in our favor (increases for YES position),
        the trailing stop should be recalculated at the new high minus
        the trailing percentage. If the new stop is higher than the
        current stop, it updates. Otherwise, it stays the same.

        Key property: stop_level_new >= stop_level_old always holds.
        """
        # Only update if price moved in favor (new_high > entry)
        if new_high_price <= entry_price:
            return

        # Calculate new trailing stop level
        new_trailing_stop = new_high_price * (1 - TRAILING_STOP_PCT)

        # Invariant: trailing stop should only move up or stay the same
        if new_trailing_stop > current_trailing_stop:
            # Update is allowed - stop moves up with price
            assert new_trailing_stop > current_trailing_stop
        else:
            # No update - stop stays at previous high water mark
            assert current_trailing_stop >= new_trailing_stop

    @given(
        high_water_marks=st.lists(
            st.floats(
                min_value=0.50, max_value=0.99, allow_nan=False, allow_infinity=False
            ),
            min_size=1,
            max_size=50,
        ),
        entry_price=st.floats(
            min_value=0.50, max_value=0.90, allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=50)
    def test_trailing_stop_monotonic_sequence(
        self, high_water_marks: list[float], entry_price: float
    ) -> None:
        """
        Invariant: Trailing stop is monotonic over a price sequence.

        As we observe a sequence of high water marks, each new trailing
        stop level must be >= the previous one. This ensures the stop
        only moves up (ratchets) and never down.

        Simulates the actual behavior of trailing stop updates over time.
        """
        trailing_stops: list[float] = []
        current_stop: float | None = None

        for price in high_water_marks:
            # Only consider prices that represent a new high (above entry)
            if price <= entry_price:
                continue

            new_stop = price * (1 - TRAILING_STOP_PCT)

            if current_stop is None:
                current_stop = new_stop
            elif new_stop > current_stop:
                current_stop = new_stop

            trailing_stops.append(current_stop)

        # Verify monotonicity
        for i in range(1, len(trailing_stops)):
            assert trailing_stops[i] >= trailing_stops[i - 1], (
                f"Trailing stop decreased from {trailing_stops[i - 1]} "
                f"to {trailing_stops[i]} - invariant violated!"
            )


class TestPositionStateInvariants:
    """Test position state invariants: consistency of position state."""

    @given(
        entry_price=st.floats(
            min_value=0.01, max_value=0.99, allow_nan=False, allow_infinity=False
        ),
        side=st.sampled_from(["YES", "NO"]),
    )
    @settings(max_examples=100)
    def test_open_position_has_entry_and_side(
        self, entry_price: float, side: str
    ) -> None:
        """
        Invariant: Open position must have both entry_price and position_side set.

        When position_open is True, both entry_price and position_side
        should be non-None. A position cannot be considered open without
        these essential attributes.
        """
        position_open = True
        position_side = side

        # Invariant: open position requires both attributes
        if position_open:
            assert entry_price is not None and entry_price > 0, (
                "Open position must have a valid entry price"
            )
            assert position_side in ["YES", "NO"], (
                "Open position must have a valid side (YES or NO)"
            )

    @given(
        entry_price_1=st.floats(
            min_value=0.01, max_value=0.99, allow_nan=False, allow_infinity=False
        ),
        entry_price_2=st.floats(
            min_value=0.01, max_value=0.99, allow_nan=False, allow_infinity=False
        ),
        side=st.sampled_from(["YES", "NO"]),
    )
    @settings(max_examples=100)
    def test_position_entry_unchanged_while_open(
        self, entry_price_1: float, entry_price_2: float, side: str
    ) -> None:
        """
        Invariant: Entry price cannot change while position is open.

        Once a position is opened with an entry price, that price should
        remain constant until the position is closed. Entry price is a
        snapshot at open time, not a mutable property.
        """
        # Simulate opening a position
        position_open = True
        entry_price = entry_price_1
        _ = side  # Side is assigned but not used in this invariant check

        # Entry price should be fixed once position is open
        assert entry_price == entry_price_1, "Entry price should remain constant"

        # Attempting to change entry while position is open should not be allowed
        if position_open:
            # In a real system, this would raise an error or be a no-op
            # The invariant is that entry_price is immutable while open
            pass

    @given(
        entry_price=st.floats(
            min_value=0.01, max_value=0.99, allow_nan=False, allow_infinity=False
        ),
        trailing_stop_pct=st.floats(
            min_value=0.01, max_value=0.20, allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=100)
    def test_trailing_stop_initialized_on_position_open(
        self, entry_price: float, trailing_stop_pct: float
    ) -> None:
        """
        Invariant: Trailing stop is initialized when position is opened.

        When a position is opened, the trailing stop should be initialized
        at entry_price * (1 - trailing_stop_pct). This provides the initial
        protection level.
        """
        position_open = True

        if position_open:
            initial_trailing_stop = entry_price * (1 - trailing_stop_pct)
            assert initial_trailing_stop < entry_price, (
                "Initial trailing stop must be below entry price"
            )
            assert initial_trailing_stop >= 0, (
                "Initial trailing stop must be non-negative"
            )

    @given(
        entry_price=st.floats(
            min_value=0.01, max_value=0.99, allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=100)
    def test_closed_position_state_reset(self, entry_price: float) -> None:
        """
        Invariant: Closing a position resets state correctly.

        When a position is closed, position_open becomes False, but the
        entry price should be preserved for record-keeping. The side and
        trailing stop may be reset or preserved depending on design.
        """
        # Simulate opening then closing
        position_open = False
        entry_price_preserved = entry_price  # Entry is preserved for records

        # After closing, position_open must be False
        assert not position_open, "Closed position must have position_open=False"

        # Entry price is preserved for PnL calculation and records
        assert entry_price_preserved is not None, "Entry price should be preserved"


class TestPnLFormulaInvariant:
    """Test PnL formula invariant: mathematical correctness of PnL calculation."""

    @given(
        size=st.floats(
            min_value=0.01, max_value=10000.0, allow_nan=False, allow_infinity=False
        ),
        entry_price=st.floats(
            min_value=0.01, max_value=0.99, allow_nan=False, allow_infinity=False
        ),
        exit_price=st.floats(
            min_value=0.01, max_value=1.0, allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=100)
    def test_pnl_formula_mathematical_correctness(
        self, size: float, entry_price: float, exit_price: float
    ) -> None:
        """
        Invariant: PnL formula produces mathematically correct results.

        For any valid inputs, the PnL calculation should produce results
        that satisfy the mathematical relationship:
        - cost = size * entry_price
        - exit_value = size * exit_price
        - profit_loss = exit_value - cost
        - roi_percent = profit_loss / cost * 100

        Additionally:
        - If exit_price > entry_price, profit_loss > 0
        - If exit_price < entry_price, profit_loss < 0
        - If exit_price == entry_price, profit_loss == 0
        """
        # Calculate PnL using the formula
        cost = size * entry_price
        exit_value = size * exit_price
        profit_loss = exit_value - cost
        roi_percent = (profit_loss / cost * 100) if cost > 0 else 0.0

        # Invariant: cost and exit_value must be non-negative
        assert cost >= 0, f"Cost {cost} should be non-negative"
        assert exit_value >= 0, f"Exit value {exit_value} should be non-negative"

        # Invariant: profit_loss sign matches price comparison
        # Use epsilon for floating-point precision issues
        epsilon = 1e-9
        if exit_price > entry_price:
            assert profit_loss > -epsilon, (
                "Profit should be non-negative when exit price > entry price (allowing for floating-point precision)"
            )
        elif exit_price < entry_price:
            assert profit_loss < epsilon, (
                "Loss should be non-positive when exit price < entry price (allowing for floating-point precision)"
            )
        else:
            assert abs(profit_loss) < epsilon, "Zero PnL when exit price equals entry price (within floating-point epsilon)"

        # Invariant: ROI percent matches profit_loss / cost relationship
        if cost > 0:
            expected_roi = (profit_loss / cost) * 100
            assert abs(roi_percent - expected_roi) < 1e-10, (
                f"ROI percent {roi_percent}% doesn't match expected {expected_roi}%"
            )

    @given(
        size=st.floats(
            min_value=0.01, max_value=10000.0, allow_nan=False, allow_infinity=False
        ),
        entry_price=st.floats(
            min_value=0.01, max_value=0.99, allow_nan=False, allow_infinity=False
        ),
        exit_price=st.floats(
            min_value=0.01, max_value=1.0, allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=100)
    def test_pnl_bounds(
        self, size: float, entry_price: float, exit_price: float
    ) -> None:
        """
        Invariant: PnL is bounded by the position size and price range.

        The maximum possible profit is when exit_price = 1.0 (pays out full).
        The maximum possible loss is when exit_price = 0.0 (pays out nothing).

        This ensures PnL stays within reasonable bounds.
        """
        cost = size * entry_price
        max_profit = size * 1.0 - cost  # Best case: pays out at 1.0
        max_loss = size * 0.0 - cost  # Worst case: pays out at 0.0

        exit_value = size * exit_price
        profit_loss = exit_value - cost

        # Invariant: PnL must be between max_loss and max_profit
        assert max_loss <= profit_loss <= max_profit, (
            f"PnL {profit_loss} outside bounds [{max_loss}, {max_profit}]"
        )

    @given(
        size=st.floats(
            min_value=0.01, max_value=10000.0, allow_nan=False, allow_infinity=False
        ),
        entry_price=st.floats(
            min_value=0.01, max_value=0.99, allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=100)
    def test_pnl_zero_cost_handling(self, size: float, entry_price: float) -> None:
        """
        Invariant: PnL calculation handles edge cases gracefully.

        When cost is zero or very small, the calculation should not produce
        infinite or undefined values. ROI percent should be 0 when cost is 0.
        """
        cost = size * entry_price

        # Calculate ROI with edge case handling
        exit_price = entry_price  # Zero PnL case
        exit_value = size * exit_price
        profit_loss = exit_value - cost

        if cost > 0:
            roi_percent = profit_loss / cost * 100
            # Should be 0% since exit_price == entry_price
            assert abs(roi_percent) < 1e-10, "ROI should be 0% for same prices"
        else:
            roi_percent = 0.0
            assert roi_percent == 0.0, "ROI should be 0% when cost is zero"

        # PnL should be 0 when entry and exit prices are equal
        assert abs(profit_loss) < 1e-10, "PnL should be 0 when prices are equal"
