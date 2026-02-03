# Polymarket Trading Bot - Documentation Index

## 📁 Documentation Structure

### Critical Reference Files

- **[.github/copilot-instructions.md](../.github/copilot-instructions.md)** - Quick start for VS Code Copilot
- **[.github/agents.md](../.github/agents.md)** - Critical context AI must always remember
- **[ai/Soul.md](../ai/Soul.md)** - Project vision and original constraints

### Agent Skills (Specialized Tasks)

AI can independently solve these problems:

1. **[.github/skills/market-discovery/SKILL.md](../.github/skills/market-discovery/)** - Find active markets
2. **[.github/skills/trading-execution/SKILL.md](../.github/skills/trading-execution/)** - Execute trades
3. **[.github/skills/debugging/SKILL.md](../.github/skills/debugging/)** - Debug issues

### Technical Documentation

1. **[ARCHITECTURE.md](./ARCHITECTURE.md)** - System architecture and component overview
   - Core components (gamma_15m_finder.py, hft_trader.py, main.py)
   - Data flow and communication patterns
   - Async task management

2. **[API-INTEGRATION.md](./API-INTEGRATION.md)** - External API integration details
   - Polymarket Gamma API (market search)
   - CLOB REST API (order book, order execution)
   - CLOB WebSocket (real-time price monitoring)
   - Authentication and error handling

3. **[PROJECT.md](./PROJECT.md)** - Technical specifications
   - API endpoints and message formats
   - Market types and parameters
   - Strategy parameters

## 🎯 Key Parameters

| Parameter | Value | Source |
|-----------|-------|--------|
| Search Window | 20 minutes | TZ requirement |
| Market Types | 5m, 15m Bitcoin/Ethereum | TZ requirement |
| Trader Start Buffer | 180s (3 min) | Code: `TRADER_START_BUFFER` |
| Trigger Threshold | ≤120s | Code: `TRIGGER_THRESHOLD` |
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
- ✅ Automatic position settlement and claiming

## 🎯 Key Features

### Trading Bot
- Discovers 5m/15m Bitcoin/Ethereum markets automatically
- Executes trades in the final window (≤120s before close)
- Buys winning side at $0.99 when available

### Position Settlement
- Auto-detects open positions from trade history
- Sells positions when price ≥ $0.999 (near-certain win)
- Claims USDC from resolved markets
- Logs P&L to CSV

**Run position settler:**
```bash
# Check once and exit
uv run python src/position_settler.py --once

# Continuous monitoring (every 5 minutes)
uv run python src/position_settler.py --daemon

# Live mode (real transactions)
uv run python src/position_settler.py --once --live
```

## 🔍 Validation Checklist

Before deployment:
- [ ] `.env` file contains all required variables
- [ ] `CLOB_API_KEY` and `CLOB_PASSPHRASE` are valid
- [ ] `PRIVATE_KEY` is properly encrypted (not in version control)
- [ ] Test `--once` mode to verify market detection
- [ ] Verify WebSocket connection in logs
- [ ] Check market selection logic matches requirements
- [ ] Validate trigger logic in dry-run mode
- [ ] **Check USDC balance**: `uv run python scripts/check_balance.py`
- [ ] **Check open positions**: `uv run python src/position_settler.py --once`
- [ ] **Test position settler in live mode**: `uv run python src/position_settler.py --once --live`

## 🛠️ Available Scripts

### Essential Scripts
- `scripts/check_balance.py` - Check USDC balance and allowance
- `scripts/approve.py` - Approve USDC spending for trading
- `src/position_settler.py` - Settle positions and claim winnings (replaces check_all_positions.py)

### Testing Scripts
- `scripts/test_clob_connection.py` - Test CLOB API connection
- `scripts/test_order_submission.py` - Test order submission (dry-run)
- `scripts/test_websocket.py` - Test WebSocket connection

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
