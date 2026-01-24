# Polymarket Trading Bot - Custom Instructions for GitHub Copilot

## 🎯 Project Overview

This is a high-frequency trading bot for Polymarket 5/15-minute Bitcoin/Ethereum markets. The system automatically discovers, monitors, and executes trades in the final seconds before market close.

**Key Characteristics:**
- Source of truth: Implementation code (main.py, gamma_15m_finder.py, hft_trader.py)
- Language: Python 3.12.8
- Package Manager: uv
- Mode: Dry-run (default) or Live trading (--live flag)
- Execution: Single poll (--once) or continuous loop (default)

---

## 📋 System Parameters (From Code)

| Parameter | Value | Location | Purpose |
|-----------|-------|----------|---------|
| Search Window | 20 minutes | `gamma_15m_finder.py:36` | TZ requirement - find markets ending in < 20min |
| Market Types | BTC/ETH 5/15m | `main.py` & `gamma_15m_finder.py` | Only short-duration markets |
| Poll Interval | 90 seconds | `main.py:43` | How often to check Gamma API |
| Trader Start Buffer | 180 seconds | `main.py:44` | Start trader 3 min before market close |
| Trigger Threshold | ≤1.0 second | `hft_trader.py:352` | Execute when this close to market end |
| Winning Side Logic | price > 0.50 | `hft_trader.py:295` | YES wins if best_ask_yes > best_ask_no |
| Buy Price | $0.99 | `hft_trader.py:67` | Maximum price for FOK order |
| Min Time to Start | 30 seconds | `main.py:45` | Don't start if less time remaining |

---

## 🏗️ Architecture & Components

### Core Files

1. **main.py** - Orchestrator
   - Market polling loop every 90 seconds
   - Trader task management
   - Command-line argument parsing
   - Logging setup

2. **gamma_15m_finder.py** - Market Discovery
   - Queries Polymarket Gamma API with `q` parameter (not `query`)
   - Filters for active, non-closed markets ending within 20 minutes
   - Extracts condition_id and token_ids (YES/NO)
   - Handles UTC/ET timezone conversion

3. **hft_trader.py** - Trading Execution
   - WebSocket connections to CLOB for real-time prices
   - Monitors both YES and NO token prices
   - Determines winning side (price > 0.50 logic)
   - Executes FOK order when trigger fires (≤1 second)

### Data Flow

```
Gamma API (market discovery)
         ↓
GammaAPI15mFinder (filters & extracts tokens)
         ↓
TradingBotRunner (launches traders)
         ↓
LastSecondTrader (connects WebSocket)
         ↓
CLOB WebSocket (real-time prices)
         ↓
CLOB REST API (order execution)
         ↓
Live Trade (if --live mode)
```

---

## 🔧 Environment Setup

**Required .env variables:**
```bash
PRIVATE_KEY=0x...                    # Wallet private key (Polygon)
POLYGON_CHAIN_ID=137                 # Polygon mainnet
CLOB_API_KEY=...                     # From Polymarket
CLOB_PASSPHRASE=...                  # From Polymarket
```

**Location:** `.env` file in project root (not version controlled)

---

## 🚀 Usage

### Dry-Run Mode (Safe - Default)
```bash
uv run python main.py                    # Continuous loop
uv run python main.py --once             # Single poll (testing)
```

### Live Trading Mode (Real Money!)
```bash
uv run python main.py --live             # Continuous loop with real trades
uv run python main.py --once --live      # Single poll with real trades
```

### Custom Parameters
```bash
uv run python main.py --live --size 10 --poll-interval 60
# --size: Trade size in dollars (default: 1.0)
# --poll-interval: API poll frequency in seconds (default: 90)
```

**Safety Features:**
- Dry-run mode is default (no real trades)
- Live mode shows 5-second warning before starting
- Must explicitly use --live flag

---

## 🔌 API Integration

### Gamma API
- **Endpoint:** `https://gamma-api.polymarket.com/public-search`
- **Parameter:** `q` (not `query` - returns 422 if wrong)
- **Response:** `{events: [...]}` structure with nested markets
- **Query format:** Time-specific works best (e.g., "Bitcoin Up or Down - January 24, 8:")

### CLOB WebSocket
- **Endpoint:** `wss://ws-subscriptions-clob.polymarket.com/ws/market`
- **Subscription:** `{"assets_ids": [TOKEN_ID], "type": "MARKET"}` (uppercase MARKET)
- **Messages:** Can be array `[{}]` or object `{}`
- **Data:** Extract best_ask from `asks[0]["price"]`

### CLOB REST API
- **Base URL:** `https://clob.polymarket.com`
- **Order:** `OrderArgs(token_id=..., price=0.99, size=1.0, side="BUY", order_type=OrderType.FOK)`
- **Auth:** Via CLOB_API_KEY, CLOB_PASSPHRASE in CLOB client

---

## 📊 Winning Side Detection

**Algorithm:**
```python
# In _determine_winning_side()
if best_ask_yes and best_ask_no:
    # YES wins if: best_ask_yes > best_ask_no
    # (market expects YES to be higher probability)
    winning_is_yes = best_ask_yes > best_ask_no
    self.winning_token_id = token_id_yes if winning_is_yes else token_id_no
```

**Rationale:**
- Higher ask price = market expects that side to win
- If YES ask is higher than NO ask, YES is the winning side
- Price naturally encodes market probability

