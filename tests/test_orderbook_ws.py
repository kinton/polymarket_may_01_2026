"""Tests for OrderbookWS client."""

from __future__ import annotations

import json

import pytest

from src.trading.orderbook_ws import OrderbookSnapshot, OrderbookWS


class TestOrderbookSnapshot:
    def test_empty_snapshot(self) -> None:
        ob = OrderbookSnapshot()
        assert ob.bids == []
        assert ob.asks == []

    def test_snapshot_with_data(self) -> None:
        ob = OrderbookSnapshot(
            bids=[(0.55, 100.0), (0.54, 200.0)],
            asks=[(0.56, 150.0)],
        )
        assert len(ob.bids) == 2
        assert len(ob.asks) == 1


class TestOrderbookWSInit:
    def test_default_url(self) -> None:
        ws = OrderbookWS()
        assert "polymarket" in ws.url
        assert ws._running is False
        assert ws._subscriptions == set()

    def test_custom_url(self) -> None:
        ws = OrderbookWS(url="wss://example.com/ws")
        assert ws.url == "wss://example.com/ws"

    def test_custom_params(self) -> None:
        ws = OrderbookWS(reconnect_delay=5.0, max_reconnect_delay=120.0, ping_interval=10.0)
        assert ws.reconnect_delay == 5.0
        assert ws.max_reconnect_delay == 120.0
        assert ws.ping_interval == 10.0


class TestGetBestPrices:
    def test_no_data(self) -> None:
        ws = OrderbookWS()
        assert ws.get_best_bid("unknown") is None
        assert ws.get_best_ask("unknown") is None

    def test_empty_orderbook(self) -> None:
        ws = OrderbookWS()
        ws._orderbooks["asset1"] = OrderbookSnapshot()
        assert ws.get_best_bid("asset1") is None
        assert ws.get_best_ask("asset1") is None

    def test_best_bid(self) -> None:
        ws = OrderbookWS()
        ws._orderbooks["asset1"] = OrderbookSnapshot(
            bids=[(0.50, 100.0), (0.55, 200.0), (0.52, 150.0)]
        )
        assert ws.get_best_bid("asset1") == 0.55

    def test_best_ask(self) -> None:
        ws = OrderbookWS()
        ws._orderbooks["asset1"] = OrderbookSnapshot(
            asks=[(0.60, 100.0), (0.56, 200.0), (0.58, 150.0)]
        )
        assert ws.get_best_ask("asset1") == 0.56

    def test_get_orderbook(self) -> None:
        ws = OrderbookWS()
        ws._orderbooks["asset1"] = OrderbookSnapshot(bids=[(0.5, 10.0)])
        ob = ws.get_orderbook("asset1")
        assert ob is not None
        assert ob.bids == [(0.5, 10.0)]
        assert ws.get_orderbook("unknown") is None


class TestHandleMessage:
    def test_book_snapshot(self) -> None:
        ws = OrderbookWS()
        msg = json.dumps({
            "type": "book",
            "asset_id": "asset1",
            "bids": [{"price": "0.55", "size": "100"}, {"price": "0.54", "size": "200"}],
            "asks": [{"price": "0.56", "size": "150"}],
        })
        ws._handle_message(msg)
        assert ws.get_best_bid("asset1") == 0.55
        assert ws.get_best_ask("asset1") == 0.56

    def test_price_change_buy(self) -> None:
        ws = OrderbookWS()
        ws._orderbooks["asset1"] = OrderbookSnapshot(
            bids=[(0.55, 100.0)], asks=[(0.56, 150.0)]
        )
        msg = json.dumps({
            "type": "price_change",
            "asset_id": "asset1",
            "changes": [{"side": "BUY", "price": "0.57", "size": "50"}],
        })
        ws._handle_message(msg)
        assert ws.get_best_bid("asset1") == 0.57

    def test_price_change_sell(self) -> None:
        ws = OrderbookWS()
        ws._orderbooks["asset1"] = OrderbookSnapshot(
            bids=[(0.55, 100.0)], asks=[(0.60, 150.0)]
        )
        msg = json.dumps({
            "type": "price_change",
            "asset_id": "asset1",
            "changes": [{"side": "SELL", "price": "0.58", "size": "75"}],
        })
        ws._handle_message(msg)
        assert ws.get_best_ask("asset1") == 0.58

    def test_price_change_remove_level(self) -> None:
        ws = OrderbookWS()
        ws._orderbooks["asset1"] = OrderbookSnapshot(
            bids=[(0.55, 100.0), (0.54, 200.0)], asks=[]
        )
        msg = json.dumps({
            "type": "price_change",
            "asset_id": "asset1",
            "changes": [{"side": "BUY", "price": "0.55", "size": "0"}],
        })
        ws._handle_message(msg)
        assert ws.get_best_bid("asset1") == 0.54
        assert len(ws._orderbooks["asset1"].bids) == 1

    def test_invalid_json(self) -> None:
        ws = OrderbookWS()
        ws._handle_message("not json")
        assert len(ws._orderbooks) == 0

    def test_unknown_type(self) -> None:
        ws = OrderbookWS()
        msg = json.dumps({"type": "heartbeat"})
        ws._handle_message(msg)
        assert len(ws._orderbooks) == 0


class TestSubscribe:
    @pytest.mark.asyncio
    async def test_subscribe_without_connection(self) -> None:
        ws = OrderbookWS()
        await ws.subscribe("asset1")
        assert "asset1" in ws._subscriptions
        assert "asset1" in ws._orderbooks

    @pytest.mark.asyncio
    async def test_disconnect_noop(self) -> None:
        ws = OrderbookWS()
        await ws.disconnect()
        assert ws._running is False
