# Polymarket Trading Bot

High-frequency trading bot for Polymarket's 5/15-minute binary "Up or Down" markets.

## Architecture

```
polymarket_may_01_2026/
├── main.py                     # Bot entry point & orchestrator
├── src/
│   ├── config.py               # Centralized config (Pydantic BaseSettings)
│   ├── gamma_15m_finder.py     # Market discovery via Gamma API
│   ├── hft_trader.py           # Last-second HFT trader
│   ├── oracle_tracker.py       # Chainlink oracle integration
│   ├── position_settler.py     # Position settlement
│   ├── healthcheck.py          # HTTP health check server
│   ├── metrics.py              # Prometheus-style metrics
│   ├── alerts.py               # Alert system
│   ├── trading/                # Trading subsystem
│   │   ├── dry_run_simulator.py
│   │   ├── parallel_launcher.py
│   │   ├── risk_manager.py
│   │   ├── circuit_breaker.py
│   │   ├── oracle_guard_manager.py
│   │   ├── trade_db.py         # SQLite trade database
│   │   ├── position_manager.py
│   │   ├── stop_loss_manager.py
│   │   ├── orderbook_ws.py     # WebSocket order book
│   │   └── ...
│   └── web_dashboard/          # FastAPI dashboard
├── scripts/
│   ├── approve.py              # Approve USDC allowance
│   ├── check_balance.py        # Check USDC balance
│   ├── daily_report.py         # Generate daily reports
│   └── migrate_to_sqlite.py    # Migrate JSON → SQLite
├── tests/                      # Test suite
└── docs/                       # Historical docs & plans
```

## Configuration

All settings are managed via **Pydantic BaseSettings** in `src/config.py`. Configure via `.env` file or environment variables:

```bash
cp .env.example .env
# Edit .env with your credentials
```

Key settings: `PRIVATE_KEY`, `CLOB_HOST`, `CLOB_API_KEY`, `CLOB_SECRET`, `CLOB_PASSPHRASE`.

## Usage

```bash
# Install dependencies
uv sync

# Dry run (default, no real trades)
uv run python main.py

# Live trading
uv run python main.py --live

# Custom polling interval
uv run python main.py --poll-interval 30

# Web dashboard
uv run python -m src.web_dashboard

# Daily report
uv run python scripts/daily_report.py --date 2026-02-10

# Run tests
uv run pytest tests/ -q
```

## Trading Strategy

1. **Discover** 5/15-min markets ending soon via Gamma API
2. **Monitor** real-time order book via WebSocket
3. **Execute** when time ≤ 30s remaining and winning side ≤ $0.99
4. **Exit** on stop-loss (−30%), take-profit (+10%), or trailing stop

## Safety

- **Dry run by default** — no real trades unless `--live`
- Oracle guard, circuit breaker, daily loss limits, max capital per trade
- See `docs/TRADING_GUIDE.md` for full risk details

## License

MIT
