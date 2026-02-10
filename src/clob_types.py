"""
Common type definitions and constants for trading system.
"""

from dataclasses import dataclass
from datetime import datetime


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

    best_ask_yes: float | None = None
    best_bid_yes: float | None = None
    best_ask_yes_size: float | None = None
    best_bid_yes_size: float | None = None
    best_ask_no: float | None = None
    best_bid_no: float | None = None
    best_ask_no_size: float | None = None
    best_bid_no_size: float | None = None
    sum_asks: float | None = None  # YES ask + NO ask (should be ~1.0)

    def update(self) -> None:
        """Recalculate derived values."""
        if self.best_ask_yes is not None and self.best_ask_no is not None:
            self.sum_asks = self.best_ask_yes + self.best_ask_no


# Trading constants
BUY_PRICE = 0.99
TRIGGER_THRESHOLD = 30.0  # Start attempting trades when ≤ s remain (was 60s)
PRICE_THRESHOLD = 0.85
PRICE_TIE_EPS = 1e-6
MIN_CONFIDENCE = 0.75  # Only buy if winning side has ≥75% confidence (bid/ask ≥ 0.75)
MIN_TRADE_USDC = 1.00  # Minimum trade size in USDC

# Stop-loss constants (CRITICAL!)
STOP_LOSS_PCT = 0.30  # Exit if price drops 30% from entry
STOP_LOSS_ABSOLUTE = 0.95  # Exit if price drops below this absolute value
TRAILING_STOP_PCT = 0.05  # Trailing stop: move stop up 5% when price moves in favor
STOP_LOSS_CHECK_INTERVAL_S = 1.0  # Check stop-loss every 1 second

# API constants
GAMMA_API_URL = "https://gamma-api.polymarket.com/public-search"
CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
