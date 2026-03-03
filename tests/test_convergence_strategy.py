"""Tests for the Convergence Trading Strategy V2 (accumulate-then-decide)."""

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


def _accumulate_and_decide(
    cs: ConvergenceStrategy,
    snap: OracleSnapshot,
    ob: OrderBook,
    obs_times: list[float] | None = None,
    decision_time: float = 8.0,
) -> ConvergenceSignal | None:
    """Helper: feed observations then call decide."""
    if obs_times is None:
        obs_times = [50.0, 45.0, 40.0, 35.0, 30.0, 25.0]
    for t in obs_times:
        cs.observe(t, snap, ob)
        cs._total_ticks += 1  # simulate tick count (observe only increments inside window)
    # Fix: observe already increments _total_ticks, undo our extra
    cs._total_ticks = cs._total_ticks // 2  # each observe call already counted
    # Actually let's just reset and do it properly
    cs.reset()
    for t in obs_times:
        cs.observe(t, snap, ob)
    return cs.decide(decision_time, snap, ob)


def _full_cycle(
    cs: ConvergenceStrategy,
    snap: OracleSnapshot,
    ob: OrderBook,
    obs_times: list[float] | None = None,
    decision_time: float = 8.0,
) -> ConvergenceSignal | None:
    """Helper: simulate full cycle using get_signal (legacy interface)."""
    cs.reset()
    if obs_times is None:
        obs_times = [50.0, 45.0, 40.0, 35.0, 30.0]
    decision_t = decision_time if decision_time is not None else cs.decision_time_s
    for t in obs_times:
        if t > decision_t:  # only observe above decision time
            result = cs.get_signal(t, snap, ob)
            assert result is None, f"Should not trigger during observation at t={t}"
        else:
            cs.observe(t, snap, ob)
    return cs.get_signal(decision_t, snap, ob)


class TestCheapSideBuying:
    """Core: buys the cheap side when oracle is at beat."""

    def test_buys_cheap_yes(self):
        """YES cheap, NO expensive → buy YES."""
        cs = ConvergenceStrategy(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30,
                                 min_observations=3)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        signal = _full_cycle(cs, snap, ob)
        assert signal is not None
        assert signal.side == "YES"
        assert signal.price == 0.15

    def test_buys_cheap_no(self):
        """NO cheap, YES expensive → buy NO."""
        cs = ConvergenceStrategy(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30,
                                 min_observations=3)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.80, ask_no=0.20)
        signal = _full_cycle(cs, snap, ob)
        assert signal is not None
        assert signal.side == "NO"
        assert signal.price == 0.20

    def test_slightly_off_beat_still_converged(self):
        """Oracle 1bp off beat → still converged."""
        cs = ConvergenceStrategy(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30,
                                 min_observations=3)
        snap = _make_snapshot(price=85008.5, price_to_beat=85000.0)  # +0.01%
        ob = _make_orderbook(ask_yes=0.20, ask_no=0.80)
        signal = _full_cycle(cs, snap, ob)
        assert signal is not None


class TestNoTriggerConditions:
    """Conditions where strategy should NOT trigger."""

    def test_delta_too_large(self):
        cs = ConvergenceStrategy(threshold_pct=0.0002, min_observations=3)
        snap = _make_snapshot(price=86000.0, price_to_beat=85000.0)  # 1.17%
        ob = _make_orderbook()
        signal = _full_cycle(cs, snap, ob)
        assert signal is None

    def test_no_oracle_data(self):
        cs = ConvergenceStrategy(min_observations=1)
        ob = _make_orderbook()
        cs.reset()
        cs.observe(30.0, None, ob)
        signal = cs.decide(8.0, None, ob)
        assert signal is None

    def test_no_price_to_beat(self):
        cs = ConvergenceStrategy(min_observations=1)
        snap = _make_snapshot(price_to_beat=None)
        ob = _make_orderbook()
        signal = _full_cycle(cs, snap, ob)
        assert signal is None

    def test_insufficient_skew(self):
        cs = ConvergenceStrategy(min_skew=0.75, min_observations=3)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.40, ask_no=0.60)  # not skewed
        signal = _full_cycle(cs, snap, ob)
        assert signal is None

    def test_cheap_price_too_high(self):
        cs = ConvergenceStrategy(max_cheap_price=0.30, min_observations=3, min_skew=0.75)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.40, ask_no=0.80)  # cheap=0.40 > 0.30
        signal = _full_cycle(cs, snap, ob)
        assert signal is None

    def test_missing_orderbook_asks(self):
        cs = ConvergenceStrategy(min_observations=3)
        snap = _make_snapshot()
        ob = OrderBook(best_ask_yes=None, best_ask_no=0.50)
        signal = _full_cycle(cs, snap, ob)
        assert signal is None


