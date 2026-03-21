"""Tests for Task 1 (Bug 2): settler writes sell trades to DB and closes positions."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.position_settler import PositionSettler


def _make_settler(dry_run: bool = True) -> PositionSettler:
    logger = logging.getLogger("test_settler_sell_db")
    logger.setLevel(logging.DEBUG)
    client = MagicMock() if not dry_run else None
    return PositionSettler(dry_run=dry_run, logger=logger, client=client)


POSITION = {
    "token_id": "0xTOKEN",
    "condition_id": "0xCOND",
    "balance": 10.0,
    "current_price": 0.999,
    "entry_price": 0.25,
    "side": "YES",
    "market_title": "Test Market",
}


# ---------------------------------------------------------------------------
# T1 — _record_sell_trade writes to all DB paths
# ---------------------------------------------------------------------------


class TestRecordSellTrade:
    def test_sell_records_trade_in_db(self, monkeypatch):
        """_record_sell_trade() calls record_trade() on every DB path."""
        settler = _make_settler(dry_run=False)

        recorded = []

        async def fake_record_trade(**kwargs):
            recorded.append(kwargs)

        mock_db = AsyncMock()
        mock_db.record_trade = fake_record_trade
        mock_db.close = AsyncMock()

        monkeypatch.setattr(
            "src.position_settler.TradeDatabase.initialize",
            AsyncMock(return_value=mock_db),
        )
        monkeypatch.setattr(
            "src.position_settler.PositionSettler._get_db_paths",
            staticmethod(lambda: ["data/fake.db"]),
        )

        asyncio.run(settler._record_sell_trade(POSITION, exit_price=0.999, reason="settler_sell"))

        assert len(recorded) == 1
        assert recorded[0]["action"] == "sell"
        assert recorded[0]["condition_id"] == "0xCOND"
        assert recorded[0]["price"] == pytest.approx(0.999)

    def test_record_sell_trade_calculates_pnl(self, monkeypatch):
        """PnL = (exit - entry) * balance."""
        settler = _make_settler(dry_run=False)

        recorded = []

        async def fake_record_trade(**kwargs):
            recorded.append(kwargs)

        mock_db = AsyncMock()
        mock_db.record_trade = fake_record_trade
        mock_db.close = AsyncMock()

        monkeypatch.setattr(
            "src.position_settler.TradeDatabase.initialize",
            AsyncMock(return_value=mock_db),
        )
        monkeypatch.setattr(
            "src.position_settler.PositionSettler._get_db_paths",
            staticmethod(lambda: ["data/fake.db"]),
        )

        asyncio.run(settler._record_sell_trade(POSITION, exit_price=0.999, reason="settler_sell"))

        assert len(recorded) == 1
        expected_pnl = (0.999 - 0.25) * 10.0
        assert recorded[0]["pnl"] == pytest.approx(expected_pnl, rel=1e-4)

    def test_record_sell_skips_missing_condition_id(self, monkeypatch):
        """_record_sell_trade() is a no-op when condition_id is empty."""
        settler = _make_settler(dry_run=False)

        mock_init = AsyncMock()
        monkeypatch.setattr(
            "src.position_settler.TradeDatabase.initialize",
            mock_init,
        )

        pos = {**POSITION, "condition_id": ""}
        asyncio.run(settler._record_sell_trade(pos, exit_price=0.999, reason="settler_sell"))
        mock_init.assert_not_called()


# ---------------------------------------------------------------------------
# T2 — _close_position_in_db marks position closed
# ---------------------------------------------------------------------------


class TestClosePositionInDb:
    def test_sell_closes_position_in_db(self, monkeypatch):
        """_close_position_in_db() calls close_position() on every DB path."""
        settler = _make_settler(dry_run=False)

        closed = []

        async def fake_close_position(condition_id, reason=None):
            closed.append((condition_id, reason))

        mock_db = AsyncMock()
        mock_db.close_position = fake_close_position
        mock_db.close = AsyncMock()

        monkeypatch.setattr(
            "src.position_settler.TradeDatabase.initialize",
            AsyncMock(return_value=mock_db),
        )
        monkeypatch.setattr(
            "src.position_settler.PositionSettler._get_db_paths",
            staticmethod(lambda: ["data/fake.db"]),
        )

        asyncio.run(settler._close_position_in_db("0xCOND", reason="settler_sell"))

        assert len(closed) == 1
        assert closed[0] == ("0xCOND", "settler_sell")


# ---------------------------------------------------------------------------
# T3 — sell_position_if_profitable wires both DB calls on success
# ---------------------------------------------------------------------------


class TestSellPositionWiresDb:
    def test_sell_calls_record_and_close_on_success(self, monkeypatch):
        """Successful sell triggers _record_sell_trade and _close_position_in_db."""
        settler = _make_settler(dry_run=False)
        mock_client = MagicMock()
        mock_client.create_market_order.return_value = MagicMock()
        mock_client.post_order.return_value = {"success": True, "orderID": "abc"}
        settler.client = mock_client

        record_calls = []
        close_calls = []

        async def fake_record(position, exit_price, reason):
            record_calls.append((exit_price, reason))

        async def fake_close(condition_id, reason):
            close_calls.append((condition_id, reason))

        settler._record_sell_trade = fake_record
        settler._close_position_in_db = fake_close
        settler.log_pnl_to_csv = AsyncMock()
        settler._lookup_entry_price_from_db = AsyncMock(return_value=0.25)

        pos = {**POSITION, "current_price": 0.999}
        asyncio.run(settler.sell_position_if_profitable(pos))

        assert len(record_calls) == 1, "Expected _record_sell_trade to be called once"
        assert record_calls[0][1] == "settler_sell"
        assert len(close_calls) == 1, "Expected _close_position_in_db to be called once"
        assert close_calls[0][0] == "0xCOND"

    def test_failed_sell_does_not_record_trade(self, monkeypatch):
        """Failed sell order does NOT trigger DB writes."""
        settler = _make_settler(dry_run=False)
        mock_client = MagicMock()
        mock_client.create_market_order.return_value = MagicMock()
        mock_client.post_order.return_value = {"success": False, "error": "no liquidity"}
        settler.client = mock_client

        record_calls = []

        async def fake_record(position, exit_price, reason):
            record_calls.append((exit_price, reason))

        settler._record_sell_trade = fake_record

        pos = {**POSITION, "current_price": 0.999}
        asyncio.run(settler.sell_position_if_profitable(pos))

        assert len(record_calls) == 0, "No DB write on failed sell"
