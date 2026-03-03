"""
Convergence Trading Strategy for Polymarket "Up or Down" markets.

Core idea: When the oracle price converges with price_to_beat (within threshold),
the real odds are ~50/50. But the market may still show a skew.

CRITICAL RULE: We ONLY buy the side that the oracle SUPPORTS (or is neutral).
If oracle delta is negative (price below beat → DOWN likely), buy DOWN/NO side.
If oracle delta is positive (price above beat → UP likely), buy UP/YES side.
If oracle is truly neutral (within 1bp), buy the cheaper side.

Never buy AGAINST the oracle direction just because it's "cheap" —
it's cheap for a reason.

Entry conditions (ALL must be met):
1. Time window: 20-60 seconds before expiry
2. Oracle convergence: |delta_pct| <= threshold (3 basis points)
3. Market skew: expensive side >= 0.75
4. Oracle-favored side price <= 0.30 (great risk/reward)
5. Oracle data is fresh with price_to_beat
6. Direction alignment: buy side must match oracle lean (or oracle neutral)
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
    side: str                 # "YES" or "NO" — side to buy
    side_label: str           # "UP" or "DOWN"
    price: float              # ask price of the side we're buying
    expensive_price: float    # ask price of expensive side
    delta_pct: float          # signed oracle delta_pct
    abs_delta_pct: float      # |delta_pct|
    time_remaining: float
    oracle_price: float
    price_to_beat: float
    direction: str            # "oracle_up", "oracle_down", or "neutral"


class ConvergenceStrategy:
    """
    Detects convergence opportunities — buys the oracle-favored side
    when it's still cheap on the orderbook.
    """

    # Oracle direction thresholds
    NEUTRAL_THRESHOLD = 0.0001  # 1bp — truly 50/50

    def __init__(
        self,
        threshold_pct: float = 0.0003,    # 3bp convergence (tighter than before)
        min_skew: float = 0.75,            # expensive side >= 75¢
        max_cheap_price: float = 0.30,     # only buy at 30¢ or less
        window_start_s: float = 60.0,
        window_end_s: float = 20.0,
        logger: logging.Logger | None = None,
    ):
        self.threshold_pct = threshold_pct
        self.min_skew = min_skew
        self.max_cheap_price = max_cheap_price
        self.window_start_s = window_start_s
        self.window_end_s = window_end_s
        self.logger = logger

    def _log(self, msg: str) -> None:
        if self.logger:
            self.logger.info(msg)

    def _get_oracle_direction(
        self, snapshot: OracleSnapshot
    ) -> tuple[str, str, str]:
        """Determine oracle direction.

        Returns:
            (favored_side, side_label, direction_tag)
            e.g. ("YES", "UP", "oracle_up") or ("NO", "DOWN", "oracle_down")
            or ("NEUTRAL", "NEUTRAL", "neutral") if within 1bp
        """
        if snapshot.delta_pct is None:
            return ("NEUTRAL", "NEUTRAL", "neutral")

        if snapshot.delta_pct > self.NEUTRAL_THRESHOLD:
            # Price above beat → UP likely → YES wins
            return ("YES", "UP", "oracle_up")
        elif snapshot.delta_pct < -self.NEUTRAL_THRESHOLD:
            # Price below beat → DOWN likely → NO wins
            return ("NO", "DOWN", "oracle_down")
        else:
            return ("NEUTRAL", "NEUTRAL", "neutral")

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

        Key change: we buy the oracle-favored side, not just the cheap side.
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

        # 3. Convergence check
        abs_delta = abs(oracle_snapshot.delta_pct)
        if abs_delta > self.threshold_pct:
            return None

        # 4. Orderbook
        if orderbook.best_ask_yes is None or orderbook.best_ask_no is None:
            return None

        # 5. Determine oracle direction
        favored_side, side_label, direction = self._get_oracle_direction(oracle_snapshot)

        # 6. Choose which side to buy
        if direction == "neutral":
            # Truly neutral — buy the cheaper side (classic convergence)
            if orderbook.best_ask_yes <= orderbook.best_ask_no:
                buy_side, buy_price = "YES", orderbook.best_ask_yes
                side_label = "UP"
                expensive_price = orderbook.best_ask_no
            else:
                buy_side, buy_price = "NO", orderbook.best_ask_no
                side_label = "DOWN"
                expensive_price = orderbook.best_ask_yes
        else:
            # Oracle has a lean — buy the favored side
            buy_side = favored_side
            if buy_side == "YES":
                buy_price = orderbook.best_ask_yes
                expensive_price = orderbook.best_ask_no
            else:
                buy_price = orderbook.best_ask_no
                expensive_price = orderbook.best_ask_yes

        # 7. Price checks
        if buy_price > self.max_cheap_price:
            return None
        if expensive_price < self.min_skew:
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
            direction=direction,
        )

    def get_cheap_side(self, orderbook: OrderBook) -> tuple[str, float]:
        """Legacy compat: return the cheap side from orderbook.
        NOTE: Prefer get_signal() which includes direction logic.
        """
        ask_yes = orderbook.best_ask_yes
        ask_no = orderbook.best_ask_no
        if ask_yes is None or ask_no is None:
            raise ValueError("Orderbook missing ask prices")
        if ask_yes <= ask_no:
            return ("YES", ask_yes)
        return ("NO", ask_no)
