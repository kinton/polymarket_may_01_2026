"""Web dashboard for Polymarket trading bot.

Run: python -m src.web_dashboard.app
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

DB_PATH = "data/trades.db"
TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _db_path() -> str:
    return DB_PATH


async def _fetch_all(query: str, params: tuple = ()) -> list[dict]:
    """Run a read-only query and return list of dicts."""
    db_file = _db_path()
    if not Path(db_file).exists():
        return []
    async with aiosqlite.connect(db_file) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def _fetch_one(query: str, params: tuple = ()) -> dict | None:
    db_file = _db_path()
    if not Path(db_file).exists():
        return None
    async with aiosqlite.connect(db_file) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    yield


app = FastAPI(title="Polymarket Bot Dashboard", lifespan=lifespan)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _fmt_ts(iso: str | None) -> str:
    if not iso:
        return "-"
    return iso[:19].replace("T", " ")


def _fmt_pnl(val: float | None) -> str:
    if val is None:
        return "-"
    return f"${val:+.4f}"


def _fmt_pct(val: float | None) -> str:
    if val is None:
        return "-"
    return f"{val:+.1f}%"


def _win_rate(wins: int, total: int) -> float:
    return (wins / total * 100) if total > 0 else 0.0


# Register template globals
templates.env.globals["fmt_ts"] = _fmt_ts
templates.env.globals["fmt_pnl"] = _fmt_pnl
templates.env.globals["fmt_pct"] = _fmt_pct
templates.env.globals["win_rate"] = _win_rate


# ── API: Overview ────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def overview(request: Request) -> HTMLResponse:
    today = _today()

    # Daily stats
    daily = await _fetch_one(
        "SELECT * FROM daily_stats WHERE date = ?", (today,)
    )
    daily_pnl = daily["current_pnl"] if daily else 0.0
    daily_trades = daily["total_trades"] if daily else 0
    daily_wins = daily["winning_trades"] if daily else 0
    daily_losses = daily["losing_trades"] if daily else 0

    # Total PnL
    total_row = await _fetch_one(
        "SELECT COALESCE(SUM(pnl), 0) as total_pnl, "
        "SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins, "
        "SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses, "
        "COUNT(*) as total FROM trades WHERE pnl IS NOT NULL"
    )
    total_pnl = total_row["total_pnl"] if total_row else 0.0
    total_wins = total_row["wins"] if total_row else 0
    total_losses = total_row["losses"] if total_row else 0
    total_count = total_row["total"] if total_row else 0

    # Open positions
    open_positions = await _fetch_all(
        "SELECT * FROM positions WHERE is_open = 1"
    )

    # Open dry-run positions
    open_dry = await _fetch_all(
        "SELECT * FROM dry_run_positions WHERE status = 'open'"
    )

    # Recent trades
    recent_trades = await _fetch_all(
        "SELECT * FROM trades ORDER BY timestamp DESC LIMIT 10"
    )

    # Bot status: check if there are trades in last 5 min
    import time
    cutoff = time.time() - 300
    active_row = await _fetch_one(
        "SELECT COUNT(*) as cnt FROM trades WHERE timestamp > ?", (cutoff,)
    )
    bot_active = (active_row["cnt"] if active_row else 0) > 0

    ctx = {
        "request": request,
        "page": "overview",
        "bot_active": bot_active,
        "daily_pnl": daily_pnl,
        "total_pnl": total_pnl,
        "daily_trades": daily_trades,
        "daily_wins": daily_wins,
        "daily_losses": daily_losses,
        "total_wins": total_wins,
        "total_losses": total_losses,
        "total_count": total_count,
        "win_rate_val": _win_rate(total_wins, total_wins + total_losses),
        "open_positions": open_positions,
        "open_dry": open_dry,
        "recent_trades": recent_trades,
        "today": today,
    }
    return templates.TemplateResponse(request, "overview.html", ctx)


# ── API: Trades ──────────────────────────────────────────────────────────────


@app.get("/trades", response_class=HTMLResponse)
async def trades_page(
    request: Request,
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    market: str | None = Query(None),
    action: str | None = Query(None),
    dry_run: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200),
) -> HTMLResponse:
    clauses: list[str] = []
    params: list[Any] = []

    if date_from:
        clauses.append("date(timestamp_iso) >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("date(timestamp_iso) <= ?")
        params.append(date_to)
    if market:
        clauses.append("market_name LIKE ?")
        params.append(f"%{market}%")
    if action:
        clauses.append("action = ?")
        params.append(action)
    if dry_run is not None and dry_run != "":
        clauses.append("dry_run = ?")
        params.append(int(dry_run))

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    offset = (page - 1) * per_page

    count_row = await _fetch_one(
        f"SELECT COUNT(*) as cnt FROM trades {where}", tuple(params)
    )
    total = count_row["cnt"] if count_row else 0

    rows = await _fetch_all(
        f"SELECT * FROM trades {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
        (*params, per_page, offset),
    )

    total_pages = max(1, (total + per_page - 1) // per_page)

    ctx = {
        "request": request,
        "page": "trades",
        "trades": rows,
        "current_page": page,
        "total_pages": total_pages,
        "total": total,
        "filters": {
            "date_from": date_from or "",
            "date_to": date_to or "",
            "market": market or "",
            "action": action or "",
            "dry_run": dry_run or "",
        },
    }
    return templates.TemplateResponse(request, "trades.html", ctx)


# ── API: Decisions ───────────────────────────────────────────────────────────


@app.get("/decisions", response_class=HTMLResponse)
async def decisions_page(
    request: Request,
    reason: str | None = Query(None),
    action: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200),
) -> HTMLResponse:
    clauses: list[str] = []
    params: list[Any] = []

    if reason:
        clauses.append("reason = ?")
        params.append(reason)
    if action:
        clauses.append("action = ?")
        params.append(action)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    offset = (page - 1) * per_page

    count_row = await _fetch_one(
        f"SELECT COUNT(*) as cnt FROM trade_decisions {where}", tuple(params)
    )
    total = count_row["cnt"] if count_row else 0

    rows = await _fetch_all(
        f"SELECT * FROM trade_decisions {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
        (*params, per_page, offset),
    )

    # Skip reason counts for pie chart
    skip_reasons = await _fetch_all(
        "SELECT reason, COUNT(*) as cnt FROM trade_decisions "
        "WHERE action = 'skip' GROUP BY reason ORDER BY cnt DESC"
    )

    # Oracle guard stats
    oracle_stats = await _fetch_all(
        "SELECT reason, COUNT(*) as cnt FROM trade_decisions "
        "WHERE reason LIKE 'oracle_%' GROUP BY reason ORDER BY cnt DESC"
    )

    # All unique reasons for filter dropdown
    all_reasons = await _fetch_all(
        "SELECT DISTINCT reason FROM trade_decisions ORDER BY reason"
    )

    total_pages = max(1, (total + per_page - 1) // per_page)

    ctx = {
        "request": request,
        "page": "decisions",
        "decisions": rows,
        "skip_reasons": skip_reasons,
        "oracle_stats": oracle_stats,
        "all_reasons": [r["reason"] for r in all_reasons],
        "current_page": page,
        "total_pages": total_pages,
        "total": total,
        "filters": {"reason": reason or "", "action": action or ""},
    }
    return templates.TemplateResponse(request, "decisions.html", ctx)


# ── API: Analytics ───────────────────────────────────────────────────────────


@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request) -> HTMLResponse:
    # Win rate by market
    by_market = await _fetch_all(
        "SELECT market_name, COUNT(*) as total, "
        "SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins, "
        "SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses, "
        "COALESCE(SUM(pnl), 0) as pnl "
        "FROM trades WHERE pnl IS NOT NULL "
        "GROUP BY market_name ORDER BY total DESC"
    )

    # Win rate by hour
    by_hour = await _fetch_all(
        "SELECT CAST(strftime('%H', timestamp_iso) AS INTEGER) as hour, "
        "COUNT(*) as total, "
        "SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins, "
        "SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses, "
        "COALESCE(SUM(pnl), 0) as pnl "
        "FROM trades WHERE pnl IS NOT NULL "
        "GROUP BY hour ORDER BY hour"
    )

    # Win rate by weekday
    by_weekday = await _fetch_all(
        "SELECT CAST(strftime('%w', timestamp_iso) AS INTEGER) as dow, "
        "COUNT(*) as total, "
        "SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins, "
        "SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses, "
        "COALESCE(SUM(pnl), 0) as pnl "
        "FROM trades WHERE pnl IS NOT NULL "
        "GROUP BY dow ORDER BY dow"
    )
    day_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    for row in by_weekday:
        row["day_name"] = day_names[row["dow"]]

    # Equity curve (daily PnL cumulative)
    equity = await _fetch_all(
        "SELECT date(timestamp_iso) as day, COALESCE(SUM(pnl), 0) as daily_pnl "
        "FROM trades WHERE pnl IS NOT NULL "
        "GROUP BY day ORDER BY day"
    )
    cumulative = 0.0
    equity_curve = []
    for row in equity:
        cumulative += row["daily_pnl"]
        equity_curve.append({"day": row["day"], "cumulative": round(cumulative, 4)})

    # Avg PnL by exit type
    by_exit = await _fetch_all(
        "SELECT close_reason as exit_type, COUNT(*) as total, "
        "COALESCE(AVG(pnl), 0) as avg_pnl, COALESCE(SUM(pnl), 0) as total_pnl "
        "FROM dry_run_positions WHERE status != 'open' AND pnl IS NOT NULL "
        "GROUP BY close_reason ORDER BY total DESC"
    )

    ctx = {
        "request": request,
        "page": "analytics",
        "by_market": by_market,
        "by_hour": by_hour,
        "by_weekday": by_weekday,
        "equity_curve": equity_curve,
        "by_exit": by_exit,
    }
    return templates.TemplateResponse(request, "analytics.html", ctx)


# ── API: Settings ────────────────────────────────────────────────────────────


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    from src import clob_types

    # Collect all uppercase constants
    trading_params = {}
    oracle_params = {}
    risk_params = {}

    for name in sorted(dir(clob_types)):
        if name.startswith("_") or not name.isupper():
            continue
        val = getattr(clob_types, name)
        if callable(val) or isinstance(val, type):
            continue

        if "ORACLE" in name or "STALE" in name:
            oracle_params[name] = val
        elif any(k in name for k in ("LOSS", "PROFIT", "RISK", "DAILY", "MAX_TOTAL", "CAPITAL")):
            risk_params[name] = val
        else:
            trading_params[name] = val

    ctx = {
        "request": request,
        "page": "settings",
        "trading_params": trading_params,
        "oracle_params": oracle_params,
        "risk_params": risk_params,
    }
    return templates.TemplateResponse(request, "settings.html", ctx)


# ── JSON API endpoints (for htmx partial refresh) ───────────────────────────


@app.get("/api/stats")
async def api_stats() -> dict:
    today = _today()
    daily = await _fetch_one(
        "SELECT * FROM daily_stats WHERE date = ?", (today,)
    )
    total_row = await _fetch_one(
        "SELECT COALESCE(SUM(pnl), 0) as total_pnl FROM trades WHERE pnl IS NOT NULL"
    )
    return {
        "daily_pnl": daily["current_pnl"] if daily else 0,
        "total_pnl": total_row["total_pnl"] if total_row else 0,
        "daily_trades": daily["total_trades"] if daily else 0,
    }


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    import uvicorn

    uvicorn.run(
        "src.web_dashboard.app:app",
        host="0.0.0.0",
        port=8080,
        reload=False,
    )


if __name__ == "__main__":
    main()
