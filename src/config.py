"""
Centralized configuration via Pydantic BaseSettings.

All trading constants can be overridden via env vars.
Env var names match the field names in UPPER_CASE (e.g., MAX_BUY_PRICE=0.95).
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


# Keep legacy helpers for backward compatibility (tests import them)
import os


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


class TradingConfig(BaseSettings):
    """All trading constants in one place, overridable via env vars."""

    # --- Price thresholds ---
    trigger_threshold: float = Field(default=30.0)
    price_threshold: float = Field(default=0.50)  # Winning side threshold (bid > this = winning)
    price_tie_eps: float = Field(default=1e-6)

    # --- Trade sizing ---
    min_trade_usdc: float = Field(default=1.00)
    max_trade_usdc: float = Field(default=10.00)
    max_capital_pct_per_trade: float = Field(default=0.05)

    # --- Liquidity ---
    min_orderbook_size_usd: float = Field(default=100.0)

    # --- Stop-loss ---
    stop_loss_pct: float = Field(default=0.30)
    stop_loss_absolute: float = Field(default=0.80)
    trailing_stop_pct: float = Field(default=0.05)
    stop_loss_check_interval_s: float = Field(default=1.0)

    # --- Take-profit ---
    take_profit_pct: float = Field(default=0.10)
    take_profit_check_interval_s: float = Field(default=1.0)

    # --- Risk management ---
    max_daily_loss_pct: float = Field(default=0.20)
    max_total_trades_per_day: int = Field(default=100)

    # --- Alert ---
    alert_rate_limit_per_minute: int = Field(default=10)

    # --- Oracle Guard ---
    max_stale_s: float = Field(default=20.0)
    min_oracle_points: int = Field(default=4)
    max_vol_pct: float = Field(default=0.002)
    min_abs_z: float = Field(default=0.75)
    max_reversal_slope: float = Field(default=0.0)

    # --- Convergence strategy ---
    convergence_enabled: bool = Field(default=True)
    convergence_threshold_pct: float = Field(default=0.0005)  # 5 basis points
    convergence_min_skew: float = Field(default=0.80)  # expensive side >= 80¢
    convergence_max_cheap_price: float = Field(default=0.40)  # max price for cheap side
    convergence_window_start_s: float = Field(default=60.0)  # start at 60s before expiry
    convergence_window_end_s: float = Field(default=20.0)  # stop at 20s before expiry
    convergence_disable_stop_loss: bool = Field(default=True)  # hold until resolution

    # --- Early entry (removed — legacy strategy) ---

    # --- API URLs ---
    gamma_api_url: str = Field(default="https://gamma-api.polymarket.com/public-search")
    clob_ws_url: str = Field(default="wss://ws-subscriptions-clob.polymarket.com/ws/market")
    exchange_contract: str = Field(default="0xC5d563A36AE78145C45a50134d48A1215220f80a")

    # --- API credentials ---
    clob_host: str = Field(default="https://clob.polymarket.com")
    private_key: str = Field(default="")
    polygon_chain_id: int = Field(default=137)
    polymarket_proxy_address: str = Field(default="")
    clob_api_key: str = Field(default="")
    clob_secret: str = Field(default="")
    clob_passphrase: str = Field(default="")

    # --- Notifications ---
    telegram_bot_token: str = Field(default="")
    telegram_chat_id: str = Field(default="")
    slack_webhook_url: str = Field(default="")

    # --- Health check ---
    health_host: str = Field(default="0.0.0.0")
    health_port: int = Field(default=8080)

    # --- Misc ---
    log_dir: str = Field(default="logs")
    log_level: str = Field(default="INFO")
    replay_dir: str = Field(default="data/replays")
    use_orderbook_ws: bool = Field(default=False)

    # --- Gamma finder ---
    gamma_min_request_interval: float = Field(default=0.35)
    gamma_max_retries: int = Field(default=3)
    gamma_backoff_base: float = Field(default=0.5)
    gamma_backoff_max: float = Field(default=4.0)
    market_queries: str = Field(default="")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "frozen": True,
    }


# Singleton — import and use this everywhere
config = TradingConfig()


def reload_config() -> TradingConfig:
    """Re-read env vars and return a fresh config (useful for tests)."""
    global config  # noqa: PLW0603
    config = TradingConfig()
    return config
