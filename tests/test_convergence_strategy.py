"""Tests for the Convergence Trading Strategy."""

import pytest

from src.trading.convergence_strategy import ConvergenceStrategy, ConvergenceSignal
from src.oracle_tracker import OracleSnapshot
from src.clob_types import OrderBook


def _make_snapshot(
    price: float = 85000.0,
    price_to_beat: float | None = 85000.0,
    delta: float | None = None,
    delta_pct: float | None = None,
    n_points: int = 10,
) -> OracleSnapshot:
    """Helper to create OracleSnapshot with auto-calculated delta/delta_pct."""
    if price_to_beat is not None and delta is None:
        delta = price - price_to_beat
    if price_to_beat is not None and delta_pct is None and delta is not None:
        delta_pct = delta / price_to_beat if price_to_beat else None
    return OracleSnapshot(
        ts_ms=1000000,
        price=price,
        n_points=n_points,
        price_to_beat=price_to_beat,
        delta=delta,
        delta_pct=delta_pct,
        vol_pct=0.001,
        slope_usd_per_s=0.0,
        zscore=0.0,
    )


def _make_orderbook(ask_yes: float = 0.12, ask_no: float = 0.88) -> OrderBook:
    """Helper to create OrderBook with asks."""
    ob = OrderBook(
        best_ask_yes=ask_yes,
        best_ask_no=ask_no,
        best_bid_yes=ask_yes - 0.01 if ask_yes else None,
        best_bid_no=ask_no - 0.01 if ask_no else None,
    )
    ob.update()
    return ob


class TestShouldEnter:
    """Test should_enter() with various conditions."""

    def test_all_conditions_met(self):
        cs = ConvergenceStrategy()
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.12, ask_no=0.88)
        assert cs.should_enter(30.0, snap, ob) is True

    def test_converged_within_threshold(self):
        """5 basis points = 0.05% → for BTC $85000, that's $42.50."""
        cs = ConvergenceStrategy(threshold_pct=0.0005)
        # delta = $40 → 40/85000 = 0.00047 < 0.0005 ✓
        snap = _make_snapshot(price=85040.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.10, ask_no=0.90)
        assert cs.should_enter(30.0, snap, ob) is True

    def test_no_oracle_snapshot(self):
        cs = ConvergenceStrategy()
        ob = _make_orderbook()
        assert cs.should_enter(30.0, None, ob) is False

    def test_no_price_to_beat(self):
        cs = ConvergenceStrategy()
        snap = _make_snapshot(price_to_beat=None, delta=None, delta_pct=None)
        ob = _make_orderbook()
        assert cs.should_enter(30.0, snap, ob) is False

    def test_no_convergence_delta_too_big(self):
        """Delta exceeds threshold → no entry."""
        cs = ConvergenceStrategy(threshold_pct=0.0005)
        # delta = $100 → 100/85000 = 0.00118 > 0.0005 ✗
        snap = _make_snapshot(price=85100.0, price_to_beat=85000.0)
        ob = _make_orderbook()
        assert cs.should_enter(30.0, snap, ob) is False

    def test_no_skew_prices_balanced(self):
        """Market shows ~50/50, no skew → no entry."""
        cs = ConvergenceStrategy(min_skew=0.80)
        snap = _make_snapshot()
        ob = _make_orderbook(ask_yes=0.55, ask_no=0.45)
        assert cs.should_enter(30.0, snap, ob) is False

    def test_time_too_early(self):
        """More than 60s before expiry → no entry."""
        cs = ConvergenceStrategy(window_start_s=60.0)
        snap = _make_snapshot()
        ob = _make_orderbook()
        assert cs.should_enter(120.0, snap, ob) is False

    def test_time_too_late(self):
        """Less than 20s before expiry → no entry."""
        cs = ConvergenceStrategy(window_end_s=20.0)
        snap = _make_snapshot()
        ob = _make_orderbook()
        assert cs.should_enter(10.0, snap, ob) is False

    def test_time_exactly_at_boundary_start(self):
        cs = ConvergenceStrategy(window_start_s=60.0)
        snap = _make_snapshot()
        ob = _make_orderbook()
        assert cs.should_enter(60.0, snap, ob) is True

    def test_time_exactly_at_boundary_end(self):
        cs = ConvergenceStrategy(window_end_s=20.0)
        snap = _make_snapshot()
        ob = _make_orderbook()
        assert cs.should_enter(20.0, snap, ob) is True

    def test_cheap_side_too_expensive(self):
        """Cheap side > 40¢ → no entry."""
        cs = ConvergenceStrategy(max_cheap_price=0.40)
        snap = _make_snapshot()
        ob = _make_orderbook(ask_yes=0.45, ask_no=0.85)
        assert cs.should_enter(30.0, snap, ob) is False

    def test_orderbook_missing_yes_ask(self):
        cs = ConvergenceStrategy()
        snap = _make_snapshot()
        ob = OrderBook(best_ask_yes=None, best_ask_no=0.88)
        assert cs.should_enter(30.0, snap, ob) is False

    def test_orderbook_missing_no_ask(self):
        cs = ConvergenceStrategy()
        snap = _make_snapshot()
        ob = OrderBook(best_ask_yes=0.12, best_ask_no=None)
        assert cs.should_enter(30.0, snap, ob) is False

    def test_negative_delta_converged(self):
        """Price below beat but still within threshold."""
        cs = ConvergenceStrategy(threshold_pct=0.0005)
        # delta = -$30 → 30/85000 = 0.000353 < 0.0005 ✓
        snap = _make_snapshot(price=84970.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        assert cs.should_enter(30.0, snap, ob) is True


class TestGetCheapSide:
    """Test get_cheap_side() logic."""

    def test_yes_cheaper(self):
        cs = ConvergenceStrategy()
        ob = _make_orderbook(ask_yes=0.10, ask_no=0.90)
        side, price = cs.get_cheap_side(ob)
        assert side == "YES"
        assert price == 0.10

    def test_no_cheaper(self):
        cs = ConvergenceStrategy()
        ob = _make_orderbook(ask_yes=0.88, ask_no=0.12)
        side, price = cs.get_cheap_side(ob)
        assert side == "NO"
        assert price == 0.12

    def test_equal_prices_returns_yes(self):
        """When equal, YES is preferred."""
        cs = ConvergenceStrategy()
        ob = _make_orderbook(ask_yes=0.50, ask_no=0.50)
        side, price = cs.get_cheap_side(ob)
        assert side == "YES"
        assert price == 0.50

    def test_raises_on_missing_orderbook(self):
        cs = ConvergenceStrategy()
        ob = OrderBook(best_ask_yes=None, best_ask_no=0.50)
        with pytest.raises(ValueError):
            cs.get_cheap_side(ob)


class TestGetSignal:
    """Test get_signal() returns full ConvergenceSignal."""

    def test_signal_when_conditions_met(self):
        cs = ConvergenceStrategy()
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.10, ask_no=0.90)
        signal = cs.get_signal(30.0, snap, ob)

        assert signal is not None
        assert isinstance(signal, ConvergenceSignal)
        assert signal.cheap_side == "YES"
        assert signal.cheap_price == 0.10
        assert signal.expensive_side == "NO"
        assert signal.expensive_price == 0.90
        assert signal.delta_pct == 0.0
        assert signal.oracle_price == 85000.0
        assert signal.price_to_beat == 85000.0
        assert signal.time_remaining == 30.0

    def test_signal_none_when_conditions_not_met(self):
        cs = ConvergenceStrategy()
        snap = _make_snapshot(price=86000.0, price_to_beat=85000.0)  # too far
        ob = _make_orderbook()
        signal = cs.get_signal(30.0, snap, ob)
        assert signal is None

    def test_signal_no_side_cheaper(self):
        """When NO side is cheaper, signal reflects that."""
        cs = ConvergenceStrategy()
        snap = _make_snapshot()
        ob = _make_orderbook(ask_yes=0.85, ask_no=0.15)
        signal = cs.get_signal(30.0, snap, ob)

        assert signal is not None
        assert signal.cheap_side == "NO"
        assert signal.cheap_price == 0.15
        assert signal.expensive_side == "YES"
        assert signal.expensive_price == 0.85


