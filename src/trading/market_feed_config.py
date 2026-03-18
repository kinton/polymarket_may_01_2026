"""Configuration dataclass for market infrastructure (WebSocket + oracle).

MarketFeedConfig bundles all per-market infrastructure parameters so they
can be passed as a single object instead of N individual kwargs through
TradingBotRunner → LastSecondTrader.

Strategy-specific parameters (tickers, thresholds, filters) live inside
each concrete strategy class, NOT here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MarketFeedConfig:
    """Infrastructure configuration for one market connection."""

    # Oracle (RTDS / EventPage)
    oracle_enabled: bool = True
    oracle_guard_enabled: bool = True
    oracle_min_points: int = 4
    oracle_window_s: float = 60.0

    # Level 2 orderbook WebSocket (optional upgrade from default L1)
    use_level2_ws: bool = False
    orderbook_ws_poll_interval: float = 0.1

    # Book-state logging throttle
    book_log_every_s: float = 1.0
    book_log_every_s_final: float = 0.5
