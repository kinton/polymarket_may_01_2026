"""Tests for the Convergence Trading Strategy (v2 — buy cheap side when oracle ≈ beat)."""

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
    if price_to_beat is not None and delta is None:
        delta = price - price_to_beat
    if price_to_beat is not None and delta_pct is None and delta is not None:
        delta_pct = delta / price_to_beat if price_to_beat else None
    return OracleSnapshot(
        ts_ms=1000000, price=price, n_points=n_points,
        price_to_beat=price_to_beat, delta=delta, delta_pct=delta_pct,
        vol_pct=0.001, slope_usd_per_s=0.0, zscore=0.0,
    )


def _make_orderbook(ask_yes: float = 0.10, ask_no: float = 0.90) -> OrderBook:
    ob = OrderBook()
    ob.best_ask_yes = ask_yes
    ob.best_bid_yes = ask_yes - 0.01
    ob.best_ask_no = ask_no
    ob.best_bid_no = ask_no - 0.01
    ob.update()
    return ob


class TestCheapSideBuying:
    """Core: buys the cheap side when oracle is at beat."""

    def test_buys_cheap_yes(self):
        """YES cheap, NO expensive → buy YES."""
        cs = ConvergenceStrategy(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)  # exact convergence
        ob = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        signal = cs.get_signal(30.0, snap, ob)
        assert signal is not None
        assert signal.side == "YES"
        assert signal.price == 0.15

    def test_buys_cheap_no(self):
        """NO cheap, YES expensive → buy NO."""
        cs = ConvergenceStrategy(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.80, ask_no=0.20)
        signal = cs.get_signal(30.0, snap, ob)
        assert signal is not None
        assert signal.side == "NO"
        assert signal.price == 0.20

    def test_slightly_off_beat_still_converged(self):
        """Oracle 1bp off beat → still converged."""
        cs = ConvergenceStrategy(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30)
        # 1bp = 0.0001 < 0.0002 threshold
        snap = _make_snapshot(price=85008.5, price_to_beat=85000.0)  # +0.01%
        ob = _make_orderbook(ask_yes=0.20, ask_no=0.80)
        assert cs.should_enter(30.0, snap, ob) is True


class TestShouldEnter:
    """Entry condition checks."""

    def test_all_conditions_met(self):
        cs = ConvergenceStrategy(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.10, ask_no=0.90)
        assert cs.should_enter(30.0, snap, ob) is True

    def test_delta_too_large(self):
        """Not converged — delta too big."""
        cs = ConvergenceStrategy(threshold_pct=0.0002)
        snap = _make_snapshot(price=86000.0, price_to_beat=85000.0)  # 1.17%
        ob = _make_orderbook()
        assert cs.should_enter(30.0, snap, ob) is False

    def test_no_oracle_data(self):
        cs = ConvergenceStrategy()
        ob = _make_orderbook()
        assert cs.should_enter(30.0, None, ob) is False

    def test_no_price_to_beat(self):
        cs = ConvergenceStrategy()
        snap = _make_snapshot(price_to_beat=None)
        ob = _make_orderbook()
        assert cs.should_enter(30.0, snap, ob) is False

    def test_insufficient_skew(self):
        cs = ConvergenceStrategy(min_skew=0.75)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.40, ask_no=0.60)  # not skewed
        assert cs.should_enter(30.0, snap, ob) is False

    def test_time_before_window(self):
        cs = ConvergenceStrategy(window_start_s=60.0)
        snap = _make_snapshot()
        ob = _make_orderbook()
        assert cs.should_enter(90.0, snap, ob) is False

    def test_time_after_window(self):
        cs = ConvergenceStrategy(window_end_s=20.0)
        snap = _make_snapshot()
        ob = _make_orderbook()
        assert cs.should_enter(10.0, snap, ob) is False

    def test_cheap_price_too_high(self):
        cs = ConvergenceStrategy(max_cheap_price=0.30)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.40, ask_no=0.80)  # cheap=0.40 > 0.30
        assert cs.should_enter(30.0, snap, ob) is False

    def test_missing_orderbook_asks(self):
        cs = ConvergenceStrategy()
        snap = _make_snapshot()
        ob = OrderBook(best_ask_yes=None, best_ask_no=0.50)
        assert cs.should_enter(30.0, snap, ob) is False


class TestGetCheapSide:
    def test_yes_cheaper(self):
        cs = ConvergenceStrategy()
        ob = _make_orderbook(ask_yes=0.10, ask_no=0.90)
        side, price = cs.get_cheap_side(ob)
        assert side == "YES" and price == 0.10

    def test_no_cheaper(self):
        cs = ConvergenceStrategy()
        ob = _make_orderbook(ask_yes=0.88, ask_no=0.12)
        side, price = cs.get_cheap_side(ob)
        assert side == "NO" and price == 0.12

    def test_raises_on_missing(self):
        cs = ConvergenceStrategy()
        ob = OrderBook(best_ask_yes=None, best_ask_no=0.50)
        with pytest.raises(ValueError):
            cs.get_cheap_side(ob)


class TestGetSignal:
    def test_signal_fields(self):
        cs = ConvergenceStrategy(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.10, ask_no=0.90)
        signal = cs.get_signal(30.0, snap, ob)
        assert signal is not None
        assert isinstance(signal, ConvergenceSignal)
        assert signal.side == "YES"
        assert signal.price == 0.10
        assert signal.expensive_price == 0.90
        assert signal.delta_pct == 0.0
        assert signal.time_remaining == 30.0

    def test_signal_none_when_not_converged(self):
        cs = ConvergenceStrategy(threshold_pct=0.0002)
        snap = _make_snapshot(price=86000.0, price_to_beat=85000.0)
        ob = _make_orderbook()
        assert cs.get_signal(30.0, snap, ob) is None


class TestEdgeCases:
    def test_zero_delta(self):
        cs = ConvergenceStrategy(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.05, ask_no=0.95)
        signal = cs.get_signal(30.0, snap, ob)
        assert signal is not None
        assert signal.delta_pct == 0.0

    def test_custom_parameters(self):
        cs = ConvergenceStrategy(
            threshold_pct=0.001, min_skew=0.70, max_cheap_price=0.35,
            window_start_s=120.0, window_end_s=10.0,
        )
        snap = _make_snapshot(price=85040.0, price_to_beat=85000.0)  # 0.047%
        ob = _make_orderbook(ask_yes=0.25, ask_no=0.75)
        assert cs.should_enter(90.0, snap, ob) is True

    def test_negative_delta_converged(self):
        """Price slightly below beat, still within threshold."""
        cs = ConvergenceStrategy(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30)
        snap = _make_snapshot(price=84985.0, price_to_beat=85000.0)  # -0.018%
        ob = _make_orderbook(ask_yes=0.80, ask_no=0.20)
        assert cs.should_enter(30.0, snap, ob) is True
