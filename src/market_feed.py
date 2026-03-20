"""MarketFeed — owns WebSocket, oracle, and orderbook for ONE market.

Extracts the infrastructure concerns (WS connection, oracle streaming,
orderbook management) out of LastSecondTrader so they can be shared across
multiple strategy runners without opening duplicate connections.

Subscribers register async callbacks:
    feed.subscribe(async_callback)
    # callback receives (tick: MarketTick)

The feed calls every subscriber on each WS update AND once per second
(if data is fresh) to guarantee strategies evaluate at least 1 tick/second.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable

import aiohttp

from src.clob_types import PRICE_TIE_EPS, OrderBook
from src.market_parser import (
    extract_best_ask_with_size_from_book,
    extract_best_bid_with_size_from_book,
)
from src.oracle_tracker import OracleSnapshot
from src.trading.market_feed_config import MarketFeedConfig
from src.trading.oracle_guard_manager import OracleGuardManager
from src.trading.orderbook_tracker import OrderbookTracker
from src.trading.orderbook_ws import OrderbookWS
from src.trading.orderbook_ws_adapter import OrderbookWSAdapter
from src.trading.websocket_client import WebSocketClient
from src.updown_prices import EventPageClient, RtdsClient
from strategies.base import MarketInfo, MarketTick


# Type alias for subscriber callbacks
TickCallback = Callable[[MarketTick], Awaitable[None]]


def _extract_market_name(title: str | None) -> str:
    """Extract short market name from title for logging."""
    if not title:
        return "UNKNOWN"
    title_lower = title.lower()
    if "bitcoin" in title_lower or "btc" in title_lower:
        return "BTC"
    elif "ethereum" in title_lower or "eth" in title_lower:
        return "ETH"
    elif "solana" in title_lower or "sol" in title_lower:
        return "SOL"
    elif "xrp" in title_lower or "ripple" in title_lower:
        return "XRP"
    return title.split()[0][:8].upper() if title else "UNKNOWN"


class MarketFeed:
    """Owns WS + oracle + orderbook for one active market.

    Subscribers (e.g. LastSecondTrader) call ``subscribe(cb)`` and receive
    a ``MarketTick`` on every WS update and every ≥1 s heartbeat tick.
    """

    WS_STALE_SECONDS = 2.0

    def __init__(
        self,
        market: MarketInfo,
        feed_config: MarketFeedConfig,
        logger: logging.Logger | None = None,
    ) -> None:
        self._market = market
        self._config = feed_config
        self._logger = logger
        self._market_name = _extract_market_name(market.title)

        # Parse end_time for time_remaining calculations
        end_str = market.end_time_utc.replace(" UTC", "+00:00")
        self._end_time = datetime.fromisoformat(end_str)

        # Orderbook
        self._orderbook = OrderBook()
        self._ob_tracker = OrderbookTracker(
            orderbook=self._orderbook,
            token_id_yes=market.token_id_yes,
            token_id_no=market.token_id_no,
            tie_epsilon=PRICE_TIE_EPS,
        )

        # Oracle guard
        end_iso = self._end_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        self._oracle_guard = OracleGuardManager(
            title=market.title,
            market_name=self._market_name,
            end_time=end_iso,
            enabled=feed_config.oracle_enabled,
            guard_enabled=feed_config.oracle_guard_enabled,
            min_points=feed_config.oracle_min_points,
            window_s=feed_config.oracle_window_s,
        )

        # L1 WebSocket client (default)
        self._ws_client = WebSocketClient(
            token_id_yes=market.token_id_yes,
            token_id_no=market.token_id_no,
            market_name=self._market_name,
            logger=logger,
        )

        # Optional L2 orderbook WS adapter
        self._orderbook_ws_adapter: OrderbookWSAdapter | None = None
        if feed_config.use_level2_ws:
            ws_client = OrderbookWS()
            self._orderbook_ws_adapter = OrderbookWSAdapter(
                ws=ws_client,
                orderbook=self._orderbook,
                token_id_yes=market.token_id_yes,
                token_id_no=market.token_id_no,
                poll_interval=feed_config.orderbook_ws_poll_interval,
            )

        # Subscriber callbacks
        self._subscribers: list[TickCallback] = []

        # State
        self.last_ws_update_ts: float = 0.0
        self._shutting_down = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def subscribe(self, cb: TickCallback) -> None:
        """Register a callback to receive ticks from this feed."""
        if cb not in self._subscribers:
            self._subscribers.append(cb)

    def unsubscribe(self, cb: TickCallback) -> None:
        self._subscribers = [s for s in self._subscribers if s is not cb]

    @property
    def market_name(self) -> str:
        return self._market_name

    @property
    def orderbook(self) -> OrderBook:
        return self._orderbook

    @property
    def oracle_guard(self) -> OracleGuardManager:
        return self._oracle_guard

    @property
    def oracle_snapshot(self) -> OracleSnapshot | None:
        return self._oracle_guard.snapshot

    @property
    def winning_side(self) -> str | None:
        return self._ob_tracker.winning_side

    def get_time_remaining(self) -> float:
        """Seconds until market close (negative if already closed)."""
        now = datetime.now(timezone.utc)
        return (self._end_time - now).total_seconds()

    async def run(self) -> None:
        """Start the feed: connect WS + oracle, emit ticks to subscribers."""
        tasks: list[asyncio.coroutines.Coroutine] = []
        try:
            if self._orderbook_ws_adapter is not None:
                await self._orderbook_ws_adapter.start()
                self._log(f"[{self._market_name}] MarketFeed: OrderbookWS connected (L2)")
                tasks.append(self._l2_tick_loop())
            else:
                connected = await self._ws_client.connect()
                if not connected:
                    self._log(f"[{self._market_name}] MarketFeed: WS connect failed")
                    return
                tasks.append(self._listen_ws())
                tasks.append(self._tick_heartbeat_loop())

            if self._oracle_guard.enabled:
                tasks.append(self._oracle_price_loop())

            await asyncio.gather(*tasks)

        finally:
            if self._orderbook_ws_adapter is not None:
                try:
                    await self._orderbook_ws_adapter.stop()
                except Exception:
                    pass
            if self._ws_client.ws is not None:
                try:
                    await self._ws_client.close()
                except Exception:
                    pass
            if self._oracle_guard.enabled:
                self._oracle_guard.log_block_summary(self._logger)

    async def shutdown(self) -> None:
        """Signal the feed to stop."""
        self._shutting_down = True
        if self._orderbook_ws_adapter is not None:
            await self._orderbook_ws_adapter.stop()
        if self._ws_client.ws is not None:
            await self._ws_client.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _log(self, message: str) -> None:
        if self._logger:
            self._logger.info(message)
        else:
            print(message)

    def _notify_subscribers(self, tick: MarketTick) -> None:
        """Schedule subscriber callbacks without blocking the caller."""
        for cb in list(self._subscribers):
            asyncio.ensure_future(self._call_subscriber(cb, tick))

    async def _call_subscriber(self, cb: TickCallback, tick: MarketTick) -> None:
        try:
            await cb(tick)
        except Exception as e:
            self._log(f"[{self._market_name}] Feed subscriber error: {e}")

    def _build_tick(self) -> MarketTick:
        return MarketTick(
            time_remaining=self.get_time_remaining(),
            oracle_snapshot=self._oracle_guard.snapshot,
            orderbook=self._orderbook,
        )

    # ------------------------------------------------------------------
    # WS message parsing (moved from LastSecondTrader.process_market_update)
    # ------------------------------------------------------------------

    async def _process_ws_message(self, data: dict) -> None:
        """Parse a raw WS message, update the orderbook, notify subscribers."""
        try:
            if not data:
                return
            if isinstance(data, list) and len(data) > 0:
                data = data[0]  # type: ignore[index]
            if not isinstance(data, dict):
                return

            received_asset_id = data.get("asset_id")
            if not received_asset_id:
                return

            is_yes_data = received_asset_id == self._market.token_id_yes
            is_no_data = received_asset_id == self._market.token_id_no
            if not is_yes_data and not is_no_data:
                return

            event_type = data.get("event_type")

            if event_type == "book":
                asks = data.get("asks", [])
                bids = data.get("bids", [])
                best_ask, best_ask_size = extract_best_ask_with_size_from_book(asks)
                best_bid, best_bid_size = extract_best_bid_with_size_from_book(bids)

                if best_ask is not None and 0.001 <= best_ask <= 0.999:
                    if is_yes_data:
                        self._orderbook.best_ask_yes = best_ask
                        self._orderbook.best_ask_yes_size = best_ask_size
                    else:
                        self._orderbook.best_ask_no = best_ask
                        self._orderbook.best_ask_no_size = best_ask_size

                if best_bid is not None and 0.001 <= best_bid <= 0.999:
                    if is_yes_data:
                        self._orderbook.best_bid_yes = best_bid
                        self._orderbook.best_bid_yes_size = best_bid_size
                    else:
                        self._orderbook.best_bid_no = best_bid
                        self._orderbook.best_bid_no_size = best_bid_size

            elif event_type == "price_change":
                changes = data.get("price_changes", [])
                for change in changes:
                    change_asset_id = change.get("asset_id")
                    if not change_asset_id:
                        continue
                    is_yes_change = change_asset_id == self._market.token_id_yes
                    is_no_change = change_asset_id == self._market.token_id_no
                    if not is_yes_change and not is_no_change:
                        continue

                    best_ask = change.get("best_ask")
                    best_bid = change.get("best_bid")

                    if best_ask is not None and best_ask != "":
                        try:
                            ask_val = float(best_ask)
                            if 0.001 <= ask_val <= 0.999:
                                if is_yes_change:
                                    self._orderbook.best_ask_yes = ask_val
                                    self._orderbook.best_ask_yes_size = None
                                else:
                                    self._orderbook.best_ask_no = ask_val
                                    self._orderbook.best_ask_no_size = None
                        except (ValueError, TypeError):
                            pass

                    if best_bid is not None and best_bid != "":
                        try:
                            bid_val = float(best_bid)
                            if 0.001 <= bid_val <= 0.999:
                                if is_yes_change:
                                    self._orderbook.best_bid_yes = bid_val
                                    self._orderbook.best_bid_yes_size = None
                                else:
                                    self._orderbook.best_bid_no = bid_val
                                    self._orderbook.best_bid_no_size = None
                        except (ValueError, TypeError):
                            pass

            elif event_type == "best_bid_ask":
                best_ask = data.get("best_ask")
                best_bid = data.get("best_bid")

                if best_ask is not None and best_ask != "":
                    try:
                        val = float(best_ask)
                        if 0.001 <= val <= 0.999:
                            if is_yes_data:
                                self._orderbook.best_ask_yes = val
                                self._orderbook.best_ask_yes_size = None
                            else:
                                self._orderbook.best_ask_no = val
                                self._orderbook.best_ask_no_size = None
                    except (ValueError, TypeError):
                        pass

                if best_bid is not None and best_bid != "":
                    try:
                        val = float(best_bid)
                        if 0.001 <= val <= 0.999:
                            if is_yes_data:
                                self._orderbook.best_bid_yes = val
                                self._orderbook.best_bid_yes_size = None
                            else:
                                self._orderbook.best_bid_no = val
                                self._orderbook.best_bid_no_size = None
                    except (ValueError, TypeError):
                        pass

            self._orderbook.update()
            self._ob_tracker.update_winning_side()
            self.last_ws_update_ts = time.time()

            tick = self._build_tick()
            for cb in list(self._subscribers):
                await self._call_subscriber(cb, tick)

        except Exception as e:
            self._log(f"[{self._market_name}] Feed: error processing WS message: {e}")

    # ------------------------------------------------------------------
    # WS listener loops
    # ------------------------------------------------------------------

    async def _listen_ws(self) -> None:
        """Listen to L1 WS and dispatch messages to subscribers."""
        self._ws_client.ws = self._ws_client.ws  # already set by connect()
        await self._ws_client.listen(
            on_update=self._process_ws_message,
            should_stop=lambda: self.get_time_remaining() <= 0 or self._shutting_down,
        )

    async def _tick_heartbeat_loop(self) -> None:
        """Emit ticks to subscribers every ~1 s even when WS is quiet.

        This mirrors the original _trigger_check_loop's role of ensuring
        strategies evaluate at least once per second with fresh data.
        """
        while not self._shutting_down:
            time_remaining = self.get_time_remaining()
            if time_remaining <= 0:
                break

            has_data = (
                self._orderbook.best_ask_yes is not None
                or self._orderbook.best_ask_no is not None
            )
            if has_data:
                now_ts = time.time()
                ws_fresh = (now_ts - self.last_ws_update_ts) <= self.WS_STALE_SECONDS
                if ws_fresh:
                    tick = self._build_tick()
                    for cb in list(self._subscribers):
                        await self._call_subscriber(cb, tick)

            await asyncio.sleep(1.0)

    async def _l2_tick_loop(self) -> None:
        """Emit ticks when using the L2 OrderbookWS adapter."""
        while not self._shutting_down:
            time_remaining = self.get_time_remaining()
            if time_remaining <= 0:
                break

            has_data = (
                self._orderbook.best_ask_yes is not None
                or self._orderbook.best_ask_no is not None
            )
            if has_data:
                now_ts = time.time()
                assert self._orderbook_ws_adapter is not None
                last_sync = self._orderbook_ws_adapter.last_sync_ts
                ws_fresh = (now_ts - last_sync) <= self.WS_STALE_SECONDS
                if ws_fresh:
                    # L2 adapter already updated the orderbook; build tick
                    self._ob_tracker.update_winning_side()
                    self.last_ws_update_ts = last_sync
                    tick = self._build_tick()
                    for cb in list(self._subscribers):
                        await self._call_subscriber(cb, tick)

            await asyncio.sleep(1.0)

    # ------------------------------------------------------------------
    # Oracle price loop (moved from LastSecondTrader._oracle_price_loop)
    # ------------------------------------------------------------------

    async def _oracle_price_loop(self) -> None:
        """Stream Chainlink oracle prices and feed them into OracleGuardManager."""
        if self._oracle_guard.symbol is None:
            self._log(
                f"⚠️  [{self._market_name}] Oracle tracking enabled but symbol is unknown"
            )
            return
        if self._oracle_guard.tracker is None:
            return

        start_ms = (
            getattr(self._oracle_guard.window, "start_ms", None)
            if self._oracle_guard.window
            else None
        )
        end_ms = (
            getattr(self._oracle_guard.window, "end_ms", None)
            if self._oracle_guard.window
            else None
        )
        now_ms = int(time.time() * 1000)
        missed_start = False
        if start_ms is None:
            self._log(
                f"⚠️  [{self._market_name}] Oracle window start not parsed; "
                "price_to_beat capture may be unavailable"
            )
            missed_start = True
        else:
            lag_ms = now_ms - start_ms
            if lag_ms > self._oracle_guard.beat_max_lag_ms:
                self._log(
                    f"⚠️  [{self._market_name}] Oracle start missed by "
                    f"{lag_ms / 1000:.1f}s; price_to_beat will be unavailable"
                )
                missed_start = True

        slug = self._market.slug
        if (
            missed_start
            and not self._oracle_guard.html_beat_attempted
            and slug
            and self._oracle_guard.window is not None
            and self._oracle_guard.window.start_iso_z is not None
            and self._oracle_guard.tracker.price_to_beat is None
        ):
            self._oracle_guard.html_beat_attempted = True
            try:
                asset = self._market_name
                cadence = "fifteen"
                if start_ms is not None and end_ms is not None:
                    dur_ms = end_ms - start_ms
                    if abs(dur_ms - 300_000) <= 15_000:
                        cadence = "five"
                async with aiohttp.ClientSession() as session:
                    event_page = EventPageClient(session)
                    open_price, _ = await event_page.fetch_past_results(
                        eslug=slug,
                        asset=asset,
                        cadence=cadence,
                        start_time_iso_z=self._oracle_guard.window.start_iso_z,
                    )
                if open_price is not None:
                    self._oracle_guard.tracker.price_to_beat = float(open_price)
                    self._log(
                        f"✓ [{self._market_name}] price_to_beat from event HTML: {open_price:,.2f}"
                    )
                else:
                    self._log(
                        f"⚠️  [{self._market_name}] Could not fetch price_to_beat from event HTML"
                    )
            except Exception as e:
                self._log(f"⚠️  [{self._market_name}] Event HTML price_to_beat fetch failed: {e}")

        if slug and (
            self._oracle_guard.up_side is None or self._oracle_guard.down_side is None
        ):
            try:
                url = f"https://gamma-api.polymarket.com/markets/slug/{slug}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=15)
                    ) as resp:
                        data = await resp.json() if resp.status == 200 else None

                if isinstance(data, dict):
                    outcomes_raw = data.get("outcomes")
                    token_ids_raw = data.get("clobTokenIds")
                    outcomes = (
                        json.loads(outcomes_raw)
                        if isinstance(outcomes_raw, str)
                        else outcomes_raw
                    )
                    token_ids = (
                        json.loads(token_ids_raw)
                        if isinstance(token_ids_raw, str)
                        else token_ids_raw
                    )
                    if (
                        isinstance(outcomes, list)
                        and isinstance(token_ids, list)
                        and len(outcomes) == 2
                        and len(token_ids) == 2
                    ):
                        up_idx = next(
                            (i for i, o in enumerate(outcomes)
                             if isinstance(o, str) and o.strip().lower() == "up"),
                            None,
                        )
                        down_idx = next(
                            (i for i, o in enumerate(outcomes)
                             if isinstance(o, str) and o.strip().lower() == "down"),
                            None,
                        )
                        if up_idx is not None and down_idx is not None:
                            up_token = str(token_ids[up_idx])
                            down_token = str(token_ids[down_idx])
                            if up_token == self._market.token_id_yes:
                                self._oracle_guard.up_side = "YES"
                            elif up_token == self._market.token_id_no:
                                self._oracle_guard.up_side = "NO"
                            if down_token == self._market.token_id_yes:
                                self._oracle_guard.down_side = "YES"
                            elif down_token == self._market.token_id_no:
                                self._oracle_guard.down_side = "NO"
                            if self._oracle_guard.up_side and self._oracle_guard.down_side:
                                self._log(
                                    f"✓ [{self._market_name}] Oracle outcome mapping: "
                                    f"Up→{self._oracle_guard.up_side}, "
                                    f"Down→{self._oracle_guard.down_side}"
                                )
            except Exception as e:
                self._log(f"⚠️  [{self._market_name}] Oracle mapping fetch failed: {e}")

        self._log(
            f"✓ [{self._market_name}] Oracle tracking enabled "
            f"(RTDS Chainlink) symbol={self._oracle_guard.symbol}"
        )

        rtds = RtdsClient()
        topics = {"crypto_prices_chainlink"}

        while self.get_time_remaining() > 0:
            try:
                async for tick in rtds.iter_prices(
                    symbol=self._oracle_guard.symbol, topics=topics, seconds=15.0
                ):
                    self._oracle_guard.last_update_ts = time.time()

                    if start_ms is not None:
                        self._oracle_guard.tracker.maybe_set_price_to_beat(
                            ts_ms=tick.ts_ms,
                            price=tick.price,
                            start_ms=start_ms,
                            max_lag_ms=self._oracle_guard.beat_max_lag_ms,
                        )
                    if self._oracle_guard.tracker.price_to_beat is None:
                        self._oracle_guard.tracker.price_to_beat = tick.price
                        self._log(
                            f"[{self._market_name}] Using first oracle price as "
                            f"price_to_beat: {tick.price:,.2f}"
                        )

                    self._oracle_guard.snapshot = self._oracle_guard.tracker.update(
                        ts_ms=tick.ts_ms, price=tick.price
                    )

                    # HTML fallback: if beat still missing 10s after start
                    if (
                        self._oracle_guard.tracker.price_to_beat is None
                        and not self._oracle_guard.html_beat_attempted
                        and slug
                        and start_ms is not None
                        and (tick.ts_ms - start_ms) > 10_000
                    ):
                        self._oracle_guard.html_beat_attempted = True
                        try:
                            asset = self._market_name
                            cadence = "fifteen"
                            if start_ms is not None and end_ms is not None:
                                dur_ms = end_ms - start_ms
                                if abs(dur_ms - 300_000) <= 15_000:
                                    cadence = "five"
                            async with aiohttp.ClientSession() as session:
                                event_page = EventPageClient(session)
                                open_price, _ = await event_page.fetch_past_results(
                                    eslug=slug, asset=asset, cadence=cadence,
                                    start_time_iso_z=(
                                        self._oracle_guard.window.start_iso_z
                                        if self._oracle_guard.window else None
                                    ),
                                )
                            if open_price is not None:
                                self._oracle_guard.tracker.price_to_beat = float(open_price)
                                self._log(
                                    f"✓ [{self._market_name}] price_to_beat from "
                                    f"HTML fallback: {open_price:,.2f}"
                                )
                            else:
                                if self._oracle_guard.tracker._points:
                                    first_price = self._oracle_guard.tracker._points[0][1]
                                    self._oracle_guard.tracker.price_to_beat = first_price
                                    self._log(
                                        f"⚠️  [{self._market_name}] price_to_beat from "
                                        f"first oracle tick (approx): {first_price:,.2f}"
                                    )
                        except Exception as e:
                            self._log(f"⚠️  [{self._market_name}] HTML fallback failed: {e}")
                            if (
                                self._oracle_guard.tracker._points
                                and self._oracle_guard.tracker.price_to_beat is None
                            ):
                                first_price = self._oracle_guard.tracker._points[0][1]
                                self._oracle_guard.tracker.price_to_beat = first_price

                    now_ts = time.time()
                    if (now_ts - self._oracle_guard._last_log_ts) >= 1.0:
                        snap = self._oracle_guard.snapshot
                        beat = (
                            f"{snap.price_to_beat:,.2f}"
                            if snap.price_to_beat is not None
                            else "-"
                        )
                        delta = f"{snap.delta:,.2f}" if snap.delta is not None else "-"
                        delta_pct = (
                            f"{snap.delta_pct * 100:.4f}%"
                            if snap.delta_pct is not None
                            else "-"
                        )
                        z = f"{snap.zscore:.2f}" if snap.zscore is not None else "-"
                        self._log(
                            f"[{self._market_name}] ORACLE "
                            f"{self._oracle_guard.symbol}={snap.price:,.2f} | "
                            f"beat={beat} | Δ={delta} | Δ%={delta_pct} | z={z}"
                        )
                        self._oracle_guard._last_log_ts = now_ts

                    if end_ms is not None and tick.ts_ms >= end_ms:
                        return

            except Exception as e:
                self._log(f"⚠️  [{self._market_name}] Oracle RTDS error: {e}")
                await asyncio.sleep(2.0)
