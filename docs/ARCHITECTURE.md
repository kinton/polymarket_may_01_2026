# System Architecture

## 🏗️ High-Level Architecture

```
┌─────────────────────────────────────────────────────────┐
│           TradingBotRunner (main.py)                    │
│     Orchestrates market discovery & trader execution    │
└──────────────────┬──────────────────────────────────────┘
                   │
       ┌───────────┴───────────┐
       │                       │
       ▼                       ▼
┌──────────────────┐    ┌──────────────────────┐
│ GammaAPI15mFinder│    │ LastSecondTrader     │
│ (gamma_15m_...) │    │ (hft_trader.py)      │
│                  │    │                      │
│ • Gamma API      │    │ • WebSocket monitor  │
│ • Market filter  │    │ • Price tracking     │
│ • Time calc      │    │ • Order execution    │
└──────────────────┘    └──────────────────────┘
       │                       │
       └───────────┬───────────┘
                   │
       ┌───────────┴─────────────────┐
       │                             │
       ▼                             ▼
┌─────────────────┐        ┌──────────────────┐
│  Polymarket     │        │   Polymarket     │
│  Gamma API      │        │   CLOB API       │
│ (market search) │        │ (order execution)│
└─────────────────┘        └──────────────────┘
                                    │
                                    ▼
                           ┌──────────────────┐
                           │   WebSocket      │
                           │  (price updates) │
                           └──────────────────┘
```

## 📦 Core Components

### 1. TradingBotRunner (main.py)
**Role:** Orchestrator and market polling engine

**Key Responsibilities:**
- Poll Gamma API every `POLL_INTERVAL` (90s) for new markets
- Filter markets based on time window (20 minutes)
- Launch trader tasks when markets are suitable
- Manage async tasks and cleanup

**Key Attributes:**
```python
POLL_INTERVAL = 90          # seconds between API polls
TRADER_START_BUFFER = 180   # start trader 3min before close
MIN_TIME_TO_START = 30      # minimum time left to start trader
```

**Entry Points:**
- `run()` - Main async entry point
- `poll_and_trade()` - Polling loop
- `start_trader_for_market()` - Launch trader for specific market

### 2. GammaAPI15mFinder (gamma_15m_finder.py)
**Role:** Market discovery and filtering

**Key Responsibilities:**
- Query Polymarket Gamma API with time-specific searches
- Filter for Bitcoin/Ethereum 5-15 minute markets
- Validate market end times against search window
- Extract token IDs (YES/NO)

**Key Methods:**
- `search_markets(query)` - Query Gamma API (uses `q` parameter)
- `filter_markets(events)` - Filter for matching markets
- `find_active_market()` - Main discovery method

**Filtering Criteria:**
- Market `active=True` and `closed=False`
- Market ends within `max_minutes_ahead` (20 minutes)
- Has both YES and NO token IDs

**Return Format:**
```python
{
    "condition_id": "0x...",
    "token_id_yes": "...",
    "token_id_no": "...",
    "end_time": "HH:MM:SS ET",
    "end_time_utc": "YYYY-MM-DD HH:MM:SS UTC",
    "minutes_until_end": float,
    "title": "Market Name",
    "ticker": "ticker-slug"
}
```

### 3. LastSecondTrader (hft_trader.py)
**Role:** High-frequency trading execution in final seconds

**Key Responsibilities:**
- Connect to CLOB WebSocket for real-time prices
- Monitor both YES and NO token prices
- Determine winning side (price > 0.50)
- Execute FOK order in final second

**Key Methods:**
- `connect_websocket()` - Establish WebSocket connections
- `listen_to_market()` - Process price updates
- `process_market_update()` - Parse price data
- `_determine_winning_side()` - Calculate winner
- `check_trigger()` - Check execution conditions
- `execute_order()` - Submit FOK order

**Winning Side Logic:**
```python
def _determine_winning_side(self):
    # YES wins if: best_ask_yes > best_ask_no (price > 0.50 indicates YES)
    if self.best_ask_yes and self.best_ask_no:
        winning_is_yes = self.best_ask_yes > self.best_ask_no
        self.winning_token_id = self.token_id_yes if winning_is_yes else self.token_id_no
```

**Trigger Conditions:**
- Time remaining ≤ 1.0 second
- Best ask ≤ $0.99
- Order not yet executed

## 🔄 Data Flow

