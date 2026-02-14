"""
Centralized configuration via environment variables.

All trading constants can be overridden via env vars.
Env var names match the constant names (e.g., MAX_BUY_PRICE=0.95).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_float(name: str, default: float) -> float:
    """Read a float from env, falling back to *default*."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    """Read an int from env, falling back to *default*."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    """Read a bool from env (true/1/yes → True, false/0/no → False)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes")


@dataclass(frozen=True)
class TradingConfig:
    """All trading constants in one place, overridable via env."""

    # --- Price thresholds ---
    max_buy_price: float = field(default_factory=lambda: _env_float("MAX_BUY_PRICE", 0.99))
    min_buy_price: float = field(default_factory=lambda: _env_float("MIN_BUY_PRICE", 0.85))
    trigger_threshold: float = field(default_factory=lambda: _env_float("TRIGGER_THRESHOLD", 30.0))
    price_threshold: float = field(default_factory=lambda: _env_float("PRICE_THRESHOLD", 0.85))
    price_tie_eps: float = field(default_factory=lambda: _env_float("PRICE_TIE_EPS", 1e-6))
    min_confidence: float = field(default_factory=lambda: _env_float("MIN_CONFIDENCE", 0.85))

    # --- Trade sizing ---
    min_trade_usdc: float = field(default_factory=lambda: _env_float("MIN_TRADE_USDC", 1.00))
    max_trade_usdc: float = field(default_factory=lambda: _env_float("MAX_TRADE_USDC", 10.00))
    max_capital_pct_per_trade: float = field(default_factory=lambda: _env_float("MAX_CAPITAL_PCT_PER_TRADE", 0.05))

    # --- Liquidity ---
    min_orderbook_size_usd: float = field(default_factory=lambda: _env_float("MIN_ORDERBOOK_SIZE_USD", 100.0))

    # --- Stop-loss ---
    stop_loss_pct: float = field(default_factory=lambda: _env_float("STOP_LOSS_PCT", 0.30))
    stop_loss_absolute: float = field(default_factory=lambda: _env_float("STOP_LOSS_ABSOLUTE", 0.80))
    trailing_stop_pct: float = field(default_factory=lambda: _env_float("TRAILING_STOP_PCT", 0.05))
    stop_loss_check_interval_s: float = field(default_factory=lambda: _env_float("STOP_LOSS_CHECK_INTERVAL_S", 1.0))

    # --- Take-profit ---
    take_profit_pct: float = field(default_factory=lambda: _env_float("TAKE_PROFIT_PCT", 0.10))
    take_profit_check_interval_s: float = field(default_factory=lambda: _env_float("TAKE_PROFIT_CHECK_INTERVAL_S", 1.0))

    # --- Risk management ---
    max_daily_loss_pct: float = field(default_factory=lambda: _env_float("MAX_DAILY_LOSS_PCT", 0.10))
    max_total_trades_per_day: int = field(default_factory=lambda: _env_int("MAX_TOTAL_TRADES_PER_DAY", 100))

    # --- Alert ---
    alert_rate_limit_per_minute: int = field(default_factory=lambda: _env_int("ALERT_RATE_LIMIT_PER_MINUTE", 10))

    # --- Oracle Guard ---
    max_stale_s: float = field(default_factory=lambda: _env_float("MAX_STALE_S", 20.0))
    min_oracle_points: int = field(default_factory=lambda: _env_int("MIN_ORACLE_POINTS", 4))
    max_vol_pct: float = field(default_factory=lambda: _env_float("MAX_VOL_PCT", 0.002))
    min_abs_z: float = field(default_factory=lambda: _env_float("MIN_ABS_Z", 0.75))
    max_reversal_slope: float = field(default_factory=lambda: _env_float("MAX_REVERSAL_SLOPE", 0.0))

    # --- Early entry ---
    early_entry_enabled: bool = field(default_factory=lambda: _env_bool("EARLY_ENTRY_ENABLED", True))
    early_entry_confidence_threshold: float = field(default_factory=lambda: _env_float("EARLY_ENTRY_CONFIDENCE_THRESHOLD", 0.90))
    early_entry_start_time_s: float = field(default_factory=lambda: _env_float("EARLY_ENTRY_START_TIME_S", 600.0))
    early_entry_end_time_s: float = field(default_factory=lambda: _env_float("EARLY_ENTRY_END_TIME_S", 60.0))

    # --- API URLs (not typically overridden, but available) ---
    gamma_api_url: str = field(default_factory=lambda: os.environ.get("GAMMA_API_URL", "https://gamma-api.polymarket.com/public-search"))
    clob_ws_url: str = field(default_factory=lambda: os.environ.get("CLOB_WS_URL", "wss://ws-subscriptions-clob.polymarket.com/ws/market"))
    exchange_contract: str = field(default_factory=lambda: os.environ.get("EXCHANGE_CONTRACT", "0xC5d563A36AE78145C45a50134d48A1215220f80a"))


# Singleton — import and use this everywhere
config = TradingConfig()


def reload_config() -> TradingConfig:
    """Re-read env vars and return a fresh config (useful for tests)."""
    global config  # noqa: PLW0603
    config = TradingConfig()
    return config
