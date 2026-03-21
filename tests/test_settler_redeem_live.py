"""Tests for Task 3 (Bug 1): auto-redeem winning live positions."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.position_settler import PositionSettler


def _make_settler(dry_run: bool = True) -> PositionSettler:
    logger = logging.getLogger("test_settler_redeem")
    logger.setLevel(logging.DEBUG)
    client = MagicMock() if not dry_run else None
    return PositionSettler(dry_run=dry_run, logger=logger, client=client)


POSITION = {
    "token_id": "0xWIN_TOKEN",
    "condition_id": "0xCOND",
    "balance": 10.0,
    "current_price": 0.0,
    "entry_price": 0.25,
    "side": "YES",
    "market_title": "Test Market",
}

RESOLVED_WINNER_RESPONSE = {
    "closed": True,
    "outcome": "Yes",
    "tokens": [
        {"outcome": "Yes", "token_id": "0xWIN_TOKEN"},
        {"outcome": "No", "token_id": "0xLOSE_TOKEN"},
    ],
}

RESOLVED_LOSER_RESPONSE = {
    "closed": True,
    "outcome": "No",
    "tokens": [
        {"outcome": "Yes", "token_id": "0xWIN_TOKEN"},
        {"outcome": "No", "token_id": "0xLOSE_TOKEN"},
    ],
}

UNRESOLVED_RESPONSE = {
    "closed": False,
    "outcome": None,
    "tokens": [
        {"outcome": "Yes", "token_id": "0xWIN_TOKEN"},
        {"outcome": "No", "token_id": "0xLOSE_TOKEN"},
    ],
}


# ---------------------------------------------------------------------------
# _is_winning_resolved_token
# ---------------------------------------------------------------------------


class TestIsWinningResolvedToken:
    def _mock_get(self, json_data: dict, ok: bool = True):
        resp = MagicMock()
        resp.ok = ok
        resp.json.return_value = json_data
        return resp

    def test_true_for_winner(self, monkeypatch):
        settler = _make_settler()
        resp = self._mock_get(RESOLVED_WINNER_RESPONSE)
        with patch("requests.get", return_value=resp):
            result = asyncio.run(
                settler._is_winning_resolved_token("0xWIN_TOKEN", "0xCOND")
            )
        assert result is True

    def test_false_for_loser(self, monkeypatch):
        settler = _make_settler()
        resp = self._mock_get(RESOLVED_LOSER_RESPONSE)
        with patch("requests.get", return_value=resp):
            result = asyncio.run(
                settler._is_winning_resolved_token("0xWIN_TOKEN", "0xCOND")
            )
        assert result is False

    def test_false_if_not_resolved(self, monkeypatch):
        settler = _make_settler()
        resp = self._mock_get(UNRESOLVED_RESPONSE)
        with patch("requests.get", return_value=resp):
            result = asyncio.run(
                settler._is_winning_resolved_token("0xWIN_TOKEN", "0xCOND")
            )
        assert result is False

    def test_false_on_api_error(self, monkeypatch):
        settler = _make_settler()
        resp = self._mock_get({}, ok=False)
        with patch("requests.get", return_value=resp):
            result = asyncio.run(
                settler._is_winning_resolved_token("0xWIN_TOKEN", "0xCOND")
            )
        assert result is False

    def test_false_for_empty_condition_id(self):
        settler = _make_settler()
        result = asyncio.run(settler._is_winning_resolved_token("0xTOKEN", ""))
        assert result is False


# ---------------------------------------------------------------------------
# _redeem_live_winning_position — dry run
# ---------------------------------------------------------------------------


class TestRedeemLiveWinningDryRun:
    def test_dry_run_returns_without_on_chain(self):
        settler = _make_settler(dry_run=True)
        result = asyncio.run(settler._redeem_live_winning_position(POSITION))
        assert result is not None
        assert result["status"] == "dry_run"


# ---------------------------------------------------------------------------
# process_positions — 3-way branch
# ---------------------------------------------------------------------------


class TestProcessPositionsThreeWayBranch:
    def _make_settler_with_positions(self, positions: list[dict], dry_run: bool = False):
        settler = _make_settler(dry_run=dry_run)
        settler.get_open_positions = AsyncMock(return_value=positions)
        settler.check_dryrun_resolution = AsyncMock()
        return settler

    def test_winning_resolved_triggers_redeem(self):
        """price=0.0 + winning token → _redeem_live_winning_position called."""
        settler = self._make_settler_with_positions([POSITION])

        settler._is_winning_resolved_token = AsyncMock(return_value=True)
        settler._redeem_live_winning_position = AsyncMock(
            return_value={"status": "success"}
        )

        asyncio.run(settler.process_positions())

        settler._is_winning_resolved_token.assert_called_once_with("0xWIN_TOKEN", "0xCOND")
        settler._redeem_live_winning_position.assert_called_once()

    def test_losing_resolved_held_not_redeemed(self):
        """price=0.0 + losing token → no redeem call."""
        settler = self._make_settler_with_positions([POSITION])

        settler._is_winning_resolved_token = AsyncMock(return_value=False)
        settler._redeem_live_winning_position = AsyncMock()

        asyncio.run(settler.process_positions())

        settler._redeem_live_winning_position.assert_not_called()

    def test_high_price_triggers_sell_not_redeem(self):
        """price >= 0.999 → sell path, not redeem path."""
        pos = {**POSITION, "current_price": 0.999}
        settler = self._make_settler_with_positions([pos])

        settler._is_winning_resolved_token = AsyncMock()
        settler.sell_position_if_profitable = AsyncMock(return_value={"success": True})

        asyncio.run(settler.process_positions())

        settler._is_winning_resolved_token.assert_not_called()
        settler.sell_position_if_profitable.assert_called_once()


# ---------------------------------------------------------------------------
# _redeem_live_winning_position — DB writes after successful redeem
# ---------------------------------------------------------------------------


class TestRedeemRecordsDb:
    def test_redeem_records_sell_trade_and_closes_position(self, monkeypatch):
        """Successful redeem writes to DB via _record_sell_trade and _close_position_in_db."""
        settler = _make_settler(dry_run=False)

        record_calls = []
        close_calls = []

        async def fake_record(position, exit_price, reason):
            record_calls.append((exit_price, reason))

        async def fake_close(condition_id, reason):
            close_calls.append((condition_id, reason))

        settler._record_sell_trade = fake_record
        settler._close_position_in_db = fake_close
        settler.log_pnl_to_csv = AsyncMock()
        settler.calculate_pnl = MagicMock(return_value={"profit_loss": 7.5})

        mock_market_resp = MagicMock()
        mock_market_resp.ok = True
        mock_market_resp.json.return_value = {"neg_risk": False}

        mock_redeemer = AsyncMock()
        mock_redeemer.redeem_position = AsyncMock(return_value={"status": "success"})

        with patch("requests.get", return_value=mock_market_resp):
            with patch("src.trading.auto_redeem.AutoRedeemer", return_value=mock_redeemer):
                monkeypatch.setenv("PRIVATE_KEY", "0xPRIVATEKEY")
                result = asyncio.run(settler._redeem_live_winning_position(POSITION))

        assert result == {"status": "success"}
        assert len(record_calls) == 1
        assert record_calls[0] == (1.0, "redeemed")
        assert len(close_calls) == 1
        assert close_calls[0][0] == "0xCOND"
