"""Tests for alert dispatcher improvements (levels, rate limiting, history, summary)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.trading.alert_dispatcher import AlertDispatcher, AlertLevel


def make_dispatcher(
    tmp_path: Path,
    enabled: bool = True,
    rate_limit: float = 300.0,
) -> tuple[AlertDispatcher, MagicMock]:
    """Create a dispatcher with a mock alert manager."""
    am = MagicMock()
    am.is_enabled.return_value = enabled
    am.send_trade_alert = AsyncMock()
    am.send_stop_loss_alert = AsyncMock()
    am.send_take_profit_alert = AsyncMock()
    am.send_oracle_guard_block = AsyncMock()
    am.send_daily_report_summary = AsyncMock()

    history_path = tmp_path / "alert_history.json"
    dispatcher = AlertDispatcher(am, rate_limit_seconds=rate_limit, history_path=history_path)
    return dispatcher, am


class TestAlertLevels:
    def test_alert_level_values(self) -> None:
        assert AlertLevel.INFO.value == "INFO"
        assert AlertLevel.WARNING.value == "WARNING"
        assert AlertLevel.CRITICAL.value == "CRITICAL"

    @pytest.mark.asyncio
    async def test_trade_alert_default_level(self, tmp_path: Path) -> None:
        d, am = make_dispatcher(tmp_path)
        await d.send_trade_alert("BTC", "YES", 0.55, 10.0)
        am.send_trade_alert.assert_called_once()
        assert d._history[-1]["level"] == "INFO"

    @pytest.mark.asyncio
    async def test_stop_loss_default_level(self, tmp_path: Path) -> None:
        d, am = make_dispatcher(tmp_path)
        await d.send_stop_loss_alert("BTC", -5.0)
        assert d._history[-1]["level"] == "WARNING"

    @pytest.mark.asyncio
    async def test_oracle_guard_default_level(self, tmp_path: Path) -> None:
        d, am = make_dispatcher(tmp_path)
        await d.send_oracle_guard_block("BTC", "price_deviation")
        assert d._history[-1]["level"] == "CRITICAL"

    @pytest.mark.asyncio
    async def test_custom_level(self, tmp_path: Path) -> None:
        d, am = make_dispatcher(tmp_path)
        await d.send_trade_alert("BTC", "YES", 0.55, 10.0, level=AlertLevel.CRITICAL)
        assert d._history[-1]["level"] == "CRITICAL"


class TestRateLimiting:
    @pytest.mark.asyncio
    async def test_duplicate_blocked(self, tmp_path: Path) -> None:
        d, am = make_dispatcher(tmp_path, rate_limit=300.0)
        await d.send_trade_alert("BTC", "YES", 0.55, 10.0)
        await d.send_trade_alert("BTC", "YES", 0.55, 10.0)
        assert am.send_trade_alert.call_count == 1

    @pytest.mark.asyncio
    async def test_different_keys_not_blocked(self, tmp_path: Path) -> None:
        d, am = make_dispatcher(tmp_path, rate_limit=300.0)
        await d.send_trade_alert("BTC", "YES", 0.55, 10.0)
        await d.send_trade_alert("ETH", "NO", 0.45, 20.0)
        assert am.send_trade_alert.call_count == 2

    @pytest.mark.asyncio
    async def test_expired_rate_limit(self, tmp_path: Path) -> None:
        d, am = make_dispatcher(tmp_path, rate_limit=0.0)  # instant expiry
        await d.send_trade_alert("BTC", "YES", 0.55, 10.0)
        await d.send_trade_alert("BTC", "YES", 0.55, 10.0)
        assert am.send_trade_alert.call_count == 2

    @pytest.mark.asyncio
    async def test_stop_loss_rate_limited(self, tmp_path: Path) -> None:
        d, am = make_dispatcher(tmp_path, rate_limit=300.0)
        await d.send_stop_loss_alert("BTC", -5.0)
        await d.send_stop_loss_alert("BTC", -5.0)
        assert am.send_stop_loss_alert.call_count == 1


class TestAlertHistory:
    @pytest.mark.asyncio
    async def test_history_saved(self, tmp_path: Path) -> None:
        d, am = make_dispatcher(tmp_path)
        await d.send_trade_alert("BTC", "YES", 0.55, 10.0)
        history = d.get_history()
        assert len(history) == 1
        assert history[0]["type"] == "trade"
        assert history[0]["market"] == "BTC"

    @pytest.mark.asyncio
    async def test_history_persisted_to_file(self, tmp_path: Path) -> None:
        d, am = make_dispatcher(tmp_path)
        await d.send_trade_alert("BTC", "YES", 0.55, 10.0)
        # Read file
        data = json.loads(d.history_path.read_text())
        assert len(data) == 1

    @pytest.mark.asyncio
    async def test_history_loaded_on_init(self, tmp_path: Path) -> None:
        history_path = tmp_path / "alert_history.json"
        existing = [{"timestamp": time.time(), "type": "trade", "level": "INFO"}]
        history_path.write_text(json.dumps(existing))

        am = MagicMock()
        am.is_enabled.return_value = True
        d = AlertDispatcher(am, history_path=history_path)
        assert len(d.get_history()) == 1

    def test_disabled_dispatcher(self, tmp_path: Path) -> None:
        d, _ = make_dispatcher(tmp_path, enabled=False)
        assert d.is_enabled() is False

    def test_none_manager(self, tmp_path: Path) -> None:
        d = AlertDispatcher(None, history_path=tmp_path / "h.json")
        assert d.is_enabled() is False


class TestSummary:
    @pytest.mark.asyncio
    async def test_summary_empty(self, tmp_path: Path) -> None:
        d, am = make_dispatcher(tmp_path)
        summary = d.get_summary()
        assert summary["total_alerts"] == 0

    @pytest.mark.asyncio
    async def test_summary_with_alerts(self, tmp_path: Path) -> None:
        d, am = make_dispatcher(tmp_path)
        await d.send_trade_alert("BTC", "YES", 0.55, 10.0)
        await d.send_stop_loss_alert("ETH", -3.0)
        summary = d.get_summary()
        assert summary["total_alerts"] == 2
        assert summary["by_level"]["INFO"] == 1
        assert summary["by_level"]["WARNING"] == 1
        assert summary["by_type"]["trade"] == 1
        assert summary["by_type"]["stop_loss"] == 1

    @pytest.mark.asyncio
    async def test_send_summary_alert(self, tmp_path: Path) -> None:
        d, am = make_dispatcher(tmp_path)
        await d.send_trade_alert("BTC", "YES", 0.55, 10.0)
        await d.send_summary_alert()
        am.send_daily_report_summary.assert_called_once()
        # Summary itself is recorded
        assert d._history[-1]["type"] == "alert_summary"
