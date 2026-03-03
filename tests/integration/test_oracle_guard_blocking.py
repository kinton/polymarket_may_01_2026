"""
Test Oracle Guard integration with convergence strategy.

With convergence-only mode, oracle_guard.quality_ok() is no longer called.
Convergence strategy uses oracle data (delta_pct, price_to_beat) directly.
These tests verify oracle data flows correctly to convergence decisions.
"""

import pytest
from dataclasses import replace
from unittest.mock import AsyncMock

from src.clob_types import OrderBook
from src.oracle_tracker import OracleTracker, OracleSnapshot
from src.trading.convergence_strategy import ConvergenceStrategy


def _skewed_orderbook() -> OrderBook:
    """Create a skewed orderbook suitable for convergence."""
    ob = OrderBook()
    ob.best_ask_yes = 0.85
    ob.best_bid_yes = 0.84
    ob.best_ask_no = 0.15
    ob.best_bid_no = 0.14
    ob.update()
    return ob


@pytest.mark.asyncio
async def test_convergence_blocked_when_oracle_not_converged(integration_trader):
    """No trade when oracle delta_pct is too high (not converged)."""
    integration_trader.convergence_strategy = ConvergenceStrategy(
        threshold_pct=0.0005, min_skew=0.80, max_cheap_price=0.40,
        window_start_s=60.0, window_end_s=20.0,
    )
    integration_trader.oracle_guard.enabled = True
    # delta_pct = 5% >> 0.05% threshold
    integration_trader.oracle_guard.snapshot = OracleSnapshot(
        ts_ms=1000000, price=105.0, n_points=10,
        price_to_beat=100.0, delta=5.0, delta_pct=0.05,
        vol_pct=0.001, slope_usd_per_s=0.01, zscore=2.0,
    )

    integration_trader.orderbook = _skewed_orderbook()
    integration_trader._update_winning_side()
    integration_trader.execute_order = AsyncMock()

    await integration_trader.check_trigger(time_remaining=40.0)
    integration_trader.execute_order.assert_not_called()


@pytest.mark.asyncio
async def test_convergence_fires_when_oracle_converged(integration_trader):
    """Trade fires when oracle delta_pct < threshold and direction aligns with cheap side."""
    integration_trader.convergence_strategy = ConvergenceStrategy(
        threshold_pct=0.0003, min_skew=0.75, max_cheap_price=0.30,
        window_start_s=60.0, window_end_s=20.0,
    )
    integration_trader.oracle_guard.enabled = True
    # Oracle slightly below beat → DOWN/NO favored, delta_pct = -0.02%
    integration_trader.oracle_guard.snapshot = OracleSnapshot(
        ts_ms=1000000, price=99.98, n_points=10,
        price_to_beat=100.0, delta=-0.02, delta_pct=-0.0002,
        vol_pct=0.001, slope_usd_per_s=-0.01, zscore=-0.5,
    )

    # Skewed: YES expensive, NO cheap — aligns with oracle DOWN direction
    integration_trader.orderbook = _skewed_orderbook()
    integration_trader._update_winning_side()
    integration_trader.execute_order = AsyncMock()

    await integration_trader.check_trigger(time_remaining=40.0)
    integration_trader.execute_order.assert_called_once()
    assert integration_trader._convergence_trade is True


@pytest.mark.asyncio
async def test_convergence_blocked_when_no_oracle_snapshot(integration_trader):
    """No trade when oracle snapshot is None."""
    integration_trader.convergence_strategy = ConvergenceStrategy(
        threshold_pct=0.0005, min_skew=0.80, max_cheap_price=0.40,
        window_start_s=60.0, window_end_s=20.0,
    )
    integration_trader.oracle_guard.enabled = True
    integration_trader.oracle_guard.snapshot = None

    integration_trader.orderbook = _skewed_orderbook()
    integration_trader._update_winning_side()
    integration_trader.execute_order = AsyncMock()

    await integration_trader.check_trigger(time_remaining=40.0)
    integration_trader.execute_order.assert_not_called()
