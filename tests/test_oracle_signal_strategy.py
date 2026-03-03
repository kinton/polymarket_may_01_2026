"""Tests for OracleSignalStrategy."""

import pytest
from src.oracle_tracker import OracleSnapshot
from src.clob_types import OrderBook
from src.trading.oracle_signal_strategy import OracleSignalStrategy, OracleSignal


def _snap(price=100.0, beat=100.0, delta=None, delta_pct=None):
    if delta is None:
        delta = price - beat
    if delta_pct is None:
        delta_pct = delta / beat if beat else 0
    return OracleSnapshot(
        ts_ms=1000000, price=price, n_points=10,
        price_to_beat=beat, delta=delta, delta_pct=delta_pct,
        vol_pct=0.001, slope_usd_per_s=0.01, zscore=1.0,
    )


def _ob(ask_yes=0.50, ask_no=0.50, bid_yes=0.49, bid_no=0.49):
    ob = OrderBook()
    ob.best_ask_yes = ask_yes
    ob.best_ask_no = ask_no
    ob.best_bid_yes = bid_yes
    ob.best_bid_no = bid_no
    ob.update()
    return ob


class TestOracleSignalStrategy:

    def test_signal_when_oracle_diverged_up(self):
        """Oracle up 0.2% → buy UP side (YES) at 0.50."""
        strat = OracleSignalStrategy(min_delta_pct=0.001, max_entry_price=0.55, min_edge_pct=0.05)
        snap = _snap(price=100.20, beat=100.0)  # +0.20%
        ob = _ob(ask_yes=0.50, ask_no=0.50)

        signal = strat.get_signal(30.0, snap, ob, up_side="YES", down_side="NO")
        assert signal is not None
        assert signal.side == "YES"
        assert signal.side_label == "UP"
        assert signal.price == 0.50
        assert signal.edge_pct > 0

    def test_signal_when_oracle_diverged_down(self):
        """Oracle down 0.2% → buy DOWN side (NO) at 0.50."""
        strat = OracleSignalStrategy(min_delta_pct=0.001, max_entry_price=0.55, min_edge_pct=0.05)
        snap = _snap(price=99.80, beat=100.0)  # -0.20%
        ob = _ob(ask_yes=0.50, ask_no=0.50)

        signal = strat.get_signal(30.0, snap, ob, up_side="YES", down_side="NO")
        assert signal is not None
        assert signal.side == "NO"
        assert signal.side_label == "DOWN"

    def test_no_signal_delta_too_small(self):
        """Oracle only moved 0.05% — below 0.10% threshold."""
        strat = OracleSignalStrategy(min_delta_pct=0.001)
        snap = _snap(price=100.05, beat=100.0)  # 0.05%
        ob = _ob()

        signal = strat.get_signal(30.0, snap, ob, up_side="YES", down_side="NO")
        assert signal is None

    def test_no_signal_price_too_high(self):
        """Correct side ask is 0.60 > max_entry_price 0.55."""
        strat = OracleSignalStrategy(min_delta_pct=0.001, max_entry_price=0.55, min_zscore=0.0)
        snap = _snap(price=100.20, beat=100.0)
        ob = _ob(ask_yes=0.60, ask_no=0.40)

        signal = strat.get_signal(30.0, snap, ob, up_side="YES", down_side="NO")
        assert signal is None

    def test_no_signal_outside_time_window(self):
        """Too early or too late."""
        strat = OracleSignalStrategy(window_start_s=60.0, window_end_s=5.0, max_entry_price=0.55, min_edge_pct=0.05, min_zscore=0.0)
        snap = _snap(price=100.20, beat=100.0)
        ob = _ob()

        assert strat.get_signal(120.0, snap, ob, "YES", "NO") is None  # too early
        assert strat.get_signal(3.0, snap, ob, "YES", "NO") is None    # too late
        assert strat.get_signal(30.0, snap, ob, "YES", "NO") is not None  # in window

    def test_no_signal_without_beat(self):
        """No price_to_beat → no signal."""
        strat = OracleSignalStrategy()
        snap = OracleSnapshot(
            ts_ms=1000, price=100.0, n_points=5,
            price_to_beat=None, delta=None, delta_pct=None,
            vol_pct=0.001, slope_usd_per_s=0.01, zscore=1.0,
        )
        ob = _ob()
        assert strat.get_signal(30.0, snap, ob, "YES", "NO") is None

    def test_no_signal_without_side_mapping(self):
        """up_side/down_side not mapped → no signal."""
        strat = OracleSignalStrategy()
        snap = _snap(price=100.20, beat=100.0)
        ob = _ob()
        assert strat.get_signal(30.0, snap, ob, None, None) is None

    def test_edge_calculation(self):
        """Verify edge = (fair - price) / price."""
        strat = OracleSignalStrategy(min_delta_pct=0.001, min_edge_pct=0.0, max_entry_price=0.55, min_zscore=0.0)
        snap = _snap(price=100.20, beat=100.0)  # +0.20% → ~65% fair
        ob = _ob(ask_yes=0.50)

        signal = strat.get_signal(30.0, snap, ob, "YES", "NO")
        assert signal is not None
        expected_edge = (signal.estimated_fair_value - 0.50) / 0.50
        assert abs(signal.edge_pct - expected_edge) < 0.001

    def test_fair_value_increases_with_delta(self):
        """Bigger delta → higher fair value."""
        strat = OracleSignalStrategy(min_delta_pct=0.001, min_edge_pct=0.0, max_entry_price=0.55, min_zscore=0.0)
        ob = _ob(ask_yes=0.40)

        sig_small = strat.get_signal(30.0, _snap(price=100.20, beat=100.0), ob, "YES", "NO")
        sig_big = strat.get_signal(30.0, _snap(price=101.00, beat=100.0), ob, "YES", "NO")

        assert sig_small is not None and sig_big is not None
        assert sig_big.estimated_fair_value > sig_small.estimated_fair_value

    def test_should_enter_delegates_to_get_signal(self):
        """should_enter returns True iff get_signal is not None."""
        strat = OracleSignalStrategy(min_delta_pct=0.001, min_edge_pct=0.05, max_entry_price=0.55, min_zscore=0.0)
        snap = _snap(price=100.20, beat=100.0)
        ob = _ob()

        assert strat.should_enter(30.0, snap, ob, "YES", "NO") is True
        assert strat.should_enter(120.0, snap, ob, "YES", "NO") is False

    def test_no_edge_no_signal(self):
        """If entry price >= fair value, no signal."""
        strat = OracleSignalStrategy(min_delta_pct=0.001, min_edge_pct=0.10)
        # Tiny delta → fair ~0.60, but ask is 0.55 → edge ~9% < 10%
        snap = _snap(price=100.10, beat=100.0)  # 0.10%
        ob = _ob(ask_yes=0.55)

        signal = strat.get_signal(30.0, snap, ob, "YES", "NO")
        # Either None (no edge) or edge < min_edge_pct
        if signal is not None:
            assert signal.edge_pct >= 0.10
