"""Health watchdog — alerts via Telegram if no trades in N hours.

Runs as an asyncio background task alongside the main trading loop.
Checks every 30 minutes. Suppresses repeated alerts (fires once per
staleness event, re-arms after a new trade).
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from src.alerts import AlertManager
from src.trading.trade_db import TradeDatabase

logger = logging.getLogger(__name__)

CHECK_INTERVAL_S = 30 * 60  # 30 minutes


async def watchdog_loop(
    db: TradeDatabase,
    alert_manager: AlertManager,
    threshold_hours: float = 3.0,
    context: dict[str, str] | None = None,
) -> None:
    """Run the trade-staleness watchdog.

    Args:
        db: TradeDatabase instance to query last trade time.
        alert_manager: AlertManager for sending alerts.
        threshold_hours: Hours without a trade before alerting.
        context: Strategy context dict (strategy/version/mode) for alert prefix.
    """

    db_name = Path(db._db_path).name

    logger.info("Watchdog active, threshold: %gh", threshold_hours)

    # Track whether we already fired an alert for the current staleness event.
    _alert_fired = False
    # Remember the last trade timestamp that re-armed the watchdog.
    _last_seen_ts: float | None = None

    while True:
        try:
            last_trade_ts = await db.get_last_trade_timestamp()

            # Re-arm: if a new trade appeared since we last fired, reset.
            if last_trade_ts is not None and last_trade_ts != _last_seen_ts:
                if _alert_fired:
                    logger.info("Watchdog re-armed (new trade detected)")
                _alert_fired = False
                _last_seen_ts = last_trade_ts

            if last_trade_ts is None:
                # No trades at all — treat bot start as the baseline;
                # don't alert until threshold elapses from now.
                if _last_seen_ts is None:
                    _last_seen_ts = time.time()
                hours_since = (time.time() - _last_seen_ts) / 3600
            else:
                hours_since = (time.time() - last_trade_ts) / 3600

            if hours_since >= threshold_hours and not _alert_fired:
                msg = (
                    f"⚠️ WATCHDOG: No trades in {hours_since:.1f}h "
                    f"(threshold: {threshold_hours:g}h)\n"
                    f"DB: {db_name}"
                )
                logger.warning(msg)
                await alert_manager.broadcast_alert(msg)
                _alert_fired = True

        except Exception:
            logger.exception("Watchdog check failed")

        await asyncio.sleep(CHECK_INTERVAL_S)
