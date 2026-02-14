"""Tests for PnL Dashboard v2 — SQLite + Rich + legacy compat."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.trading.pnl_dashboard import (
    PnLReport,
    TradeRecord,
    compute_pnl_report,
    format_report,
    load_daily_limits,
    load_report_from_sqlite,
    parse_trade_logs,
    render_rich_report,
    _win_rate,
    WEEKDAY_NAMES,
)
from src.trading.trade_db import TradeDatabase


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
async def db(tmp_path: Path):
    """Create an in-memory-like temp SQLite DB."""
    db_path = str(tmp_path / "test.db")
    tdb = await TradeDatabase.initialize(db_path)
    yield tdb
    await tdb.close()


async def _insert_sample_trades(db: TradeDatabase, count: int = 5) -> list[int]:
    """Insert sample trades and return their IDs."""
    ids = []
    for i in range(count):
        ts = 1707700000.0 + i * 3600
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        pnl = 1.5 if i % 3 != 0 else -0.5
        tid = await db.insert_trade(
            timestamp=ts,
            timestamp_iso=dt.isoformat(),
            market_name="BTC" if i % 2 == 0 else "ETH",
            condition_id=f"cond_{i}",
            action="sell",
            side="YES",
            price=0.95,
            amount=10.0 + i,
            pnl=pnl,
            pnl_pct=pnl / (10.0 + i) * 100,
            reason="trigger",
            dry_run=False,
        )
        ids.append(tid)
    return ids


# ---------------------------------------------------------------------------
# Legacy tests (backward compat)
# ---------------------------------------------------------------------------

class TestLoadDailyLimits:
    def test_missing_file(self, tmp_path: Path) -> None:
        result = load_daily_limits(tmp_path)
        assert result["current_pnl"] == 0.0

    def test_valid_file(self, tmp_path: Path) -> None:
        data = {"date": "2026-02-12", "current_pnl": 5.5, "total_trades": 3}
        (tmp_path / "daily_limits.json").write_text(json.dumps(data))
        result = load_daily_limits(tmp_path)
        assert result["current_pnl"] == 5.5
        assert result["total_trades"] == 3


class TestParseTradelogs:
    def test_empty_dir(self, tmp_path: Path) -> None:
        trades = parse_trade_logs(tmp_path)
        assert trades == []

    def test_no_matching_lines(self, tmp_path: Path) -> None:
        (tmp_path / "trades-20260212-100000.log").write_text(
            "2026-02-12 10:00:00 - INFO - Starting trader\n"
        )
        trades = parse_trade_logs(tmp_path)
        assert trades == []


class TestComputePnlReport:
    def test_no_trades(self) -> None:
        report = compute_pnl_report([], {"current_pnl": 0.0})
        assert report.total_trades == 0
        assert report.win_rate == 0.0
        assert report.avg_trade_size == 0.0
        assert report.best_trade is None
        assert report.worst_trade is None

    def test_with_trades(self) -> None:
        trades = [
            TradeRecord("ts1", "BTC", "YES", 0.55, 10.0, pnl=2.5),
            TradeRecord("ts2", "BTC", "NO", 0.45, 20.0, pnl=-1.0),
            TradeRecord("ts3", "ETH", "YES", 0.60, 15.0, pnl=3.0),
        ]
        report = compute_pnl_report(trades, {"current_pnl": 4.5})
        assert report.daily_pnl == 4.5
        assert report.total_pnl == 4.5  # sum of pnl values
        assert report.total_trades == 3
        assert report.winning_trades == 2
        assert report.losing_trades == 1
        assert abs(report.win_rate - 66.7) < 0.1
        assert report.avg_trade_size == 15.0
        assert report.best_trade == 3.0
        assert report.worst_trade == -1.0

    def test_trades_without_pnl(self) -> None:
        trades = [
            TradeRecord("ts1", "BTC", "YES", 0.55, 10.0, pnl=None),
        ]
        report = compute_pnl_report(trades, {"current_pnl": 1.0})
        assert report.total_trades == 1
        assert report.win_rate == 0.0
        assert report.best_trade is None


class TestFormatReport:
    def test_format_with_data(self) -> None:
        report = PnLReport(
            daily_pnl=5.0,
            total_pnl=10.0,
            total_trades=5,
            winning_trades=3,
            losing_trades=2,
            win_rate=60.0,
            avg_trade_size=15.0,
            best_trade=3.0,
            worst_trade=-1.0,
        )
        output = format_report(report)
        assert "PnL Dashboard" in output
        assert "+5.00" in output
        assert "+10.00" in output
        assert "60.0%" in output

    def test_format_no_trades(self) -> None:
        report = PnLReport(
            daily_pnl=0.0,
            total_pnl=0.0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0.0,
            avg_trade_size=0.0,
            best_trade=None,
            worst_trade=None,
        )
        output = format_report(report)
        assert "N/A" in output


# ---------------------------------------------------------------------------
# SQLite-backed report tests
# ---------------------------------------------------------------------------

class TestWinRate:
    def test_zero_total(self) -> None:
        assert _win_rate(0, 0) == 0.0

    def test_all_wins(self) -> None:
        assert _win_rate(5, 5) == 100.0

    def test_partial(self) -> None:
        assert abs(_win_rate(3, 10) - 30.0) < 0.01


@pytest.mark.asyncio
class TestLoadReportFromSQLite:
    async def test_empty_db(self, db: TradeDatabase) -> None:
        report = await load_report_from_sqlite(db, date="2026-02-12")
        assert report.total_trades == 0
        assert report.win_rate == 0.0
        assert report.by_market == {}
        assert report.by_hour == {}
        assert report.by_weekday == {}
        assert report.equity_curve == []

    async def test_with_trades(self, db: TradeDatabase) -> None:
        await _insert_sample_trades(db, count=5)
        report = await load_report_from_sqlite(db, date=None, limit=100)
        assert report.total_trades == 5
        assert report.winning_trades > 0
        assert report.losing_trades > 0

    async def test_by_market_breakdown(self, db: TradeDatabase) -> None:
        await _insert_sample_trades(db, count=6)
        report = await load_report_from_sqlite(db, date=None, limit=100)
        assert "BTC" in report.by_market
        assert "ETH" in report.by_market
        assert report.by_market["BTC"]["trades"] > 0
        assert report.by_market["ETH"]["trades"] > 0

    async def test_by_hour_breakdown(self, db: TradeDatabase) -> None:
        await _insert_sample_trades(db, count=5)
        report = await load_report_from_sqlite(db, date=None, limit=100)
        assert len(report.by_hour) > 0
        for h, d in report.by_hour.items():
            assert 0 <= h <= 23
            assert d["trades"] > 0

    async def test_by_weekday_breakdown(self, db: TradeDatabase) -> None:
        await _insert_sample_trades(db, count=5)
        report = await load_report_from_sqlite(db, date=None, limit=100)
        assert len(report.by_weekday) > 0
        for day_name in report.by_weekday:
            assert day_name in WEEKDAY_NAMES

    async def test_equity_curve(self, db: TradeDatabase) -> None:
        await _insert_sample_trades(db, count=5)
        report = await load_report_from_sqlite(db, date=None, limit=100)
        assert len(report.equity_curve) > 0
        # Equity curve should be cumulative
        labels, values = zip(*report.equity_curve)
        # Check that values are cumulative sums
        assert all(isinstance(v, float) for v in values)

    async def test_best_worst_trade(self, db: TradeDatabase) -> None:
        await _insert_sample_trades(db, count=5)
        report = await load_report_from_sqlite(db, date=None, limit=100)
        assert report.best_trade is not None
        assert report.worst_trade is not None
        assert report.best_trade >= report.worst_trade

    async def test_daily_pnl_from_stats(self, db: TradeDatabase) -> None:
        await db.update_daily_stats(
            "2026-02-12", pnl_delta=5.5, trade_count_delta=3,
        )
        report = await load_report_from_sqlite(db, date="2026-02-12")
        assert report.daily_pnl == 5.5


# ---------------------------------------------------------------------------
# Rich rendering tests (smoke tests — just ensure no crashes)
# ---------------------------------------------------------------------------

class TestRichRendering:
    def test_render_empty_report(self) -> None:
        from rich.console import Console
        from io import StringIO

        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=100)
        report = PnLReport(
            daily_pnl=0.0, total_pnl=0.0, total_trades=0,
            winning_trades=0, losing_trades=0, win_rate=0.0,
            avg_trade_size=0.0, best_trade=None, worst_trade=None,
        )
        render_rich_report(report, console=console)
        output = buf.getvalue()
        assert "PnL Dashboard" in output

    def test_render_full_report(self) -> None:
        from rich.console import Console
        from io import StringIO

        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=100)
        report = PnLReport(
            daily_pnl=5.0, total_pnl=12.5, total_trades=10,
            winning_trades=7, losing_trades=3, win_rate=70.0,
            avg_trade_size=11.0, best_trade=4.0, worst_trade=-1.5,
            by_market={
                "BTC": {"trades": 6, "wins": 4, "losses": 2, "pnl": 8.0},
                "ETH": {"trades": 4, "wins": 3, "losses": 1, "pnl": 4.5},
            },
            by_hour={
                14: {"trades": 5, "wins": 3, "losses": 2, "pnl": 3.0},
                15: {"trades": 5, "wins": 4, "losses": 1, "pnl": 9.5},
            },
            by_weekday={
                "Mon": {"trades": 3, "wins": 2, "losses": 1, "pnl": 4.0},
                "Tue": {"trades": 7, "wins": 5, "losses": 2, "pnl": 8.5},
            },
            equity_curve=[
                ("2026-02-12T14:0", 1.5),
                ("2026-02-12T14:3", 3.0),
                ("2026-02-12T15:0", 2.5),
                ("2026-02-12T15:3", 5.0),
                ("2026-02-12T16:0", 12.5),
            ],
        )
        render_rich_report(report, console=console)
        output = buf.getvalue()
        assert "PnL Dashboard" in output
        assert "Win Rate by Market" in output
        assert "Win Rate by Hour" in output
        assert "Win Rate by Weekday" in output
        assert "Equity Curve" in output

    def test_render_negative_equity(self) -> None:
        from rich.console import Console
        from io import StringIO

        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=100)
        report = PnLReport(
            daily_pnl=-3.0, total_pnl=-5.0, total_trades=4,
            winning_trades=1, losing_trades=3, win_rate=25.0,
            avg_trade_size=10.0, best_trade=1.0, worst_trade=-3.0,
            equity_curve=[
                ("2026-02-12T10:0", -1.0),
                ("2026-02-12T11:0", -3.0),
                ("2026-02-12T12:0", -2.0),
                ("2026-02-12T13:0", -5.0),
            ],
        )
        render_rich_report(report, console=console)
        output = buf.getvalue()
        assert "PnL Dashboard" in output
