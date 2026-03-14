"""Strategy plugin registry and dynamic loader.

Usage:
    from strategies import discover_strategies, load_strategy

    discover_strategies()  # scan strategies/*.py, auto-register via @register
    strategy = load_strategy("convergence", "v1", threshold_pct=0.0005, ...)
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any

from strategies.base import BaseStrategy, MarketInfo, MarketTick, Signal

__all__ = [
    "BaseStrategy",
    "MarketInfo",
    "MarketTick",
    "Signal",
    "register",
    "load_strategy",
    "discover_strategies",
    "STRATEGY_REGISTRY",
]

logger = logging.getLogger(__name__)

# Global registry: "name/version" -> class
STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {}


def register(cls: type[BaseStrategy]) -> type[BaseStrategy]:
    """Class decorator — adds a strategy to the global registry."""
    key = f"{cls.name}/{cls.version}"
    if not cls.name or not cls.version:
        raise ValueError(
            f"Strategy class {cls.__name__} must define non-empty 'name' and 'version'"
        )
    if key in STRATEGY_REGISTRY:
        existing = STRATEGY_REGISTRY[key]
        if existing is not cls:
            raise ValueError(
                f"Duplicate strategy key '{key}': "
                f"{existing.__name__} vs {cls.__name__}"
            )
    STRATEGY_REGISTRY[key] = cls
    logger.debug("Registered strategy: %s -> %s", key, cls.__name__)
    return cls


def load_strategy(name: str, version: str, **kwargs: Any) -> BaseStrategy:
    """Instantiate a registered strategy by name/version.

    Extra kwargs are forwarded to the strategy __init__.
    Raises KeyError if not found.
    """
    key = f"{name}/{version}"
    if key not in STRATEGY_REGISTRY:
        available = ", ".join(sorted(STRATEGY_REGISTRY)) or "(none)"
        raise KeyError(
            f"Strategy '{key}' not found. Available: {available}"
        )
    cls = STRATEGY_REGISTRY[key]
    return cls(**kwargs)


def discover_strategies(path: str | None = None) -> int:
    """Scan a directory for strategy modules and import them.

    Importing triggers @register decorators. Returns the number of
    newly discovered strategies.

    Args:
        path: Directory to scan. Defaults to the ``strategies/`` package dir.
    """
    if path is None:
        pkg_dir = Path(__file__).parent
    else:
        pkg_dir = Path(path)

    before = len(STRATEGY_REGISTRY)

    for py_file in sorted(pkg_dir.glob("*.py")):
        module_name = py_file.stem
        if module_name.startswith("_"):
            continue  # skip __init__, __pycache__, etc.
        if module_name == "base":
            continue  # skip the ABC module

        qualified = f"strategies.{module_name}"
        try:
            importlib.import_module(qualified)
        except Exception:
            logger.exception("Failed to import strategy module: %s", qualified)

    discovered = len(STRATEGY_REGISTRY) - before
    if discovered:
        logger.info(
            "Discovered %d strategy(ies): %s",
            discovered,
            ", ".join(sorted(STRATEGY_REGISTRY)),
        )
    return discovered
