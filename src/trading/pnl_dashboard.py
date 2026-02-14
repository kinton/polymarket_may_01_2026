"""PnL Dashboard v2 — SQLite-backed analytics with Rich CLI output.

Reads from TradeDatabase (SQLite) with fallback to legacy JSON/log parsing.
Features: win rate by market/hour/weekday, equity curve, rich tables.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Rich imports (optional graceful degradation)
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text

    HAS_RICH = True
except ImportError:
    HAS_RICH = False

from src.trading.trade_db import TradeDatabase

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TradeRecord:
    """A single trade record parsed from log files (legacy compat)."""

    timestamp: str
    market: str
    side: str
    price: float
    amount: float
    pnl: float | None = None


@dataclass
class PnLReport:
    """Aggregated PnL report."""

    daily_pnl: float
    total_pnl: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_trade_size: float
    best_trade: float | None
    worst_trade: float | None
    # v2 fields
    by_market: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_hour: dict[int, dict[str, Any]] = field(default_factory=dict)
    by_weekday: dict[str, dict[str, Any]] = field(default_factory=dict)
    equity_curve: list[tuple[str, float]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Legacy loaders (kept for backward compatibility)
# ---------------------------------------------------------------------------

LOG_DIR = Path("log")

TRADE_PATTERN = re.compile(
    r"(?:BUY|SELL|FILLED).*?"
    r"(?:market|Market)[=: ]*([^\|,]+?)[\|,].*?"
    r"(?:side|Side)[=: ]*(YES|NO).*?"
    r"(?:price|Price)[=: ]*\$?([\d.]+).*?"
    r"(?:amount|Amount|size)[=: ]*\$?([\d.]+)",
    re.IGNORECASE,
)

PNL_PATTERN = re.compile(
    r"(?:pnl|P&L|profit)[=: ]*\$?([-\d.]+)%?",
    re.IGNORECASE,
)


def load_daily_limits(log_dir: Path = LOG_DIR) -> dict:
    """Load daily limits file (legacy)."""
    path = log_dir / "daily_limits.json"
    if not path.exists():
        return {"date": "unknown", "current_pnl": 0.0, "total_trades": 0}
    with open(path) as f:
        return json.load(f)


def parse_trade_logs(log_dir: Path = LOG_DIR) -> list[TradeRecord]:
    """Parse trade records from log files (legacy)."""
    trades: list[TradeRecord] = []
    log_files = sorted(log_dir.glob("trades-*.log"))

    for log_file in log_files:
        try:
            content = log_file.read_text()
        except OSError:
            continue

        for line in content.splitlines():
            match = TRADE_PATTERN.search(line)
            if match:
                market = match.group(1).strip()
                side = match.group(2).upper()
                price = float(match.group(3))
                amount = float(match.group(4))
                pnl_match = PNL_PATTERN.search(line)
                pnl = float(pnl_match.group(1)) if pnl_match else None
                trades.append(TradeRecord(
                    timestamp=line[:23] if len(line) >= 23 else "",
                    market=market,
                    side=side,
                    price=price,
                    amount=amount,
                    pnl=pnl,
                ))

    return trades


# ---------------------------------------------------------------------------
# SQLite-backed analytics
# ---------------------------------------------------------------------------

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _win_rate(wins: int, total: int) -> float:
    """Compute win rate as percentage."""
    return (wins / total * 100) if total > 0 else 0.0


async def load_report_from_sqlite(
    db: TradeDatabase,
    date: str | None = None,
    limit: int = 1000,
) -> PnLReport:
    """Build a full PnLReport from SQLite data."""
    trades = await db.get_trades(date=date, limit=limit)

    if date:
        stats = await db.get_or_create_daily_stats(date)
        daily_pnl = stats.get("current_pnl", 0.0)
    else:
        daily_pnl = 0.0

    pnl_values = [t["pnl"] for t in trades if t.get("pnl") is not None]
    amounts = [t["amount"] for t in trades if t.get("amount")]
    winning = sum(1 for p in pnl_values if p > 0)
    losing = sum(1 for p in pnl_values if p < 0)
    total_pnl = sum(pnl_values) if pnl_values else daily_pnl

    # By market
    by_market: dict[str, dict[str, Any]] = {}
    for t in trades:
        mkt = t.get("market_name", "unknown")
        if mkt not in by_market:
            by_market[mkt] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
        by_market[mkt]["trades"] += 1
        p = t.get("pnl")
        if p is not None:
            by_market[mkt]["pnl"] += p
            if p > 0:
                by_market[mkt]["wins"] += 1
            elif p < 0:
                by_market[mkt]["losses"] += 1

    # By hour
    by_hour: dict[int, dict[str, Any]] = {}
    for t in trades:
        iso = t.get("timestamp_iso", "")
        if len(iso) >= 13:
            try:
                hour = int(iso[11:13])
            except (ValueError, IndexError):
                continue
            if hour not in by_hour:
                by_hour[hour] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
            by_hour[hour]["trades"] += 1
            p = t.get("pnl")
            if p is not None:
                by_hour[hour]["pnl"] += p
                if p > 0:
                    by_hour[hour]["wins"] += 1
                elif p < 0:
                    by_hour[hour]["losses"] += 1

    # By weekday
    by_weekday: dict[str, dict[str, Any]] = {}
    for t in trades:
        iso = t.get("timestamp_iso", "")
        if len(iso) >= 10:
            try:
                dt = datetime.fromisoformat(iso[:10])
                day_name = WEEKDAY_NAMES[dt.weekday()]
            except (ValueError, IndexError):
                continue
            if day_name not in by_weekday:
                by_weekday[day_name] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
            by_weekday[day_name]["trades"] += 1
            p = t.get("pnl")
            if p is not None:
                by_weekday[day_name]["pnl"] += p
                if p > 0:
                    by_weekday[day_name]["wins"] += 1
                elif p < 0:
                    by_weekday[day_name]["losses"] += 1

    # Equity curve (cumulative PnL over time, oldest first)
    equity_curve: list[tuple[str, float]] = []
    cumulative = 0.0
    for t in sorted(trades, key=lambda x: x.get("timestamp", 0)):
        p = t.get("pnl")
        if p is not None:
            cumulative += p
            iso = t.get("timestamp_iso", "")
            label = iso[:16] if len(iso) >= 16 else iso
            equity_curve.append((label, cumulative))

    return PnLReport(
        daily_pnl=daily_pnl,
        total_pnl=total_pnl,
        total_trades=len(trades),
        winning_trades=winning,
        losing_trades=losing,
        win_rate=_win_rate(winning, len(pnl_values)) if pnl_values else 0.0,
        avg_trade_size=sum(amounts) / len(amounts) if amounts else 0.0,
        best_trade=max(pnl_values) if pnl_values else None,
        worst_trade=min(pnl_values) if pnl_values else None,
        by_market=by_market,
        by_hour=by_hour,
        by_weekday=by_weekday,
        equity_curve=equity_curve,
    )


# ---------------------------------------------------------------------------
# Legacy report builder (non-async, for backward compat)
# ---------------------------------------------------------------------------


def compute_pnl_report(
    trades: list[TradeRecord],
    daily_limits: dict,
) -> PnLReport:
    """Compute PnL report from legacy trades and daily limits."""
    daily_pnl = float(daily_limits.get("current_pnl", 0.0))

    pnl_values = [t.pnl for t in trades if t.pnl is not None]
    total_pnl = sum(pnl_values) if pnl_values else daily_pnl
    winning = sum(1 for p in pnl_values if p > 0)
    losing = sum(1 for p in pnl_values if p < 0)
    total = len(trades)
    amounts = [t.amount for t in trades]

    return PnLReport(
        daily_pnl=daily_pnl,
        total_pnl=total_pnl,
        total_trades=total,
        winning_trades=winning,
        losing_trades=losing,
        win_rate=(winning / len(pnl_values) * 100) if pnl_values else 0.0,
        avg_trade_size=sum(amounts) / len(amounts) if amounts else 0.0,
        best_trade=max(pnl_values) if pnl_values else None,
        worst_trade=min(pnl_values) if pnl_values else None,
    )


# ---------------------------------------------------------------------------
# Rich formatters
# ---------------------------------------------------------------------------


def format_report(report: PnLReport) -> str:
    """Format the PnL report for CLI display (plain text fallback)."""
    lines = [
        "╔══════════════════════════════════════╗",
        "║        📊 PnL Dashboard              ║",
        "╠══════════════════════════════════════╣",
        f"║  Daily PnL:      ${report.daily_pnl:>+10.2f}       ║",
        f"║  Total PnL:      ${report.total_pnl:>+10.2f}       ║",
        "╠══════════════════════════════════════╣",
        f"║  Total trades:   {report.total_trades:>10d}       ║",
        f"║  Winning:        {report.winning_trades:>10d}       ║",
        f"║  Losing:         {report.losing_trades:>10d}       ║",
        f"║  Win rate:       {report.win_rate:>9.1f}%       ║",
        f"║  Avg trade size: ${report.avg_trade_size:>9.2f}       ║",
        "╠══════════════════════════════════════╣",
    ]

    if report.best_trade is not None:
        lines.append(f"║  Best trade:     ${report.best_trade:>+9.2f}       ║")
    else:
        lines.append("║  Best trade:          N/A       ║")

    if report.worst_trade is not None:
        lines.append(f"║  Worst trade:    ${report.worst_trade:>+9.2f}       ║")
    else:
        lines.append("║  Worst trade:         N/A       ║")

    lines.append("╚══════════════════════════════════════╝")
    return "\n".join(lines)


def render_rich_report(report: PnLReport, console: Console | None = None) -> None:
    """Render a full Rich-formatted dashboard to the console."""
    if console is None:
        console = Console()

    # --- Summary panel ---
    pnl_color = "green" if report.total_pnl >= 0 else "red"
    daily_color = "green" if report.daily_pnl >= 0 else "red"

    summary = Table(show_header=False, box=None, padding=(0, 2))
    summary.add_column("Key", style="bold")
    summary.add_column("Value", justify="right")
    summary.add_row("Daily PnL", f"[{daily_color}]${report.daily_pnl:+.4f}[/]")
    summary.add_row("Total PnL", f"[{pnl_color}]${report.total_pnl:+.4f}[/]")
    summary.add_row("Total Trades", str(report.total_trades))
    summary.add_row("Winning", f"[green]{report.winning_trades}[/]")
    summary.add_row("Losing", f"[red]{report.losing_trades}[/]")
    summary.add_row("Win Rate", f"{report.win_rate:.1f}%")
    summary.add_row("Avg Trade Size", f"${report.avg_trade_size:.2f}")
    if report.best_trade is not None:
        summary.add_row("Best Trade", f"[green]${report.best_trade:+.4f}[/]")
    if report.worst_trade is not None:
        summary.add_row("Worst Trade", f"[red]${report.worst_trade:+.4f}[/]")

    console.print(Panel(summary, title="📊 PnL Dashboard", border_style="cyan"))

    # --- Win rate by market ---
    if report.by_market:
        tbl = Table(title="Win Rate by Market", border_style="blue")
        tbl.add_column("Market", style="bold")
        tbl.add_column("Trades", justify="right")
        tbl.add_column("Wins", justify="right", style="green")
        tbl.add_column("Losses", justify="right", style="red")
        tbl.add_column("Win Rate", justify="right")
        tbl.add_column("PnL", justify="right")
        for mkt, d in sorted(report.by_market.items()):
            wr = _win_rate(d["wins"], d["wins"] + d["losses"])
            pc = "green" if d["pnl"] >= 0 else "red"
            tbl.add_row(
                mkt, str(d["trades"]), str(d["wins"]), str(d["losses"]),
                f"{wr:.1f}%", f"[{pc}]${d['pnl']:+.4f}[/]",
            )
        console.print(tbl)

    # --- Win rate by hour ---
    if report.by_hour:
        tbl = Table(title="Win Rate by Hour (UTC)", border_style="magenta")
        tbl.add_column("Hour", justify="center")
        tbl.add_column("Trades", justify="right")
        tbl.add_column("Win Rate", justify="right")
        tbl.add_column("PnL", justify="right")
        for h in sorted(report.by_hour.keys()):
            d = report.by_hour[h]
            wr = _win_rate(d["wins"], d["wins"] + d["losses"])
            pc = "green" if d["pnl"] >= 0 else "red"
            tbl.add_row(
                f"{h:02d}:00", str(d["trades"]),
                f"{wr:.1f}%", f"[{pc}]${d['pnl']:+.4f}[/]",
            )
        console.print(tbl)

    # --- Win rate by weekday ---
    if report.by_weekday:
        tbl = Table(title="Win Rate by Weekday", border_style="yellow")
        tbl.add_column("Day", style="bold")
        tbl.add_column("Trades", justify="right")
        tbl.add_column("Win Rate", justify="right")
        tbl.add_column("PnL", justify="right")
        for day in WEEKDAY_NAMES:
            if day in report.by_weekday:
                d = report.by_weekday[day]
                wr = _win_rate(d["wins"], d["wins"] + d["losses"])
                pc = "green" if d["pnl"] >= 0 else "red"
                tbl.add_row(
                    day, str(d["trades"]),
                    f"{wr:.1f}%", f"[{pc}]${d['pnl']:+.4f}[/]",
                )
        console.print(tbl)

    # --- Equity curve (text-based sparkline) ---
    if report.equity_curve:
        _render_equity_curve(report.equity_curve, console)


def _render_equity_curve(
    curve: list[tuple[str, float]],
    console: Console,
    width: int = 60,
    height: int = 12,
) -> None:
    """Render a simple text-based equity curve."""
    if not curve:
        return

    values = [v for _, v in curve]
    min_v = min(values)
    max_v = max(values)
    span = max_v - min_v if max_v != min_v else 1.0

    # Downsample if more points than width
    if len(values) > width:
        step = len(values) / width
        sampled = [values[int(i * step)] for i in range(width)]
    else:
        sampled = values

    # Build character grid
    blocks = "▁▂▃▄▅▆▇█"
    chars: list[str] = []
    for v in sampled:
        idx = int((v - min_v) / span * (len(blocks) - 1))
        idx = max(0, min(idx, len(blocks) - 1))
        color = "green" if v >= 0 else "red"
        chars.append(f"[{color}]{blocks[idx]}[/]")

    spark = "".join(chars)
    first_label = curve[0][0][:10] if curve[0][0] else ""
    last_label = curve[-1][0][:10] if curve[-1][0] else ""
    final_val = values[-1]
    fc = "green" if final_val >= 0 else "red"

    text = Text.from_markup(
        f"  {first_label}  {spark}  {last_label}\n"
        f"  Min: ${min_v:+.4f}  Max: ${max_v:+.4f}  "
        f"Final: [{fc}]${final_val:+.4f}[/]"
    )
    console.print(Panel(text, title="📈 Equity Curve", border_style="green"))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


async def async_main(
    db_path: str = "data/trades.db",
    date: str | None = None,
) -> None:
    """Async entry point — load from SQLite and render."""
    db_file = Path(db_path)
    if db_file.exists():
        db = await TradeDatabase.initialize(db_path)
        try:
            if date is None:
                date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            report = await load_report_from_sqlite(db, date=date)
        finally:
            await db.close()
    else:
        # Fallback to legacy
        daily_limits = load_daily_limits()
        trades = parse_trade_logs()
        report = compute_pnl_report(trades, daily_limits)

    if HAS_RICH:
        render_rich_report(report)
    else:
        print(format_report(report))


def main() -> None:
    """Entry point for CLI dashboard."""
    import sys

    date = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(async_main(date=date))


if __name__ == "__main__":
    main()
