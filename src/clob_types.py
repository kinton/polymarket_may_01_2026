"""
Common type definitions and constants for trading system.

Constants are loaded from the unified TradingConfig (src/config.py).
They can be overridden via environment variables.
"""

from dataclasses import dataclass
from datetime import datetime

from src.config import config as _cfg


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


# Trading constants — sourced from TradingConfig (env vars override defaults)
# MAX_BUY_PRICE / MIN_BUY_PRICE removed — convergence uses its own thresholds
TRIGGER_THRESHOLD = _cfg.trigger_threshold
PRICE_THRESHOLD = _cfg.price_threshold
PRICE_TIE_EPS = _cfg.price_tie_eps
# MIN_CONFIDENCE removed — legacy strategy cleaned up

# Trade sizing constants
MIN_TRADE_USDC = _cfg.min_trade_usdc
MAX_TRADE_USDC = _cfg.max_trade_usdc
MAX_CAPITAL_PCT_PER_TRADE = _cfg.max_capital_pct_per_trade

# Liquidity filtering constants
MIN_ORDERBOOK_SIZE_USD = _cfg.min_orderbook_size_usd

# Stop-loss constants
STOP_LOSS_PCT = _cfg.stop_loss_pct
STOP_LOSS_ABSOLUTE = _cfg.stop_loss_absolute
TRAILING_STOP_PCT = _cfg.trailing_stop_pct
STOP_LOSS_CHECK_INTERVAL_S = _cfg.stop_loss_check_interval_s

# Take-profit constants
TAKE_PROFIT_PCT = _cfg.take_profit_pct
TAKE_PROFIT_CHECK_INTERVAL_S = _cfg.take_profit_check_interval_s

# Risk management limits
MAX_DAILY_LOSS_PCT = _cfg.max_daily_loss_pct
MAX_TOTAL_TRADES_PER_DAY = _cfg.max_total_trades_per_day

# API constants
GAMMA_API_URL = _cfg.gamma_api_url
CLOB_WS_URL = _cfg.clob_ws_url

# Exchange contract address for USDC
EXCHANGE_CONTRACT = _cfg.exchange_contract

# Alert rate limiting
ALERT_RATE_LIMIT_PER_MINUTE = _cfg.alert_rate_limit_per_minute

# Oracle Guard constants
MAX_STALE_S = _cfg.max_stale_s
MIN_ORACLE_POINTS = _cfg.min_oracle_points
MAX_VOL_PCT = _cfg.max_vol_pct
MIN_ABS_Z = _cfg.min_abs_z
MAX_REVERSAL_SLOPE = _cfg.max_reversal_slope

# Convergence strategy constants
CONVERGENCE_ENABLED = _cfg.convergence_enabled
CONVERGENCE_THRESHOLD_PCT = _cfg.convergence_threshold_pct
CONVERGENCE_MIN_SKEW = _cfg.convergence_min_skew
CONVERGENCE_MAX_CHEAP_PRICE = _cfg.convergence_max_cheap_price
CONVERGENCE_WINDOW_START_S = _cfg.convergence_window_start_s
CONVERGENCE_WINDOW_END_S = _cfg.convergence_window_end_s
CONVERGENCE_DISABLE_STOP_LOSS = _cfg.convergence_disable_stop_loss

# Early entry mode constants
# Early entry constants removed — legacy strategy cleaned up
