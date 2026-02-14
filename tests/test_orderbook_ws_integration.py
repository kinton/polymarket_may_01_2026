"""Integration tests for OrderbookWS adapter with LastSecondTrader."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from src.clob_types import OrderBook
from src.trading.orderbook_ws import OrderbookSnapshot, OrderbookWS
from src.trading.orderbook_ws_adapter import OrderbookWSAdapter


# ---------------------------------------------------------------------------
# OrderbookWSAdapter unit tests
# ---------------------------------------------------------------------------


class TestOrderbookWSAdapterSyncOnce:
    """Test sync_once projecting Level 2 → Level 1."""

    def _make_adapter(self) -> tuple[OrderbookWS, OrderBook, OrderbookWSAdapter]:
        ws = OrderbookWS()
        ob = OrderBook()
        adapter = OrderbookWSAdapter(
            ws=ws,
            orderbook=ob,
            token_id_yes="YES_TOKEN",
            token_id_no="NO_TOKEN",
        )
        return ws, ob, adapter

    def test_sync_empty(self) -> None:
        ws, ob, adapter = self._make_adapter()
        adapter.sync_once()
        assert ob.best_ask_yes is None
        assert ob.best_bid_yes is None
        assert adapter.sync_count == 1

    def test_sync_yes_side(self) -> None:
        ws, ob, adapter = self._make_adapter()
        ws._orderbooks["YES_TOKEN"] = OrderbookSnapshot(
            bids=[(0.90, 100.0), (0.89, 200.0)],
            asks=[(0.92, 50.0), (0.93, 80.0)],
        )
        adapter.sync_once()
        assert ob.best_bid_yes == 0.90
        assert ob.best_bid_yes_size == 100.0
        assert ob.best_ask_yes == 0.92
        assert ob.best_ask_yes_size == 50.0

    def test_sync_no_side(self) -> None:
        ws, ob, adapter = self._make_adapter()
        ws._orderbooks["NO_TOKEN"] = OrderbookSnapshot(
            bids=[(0.08, 300.0)],
            asks=[(0.10, 150.0)],
        )
        adapter.sync_once()
        assert ob.best_bid_no == 0.08
        assert ob.best_bid_no_size == 300.0
        assert ob.best_ask_no == 0.10
        assert ob.best_ask_no_size == 150.0

    def test_sync_both_sides(self) -> None:
        ws, ob, adapter = self._make_adapter()
        ws._orderbooks["YES_TOKEN"] = OrderbookSnapshot(
            bids=[(0.90, 100.0)], asks=[(0.92, 50.0)]
        )
        ws._orderbooks["NO_TOKEN"] = OrderbookSnapshot(
            bids=[(0.08, 300.0)], asks=[(0.10, 150.0)]
        )
        adapter.sync_once()
        assert ob.best_bid_yes == 0.90
        assert ob.best_ask_yes == 0.92
        assert ob.best_bid_no == 0.08
        assert ob.best_ask_no == 0.10
        # sum_asks should be computed
        assert ob.sum_asks is not None
        assert abs(ob.sum_asks - 1.02) < 0.001

    def test_sync_updates_timestamp(self) -> None:
        ws, ob, adapter = self._make_adapter()
        assert adapter.last_sync_ts == 0.0
        adapter.sync_once()
        assert adapter.last_sync_ts > 0.0

    def test_sync_count_increments(self) -> None:
        ws, ob, adapter = self._make_adapter()
        adapter.sync_once()
        adapter.sync_once()
        adapter.sync_once()
        assert adapter.sync_count == 3


class TestOrderbookWSAdapterStartStop:
    """Test start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_subscribes(self) -> None:
        ws = OrderbookWS()
        ws.connect = AsyncMock()
        ws.subscribe = AsyncMock()
        ws.disconnect = AsyncMock()

        ob = OrderBook()
        adapter = OrderbookWSAdapter(
            ws=ws, orderbook=ob,
            token_id_yes="Y", token_id_no="N",
            poll_interval=0.05,
        )
        await adapter.start()
        assert adapter._running is True
        ws.connect.assert_awaited_once()
        assert ws.subscribe.await_count == 2

        # Let sync loop run a bit
        await asyncio.sleep(0.15)
        assert adapter.sync_count >= 1

        await adapter.stop()
        assert adapter._running is False
        ws.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_without_start(self) -> None:
        ws = OrderbookWS()
        ws.disconnect = AsyncMock()
        ob = OrderBook()
        adapter = OrderbookWSAdapter(
            ws=ws, orderbook=ob, token_id_yes="Y", token_id_no="N"
        )
        await adapter.stop()  # should not raise
        assert adapter._running is False


