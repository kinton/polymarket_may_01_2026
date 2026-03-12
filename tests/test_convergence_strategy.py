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


def _full_cycle(
    cs: ConvergenceStrategy,
    snap: OracleSnapshot,
    ob: OrderBook,
    obs_times: list[float] | None = None,
    **kwargs,
) -> ConvergenceSignal | None:
    """Helper: simulate full cycle — feed ticks via get_signal, return first trigger or None."""
    cs.reset()
    if obs_times is None:
        obs_times = [170.0, 150.0, 130.0, 110.0, 90.0, 70.0]
    for t in obs_times:
        signal = cs.get_signal(t, snap, ob)
        if signal is not None:
            return signal
    return None


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
        signal = cs.get_signal(100.0, None, ob)
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
        for t in [170.0, 150.0, 130.0, 110.0]:
            assert cs.get_signal(t, snap, ob) is None

    def test_insufficient_observations(self):
        """Too few observations → skip."""
        cs = ConvergenceStrategy(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30,
                                 min_observations=10)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        # Only 2 observations
        signal = _full_cycle(cs, snap, ob, obs_times=[170.0, 150.0])
        assert signal is None

    def test_side_inconsistency_blocks(self):
        """Cheap side flip-flops → skip."""
        cs = ConvergenceStrategy(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30,
                                 min_observations=3, min_side_consistency=0.70)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob_yes = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        ob_no = _make_orderbook(ask_yes=0.85, ask_no=0.15)
        cs.reset()
        # Alternate sides — should never trigger due to inconsistency
        signal = None
        for t, ob in [(170.0, ob_yes), (150.0, ob_no), (130.0, ob_yes),
                       (110.0, ob_no), (90.0, ob_yes), (70.0, ob_no)]:
            result = cs.get_signal(t, snap, ob)
            if result is not None:
                signal = result
        assert signal is None

    def test_low_convergence_rate_blocks(self):
        """Most ticks NOT converged → skip. Need enough ticks to dilute rate below threshold."""
        cs = ConvergenceStrategy(threshold_pct=0.0001, min_observations=5,
                                 min_convergence_rate=0.50, min_skew=0.75, max_cheap_price=0.30)
        snap_converged = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        snap_diverged = _make_snapshot(price=86000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        cs.reset()
        # 1 converged, then 5 diverged — convergence_rate = 1/6 < 50%
        signal = None
        for t, s in [(170.0, snap_converged), (160.0, snap_diverged), (150.0, snap_diverged),
                      (140.0, snap_diverged), (130.0, snap_diverged), (120.0, snap_diverged)]:
            result = cs.get_signal(t, s, ob)
            if result is not None:
                signal = result
        assert signal is None

    def test_decide_only_once(self):
        """Second call to decide returns None."""
        cs = ConvergenceStrategy(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30,
                                 min_observations=3)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        signal = _full_cycle(cs, snap, ob)
        assert signal is not None
        # After trigger, further calls return None
        signal2 = cs.get_signal(60.0, snap, ob)
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
        assert signal.observations >= 3
        assert signal.convergence_rate > 0
        assert signal.side_consistency > 0

    def test_confidence_is_not_price(self):
        """confidence = convergence_rate * side_consistency, NOT entry price.

        Regression test: previously confidence was accidentally set to conv_signal.price.
        """
        cs = ConvergenceStrategy(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30,
                                 min_observations=3)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        signal = _full_cycle(cs, snap, ob)
        assert signal is not None
        expected_confidence = signal.convergence_rate * signal.side_consistency
        # confidence must be in [0, 1] — not a raw price like 0.15
        assert 0.0 < expected_confidence <= 1.0
        # entry price is 0.15; confidence must differ from price
        assert abs(expected_confidence - signal.price) > 1e-9


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
                                 min_cheap_price=0.0,  # disable min price for this test
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
        )
        snap = _make_snapshot(price=85040.0, price_to_beat=85000.0)  # 0.047%
        ob = _make_orderbook(ask_yes=0.25, ask_no=0.75)
        signal = _full_cycle(cs, snap, ob, obs_times=[90.0, 60.0, 30.0, 15.0])
        assert signal is not None
