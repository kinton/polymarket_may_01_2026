"""
Test Oracle Guard integration with convergence strategy V2 (continuous eval).
"""

import pytest
from unittest.mock import AsyncMock

from src.clob_types import OrderBook
from src.oracle_tracker import OracleSnapshot
from src.trading.convergence_strategy import ConvergenceStrategy


def _skewed_orderbook() -> OrderBook:
    ob = OrderBook()
    ob.best_ask_yes = 0.85
    ob.best_bid_yes = 0.84
    ob.best_ask_no = 0.15
    ob.best_bid_no = 0.14
    ob.update()
    return ob


@pytest.mark.asyncio
async def test_convergence_blocked_when_oracle_not_converged(integration_trader):
    """No trade when oracle delta is too high."""
    integration_trader.convergence_strategy = ConvergenceStrategy(
        threshold_pct=0.0005, min_skew=0.80, max_cheap_price=0.40,
        window_start_s=180.0, window_end_s=20.0, min_observations=3,
    )
    integration_trader.oracle_guard.enabled = True
    integration_trader.oracle_guard.snapshot = OracleSnapshot(
        ts_ms=1000000, price=105.0, n_points=10,
        price_to_beat=100.0, delta=5.0, delta_pct=0.05,
        vol_pct=0.001, slope_usd_per_s=0.01, zscore=2.0,
    )
    integration_trader.orderbook = _skewed_orderbook()
    integration_trader._update_winning_side()
    integration_trader.execute_order = AsyncMock()

    for t in [170.0, 130.0, 90.0, 70.0, 50.0]:
        await integration_trader.check_trigger(time_remaining=t)
    integration_trader.execute_order.assert_not_called()


@pytest.mark.asyncio
async def test_convergence_fires_when_oracle_converged(integration_trader):
    """Trade fires when oracle converged and enough evidence accumulated."""
    integration_trader.convergence_strategy = ConvergenceStrategy(
        threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30,
        window_start_s=180.0, window_end_s=20.0, min_observations=3,
    )
    integration_trader.oracle_guard.enabled = True
    integration_trader.oracle_guard.snapshot = OracleSnapshot(
        ts_ms=1000000, price=100.0, n_points=10,
        price_to_beat=100.0, delta=0.0, delta_pct=0.0,
        vol_pct=0.001, slope_usd_per_s=0.0, zscore=0.0,
    )
    integration_trader.orderbook = _skewed_orderbook()
    integration_trader._update_winning_side()
    integration_trader.execute_order = AsyncMock()

    # Feed enough ticks for min_observations=3
    for t in [170.0, 130.0, 90.0]:
        await integration_trader.check_trigger(time_remaining=t)
    integration_trader.execute_order.assert_called_once()
    assert integration_trader._convergence_trade is True


@pytest.mark.asyncio
async def test_convergence_blocked_when_no_oracle_snapshot(integration_trader):
    """No trade when oracle snapshot is None."""
    integration_trader.convergence_strategy = ConvergenceStrategy(
        threshold_pct=0.0005, min_skew=0.80, max_cheap_price=0.40,
        window_start_s=180.0, window_end_s=20.0, min_observations=3,
    )
    integration_trader.oracle_guard.enabled = True
    integration_trader.oracle_guard.snapshot = None
    integration_trader.orderbook = _skewed_orderbook()
    integration_trader._update_winning_side()
    integration_trader.execute_order = AsyncMock()

    for t in [170.0, 130.0, 90.0, 70.0]:
        await integration_trader.check_trigger(time_remaining=t)
    integration_trader.execute_order.assert_not_called()
