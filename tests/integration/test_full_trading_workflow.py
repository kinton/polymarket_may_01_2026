"""
Test full trading workflow with convergence strategy V2 (accumulate-then-decide).

Verifies:
- Observation phase collects data, does NOT trigger
- Decision at t=8s fires when accumulated data is strong
- No trade when conditions not met (no convergence, side flip-flop, etc.)
- No trade outside time window
"""

from unittest.mock import AsyncMock, MagicMock
import pytest

from src.clob_types import OrderBook
from src.oracle_tracker import OracleSnapshot


def _make_oracle_snapshot(
    price: float = 100.0,
    price_to_beat: float = 100.0,
    delta_pct: float = 0.0001,
) -> OracleSnapshot:
    delta = price - price_to_beat
    return OracleSnapshot(
        ts_ms=1000000,
        price=price,
        n_points=10,
        price_to_beat=price_to_beat,
        delta=delta,
        delta_pct=delta_pct,
        vol_pct=0.001,
        slope_usd_per_s=0.01,
        zscore=0.5,
    )


def _make_skewed_ob(cheap_side: str = "NO", cheap_price: float = 0.20, expensive_price: float = 0.80) -> OrderBook:
    """Make a skewed orderbook."""
    ob = OrderBook()
    if cheap_side == "NO":
        ob.best_ask_yes = expensive_price
        ob.best_bid_yes = expensive_price - 0.01
        ob.best_ask_no = cheap_price
        ob.best_bid_no = cheap_price - 0.01
    else:
        ob.best_ask_yes = cheap_price
        ob.best_bid_yes = cheap_price - 0.01
        ob.best_ask_no = expensive_price
        ob.best_bid_no = expensive_price - 0.01
    ob.update()
    return ob


@pytest.mark.asyncio
async def test_convergence_accumulate_then_decide(integration_trader):
    """
    V2: Accumulate observations during window, then decide at t=8s.
    
    Simulate multiple ticks during observation, then one at decision time.
    """
    from src.trading.convergence_strategy import ConvergenceStrategy

    strategy = ConvergenceStrategy(
        threshold_pct=0.0003,
        min_skew=0.75,
        max_cheap_price=0.30,
        window_start_s=60.0,
        window_end_s=20.0,
        min_observations=3,
        min_convergence_rate=0.30,
        min_side_consistency=0.70,
        decision_time_s=8.0,
    )
    integration_trader.convergence_strategy = strategy
    integration_trader.oracle_guard.enabled = True

    # Oracle converged
    snap = _make_oracle_snapshot(price=100.0, price_to_beat=100.0, delta_pct=0.0)
    integration_trader.oracle_guard.snapshot = snap

    # Skewed orderbook: YES expensive, NO cheap
    ob = _make_skewed_ob("NO", 0.20, 0.80)
    integration_trader.orderbook = ob
    integration_trader._update_winning_side()
    integration_trader.execute_order = AsyncMock()

    # Phase 1: Observation ticks (should NOT trigger)
    for t in [50.0, 45.0, 40.0, 35.0, 30.0, 25.0]:
        await integration_trader.check_trigger(time_remaining=t)
    integration_trader.execute_order.assert_not_called()

    # Phase 2: Decision time (t=8s)
    await integration_trader.check_trigger(time_remaining=8.0)
    integration_trader.execute_order.assert_called_once()
    assert integration_trader._convergence_trade is True


@pytest.mark.asyncio
async def test_no_trigger_without_convergence(integration_trader):
    """
    Without convergence (delta too high), no trade even after accumulation.
    """
    from src.trading.convergence_strategy import ConvergenceStrategy

    strategy = ConvergenceStrategy(
        threshold_pct=0.0003,
        min_skew=0.75,
        max_cheap_price=0.30,
        min_observations=3,
        decision_time_s=8.0,
    )
    integration_trader.convergence_strategy = strategy
    integration_trader.oracle_guard.enabled = True

    # Oracle NOT converged (delta way too high)
    snap = _make_oracle_snapshot(price=105.0, price_to_beat=100.0, delta_pct=0.05)
    integration_trader.oracle_guard.snapshot = snap

    ob = _make_skewed_ob("NO", 0.15, 0.85)
    integration_trader.orderbook = ob
    integration_trader._update_winning_side()
    integration_trader.execute_order = AsyncMock()

    # Observe
    for t in [50.0, 40.0, 30.0]:
        await integration_trader.check_trigger(time_remaining=t)

    # Decide
    await integration_trader.check_trigger(time_remaining=8.0)
    integration_trader.execute_order.assert_not_called()


