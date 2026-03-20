"""Tests for ConvergenceV1 strategy plugin.

Mirrors test_convergence_strategy.py but uses the new plugin types
(MarketTick, Signal) directly — validates that the port is correct.
"""


from strategies.convergence_v1 import ConvergenceV1
from strategies.base import MarketInfo, MarketTick, Signal
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


def _make_tick(
    time_remaining: float,
    snap: OracleSnapshot | None,
    ob: OrderBook,
) -> MarketTick:
    return MarketTick(time_remaining=time_remaining, oracle_snapshot=snap, orderbook=ob)


def _full_cycle(
    cs: ConvergenceV1,
    snap: OracleSnapshot,
    ob: OrderBook,
    obs_times: list[float] | None = None,
) -> Signal | None:
    """Helper: simulate full cycle — feed ticks via get_signal, return first trigger or None."""
    cs.reset()
    if obs_times is None:
        obs_times = [170.0, 150.0, 130.0, 110.0, 90.0, 70.0]
    for t in obs_times:
        tick = _make_tick(t, snap, ob)
        signal = cs.get_signal(tick)
        if signal is not None:
            return signal
    return None


class TestCheapSideBuying:
    """Core: buys the cheap side when oracle is at beat."""

    def test_buys_cheap_yes(self):
        cs = ConvergenceV1(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30,
                           min_observations=3)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        signal = _full_cycle(cs, snap, ob)
        assert signal is not None
        assert signal.side == "YES"
        assert signal.price == 0.15

    def test_buys_cheap_no(self):
        cs = ConvergenceV1(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30,
                           min_observations=3)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.80, ask_no=0.20)
        signal = _full_cycle(cs, snap, ob)
        assert signal is not None
        assert signal.side == "NO"
        assert signal.price == 0.20

    def test_slightly_off_beat_still_converged(self):
        cs = ConvergenceV1(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30,
                           min_observations=3)
        snap = _make_snapshot(price=85008.5, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.20, ask_no=0.80)
        signal = _full_cycle(cs, snap, ob)
        assert signal is not None


class TestNoTriggerConditions:
    def test_delta_too_large(self):
        cs = ConvergenceV1(threshold_pct=0.0002, min_observations=3)
        snap = _make_snapshot(price=86000.0, price_to_beat=85000.0)
        ob = _make_orderbook()
        signal = _full_cycle(cs, snap, ob)
        assert signal is None

    def test_no_oracle_data(self):
        cs = ConvergenceV1(min_observations=1)
        ob = _make_orderbook()
        cs.reset()
        tick = _make_tick(100.0, None, ob)
        signal = cs.get_signal(tick)
        assert signal is None

    def test_no_price_to_beat(self):
        cs = ConvergenceV1(min_observations=1)
        snap = _make_snapshot(price_to_beat=None)
        ob = _make_orderbook()
        signal = _full_cycle(cs, snap, ob)
        assert signal is None

    def test_insufficient_skew(self):
        cs = ConvergenceV1(min_skew=0.75, min_observations=3)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.40, ask_no=0.60)
        signal = _full_cycle(cs, snap, ob)
        assert signal is None

    def test_cheap_price_too_high(self):
        cs = ConvergenceV1(max_cheap_price=0.30, min_observations=3, min_skew=0.75)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.40, ask_no=0.80)
        signal = _full_cycle(cs, snap, ob)
        assert signal is None

    def test_missing_orderbook_asks(self):
        cs = ConvergenceV1(min_observations=3)
        snap = _make_snapshot()
        ob = OrderBook(best_ask_yes=None, best_ask_no=0.50)
        signal = _full_cycle(cs, snap, ob)
        assert signal is None


