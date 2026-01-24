# Polymarket Trading Bot - Documentation Index

## 📁 Documentation Structure

This directory contains the source of truth for the trading bot implementation. Each component is documented separately:

### Core Documentation Files

1. **[ARCHITECTURE.md](./ARCHITECTURE.md)** - System architecture and component overview
   - Core components (gamma_15m_finder.py, hft_trader.py, main.py)
   - Data flow and communication patterns
   - Async task management

2. **[API-INTEGRATION.md](./API-INTEGRATION.md)** - External API integration details
   - Polymarket Gamma API (market search)
   - CLOB REST API (order book, order execution)
   - CLOB WebSocket (real-time price monitoring)
   - Authentication and error handling

3. **[TRADING-STRATEGY.md](./TRADING-STRATEGY.md)** - Trading logic and execution
   - Market filtering criteria
   - Winning side determination (price > 0.50)
   - Order execution parameters
   - Risk management

4. **[ENVIRONMENT.md](./ENVIRONMENT.md)** - Configuration and environment setup
   - Required environment variables
   - .env file format
   - Configuration parameters
   - Security considerations

5. **[DEPLOYMENT.md](./DEPLOYMENT.md)** - Deployment and operational guide
   - Running in dry-run mode
   - Running in live trading mode
   - Command-line arguments
   - Monitoring and logging

## 🎯 Key Parameters

| Parameter | Value | Source |
|-----------|-------|--------|
| Search Window | 20 minutes | TZ requirement |
| Market Types | 5m, 15m Bitcoin/Ethereum | TZ requirement |
| Trader Start Buffer | 180s (3 min) | Code: `TRADER_START_BUFFER` |
| Trigger Threshold | ≤1.0 second | Code: `TRIGGER_SECONDS` |
| Winning Side | Price > 0.50 | Code: `_determine_winning_side()` |
| Buy Price | $0.99 | Code: `BUY_PRICE` |
| Poll Interval | 90s | Code: `POLL_INTERVAL` |

## 🚀 Quick Start

### Dry Run (Safe Mode)
```bash
uv run python main.py
```

### Live Trading (DANGER!)
```bash
uv run python main.py --live
```

### Single Run (for testing)
```bash
uv run python main.py --once
```

### Custom Parameters
```bash
uv run python main.py --live --size 10 --poll-interval 60
```

## 📊 System Status

- ✅ Market discovery via Gamma API
- ✅ Real-time price monitoring via WebSocket
- ✅ Dual-token monitoring (YES/NO)
- ✅ Dynamic winning side detection
- ✅ UTC/ET timezone handling
- ✅ Live trading capability
- ✅ Dry-run testing mode

## 🔍 Validation Checklist

Before deployment:
- [ ] `.env` file contains all required variables
- [ ] `CLOB_API_KEY` and `CLOB_PASSPHRASE` are valid
- [ ] `PRIVATE_KEY` is properly encrypted (not in version control)
- [ ] Test `--once` mode to verify market detection
- [ ] Verify WebSocket connection in logs
- [ ] Check market selection logic matches requirements
- [ ] Validate trigger logic in dry-run mode

## 📝 Implementation Notes

### Version Information
- Python: 3.12.8
- Package Manager: uv
- Key Libraries:
  - `py-clob-client` - Polymarket CLOB API
  - `websockets` - WebSocket connections
  - `aiohttp` - Async HTTP requests
  - `python-dotenv` - Environment configuration

### Recent Changes
- Fixed `max_minutes_ahead`: 30→20 minutes (TZ compliance)
- Corrected environment variables (CLOB_API_KEY, CLOB_PASSPHRASE)
- Implemented dynamic winning side selection
- Added WebSocket real-time monitoring for both tokens
- Fixed UTC time handling
- Added `--once` flag for testing

### Known Issues & Limitations
- WebSocket only emits on orderbook changes (not continuous stream)
- Market data may have 0.1-0.5s latency
- FOK orders have ~100ms execution latency

## 🔗 Related Files

- **Main code**: `main.py`, `gamma_15m_finder.py`, `hft_trader.py`
- **Configuration**: `.env`, `pyproject.toml`
- **Logs**: `log/finder.log`, `log/trades.log`
- **Tests**: `test_*.py` files
