"""Tests for market resolution logic in DryRunSimulator."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from src.trading.trade_db import TradeDatabase
from src.trading.dry_run_simulator import DryRunSimulator


@pytest.fixture
def db(tmp_path):
    db = asyncio.run(TradeDatabase.initialize(str(tmp_path / "test.db")))
    yield db
    asyncio.run(db.close())


@pytest.fixture
def sim(db):
    return DryRunSimulator(
        db=db, market_name="Test Market", condition_id="cond_1", dry_run=True,
    )


def _run(coro):
    return asyncio.run(coro)


async def _open_position(db, condition_id="cond_1", side="YES", entry_price=0.60, amount=10.0):
    """Helper to insert an open dry-run position."""
    trade_id = await db.insert_trade(
        timestamp=time.time(), timestamp_iso="2026-01-01T00:00:00Z",
        market_name="Test Market", condition_id=condition_id,
        action="buy", side=side, price=entry_price, amount=amount,
        dry_run=True,
    )
    pos_id = await db.open_dry_run_position(
        trade_id=trade_id, condition_id=condition_id,
        market_name="Test Market", side=side, entry_price=entry_price,
        amount=amount, opened_at=time.time(),
    )
    return pos_id


class TestResolvePosition:
    def test_resolve_winning_position(self, sim, db):
        """Bought YES, resolved YES → profit."""
        async def run():
            await _open_position(db, side="YES", entry_price=0.60, amount=10.0)
            resolved = await sim.resolve_position("cond_1", "YES", "YES")
            assert len(resolved) == 1
            r = resolved[0]
            assert r["status"] == "resolved_win"
            assert r["exit_price"] == 1.0
            assert abs(r["pnl"] - 4.0) < 0.001  # (1.0 - 0.6) * 10
            assert r["pnl_pct"] > 0
            # Position should be closed in DB
            open_pos = await db.get_open_dry_run_positions()
            assert len(open_pos) == 0
        _run(run())

    def test_resolve_losing_position(self, sim, db):
        """Bought YES, resolved NO → loss."""
        async def run():
            await _open_position(db, side="YES", entry_price=0.60, amount=10.0)
            resolved = await sim.resolve_position("cond_1", "NO", "NO")
            assert len(resolved) == 1
            r = resolved[0]
            assert r["status"] == "resolved_loss"
            assert r["exit_price"] == 0.0
            assert abs(r["pnl"] - (-6.0)) < 0.001  # -0.6 * 10
            assert r["pnl_pct"] == -100.0
        _run(run())

    def test_resolve_no_open_positions(self, sim, db):
        """Nothing to resolve when no positions exist."""
        async def run():
            resolved = await sim.resolve_position("cond_1", "YES", "YES")
            assert resolved == []
        _run(run())

    def test_resolve_already_closed(self, sim, db):
        """Already-closed positions should be skipped."""
        async def run():
            pos_id = await _open_position(db, side="YES", entry_price=0.60, amount=10.0)
            # Close it manually first
            await db.close_dry_run_position(
                pos_id, exit_price=0.70, status="stop_loss",
                close_reason="manual", pnl=1.0, pnl_pct=16.7, closed_at=time.time(),
            )
            resolved = await sim.resolve_position("cond_1", "YES", "YES")
            assert resolved == []
        _run(run())

    def test_resolve_different_condition_id(self, sim, db):
        """Positions for other markets should not be resolved."""
        async def run():
            await _open_position(db, condition_id="other_cond", side="YES", entry_price=0.60)
            resolved = await sim.resolve_position("cond_1", "YES", "YES")
            assert resolved == []
            # The other position should still be open
            open_pos = await db.get_open_dry_run_positions()
            assert len(open_pos) == 1
        _run(run())


class TestVoidPositions:
    def test_void_positions(self, sim, db):
        """Voided market → PnL = 0, exit = entry (refund)."""
        async def run():
            await _open_position(db, side="YES", entry_price=0.75, amount=10.0)
            voided = await sim.void_positions("cond_1", "50-50 resolution")
            assert len(voided) == 1
            v = voided[0]
            assert v["status"] == "voided"
            assert v["pnl"] == 0.0
            assert v["exit_price"] == 0.75  # refund at entry
            # Position should be closed
            open_pos = await db.get_open_dry_run_positions()
            assert len(open_pos) == 0
        _run(run())

    def test_void_no_positions(self, sim, db):
        """Nothing to void."""
        async def run():
            voided = await sim.void_positions("cond_1", "test")
            assert voided == []
        _run(run())


class TestResolveAllMarkets:
    def test_resolve_all_markets_mixed(self, db):
        """Some markets resolved, some pending."""
        async def run():
            sim = DryRunSimulator(db=db, market_name="resolver", condition_id="resolver", dry_run=True)

            await _open_position(db, condition_id="resolved_market", side="YES", entry_price=0.50, amount=5.0)
            await _open_position(db, condition_id="pending_market", side="NO", entry_price=0.30, amount=8.0)

            mock_client = MagicMock()

            def get_market(cid):
                if cid == "resolved_market":
                    return {
                        "closed": True,
                        "accepting_orders": False,
                        "is_50_50_outcome": False,
                        "tokens": [
                            {"outcome": "YES", "winner": True},
                            {"outcome": "NO", "winner": False},
                        ],
                    }
                else:
                    return {"closed": False, "accepting_orders": True}

            mock_client.get_market = get_market

            resolved = await sim.resolve_all_markets(mock_client)

            assert len(resolved) == 1
            assert resolved[0]["status"] == "resolved_win"
            assert abs(resolved[0]["pnl"] - 2.5) < 0.001  # (1.0 - 0.5) * 5

            open_pos = await db.get_open_dry_run_positions()
            assert len(open_pos) == 1
            assert open_pos[0]["condition_id"] == "pending_market"

        _run(run())

    def test_resolve_all_voided_market(self, db):
        """50-50 outcome → positions voided with PnL=0."""
        async def run():
            sim = DryRunSimulator(db=db, market_name="resolver", condition_id="resolver", dry_run=True)

            await _open_position(db, condition_id="voided_market", side="YES", entry_price=0.60, amount=5.0)

            mock_client = MagicMock()

            def get_market(cid):
                return {
                    "closed": True,
                    "accepting_orders": False,
                    "is_50_50_outcome": True,
                    "tokens": [
                        {"outcome": "YES", "winner": False},
                        {"outcome": "NO", "winner": False},
                    ],
                }

            mock_client.get_market = get_market

            resolved = await sim.resolve_all_markets(mock_client)
            assert len(resolved) == 1
            assert resolved[0]["status"] == "voided"
            assert resolved[0]["pnl"] == 0.0

        _run(run())

    def test_resolve_all_disputed_skipped(self, db):
        """Market closed but still accepting_orders (disputed) → skip."""
        async def run():
            sim = DryRunSimulator(db=db, market_name="resolver", condition_id="resolver", dry_run=True)

            await _open_position(db, condition_id="disputed_market", side="YES", entry_price=0.70, amount=5.0)

            mock_client = MagicMock()

            def get_market(cid):
                return {
                    "closed": True,
                    "accepting_orders": True,  # disputed — still accepting
                    "tokens": [],
                }

            mock_client.get_market = get_market

            resolved = await sim.resolve_all_markets(mock_client)
            assert resolved == []

            # Position should still be open
            open_pos = await db.get_open_dry_run_positions()
            assert len(open_pos) == 1

        _run(run())

    def test_resolve_all_no_positions(self, db):
        """No positions → empty result."""
        async def run():
            sim = DryRunSimulator(db=db, market_name="resolver", condition_id="resolver", dry_run=True)
            mock_client = MagicMock()
            resolved = await sim.resolve_all_markets(mock_client)
            assert resolved == []
        _run(run())
