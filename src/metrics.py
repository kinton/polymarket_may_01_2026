"""
In-process trading metrics collector.

Tracks counters, gauges, and histograms for:
- Trade counts (buys, sells, by outcome)
- PnL (per-trade and cumulative)
- API call latency
- Error counts

All operations are thread-safe (use atomics via threading.Lock).
Metrics are exposed as a dict for the health-check endpoint and can be
scraped by Prometheus-compatible tools via ``/metrics`` (text format).

Environment variables:
    METRICS_ENABLED  — enable/disable collection (default true)
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, Optional


def _env_bool(key: str, default: bool = True) -> bool:
    val = os.getenv(key, "").lower()
    if val in ("0", "false", "no", "off"):
        return False
    if val in ("1", "true", "yes", "on"):
        return True
    return default


@dataclass
class LatencyStats:
    """Lightweight latency histogram (no numpy dependency)."""

    count: int = 0
    total_ms: float = 0.0
    min_ms: float = float("inf")
    max_ms: float = 0.0
    _recent: List[float] = field(default_factory=list)

    def record(self, ms: float) -> None:
        self.count += 1
        self.total_ms += ms
        if ms < self.min_ms:
            self.min_ms = ms
        if ms > self.max_ms:
            self.max_ms = ms
        # Keep last 100 for p50/p95
        self._recent.append(ms)
        if len(self._recent) > 100:
            self._recent.pop(0)

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.count if self.count else 0.0

    @property
    def p50_ms(self) -> float:
        return self._percentile(50)

    @property
    def p95_ms(self) -> float:
        return self._percentile(95)

    def _percentile(self, pct: int) -> float:
        if not self._recent:
            return 0.0
        s = sorted(self._recent)
        idx = int(len(s) * pct / 100)
        idx = min(idx, len(s) - 1)
        return s[idx]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "count": self.count,
            "avg_ms": round(self.avg_ms, 2),
            "min_ms": round(self.min_ms, 2) if self.count else None,
            "max_ms": round(self.max_ms, 2) if self.count else None,
            "p50_ms": round(self.p50_ms, 2),
            "p95_ms": round(self.p95_ms, 2),
        }


class MetricsCollector:
    """Central metrics collector — singleton per process."""

    _instance: Optional["MetricsCollector"] = None
    _lock_cls = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._enabled = _env_bool("METRICS_ENABLED", True)
        self._started_at = time.time()

        # Counters
        self._trades_total: int = 0
        self._buys: int = 0
        self._sells: int = 0
        self._wins: int = 0
        self._losses: int = 0
        self._errors: int = 0
        self._error_types: Dict[str, int] = defaultdict(int)

        # Gauges
        self._cumulative_pnl: float = 0.0
        self._last_trade_pnl: float = 0.0
        self._active_positions: int = 0

        # Latency histograms
        self._api_latency = LatencyStats()
        self._order_latency = LatencyStats()

    @classmethod
    def get(cls) -> "MetricsCollector":
        """Return the process-wide singleton."""
        if cls._instance is None:
            with cls._lock_cls:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> "MetricsCollector":
        """Reset singleton (for tests)."""
        with cls._lock_cls:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # Recording helpers
    # ------------------------------------------------------------------

    def record_trade(
        self,
        side: str,
        pnl: float = 0.0,
        *,
        won: Optional[bool] = None,
    ) -> None:
        """Record a completed trade.

        Args:
            side: "buy" or "sell"
            pnl: Profit/loss amount for this trade
            won: Explicit win/loss flag (if None, inferred from pnl on sells)
        """
        if not self._enabled:
            return
        with self._lock:
            self._trades_total += 1
            if side == "buy":
                self._buys += 1
            else:
                self._sells += 1
                self._cumulative_pnl += pnl
                self._last_trade_pnl = pnl
                if won is True or (won is None and pnl > 0):
                    self._wins += 1
                elif won is False or (won is None and pnl < 0):
                    self._losses += 1

    def record_error(self, error_type: str = "unknown") -> None:
        """Increment error counter."""
        if not self._enabled:
            return
        with self._lock:
            self._errors += 1
            self._error_types[error_type] += 1

    def set_active_positions(self, count: int) -> None:
        """Update active-positions gauge."""
        if not self._enabled:
            return
        with self._lock:
            self._active_positions = count

    def record_api_latency(self, ms: float) -> None:
        """Record an API call latency in milliseconds."""
        if not self._enabled:
            return
        with self._lock:
            self._api_latency.record(ms)

    def record_order_latency(self, ms: float) -> None:
        """Record order execution latency in milliseconds."""
        if not self._enabled:
            return
        with self._lock:
            self._order_latency.record(ms)

    @contextmanager
    def measure_api(self) -> Generator[None, None, None]:
        """Context manager that auto-records API latency."""
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.record_api_latency(elapsed_ms)

    @contextmanager
    def measure_order(self) -> Generator[None, None, None]:
        """Context manager that auto-records order latency."""
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.record_order_latency(elapsed_ms)

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """Return a point-in-time snapshot of all metrics."""
        with self._lock:
            win_rate = (
                (self._wins / (self._wins + self._losses) * 100)
                if (self._wins + self._losses) > 0
                else 0.0
            )
            return {
                "uptime_s": round(time.time() - self._started_at, 1),
                "trades_total": self._trades_total,
                "buys": self._buys,
                "sells": self._sells,
                "wins": self._wins,
                "losses": self._losses,
                "win_rate_pct": round(win_rate, 1),
                "cumulative_pnl": round(self._cumulative_pnl, 6),
                "last_trade_pnl": round(self._last_trade_pnl, 6),
                "active_positions": self._active_positions,
                "errors": self._errors,
                "error_types": dict(self._error_types),
                "api_latency": self._api_latency.to_dict(),
                "order_latency": self._order_latency.to_dict(),
            }

    # ------------------------------------------------------------------
    # Prometheus text format
    # ------------------------------------------------------------------

    def prometheus_text(self) -> str:
        """Render metrics in Prometheus exposition format."""
        snap = self.snapshot()
        lines: List[str] = []

        def _g(name: str, help_text: str, value: Any) -> None:
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {value}")

        def _c(name: str, help_text: str, value: Any) -> None:
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {value}")

        _g("polymarket_uptime_seconds", "Bot uptime in seconds", snap["uptime_s"])
        _c("polymarket_trades_total", "Total trades executed", snap["trades_total"])
        _c("polymarket_buys_total", "Total buy orders", snap["buys"])
        _c("polymarket_sells_total", "Total sell orders", snap["sells"])
        _c("polymarket_wins_total", "Winning trades", snap["wins"])
        _c("polymarket_losses_total", "Losing trades", snap["losses"])
        _g("polymarket_win_rate_pct", "Win rate percentage", snap["win_rate_pct"])
        _g("polymarket_cumulative_pnl", "Cumulative PnL", snap["cumulative_pnl"])
        _g("polymarket_active_positions", "Active positions", snap["active_positions"])
        _c("polymarket_errors_total", "Total errors", snap["errors"])

        lat = snap["api_latency"]
        _g("polymarket_api_latency_avg_ms", "Average API latency ms", lat["avg_ms"])
        _g("polymarket_api_latency_p95_ms", "P95 API latency ms", lat["p95_ms"])

        olat = snap["order_latency"]
        _g("polymarket_order_latency_avg_ms", "Average order latency ms", olat["avg_ms"])
        _g("polymarket_order_latency_p95_ms", "P95 order latency ms", olat["p95_ms"])

        lines.append("")
        return "\n".join(lines)
