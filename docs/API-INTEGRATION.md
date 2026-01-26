# API Integration Guide

## 🔗 External APIs Overview

The system integrates with three main Polymarket APIs:

1. **Gamma API** - Market discovery
2. **CLOB REST API** - Order execution
3. **CLOB WebSocket** - Real-time price monitoring

## 📍 Polymarket Gamma API

**Purpose:** Find active 5/15-minute Bitcoin/Ethereum markets

**Endpoint:** `https://gamma-api.polymarket.com/public-search`

**Request Format:**
```python
# Query parameter: 'q' (not 'query')
GET /public-search?q=Bitcoin%20Up%20or%20Down%20-%20January%2024,%208:
```

**Implementation** (gamma_15m_finder.py):
```python
async def search_markets(query: str) -> Dict[str, Any]:
    params = {"q": query}  # Must use 'q' not 'query'
    async with session.get(self.BASE_URL, params=params) as response:
        if response.status == 200:
            return await response.json()
        elif response.status == 422:
            # Validation error - invalid query format
            print(f"API validation error: {error_data}")
```

**Response Format:**
```json
{
  "events": [
    {
      "id": "178316",
      "title": "Bitcoin Up or Down - January 24, 8AM ET",
      "ticker": "btc-updown-5m-1769259600",
      "active": true,
      "closed": false,
      "endDate": "2026-01-24T14:00:00Z",
      "markets": [
        {
          "conditionId": "0xfe3abe7c...",
          "clobTokenIds": "[\"10351064302...\", \"9749632838...\"]",
          "active": true,
          "closed": false,
          "question": "Will Bitcoin price close higher on January 24 at 8AM ET?"
        }
      ]
    }
  ]
}
```

**Query Format Guide:**
```
# Time-specific queries work best
Bitcoin Up or Down - January 24, 8:    (searches 8:00, 8:05, etc)
Bitcoin Up or Down - January 24        (searches all times)
Bitcoin Up or Down                     (general search)
```

**Important Notes:**
- **Parameter name:** Must use `q`, not `query` (returns 422 otherwise)
- **Timezone:** Events use UTC in `endDate`, but market names show ET
- **Token IDs:** Array of 2 elements - [YES_token, NO_token]
- **Deduplication:** Must deduplicate by `conditionId`

**Error Codes:**
- `200` - Success
- `422` - Unprocessable Entity (invalid query format)
- `5xx` - Server error (retry with backoff)

---

## 📡 CLOB WebSocket API

**Purpose:** Real-time order book updates and price monitoring

**Endpoint:** `wss://ws-subscriptions-clob.polymarket.com/ws/market`

### Connection & Subscription

**Subscription Message:**
```json
{
  "assets_ids": ["TOKEN_ID"],
  "type": "MARKET"
}
```

**Implementation** (hft_trader.py):
```python
async def connect_websocket(self):
    # Connect to YES token
    self.ws_yes = await websockets.connect(
        self.WS_URL,
        ping_interval=20,
        ping_timeout=10
    )
    
    # Send subscription
    subscribe_msg_yes = {
        "assets_ids": [self.token_id_yes],
        "type": "MARKET"  # MUST be uppercase
    }
    await self.ws_yes.send(json.dumps(subscribe_msg_yes))
```

**Important Details:**
- **Field name:** `assets_ids` (plural, with underscore)
- **Type value:** Must be uppercase `"MARKET"`
- **Ping/Pong:** Automatic with `ping_interval=20`
- **Array format:** Always pass token ID in array

### Message Types

**1. Initial Subscription Confirmation**
```json
[]  // Empty array - confirms subscription
```

**2. Full Order Book (book event)**
```json
[
  {
    "market": "0x21535040116181fed1d0d1840470e9e8f5e1f63f02863848d34472cd1ab2e3e4",
    "asset_id": "94855542033541008716935846953008634462792156662673404731394094821990526158455",
    "event_type": "book",
    "bids": [
      {"price": "0.74", "size": "100"},
      {"price": "0.73", "size": "50"}
    ],
    "asks": [
      {"price": "0.76", "size": "100"},
      {"price": "0.77", "size": "50"}
    ],
    "timestamp": "1769262133000"
  }
]
```

**3. Price Change Event**
```json
{
  "market": "0x21535040116181fed1d0d1840470e9e8f5e1f63f02863848d34472cd1ab2e3e4",
  "asset_id": "94855542033541008716935846953008634462792156662673404731394094821990526158455",
  "event_type": "price_change",
  "best_bid": "0.74",
  "best_ask": "0.76",
  "timestamp": "1769262133000"
}
```

### Data Processing

**Array Handling** (hft_trader.py):
```python
# WebSocket can return array or single object
if isinstance(data, list):
    for update in data:
        await self.process_market_update(update, is_yes_token)
else:
    await self.process_market_update(data, is_yes_token)
```

**Price Extraction:**
```python
# From book event (array of messages)
if isinstance(data, list) and len(data) > 0:
    data = data[0]  # Extract first element

# From price change event
if event_type == "book":
    asks = data.get("asks", [])
    if asks and len(asks) > 0:
        best_ask = float(asks[0]["price"])
```

---

## 💳 CLOB REST API (Order Execution)

**Purpose:** Execute trades and manage orders

**Base URL:** `https://clob.polymarket.com`

### Authentication

**Required Environment Variables:**
```bash
PRIVATE_KEY=0x...                # Your wallet private key
POLYGON_CHAIN_ID=137             # Polygon mainnet (default)
```

**Important:** CLOB_API_KEY, CLOB_PASSPHRASE, and CLOB_SECRET are **NO LONGER NEEDED**. The new authentication method uses only the private key.

