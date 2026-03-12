"""
Convergence Trading Strategy for Polymarket "Up or Down" markets.

Core idea (May strategy):
  When |current_price - target| < 5bp, the outcome is ~50/50.
  If the market shows a strong skew (cheap side ≤ 35¢), buy the cheap side.
  That's it. Direction within 5bp is noise — price skew is the edge, not delta sign.

V2 — Accumulate-and-trigger:
Instead of buying on the first convergence tick, we accumulate evidence
and buy AS SOON AS accumulated data meets ALL quality thresholds.

Observation window (180s → 20s before expiry):
  - Collect oracle snapshots + orderbook snapshots every tick
  - Track: delta_pct, cheap side, cheap price, expensive price

Trigger (any tick after min_observations reached):
  - Need minimum N observations
  - ≥30% of ticks show convergence (|delta| < threshold)
  - ≥70% of observations agree on cheap side (consistency)
  - Median cheap price ≤ max_cheap_price
  - Oracle not strongly against cheap side (on median)

Buys early when evidence is clear, waits longer when noisy.
No arbitrary fixed decision time.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
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
    observations: int = 0     # how many observations backed this decision
    convergence_rate: float = 0.0  # % of observations within threshold
    side_consistency: float = 0.0  # % of observations agreeing on side


@dataclass
class _Observation:
    """Single tick observation during accumulation."""
    timestamp: float
    time_remaining: float
    delta_pct: float
    abs_delta_pct: float
    cheap_side: str           # "YES" or "NO"
    cheap_price: float
    expensive_price: float
    oracle_price: float
    price_to_beat: float


class ConvergenceStrategy:
    """
    V2: Accumulate observations, then decide once.
    
    Two phases:
    1. observe() — called every tick during observation window (60s→10s)
    2. decide() — called once at decision time (≤10s), returns signal or None
    """

    # REMOVED: direction filter within convergence zone is noise.
    # Within 5bp = ~50/50; cheap side payoff dominates regardless of delta sign.
    DEFAULT_MAX_AGAINST_BP = float("inf")  # disabled: buy cheap side regardless

    # Minimum observations needed for a decision
    MIN_OBSERVATIONS = 5

    # Minimum % of observations that show convergence
    MIN_CONVERGENCE_RATE = 0.40  # 40% of ticks must show convergence

    # Minimum % of observations agreeing on which side is cheap
    MIN_SIDE_CONSISTENCY = 0.70  # 70% must agree on same cheap side

    def __init__(
        self,
        threshold_pct: float = 0.0003,    # 3bp convergence
        min_skew: float = 0.75,            # expensive side >= 75¢
        max_cheap_price: float = 0.35,     # only buy at 35¢ or less
        min_cheap_price: float = 0.0,      # disabled — convergence check is the real filter
        max_against_pct: float = DEFAULT_MAX_AGAINST_BP,
        window_start_s: float = 200.0,    # observe from ~3.3 min before close
        window_end_s: float = 20.0,
        min_observations: int = MIN_OBSERVATIONS,
        min_convergence_rate: float = MIN_CONVERGENCE_RATE,
        min_side_consistency: float = MIN_SIDE_CONSISTENCY,
        decision_time_s: float | None = None,  # deprecated, kept for compat
        logger: logging.Logger | None = None,
    ):
        self.threshold_pct = threshold_pct
        self.min_skew = min_skew
        self.max_cheap_price = max_cheap_price
        self.min_cheap_price = min_cheap_price
        self.max_against_pct = max_against_pct
        self.window_start_s = window_start_s
        self.window_end_s = window_end_s
        self.min_observations = min_observations
        self.min_convergence_rate = min_convergence_rate
        self.min_side_consistency = min_side_consistency
        self.decision_time_s = decision_time_s  # if set, forces fixed decision point
        self.logger = logger

        # Accumulation state (reset per market cycle)
        self._observations: list[_Observation] = []
        self._decided: bool = False
        self._total_ticks: int = 0  # all ticks in window, not just converging

    def reset(self) -> None:
        """Reset state for a new market cycle."""
        self._observations = []
        self._decided = False
        self._total_ticks = 0

    def _log(self, msg: str) -> None:
        if self.logger:
            self.logger.info(msg)

    def observe(
        self,
        time_remaining: float,
        oracle_snapshot: OracleSnapshot | None,
        orderbook: OrderBook,
    ) -> None:
        """
        Called every tick during observation window.
        Collects data for later decision.
        """
        # Only observe during window
        if time_remaining < self.window_end_s or time_remaining > self.window_start_s:
            return

        self._total_ticks += 1

        # Need oracle data
        if oracle_snapshot is None:
            return
        if oracle_snapshot.price_to_beat is None or oracle_snapshot.delta_pct is None:
            return

        # Need orderbook
        if orderbook.best_ask_yes is None or orderbook.best_ask_no is None:
            return

        # Determine cheap side
        if orderbook.best_ask_yes <= orderbook.best_ask_no:
            cheap_side = "YES"
            cheap_price = orderbook.best_ask_yes
            expensive_price = orderbook.best_ask_no
        else:
            cheap_side = "NO"
            cheap_price = orderbook.best_ask_no
            expensive_price = orderbook.best_ask_yes

        abs_delta = abs(oracle_snapshot.delta_pct)

        self._observations.append(_Observation(
            timestamp=0,  # not needed for logic
            time_remaining=time_remaining,
            delta_pct=oracle_snapshot.delta_pct,
            abs_delta_pct=abs_delta,
            cheap_side=cheap_side,
            cheap_price=cheap_price,
            expensive_price=expensive_price,
            oracle_price=oracle_snapshot.price,
            price_to_beat=oracle_snapshot.price_to_beat,
        ))

    def should_decide(self, time_remaining: float) -> bool:
        """Check if we have enough data to attempt a decision."""
        if self._decided:
            return False
        if len(self._observations) < self.min_observations:
            return False
        # If fixed decision time set, wait for it
        if self.decision_time_s is not None:
            return time_remaining <= self.decision_time_s
        # Otherwise: ready to evaluate as soon as we have enough observations
        return True

    def decide(
        self,
        time_remaining: float,
        oracle_snapshot: OracleSnapshot | None,
        orderbook: OrderBook,
    ) -> ConvergenceSignal | None:
        """
        Evaluate accumulated observations. Returns signal if all thresholds met.
        
        Can be called multiple times — only marks _decided=True on success.
        Failed checks allow re-evaluation with more data on next tick.
        """
        if self._decided:
            return None

        obs = self._observations
        total_ticks = max(self._total_ticks, 1)

        if len(obs) < self.min_observations:
            return None

        # 1. Convergence rate: how many observations had |delta| < threshold?
        converging = [o for o in obs if o.abs_delta_pct <= self.threshold_pct]
        convergence_rate = len(converging) / total_ticks

        if convergence_rate < self.min_convergence_rate:
            return None

        # 2. Side consistency: which side was cheap most often?
        yes_count = sum(1 for o in obs if o.cheap_side == "YES")
        no_count = len(obs) - yes_count
        if yes_count >= no_count:
            dominant_side = "YES"
            dominant_label = "UP"
            side_consistency = yes_count / len(obs)
        else:
            dominant_side = "NO"
            dominant_label = "DOWN"
            side_consistency = no_count / len(obs)

        if side_consistency < self.min_side_consistency:
            return None

        # 3. Use only observations matching dominant side for price analysis
        side_obs = [o for o in obs if o.cheap_side == dominant_side]

        # Median prices (more robust than mean against spikes)
        median_cheap = statistics.median([o.cheap_price for o in side_obs])
        median_expensive = statistics.median([o.expensive_price for o in side_obs])
        median_delta = statistics.median([o.delta_pct for o in side_obs])

        # 4. Price filters
        if median_cheap > self.max_cheap_price:
            return None

        if median_cheap < self.min_cheap_price:
            self._log(
                f"SKIP: median_cheap={median_cheap:.2f} < min_cheap={self.min_cheap_price:.2f} "
                f"(market already decided, not worth buying)"
            )
            return None

        if median_expensive < self.min_skew:
            return None

        # 5. Oracle "not against" filter (on median delta)
        if dominant_side == "NO" and median_delta > self.max_against_pct:
            return None
        if dominant_side == "YES" and median_delta < -self.max_against_pct:
            return None

        # 6. Use CURRENT orderbook for actual execution price
        if orderbook.best_ask_yes is None or orderbook.best_ask_no is None:
            return None

        if dominant_side == "YES":
            buy_price = orderbook.best_ask_yes
            expensive_now = orderbook.best_ask_no
        else:
            buy_price = orderbook.best_ask_no
            expensive_now = orderbook.best_ask_yes

        # Re-check current price (might have changed since observation)
        if buy_price > self.max_cheap_price:
            return None

        if buy_price < self.min_cheap_price:
            self._log(
                f"SKIP: buy_price={buy_price:.2f} < min_cheap={self.min_cheap_price:.2f}"
            )
            return None

        oracle_price = oracle_snapshot.price if oracle_snapshot else side_obs[-1].oracle_price
        price_to_beat = oracle_snapshot.price_to_beat if oracle_snapshot and oracle_snapshot.price_to_beat else side_obs[-1].price_to_beat
        current_delta = oracle_snapshot.delta_pct if oracle_snapshot and oracle_snapshot.delta_pct is not None else median_delta

        # Warn if oracle has drifted outside the convergence zone at decision time.
        # This can happen when the oracle converged during the observation window but
        # moved away before we decided. Does NOT block the trade (historical evidence
        # still valid), but logs a visible warning for post-trade review.
        if oracle_snapshot is not None and oracle_snapshot.delta_pct is not None:
            current_abs_delta_pct = abs(oracle_snapshot.delta_pct)
            if current_abs_delta_pct > self.threshold_pct:
                self._log(
                    f"⚠️  CONVERGENCE: oracle outside zone at decision time — "
                    f"current delta_pct={current_abs_delta_pct * 100:.4f}% > threshold={self.threshold_pct * 100:.4f}% "
                    f"(delta_usd={oracle_snapshot.delta:+.4f}, z={oracle_snapshot.zscore:.3f})"
                    if oracle_snapshot.zscore is not None else
                    f"⚠️  CONVERGENCE: oracle outside zone at decision time — "
                    f"current delta_pct={current_abs_delta_pct * 100:.4f}% > threshold={self.threshold_pct * 100:.4f}% "
                    f"(delta_usd={oracle_snapshot.delta:+.4f})"
                )

        # All checks passed — commit to this decision
        self._decided = True

        self._log(
            f"CONVERGENCE TRIGGER: {dominant_side} ({dominant_label}) | "
            f"obs={len(obs)}/{total_ticks} ticks | "
            f"conv_rate={convergence_rate:.0%} | "
            f"side_consistency={side_consistency:.0%} | "
            f"median_cheap={median_cheap:.2f} | "
            f"median_delta={median_delta*100:+.4f}% | "
            f"buy_price={buy_price:.2f} | "
            f"t={time_remaining:.1f}s"
        )

        return ConvergenceSignal(
            side=dominant_side,
            side_label=dominant_label,
            price=buy_price,
            expensive_price=expensive_now,
            delta_pct=current_delta,
            abs_delta_pct=abs(current_delta),
            time_remaining=time_remaining,
            oracle_price=oracle_price,
            price_to_beat=price_to_beat,
            observations=len(obs),
            convergence_rate=convergence_rate,
            side_consistency=side_consistency,
        )

    # ── Legacy interface (for backward compat) ──

    def get_signal(
        self,
        time_remaining: float,
        oracle_snapshot: OracleSnapshot | None,
        orderbook: OrderBook,
    ) -> ConvergenceSignal | None:
        """
        Main interface — observe and evaluate on every tick.

        Each tick:
        1. Accumulate observation (if in window)
        2. If enough evidence accumulated → evaluate and potentially trigger

        Returns signal when ready, None otherwise.
        """
        if self._decided:
            return None

        # Accumulate observation
        self.observe(time_remaining, oracle_snapshot, orderbook)

        # Try to decide if we have enough data
        if self.should_decide(time_remaining):
            return self.decide(time_remaining, oracle_snapshot, orderbook)

        return None

    def get_cheap_side(self, orderbook: OrderBook) -> tuple[str, float]:
        """Return the cheap side from orderbook."""
        ask_yes = orderbook.best_ask_yes
        ask_no = orderbook.best_ask_no
        if ask_yes is None or ask_no is None:
            raise ValueError("Orderbook missing ask prices")
        if ask_yes <= ask_no:
            return ("YES", ask_yes)
        return ("NO", ask_no)
