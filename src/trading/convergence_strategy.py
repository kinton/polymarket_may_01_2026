"""DEPRECATED: Use strategies.convergence_v1 instead.

This module is a backward-compatibility shim. All logic has moved to
strategies/convergence_v1.py as part of the plugin architecture.

The ConvergenceStrategy class wraps ConvergenceV1 with the old
(time_remaining, oracle_snapshot, orderbook) call signatures so that
existing tests and any residual imports continue to work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.oracle_tracker import OracleSnapshot
from src.clob_types import OrderBook
from strategies.base import MarketTick, Signal
from strategies.convergence_v1 import ConvergenceV1


@dataclass(frozen=True)
class ConvergenceSignal:
    """Legacy signal type — wraps Signal.metadata for backward compat."""
    side: str
    side_label: str
    price: float
    expensive_price: float
    delta_pct: float
    abs_delta_pct: float
    time_remaining: float
    oracle_price: float
    price_to_beat: float
    observations: int = 0
    convergence_rate: float = 0.0
    side_consistency: float = 0.0

    @classmethod
    def from_signal(cls, sig: Signal) -> ConvergenceSignal:
        """Convert a plugin Signal to the legacy ConvergenceSignal."""
        m = sig.metadata
        return cls(
            side=sig.side,
            side_label=m.get("side_label", ""),
            price=sig.price,
            expensive_price=m.get("expensive_price", 0.0),
            delta_pct=m.get("delta_pct", 0.0),
            abs_delta_pct=m.get("abs_delta_pct", 0.0),
            time_remaining=m.get("time_remaining", 0.0),
            oracle_price=m.get("oracle_price", 0.0),
            price_to_beat=m.get("price_to_beat", 0.0),
            observations=m.get("observations", 0),
            convergence_rate=m.get("convergence_rate", 0.0),
            side_consistency=m.get("side_consistency", 0.0),
        )


class ConvergenceStrategy:
    """Deprecated wrapper around ConvergenceV1 with old call signatures."""

    # Expose class constants for tests that reference them
    DEFAULT_MAX_AGAINST_BP = ConvergenceV1.DEFAULT_MAX_AGAINST_BP
    MIN_OBSERVATIONS = ConvergenceV1.MIN_OBSERVATIONS
    MIN_CONVERGENCE_RATE = ConvergenceV1.MIN_CONVERGENCE_RATE
    MIN_SIDE_CONSISTENCY = ConvergenceV1.MIN_SIDE_CONSISTENCY

    def __init__(self, **kwargs: Any) -> None:
        self._inner = ConvergenceV1(**kwargs)

    def reset(self) -> None:
        self._inner.reset()

    def observe(
        self,
        time_remaining: float,
        oracle_snapshot: OracleSnapshot | None,
        orderbook: OrderBook,
    ) -> None:
        tick = MarketTick(time_remaining=time_remaining,
                          oracle_snapshot=oracle_snapshot,
                          orderbook=orderbook)
        self._inner.observe(tick)

    def should_decide(self, time_remaining: float) -> bool:
        return self._inner.should_decide(time_remaining)

    def decide(
        self,
        time_remaining: float,
        oracle_snapshot: OracleSnapshot | None,
        orderbook: OrderBook,
    ) -> ConvergenceSignal | None:
        tick = MarketTick(time_remaining=time_remaining,
                          oracle_snapshot=oracle_snapshot,
                          orderbook=orderbook)
        sig = self._inner.decide(tick)
        if sig is None:
            return None
        return ConvergenceSignal.from_signal(sig)

    def get_signal(
        self,
        time_remaining: float,
        oracle_snapshot: OracleSnapshot | None,
        orderbook: OrderBook,
    ) -> ConvergenceSignal | None:
        tick = MarketTick(time_remaining=time_remaining,
                          oracle_snapshot=oracle_snapshot,
                          orderbook=orderbook)
        sig = self._inner.get_signal(tick)
        if sig is None:
            return None
        return ConvergenceSignal.from_signal(sig)

    def get_cheap_side(self, orderbook: OrderBook) -> tuple[str, float]:
        return self._inner.get_cheap_side(orderbook)
