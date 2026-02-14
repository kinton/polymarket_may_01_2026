"""Adapter that bridges OrderbookWS (Level 2) to the LastSecondTrader's OrderBook (Level 1).

When USE_ORDERBOOK_WS=1, the trader uses OrderbookWS for:
- Auto-reconnect with exponential backoff
- Level 2 orderbook depth
- Ping/pong heartbeat management

The adapter translates Level 2 snapshots into Level 1 best bid/ask updates
that the existing OrderBook dataclass expects.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.clob_types import OrderBook
    from src.trading.orderbook_ws import OrderbookWS

logger = logging.getLogger(__name__)


class OrderbookWSAdapter:
    """Bridges OrderbookWS → OrderBook (Level 2 → Level 1 projection).

    Periodically reads best bid/ask from OrderbookWS and updates the
    trader's OrderBook dataclass so all existing trigger/stop-loss logic
    continues to work unchanged.
    """

    def __init__(
        self,
        ws: OrderbookWS,
        orderbook: OrderBook,
        token_id_yes: str,
        token_id_no: str,
        poll_interval: float = 0.1,
    ) -> None:
        self.ws = ws
        self.orderbook = orderbook
        self.token_id_yes = token_id_yes
        self.token_id_no = token_id_no
        self.poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self.last_sync_ts: float = 0.0
        self.sync_count: int = 0

    async def start(self) -> None:
        """Connect OrderbookWS, subscribe to tokens, start sync loop."""
        await self.ws.connect()
        await self.ws.subscribe(self.token_id_yes)
        await self.ws.subscribe(self.token_id_no)
        self._running = True
        self._task = asyncio.create_task(self._sync_loop())
        logger.info("OrderbookWSAdapter started (poll=%.2fs)", self.poll_interval)

    async def stop(self) -> None:
        """Stop sync loop and disconnect."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        await self.ws.disconnect()
        logger.info("OrderbookWSAdapter stopped (syncs=%d)", self.sync_count)

    def sync_once(self) -> None:
        """Sync Level 2 → Level 1 once (non-async, for manual/test use)."""
        # YES side
        yes_ob = self.ws.get_orderbook(self.token_id_yes)
        if yes_ob is not None:
            if yes_ob.asks:
                best = min(yes_ob.asks, key=lambda x: x[0])
                self.orderbook.best_ask_yes = best[0]
                self.orderbook.best_ask_yes_size = best[1]
            if yes_ob.bids:
                best = max(yes_ob.bids, key=lambda x: x[0])
                self.orderbook.best_bid_yes = best[0]
                self.orderbook.best_bid_yes_size = best[1]

        # NO side
        no_ob = self.ws.get_orderbook(self.token_id_no)
        if no_ob is not None:
            if no_ob.asks:
                best = min(no_ob.asks, key=lambda x: x[0])
                self.orderbook.best_ask_no = best[0]
                self.orderbook.best_ask_no_size = best[1]
            if no_ob.bids:
                best = max(no_ob.bids, key=lambda x: x[0])
                self.orderbook.best_bid_no = best[0]
                self.orderbook.best_bid_no_size = best[1]

        self.orderbook.update()
        self.last_sync_ts = time.time()
        self.sync_count += 1

    async def _sync_loop(self) -> None:
        """Periodically project Level 2 data into Level 1 OrderBook."""
        while self._running:
            try:
                self.sync_once()
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("OrderbookWSAdapter sync error")
                await asyncio.sleep(1.0)
