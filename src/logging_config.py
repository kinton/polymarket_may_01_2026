"""Centralized logging configuration with file rotation.

Provides rotating file handlers to prevent unbounded log growth.
All settings can be overridden via environment variables.

Environment variables:
    LOG_DIR              — log directory (default: "log")
    LOG_MAX_BYTES        — max bytes per log file before rotation (default: 10MB)
    LOG_BACKUP_COUNT     — number of rotated files to keep (default: 5)
    LOG_LEVEL            — log level: DEBUG, INFO, WARNING, ERROR (default: INFO)
    LOG_CONSOLE          — enable console output: 1/0 (default: 1)
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Defaults
_DEFAULT_LOG_DIR = "log"
_DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_DEFAULT_BACKUP_COUNT = 5
_DEFAULT_LOG_LEVEL = "INFO"
_DEFAULT_LOG_CONSOLE = True

_LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"


def _env_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes")


def get_log_dir() -> Path:
    """Return the log directory, creating it if needed."""
    log_dir = Path(os.environ.get("LOG_DIR", _DEFAULT_LOG_DIR))
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def get_log_level() -> int:
    """Return the configured log level."""
    level_name = os.environ.get("LOG_LEVEL", _DEFAULT_LOG_LEVEL).upper()
    return getattr(logging, level_name, logging.INFO)


def setup_logger(
    name: str,
    log_file: str | Path,
    *,
    console_prefix: str | None = None,
    max_bytes: int | None = None,
    backup_count: int | None = None,
) -> logging.Logger:
    """Create or reconfigure a logger with rotating file handler.

    Args:
        name: Logger name (e.g. "finder", "trader").
        log_file: Path to the log file (relative to LOG_DIR or absolute).
        console_prefix: If set, add a console handler with this prefix
                        (e.g. "[FINDER]"). Set to None to disable console.
        max_bytes: Override max bytes per file. Defaults to LOG_MAX_BYTES env or 10MB.
        backup_count: Override backup count. Defaults to LOG_BACKUP_COUNT env or 5.

    Returns:
        Configured logger instance.
    """
    logger = logging.getLogger(name)
    logger.setLevel(get_log_level())

    # Clear existing handlers to prevent accumulation on restarts
    if logger.hasHandlers():
        logger.handlers.clear()

    # Resolve max_bytes / backup_count
    if max_bytes is None:
        max_bytes = _env_int("LOG_MAX_BYTES", _DEFAULT_MAX_BYTES)
    if backup_count is None:
        backup_count = _env_int("LOG_BACKUP_COUNT", _DEFAULT_BACKUP_COUNT)

    # Rotating file handler
    log_path = Path(log_file)
    if not log_path.is_absolute():
        log_path = get_log_dir() / log_path

    rotating_handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    rotating_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    logger.addHandler(rotating_handler)

    # Console handler (optional)
    console_enabled = _env_bool("LOG_CONSOLE", _DEFAULT_LOG_CONSOLE)
    if console_enabled and console_prefix is not None:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(
            logging.Formatter(f"%(asctime)s - {console_prefix} - %(message)s")
        )
        logger.addHandler(console_handler)

    return logger


def setup_bot_loggers() -> tuple[logging.Logger, logging.Logger, Path]:
    """Setup the standard finder + trader loggers for the bot.

    Returns:
        (finder_logger, trader_logger, trader_log_file)
    """
    log_dir = get_log_dir()

    finder_logger = setup_logger(
        "finder",
        "finder.log",
        console_prefix="[FINDER]",
    )

    # Trader gets a timestamped file for per-run separation
    run_ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    trader_log_file = log_dir / f"trades-{run_ts}.log"

    trader_logger = setup_logger(
        "trader",
        trader_log_file,
        console_prefix="[TRADER]",
    )

    return finder_logger, trader_logger, trader_log_file