class TestAccumulation:
    def test_insufficient_observations(self):
        cs = ConvergenceV1(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30,
                           min_observations=10)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        signal = _full_cycle(cs, snap, ob, obs_times=[170.0, 150.0])
        assert signal is None

    def test_side_inconsistency_blocks(self):
        cs = ConvergenceV1(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30,
                           min_observations=3, min_side_consistency=0.70)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob_yes = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        ob_no = _make_orderbook(ask_yes=0.85, ask_no=0.15)
        cs.reset()
        signal = None
        for t, ob in [(170.0, ob_yes), (150.0, ob_no), (130.0, ob_yes),
                       (110.0, ob_no), (90.0, ob_yes), (70.0, ob_no)]:
            tick = _make_tick(t, snap, ob)
            result = cs.get_signal(tick)
            if result is not None:
                signal = result
        assert signal is None

    def test_low_convergence_rate_blocks(self):
        cs = ConvergenceV1(threshold_pct=0.0001, min_observations=5,
                           min_convergence_rate=0.50, min_skew=0.75, max_cheap_price=0.30)
        snap_converged = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        snap_diverged = _make_snapshot(price=86000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        cs.reset()
        signal = None
        for t, s in [(170.0, snap_converged), (160.0, snap_diverged), (150.0, snap_diverged),
                      (140.0, snap_diverged), (130.0, snap_diverged), (120.0, snap_diverged)]:
            tick = _make_tick(t, s, ob)
            result = cs.get_signal(tick)
            if result is not None:
                signal = result
        assert signal is None

    def test_decide_only_once(self):
        cs = ConvergenceV1(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30,
                           min_observations=3)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        signal = _full_cycle(cs, snap, ob)
        assert signal is not None
        tick = _make_tick(60.0, snap, ob)
        signal2 = cs.get_signal(tick)
        assert signal2 is None

    def test_reset_clears_state(self):
        cs = ConvergenceV1(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30,
                           min_observations=3)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        signal1 = _full_cycle(cs, snap, ob)
        assert signal1 is not None
        signal2 = _full_cycle(cs, snap, ob)
        assert signal2 is not None


class TestSignalMetadata:
    """Verify Signal.metadata contains expected convergence fields."""

    def test_signal_has_metadata_fields(self):
        cs = ConvergenceV1(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30,
                           min_observations=3)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        signal = _full_cycle(cs, snap, ob)
        assert signal is not None
        m = signal.metadata
        assert m["observations"] >= 3
        assert m["convergence_rate"] > 0
        assert m["side_consistency"] > 0
        assert m["side_label"] in ("UP", "DOWN")
        assert m["reason"] == "convergence"

    def test_confidence_is_rate_times_consistency(self):
        cs = ConvergenceV1(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30,
                           min_observations=3)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        signal = _full_cycle(cs, snap, ob)
        assert signal is not None
        m = signal.metadata
        expected = m["convergence_rate"] * m["side_consistency"]
        assert abs(m["confidence"] - expected) < 1e-9

    def test_disable_stop_loss_is_true(self):
        cs = ConvergenceV1(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30,
                           min_observations=3)
        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        signal = _full_cycle(cs, snap, ob)
        assert signal is not None
        assert signal.disable_stop_loss is True


class TestSnapshotParity:
    """Ensure new plugin produces same decisions as old shim."""

    def test_old_shim_matches_new_plugin(self):
        """ConvergenceStrategy (shim) and ConvergenceV1 (plugin) produce equivalent signals."""
        from src.trading.convergence_strategy import ConvergenceStrategy

        kwargs = dict(threshold_pct=0.0002, min_skew=0.75, max_cheap_price=0.30, min_observations=3)
        old = ConvergenceStrategy(**kwargs)
        new = ConvergenceV1(**kwargs)

        snap = _make_snapshot(price=85000.0, price_to_beat=85000.0)
        ob = _make_orderbook(ask_yes=0.15, ask_no=0.85)
        times = [170.0, 150.0, 130.0, 110.0, 90.0, 70.0]

        old.reset()
        new.reset()

        old_signal = None
        new_signal = None
        for t in times:
            tick = _make_tick(t, snap, ob)
            os = old.get_signal(t, snap, ob)
            ns = new.get_signal(tick)
            if os is not None and old_signal is None:
                old_signal = os
            if ns is not None and new_signal is None:
                new_signal = ns

        assert old_signal is not None
        assert new_signal is not None
        assert old_signal.side == new_signal.side
        assert old_signal.price == new_signal.price
        assert abs(old_signal.convergence_rate - new_signal.metadata["convergence_rate"]) < 1e-9
        assert abs(old_signal.side_consistency - new_signal.metadata["side_consistency"]) < 1e-9


def _make_market_info(ticker: str = "BTC") -> MarketInfo:
    return MarketInfo(
        condition_id="test_cid", ticker=ticker, title=f"{ticker} Up or Down",
        end_time_utc="2026-03-14 18:30:00 UTC", minutes_until_end=5.0,
        token_id_yes="ty", token_id_no="tn",
    )


class TestMarketFilter:
    def test_accepts_btc(self):
        cs = ConvergenceV1()
        assert cs.market_filter(_make_market_info("BTC")) is True

    def test_accepts_eth(self):
        cs = ConvergenceV1()
        assert cs.market_filter(_make_market_info("ETH")) is True

    def test_accepts_sol_by_default(self):
        # v1 is the broad data-collection strategy — SOL included
        cs = ConvergenceV1()
        assert cs.market_filter(_make_market_info("SOL")) is True

    def test_case_insensitive(self):
        cs = ConvergenceV1()
        assert cs.market_filter(_make_market_info("btc")) is True
        assert cs.market_filter(_make_market_info("Eth")) is True

    def test_configure_overrides_tickers(self):
        cs = ConvergenceV1()
        cs.configure(tickers=["SOL", "BTC"])
        assert cs.market_filter(_make_market_info("SOL")) is True
        assert cs.market_filter(_make_market_info("BTC")) is True
        assert cs.market_filter(_make_market_info("ETH")) is False

    def test_rejects_unknown_ticker(self):
        cs = ConvergenceV1()
        assert cs.market_filter(_make_market_info("DOGE")) is False
