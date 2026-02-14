"""Tests for DryRunSimulator and enhanced trade_db features."""

from __future__ import annotations

import asyncio
import time

import pytest

from src.trading.trade_db import TradeDatabase
from src.trading.dry_run_simulator import DryRunSimulator, _extract_oracle


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_dryrun.db")


@pytest.fixture
def db(db_path):
    db = asyncio.run(TradeDatabase.initialize(db_path))
    yield db
    asyncio.run(db.close())


@pytest.fixture
def sim(db):
    return DryRunSimulator(
        db=db,
        market_name="BTC",
        condition_id="test-condition-123",
        dry_run=True,
    )


class TestTradeDecisionsTable:
    def test_insert_and_query_buy(self, db):
        async def _test():
            now = time.time()
            tid = await db.insert_trade_decision(
                timestamp=now,
                timestamp_iso="2026-02-14T18:00:00Z",
                market_name="BTC",
                condition_id="cond-1",
                action="buy",
                side="YES",
                price=0.95,
                amount=1.10,
                confidence=0.95,
                time_remaining=25.0,
                reason="trigger",
                dry_run=True,
            )
            assert tid > 0

            rows = await db.get_trade_decisions(action="buy")
            assert len(rows) >= 1
            assert rows[0]["action"] == "buy"
            assert rows[0]["side"] == "YES"
            assert rows[0]["price"] == 0.95

        asyncio.run(_test())

    def test_insert_and_query_skip(self, db):
        async def _test():
            await db.insert_trade_decision(
                timestamp=time.time(),
                timestamp_iso="2026-02-14T18:00:00Z",
                market_name="BTC",
                condition_id="cond-1",
                action="skip",
                reason="oracle_guard_blocked",
                reason_detail="z-score too low",
                dry_run=True,
            )
            rows = await db.get_trade_decisions(action="skip")
            assert len(rows) >= 1
            assert rows[0]["reason"] == "oracle_guard_blocked"

        asyncio.run(_test())

    def test_skip_reason_counts(self, db):
        async def _test():
            for reason in ["low_confidence", "low_confidence", "oracle_vol_high"]:
                await db.insert_trade_decision(
                    timestamp=time.time(),
                    timestamp_iso="2026-02-14T18:00:00Z",
                    market_name="BTC",
                    condition_id="cond-1",
                    action="skip",
                    reason=reason,
                    dry_run=True,
                )
            counts = await db.get_skip_reason_counts()
            assert len(counts) >= 2
            assert counts[0]["reason"] == "low_confidence"
            assert counts[0]["cnt"] == 2

        asyncio.run(_test())


class TestDryRunPositions:
    def test_open_and_close_position(self, db):
        async def _test():
            pid = await db.open_dry_run_position(
                condition_id="cond-1",
                market_name="BTC",
                side="YES",
                entry_price=0.95,
                amount=1.10,
                stop_loss_price=0.665,
                take_profit_price=1.045,
                opened_at=time.time(),
            )
            assert pid > 0

            open_pos = await db.get_open_dry_run_positions()
            assert len(open_pos) == 1

            await db.close_dry_run_position(
                pid,
                exit_price=1.05,
                status="take_profit",
                close_reason="take_profit at $1.05",
                pnl=0.116,
                pnl_pct=10.5,
                closed_at=time.time(),
            )

            open_pos = await db.get_open_dry_run_positions()
            assert len(open_pos) == 0

            summary = await db.get_dry_run_summary()
            assert summary["total"] == 1
            assert summary["wins"] == 1

        asyncio.run(_test())


