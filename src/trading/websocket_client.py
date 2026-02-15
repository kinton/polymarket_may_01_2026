"""WebSocket client for Polymarket CLOB market data streaming."""

import asyncio
import json
import logging
from typing import Any, Callable, Awaitable

import websockets

from src.clob_types import CLOB_WS_URL


# Constants
WS_STALE_SECONDS = 2.0
MAX_RECONNECTS = 3


class WebSocketClient:
    """Manages WebSocket connection to Polymarket CLOB."""

    WS_URL = CLOB_WS_URL

    def __init__(
        self,
        token_id_yes: str,
        token_id_no: str,
        market_name: str = "UNKNOWN",
        logger: logging.Logger | None = None,
    ):
        self.token_id_yes = token_id_yes
        self.token_id_no = token_id_no
        self.market_name = market_name
        self.logger = logger
        self.ws: websockets.WebSocketClientProtocol | None = None

    def _log(self, message: str) -> None:
        if self.logger:
            self.logger.info(message)
        else:
            print(message)

    async def connect(self) -> bool:
        """Connect to Polymarket WebSocket and subscribe to both YES and NO tokens."""
        max_attempts = MAX_RECONNECTS
        for attempt in range(max_attempts):
            try:
                self.ws = await websockets.connect(
                    self.WS_URL, ping_interval=20, ping_timeout=10
                )
                subscribe_msg = {
                    "assets_ids": [self.token_id_yes, self.token_id_no],
                    "type": "MARKET",
                }
                await self.ws.send(json.dumps(subscribe_msg))

                self._log("✓ WebSocket connected, subscribed to YES+NO tokens")
                return True

            except Exception as e:
                self._log(f"❌ WebSocket connection failed: {e}")
                if attempt < (max_attempts - 1):
                    await asyncio.sleep(2**attempt)
                else:
                    return False
        return False

    async def listen(
        self,
        on_update: Callable[[dict[str, Any]], Awaitable[None]],
        should_stop: Callable[[], bool],
        on_close: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Listen to WebSocket and process market updates until should_stop returns True."""
        if self.ws is None:
            self._log("❌ WebSocket not initialized")
            return
        try:
            async for message in self.ws:
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    continue

                if not data or (isinstance(data, list) and len(data) == 0):
                    continue

                if isinstance(data, list):
                    for update in data:
                        await on_update(update)
                else:
                    await on_update(data)

                if should_stop():
                    self._log(f"⏰ [{self.market_name}] Market closed")
                    if on_close:
                        try:
                            await on_close()
                        except Exception as e:
                            self._log(f"❌ [{self.market_name}] Error in on_close: {e}")
                    break

        except websockets.exceptions.ConnectionClosed:
            self._log(f"⚠️  [{self.market_name}] WebSocket connection closed")
        except Exception as e:
            self._log(f"❌ [{self.market_name}] Error in market listener: {e}")

    async def close(self) -> None:
        """Close the WebSocket connection."""
        if self.ws and not self.ws.closed:
            try:
                await asyncio.wait_for(self.ws.close(), timeout=5.0)
            except (asyncio.TimeoutError, Exception):
                pass
