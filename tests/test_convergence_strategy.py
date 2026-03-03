"""Tests for the Convergence Trading Strategy (direction-aware v2)."""

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


def _make_orderbook(
    ask_yes: float = 0.10,
    ask_no: float = 0.90,
) -> OrderBook:
    ob = OrderBook()
    ob.best_ask_yes = ask_yes
    ob.best_bid_yes = ask_yes - 0.01
    ob.best_ask_no = ask_no
    ob.best_bid_no = ask_no - 0.01
    ob.update()
    return ob


class TestDirectionAwareness:
    """Core test: strategy respects oracle direction."""

    def test_oracle_down_buys_no(self):
        """Oracle below beat → DOWN → buy NO (cheap side must be NO)."""
        cs = ConvergenceStrategy(threshold_pct=0.0003, min_skew=0.75, max_cheap_price=0.30)
        # price below beat → delta_pct negative → DOWN/NO
        snap = _make_snapshot(price=84980.0, price_to_beat=85000.0)  # -0.0235%
        ob = _make_orderbook(ask_yes=0.80, ask_no=0.20)  # NO is cheap
        signal = cs.get_signal(30.0, snap, ob)
        assert signal is not None
        assert signal.side == "NO"
        assert signal.side_label == "DOWN"
        assert signal.direction == "oracle_down"

    def test_oracle_up_buys_yes(self):
        """Oracle above beat → UP → buy YES (cheap side must be YES)."""
        cs = ConvergenceStrategy(threshold_pct=0.0003, min_skew=0.75, max_cheap_price=0.30)
        # price above beat → delta_pct positive → UP/YES
        snap = _make_snapshot(price=85020.0, price_to_beat=85000.0)  # +0.0235%
        ob = _make_orderbook(ask_yes=0.20, ask_no=0.80)  # YES is cheap
        signal = cs.get_signal(30.0, snap, ob)
        assert signal is not None
        assert signal.side == "YES"
        assert signal.side_label == "UP"
        assert signal.direction == "oracle_up"

    def test_oracle_up_but_yes_expensive_no_trade(self):
        """Oracle says UP but YES is expensive → no trade (don't buy against direction)."""
        cs = ConvergenceStrategy(threshold_pct=0.0003, min_skew=0.75, max_cheap_price=0.30)
        snap = _make_snapshot(price=85020.0, price_to_beat=85000.0)  # UP
        ob = _make_orderbook(ask_yes=0.80, ask_no=0.20)  # YES expensive, NO cheap
        signal = cs.get_signal(30.0, snap, ob)
        # Oracle says buy YES but YES is $0.80 > max_cheap_price → no trade
        assert signal is None

    def test_oracle_down_but_no_expensive_no_trade(self):
        """Oracle says DOWN but NO is expensive → no trade."""
        cs = ConvergenceStrategy(threshold_pct=0.0003, min_skew=0.75, max_cheap_price=0.30)
        snap = _make_snapshot(price=84980.0, price_to_beat=85000.0)  # DOWN
        ob = _make_orderbook(ask_yes=0.20, ask_no=0.80)  # NO expensive
        signal = cs.get_signal(30.0, snap, ob)
        assert signal is None

    def test_neutral_oracle_buys_cheap_side(self):
        """Oracle exactly at beat → neutral → buy cheaper side."""
        cs = ConvergenceStrategy(threshold_pct=0.0003, min_skew=0.75, max_cheap_price=0.30)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)  # exact
        ob = _make_orderbook(ask_yes=0.15, ask_no=0.85)  # YES cheap
        signal = cs.get_signal(30.0, snap, ob)
        assert signal is not None
        assert signal.side == "YES"
        assert signal.direction == "neutral"