### Market Discovery Cycle
```
1. TradingBotRunner.poll_and_trade()
   └─> GammaAPI15mFinder.find_active_market()
       └─> search_markets(query) → Gamma API
       └─> filter_markets(events) → Validated markets
       └─> yield {condition_id, token_ids, end_time}

2. For each valid market:
   TradingBotRunner.start_trader_for_market(market)
   └─> LastSecondTrader(condition_id, token_id_yes, token_id_no, end_time)
   └─> trader.run()
```

### Real-Time Trading Cycle
```
1. LastSecondTrader.connect_websocket()
   └─> wss://ws-subscriptions-clob.polymarket.com/ws/market
   └─> Subscribe to both token_id_yes and token_id_no

2. LastSecondTrader.listen_to_market()
   └─> Listen to both WebSocket streams concurrently
   └─> Parse incoming price updates
   
3. For each price update:
   └─> process_market_update(data, is_yes_token)
   └─> Extract best_ask prices
   └─> _determine_winning_side()
   └─> check_trigger(time_remaining)
   
4. When trigger fires:
   └─> execute_order() → CLOB API → Live trade
```

## ⏱️ Timing Architecture

### Timeline Example (9:00 ET Market)
```
08:45:00 ← 15min before close
  │
  ├─ Market appears in search results
  │
08:57:00 ← 3min before close (TRADER_START_BUFFER)
  │
  ├─ Trader launched
  ├─ WebSocket connections established
  ├─ Monitoring begins
  │
08:59:59 ← 1 second before close (TRIGGER_SECONDS)
  │
  ├─ Trigger fires
  ├─ Order submitted
  ├─ FOK order executed
  │
09:00:00 ← MARKET CLOSES
```

## 🔌 Async Task Management

### Task Hierarchy
```
main()
└─ TradingBotRunner.run()
   └─ TradingBotRunner.poll_and_trade()
      ├─ [Loop every POLL_INTERVAL]
      │
      └─ For each market:
         └─ asyncio.create_task(
              TradingBotRunner.start_trader_for_market()
              └─ LastSecondTrader.run()
                 └─ asyncio.gather(
                      listen_to_ws(YES_token),
                      listen_to_ws(NO_token)
                    )
            )
```

### Concurrency Model
- Main polling loop: Single
- Traders: Multiple (one per market)
- WebSocket listeners per trader: Two (YES + NO)
- All async with proper cleanup on market close

## 📊 State Management

### TradingBotRunner State
```python
self.active_traders = {}        # condition_id → asyncio.Task
self.monitored_markets = set()  # condition_id (to avoid duplicates)
```

### LastSecondTrader State
```python
self.ws_yes = None              # YES token WebSocket
self.ws_no = None               # NO token WebSocket
self.best_ask_yes = None        # Latest YES best ask
self.best_ask_no = None         # Latest NO best ask
self.winning_token_id = None    # Selected token for trading
self.order_executed = False     # Execution status
```

## 🛡️ Error Handling

### Graceful Degradation
- WebSocket connection failure → Log error, wait for next poll
- API error → Retry with exponential backoff
- Order execution error → Log, continue monitoring
- Market close detection → Clean exit

### Resource Cleanup
- WebSocket connections closed on market close
- Tasks awaited with timeout
- Log files flushed before exit

## 📈 Performance Characteristics

| Operation | Latency | Frequency |
|-----------|---------|-----------|
| API Poll | ~1-2s | Every 90s |
| Market Filter | <100ms | Every 90s |
| WebSocket Update | 0.1-0.5s | Event-driven |
| Order Execution | ~100ms | Once per market |

## 🔐 Security Considerations

1. **Private Key Management**
   - Stored in `.env` (not in version control)
   - Only used during CLOB client initialization
   - Never logged

2. **API Keys**
   - CLOB_API_KEY and CLOB_PASSPHRASE stored in `.env`
   - Used only for CLOB API authentication
   - Never exposed in logs

3. **Mode Protection**
   - Live mode requires explicit `--live` flag
   - 5-second warning before live trading starts
   - Dry-run mode by default (safe)

## 📝 Logging Architecture

### Log Files
- `log/finder.log` - Market discovery and polling
- `log/trades.log` - Trading execution details

### Log Levels
- DEBUG: WebSocket messages, trade details
- INFO: Market found, trader started, execution
- ERROR: API errors, connection failures
- CRITICAL: Unexpected system failures
