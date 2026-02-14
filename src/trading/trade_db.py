"""TradeDatabase — SQLite storage for Polymarket trading bot.

Uses aiosqlite with WAL mode for concurrent reads.
Schema version migrations applied automatically on initialize().
"""

from __future__ import annotations

import logging
import time
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema & Migrations
# ---------------------------------------------------------------------------

_CREATE_SCHEMA_VERSION = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_V1_TABLES = """
-- trades
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    timestamp_iso TEXT NOT NULL,
    market_name TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    action TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    amount REAL NOT NULL,
    order_id TEXT,
    status TEXT,
    pnl REAL,
    pnl_pct REAL,
    reason TEXT,
    dry_run INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
CREATE INDEX IF NOT EXISTS idx_trades_market ON trades(market_name);
CREATE INDEX IF NOT EXISTS idx_trades_condition ON trades(condition_id);
CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(date(timestamp_iso));

-- positions
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id TEXT NOT NULL UNIQUE,
    market_name TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price REAL NOT NULL,
    trailing_stop_price REAL,
    is_open INTEGER NOT NULL DEFAULT 1,
    opened_at REAL NOT NULL,
    closed_at REAL,
    close_reason TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_positions_open ON positions(is_open);
CREATE INDEX IF NOT EXISTS idx_positions_condition ON positions(condition_id);

-- order_book_snapshots
CREATE TABLE IF NOT EXISTS order_book_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    condition_id TEXT NOT NULL,
    best_ask_yes REAL,
    best_bid_yes REAL,
    best_ask_yes_size REAL,
    best_bid_yes_size REAL,
    best_ask_no REAL,
    best_bid_no REAL,
    best_ask_no_size REAL,
    best_bid_no_size REAL,
    winning_side TEXT,
    time_remaining REAL
);
CREATE INDEX IF NOT EXISTS idx_ob_ts ON order_book_snapshots(timestamp);
CREATE INDEX IF NOT EXISTS idx_ob_condition_ts ON order_book_snapshots(condition_id, timestamp);

-- alerts
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    alert_type TEXT NOT NULL,
    level TEXT NOT NULL,
    market_name TEXT,
    details_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(timestamp);
CREATE INDEX IF NOT EXISTS idx_alerts_type ON alerts(alert_type);

-- daily_stats
CREATE TABLE IF NOT EXISTS daily_stats (
    date TEXT PRIMARY KEY,
    initial_balance REAL,
    current_pnl REAL NOT NULL DEFAULT 0,
    total_trades INTEGER NOT NULL DEFAULT 0,
    winning_trades INTEGER NOT NULL DEFAULT 0,
    losing_trades INTEGER NOT NULL DEFAULT 0,
    total_volume REAL NOT NULL DEFAULT 0,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- events (replay)
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    timestamp REAL NOT NULL,
    event_type TEXT NOT NULL,
    condition_id TEXT,
    market_name TEXT,
    data_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_session_ts ON events(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
"""


async def _apply_v1(db: aiosqlite.Connection) -> None:
    """Create all v1 tables and indices."""
    await db.executescript(_V1_TABLES)


# List of (version, coroutine_factory).  Each is applied once, in order.
MIGRATIONS: list[tuple[int, Any]] = [
    (1, _apply_v1),
]


# ---------------------------------------------------------------------------
# TradeDatabase
# ---------------------------------------------------------------------------