class TestShouldEnter:
    """Test entry condition checks."""

    def test_all_conditions_met(self):
        """Entry when all conditions are met with oracle direction alignment."""
        cs = ConvergenceStrategy(threshold_pct=0.0003, min_skew=0.75, max_cheap_price=0.30)
        snap = _make_snapshot(price=84985.0, price_to_beat=85000.0)  # -0.018% < 0.03%
        ob = _make_orderbook(ask_yes=0.80, ask_no=0.20)  # oracle DOWN, NO cheap ✓
        assert cs.should_enter(30.0, snap, ob) is True

    def test_delta_too_large(self):
        """Reject when delta exceeds threshold."""
        cs = ConvergenceStrategy(threshold_pct=0.0003)
        snap = _make_snapshot(price=86000.0, price_to_beat=85000.0)  # 1.17% >> threshold
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
        """Reject when expensive side < min_skew."""
        cs = ConvergenceStrategy(min_skew=0.75)
        snap = _make_snapshot(price=84985.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.60, ask_no=0.40)  # not skewed enough
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
        """Reject when oracle-favored side > max_cheap_price."""
        cs = ConvergenceStrategy(max_cheap_price=0.30)
        snap = _make_snapshot(price=84985.0, price_to_beat=85000.0)  # DOWN
        ob = _make_orderbook(ask_yes=0.60, ask_no=0.40)  # NO=0.40 > 0.30
        assert cs.should_enter(30.0, snap, ob) is False

    def test_missing_orderbook_asks(self):
        cs = ConvergenceStrategy()
        snap = _make_snapshot()
        ob = OrderBook(best_ask_yes=None, best_ask_no=0.50)
        assert cs.should_enter(30.0, snap, ob) is False


class TestGetCheapSide:
    """Legacy get_cheap_side() still works."""

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

    def test_raises_on_missing_orderbook(self):
        cs = ConvergenceStrategy()
        ob = OrderBook(best_ask_yes=None, best_ask_no=0.50)
        with pytest.raises(ValueError):
            cs.get_cheap_side(ob)


class TestGetSignal:
    """Test get_signal() returns full ConvergenceSignal."""

    def test_signal_direction_aligned(self):
        cs = ConvergenceStrategy(threshold_pct=0.0003, min_skew=0.75, max_cheap_price=0.30)
        snap = _make_snapshot(price=84985.0, price_to_beat=85000.0)  # DOWN
        ob = _make_orderbook(ask_yes=0.80, ask_no=0.20)  # NO cheap = aligned
        signal = cs.get_signal(30.0, snap, ob)
        assert signal is not None
        assert isinstance(signal, ConvergenceSignal)
        assert signal.side == "NO"
        assert signal.price == 0.20
        assert signal.expensive_price == 0.80
        assert signal.direction == "oracle_down"

    def test_signal_none_when_misaligned(self):
        cs = ConvergenceStrategy(threshold_pct=0.0003, min_skew=0.75, max_cheap_price=0.30)
        snap = _make_snapshot(price=85015.0, price_to_beat=85000.0)  # UP
        ob = _make_orderbook(ask_yes=0.80, ask_no=0.20)  # YES expensive
        signal = cs.get_signal(30.0, snap, ob)
        assert signal is None


class TestEdgeCases:
    """Edge cases and integration-like scenarios."""

    def test_eth_convergence_direction_aligned(self):
        """ETH below beat, NO side cheap."""
        cs = ConvergenceStrategy(threshold_pct=0.0003, min_skew=0.75, max_cheap_price=0.30)
        snap = _make_snapshot(price=2499.5, price_to_beat=2500.0)  # -0.02% DOWN
        ob = _make_orderbook(ask_yes=0.78, ask_no=0.22)
        assert cs.should_enter(40.0, snap, ob) is True

    def test_zero_delta_neutral(self):
        """Exact convergence → neutral → buy cheap side."""
        cs = ConvergenceStrategy(threshold_pct=0.0003, min_skew=0.75, max_cheap_price=0.30)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.05, ask_no=0.95)
        signal = cs.get_signal(30.0, snap, ob)
        assert signal is not None
        assert signal.direction == "neutral"
        assert signal.side == "YES"
        assert signal.price == 0.05

    def test_custom_parameters(self):
        cs = ConvergenceStrategy(
            threshold_pct=0.001, min_skew=0.70, max_cheap_price=0.35,
            window_start_s=120.0, window_end_s=10.0,
        )
        # UP direction, YES cheap
        snap = _make_snapshot(price=85040.0, price_to_beat=85000.0)  # +0.047%
        ob = _make_orderbook(ask_yes=0.25, ask_no=0.75)
        assert cs.should_enter(90.0, snap, ob) is True
