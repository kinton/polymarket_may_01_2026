# Polymarket Trading Bot

High-frequency trading bot for Polymarket's 5/15-minute binary markets.

## Features

- **High-Frequency Trading**: Automated trading in the final 2-minute window before market close
- **Multi-Market Support**: BTC, ETH, SOL 5-minute and 15-minute markets
- **Risk Management**: Stop-loss and take-profit triggers
- **Oracle Guard**: Chainlink oracle integration to prevent low-probability trades
- **Dry Run Mode**: Safe testing mode (default)
- **Daily Reports**: Automated daily trading summaries

## Quick Start

```bash
# Clone and setup
git clone https://github.com/kinton/polymarket_may_01_2026.git
cd polymarket_may_01_2026
uv sync

# Run in dry run mode (safe, no real trades)
uv run python main.py

# Run live trading (DANGER!)
uv run python main.py --live
```

## Configuration

Copy `.env.example` to `.env` and configure:

```bash
PRIVATE_KEY=your_private_key_here
CLOB_HOST=https://clob.polymarket.com
CLOB_API_KEY=your_api_key
CLOB_SECRET=your_secret
CLOB_PASSPHRASE=your_passphrase
```

## Daily Reports

Generate daily trading summaries:

```bash
# Generate report for a specific date
uv run python scripts/daily_report.py --date 2026-02-10

# Generate in different formats
uv run python scripts/daily_report.py --date 2026-02-10 --format json
uv run python scripts/daily_report.py --date 2026-02-10 --format csv

# Custom output path
uv run python scripts/daily_report.py --date 2026-02-10 --output reports/my-report.md
```

Reports are saved to `daily-summary/YYYY-MM-DD.md` by default.

### Cron Integration

Add to crontab for automatic daily reports:

```bash
# Daily report at 23:00 UTC
0 23 * * * cd /path/to/polymarket && uv run python scripts/daily_report.py
```

## Trading Strategy

The bot implements a "last-second" strategy:

1. **Market Discovery**: Find 5/15-minute markets ending in the next 4 minutes
2. **Monitoring**: Stream real-time order book data via WebSocket
3. **Trigger**: Execute when time remaining ≤ 120 seconds AND winning side ≤ $0.99
4. **Exit**: Sell on stop-loss (-5%) or take-profit (+2%)

## Safety Features

- **Dry Run Mode**: Simulate trades without real execution (default)
- **Oracle Guard**: Block trades when oracle signals are unreliable
- **Stop-Loss**: Automatic exit at -5% (or $0.50 absolute)
- **Take-Profit**: Automatic exit at +2%
- **Minimum Confidence**: Only trade when winning side ≥ 75%
- **Balance Checks**: Verify sufficient funds before trading

## Risk Warning

⚠️ **This bot trades with real money in live mode.**

- Start with small amounts (e.g., $1-$10)
- Always test in dry run mode first
- Monitor position closely
- Understand the markets you're trading
- Never trade more than you can afford to lose

## Project Structure

```
polymarket_may_01_2026/
├── main.py                 # Bot entry point
├── src/                    # Core modules
│   ├── hft_trader.py      # High-frequency trader
│   ├── gamma_15m_finder.py # Market discovery
│   ├── oracle_tracker.py   # Chainlink oracle integration
│   └── ...
├── scripts/
│   └── daily_report.py     # Daily report generator
├── log/                    # Log files
├── daily-summary/          # Daily reports
└── docs/                   # Documentation
```

## Logs

- `log/finder.log` - Market discovery and general activity
- `log/trades-YYYYMMDD-*.log` - Trading logs per day

## Support

- **Issues**: https://github.com/kinton/polymarket_may_01_2026/issues
- **Documentation**: See `docs/` directory

## License

MIT