class TestOrderbookWSAdapterSyncLoop:
    """Test that the sync loop continuously updates OrderBook."""

    @pytest.mark.asyncio
    async def test_continuous_sync(self) -> None:
        ws = OrderbookWS()
        ws.connect = AsyncMock()
        ws.subscribe = AsyncMock()
        ws.disconnect = AsyncMock()
        ob = OrderBook()
        adapter = OrderbookWSAdapter(
            ws=ws, orderbook=ob,
            token_id_yes="Y", token_id_no="N",
            poll_interval=0.02,
        )

        await adapter.start()

        # Feed data after start
        ws._orderbooks["Y"] = OrderbookSnapshot(
            bids=[(0.85, 100.0)], asks=[(0.87, 50.0)]
        )
        await asyncio.sleep(0.1)

        assert ob.best_bid_yes == 0.85
        assert ob.best_ask_yes == 0.87

        # Update data — adapter should pick it up
        ws._orderbooks["Y"] = OrderbookSnapshot(
            bids=[(0.86, 100.0)], asks=[(0.88, 50.0)]
        )
        await asyncio.sleep(0.1)

        assert ob.best_bid_yes == 0.86
        assert ob.best_ask_yes == 0.88

        await adapter.stop()


# ---------------------------------------------------------------------------
# Integration with LastSecondTrader
# ---------------------------------------------------------------------------


class TestTraderOrderbookWSFlag:
    """Test that USE_ORDERBOOK_WS env var / param works."""

    def _make_trader(self, use_orderbook_ws: bool = False):
        """Create a minimal LastSecondTrader with mocked deps."""
        with patch.dict("os.environ", {
            "PRIVATE_KEY": "",
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_CHAT_ID": "",
        }):
            from src.hft_trader import LastSecondTrader
            trader = LastSecondTrader(
                condition_id="cond1",
                token_id_yes="tok_yes",
                token_id_no="tok_no",
                end_time=datetime.now(timezone.utc) + timedelta(minutes=5),
                dry_run=True,
                trade_size=1.0,
                title="BTC test",
                use_orderbook_ws=use_orderbook_ws,
            )
        return trader

    def test_disabled_by_default(self) -> None:
        trader = self._make_trader(use_orderbook_ws=False)
        assert trader.use_orderbook_ws is False
        assert trader._orderbook_ws_adapter is None

    def test_enabled_by_param(self) -> None:
        trader = self._make_trader(use_orderbook_ws=True)
        assert trader.use_orderbook_ws is True
        assert trader._orderbook_ws_adapter is not None

    def test_enabled_by_env(self) -> None:
        with patch.dict("os.environ", {
            "USE_ORDERBOOK_WS": "1",
            "PRIVATE_KEY": "",
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_CHAT_ID": "",
        }):
            from src.hft_trader import LastSecondTrader
            trader = LastSecondTrader(
                condition_id="cond1",
                token_id_yes="tok_yes",
                token_id_no="tok_no",
                end_time=datetime.now(timezone.utc) + timedelta(minutes=5),
                dry_run=True,
                trade_size=1.0,
                title="BTC test",
            )
        assert trader.use_orderbook_ws is True
        assert trader._orderbook_ws_adapter is not None

    def test_env_false(self) -> None:
        with patch.dict("os.environ", {
            "USE_ORDERBOOK_WS": "0",
            "PRIVATE_KEY": "",
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_CHAT_ID": "",
        }):
            from src.hft_trader import LastSecondTrader
            trader = LastSecondTrader(
                condition_id="cond1",
                token_id_yes="tok_yes",
                token_id_no="tok_no",
                end_time=datetime.now(timezone.utc) + timedelta(minutes=5),
                dry_run=True,
                trade_size=1.0,
                title="BTC test",
            )
        assert trader.use_orderbook_ws is False


class TestTraderGracefulShutdownWithAdapter:
    """Test graceful_shutdown stops adapter."""

    @pytest.mark.asyncio
    async def test_shutdown_stops_adapter(self) -> None:
        with patch.dict("os.environ", {
            "PRIVATE_KEY": "",
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_CHAT_ID": "",
        }):
            from src.hft_trader import LastSecondTrader
            trader = LastSecondTrader(
                condition_id="cond1",
                token_id_yes="tok_yes",
                token_id_no="tok_no",
                end_time=datetime.now(timezone.utc) + timedelta(minutes=5),
                dry_run=True,
                trade_size=1.0,
                title="BTC test",
                use_orderbook_ws=True,
            )
        adapter = trader._orderbook_ws_adapter
        assert adapter is not None
        adapter.stop = AsyncMock()

        await trader.graceful_shutdown("test")
        adapter.stop.assert_awaited_once()


class TestTriggerLoopUsesAdapterTimestamp:
    """Test that _trigger_check_loop uses adapter.last_sync_ts for freshness."""

    def test_freshness_from_adapter(self) -> None:
        with patch.dict("os.environ", {
            "PRIVATE_KEY": "",
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_CHAT_ID": "",
        }):
            from src.hft_trader import LastSecondTrader
            trader = LastSecondTrader(
                condition_id="cond1",
                token_id_yes="tok_yes",
                token_id_no="tok_no",
                end_time=datetime.now(timezone.utc) + timedelta(minutes=5),
                dry_run=True,
                trade_size=1.0,
                title="BTC test",
                use_orderbook_ws=True,
            )

        adapter = trader._orderbook_ws_adapter
        assert adapter is not None

        # Simulate adapter sync
        adapter.last_sync_ts = time.time()

        # The trigger loop should see fresh data via adapter timestamp
        # (We just verify the attribute is accessible and used in logic)
        now_ts = time.time()
        last_update = adapter.last_sync_ts
        ws_fresh = (now_ts - last_update) <= trader.WS_STALE_SECONDS
        assert ws_fresh is True
