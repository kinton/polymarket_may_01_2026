"""
Test full trading workflow with convergence strategy V2 (continuous evaluation).

Verifies:
- Triggers as soon as enough evidence accumulated (no fixed decision time)
- No trade when conditions not met (no convergence, side flip-flop, etc.)
- No trade outside time window
- No trade with insufficient observations
"""

from unittest.mock import AsyncMock, MagicMock
import time
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
async def test_convergence_triggers_when_evidence_sufficient(integration_trader):
    """
    Triggers as soon as min_observations reached and all conditions met.
    With min_observations=3, should trigger on the 3rd+ tick.
    """
    from strategies.convergence_v1 import ConvergenceV1

    strategy = ConvergenceV1(
        threshold_pct=0.0003,
        min_skew=0.75,
        max_cheap_price=0.30,
        window_start_s=180.0,
        window_end_s=20.0,
        min_observations=5,
        min_convergence_rate=0.30,
        min_side_consistency=0.70,
    )
    integration_trader.strategy_instance = strategy
    integration_trader.oracle_guard.enabled = True
    snap = _make_oracle_snapshot(price=100.0, price_to_beat=100.0, delta_pct=0.0)
    integration_trader.oracle_guard.snapshot = snap
    integration_trader.oracle_guard.last_update_ts = time.time()
    ob = _make_skewed_ob("NO", 0.20, 0.80)
    integration_trader.orderbook = ob
    integration_trader._update_winning_side()
    integration_trader.execute_order = AsyncMock()
    # Update the primary strategy slot to use our test strategy
    if integration_trader.strategies:
        integration_trader.strategies[0].strategy_instance = strategy
        integration_trader.strategies[0].order_execution.execute_order_for = AsyncMock()
    else:
        # No slots yet — create one from the trader's own attrs
        from src.hft_trader import StrategySlot
        slot = StrategySlot(
            strategy_instance=strategy,
            order_execution=integration_trader.order_execution,
            dry_run_sim=integration_trader.dry_run_sim,
            dry_run=integration_trader.dry_run,
            mode="test",
            strategy_name="convergence_v1",
            strategy_version="v1",
        )
        slot.order_execution.execute_order_for = AsyncMock()
        integration_trader.strategies.append(slot)

    # First 4 ticks: not enough observations yet
    for t in [170.0, 150.0, 130.0, 110.0]:
        await integration_trader.check_trigger(time_remaining=t)
    for slot in integration_trader.strategies:
        slot.order_execution.execute_order_for.assert_not_called()

    # 5th tick: should trigger (min_observations=5 met)
    await integration_trader.check_trigger(time_remaining=90.0)
    any_executed = any(slot.order_execution.execute_order_for.called for slot in integration_trader.strategies)
    assert any_executed, "Expected at least one strategy slot to execute"
    assert integration_trader._strategy_trade is True


@pytest.mark.asyncio
async def test_no_trigger_without_convergence(integration_trader):
    """Without convergence (delta too high), no trade."""
    from strategies.convergence_v1 import ConvergenceV1

    strategy = ConvergenceV1(
        threshold_pct=0.0003,
        min_skew=0.75,
        max_cheap_price=0.30,
        min_observations=3,
    )
    integration_trader.strategy_instance = strategy
    integration_trader.oracle_guard.enabled = True
    snap = _make_oracle_snapshot(price=105.0, price_to_beat=100.0, delta_pct=0.05)
    integration_trader.oracle_guard.snapshot = snap
    ob = _make_skewed_ob("NO", 0.15, 0.85)
    integration_trader.orderbook = ob
    integration_trader._update_winning_side()
    integration_trader.execute_order = AsyncMock()

    for t in [170.0, 130.0, 90.0, 70.0, 50.0]:
        await integration_trader.check_trigger(time_remaining=t)
    integration_trader.execute_order.assert_not_called()


@pytest.mark.asyncio
async def test_no_trigger_outside_time_window(integration_trader):
    """No accumulation outside observation window."""
    from strategies.convergence_v1 import ConvergenceV1

    strategy = ConvergenceV1(
        threshold_pct=0.0003,
        min_skew=0.75,
        max_cheap_price=0.30,
        min_observations=3,
    )
    integration_trader.strategy_instance = strategy
    integration_trader.oracle_guard.enabled = True
    snap = _make_oracle_snapshot(price=100.0, price_to_beat=100.0, delta_pct=0.0)
    integration_trader.oracle_guard.snapshot = snap
    ob = _make_skewed_ob("NO", 0.20, 0.80)
    integration_trader.orderbook = ob
    integration_trader._update_winning_side()
    integration_trader.execute_order = AsyncMock()

    # Way too early — outside 180s window
    await integration_trader.check_trigger(time_remaining=300.0)
    integration_trader.execute_order.assert_not_called()

    # Too late — below 20s
    await integration_trader.check_trigger(time_remaining=10.0)
    integration_trader.execute_order.assert_not_called()


@pytest.mark.asyncio
async def test_no_trigger_insufficient_observations(integration_trader):
    """Too few observations → skip."""
    from strategies.convergence_v1 import ConvergenceV1

    strategy = ConvergenceV1(
        threshold_pct=0.0003,
        min_skew=0.75,
        max_cheap_price=0.30,
        min_observations=10,  # need 10 but only get 2
    )
    integration_trader.strategy_instance = strategy
    integration_trader.oracle_guard.enabled = True
    snap = _make_oracle_snapshot(price=100.0, price_to_beat=100.0, delta_pct=0.0)
    integration_trader.oracle_guard.snapshot = snap
    ob = _make_skewed_ob("NO", 0.20, 0.80)
    integration_trader.orderbook = ob
    integration_trader._update_winning_side()
    integration_trader.execute_order = AsyncMock()

    # Only 2 ticks
    await integration_trader.check_trigger(time_remaining=170.0)
    await integration_trader.check_trigger(time_remaining=150.0)
    integration_trader.execute_order.assert_not_called()


@pytest.mark.asyncio
async def test_no_trigger_side_inconsistency(integration_trader):
    """Cheap side flip-flops → no trigger."""
    from strategies.convergence_v1 import ConvergenceV1

    strategy = ConvergenceV1(
        threshold_pct=0.0003,
        min_skew=0.75,
        max_cheap_price=0.30,
        min_observations=3,
        min_side_consistency=0.70,
    )
    integration_trader.strategy_instance = strategy
    integration_trader.oracle_guard.enabled = True
    snap = _make_oracle_snapshot(price=100.0, price_to_beat=100.0, delta_pct=0.0)
    integration_trader.oracle_guard.snapshot = snap
    integration_trader.execute_order = AsyncMock()

    ob_no = _make_skewed_ob("NO", 0.20, 0.80)
    ob_yes = _make_skewed_ob("YES", 0.20, 0.80)

    for t, ob in [(170.0, ob_no), (150.0, ob_yes), (130.0, ob_no), (110.0, ob_yes), (90.0, ob_no), (70.0, ob_yes)]:
        integration_trader.orderbook = ob
        integration_trader._update_winning_side()
        await integration_trader.check_trigger(time_remaining=t)
    integration_trader.execute_order.assert_not_called()