---

## 🛡️ Error Handling

**API Errors:**
- Gamma API timeout → Log and retry next poll
- Gamma API 422 → Invalid query format, skip and continue
- WebSocket disconnect → Log and wait for next market close

**Order Errors:**
- Insufficient balance → Log error, continue monitoring
- Invalid token → Log error, continue monitoring
- Market already closed → Graceful exit

**Resource Cleanup:**
- WebSocket connections closed when market closes
- Async tasks awaited with proper cleanup
- Logs flushed before shutdown

---

## 📈 Performance & Latency

| Operation | Latency | Frequency |
|-----------|---------|-----------|
| Gamma API search | 1-2s | Every 90s (poll interval) |
| Market filtering | <100ms | Every 90s |
| WebSocket connection | 50-200ms | Once per trader start |
| WebSocket price update | 0.1-0.5s | Event-driven |
| Order execution | ~100ms | Once per market |

---

## 📝 Logging

**Log Files:**
- `log/finder.log` - Market discovery and polling
- `log/trades.log` - Trading execution details

**Key Log Entries:**
- `[FINDER]` - Market discovery activities
- `[TRADER]` - Trade execution activities
- `[DEBUG]` - WebSocket messages and price updates

**Enable Debugging:**
- Check logs for WebSocket message format
- Verify market detection in finder.log
- Monitor price updates in trades.log

---

## ✅ Recent Fixes & Compliance

**TZ Requirements Met:**
- ✅ Search window: 20 minutes (was 30, now 20)
- ✅ Market types: 5/15-minute Bitcoin/Ethereum
- ✅ Environment variables: Using CLOB_API_KEY and CLOB_PASSPHRASE

**System Validation:**
- ✅ Gamma API finds active markets correctly
- ✅ WebSocket connects and receives real-time prices
- ✅ Dynamic winning side detection works
- ✅ UTC/ET timezone handling correct
- ✅ Dry-run mode executes without real trades
- ✅ Live mode executes with real trades

---

## 📚 Documentation Files

- **docs/README.md** - Documentation index and quick reference
- **docs/ARCHITECTURE.md** - Detailed system architecture
- **docs/API-INTEGRATION.md** - API integration details and examples
- **.github/copilot-instructions.md** - This file (GitHub Copilot custom instructions)

---

## 🔑 Key Code Locations

| Functionality | File | Lines | Key Method |
|--------------|------|-------|-----------|
| Market polling | main.py | 240-265 | `poll_and_trade()` |
| API search | gamma_15m_finder.py | 55-93 | `search_markets()` |
| Market filtering | gamma_15m_finder.py | 95-205 | `filter_markets()` |
| WebSocket connection | hft_trader.py | 160-203 | `connect_websocket()` |
| Price monitoring | hft_trader.py | 426-476 | `listen_to_market()` |
| Winning side calc | hft_trader.py | 295-310 | `_determine_winning_side()` |
| Trigger check | hft_trader.py | 320-360 | `check_trigger()` |
| Order execution | hft_trader.py | 368-405 | `execute_order()` |

---

## 🎓 When Modifying Code

**Always check:**
1. Parameter values match system parameters table above
2. API endpoints match documented endpoints
3. WebSocket message format matches spec (uppercase MARKET, assets_ids array)
4. Timezone handling uses UTC internally, ET for display
5. Error cases are logged and don't crash the system
6. Both dry-run and live modes are tested

**Before committing:**
1. Test with `--once` flag first (single poll)
2. Verify logs show expected behavior
3. Check for deprecated Gamma API parameter names
4. Ensure environment variables are not logged

---

## 🚨 Critical Notes

1. **Live Mode is Real:** `--live` flag executes actual trades with real USDC
2. **5-Second Warning:** Live mode waits 5 seconds before starting (Ctrl+C to cancel)
3. **Private Key:** Never commit PRIVATE_KEY - it's in .env
4. **Token IDs:** Always [YES_token, NO_token] from clobTokenIds
5. **FOK Orders:** Fill-or-Kill - all or nothing, no partial fills
6. **Timezone:** All times shown as ET, stored as UTC internally

---

## 📞 Troubleshooting

**Market not found?**
- Check Gamma API is returning events for the search window
- Verify market is active and not closed
- Ensure time window calculation is correct

**WebSocket not connecting?**
- Verify endpoint: `wss://ws-subscriptions-clob.polymarket.com/ws/market`
- Check subscription format: `{"assets_ids": [TOKEN_ID], "type": "MARKET"}`
- Look for connection errors in logs

**Order not executing?**
- Verify CLOB credentials in .env
- Check account has sufficient USDC balance
- Ensure token_id is correct (YES or NO)
- Verify order price is not above best ask

---

## 🔄 Continuous Improvement

When adding features:
1. Update relevant parameter in this file
2. Add implementation to appropriate component
3. Update logs and debugging output
4. Test in dry-run mode first
5. Validate in live mode with small trades
6. Document in docs/ folder
7. Commit with clear message

---

## 📖 Reference Links

- **VS Code Copilot Custom Instructions:** https://code.visualstudio.com/docs/copilot/customization/custom-instructions
- **Polymarket:** https://polymarket.com
- **py-clob-client:** https://github.com/polymarket/py-clob-client
- **WebSocket Protocol:** https://websockets.readthedocs.io/
