#!/usr/bin/env python3
"""
Daily Report Generator for Polymarket Trading Bot

Generates daily reports from trade logs and daily limits.
Usage:
    python scripts/daily_report.py                    # Generate report for today/yesterday
    python scripts/daily_report.py --date 2026-02-11 # Generate report for specific date
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@dataclass
class Trade:
    """Represents a single trade from the logs."""
    timestamp: str
    market: str
    condition_id: str
    side: str  # "YES" or "NO"
    trigger_price: Optional[float]
    entry_price: Optional[float]
    exit_price: Optional[float]
    amount: float
    profit_loss: Optional[float]
    exit_reason: Optional[str]  # "STOP_LOSS", "TAKE_PROFIT", "MARKET_CLOSE", "MANUAL"
    outcome: Optional[str]  # "WIN", "LOSS", "BREAKEVEN", "SKIPPED"
    skip_reason: Optional[str]  # "INSUFFICIENT_FUNDS", "NO_EXECUTION"


@dataclass
class BlockedMarket:
    """Represents a market blocked by Oracle Guard."""
    timestamp: str
    market: str
    condition_id: str
    reason: str


@dataclass
class DailyReport:
    """Daily trading report."""
    date: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    skipped_trades: int
    total_pnl: float
    win_rate: float
    trades: List[Trade] = field(default_factory=list)
    blocked_markets: List[BlockedMarket] = field(default_factory=list)
    risk_limit_blocks: int = 0
    oracle_guard_blocks: int = 0
    total_triggers: int = 0


class LogParser:
    """Parse trading logs and extract trade information."""

    # Regex patterns for log parsing
    MARKET_START_PATTERN = re.compile(
        r"Starting trader for market: (.+)"
    )
    CONDITION_ID_PATTERN = re.compile(
        r"Condition ID: (0x[a-fA-F0-9]+)"
    )
    TRIGGER_PATTERN = re.compile(
        r"🎯 \[([A-Z]+)\] TRIGGER at ([\d.]+)s! ([A-Z]+) @ \$(\d+\.\d+)"
    )
    INSUFFICIENT_FUNDS_PATTERN = re.compile(
        r"❌ \[([A-Z]+)\] Insufficient balance"
    )
    ORDER_BUY_PATTERN = re.compile(
        r"✓ \[([A-Z]+)\] Order posted:.*?'takingAmount':\s*'([^']+)'"
    )
    ORDER_SELL_PATTERN = re.compile(
        r"💰 \[([A-Z]+)\] Sold @ \$(\d+\.\d+)\s+\|\s+PnL:\s+([\+\-]\d+\.\d+)%"
    )
    ORDER_POSTED_PATTERN = re.compile(
        r"✓ \[([A-Z]+)\] Order posted:"
    )
    POSITION_OPENED_PATTERN = re.compile(
        r"📍 Position opened @ \$(\d+\.\d+)"
    )
    STOP_LOSS_PATTERN = re.compile(
        r"⚠️ \[([A-Z]+)\] STOP-LOSS @ \$(\d+\.\d+)"
    )
    TAKE_PROFIT_PATTERN = re.compile(
        r"🎉 \[([A-Z]+)\] TAKE-PROFIT @ \$(\d+\.\d+)"
    )
    MARKET_CLOSED_PATTERN = re.compile(
        r"⏰ \[([A-Z]+)\] Market closed"
    )

    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.trades: List[Trade] = []
        self.blocked_markets: List[BlockedMarket] = []
        self.risk_limit_blocks = 0
        self.total_triggers = 0

    def parse_date(self, date_str: str) -> DailyReport:
        """Parse all logs for a specific date and generate report."""
        # Find all trade logs for the date
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        date_prefix = date_obj.strftime("%Y%m%d")

        log_files = list(self.log_dir.glob(f"trades-{date_prefix}-*.log"))

        if not log_files:
            return DailyReport(
                date=date_str,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                skipped_trades=0,
                total_pnl=0.0,
                win_rate=0.0,
            )

        # Parse each log file
        for log_file in sorted(log_files):
            self._parse_log_file(log_file)

        # Load daily limits for PnL
        daily_pnl = self._load_daily_pnl(date_str)

        # Calculate statistics
        winning_trades = sum(1 for t in self.trades if t.outcome == "WIN")
        losing_trades = sum(1 for t in self.trades if t.outcome == "LOSS")
        skipped_trades = sum(1 for t in self.trades if t.outcome == "SKIPPED")
        total_pnl = daily_pnl if daily_pnl is not None else sum(
            t.profit_loss for t in self.trades if t.profit_loss is not None
        )
        win_rate = (
            (winning_trades / max(len(self.trades) - skipped_trades, 1)) * 100
            if (len(self.trades) - skipped_trades) > 0
            else 0.0
        )

        return DailyReport(
            date=date_str,
            total_trades=len(self.trades),
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            skipped_trades=skipped_trades,
            total_pnl=total_pnl,
            win_rate=win_rate,
            trades=self.trades,
            blocked_markets=self.blocked_markets,
            risk_limit_blocks=self.risk_limit_blocks,
            oracle_guard_blocks=len(self.blocked_markets),
            total_triggers=self.total_triggers,
        )

    def _parse_log_file(self, log_file: Path):
        """Parse a single log file and extract trades."""
        current_market = None
        current_condition_id = None
        current_trade: Optional[Trade] = None
        has_insufficient_funds = False

        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                # Track current market
                market_match = self.MARKET_START_PATTERN.search(line)
                if market_match:
                    current_market = market_match.group(1)

                condition_match = self.CONDITION_ID_PATTERN.search(line)
                if condition_match:
                    current_condition_id = condition_match.group(1)

                # Check for trigger events (new trades)
                trigger_match = self.TRIGGER_PATTERN.search(line)
                if trigger_match and current_market and current_condition_id:
                    self.total_triggers += 1

                    # Extract trigger info
                    side = trigger_match.group(3)
                    price = float(trigger_match.group(4))

                    # Store as a potential trade
                    current_trade = Trade(
                        timestamp=line[:26],  # Extract timestamp
                        market=current_market,
                        condition_id=current_condition_id,
                        side=side,
                        trigger_price=price,
                        entry_price=None,
                        exit_price=None,
                        amount=1.10,  # Default trade size
                        profit_loss=None,
                        exit_reason=None,
                        outcome=None,
                        skip_reason=None,
                    )
                    has_insufficient_funds = False  # Reset for new trade

                # Check for insufficient funds (mark as skipped)
                if current_trade and self.INSUFFICIENT_FUNDS_PATTERN.search(line):
                    has_insufficient_funds = True

                # Check for BUY order (successful buy execution)
                buy_match = self.ORDER_BUY_PATTERN.search(line)
                if buy_match and current_trade:
                    amount = float(buy_match.group(1))
                    current_trade.amount = amount
                    current_trade.entry_price = None  # Will be determined by side
                    # Determine entry price from order book (simplified)
                    # In real logs, entry price is in the order book around trigger time
                    has_insufficient_funds = False

                # Check for SELL order (successful sell execution)
                sell_match = self.ORDER_SELL_PATTERN.search(line)
                if sell_match and current_trade:
                    current_trade.exit_price = float(sell_match.group(2))
                    pnl_pct = float(sell_match.group(3))
                    current_trade.profit_loss = current_trade.amount * (pnl_pct / 100.0)
                    # Determine outcome based on PnL
                    if pnl_pct >= 0:
                        current_trade.outcome = "WIN"
                        current_trade.exit_reason = "TAKE_PROFIT" if pnl_pct > 0 else "BREAKEVEN"
                    else:
                        current_trade.outcome = "LOSS"
                        current_trade.exit_reason = "STOP_LOSS"
                    # Append trade
                    self.trades.append(current_trade)
                    current_trade = None
                    has_insufficient_funds = False

                # Check for order posted (successful execution) - for backward compatibility
                if current_trade and self.ORDER_POSTED_PATTERN.search(line):
                    has_insufficient_funds = False  # Trade executed

                # Check for position opened
                if current_trade and self.POSITION_OPENED_PATTERN.search(line):
                    pos_match = self.POSITION_OPENED_PATTERN.search(line)
                    if pos_match:
                        current_trade.entry_price = float(pos_match.group(1))

                # Check for stop-loss
                if current_trade and self.STOP_LOSS_PATTERN.search(line):
                    sl_match = self.STOP_LOSS_PATTERN.search(line)
                    if sl_match:
                        current_trade.exit_price = float(sl_match.group(1))
                        current_trade.exit_reason = "STOP_LOSS"
                        current_trade.outcome = "LOSS"
                        # Calculate PnL (simplified)
                        if current_trade.entry_price:
                            pct_change = (
                                (current_trade.exit_price - current_trade.entry_price)
                                / current_trade.entry_price
                            )
                            current_trade.profit_loss = (
                                current_trade.amount * pct_change
                            )
                        self.trades.append(current_trade)
                        current_trade = None
                        has_insufficient_funds = False

                # Check for take-profit
                if current_trade and self.TAKE_PROFIT_PATTERN.search(line):
                    tp_match = self.TAKE_PROFIT_PATTERN.search(line)
                    if tp_match:
                        current_trade.exit_price = float(tp_match.group(1))
                        current_trade.exit_reason = "TAKE_PROFIT"
                        current_trade.outcome = "WIN"
                        if current_trade.entry_price:
                            pct_change = (
                                (current_trade.exit_price - current_trade.entry_price)
                                / current_trade.entry_price
                            )
                            current_trade.profit_loss = (
                                current_trade.amount * pct_change
                            )
                        self.trades.append(current_trade)
                        current_trade = None
                        has_insufficient_funds = False

                # Check for market close (position exits at close)
                # If we have a pending trade and market closes without execution,
                # mark it as SKIPPED
                if current_trade and self.MARKET_CLOSED_PATTERN.search(line):
                    if has_insufficient_funds:
                        current_trade.outcome = "SKIPPED"
                        current_trade.skip_reason = "INSUFFICIENT_FUNDS"
                        self.trades.append(current_trade)
                    else:
                        # Trade was triggered but no exit recorded
                        # This might be a market close without prior position
                        # or the trade is still pending
                        current_trade.outcome = "SKIPPED"
                        current_trade.skip_reason = "NO_EXECUTION"
                        self.trades.append(current_trade)

                    current_trade = None
                    has_insufficient_funds = False

    def _load_daily_pnl(self, date_str: str) -> Optional[float]:
        """Load daily PnL from daily_limits.json."""
        limits_file = self.log_dir.parent / "log" / "daily_limits.json"

        if not limits_file.exists():
            return None

        try:
            with open(limits_file, "r") as f:
                limits = json.load(f)

            # Check if the date matches
            if limits.get("date") == date_str:
                return limits.get("current_pnl", 0.0)
        except (json.JSONDecodeError, IOError):
            pass

        return None


class ReportGenerator:
    """Generate Markdown reports from daily data."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, report: DailyReport) -> str:
        """Generate a Markdown report."""
        lines = [
            f"# Daily Report - {report.date}",
            "",
            "## 📊 Summary",
            "",
            f"- **Total Triggers:** {report.total_triggers}",
            f"- **Total Trades:** {report.total_trades}",
            f"- **Winning Trades:** {report.winning_trades}",
            f"- **Losing Trades:** {report.losing_trades}",
            f"- **Skipped Trades:** {report.skipped_trades}",
            f"- **Win Rate:** {report.win_rate:.1f}% (excluding skipped)",
            f"- **Total PnL:** ${report.total_pnl:.4f}",
            f"- **Risk Limit Blocks:** {report.risk_limit_blocks}",
            f"- **Oracle Guard Blocks:** {report.oracle_guard_blocks}",
            "",
        ]

        # Trade details
        if report.trades:
            lines.extend([
                "## 💼 Trade Details",
                "",
                "| Time | Market | Side | Trigger | Entry | Exit | PnL | Reason | Outcome |",
                "|------|--------|------|---------|-------|------|-----|--------|---------|",
            ])

            for trade in report.trades:
                trigger_str = f"${trade.trigger_price:.4f}" if trade.trigger_price else "-"
                entry_str = f"${trade.entry_price:.4f}" if trade.entry_price else "-"
                exit_str = f"${trade.exit_price:.4f}" if trade.exit_price else "-"
                pnl_str = f"${trade.profit_loss:.4f}" if trade.profit_loss is not None else "-"
                reason_str = trade.exit_reason or trade.skip_reason or "-"
                outcome_str = trade.outcome or "-"

                lines.append(
                    f"| {trade.timestamp[:19]} | {trade.market[:30]} | {trade.side} | {trigger_str} | {entry_str} | {exit_str} | {pnl_str} | {reason_str} | {outcome_str} |"
                )
            lines.append("")

        # Blocked markets
        if report.blocked_markets:
            lines.extend([
                "## 🚫 Blocked Markets (Oracle Guard)",
                "",
                "| Time | Market | Reason |",
                "|------|--------|--------|",
            ])

            for blocked in report.blocked_markets:
                lines.append(
                    f"| {blocked.timestamp[:19]} | {blocked.market[:30]} | {blocked.reason} |"
                )
            lines.append("")

        # Performance metrics
        lines.extend([
            "## 📈 Performance Metrics",
            "",
            f"- **Average PnL per Trade:** ${report.total_pnl / report.total_trades if report.total_trades > 0 else 0:.4f}",
            f"- **Execution Rate:** {((report.total_trades - report.skipped_trades) / report.total_triggers * 100) if report.total_triggers > 0 else 0:.1f}%",
            f"- **Skip Rate:** {(report.skipped_trades / report.total_triggers * 100) if report.total_triggers > 0 else 0:.1f}%",
            f"- **Block Rate:** {((report.risk_limit_blocks + report.oracle_guard_blocks) / report.total_triggers * 100) if report.total_triggers > 0 else 0:.1f}%",
            "",
        ])

        lines.extend([
            "---",
            "",
            "*Generated by Polymarket Trading Bot*",
            f"*Report generated at: {datetime.now(timezone.utc).isoformat()}*",
        ])

        return "\n".join(lines)

    def save(self, report: DailyReport, content: str):
        """Save report to file."""
        output_file = self.output_dir / f"{report.date}.md"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(content)

        print(f"✓ Report saved: {output_file}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate daily trading reports for Polymarket bot"
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Date in YYYY-MM-DD format (default: yesterday if no trades today)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output directory (default: daily-summary/)",
    )

    args = parser.parse_args()

    # Determine date
    if args.date:
        date_str = args.date
    else:
        # Default to yesterday
        today = datetime.now(timezone.utc).date()
        yesterday = today.replace(day=today.day - 1) if today.day > 1 else today
        date_str = yesterday.strftime("%Y-%m-%d")

    # Setup paths
    log_dir = PROJECT_ROOT / "log"
    output_dir = Path(args.output) if args.output else PROJECT_ROOT / "daily-summary"

    # Check if log directory exists
    if not log_dir.exists():
        print(f"❌ Log directory not found: {log_dir}")
        sys.exit(1)

    # Parse logs
    print(f"📊 Generating report for: {date_str}")
    print(f"📁 Log directory: {log_dir}")

    parser = LogParser(log_dir)
    report = parser.parse_date(date_str)

    # Generate report
    generator = ReportGenerator(output_dir)
    content = generator.generate(report)
    generator.save(report, content)

    # Print summary
    print()
    print("📈 Report Summary:")
    print(f"  Total Triggers: {report.total_triggers}")
    print(f"  Total Trades: {report.total_trades}")
    print(f"  Winning: {report.winning_trades} | Losing: {report.losing_trades} | Skipped: {report.skipped_trades}")
    print(f"  Win Rate: {report.win_rate:.1f}%")
    print(f"  Total PnL: ${report.total_pnl:.4f}")
    print(f"  Execution Rate: {((report.total_trades - report.skipped_trades) / report.total_triggers * 100) if report.total_triggers > 0 else 0:.1f}%")
    print(f"  Blocks: Risk={report.risk_limit_blocks}, Oracle={report.oracle_guard_blocks}")


if __name__ == "__main__":
    main()
