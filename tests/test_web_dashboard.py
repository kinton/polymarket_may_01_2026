"""Tests for web dashboard API endpoints."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from src.web_dashboard import app as app_module
from src.web_dashboard.app import app


@pytest.fixture(autouse=True)
def _use_tmp_db(tmp_path, monkeypatch):
    """Point dashboard at a temporary database."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(app_module, "DB_PATH", db_path)
    return db_path


@pytest.fixture()
def db_path(_use_tmp_db):
    return _use_tmp_db


@pytest.fixture()
def client():
    return TestClient(app)


def _seed_db_sync(db_path: str) -> None:
    """Seed the test database with sample data (sync for setup)."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # Create tables (simplified, matching trade_db schema)
    c.executescript("""
        CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version VALUES (2);

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

        CREATE TABLE IF NOT EXISTS trade_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            timestamp_iso TEXT NOT NULL,
            market_name TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            action TEXT NOT NULL,
            side TEXT,
            price REAL,
            amount REAL,
            confidence REAL,
            time_remaining REAL,
            reason TEXT NOT NULL,
            reason_detail TEXT,
            oracle_price REAL,
            oracle_z REAL,
            oracle_vol REAL,
            oracle_delta REAL,
            oracle_n_points INTEGER,
            dry_run INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS dry_run_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER,
            condition_id TEXT NOT NULL,
            market_name TEXT NOT NULL,
            side TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL,
            amount REAL NOT NULL,
            trailing_stop REAL,
            stop_loss_price REAL,
            take_profit_price REAL,
            status TEXT NOT NULL DEFAULT 'open',
            pnl REAL,
            pnl_pct REAL,
            opened_at REAL NOT NULL,
            closed_at REAL,
            close_reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            alert_type TEXT NOT NULL,
            level TEXT NOT NULL,
            market_name TEXT,
            details_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS order_book_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            condition_id TEXT NOT NULL,
            best_ask_yes REAL, best_bid_yes REAL,
            best_ask_yes_size REAL, best_bid_yes_size REAL,
            best_ask_no REAL, best_bid_no REAL,
            best_ask_no_size REAL, best_bid_no_size REAL,
            winning_side TEXT, time_remaining REAL
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            timestamp REAL NOT NULL,
            event_type TEXT NOT NULL,
            condition_id TEXT,
            market_name TEXT,
            data_json TEXT NOT NULL
        );
    """)

    now = time.time()

    # Insert trades
    for i in range(15):
        pnl = 0.01 if i % 3 != 0 else -0.005
        c.execute(
            "INSERT INTO trades (timestamp, timestamp_iso, market_name, condition_id, "
            "action, side, price, amount, pnl, pnl_pct, reason, dry_run) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now - i * 60, f"2026-02-14T18:{i:02d}:00Z", f"Market {i % 3}",
             f"cond_{i % 3}", "buy" if i % 2 == 0 else "sell", "YES",
             0.92, 5.0, pnl, pnl / 0.92 * 100, "trigger", 1),
        )

    # Daily stats
    c.execute(
        "INSERT INTO daily_stats (date, current_pnl, total_trades, winning_trades, losing_trades) "
        "VALUES ('2026-02-14', 0.05, 15, 10, 5)"
    )

    # Trade decisions
    for i in range(10):
        action = "buy" if i < 3 else "skip"
        reason = "trigger" if action == "buy" else ["oracle_guard_blocked", "low_confidence", "no_liquidity"][i % 3]
        c.execute(
            "INSERT INTO trade_decisions (timestamp, timestamp_iso, market_name, condition_id, "
            "action, side, price, confidence, reason, dry_run) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now - i * 60, f"2026-02-14T18:{i:02d}:00Z", f"Market {i % 3}",
             f"cond_{i % 3}", action, "YES", 0.92, 0.88, reason, 1),
        )

    # Dry-run positions
    c.execute(
        "INSERT INTO dry_run_positions (condition_id, market_name, side, entry_price, "
        "amount, stop_loss_price, take_profit_price, status, pnl, pnl_pct, opened_at, "
        "closed_at, close_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("cond_0", "Market 0", "YES", 0.90, 5.0, 0.80, 0.99,
         "stop_loss", -0.05, -5.5, now - 3600, now - 1800, "stop_loss at $0.85"),
    )

    conn.commit()
    conn.close()


# ── Page Load Tests ──────────────────────────────────────────────────────────


class TestOverview:
    def test_overview_empty_db(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Overview" in resp.text

    def test_overview_with_data(self, client, db_path):
        _seed_db_sync(db_path)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Market" in resp.text
        assert "IDLE" in resp.text or "RUNNING" in resp.text


class TestTrades:
    def test_trades_empty(self, client):
        resp = client.get("/trades")
        assert resp.status_code == 200
        assert "No trades found" in resp.text

    def test_trades_with_data(self, client, db_path):
        _seed_db_sync(db_path)
        resp = client.get("/trades")
        assert resp.status_code == 200
        assert "Market 0" in resp.text

    def test_trades_filter_action(self, client, db_path):
        _seed_db_sync(db_path)
        resp = client.get("/trades?action=buy")
        assert resp.status_code == 200

    def test_trades_filter_market(self, client, db_path):
        _seed_db_sync(db_path)
        resp = client.get("/trades?market=Market+1")
        assert resp.status_code == 200

    def test_trades_pagination(self, client, db_path):
        _seed_db_sync(db_path)
        resp = client.get("/trades?per_page=10&page=2")
        assert resp.status_code == 200
        assert "Page 2" in resp.text

    def test_trades_filter_dry_run(self, client, db_path):
        _seed_db_sync(db_path)
        resp = client.get("/trades?dry_run=1")
        assert resp.status_code == 200


class TestDecisions:
    def test_decisions_empty(self, client):
        resp = client.get("/decisions")
        assert resp.status_code == 200

    def test_decisions_with_data(self, client, db_path):
        _seed_db_sync(db_path)
        resp = client.get("/decisions")
        assert resp.status_code == 200
        assert "trigger" in resp.text

    def test_decisions_filter_reason(self, client, db_path):
        _seed_db_sync(db_path)
        resp = client.get("/decisions?reason=oracle_guard_blocked")
        assert resp.status_code == 200

    def test_decisions_filter_action(self, client, db_path):
        _seed_db_sync(db_path)
        resp = client.get("/decisions?action=skip")
        assert resp.status_code == 200


class TestAnalytics:
    def test_analytics_empty(self, client):
        resp = client.get("/analytics")
        assert resp.status_code == 200

    def test_analytics_with_data(self, client, db_path):
        _seed_db_sync(db_path)
        resp = client.get("/analytics")
        assert resp.status_code == 200
        assert "Market" in resp.text


class TestSettings:
    def test_settings_page(self, client):
        resp = client.get("/settings")
        assert resp.status_code == 200
        assert "STOP_LOSS_PCT" in resp.text
        assert "MAX_STALE_S" in resp.text


class TestApiStats:
    def test_api_stats_empty(self, client):
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "daily_pnl" in data

    def test_api_stats_with_data(self, client, db_path):
        _seed_db_sync(db_path)
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["daily_pnl"] == 0.05
