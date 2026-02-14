"""Tests for centralized configuration module."""

import os
import pytest
from unittest.mock import patch

from src.config import TradingConfig, reload_config, _env_float, _env_int, _env_bool


class TestEnvHelpers:
    """Test env parsing helpers."""

    def test_env_float_default(self):
        assert _env_float("NONEXISTENT_VAR_12345", 3.14) == 3.14

    def test_env_float_override(self):
        with patch.dict(os.environ, {"TEST_FLOAT": "2.5"}):
            assert _env_float("TEST_FLOAT", 1.0) == 2.5

    def test_env_float_invalid(self):
        with patch.dict(os.environ, {"TEST_FLOAT": "not_a_number"}):
            assert _env_float("TEST_FLOAT", 1.0) == 1.0

    def test_env_int_default(self):
        assert _env_int("NONEXISTENT_VAR_12345", 42) == 42

    def test_env_int_override(self):
        with patch.dict(os.environ, {"TEST_INT": "99"}):
            assert _env_int("TEST_INT", 0) == 99

    def test_env_int_invalid(self):
        with patch.dict(os.environ, {"TEST_INT": "abc"}):
            assert _env_int("TEST_INT", 7) == 7

    def test_env_bool_default(self):
        assert _env_bool("NONEXISTENT_VAR_12345", True) is True
        assert _env_bool("NONEXISTENT_VAR_12345", False) is False

    def test_env_bool_true_values(self):
        for val in ("true", "True", "TRUE", "1", "yes", "YES"):
            with patch.dict(os.environ, {"TEST_BOOL": val}):
                assert _env_bool("TEST_BOOL", False) is True

    def test_env_bool_false_values(self):
        for val in ("false", "False", "0", "no", "NO", "anything"):
            with patch.dict(os.environ, {"TEST_BOOL": val}):
                assert _env_bool("TEST_BOOL", True) is False


class TestTradingConfig:
    """Test TradingConfig dataclass."""

    def test_defaults(self):
        cfg = TradingConfig()
        assert cfg.max_buy_price == 0.99
        assert cfg.min_buy_price == 0.85
        assert cfg.stop_loss_pct == 0.30
        assert cfg.max_total_trades_per_day == 100
        assert cfg.early_entry_enabled is True

    def test_env_override(self):
        env = {
            "MAX_BUY_PRICE": "0.95",
            "STOP_LOSS_PCT": "0.20",
            "MAX_TOTAL_TRADES_PER_DAY": "50",
            "EARLY_ENTRY_ENABLED": "false",
        }
        with patch.dict(os.environ, env):
            cfg = TradingConfig()
            assert cfg.max_buy_price == 0.95
            assert cfg.stop_loss_pct == 0.20
            assert cfg.max_total_trades_per_day == 50
            assert cfg.early_entry_enabled is False

    def test_frozen(self):
        cfg = TradingConfig()
        with pytest.raises(AttributeError):
            cfg.max_buy_price = 0.50  # type: ignore[misc]

    def test_reload_config(self):
        with patch.dict(os.environ, {"MIN_CONFIDENCE": "0.99"}):
            cfg = reload_config()
            assert cfg.min_confidence == 0.99

    def test_api_urls_default(self):
        cfg = TradingConfig()
        assert "gamma-api.polymarket.com" in cfg.gamma_api_url
        assert "ws-subscriptions-clob" in cfg.clob_ws_url

    def test_api_url_override(self):
        with patch.dict(os.environ, {"GAMMA_API_URL": "https://custom.api/search"}):
            cfg = TradingConfig()
            assert cfg.gamma_api_url == "https://custom.api/search"
