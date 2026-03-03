"""
Convergence Trading Strategy for Polymarket "Up or Down" markets.

Core idea: When the oracle price converges with price_to_beat (within threshold),
the real odds are ~50/50. But the market may still show a skew — one side
priced high, one side cheap. We buy the CHEAP side because it's undervalued.

Entry conditions (ALL must be met):
1. Time window: 20-60 seconds before expiry
2. Oracle convergence: |delta_pct| <= threshold (2 basis points — TIGHT)
3. Market skew: expensive side >= 0.75
4. Cheap side price <= 0.30 (great risk/reward for a ~50/50 bet)
5. Oracle data is fresh with price_to_beat
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
    side: str                 # "YES" or "NO" — side to buy (cheap side)
    side_label: str           # "UP" or "DOWN"
    price: float              # ask price of the side we're buying
    expensive_price: float    # ask price of expensive side
    delta_pct: float          # signed oracle delta_pct
    abs_delta_pct: float      # |delta_pct|
    time_remaining: float
    oracle_price: float
    price_to_beat: float


class ConvergenceStrategy:
    """
    Detects convergence opportunities — buys the CHEAP side when
    oracle is at the beat (50/50 odds) but market still shows skew.
    """

    # If oracle is MORE than this against the cheap side, skip
    DEFAULT_MAX_AGAINST_BP = 0.0001  # 1bp

    def __init__(
        self,
        threshold_pct: float = 0.0003,    # 3bp convergence
        min_skew: float = 0.75,            # expensive side >= 75¢
        max_cheap_price: float = 0.35,     # only buy at 35¢ or less
        max_against_pct: float = DEFAULT_MAX_AGAINST_BP,  # max oracle delta AGAINST cheap side
        window_start_s: float = 60.0,
        window_end_s: float = 20.0,
        logger: logging.Logger | None = None,
    ):
        self.threshold_pct = threshold_pct
        self.min_skew = min_skew
        self.max_cheap_price = max_cheap_price
        self.max_against_pct = max_against_pct
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
        """Check if all entry conditions are met."""
        return self.get_signal(time_remaining, oracle_snapshot, orderbook) is not None

    def get_signal(
        self,
        time_remaining: float,
        oracle_snapshot: OracleSnapshot | None,
        orderbook: OrderBook,
    ) -> ConvergenceSignal | None:
        """
        Evaluate and return signal if all conditions met.

        Buy the CHEAP side when oracle ≈ beat (50/50).
        """
        # 1. Time window
        if time_remaining < self.window_end_s or time_remaining > self.window_start_s:
            return None

        # 2. Oracle data
        if oracle_snapshot is None:
            return None
        if oracle_snapshot.price_to_beat is None:
            return None
        if oracle_snapshot.delta_pct is None:
            return None

        # 3. Convergence check — oracle MUST be very close to beat
        abs_delta = abs(oracle_snapshot.delta_pct)
        if abs_delta > self.threshold_pct:
            return None

        # 4. Orderbook
        if orderbook.best_ask_yes is None or orderbook.best_ask_no is None:
            return None

        # 5. Find the cheap side
        if orderbook.best_ask_yes <= orderbook.best_ask_no:
            buy_side = "YES"
            side_label = "UP"
            buy_price = orderbook.best_ask_yes
            expensive_price = orderbook.best_ask_no
        else:
            buy_side = "NO"
            side_label = "DOWN"
            buy_price = orderbook.best_ask_no
            expensive_price = orderbook.best_ask_yes

        # 6. Price checks
        if buy_price > self.max_cheap_price:
            return None
        if expensive_price < self.min_skew:
            return None

        # 7. Oracle "not against" filter
        # If cheap side is NO (DOWN), oracle delta should not be too positive (UP)
        # If cheap side is YES (UP), oracle delta should not be too negative (DOWN)
        delta = oracle_snapshot.delta_pct
        if buy_side == "NO" and delta > self.max_against_pct:
            # Oracle says UP but we want to buy DOWN — skip
            return None
        if buy_side == "YES" and delta < -self.max_against_pct:
            # Oracle says DOWN but we want to buy UP — skip
            return None

        return ConvergenceSignal(
            side=buy_side,
            side_label=side_label,
            price=buy_price,
            expensive_price=expensive_price,
            delta_pct=oracle_snapshot.delta_pct,
            abs_delta_pct=abs_delta,
            time_remaining=time_remaining,
            oracle_price=oracle_snapshot.price,
            price_to_beat=oracle_snapshot.price_to_beat or 0,
        )

    def get_cheap_side(self, orderbook: OrderBook) -> tuple[str, float]:
        """Return the cheap side from orderbook."""
        ask_yes = orderbook.best_ask_yes
        ask_no = orderbook.best_ask_no
        if ask_yes is None or ask_no is None:
            raise ValueError("Orderbook missing ask prices")
        if ask_yes <= ask_no:
            return ("YES", ask_yes)
        return ("NO", ask_no)