**Implementation** (hft_trader.py):
```python
def _init_clob_client(self):
    """Initialize authenticated CLOB client for live trading."""
    try:
        from py_clob_client.client import ClobClient
        
        private_key = os.getenv("PRIVATE_KEY")
        chain_id = int(os.getenv("POLYGON_CHAIN_ID", "137"))
        host = os.getenv("CLOB_HOST", "https://clob.polymarket.com")

        if not private_key:
            print("Warning: Missing PRIVATE_KEY in .env file")
            return None

        # Initialize client with just private key, host, and chain_id
        client = ClobClient(
            host=host,
            key=private_key,
            chain_id=chain_id,
        )
        
        # Create or derive API credentials from private key
        # This is REQUIRED - without it, you get 403 errors
        client.set_api_creds(client.create_or_derive_api_creds())
        
        return client
```

### Order Execution

**Order Parameters:**
```python
from py_clob_client.clob_types import OrderArgs, OrderType

order_args = OrderArgs(
    token_id=token_id,                    # YES or NO token
    price=0.99,                           # Buy price
    size=1.0,                             # Size in dollars
    side="BUY",                           # BUY or SELL
)

# Step 1: Create the order
created_order = client.create_order(order_args)

# Step 2: Post the order with type
response = client.post_order(created_order, OrderType.FOK)
```

**Order Types:**
- `FOK` (Fill-or-Kill) - Our choice: execute entire order or cancel
- `GTC` (Good-Till-Cancelled) - Order persists until filled or cancelled
- `IOC` (Immediate-or-Cancel) - Execute immediately, cancel remainder

**Response:**
```json
{
  "orderHash": "0x...",
  "status": "pending",
  "filledAmount": "0.5",
  "averagePrice": "0.99"
}
```

### Error Handling

**Common Errors:**
```python
# Insufficient balance
{"message": "Order size exceeds available balance"}

# Invalid token
{"message": "Token not found"}

# Order below minimum
{"message": "Order size below minimum"}
```

---

## 🔄 API Call Sequences

### Market Discovery Flow
```
1. TradingBotRunner.poll_and_trade()
   │
   └─ GammaAPI15mFinder.find_active_market()
      │
      ├─ search_markets("Bitcoin Up or Down - January 24, 8:")
      │  └─ GET /public-search?q=Bitcoin...
      │     └─ Returns: {events: [...]}
      │
      ├─ filter_markets(events)
      │  └─ Check: active, not closed, time window
      │     └─ Extract: condition_id, token_ids, end_time
      │
      └─ yield {market_data}
         └─ Return to TradingBotRunner
```

### Trading Execution Flow
```
1. LastSecondTrader.run()
   │
   ├─ connect_websocket()
   │  ├─ websockets.connect(wss://ws-subscriptions-clob...)
   │  └─ Send: {"assets_ids": [token_yes], "type": "MARKET"}
   │
   ├─ listen_to_market()
   │  ├─ Receive: price updates (arrays or objects)
   │  └─ process_market_update()
   │     └─ Extract: best_ask_yes, best_ask_no
   │
   ├─ check_trigger(time_remaining)
   │  └─ if time_remaining <= 1.0:
   │
   └─ execute_order()
      └─ client.create_and_post_order(order_args)
         └─ POST /create_and_post_order
            └─ Returns: {orderHash, status}
```

---

## ⏱️ API Latency Characteristics

| Operation | Typical Latency | Notes |
|-----------|-----------------|-------|
| Gamma API search | 1-2 seconds | Includes network round-trip |
| WebSocket connection | 50-200ms | Initial connection setup |
| WebSocket message | 0.1-0.5s | Depends on orderbook activity |
| Order execution | ~100ms | Network + blockchain |
| Market detection | ~90s | Poll interval |

---

## 🛡️ Error Recovery Strategies

### Gamma API Failures
```python
# Automatic retry on timeout
except asyncio.TimeoutError:
    print("API request timed out")
    return {"markets": []}  # Empty result, try next poll

# Validation errors
except response.status == 422:
    print(f"Invalid query format: {query}")
    # Continue to next query
```

### WebSocket Disconnections
```python
except websockets.exceptions.ConnectionClosed:
    print(f"WebSocket connection closed")
    # Market listener exits gracefully
    # Main loop will detect market closure and cleanup
```

### Order Execution Failures
```python
try:
    response = await asyncio.to_thread(
        self.client.create_and_post_order,
        order_args
    )
except Exception as e:
    print(f"Error executing order: {e}")
    # Continue monitoring until market closes
    # No retry - market is closing anyway
```

---

## 📊 Rate Limiting

**Gamma API:**
- No documented rate limit
- Recommend: One poll every 90s (our default)
- Max queries per poll: ~12 (current implementation)

**CLOB API:**
- No documented rate limit for order submission
- WebSocket: Unlimited messages

---

## 🔍 Debugging Tips

### Check API Connectivity
```bash
# Test Gamma API
curl 'https://gamma-api.polymarket.com/public-search?q=Bitcoin%20Up%20or%20Down'

# Test CLOB API (requires auth)
curl -H "CLOB-API-KEY: $CLOB_API_KEY" \
     https://clob.polymarket.com/get-account-balance
```

### Monitor WebSocket Traffic
- Enable `[DEBUG]` level logging to see raw WebSocket messages
- Check `log/trades.log` for price updates

### Validate Credentials
```bash
# Verify env variables are set
echo $CLOB_API_KEY
echo $CLOB_PASSPHRASE
echo $PRIVATE_KEY
```
