"""StrategyRegistry — maps tickers to strategy registrations.

Strategies declare their own tickers via ``BaseStrategy.tickers()``.
YAML only provides runtime params (mode, size).  The registry is the
single source of truth for which strategies want which tickers.

Usage::

    registry = StrategyRegistry()
    registry.register(StrategyRegistration(
        name="convergence",
        version="v1",
        mode="test",
        size=1.0,
        tickers=["BTC", "ETH", "SOL"],
    ))

    btc_regs = registry.runners_for("BTC")
    all_tickers = registry.all_tickers()
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StrategyRegistration:
    """One strategy entry from strategies.yaml, enriched with ticker universe."""

    name: str
    version: str
    mode: str           # "test" | "live"
    size: float
    tickers: list[str]  # from strategy.tickers() or YAML universe override
    dry_run: bool = True


class StrategyRegistry:
    """Maps ticker → list of StrategyRegistration.

    Populated at startup from YAML config + strategy class tickers().
    Used by MarketOrchestrator (and SharedFinder) to determine which
    strategies should run for a given market.
    """

    def __init__(self) -> None:
        self._registrations: list[StrategyRegistration] = []

    def register(self, reg: StrategyRegistration) -> None:
        """Add a strategy registration."""
        self._registrations.append(reg)

    def runners_for(self, ticker: str) -> list[StrategyRegistration]:
        """Return all registrations interested in the given ticker."""
        ticker_upper = ticker.upper()
        return [r for r in self._registrations if ticker_upper in r.tickers]

    def all_tickers(self) -> list[str]:
        """Union of all tickers across all registrations (deduplicated, sorted)."""
        seen: set[str] = set()
        result: list[str] = []
        for reg in self._registrations:
            for t in reg.tickers:
                if t not in seen:
                    seen.add(t)
                    result.append(t)
        return sorted(result)

    def all_registrations(self) -> list[StrategyRegistration]:
        """Return all registrations."""
        return list(self._registrations)
