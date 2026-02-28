"""
Convergence Trading Strategy for Polymarket "Up or Down" markets.

Core idea: When the oracle price converges with price_to_beat (within 5 basis points),
the real odds are ~50/50. But the market may still show a skew (e.g., 90/10 due to lag).
We buy the CHEAP side for massive risk/reward.

Entry conditions (ALL must be met):
1. Time window: 20-60 seconds before expiry
2. Oracle convergence: |delta_pct| <= 0.0005 (5 basis points)
3. Market skew: expensive side >= 0.80
4. Cheap side price <= 0.40
5. Oracle data is fresh and has price_to_beat
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.oracle_tracker import OracleSnapshot
from src.clob_types import OrderBook


@dataclass(frozen=True)
class ConvergenceSignal:
    """Details of a convergence entry signal."""
    cheap_side: str           # "YES" or "NO"
    cheap_price: float        # ask price of cheap side
    expensive_side: str       # "YES" or "NO"
    expensive_price: float    # ask price of expensive side
    delta_pct: float          # |current - beat| / beat
    time_remaining: float
    oracle_price: float
    price_to_beat: float


class ConvergenceStrategy:
    """
    Detects convergence opportunities and recommends the cheap side to buy.
    """

    def __init__(
        self,
        threshold_pct: float = 0.0005,
        min_skew: float = 0.80,
        max_cheap_price: float = 0.40,
        window_start_s: float = 60.0,
        window_end_s: float = 20.0,
        logger: logging.Logger | None = None,
    ):
        """
        Args:
            threshold_pct: Max |delta_pct| for convergence (0.0005 = 5 basis points)
            min_skew: Minimum price for expensive side (0.80 = 80¢)
            max_cheap_price: Maximum price for cheap side (0.40 = 40¢)
            window_start_s: Start checking at this many seconds before expiry
            window_end_s: Stop checking at this many seconds before expiry
            logger: Optional logger
        """
        self.threshold_pct = threshold_pct
        self.min_skew = min_skew
        self.max_cheap_price = max_cheap_price
        self.window_start_s = window_start_s
        self.window_end_s = window_end_s
        self.logger = logger

    def _log(self, msg: str) -> None:
        if self.logger:
            self.logger.info(msg)

    def should_enter(
        self,
        time_remaining: float,
        oracle_snapshot: OracleSnapshot | None,
        orderbook: OrderBook,
    ) -> bool:
        """
        Check if ALL convergence conditions are met.

        Returns True if we should enter a convergence trade.
        """
        # 1. Time window check
        if time_remaining < self.window_end_s or time_remaining > self.window_start_s:
            return False

        # 2. Oracle data must exist with price_to_beat
        if oracle_snapshot is None:
            return False
        if oracle_snapshot.price_to_beat is None:
            return False
        if oracle_snapshot.delta_pct is None:
            return False

        # 3. Convergence: |delta_pct| <= threshold
        if abs(oracle_snapshot.delta_pct) > self.threshold_pct:
            return False

        # 4. Orderbook must have both sides
        if orderbook.best_ask_yes is None or orderbook.best_ask_no is None:
            return False

        # 5. Market skew: one side must be expensive (>= min_skew)
        expensive = max(orderbook.best_ask_yes, orderbook.best_ask_no)
        cheap = min(orderbook.best_ask_yes, orderbook.best_ask_no)

        if expensive < self.min_skew:
            return False

        # 6. Cheap side must be affordable (<= max_cheap_price)
        if cheap > self.max_cheap_price:
            return False

        return True

    def get_cheap_side(self, orderbook: OrderBook) -> tuple[str, float]:
        """
        Return (side, price) for the cheap/undervalued side.

        Call only after should_enter() returns True.

        Returns:
            ("YES", price) or ("NO", price) — the cheaper side
        """
        ask_yes = orderbook.best_ask_yes
        ask_no = orderbook.best_ask_no

        if ask_yes is None or ask_no is None:
            raise ValueError("Orderbook missing ask prices")

        if ask_yes <= ask_no:
            return ("YES", ask_yes)
        else:
            return ("NO", ask_no)

    def get_signal(
        self,
        time_remaining: float,
        oracle_snapshot: OracleSnapshot,
        orderbook: OrderBook,
    ) -> ConvergenceSignal | None:
        """
        Get full signal details if conditions are met.

        Returns ConvergenceSignal or None.
        """
        if not self.should_enter(time_remaining, oracle_snapshot, orderbook):
            return None

        ask_yes = orderbook.best_ask_yes
        ask_no = orderbook.best_ask_no
        if ask_yes is None or ask_no is None:
            return None

        if ask_yes <= ask_no:
            cheap_side, cheap_price = "YES", ask_yes
            expensive_side, expensive_price = "NO", ask_no
        else:
            cheap_side, cheap_price = "NO", ask_no
            expensive_side, expensive_price = "YES", ask_yes

        return ConvergenceSignal(
            cheap_side=cheap_side,
            cheap_price=cheap_price,
            expensive_side=expensive_side,
            expensive_price=expensive_price,
            delta_pct=abs(oracle_snapshot.delta_pct or 0),
            time_remaining=time_remaining,
            oracle_price=oracle_snapshot.price,
            price_to_beat=oracle_snapshot.price_to_beat or 0,
        )
