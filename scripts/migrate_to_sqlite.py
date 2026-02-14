#!/usr/bin/env python3
"""Migrate JSON data files to SQLite (TradeDatabase).

Migrates:
  - log/daily_limits.json  → daily_stats table
  - log/alert_history.json → alerts table
  - data/positions/*.json  → positions table
  - data/replays/*.jsonl   → events table

After successful migration, originals are renamed to *.bak.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.trading.trade_db import TradeDatabase

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


async def migrate_daily_limits(db: TradeDatabase, path: Path) -> int:
    """Migrate daily_limits.json → daily_stats."""
    if not path.exists():
        log.info("No daily_limits.json found, skipping")
        return 0

    data = json.loads(path.read_text())
    date_str = data.get("date")
    if not date_str:
        log.warning("daily_limits.json has no 'date' field, skipping")
        return 0

    await db.get_or_create_daily_stats(date_str)
    await db.update_daily_stats(
        date_str,
        pnl_delta=data.get("current_pnl", 0.0),
        trade_count_delta=data.get("total_trades", 0),
    )
    # Set initial_balance if present
    ib = data.get("initial_balance")
    if ib is not None:
        await db._db.execute(
            "UPDATE daily_stats SET initial_balance = ? WHERE date = ?",
            (ib, date_str),
        )
        await db._db.commit()

    path.rename(path.with_suffix(".json.bak"))
    log.info("Migrated daily_limits.json → daily_stats (date=%s)", date_str)
    return 1


async def migrate_alert_history(db: TradeDatabase, path: Path) -> int:
    """Migrate alert_history.json → alerts."""
    if not path.exists():
        log.info("No alert_history.json found, skipping")
        return 0

    try:
        alerts = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Failed to read alert_history.json: %s", e)
        return 0

    if not isinstance(alerts, list):
        log.warning("alert_history.json is not a list, skipping")
        return 0

    count = 0
    for alert in alerts:
        ts = alert.get("timestamp", 0.0)
        alert_type = alert.get("type", "unknown")
        level = alert.get("level", "INFO")
        market_name = alert.get("market")
        # Everything except timestamp/type/level/market goes into details_json
        details = {k: v for k, v in alert.items()
                   if k not in ("timestamp", "type", "level", "market")}
        await db.insert_alert(
            timestamp=ts,
            alert_type=alert_type,
            level=level,
            market_name=market_name,
            details_json=json.dumps(details) if details else None,
        )
        count += 1

    path.rename(path.with_suffix(".json.bak"))
    log.info("Migrated %d alerts → alerts table", count)
    return count


async def migrate_positions(db: TradeDatabase, positions_dir: Path) -> int:
    """Migrate data/positions/*.json → positions table."""
    if not positions_dir.exists():
        log.info("No positions directory found, skipping")
        return 0

    count = 0
    for f in positions_dir.glob("position_*.json"):
        try:
            data = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to read %s: %s", f.name, e)
            continue

        cid = data.get("condition_id", "")
        if not cid:
            log.warning("Position file %s has no condition_id, skipping", f.name)
            continue

        await db.save_position(cid, {
            "market_name": data.get("market_name", ""),
            "side": data.get("side", "YES"),
            "entry_price": data.get("entry_price", 0),
            "trailing_stop_price": data.get("trailing_stop_price"),
            "is_open": True,
            "opened_at": data.get("timestamp", 0),
        })
        f.rename(f.with_suffix(".json.bak"))
        count += 1

    log.info("Migrated %d position files → positions table", count)
    return count


async def migrate_replays(db: TradeDatabase, replays_dir: Path) -> int:
    """Migrate data/replays/*.jsonl → events table."""
    if not replays_dir.exists():
        log.info("No replays directory found, skipping")
        return 0

    count = 0
    for f in sorted(replays_dir.glob("replay_*.jsonl")):
        session_id = str(uuid.uuid4())
        events_in_file = 0
        # Read first line for metadata
        market_name = ""
        condition_id = ""

        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                event_type = data.get("type", "unknown")
                ts = data.get("ts", 0.0)
                event_data = data.get("data", {})

                if event_type == "session_start":
                    market_name = event_data.get("market_name", "")
                    condition_id = event_data.get("condition_id", "")

                await db.insert_event(
                    session_id=session_id,
                    timestamp=ts,
                    event_type=event_type,
                    condition_id=condition_id or None,
                    market_name=market_name or None,
                    data_json=json.dumps(event_data),
                )
                events_in_file += 1

        if events_in_file > 0:
            f.rename(f.with_suffix(".jsonl.bak"))
            count += events_in_file
            log.info("Migrated %s (%d events, session=%s)", f.name, events_in_file, session_id[:8])

    log.info("Migrated %d total replay events → events table", count)
    return count


async def main() -> None:
    db_path = str(PROJECT_ROOT / "data" / "trades.db")
    log.info("Opening database at %s", db_path)

    db = await TradeDatabase.initialize(db_path)

    try:
        total = 0
        total += await migrate_daily_limits(db, PROJECT_ROOT / "log" / "daily_limits.json")
        total += await migrate_alert_history(db, PROJECT_ROOT / "log" / "alert_history.json")
        total += await migrate_positions(db, PROJECT_ROOT / "data" / "positions")
        total += await migrate_replays(db, PROJECT_ROOT / "data" / "replays")

        log.info("Migration complete. Total records migrated: %d", total)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
