"""Base strategy interface for the plugin architecture.

All trading strategies inherit from BaseStrategy and implement
observe(), decide(), reset(), and market_filter().
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from src.clob_types import OrderBook
from src.oracle_tracker import OracleSnapshot


@dataclass(frozen=True)
class MarketInfo:
    """Market metadata available at discovery time (before orderbook)."""

    condition_id: str
    ticker: str            # "BTC", "ETH", "SOL"
    title: str
    end_time_utc: str
    minutes_until_end: float
    token_id_yes: str
    token_id_no: str
    slug: str = ""


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
    the four abstract methods.
    """

    # --- identity (override in subclass) ---
    name: str = ""
    version: str = ""

    @abstractmethod
    def market_filter(self, market: MarketInfo) -> bool:
        """Does this strategy want to trade this market?"""
        ...

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

    def configure(self, **kwargs: Any) -> None:
        """Optional: override runtime parameters (e.g. from CLI)."""
        pass

    @classmethod
    def tickers(cls) -> list[str]:
        """Tickers this strategy wants to trade.

        Default implementation: derive from market_filter() via SUPPORTED_TICKERS
        if present, otherwise return empty list (caller must configure via YAML).
        Override in subclasses that declare a static universe.
        """
        supported = getattr(cls, "SUPPORTED_TICKERS", None)
        if supported:
            return list(supported)
        return []

    def get_signal(self, tick: MarketTick) -> Signal | None:
        """Convenience: observe + decide in one call.

        Returns a Signal when the strategy is ready to trade, None otherwise.
        """
        self.observe(tick)
        return self.decide(tick)
