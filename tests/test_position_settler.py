"""Tests for position settler fixes: maker_address, DB paths, entry_price."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


from src.position_settler import PositionSettler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settler(dry_run: bool = True) -> PositionSettler:
    """Create a PositionSettler with a stub logger (no file I/O).

    Passes a MagicMock client when dry_run=False to avoid calling
    _create_clob_client (which would invoke load_dotenv and reload .env).
    """
    import logging
    logger = logging.getLogger("test_settler")
    logger.setLevel(logging.DEBUG)
    client = MagicMock() if not dry_run else None
    return PositionSettler(dry_run=dry_run, logger=logger, client=client)


# ---------------------------------------------------------------------------
# T1 — maker_address uses POLYMARKET_PROXY_ADDRESS when set
# ---------------------------------------------------------------------------


class TestMakerAddress:
    def test_get_open_positions_uses_proxy_address(self, monkeypatch):
        """When POLYMARKET_PROXY_ADDRESS is set, trades are queried with the proxy."""
        proxy = "0xProxyWallet"
        monkeypatch.setenv("POLYMARKET_PROXY_ADDRESS", proxy)

        captured = {}

        def fake_get_trades(params):
            captured["maker_address"] = params.maker_address
            return []

        mock_client = MagicMock()
        mock_client.get_address.return_value = "0xEOA"
        mock_client.get_trades.side_effect = fake_get_trades

        settler = _make_settler(dry_run=False)
        settler.client = mock_client

        asyncio.run(settler.get_open_positions())

        assert captured["maker_address"] == proxy, (
            f"Expected proxy {proxy!r}, got {captured['maker_address']!r}"
        )

    def test_get_open_positions_falls_back_to_eoa(self, monkeypatch):
        """When POLYMARKET_PROXY_ADDRESS is not set, EOA address is used."""
        monkeypatch.delenv("POLYMARKET_PROXY_ADDRESS", raising=False)

        captured = {}

        def fake_get_trades(params):
            captured["maker_address"] = params.maker_address
            return []

        mock_client = MagicMock()
        mock_client.get_address.return_value = "0xEOA"
        mock_client.get_trades.side_effect = fake_get_trades

        settler = _make_settler(dry_run=False)
        settler.client = mock_client

        asyncio.run(settler.get_open_positions())

        assert captured["maker_address"] == "0xEOA", (
            f"Expected EOA, got {captured['maker_address']!r}"
        )


# ---------------------------------------------------------------------------
# T2 — _get_db_paths reads SETTLER_DB_PATHS env or auto-scans data/*.db
# ---------------------------------------------------------------------------


class TestGetDbPaths:
    def test_returns_env_paths_when_set(self, monkeypatch):
        monkeypatch.setenv(
            "SETTLER_DB_PATHS",
            "data/convergence-v1-test.db,data/convergence-v2-live.db",
        )
        paths = PositionSettler._get_db_paths()
        assert paths == ["data/convergence-v1-test.db", "data/convergence-v2-live.db"]

    def test_env_paths_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv(
            "SETTLER_DB_PATHS",
            " data/a.db , data/b.db ",
        )
        paths = PositionSettler._get_db_paths()
        assert paths == ["data/a.db", "data/b.db"]

    def test_auto_scans_data_dir(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SETTLER_DB_PATHS", raising=False)
        # Create a fake data/ dir with .db files
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "convergence-v1-test.db").touch()
        (data_dir / "convergence-v2-live.db").touch()

        monkeypatch.chdir(tmp_path)

        paths = PositionSettler._get_db_paths()
        assert sorted(paths) == [
            "data/convergence-v1-test.db",
            "data/convergence-v2-live.db",
        ]

    def test_fallback_when_no_data_dir(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SETTLER_DB_PATHS", raising=False)
        monkeypatch.chdir(tmp_path)  # no data/ dir here

        paths = PositionSettler._get_db_paths()
        assert paths == ["data/trades.db"]

    def test_fallback_when_data_dir_empty(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SETTLER_DB_PATHS", raising=False)
        (tmp_path / "data").mkdir()
        monkeypatch.chdir(tmp_path)

        paths = PositionSettler._get_db_paths()
        assert paths == ["data/trades.db"]


# ---------------------------------------------------------------------------
# T1/T2 — check_dryrun_resolution iterates all DB paths
# ---------------------------------------------------------------------------


class TestCheckDryrunjResolutionMultiDb:
    def test_iterates_multiple_db_paths(self, tmp_path, monkeypatch):
        """check_dryrun_resolution opens each DB from _get_db_paths."""
        monkeypatch.delenv("SETTLER_DB_PATHS", raising=False)

        opened_dbs: list[str] = []

        async def fake_initialize(path: str):
            opened_dbs.append(path)
            db = MagicMock()
            db.get_open_dry_run_positions = AsyncMock(return_value=[])
            db.close = AsyncMock()
            return db

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "v1.db").touch()
        (data_dir / "v2.db").touch()
        monkeypatch.chdir(tmp_path)

        with patch("src.position_settler.TradeDatabase") as mock_cls:
            mock_cls.initialize = fake_initialize
            settler = _make_settler()
            asyncio.run(settler.check_dryrun_resolution())

        assert sorted(opened_dbs) == ["data/v1.db", "data/v2.db"]


# ---------------------------------------------------------------------------
# P1 — entry_price is tracked from CLOB BUY trades (not hardcoded 0.99)
# ---------------------------------------------------------------------------


class TestEntryPriceFromTrades:
    def test_entry_price_averaged_from_buy_trades(self, monkeypatch):
        """get_open_positions computes avg entry_price from BUY trades."""
        monkeypatch.delenv("POLYMARKET_PROXY_ADDRESS", raising=False)

        fake_trades = [
            {"side": "BUY", "asset_id": "tok1", "price": "0.60"},
            {"side": "BUY", "asset_id": "tok1", "price": "0.70"},
            {"side": "SELL", "asset_id": "tok1", "price": "0.80"},  # ignored
        ]

        mock_client = MagicMock()
        mock_client.get_address.return_value = "0xEOA"
        mock_client.get_trades.return_value = fake_trades
        mock_client.get_balance_allowance.return_value = {"balance": "10.0"}
        mock_client.get_price.return_value = {"price": "0.95"}

        settler = _make_settler(dry_run=False)
        settler.client = mock_client

        positions = asyncio.run(settler.get_open_positions())

        assert len(positions) == 1
        assert abs(positions[0]["entry_price"] - 0.65) < 1e-9  # avg(0.60, 0.70)

    def test_calculate_pnl_includes_entry_price(self):
        """calculate_pnl returns entry_price in the result dict."""
        settler = _make_settler()
        pnl = settler.calculate_pnl({"balance": 10}, entry_price=0.65, exit_price=1.0)
        assert "entry_price" in pnl
        assert abs(pnl["entry_price"] - 0.65) < 1e-6