class TestDryRunSimulator:
    def test_record_buy_creates_position(self, sim, db):
        async def _test():
            pos_id = await sim.record_buy(
                side="YES",
                price=0.95,
                amount=1.10,
                confidence=0.95,
                time_remaining=25.0,
            )
            assert pos_id > 0

            positions = await db.get_open_dry_run_positions()
            assert len(positions) == 1
            assert positions[0]["entry_price"] == 0.95

            decisions = await db.get_trade_decisions(action="buy")
            assert len(decisions) == 1

            trades = await db.get_trades()
            assert len(trades) == 1
            assert trades[0]["action"] == "buy"

        asyncio.run(_test())

    def test_record_skip(self, sim, db):
        async def _test():
            tid = await sim.record_skip(
                reason="low_confidence",
                reason_detail="ask=0.80<0.85",
                side="YES",
                price=0.80,
                confidence=0.80,
                time_remaining=20.0,
            )
            assert tid > 0

            decisions = await db.get_trade_decisions(action="skip")
            assert len(decisions) == 1
            assert decisions[0]["reason"] == "low_confidence"

        asyncio.run(_test())

    def test_virtual_stop_loss(self, sim, db):
        async def _test():
            await sim.record_buy(side="YES", price=0.95, amount=1.10)

            # Price drops below stop loss (0.95 * 0.70 = 0.665)
            closed = await sim.check_virtual_positions(0.60)
            assert len(closed) == 1
            assert closed[0]["status"] in ("stop_loss", "trailing_stop")
            assert closed[0]["pnl"] < 0

        asyncio.run(_test())

    def test_virtual_take_profit(self, sim, db):
        async def _test():
            await sim.record_buy(side="YES", price=0.90, amount=1.10)

            # Price rises above take-profit (0.90 * 1.10 = 0.99)
            closed = await sim.check_virtual_positions(0.995)
            assert len(closed) == 1
            assert closed[0]["status"] == "take_profit"
            assert closed[0]["pnl"] > 0

        asyncio.run(_test())

    def test_trailing_stop_update(self, sim, db):
        async def _test():
            await sim.record_buy(side="YES", price=0.90, amount=1.10)

            # Price moves up — trailing should update
            closed = await sim.check_virtual_positions(0.96)
            assert len(closed) == 0

            positions = await db.get_open_dry_run_positions()
            assert len(positions) == 1
            # Trailing stop should be around 0.96 * 0.95 = 0.912
            assert positions[0]["trailing_stop"] > 0.90

        asyncio.run(_test())


class TestExtractOracle:
    def test_none_snap(self):
        result = _extract_oracle(None)
        assert result["oracle_price"] is None

    def test_with_snap(self):
        class FakeSnap:
            price = 100000.0
            zscore = 1.5
            vol_pct = 0.001
            delta = 50.0
            n_points = 10

        result = _extract_oracle(FakeSnap())
        assert result["oracle_price"] == 100000.0
        assert result["oracle_z"] == 1.5


class TestDryRunSummary:
    def test_summary_aggregation(self, db):
        async def _test():
            now = time.time()
            pid1 = await db.open_dry_run_position(
                condition_id="c1", market_name="BTC", side="YES",
                entry_price=0.90, amount=1.0, opened_at=now,
            )
            await db.close_dry_run_position(
                pid1, exit_price=0.99, status="take_profit",
                close_reason="tp", pnl=0.10, pnl_pct=10.0, closed_at=now,
            )
            pid2 = await db.open_dry_run_position(
                condition_id="c2", market_name="ETH", side="NO",
                entry_price=0.95, amount=1.0, opened_at=now,
            )
            await db.close_dry_run_position(
                pid2, exit_price=0.80, status="stop_loss",
                close_reason="sl", pnl=-0.16, pnl_pct=-15.8, closed_at=now,
            )

            summary = await db.get_dry_run_summary()
            assert summary["total"] == 2
            assert summary["wins"] == 1
            assert summary["losses"] == 1
            assert abs(summary["total_pnl"] - (-0.06)) < 0.01

        asyncio.run(_test())


class TestMigrationV2:
    def test_v2_tables_created(self, db):
        async def _test():
            async with db._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='trade_decisions'"
            ) as cur:
                row = await cur.fetchone()
                assert row is not None

            async with db._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='dry_run_positions'"
            ) as cur:
                row = await cur.fetchone()
                assert row is not None

        asyncio.run(_test())
