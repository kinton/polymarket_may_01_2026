"""Tests for PnL Dashboard."""

from __future__ import annotations

import json
from pathlib import Path

from src.trading.pnl_dashboard import (
    PnLReport,
    TradeRecord,
    compute_pnl_report,
    format_report,
    load_daily_limits,
    parse_trade_logs,
)


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
