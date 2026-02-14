"""CLI dashboard for viewing PnL and trade statistics."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TradeRecord:
    """A single trade record parsed from log files."""

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


LOG_DIR = Path("log")

# Patterns to extract trade info from log files
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
    """Load daily limits file."""
    path = log_dir / "daily_limits.json"
    if not path.exists():
        return {"date": "unknown", "current_pnl": 0.0, "total_trades": 0}
    with open(path) as f:
        return json.load(f)


def parse_trade_logs(log_dir: Path = LOG_DIR) -> list[TradeRecord]:
    """Parse trade records from log files."""
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


def compute_pnl_report(
    trades: list[TradeRecord],
    daily_limits: dict,
) -> PnLReport:
    """Compute PnL report from trades and daily limits."""
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


def format_report(report: PnLReport) -> str:
    """Format the PnL report for CLI display."""
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


def main() -> None:
    """Entry point for CLI dashboard."""
    daily_limits = load_daily_limits()
    trades = parse_trade_logs()
    report = compute_pnl_report(trades, daily_limits)
    print(format_report(report))


if __name__ == "__main__":
    main()
