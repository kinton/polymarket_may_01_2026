"""Tests for src/metrics.py — MetricsCollector."""

import threading
import time

import pytest

from src.metrics import LatencyStats, MetricsCollector


# ------------------------------------------------------------------
# Fixture: always reset singleton between tests
# ------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_metrics():
    MetricsCollector.reset()
    yield
    MetricsCollector.reset()


# ------------------------------------------------------------------
# LatencyStats
# ------------------------------------------------------------------

class TestLatencyStats:
    def test_empty(self):
        s = LatencyStats()
        assert s.count == 0
        assert s.avg_ms == 0.0
        assert s.p50_ms == 0.0
        assert s.p95_ms == 0.0
        d = s.to_dict()
        assert d["count"] == 0
        assert d["min_ms"] is None

    def test_single_record(self):
        s = LatencyStats()
        s.record(42.5)
        assert s.count == 1
        assert s.avg_ms == 42.5
        assert s.min_ms == 42.5
        assert s.max_ms == 42.5

    def test_multiple_records(self):
        s = LatencyStats()
        for v in [10, 20, 30, 40, 50]:
            s.record(v)
        assert s.count == 5
        assert s.avg_ms == 30.0
        assert s.min_ms == 10
        assert s.max_ms == 50
        assert s.p50_ms == 30  # index 2

    def test_recent_cap_at_100(self):
        s = LatencyStats()
        for i in range(150):
            s.record(float(i))
        assert s.count == 150
        assert len(s._recent) == 100


# ------------------------------------------------------------------
# MetricsCollector — singleton
# ------------------------------------------------------------------

class TestSingleton:
    def test_get_returns_same(self):
        a = MetricsCollector.get()
        b = MetricsCollector.get()
        assert a is b

    def test_reset_creates_new(self):
        a = MetricsCollector.get()
        b = MetricsCollector.reset()
        assert a is not b


# ------------------------------------------------------------------
# Trade recording
# ------------------------------------------------------------------

class TestRecordTrade:
    def test_buy(self):
        m = MetricsCollector.get()
        m.record_trade("buy")
        snap = m.snapshot()
        assert snap["trades_total"] == 1
        assert snap["buys"] == 1
        assert snap["sells"] == 0

    def test_sell_with_pnl_positive(self):
        m = MetricsCollector.get()
        m.record_trade("sell", pnl=0.05)
        snap = m.snapshot()
        assert snap["sells"] == 1
        assert snap["wins"] == 1
        assert snap["losses"] == 0
        assert snap["cumulative_pnl"] == pytest.approx(0.05)

    def test_sell_with_pnl_negative(self):
        m = MetricsCollector.get()
        m.record_trade("sell", pnl=-0.03)
        snap = m.snapshot()
        assert snap["losses"] == 1
        assert snap["wins"] == 0

    def test_win_rate(self):
        m = MetricsCollector.get()
        m.record_trade("sell", pnl=0.1)
        m.record_trade("sell", pnl=0.2)
        m.record_trade("sell", pnl=-0.05)
        snap = m.snapshot()
        assert snap["win_rate_pct"] == pytest.approx(66.7, abs=0.1)

    def test_explicit_won_flag(self):
        m = MetricsCollector.get()
        m.record_trade("sell", pnl=0.0, won=True)
        assert m.snapshot()["wins"] == 1


# ------------------------------------------------------------------
# Error recording
# ------------------------------------------------------------------

class TestRecordError:
    def test_basic(self):
        m = MetricsCollector.get()
        m.record_error("ConnectionError")
        m.record_error("ConnectionError")
        m.record_error("TimeoutError")
        snap = m.snapshot()
        assert snap["errors"] == 3
        assert snap["error_types"]["ConnectionError"] == 2
        assert snap["error_types"]["TimeoutError"] == 1


# ------------------------------------------------------------------
# Latency recording
# ------------------------------------------------------------------

class TestLatency:
    def test_api_latency(self):
        m = MetricsCollector.get()
        m.record_api_latency(10.0)
        m.record_api_latency(20.0)
        snap = m.snapshot()
        assert snap["api_latency"]["count"] == 2
        assert snap["api_latency"]["avg_ms"] == 15.0

    def test_order_latency(self):
        m = MetricsCollector.get()
        m.record_order_latency(50.0)
        snap = m.snapshot()
        assert snap["order_latency"]["count"] == 1

    def test_measure_api_context_manager(self):
        m = MetricsCollector.get()
        with m.measure_api():
            time.sleep(0.01)  # ~10ms
        snap = m.snapshot()
        assert snap["api_latency"]["count"] == 1
        assert snap["api_latency"]["avg_ms"] > 5  # at least some ms

    def test_measure_order_context_manager(self):
        m = MetricsCollector.get()
        with m.measure_order():
            time.sleep(0.01)
        assert m.snapshot()["order_latency"]["count"] == 1


# ------------------------------------------------------------------
# Active positions gauge
# ------------------------------------------------------------------

class TestGauges:
    def test_active_positions(self):
        m = MetricsCollector.get()
        m.set_active_positions(3)
        assert m.snapshot()["active_positions"] == 3
        m.set_active_positions(0)
        assert m.snapshot()["active_positions"] == 0


# ------------------------------------------------------------------
# Disabled metrics
# ------------------------------------------------------------------

class TestDisabled:
    def test_disabled_does_not_record(self, monkeypatch):
        monkeypatch.setenv("METRICS_ENABLED", "false")
        m = MetricsCollector.reset()
        m.record_trade("buy")
        m.record_error("X")
        m.record_api_latency(10)
        assert m.snapshot()["trades_total"] == 0
        assert m.snapshot()["errors"] == 0


# ------------------------------------------------------------------
# Prometheus text format
# ------------------------------------------------------------------

class TestPrometheus:
    def test_text_contains_key_metrics(self):
        m = MetricsCollector.get()
        m.record_trade("buy")
        m.record_trade("sell", pnl=0.1)
        text = m.prometheus_text()
        assert "polymarket_trades_total 2" in text
        assert "polymarket_buys_total 1" in text
        assert "polymarket_sells_total 1" in text
        assert "polymarket_wins_total 1" in text
        assert "polymarket_cumulative_pnl" in text
        assert "# HELP" in text
        assert "# TYPE" in text


# ------------------------------------------------------------------
# Thread safety (smoke test)
# ------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_writes(self):
        m = MetricsCollector.get()
        errors = []

        def _worker():
            try:
                for _ in range(100):
                    m.record_trade("buy")
                    m.record_api_latency(1.0)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert m.snapshot()["trades_total"] == 400
        assert m.snapshot()["api_latency"]["count"] == 400