class TradeDatabase:
    """Async SQLite database for the Polymarket trading bot."""

    def __init__(self, db: aiosqlite.Connection, db_path: str) -> None:
        self._db = db
        self._db_path = db_path
        self._ob_buffer: list[dict] = []
        self._ob_buffer_limit = 50

    # -- lifecycle -----------------------------------------------------------

    @classmethod
    async def initialize(cls, db_path: str = "data/trades.db") -> "TradeDatabase":
        """Open (or create) the database, enable WAL, run migrations."""
        db = await aiosqlite.connect(db_path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")
        instance = cls(db, db_path)
        await instance.migrate()
        return instance

    async def close(self) -> None:
        if self._ob_buffer:
            await self.flush_orderbook_buffer()
        await self._db.close()

    async def migrate(self) -> None:
        await self._db.executescript(_CREATE_SCHEMA_VERSION)
        async with self._db.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_version"
        ) as cur:
            row = await cur.fetchone()
            current = row[0] if row else 0

        for ver, fn in MIGRATIONS:
            if ver > current:
                logger.info("Applying migration v%d", ver)
                await fn(self._db)
                await self._db.execute(
                    "INSERT INTO schema_version (version) VALUES (?)", (ver,)
                )
                await self._db.commit()
        logger.info("Database ready (v%d) at %s", len(MIGRATIONS), self._db_path)

    # -- trades --------------------------------------------------------------

    async def insert_trade(
        self,
        *,
        timestamp: float,
        timestamp_iso: str,
        market_name: str,
        condition_id: str,
        action: str,
        side: str,
        price: float,
        amount: float,
        order_id: str | None = None,
        status: str | None = None,
        pnl: float | None = None,
        pnl_pct: float | None = None,
        reason: str | None = None,
        dry_run: bool = True,
    ) -> int:
        cur = await self._db.execute(
            """INSERT INTO trades
               (timestamp, timestamp_iso, market_name, condition_id,
                action, side, price, amount, order_id, status,
                pnl, pnl_pct, reason, dry_run)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                timestamp, timestamp_iso, market_name, condition_id,
                action, side, price, amount, order_id, status,
                pnl, pnl_pct, reason, int(dry_run),
            ),
        )
        await self._db.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def get_trades(
        self,
        *,
        market: str | None = None,
        date: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[Any] = []
        if market:
            clauses.append("market_name = ?")
            params.append(market)
        if date:
            clauses.append("date(timestamp_iso) = ?")
            params.append(date)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        async with self._db.execute(
            f"SELECT * FROM trades {where} ORDER BY timestamp DESC LIMIT ?", params
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    # -- positions -----------------------------------------------------------

    async def save_position(self, condition_id: str, state: dict) -> None:
        await self._db.execute(
            """INSERT INTO positions
               (condition_id, market_name, side, entry_price,
                trailing_stop_price, is_open, opened_at, closed_at,
                close_reason, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(condition_id) DO UPDATE SET
                 market_name=excluded.market_name,
                 side=excluded.side,
                 entry_price=excluded.entry_price,
                 trailing_stop_price=excluded.trailing_stop_price,
                 is_open=excluded.is_open,
                 opened_at=excluded.opened_at,
                 closed_at=excluded.closed_at,
                 close_reason=excluded.close_reason,
                 updated_at=CURRENT_TIMESTAMP""",
            (
                condition_id,
                state.get("market_name", ""),
                state.get("side", "YES"),
                state.get("entry_price", 0),
                state.get("trailing_stop_price"),
                int(state.get("is_open", True)),
                state.get("opened_at", time.time()),
                state.get("closed_at"),
                state.get("close_reason"),
            ),
        )
        await self._db.commit()

    async def load_position(self, condition_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM positions WHERE condition_id = ?", (condition_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def close_position(self, condition_id: str, reason: str) -> None:
        await self._db.execute(
            """UPDATE positions
               SET is_open=0, closed_at=?, close_reason=?, updated_at=CURRENT_TIMESTAMP
               WHERE condition_id=? AND is_open=1""",
            (time.time(), reason, condition_id),
        )
        await self._db.commit()

    async def get_open_positions(self) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM positions WHERE is_open = 1"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # -- orderbook snapshots (buffered) --------------------------------------

    async def buffer_orderbook_snapshot(self, **kwargs: Any) -> None:
        self._ob_buffer.append(kwargs)
        if len(self._ob_buffer) >= self._ob_buffer_limit:
            await self.flush_orderbook_buffer()

    async def flush_orderbook_buffer(self) -> None:
        if not self._ob_buffer:
            return
        await self._db.executemany(
            """INSERT INTO order_book_snapshots
               (timestamp, condition_id,
                best_ask_yes, best_bid_yes, best_ask_yes_size, best_bid_yes_size,
                best_ask_no, best_bid_no, best_ask_no_size, best_bid_no_size,
                winning_side, time_remaining)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    s.get("timestamp", time.time()),
                    s.get("condition_id", ""),
                    s.get("best_ask_yes"),
                    s.get("best_bid_yes"),
                    s.get("best_ask_yes_size"),
                    s.get("best_bid_yes_size"),
                    s.get("best_ask_no"),
                    s.get("best_bid_no"),
                    s.get("best_ask_no_size"),
                    s.get("best_bid_no_size"),
                    s.get("winning_side"),
                    s.get("time_remaining"),
                )
                for s in self._ob_buffer
            ],
        )
        await self._db.commit()
        flushed = len(self._ob_buffer)
        self._ob_buffer.clear()
        logger.debug("Flushed %d orderbook snapshots", flushed)

    # -- alerts --------------------------------------------------------------

    async def insert_alert(
        self,
        *,
        timestamp: float,
        alert_type: str,
        level: str,
        market_name: str | None = None,
        details_json: str | None = None,
    ) -> int:
        cur = await self._db.execute(
            """INSERT INTO alerts (timestamp, alert_type, level, market_name, details_json)
               VALUES (?,?,?,?,?)""",
            (timestamp, alert_type, level, market_name, details_json),
        )
        await self._db.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def get_alerts(
        self,
        *,
        since: float | None = None,
        alert_type: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[Any] = []
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since)
        if alert_type:
            clauses.append("alert_type = ?")
            params.append(alert_type)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        async with self._db.execute(
            f"SELECT * FROM alerts {where} ORDER BY timestamp DESC LIMIT ?", params
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # -- daily stats ---------------------------------------------------------

    async def get_or_create_daily_stats(self, date: str) -> dict:
        async with self._db.execute(
            "SELECT * FROM daily_stats WHERE date = ?", (date,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return dict(row)
        await self._db.execute(
            "INSERT INTO daily_stats (date) VALUES (?)", (date,)
        )
        await self._db.commit()
        async with self._db.execute(
            "SELECT * FROM daily_stats WHERE date = ?", (date,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row)  # type: ignore[arg-type]

    async def update_daily_stats(
        self, date: str, *, pnl_delta: float = 0, trade_count_delta: int = 0,
        winning_delta: int = 0, losing_delta: int = 0, volume_delta: float = 0,
    ) -> None:
        await self.get_or_create_daily_stats(date)
        await self._db.execute(
            """UPDATE daily_stats SET
                 current_pnl = current_pnl + ?,
                 total_trades = total_trades + ?,
                 winning_trades = winning_trades + ?,
                 losing_trades = losing_trades + ?,
                 total_volume = total_volume + ?,
                 updated_at = CURRENT_TIMESTAMP
               WHERE date = ?""",
            (pnl_delta, trade_count_delta, winning_delta, losing_delta, volume_delta, date),
        )
        await self._db.commit()

    # -- events (replay) -----------------------------------------------------

    async def insert_event(
        self,
        *,
        session_id: str,
        timestamp: float,
        event_type: str,
        condition_id: str | None = None,
        market_name: str | None = None,
        data_json: str,
    ) -> int:
        cur = await self._db.execute(
            """INSERT INTO events
               (session_id, timestamp, event_type, condition_id, market_name, data_json)
               VALUES (?,?,?,?,?,?)""",
            (session_id, timestamp, event_type, condition_id, market_name, data_json),
        )
        await self._db.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def get_events(self, session_id: str) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM events WHERE session_id = ? ORDER BY timestamp", (session_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def list_sessions(self) -> list[dict]:
        async with self._db.execute(
            """SELECT session_id, MIN(timestamp) as start_ts,
                      MAX(timestamp) as end_ts, COUNT(*) as event_count
               FROM events GROUP BY session_id ORDER BY start_ts DESC"""
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # -- maintenance ---------------------------------------------------------

    async def cleanup_old_snapshots(self, days: int = 7) -> int:
        cutoff = time.time() - days * 86400
        cur = await self._db.execute(
            "DELETE FROM order_book_snapshots WHERE timestamp < ?", (cutoff,)
        )
        await self._db.commit()
        return cur.rowcount  # type: ignore[return-value]
