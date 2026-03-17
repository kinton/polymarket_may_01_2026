"""
Convergence Trading Strategy v2 — live trading with strict filters.

Inherits all logic from ConvergenceV1. Differences:
  - SUPPORTED_TICKERS: BTC, ETH only (no SOL — historically poor win rate)
  - min_cheap_price: 0.14 (tokens below 14c are statistically unprofitable)
  - All other parameters identical to v1 defaults

This is the strategy used for live trading.
v1 is used for dry-run data collection (wider universe, no min_cheap floor).
"""

from __future__ import annotations

import logging

from strategies.convergence_v1 import ConvergenceV1
from strategies import register


@register
class ConvergenceV2(ConvergenceV1):
    """
    Convergence strategy for live trading — strict filters.

    Two phases (inherited from ConvergenceV1):
    1. observe() — collect snapshots during observation window (200s→20s)
    2. decide() — evaluate accumulated data, return signal or None
    """

    # --- identity ---
    name = "convergence"
    version = "v2"

    # BTC and ETH only — SOL excluded (poor win rate in live conditions)
    SUPPORTED_TICKERS: tuple[str, ...] = ("BTC", "ETH")

    def __init__(
        self,
        threshold_pct: float = 0.0005,    # 5bp convergence
        min_skew: float = 0.75,            # expensive side >= 75c
        max_cheap_price: float = 0.35,     # only buy at 35c or less
        min_cheap_price: float = 0.14,     # tokens below 14c are unprofitable
        max_against_pct: float = ConvergenceV1.DEFAULT_MAX_AGAINST_BP,
        window_start_s: float = 200.0,
        window_end_s: float = 20.0,
        min_observations: int = ConvergenceV1.MIN_OBSERVATIONS,
        min_convergence_rate: float = ConvergenceV1.MIN_CONVERGENCE_RATE,
        min_side_consistency: float = ConvergenceV1.MIN_SIDE_CONSISTENCY,
        decision_time_s: float | None = None,
        logger: logging.Logger | None = None,
    ):
        super().__init__(
            threshold_pct=threshold_pct,
            min_skew=min_skew,
            max_cheap_price=max_cheap_price,
            min_cheap_price=min_cheap_price,
            max_against_pct=max_against_pct,
            window_start_s=window_start_s,
            window_end_s=window_end_s,
            min_observations=min_observations,
            min_convergence_rate=min_convergence_rate,
            min_side_consistency=min_side_consistency,
            decision_time_s=decision_time_s,
            logger=logger,
        )
        # Override active tickers to v2's supported set
        self._active_tickers = self.SUPPORTED_TICKERS
