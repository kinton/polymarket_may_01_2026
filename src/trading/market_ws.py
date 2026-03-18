"""Abstract base for WebSocket connections to Polymarket market data.

Both the L1 (CLOB price feed) and L2 (orderbook depth) WebSocket
implementations satisfy this interface so callers can treat them uniformly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable


class MarketWebSocket(ABC):
    """Unified interface for a single-market WebSocket feed."""

    @abstractmethod
    async def connect(self) -> None:
        """Open the WebSocket connection and subscribe to the market."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Close the connection and cancel background tasks."""
        ...

    @abstractmethod
    def set_on_update(self, cb: Callable) -> None:
        """Register a callback invoked on every market update."""
        ...