class TestAccumulation:
    """V2-specific: accumulation and decision logic."""

    def test_no_trigger_during_observation(self):
        """get_signal returns None during observation window."""
        cs = ConvergenceStrategy(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        # All observation ticks should return None
        for t in [50.0, 40.0, 30.0, 25.0]:
            assert cs.get_signal(t, snap, ob) is None

    def test_insufficient_observations(self):
        """Too few observations → skip."""
        cs = ConvergenceStrategy(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30,
                                 min_observations=10)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        # Only 2 observations
        signal = _full_cycle(cs, snap, ob, obs_times=[50.0, 40.0])
        assert signal is None

    def test_side_inconsistency_blocks(self):
        """Cheap side flip-flops → skip."""
        cs = ConvergenceStrategy(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30,
                                 min_observations=3, min_side_consistency=0.70)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob_yes = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        ob_no = _make_orderbook(ask_yes=0.85, ask_no=0.15)
        cs.reset()
        # Alternate sides
        for t, ob in [(50.0, ob_yes), (45.0, ob_no), (40.0, ob_yes),
                       (35.0, ob_no), (30.0, ob_yes), (25.0, ob_no)]:
            cs.get_signal(t, snap, ob)
        signal = cs.decide(8.0, snap, ob_yes)
        assert signal is None

    def test_low_convergence_rate_blocks(self):
        """Most ticks NOT converged → skip."""
        cs = ConvergenceStrategy(threshold_pct=0.0001, min_observations=2,
                                 min_convergence_rate=0.50, min_skew=0.75, max_cheap_price=0.30)
        snap_converged = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        snap_diverged = _make_snapshot(price=86000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        cs.reset()
        # 1 converged, 5 diverged
        cs.observe(50.0, snap_converged, ob)
        for t in [45.0, 40.0, 35.0, 30.0, 25.0]:
            cs.observe(t, snap_diverged, ob)
        signal = cs.decide(8.0, snap_diverged, ob)
        assert signal is None

    def test_decide_only_once(self):
        """Second call to decide returns None."""
        cs = ConvergenceStrategy(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30,
                                 min_observations=3)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        signal = _full_cycle(cs, snap, ob)
        assert signal is not None
        # Second call should return None
        signal2 = cs.decide(7.0, snap, ob)
        assert signal2 is None

    def test_reset_clears_state(self):
        """After reset, strategy can accumulate again."""
        cs = ConvergenceStrategy(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30,
                                 min_observations=3)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        signal1 = _full_cycle(cs, snap, ob)
        assert signal1 is not None
        # After reset, can decide again
        signal2 = _full_cycle(cs, snap, ob)
        assert signal2 is not None

    def test_signal_has_v2_fields(self):
        """V2 signal includes observations, convergence_rate, side_consistency."""
        cs = ConvergenceStrategy(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30,
                                 min_observations=3)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        signal = _full_cycle(cs, snap, ob)
        assert signal is not None
        assert signal.observations >= 5
        assert signal.convergence_rate > 0
        assert signal.side_consistency > 0


class TestOracleAgainstFilter:
    """Oracle 'not against' filter on median delta."""

    def test_oracle_against_yes_blocks(self):
        """Oracle says DOWN strongly, buying YES → blocked."""
        cs = ConvergenceStrategy(threshold_pct=0.001, min_skew=0.75, max_cheap_price=0.30,
                                 max_against_pct=0.0002, min_observations=3)
        # delta_pct = -0.0005 (oracle below beat = DOWN), but cheap side is YES (UP)
        snap = _make_snapshot(price=84957.5, price_to_beat=85000.0)  # -0.05%
        ob = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        signal = _full_cycle(cs, snap, ob)
        assert signal is None

    def test_oracle_against_no_blocks(self):
        """Oracle says UP strongly, buying NO → blocked."""
        cs = ConvergenceStrategy(threshold_pct=0.001, min_skew=0.75, max_cheap_price=0.30,
                                 max_against_pct=0.0002, min_observations=3)
        snap = _make_snapshot(price=85042.5, price_to_beat=85000.0)  # +0.05%
        ob = _make_orderbook(ask_yes=0.85, ask_no=0.15)
        signal = _full_cycle(cs, snap, ob)
        assert signal is None

    def test_oracle_neutral_passes(self):
        """Oracle at beat → passes filter."""
        cs = ConvergenceStrategy(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30,
                                 max_against_pct=0.0002, min_observations=3)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        signal = _full_cycle(cs, snap, ob)
        assert signal is not None


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


class TestEdgeCases:
    def test_zero_delta(self):
        cs = ConvergenceStrategy(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30,
                                 min_observations=3)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.05, ask_no=0.95)
        signal = _full_cycle(cs, snap, ob)
        assert signal is not None
        assert signal.delta_pct == 0.0

    def test_custom_parameters(self):
        cs = ConvergenceStrategy(
            threshold_pct=0.001, min_skew=0.70, max_cheap_price=0.35,
            window_start_s=120.0, window_end_s=10.0, min_observations=3,
            decision_time_s=12.0,
        )
        snap = _make_snapshot(price=85040.0, price_to_beat=85000.0)  # 0.047%
        ob = _make_orderbook(ask_yes=0.25, ask_no=0.75)
        signal = _full_cycle(cs, snap, ob, obs_times=[90.0, 60.0, 30.0, 15.0], decision_time=12.0)
        assert signal is not None
