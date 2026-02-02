"""
Common type definitions and constants for trading system.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Market:
    """Represents a Polymarket market."""

    condition_id: str
    token_id_yes: str
    token_id_no: str
    end_time: datetime
    title: str
    slug: str


@dataclass
class OrderBook:
    """Current order book state for a market."""

    best_ask_yes: Optional[float] = None
    best_bid_yes: Optional[float] = None
    best_ask_no: Optional[float] = None
    best_bid_no: Optional[float] = None
    sum_asks: Optional[float] = None  # YES ask + NO ask (should be ~1.0)

    def update(self) -> None:
        """Recalculate derived values."""
        if self.best_ask_yes is not None and self.best_ask_no is not None:
            self.sum_asks = self.best_ask_yes + self.best_ask_no


# Trading constants
BUY_PRICE = 0.99
TRIGGER_THRESHOLD = 120.0  # Start attempting trades when ≤ 120s remain (was 90s)
PRICE_THRESHOLD = 0.50
PRICE_TIE_EPS = 1e-6

# API constants
GAMMA_API_URL = "https://gamma-api.polymarket.com/public-search"
CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
