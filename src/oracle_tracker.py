"""
Lightweight live oracle (Chainlink) tracking helpers for "Up or Down" markets.

This module is intentionally small and dependency-free (stdlib only) so we can
embed it into the trader later without pulling in extra libs.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import sqrt


@dataclass(frozen=True)
class OracleSnapshot:
    ts_ms: int
    price: float
    n_points: int
    price_to_beat: float | None
    delta: float | None
    delta_pct: float | None
    vol_pct: float | None
    slope_usd_per_s: float | None
    zscore: float | None


class OracleTracker:
    """
    Tracks:
    - current oracle price
    - price_to_beat (captured at window start)
    - rolling volatility (stddev of % returns over last N seconds)
    - slope (USD/sec) over last N seconds
    - z-score of distance to beat (delta / (vol * price_to_beat))
    """

    def __init__(self, window_seconds: float = 60.0) -> None:
        self._window_s = float(window_seconds)
        self._points: deque[tuple[int, float]] = deque()
        self.price_to_beat: float | None = None

    def maybe_set_price_to_beat(
        self, *, ts_ms: int, price: float, start_ms: int, max_lag_ms: int = 10_000
    ) -> None:
        """
        Capture the oracle price at (or immediately after) the market window start.

        If we start listening too late (ts_ms >> start_ms), we avoid backfilling
        with a random later tick.
        """
        if self.price_to_beat is None and ts_ms >= start_ms and (ts_ms - start_ms) <= max_lag_ms:
            self.price_to_beat = price

    def update(self, *, ts_ms: int, price: float) -> OracleSnapshot:
        self._points.append((ts_ms, price))
        self._trim(ts_ms)

        beat = self.price_to_beat
        delta = (price - beat) if beat is not None else None
        delta_pct = (delta / beat) if (delta is not None and beat) else None

        vol_pct = self._rolling_vol_pct()
        slope = self._slope_usd_per_s()

        z = None
        if beat is not None and vol_pct is not None and vol_pct > 0:
            denom = beat * vol_pct
            if denom > 0:
                z = (price - beat) / denom

        return OracleSnapshot(
            ts_ms=ts_ms,
            price=price,
            n_points=len(self._points),
            price_to_beat=beat,
            delta=delta,
            delta_pct=delta_pct,
            vol_pct=vol_pct,
            slope_usd_per_s=slope,
            zscore=z,
        )

    def _trim(self, now_ms: int) -> None:
        cutoff_ms = int(now_ms - (self._window_s * 1000))
        while self._points and self._points[0][0] < cutoff_ms:
            self._points.popleft()

    def _rolling_vol_pct(self) -> float | None:
        if len(self._points) < 3:
            return None

        # Compute % returns between consecutive points.
        rets: list[float] = []
        prev_p = None
        for _ts, p in self._points:
            if prev_p is None:
                prev_p = p
                continue
            if prev_p <= 0:
                prev_p = p
                continue
            rets.append((p - prev_p) / prev_p)
            prev_p = p

        if len(rets) < 2:
            return None

        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        return sqrt(var)

    def _slope_usd_per_s(self) -> float | None:
        if len(self._points) < 2:
            return None

        first_ts, first_p = self._points[0]
        last_ts, last_p = self._points[-1]
        dt_s = (last_ts - first_ts) / 1000.0
        if dt_s <= 0:
            return None
        return (last_p - first_p) / dt_s
