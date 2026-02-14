"""WebSocket client for streaming Polymarket CLOB orderbook data."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

POLYMARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


@dataclass
class OrderbookSnapshot:
    """Snapshot of an orderbook for a single asset."""

    bids: list[tuple[float, float]] = field(default_factory=list)  # (price, size)
    asks: list[tuple[float, float]] = field(default_factory=list)


class OrderbookWS:
    """WebSocket client for streaming Polymarket CLOB orderbook.

    Supports connect/disconnect, subscribe to markets, auto-reconnect,
    and heartbeat/ping-pong.
    """

    def __init__(
        self,
        url: str = POLYMARKET_WS_URL,
        reconnect_delay: float = 2.0,
        max_reconnect_delay: float = 60.0,
        ping_interval: float = 30.0,
    ) -> None:
        self.url = url
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay
        self.ping_interval = ping_interval

        self._ws: object | None = None
        self._running = False
        self._subscriptions: set[str] = set()
        self._orderbooks: dict[str, OrderbookSnapshot] = {}
        self._recv_task: asyncio.Task[None] | None = None
        self._ping_task: asyncio.Task[None] | None = None
        self._current_delay = reconnect_delay

    async def connect(self) -> None:
        """Connect to the WebSocket server."""
        try:
            import websockets  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError("websockets package required: pip install websockets") from exc

        self._running = True
        self._ws = await websockets.connect(self.url)  # type: ignore[assignment]
        self._current_delay = self.reconnect_delay
        logger.info("Connected to %s", self.url)

        # Re-subscribe to any existing subscriptions
        for asset_id in self._subscriptions:
            await self._send_subscribe(asset_id)

        # Start background tasks
        self._recv_task = asyncio.create_task(self._recv_loop())
        self._ping_task = asyncio.create_task(self._ping_loop())

    async def disconnect(self) -> None:
        """Disconnect from the WebSocket server."""
        self._running = False

        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        self._recv_task = None

        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass
        self._ping_task = None

        if self._ws is not None:
            await self._ws.close()  # type: ignore[union-attr]
            self._ws = None

        logger.info("Disconnected")

    async def subscribe(self, asset_id: str) -> None:
        """Subscribe to orderbook updates for an asset."""
        self._subscriptions.add(asset_id)
        self._orderbooks.setdefault(asset_id, OrderbookSnapshot())
        if self._ws is not None:
            await self._send_subscribe(asset_id)

    async def _send_subscribe(self, asset_id: str) -> None:
        """Send subscribe message over websocket."""
        msg = json.dumps({
            "type": "subscribe",
            "channel": "book",
            "assets_ids": [asset_id],
        })
        await self._ws.send(msg)  # type: ignore[union-attr]
        logger.info("Subscribed to %s", asset_id)

    def get_best_bid(self, asset_id: str) -> float | None:
        """Get the best (highest) bid price for an asset."""
        ob = self._orderbooks.get(asset_id)
        if ob is None or not ob.bids:
            return None
        return max(b[0] for b in ob.bids)

    def get_best_ask(self, asset_id: str) -> float | None:
        """Get the best (lowest) ask price for an asset."""
        ob = self._orderbooks.get(asset_id)
        if ob is None or not ob.asks:
            return None
        return min(a[0] for a in ob.asks)

    def get_orderbook(self, asset_id: str) -> OrderbookSnapshot | None:
        """Get the full orderbook snapshot for an asset."""
        return self._orderbooks.get(asset_id)

    async def _recv_loop(self) -> None:
        """Background loop to receive and process messages."""
        while self._running:
            try:
                msg = await self._ws.recv()  # type: ignore[union-attr]
                self._handle_message(msg)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.warning("WebSocket receive error, reconnecting...")
                if self._running:
                    await self._reconnect()
                return

    def _handle_message(self, raw: str) -> None:
        """Parse and apply an orderbook message."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON message: %s", raw[:100])
            return

        msg_type = data.get("type", "")
        asset_id = data.get("asset_id", "")

        if msg_type == "book" and asset_id:
            ob = self._orderbooks.setdefault(asset_id, OrderbookSnapshot())
            if "bids" in data:
                ob.bids = [(float(b["price"]), float(b["size"])) for b in data["bids"]]
            if "asks" in data:
                ob.asks = [(float(a["price"]), float(a["size"])) for a in data["asks"]]
        elif msg_type == "price_change" and asset_id:
            ob = self._orderbooks.setdefault(asset_id, OrderbookSnapshot())
            for change in data.get("changes", []):
                side = change.get("side")
                price = float(change.get("price", 0))
                size = float(change.get("size", 0))
                if side == "BUY":
                    ob.bids = [(p, s) for p, s in ob.bids if p != price]
                    if size > 0:
                        ob.bids.append((price, size))
                elif side == "SELL":
                    ob.asks = [(p, s) for p, s in ob.asks if p != price]
                    if size > 0:
                        ob.asks.append((price, size))

    async def _ping_loop(self) -> None:
        """Send periodic pings to keep the connection alive."""
        while self._running:
            try:
                await asyncio.sleep(self.ping_interval)
                if self._ws is not None:
                    await self._ws.ping()  # type: ignore[union-attr]
            except asyncio.CancelledError:
                return
            except Exception:
                logger.warning("Ping failed")

    async def _reconnect(self) -> None:
        """Attempt to reconnect with exponential backoff."""
        if self._ws is not None:
            try:
                await self._ws.close()  # type: ignore[union-attr]
            except Exception:
                pass
            self._ws = None

        while self._running:
            logger.info("Reconnecting in %.1fs...", self._current_delay)
            await asyncio.sleep(self._current_delay)
            self._current_delay = min(self._current_delay * 2, self.max_reconnect_delay)

            try:
                import websockets  # type: ignore[import-untyped]

                self._ws = await websockets.connect(self.url)  # type: ignore[assignment]
                self._current_delay = self.reconnect_delay
                logger.info("Reconnected to %s", self.url)

                for asset_id in self._subscriptions:
                    await self._send_subscribe(asset_id)

                self._recv_task = asyncio.create_task(self._recv_loop())
                return
            except Exception:
                logger.warning("Reconnect failed, retrying...")
