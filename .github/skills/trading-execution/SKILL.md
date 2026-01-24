---
name: trading-execution
description: Execute trades on Polymarket by monitoring WebSocket prices, determining winning side (price > 0.50), and submitting FOK orders at market close.
---

# Trading Execution Skill

Executes trades by monitoring real-time prices and submitting orders at optimal time.

## When to Use

- Implementing order execution logic
- Fixing price monitoring issues
- Debugging WebSocket connections
- Improving trade timing or trigger logic

## Key Implementation

**File:** `hft_trader.py`

### Core Strategy

1. **Connect to WebSocket** → Subscribe to both YES and NO tokens
2. **Monitor Prices** → Get best ask for both sides
3. **Determine Winner** → Compare asks (price > 0.50 logic)
4. **Check Trigger** → Wait for ≤1 second remaining
5. **Execute Order** → Submit FOK order on winning side at $0.99

### WebSocket Connection

```python
# Endpoint: wss://ws-subscriptions-clob.polymarket.com/ws/market
# Subscription message (CRITICAL format):
{
  "assets_ids": ["TOKEN_ID"],
  "type": "MARKET"          # MUST be uppercase!
}
```

**Important:**
- Connect twice: once for YES token, once for NO token
- Messages may come as array `[{}]` or object `{}`
- Handle both cases in parsing

### Winning Side Detection

```python
# Method: _determine_winning_side()
# Logic: Higher ask price = market expects that side to win

if best_ask_yes > best_ask_no:
    # YES has higher ask → market expects YES to win
    winning_token_id = token_id_yes
else:
    # NO has higher ask → market expects NO to win
    winning_token_id = token_id_no
```

**Why this works:**
- Market prices encode probability
- Higher ask = stronger conviction
- Market is predicting the higher-priced side will win

### Trigger Mechanism

**When to execute:** ≤1.0 second remaining

```python
# Check in: check_trigger()
seconds_remaining = (market_end_time - now).total_seconds()
if seconds_remaining <= 1.0:
    execute_order()
```

**Why 1 second?**
- Enough time for order execution
- Late enough for price clarity
- Balances risk and information

### Order Execution

```python
# Method: execute_order()
# Order format:
OrderArgs(
    token_id=winning_token_id,  # YES or NO (not both!)
    price=0.99,                  # FOK at max $0.99
    size=1.0,                    # Size in dollars
    side="BUY",
    order_type=OrderType.FOK     # Fill-or-Kill (all or nothing)
)
```

**Safety:**
- FOK = no partial fills, clean execution
- $0.99 max ensures profitability at $1.00
- Buy only on winning side (don't hedge)

### Error Handling

**WebSocket Disconnects:**
- Log error, exit gracefully
- Don't retry connection (market timing is tight)
- Let main loop start new trader if needed

**Order Fails:**
- Insufficient balance → Log and exit
- Invalid token → Log and exit
- Market closed → Normal completion
- API timeout → Log and exit

### Logging

Monitor these messages in `log/trades.log`:

```
[TRADER] Connecting to WebSocket for tokens: YES, NO
[TRADER] Price update: YES=$0.65, NO=$0.35
[TRADER] Winning side: YES (higher ask)
[TRADER] Trigger fired: 0.8 seconds remaining
[TRADER] Executing order on YES token at $0.99
[TRADER] Order submitted successfully
```

## Testing

```bash
# Test single poll with real API (no trading)
uv run python main.py --once

# Test live mode (if confident)
uv run python main.py --live --once

# Watch logs during execution
tail -f log/trades.log
```

Check for:
- WebSocket connection messages
- Price updates from both tokens
- Winning side determination
- Trigger and execution timing
- Order submission confirmation

## Common Issues

**WebSocket won't connect:**
- Verify endpoint: `wss://ws-subscriptions-clob.polymarket.com/ws/market`
- Check subscription format: `{"assets_ids": [...], "type": "MARKET"}` (uppercase!)
- Confirm token IDs are strings, not integers

**Prices not updating:**
- WebSocket messages may be array `[{}]` — extract first element
- Check asks array exists: `asks[0]["price"]`
- Verify both YES and NO connections are active

**Wrong winning side:**
- Remember: higher ask = winning side
- Check if comparing asks correctly (not bids)
- Verify token mapping (token_id_yes vs token_id_no)

**Order won't execute:**
- Dry-run mode won't place real orders (normal!)
- Verify CLOB credentials in .env
- Check account has sufficient USDC
- Ensure trigger fired (≤1 second)

## Performance Targets

| Metric | Target | Actual |
|--------|--------|--------|
| WebSocket latency | <500ms | 0.1-0.5s |
| Trigger detection | <1s | ~0.8s |
| Order execution | <100ms | ~100ms |
| Total time to trade | <2s | 1-2s |

## References

- WebSocket protocol: `https://websockets.readthedocs.io/`
- py-clob-client: `https://github.com/polymarket/py-clob-client`
- Order types: FOK = Fill-or-Kill (no partial fills)
