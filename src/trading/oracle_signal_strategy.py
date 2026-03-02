"""
Oracle Signal Trading Strategy for Polymarket "Up or Down" markets.

Core idea: When the oracle clearly moves away from price_to_beat,
it reveals the likely outcome. If the market hasn't fully priced this in,
we buy the correct side at a discount.

Unlike convergence (which waits for oracle ≈ beat), this strategy exploits
DIVERGENCE: oracle has moved, but market lags behind.

Entry conditions (ALL must be met):
1. Time window: configurable seconds before expiry (default 5-60s)
2. Oracle signal: |delta_pct| >= min_delta (oracle clearly moved, default 0.10%)
3. Market mispricing: correct side ask <= max_entry_price (default 0.55)
4. Oracle data is fresh and has price_to_beat
5. Oracle direction is mapped to YES/NO (up_side/down_side known)

Example:
  Oracle: BTC dropped 0.2% below beat → outcome = DOWN
  Market: DOWN/NO still at $0.50 (should be ~$0.65-0.70)
  → Buy NO at $0.50, expected value ~$0.70 = +40% edge
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.oracle_tracker import OracleSnapshot
from src.clob_types import OrderBook


@dataclass(frozen=True)
class OracleSignal:
    """Details of an oracle signal entry."""
    side: str                 # "YES" or "NO" — the side to buy
    side_label: str           # "UP" or "DOWN" — human readable
    price: float              # ask price we'd pay
    delta_pct: float          # oracle delta from beat (signed)
    abs_delta_pct: float      # |delta_pct|
    estimated_fair_value: float  # rough estimate of fair value
    edge_pct: float           # (fair_value - price) / price
    time_remaining: float
    oracle_price: float
    price_to_beat: float


class OracleSignalStrategy:
    """
    Detects when oracle diverges from beat but market hasn't caught up.
    Recommends buying the oracle-favored side.
    """

    def __init__(
        self,
        min_delta_pct: float = 0.0010,       # 10 basis points = 0.10%
        max_entry_price: float = 0.55,        # don't buy above 55¢
        min_edge_pct: float = 0.10,           # require ≥10% estimated edge
        window_start_s: float = 60.0,         # start at 60s before expiry
        window_end_s: float = 5.0,            # stop at 5s before expiry
        # Fair value estimation: maps delta_pct ranges to fair values
        # At 10bp delta, outcome is ~60% certain; at 50bp, ~80%
        delta_to_fair: list[tuple[float, float]] | None = None,
        logger: logging.Logger | None = None,
    ):
        self.min_delta_pct = min_delta_pct
        self.max_entry_price = max_entry_price
        self.min_edge_pct = min_edge_pct
        self.window_start_s = window_start_s
        self.window_end_s = window_end_s
        self.logger = logger

        # Default delta→fair value mapping (conservative estimates)
        # (min_abs_delta_pct, estimated_fair_value_of_correct_side)
        self.delta_to_fair = delta_to_fair or [
            (0.0010, 0.60),   # 10bp → ~60% chance
            (0.0020, 0.65),   # 20bp → ~65%
            (0.0050, 0.75),   # 50bp → ~75%
            (0.0100, 0.85),   # 100bp → ~85%
            (0.0200, 0.92),   # 200bp → ~92%
        ]
        # Sort by delta ascending
        self.delta_to_fair.sort(key=lambda x: x[0])

    def _log(self, msg: str) -> None:
        if self.logger:
            self.logger.info(msg)

    def _estimate_fair_value(self, abs_delta_pct: float) -> float:
        """
        Estimate fair value of the correct side based on oracle delta.

        Uses linear interpolation between configured breakpoints.
        """
        if not self.delta_to_fair:
            return 0.60

        # Below minimum → use first value
        if abs_delta_pct <= self.delta_to_fair[0][0]:
            return self.delta_to_fair[0][1]

        # Above maximum → use last value
        if abs_delta_pct >= self.delta_to_fair[-1][0]:
            return self.delta_to_fair[-1][1]

        # Linear interpolation
        for i in range(len(self.delta_to_fair) - 1):
            d0, fv0 = self.delta_to_fair[i]
            d1, fv1 = self.delta_to_fair[i + 1]
            if d0 <= abs_delta_pct <= d1:
                t = (abs_delta_pct - d0) / (d1 - d0) if d1 != d0 else 0
                return fv0 + t * (fv1 - fv0)

        return self.delta_to_fair[-1][1]

    def get_oracle_side(
        self,
        oracle_snapshot: OracleSnapshot,
        up_side: str | None,
        down_side: str | None,
    ) -> tuple[str, str] | None:
        """
        Determine which side oracle favors.

        Returns:
            (side, label) e.g. ("YES", "UP") or ("NO", "DOWN"), or None
        """
        if oracle_snapshot.delta is None:
            return None
        if up_side is None or down_side is None:
            return None

        if oracle_snapshot.delta >= 0:
            return (up_side, "UP")
        else:
            return (down_side, "DOWN")

    def should_enter(
        self,
        time_remaining: float,
        oracle_snapshot: OracleSnapshot | None,
        orderbook: OrderBook,
        up_side: str | None,
        down_side: str | None,
    ) -> bool:
        """Check if all entry conditions are met."""
        signal = self.get_signal(
            time_remaining, oracle_snapshot, orderbook, up_side, down_side
        )
        return signal is not None

    def get_signal(
        self,
        time_remaining: float,
        oracle_snapshot: OracleSnapshot | None,
        orderbook: OrderBook,
        up_side: str | None,
        down_side: str | None,
    ) -> OracleSignal | None:
        """
        Evaluate entry conditions and return signal if all met.

        Returns OracleSignal or None.
        """
        # 1. Time window
        if time_remaining < self.window_end_s or time_remaining > self.window_start_s:
            return None

        # 2. Oracle data must exist
        if oracle_snapshot is None:
            return None
        if oracle_snapshot.price_to_beat is None:
            return None
        if oracle_snapshot.delta_pct is None:
            return None

        # 3. Oracle signal strength
        abs_delta = abs(oracle_snapshot.delta_pct)
        if abs_delta < self.min_delta_pct:
            return None

        # 4. Determine correct side
        oracle_result = self.get_oracle_side(oracle_snapshot, up_side, down_side)
        if oracle_result is None:
            return None
        side, side_label = oracle_result

        # 5. Get ask price for the correct side
        if orderbook.best_ask_yes is None or orderbook.best_ask_no is None:
            return None

        if side == "YES":
            entry_price = orderbook.best_ask_yes
        else:
            entry_price = orderbook.best_ask_no

        # 6. Entry price check
        if entry_price > self.max_entry_price:
            return None

        # 7. Estimate fair value and edge
        fair_value = self._estimate_fair_value(abs_delta)
        if entry_price >= fair_value:
            return None  # no edge

        edge_pct = (fair_value - entry_price) / entry_price

        # 8. Minimum edge check
        if edge_pct < self.min_edge_pct:
            return None

        return OracleSignal(
            side=side,
            side_label=side_label,
            price=entry_price,
            delta_pct=oracle_snapshot.delta_pct,
            abs_delta_pct=abs_delta,
            estimated_fair_value=fair_value,
            edge_pct=edge_pct,
            time_remaining=time_remaining,
            oracle_price=oracle_snapshot.price,
            price_to_beat=oracle_snapshot.price_to_beat,
        )