@pytest.mark.asyncio
async def test_no_trigger_outside_time_window(integration_trader):
    """
    Even with convergence, no trade outside the observation/decision window.
    """
    from src.trading.convergence_strategy import ConvergenceStrategy

    strategy = ConvergenceStrategy(
        threshold_pct=0.0003,
        min_skew=0.75,
        max_cheap_price=0.30,
        min_observations=3,
        decision_time_s=8.0,
    )
    integration_trader.convergence_strategy = strategy
    integration_trader.oracle_guard.enabled = True
    snap = _make_oracle_snapshot(price=100.0, price_to_beat=100.0, delta_pct=0.0)
    integration_trader.oracle_guard.snapshot = snap

    ob = _make_skewed_ob("NO", 0.20, 0.80)
    integration_trader.orderbook = ob
    integration_trader._update_winning_side()
    integration_trader.execute_order = AsyncMock()

    # Too early — outside window
    await integration_trader.check_trigger(time_remaining=120.0)
    integration_trader.execute_order.assert_not_called()

    # Too late — no observations were collected
    await integration_trader.check_trigger(time_remaining=5.0)
    integration_trader.execute_order.assert_not_called()


@pytest.mark.asyncio
async def test_no_trigger_insufficient_observations(integration_trader):
    """
    Too few observations → skip.
    """
    from src.trading.convergence_strategy import ConvergenceStrategy

    strategy = ConvergenceStrategy(
        threshold_pct=0.0003,
        min_skew=0.75,
        max_cheap_price=0.30,
        min_observations=10,  # need 10 but only get 2
        decision_time_s=8.0,
    )
    integration_trader.convergence_strategy = strategy
    integration_trader.oracle_guard.enabled = True
    snap = _make_oracle_snapshot(price=100.0, price_to_beat=100.0, delta_pct=0.0)
    integration_trader.oracle_guard.snapshot = snap

    ob = _make_skewed_ob("NO", 0.20, 0.80)
    integration_trader.orderbook = ob
    integration_trader._update_winning_side()
    integration_trader.execute_order = AsyncMock()

    # Only 2 observations
    await integration_trader.check_trigger(time_remaining=50.0)
    await integration_trader.check_trigger(time_remaining=40.0)

    # Decide — not enough obs
    await integration_trader.check_trigger(time_remaining=8.0)
    integration_trader.execute_order.assert_not_called()


@pytest.mark.asyncio
async def test_no_trigger_side_inconsistency(integration_trader):
    """
    Cheap side flip-flops between YES and NO → skip (no confidence).
    """
    from src.trading.convergence_strategy import ConvergenceStrategy

    strategy = ConvergenceStrategy(
        threshold_pct=0.0003,
        min_skew=0.75,
        max_cheap_price=0.30,
        min_observations=3,
        min_side_consistency=0.70,
        decision_time_s=8.0,
    )
    integration_trader.convergence_strategy = strategy
    integration_trader.oracle_guard.enabled = True
    snap = _make_oracle_snapshot(price=100.0, price_to_beat=100.0, delta_pct=0.0)
    integration_trader.oracle_guard.snapshot = snap
    integration_trader.execute_order = AsyncMock()

    # Alternate cheap sides
    ob_no = _make_skewed_ob("NO", 0.20, 0.80)
    ob_yes = _make_skewed_ob("YES", 0.20, 0.80)

    for t, ob in [(50.0, ob_no), (45.0, ob_yes), (40.0, ob_no), (35.0, ob_yes), (30.0, ob_no), (25.0, ob_yes)]:
        integration_trader.orderbook = ob
        integration_trader._update_winning_side()
        await integration_trader.check_trigger(time_remaining=t)

    # Decide — sides flip 50/50
    integration_trader.orderbook = ob_no
    integration_trader._update_winning_side()
    await integration_trader.check_trigger(time_remaining=8.0)
    integration_trader.execute_order.assert_not_called()
