"""Tests for src/logging_config.py — rotating log handlers."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from src.logging_config import (
    get_log_dir,
    get_log_level,
    setup_bot_loggers,
    setup_logger,
)


@pytest.fixture(autouse=True)
def _clean_loggers():
    """Remove test loggers after each test."""
    yield
    for name in ("test_rot", "finder", "trader"):
        logger = logging.getLogger(name)
        logger.handlers.clear()


class TestGetLogDir:
    def test_default(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("LOG_DIR", raising=False)
        d = get_log_dir()
        assert d.exists()
        assert d.name == "log"

    def test_custom(self, tmp_path, monkeypatch):
        custom = tmp_path / "my_logs"
        monkeypatch.setenv("LOG_DIR", str(custom))
        d = get_log_dir()
        assert d == custom
        assert d.exists()


class TestGetLogLevel:
    @pytest.mark.parametrize("env_val,expected", [
        ("DEBUG", logging.DEBUG),
        ("WARNING", logging.WARNING),
        ("error", logging.ERROR),
    ])
    def test_levels(self, monkeypatch, env_val, expected):
        monkeypatch.setenv("LOG_LEVEL", env_val)
        assert get_log_level() == expected

    def test_default(self, monkeypatch):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        assert get_log_level() == logging.INFO


class TestSetupLogger:
    def test_creates_rotating_handler(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOG_DIR", str(tmp_path))
        monkeypatch.setenv("LOG_CONSOLE", "0")
        logger = setup_logger("test_rot", "test.log")
        assert len(logger.handlers) == 1
        assert isinstance(logger.handlers[0], RotatingFileHandler)

    def test_max_bytes_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOG_DIR", str(tmp_path))
        monkeypatch.setenv("LOG_MAX_BYTES", "5000")
        monkeypatch.setenv("LOG_CONSOLE", "0")
        logger = setup_logger("test_rot", "test.log")
        handler = logger.handlers[0]
        assert handler.maxBytes == 5000

    def test_backup_count_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOG_DIR", str(tmp_path))
        monkeypatch.setenv("LOG_BACKUP_COUNT", "3")
        monkeypatch.setenv("LOG_CONSOLE", "0")
        logger = setup_logger("test_rot", "test.log")
        handler = logger.handlers[0]
        assert handler.backupCount == 3

    def test_console_handler_added(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOG_DIR", str(tmp_path))
        monkeypatch.setenv("LOG_CONSOLE", "1")
        logger = setup_logger("test_rot", "test.log", console_prefix="[TEST]")
        assert len(logger.handlers) == 2  # file + console

    def test_console_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOG_DIR", str(tmp_path))
        monkeypatch.setenv("LOG_CONSOLE", "0")
        logger = setup_logger("test_rot", "test.log", console_prefix="[TEST]")
        assert len(logger.handlers) == 1  # file only

    def test_writes_to_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOG_DIR", str(tmp_path))
        monkeypatch.setenv("LOG_CONSOLE", "0")
        logger = setup_logger("test_rot", "test.log")
        logger.info("hello rotating world")
        logger.handlers[0].flush()
        content = (tmp_path / "test.log").read_text()
        assert "hello rotating world" in content

    def test_clears_existing_handlers(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOG_DIR", str(tmp_path))
        monkeypatch.setenv("LOG_CONSOLE", "0")
        setup_logger("test_rot", "test.log")
        setup_logger("test_rot", "test.log")  # second call
        logger = logging.getLogger("test_rot")
        assert len(logger.handlers) == 1


class TestSetupBotLoggers:
    def test_returns_three(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOG_DIR", str(tmp_path))
        monkeypatch.setenv("LOG_CONSOLE", "0")
        finder, trader, trader_file = setup_bot_loggers()
        assert finder.name == "finder"
        assert trader.name == "trader"
        assert isinstance(trader_file, Path)
        assert "trades-" in trader_file.name

    def test_finder_uses_rotating(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOG_DIR", str(tmp_path))
        monkeypatch.setenv("LOG_CONSOLE", "0")
        finder, _, _ = setup_bot_loggers()
        assert isinstance(finder.handlers[0], RotatingFileHandler)
