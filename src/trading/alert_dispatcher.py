"""
Alert dispatcher for sending trading alerts.

Wraps AlertManager (from src.alerts) with higher-level features:
rate-limiting by key, alert history (JSON + SQLite), and daily summaries.

Canonical types (AlertLevel, AlertManager, senders) live in src.alerts.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from src.alerts import AlertLevel, AlertManager

_logger = logging.getLogger(__name__)


LEVEL_EMOJI = {
    AlertLevel.INFO: "ℹ️",
    AlertLevel.WARNING: "⚠️",
    AlertLevel.CRITICAL: "🚨",
}

DEFAULT_RATE_LIMIT_SECONDS = 300  # 5 minutes
ALERT_HISTORY_PATH = Path("log/alert_history.json")


class AlertDispatcher:
    """
    Dispatcher for trading alerts.

    Wraps AlertManager to provide a clean interface for sending various
    types of trading alerts (trade execution, stop-loss, take-profit, etc.).

    Features:
    - Alert levels (INFO, WARNING, CRITICAL)
    - Rate limiting (no duplicate alerts within 5 minutes)
    - Alert history (saved to log/alert_history.json)
    - Summary alerts (daily summary)
    """

    def __init__(
        self,
        alert_manager: AlertManager | None,
        rate_limit_seconds: float = DEFAULT_RATE_LIMIT_SECONDS,
        history_path: Path = ALERT_HISTORY_PATH,
        trade_db: Any | None = None,
    ):
        self.alert_manager = alert_manager
        self.rate_limit_seconds = rate_limit_seconds
        self.history_path = history_path
        self._trade_db = trade_db
        self._last_sent: dict[str, float] = {}
        self._history: list[dict] = []
        self._load_history()

    def _load_history(self) -> None:
        """Load alert history from file."""
        if self.history_path.exists():
            try:
                with open(self.history_path) as f:
                    self._history = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._history = []

    def _save_history(self) -> None:
        """Save alert history to file."""
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.history_path, "w") as f:
                json.dump(self._history, f, indent=2)
        except OSError:
            pass

    def _record_alert(self, alert_type: str, level: AlertLevel, details: dict) -> None:
        """Record an alert in history (JSON + SQLite)."""
        ts = time.time()
        entry = {
            "timestamp": ts,
            "type": alert_type,
            "level": level.value,
            **details,
        }
        self._history.append(entry)
        self._save_history()

        # Also write to SQLite if available
        if self._trade_db is not None:
            try:
                import asyncio
                try:
                    asyncio.get_running_loop()
                    # Already in event loop — can't use run_until_complete
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        pool.submit(asyncio.run, self._trade_db.insert_alert(
                            timestamp=ts,
                            alert_type=alert_type,
                            level=level.value,
                            market_name=details.get("market"),
                            details_json=json.dumps(details) if details else None,
                        )).result(timeout=5)
                except RuntimeError:
                    asyncio.run(self._trade_db.insert_alert(
                        timestamp=ts,
                        alert_type=alert_type,
                        level=level.value,
                        market_name=details.get("market"),
                        details_json=json.dumps(details) if details else None,
                    ))
            except Exception as e:
                _logger.warning("Failed to write alert to SQLite: %s", e)

    def _is_rate_limited(self, key: str) -> bool:
        """Check if an alert key is rate-limited."""
        last = self._last_sent.get(key)
        if last is None:
            return False
        return (time.time() - last) < self.rate_limit_seconds

    def _mark_sent(self, key: str) -> None:
        """Mark an alert key as sent."""
        self._last_sent[key] = time.time()

    def is_enabled(self) -> bool:
        """Check if alerts are enabled."""
        return self.alert_manager is not None and self.alert_manager.is_enabled()

    def get_history(self) -> list[dict]:
        """Get alert history."""
        return list(self._history)

    def get_summary(self) -> dict:
        """Generate a daily summary of alerts."""
        now = time.time()
        day_start = now - 86400
        recent = [a for a in self._history if a.get("timestamp", 0) >= day_start]

        by_level: dict[str, int] = {}
        by_type: dict[str, int] = {}
        for a in recent:
            lvl = a.get("level", "INFO")
            by_level[lvl] = by_level.get(lvl, 0) + 1
            atype = a.get("type", "unknown")
            by_type[atype] = by_type.get(atype, 0) + 1

        return {
            "total_alerts": len(recent),
            "by_level": by_level,
            "by_type": by_type,
            "period_hours": 24,
        }

    async def send_trade_alert(
        self,
        market: str,
        side: str,
        entry_price: float,
        amount: float,
        pnl: float | None = None,
        level: AlertLevel = AlertLevel.INFO,
    ) -> None:
        """Send a trade execution alert."""
        alert_manager = self.alert_manager
        if alert_manager is None or not alert_manager.is_enabled():
            return

        key = f"trade:{market}:{side}:{entry_price}"
        if self._is_rate_limited(key):
            return

        trade_data = {
            "market": market,
            "side": side,
            "entry_price": entry_price,
            "amount": amount,
        }
        if pnl is not None:
            trade_data["pnl"] = pnl

        await alert_manager.send_trade_alert(trade_data)
        self._mark_sent(key)
        self._record_alert("trade", level, trade_data)

    async def send_stop_loss_alert(
        self,
        market: str,
        pnl: float,
        entry_price: float | None = None,
        exit_price: float | None = None,
        level: AlertLevel = AlertLevel.WARNING,
    ) -> None:
        """Send a stop-loss trigger alert."""
        alert_manager = self.alert_manager
        if alert_manager is None or not alert_manager.is_enabled():
            return

        key = f"stop_loss:{market}"
        if self._is_rate_limited(key):
            return

        await alert_manager.send_stop_loss_alert(market, pnl, entry_price, exit_price)
        self._mark_sent(key)
        self._record_alert("stop_loss", level, {"market": market, "pnl": pnl})

    async def send_take_profit_alert(
        self,
        market: str,
        pnl: float,
        entry_price: float | None = None,
        exit_price: float | None = None,
        level: AlertLevel = AlertLevel.INFO,
    ) -> None:
        """Send a take-profit trigger alert."""
        alert_manager = self.alert_manager
        if alert_manager is None or not alert_manager.is_enabled():
            return

        key = f"take_profit:{market}"
        if self._is_rate_limited(key):
            return

        await alert_manager.send_take_profit_alert(market, pnl, entry_price, exit_price)
        self._mark_sent(key)
        self._record_alert("take_profit", level, {"market": market, "pnl": pnl})

    async def send_oracle_guard_block(
        self,
        market: str,
        reason: str,
        detail: str = "",
        level: AlertLevel = AlertLevel.CRITICAL,
    ) -> None:
        """Send an Oracle Guard block alert."""
        alert_manager = self.alert_manager
        if alert_manager is None or not alert_manager.is_enabled():
            return

        key = f"oracle_guard:{market}:{reason}"
        if self._is_rate_limited(key):
            return

        await alert_manager.send_oracle_guard_block(market, reason, detail)
        self._mark_sent(key)
        self._record_alert("oracle_guard", level, {"market": market, "reason": reason})

    async def send_daily_report_summary(self, report_summary: str) -> None:
        """Send a daily report summary."""
        alert_manager = self.alert_manager
        if alert_manager is None or not alert_manager.is_enabled():
            return

        await alert_manager.send_daily_report_summary(report_summary)
        self._record_alert("daily_summary", AlertLevel.INFO, {"summary": report_summary})

    async def send_summary_alert(self) -> None:
        """Send a daily summary alert with alert statistics."""
        alert_manager = self.alert_manager
        if alert_manager is None or not alert_manager.is_enabled():
            return

        summary = self.get_summary()
        text = (
            f"📊 Daily Alert Summary\n"
            f"Total alerts: {summary['total_alerts']}\n"
            f"By level: {summary['by_level']}\n"
            f"By type: {summary['by_type']}"
        )
        await alert_manager.send_daily_report_summary(text)
        self._record_alert("alert_summary", AlertLevel.INFO, summary)
