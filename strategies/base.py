"""Base strategy interface for the plugin architecture.

All trading strategies inherit from BaseStrategy and implement
observe(), decide(), and reset().
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from src.clob_types import OrderBook
from src.oracle_tracker import OracleSnapshot


@dataclass(frozen=True)
class MarketTick:
    """Snapshot of market state passed to strategies each tick."""

    time_remaining: float                          # seconds until market close
    oracle_snapshot: OracleSnapshot | None
    orderbook: OrderBook


@dataclass(frozen=True)
class Signal:
    """Strategy output: a trading signal."""

    side: str                          # "YES" | "NO"
    price: float                       # entry price (ask of cheap side)
    disable_stop_loss: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    # metadata stores strategy-specific data:
    #   convergence_rate, side_consistency, reason, expensive_price, ...


class BaseStrategy(ABC):
    """Abstract base for all trading strategies.

    Subclasses must define class-level identity attributes and implement
    the three abstract methods.
    """

    # --- identity (override in subclass) ---
    name: str = ""
    version: str = ""
    default_tickers: list[str] = []
    default_min_price: float = 0.0
    default_max_price: float = 0.35

    @abstractmethod
    def observe(self, tick: MarketTick) -> None:
        """Accumulate one tick of market data."""
        ...

    @abstractmethod
    def decide(self, tick: MarketTick) -> Signal | None:
        """Evaluate accumulated data and optionally return a signal."""
        ...

    @abstractmethod
    def reset(self) -> None:
        """Reset internal state for a new market cycle."""
        ...

    def get_signal(self, tick: MarketTick) -> Signal | None:
        """Convenience: observe + decide in one call.

        Returns a Signal when the strategy is ready to trade, None otherwise.
        """
        self.observe(tick)
        return self.decide(tick)
