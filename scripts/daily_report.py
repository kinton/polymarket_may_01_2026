#!/usr/bin/env python3
"""
Daily Trading Report Generator for Polymarket Trading Bot

This script parses log files and generates daily trading summaries with statistics
including total trades, win rate, PnL, and Oracle Guard blocks.

Usage:
    python scripts/daily_report.py --date 2026-02-10
    python scripts/daily_report.py --date 2026-02-10 --output custom/path.md
    python scripts/daily_report.py --format json
"""

import argparse
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TypedDict

# Type aliases
TradeLog = List[Dict[str, Any]]
OracleGuardLog = List[Dict[str, Any]]

# Performance by market stats
class MarketPerformance(TypedDict):
    trades: int
    wins: int
    total_pnl_pct: float

# Trading statistics
class Stats(TypedDict):
    total_trades: int
    wins: int
    win_rate: float
    total_pnl_pct: float
    oracle_guard_blocks: int
    stop_loss_count: int
    take_profit_count: int
    completed_trades: List[Dict[str, Any]]
    oracle_blocks: List[Dict[str, Any]]
    performance_by_market: Dict[str, MarketPerformance]


class DailyReportGenerator:
    """Generate daily trading reports from log files."""

    # Regex patterns for parsing log entries
    TRADE_ENTRY_PATTERN = re.compile(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}).*?\[([A-Z]+)\].*?TRIGGER at .*? (YES|NO) @ \$(\d+\.\d+)')
    POSITION_OPEN_PATTERN = re.compile(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}).*?\[([A-Z]+)\].*?Position opened @ \$(\d+\.\d+)')
    POSITION_CLOSED_PATTERN = re.compile(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}).*?\[([A-Z]+)\].*?Sold @ \$(\d+\.\d+).*?PnL: ([+-]\d+\.\d+)%.*?\(([^)]+)\)')
    ORACLE_GUARD_PATTERN = re.compile(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}).*?\[([A-Z]+)\].*?SKIP \(oracle_guard\): (.*?) \|')
    ORACLE_GUARD_SUMMARY_PATTERN = re.compile(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}).*?\[([A-Z]+)\].*?Oracle guard summary: blocked=(\d+)')

    def __init__(self, project_root: Path):
        """Initialize report generator.

        Args:
            project_root: Path to project root directory
        """
        self.project_root = project_root
        self.log_dir = project_root / "log"
        self.summary_dir = project_root / "daily-summary"

    def get_log_files_for_date(self, date: datetime) -> List[Path]:
        """Get all log files for a specific date.

        Args:
            date: Date to get logs for

        Returns:
            List of log file paths
        """
        date_str = date.strftime("%Y%m%d")
        log_files = []

        # Trade logs: trades-YYYYMMDD-*.log
        if self.log_dir.exists():
            for pattern in ["trades-*.log", "finder.log"]:
                matching = list(self.log_dir.glob(pattern))
                # Filter by date in filename for trade logs
                if pattern.startswith("trades-"):
                    matching = [
                        f for f in matching
                        if date_str in f.name
                    ]
                log_files.extend(matching)

        return sorted(log_files)

    def parse_trade_entry(self, line: str) -> Optional[Dict]:
        """Parse a trade entry from log line.

        Args:
            line: Log line to parse

        Returns:
            Dictionary with trade data or None if not a trade entry
        """
        # Check for TRIGGER (trade execution)
        match = self.TRADE_ENTRY_PATTERN.search(line)
        if match:
            timestamp_str, market, side, _ = match.groups()
            return {
                "timestamp": datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S"),
                "market": market,
                "side": side,
                "entry_price": None,  # TRIGGER has ask price, not entry price
                "exit_price": None,
                "pnl_pct": None,
                "trigger": None,
            }

        # Check for Position opened
        match = self.POSITION_OPEN_PATTERN.search(line)
        if match:
            timestamp_str, market, price = match.groups()
            return {
                "timestamp": datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S"),
                "market": market,
                "side": None,  # Side not in position open log
                "entry_price": float(price),
                "exit_price": None,
                "pnl_pct": None,
                "trigger": None,
            }

        # Check for Position closed (sold)
        match = self.POSITION_CLOSED_PATTERN.search(line)
        if match:
            timestamp_str, market, exit_price, pnl_pct, trigger = match.groups()
            return {
                "timestamp": datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S"),
                "market": market,
                "side": None,
                "entry_price": None,
                "exit_price": float(exit_price),
                "pnl_pct": float(pnl_pct),
                "trigger": trigger.strip(),
            }

        return None

    def parse_oracle_guard_block(self, line: str) -> Optional[Dict]:
        """Parse an Oracle Guard block from log line.

        Args:
            line: Log line to parse

        Returns:
            Dictionary with Oracle Guard data or None if not a block
        """
        match = self.ORACLE_GUARD_PATTERN.search(line)
        if match:
            timestamp_str, market, reason = match.groups()
            return {
                "timestamp": datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S"),
                "market": market,
                "reason": reason.strip(),
            }

        # Check for oracle guard summary (total blocks)
        match = self.ORACLE_GUARD_SUMMARY_PATTERN.search(line)
        if match:
            timestamp_str, market, blocked_count = match.groups()
            return {
                "timestamp": datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S"),
                "market": market,
                "reason": f"TOTAL: {blocked_count} blocks",
                "is_summary": True,
                "blocked_count": int(blocked_count),
            }

        return None

    def parse_log_file(self, log_file: Path, date: datetime) -> Tuple[TradeLog, OracleGuardLog]:
        """Parse a log file for trades and Oracle Guard blocks.

        Args:
            log_file: Path to log file
            date: Date to filter entries for

        Returns:
            Tuple of (trades, oracle_guard_blocks)
        """
        trades = []
        oracle_blocks = []

        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        # Try parsing as trade
                        trade = self.parse_trade_entry(line)
                        if trade and trade['timestamp'].date() == date.date():
                            trades.append(trade)
                            continue

                        # Try parsing as Oracle Guard block
                        block = self.parse_oracle_guard_block(line)
                        if block and block['timestamp'].date() == date.date():
                            oracle_blocks.append(block)

                    except Exception as e:
                        # Skip corrupted lines
                        print(f"Warning: Skipping corrupted line in {log_file}: {e}")
                        continue

        except Exception as e:
            print(f"Warning: Failed to read {log_file}: {e}")

        return trades, oracle_blocks

    def calculate_statistics(self, trades: TradeLog, oracle_blocks: OracleGuardLog) -> Stats:
        """Calculate trading statistics.

        Args:
            trades: List of trade entries
            oracle_blocks: List of Oracle Guard blocks

        Returns:
            Dictionary with calculated statistics
        """
        # Match open + close pairs to create completed trades
        completed_trades = []

        # Find matching open/close pairs
        # Strategy: first find TRIGGER entries (which have side), then match with Position opened
        trigger_entries: Dict[str, Dict] = {}
        open_positions: Dict[str, Dict] = {}

        for trade in sorted(trades, key=lambda t: t['timestamp']):
            market = trade['market']

            if trade['side'] and not trade['entry_price']:
                # TRIGGER entry - store side information
                trigger_entries[market] = trade
            elif trade['entry_price'] and not trade['exit_price']:
                # Position opened - combine with trigger side if available
                side = trade.get('side')
                if not side and market in trigger_entries:
                    side = trigger_entries[market]['side']
                open_positions[market] = {
                    **trade,
                    'side': side or 'UNKNOWN',
                }
            elif trade['exit_price'] and not trade['entry_price']:
                # Position closed - match with open
                if market in open_positions:
                    open_trade = open_positions[market]
                    completed_trade = {
                        'market': market,
                        'timestamp': open_trade['timestamp'],
                        'side': open_trade.get('side', 'UNKNOWN'),
                        'entry_price': open_trade['entry_price'],
                        'exit_price': trade['exit_price'],
                        'pnl_pct': trade['pnl_pct'],
                        'trigger': trade.get('trigger', 'UNKNOWN'),
                    }
                    completed_trades.append(completed_trade)
                    del open_positions[market]
                    # Clean up trigger entry if exists
                    if market in trigger_entries:
                        del trigger_entries[market]

        # Calculate stats
        total_trades = len(completed_trades)
        wins = sum(1 for t in completed_trades if t['pnl_pct'] > 0)
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        total_pnl_pct = sum(t['pnl_pct'] for t in completed_trades)

        # Count triggers (case-insensitive matching)
        stop_loss_count = sum(1 for t in completed_trades if 'STOP-LOSS' in t.get('trigger', '').upper())
        take_profit_count = sum(1 for t in completed_trades if 'TAKE-PROFIT' in t.get('trigger', '').upper())

        # Oracle Guard stats
        oracle_guard_blocks = len([b for b in oracle_blocks if not b.get('is_summary')])

        # Performance by market
        performance_by_market: Dict[str, MarketPerformance] = {}
        for trade in completed_trades:
            market = trade['market']
            if market not in performance_by_market:
                performance_by_market[market] = {
                    'trades': 0,
                    'wins': 0,
                    'total_pnl_pct': 0.0,
                }
            performance_by_market[market]['trades'] += 1
            if trade['pnl_pct'] > 0:
                performance_by_market[market]['wins'] += 1
            performance_by_market[market]['total_pnl_pct'] += trade['pnl_pct']

        return Stats(
            total_trades=total_trades,
            wins=wins,
            win_rate=win_rate,
            total_pnl_pct=total_pnl_pct,
            oracle_guard_blocks=oracle_guard_blocks,
            stop_loss_count=stop_loss_count,
            take_profit_count=take_profit_count,
            completed_trades=completed_trades,
            oracle_blocks=oracle_blocks,
            performance_by_market=performance_by_market,
        )

    def generate_markdown_report(self, date: datetime, stats: Stats) -> str:
        """Generate a markdown report.

        Args:
            date: Report date
            stats: Calculated statistics

        Returns:
            Markdown formatted report
        """
        date_str = date.strftime("%Y-%m-%d")

        # Summary section
        summary = f"""# Daily Trading Report - {date_str}

## Summary
- Total Trades: {stats['total_trades']}
- Win Rate: {stats['win_rate']:.1f}% ({stats['wins']}/{stats['total_trades']})
- Total PnL: {stats['total_pnl_pct']:+.2f}%
- Oracle Guard Blocks: {stats['oracle_guard_blocks']}
- Stop-Loss Triggers: {stats['stop_loss_count']}
- Take-Profit Triggers: {stats['take_profit_count']}
"""

        # Performance by market
        if stats['performance_by_market']:
            summary += "\n## Performance by Market\n| Market | Trades | Wins | Win Rate | PnL |\n"
            summary += "|--------|---------|-------|-----------|------|\n"
            for market, data in sorted(stats['performance_by_market'].items()):
                win_rate = (data['wins'] / data['trades'] * 100) if data['trades'] > 0 else 0
                summary += f"| {market} | {data['trades']} | {data['wins']} | {win_rate:.0f}% | {data['total_pnl_pct']:+.2f}% |\n"

        # Trade log
        if stats['completed_trades']:
            summary += "\n## Trade Log\n| Time | Market | Side | Entry | Exit | PnL | Trigger |\n"
            summary += "|------|--------|------|-------|------|-----|---------|\n"
            for trade in stats['completed_trades']:
                time_str = trade['timestamp'].strftime("%H:%M:%S")
                pnl_sign = "+" if trade['pnl_pct'] >= 0 else ""
                summary += (
                    f"| {time_str} | {trade['market']} | {trade['side']} | "
                    f"${trade['entry_price']:.4f} | ${trade['exit_price']:.4f} | "
                    f"{pnl_sign}{trade['pnl_pct']:.2f}% | {trade['trigger']} |\n"
                )

        # Oracle Guard blocks
        if stats['oracle_blocks']:
            summary += "\n## Oracle Guard Blocks\n| Time | Market | Reason |\n"
            summary += "|------|--------|--------|\n"
            for block in stats['oracle_blocks']:
                if not block.get('is_summary'):
                    time_str = block['timestamp'].strftime("%H:%M:%S")
                    summary += f"| {time_str} | {block['market']} | {block['reason']} |\n"

        return summary

    def generate_json_report(self, date: datetime, stats: Stats) -> str:
        """Generate a JSON report.

        Args:
            date: Report date
            stats: Calculated statistics

        Returns:
            JSON formatted report
        """
        report = {
            'date': date.strftime("%Y-%m-%d"),
            'summary': {
                'total_trades': stats['total_trades'],
                'wins': stats['wins'],
                'win_rate_pct': stats['win_rate'],
                'total_pnl_pct': stats['total_pnl_pct'],
                'oracle_guard_blocks': stats['oracle_guard_blocks'],
                'stop_loss_triggers': stats['stop_loss_count'],
                'take_profit_triggers': stats['take_profit_count'],
            },
            'performance_by_market': stats['performance_by_market'],
            'trades': [
                {
                    'time': t['timestamp'].strftime("%H:%M:%S"),
                    'market': t['market'],
                    'side': t['side'],
                    'entry_price': t['entry_price'],
                    'exit_price': t['exit_price'],
                    'pnl_pct': t['pnl_pct'],
                    'trigger': t['trigger'],
                }
                for t in stats['completed_trades']
            ],
            'oracle_guard_blocks': [
                {
                    'time': b['timestamp'].strftime("%H:%M:%S"),
                    'market': b['market'],
                    'reason': b['reason'],
                }
                for b in stats['oracle_blocks']
                if not b.get('is_summary')
            ],
        }
        return json.dumps(report, indent=2)

    def generate_csv_report(self, date: datetime, stats: Stats) -> str:
        """Generate a CSV report (trades only).

        Args:
            date: Report date
            stats: Calculated statistics

        Returns:
            CSV formatted report
        """
        lines = [
            "# Daily Trading Report - " + date.strftime("%Y-%m-%d"),
            f"# Summary: {stats['total_trades']} trades, {stats['win_rate']:.1f}% win rate, {stats['total_pnl_pct']:+.2f}% PnL",
            "",
            "Time,Market,Side,Entry Price,Exit Price,PnL %,Trigger",
        ]
        for trade in stats['completed_trades']:
            time_str = trade['timestamp'].strftime("%H:%M:%S")
            line = (f"{time_str},{trade['market']},{trade['side']},"
                    f"{trade['entry_price']:.4f},{trade['exit_price']:.4f},"
                    f"{trade['pnl_pct']:.2f},{trade['trigger']}")
            lines.append(line)
        return "\n".join(lines)

    def generate_report(
        self,
        date: datetime,
        output_path: Optional[Path] = None,
        format: str = "markdown"
    ) -> Tuple[bool, str]:
        """Generate daily report.

        Args:
            date: Report date
            output_path: Custom output path (default: daily-summary/YYYY-MM-DD.md)
            format: Output format (markdown, json, csv)

        Returns:
            Tuple of (success, message)
        """
        # Get log files
        log_files = self.get_log_files_for_date(date)
        if not log_files:
            return False, f"No trading activity for {date.strftime('%Y-%m-%d')}"

        # Parse log files
        all_trades = []
        all_oracle_blocks = []
        for log_file in log_files:
            trades, blocks = self.parse_log_file(log_file, date)
            all_trades.extend(trades)
            all_oracle_blocks.extend(blocks)

        if not all_trades and not all_oracle_blocks:
            return False, f"No trading activity for {date.strftime('%Y-%m-%d')}"

        # Calculate statistics
        stats = self.calculate_statistics(all_trades, all_oracle_blocks)

        # Generate report
        if format == "json":
            report = self.generate_json_report(date, stats)
            default_ext = ".json"
        elif format == "csv":
            report = self.generate_csv_report(date, stats)
            default_ext = ".csv"
        else:  # markdown
            report = self.generate_markdown_report(date, stats)
            default_ext = ".md"

        # Determine output path
        if output_path is None:
            self.summary_dir.mkdir(exist_ok=True)
            output_path = self.summary_dir / f"{date.strftime('%Y-%m-%d')}{default_ext}"

        # Write report
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(report)

        return True, f"Report generated: {output_path}"


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Generate daily trading reports for Polymarket Trading Bot"
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Date in YYYY-MM-DD format (default: yesterday)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Custom output path",
    )
    parser.add_argument(
        "--format",
        type=str,
        choices=["markdown", "json", "csv"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    parser.add_argument(
        "--project-dir",
        type=str,
        default=None,
        help="Project root directory (default: current directory)",
    )

    args = parser.parse_args()

    # Determine date
    if args.date:
        date = datetime.strptime(args.date, "%Y-%m-%d")
    else:
        date = datetime.now() - timedelta(days=1)

    # Determine project root
    if args.project_dir:
        project_root = Path(args.project_dir)
    else:
        # Default to script's parent directory (project root)
        project_root = Path(__file__).parent.parent

    # Determine output path
    output_path = Path(args.output) if args.output else None

    # Generate report
    generator = DailyReportGenerator(project_root)
    success, message = generator.generate_report(date, output_path, args.format)

    if success:
        print(f"✓ {message}")
        return 0
    else:
        print(f"✗ {message}")
        return 1


if __name__ == "__main__":
    exit(main())