class TestEdgeCases:
    """Edge cases and integration-like scenarios."""

    def test_eth_convergence(self):
        """ETH at $2500, converged within 5bp."""
        cs = ConvergenceStrategy(threshold_pct=0.0005)
        # 5bp of $2500 = $1.25
        snap = _make_snapshot(price=2501.0, price_to_beat=2500.0)
        ob = _make_orderbook(ask_yes=0.08, ask_no=0.92)
        assert cs.should_enter(40.0, snap, ob) is True

    def test_btc_barely_outside_threshold(self):
        """BTC just outside 5bp threshold."""
        cs = ConvergenceStrategy(threshold_pct=0.0005)
        # 6bp → 0.0006 > 0.0005
        snap = _make_snapshot(price=85051.0, price_to_beat=85000.0)
        ob = _make_orderbook()
        assert cs.should_enter(30.0, snap, ob) is False

    def test_custom_parameters(self):
        """Custom strategy parameters work correctly."""
        cs = ConvergenceStrategy(
            threshold_pct=0.001,  # 10bp
            min_skew=0.70,
            max_cheap_price=0.50,
            window_start_s=120.0,
            window_end_s=10.0,
        )
        snap = _make_snapshot(price=85085.0, price_to_beat=85000.0)  # ~10bp
        ob = _make_orderbook(ask_yes=0.25, ask_no=0.75)
        assert cs.should_enter(90.0, snap, ob) is True

    def test_zero_delta_pct(self):
        """Exact convergence (delta_pct = 0)."""
        cs = ConvergenceStrategy()
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.05, ask_no=0.95)
        assert cs.should_enter(30.0, snap, ob) is True
        signal = cs.get_signal(30.0, snap, ob)
        assert signal is not None
        assert signal.delta_pct == 0.0
