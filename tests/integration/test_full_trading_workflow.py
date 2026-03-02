"""
Test full trading workflow with convergence strategy.

Verifies:
- Convergence trigger fires when oracle converges and market is skewed
- No trade when convergence conditions not met
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


@pytest.mark.asyncio
async def test_convergence_trigger(integration_trader):
    """
    Test convergence trigger fires when:
    - Oracle converged (delta_pct < 5bp)
    - Market skewed (one side >= 0.80)
    - Cheap side <= 0.40
    - Time in window (20-60s)
    """
    from src.trading.convergence_strategy import ConvergenceStrategy

    integration_trader.convergence_strategy = ConvergenceStrategy(
        threshold_pct=0.0005,
        min_skew=0.80,
        max_cheap_price=0.40,
        window_start_s=60.0,
        window_end_s=20.0,
    )
    integration_trader.oracle_guard.enabled = True
    integration_trader.oracle_guard.snapshot = _make_oracle_snapshot(
        price=100.05, price_to_beat=100.0, delta_pct=0.0003,
    )

    # Skewed orderbook: YES expensive, NO cheap
    ob = OrderBook()
    ob.best_ask_yes = 0.85
    ob.best_bid_yes = 0.84
    ob.best_ask_no = 0.15
    ob.best_bid_no = 0.14
    ob.update()
    integration_trader.orderbook = ob
    integration_trader._update_winning_side()

    # Mock execute_order
    integration_trader.execute_order = AsyncMock()

    await integration_trader.check_trigger(time_remaining=40.0)

    integration_trader.execute_order.assert_called_once()
    assert integration_trader._convergence_trade is True


@pytest.mark.asyncio
async def test_no_trigger_without_convergence(integration_trader):
    """
    Without convergence conditions, no trade should fire.
    """
    from src.trading.convergence_strategy import ConvergenceStrategy

    integration_trader.convergence_strategy = ConvergenceStrategy(
        threshold_pct=0.0005,
        min_skew=0.80,
        max_cheap_price=0.40,
        window_start_s=60.0,
        window_end_s=20.0,
    )
    integration_trader.oracle_guard.enabled = True
    # Oracle NOT converged (delta too high)
    integration_trader.oracle_guard.snapshot = _make_oracle_snapshot(
        price=105.0, price_to_beat=100.0, delta_pct=0.05,
    )

    ob = OrderBook()
    ob.best_ask_yes = 0.85
    ob.best_bid_yes = 0.84
    ob.best_ask_no = 0.15
    ob.best_bid_no = 0.14
    ob.update()
    integration_trader.orderbook = ob
    integration_trader._update_winning_side()

    integration_trader.execute_order = AsyncMock()

    await integration_trader.check_trigger(time_remaining=40.0)

    integration_trader.execute_order.assert_not_called()
    assert integration_trader._convergence_trade is False


@pytest.mark.asyncio
async def test_no_trigger_outside_time_window(integration_trader):
    """
    Even with convergence, no trade outside the time window.
    """
    from src.trading.convergence_strategy import ConvergenceStrategy

    integration_trader.convergence_strategy = ConvergenceStrategy(
        threshold_pct=0.0005,
        min_skew=0.80,
        max_cheap_price=0.40,
        window_start_s=60.0,
        window_end_s=20.0,
    )
    integration_trader.oracle_guard.enabled = True
    integration_trader.oracle_guard.snapshot = _make_oracle_snapshot(
        price=100.05, price_to_beat=100.0, delta_pct=0.0003,
    )

    ob = OrderBook()
    ob.best_ask_yes = 0.85
    ob.best_bid_yes = 0.84
    ob.best_ask_no = 0.15
    ob.best_bid_no = 0.14
    ob.update()
    integration_trader.orderbook = ob
    integration_trader._update_winning_side()

    integration_trader.execute_order = AsyncMock()

    # Too early (120s > 60s window)
    await integration_trader.check_trigger(time_remaining=120.0)
    integration_trader.execute_order.assert_not_called()

    # Too late (10s < 20s window)
    await integration_trader.check_trigger(time_remaining=10.0)
    integration_trader.execute_order.assert_not_called()
