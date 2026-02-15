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
MAX_BUY_PRICE = 0.90  # Maximum price to buy — sweet spot for risk/reward
MIN_BUY_PRICE = 0.60  # Minimum price — skip 50/50 noise below this
TRIGGER_THRESHOLD = 30.0  # Start attempting trades when ≤ s remain (was 60s)
PRICE_THRESHOLD = 0.85  # Legacy name, use MIN_BUY_PRICE
PRICE_TIE_EPS = 1e-6
MIN_CONFIDENCE = 0.85  # Only buy if winning side has ≥85% confidence (ask ≥ 0.85)

# Trade sizing constants (dynamic sizing: min of hard constants vs 25% of balance)
MIN_TRADE_USDC = 1.00  # Hard minimum trade size in USDC (Polymarket minimum)
MAX_TRADE_USDC = 10.00  # Hard maximum trade size in USDC
MAX_CAPITAL_PCT_PER_TRADE = 0.05  # Maximum 5% of capital per trade

# Liquidity filtering constants
MIN_ORDERBOOK_SIZE_USD = 100.0  # Minimum total orderbook liquidity (bids+asks) in USDC

# Stop-loss constants (CRITICAL!)
STOP_LOSS_PCT = 0.30  # Exit if price drops 30% from entry
STOP_LOSS_ABSOLUTE = 0.80  # Exit if price drops below this absolute value (safety floor, not trigger)
TRAILING_STOP_PCT = 0.05  # Trailing stop: move stop up 5% when price moves in favor
STOP_LOSS_CHECK_INTERVAL_S = 1.0  # Check stop-loss every 1 second

# Take-profit constants
TAKE_PROFIT_PCT = 0.10  # Exit if price rises 10% from entry
TAKE_PROFIT_CHECK_INTERVAL_S = 1.0  # Check take-profit every 1 second

# Risk management limits (CRITICAL!)
MAX_DAILY_LOSS_PCT = 0.20  # Stop if lost 20% in a day (for $10 capital: $2 max loss)
MAX_TOTAL_TRADES_PER_DAY = 100  # Limit total trades per day (increased from 20 to 100 per Konstantin request)

# API constants
GAMMA_API_URL = "https://gamma-api.polymarket.com/public-search"
CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# Exchange contract address for USDC
EXCHANGE_CONTRACT = "0xC5d563A36AE78145C45a50134d48A1215220f80a"

# Alert rate limiting
ALERT_RATE_LIMIT_PER_MINUTE = 10  # Max alerts per minute per channel

# Oracle Guard constants
MAX_STALE_S = 20.0  # Maximum oracle data staleness in seconds
MIN_ORACLE_POINTS = 4  # Minimum data points required for oracle tracking
MAX_VOL_PCT = 0.002  # Maximum acceptable volatility percentage
MIN_ABS_Z = 0.75  # Minimum absolute z-score threshold
MAX_REVERSAL_SLOPE = 0.0  # Maximum reversal slope (disabled by default)

# Early entry mode constants
EARLY_ENTRY_ENABLED = False  # Disabled — vulnerable to manipulation before trigger
EARLY_ENTRY_CONFIDENCE_THRESHOLD = 0.90  # Require 90% confidence for early entry
EARLY_ENTRY_START_TIME_S = 600.0  # Start early entry 10 minutes before close (600s)
EARLY_ENTRY_END_TIME_S = 60.0  # Stop early entry 60 seconds before close
