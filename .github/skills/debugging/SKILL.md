---
name: debugging
description: Debug trading bot issues by analyzing logs, testing API calls, validating configuration, and checking WebSocket connections.
---

# Debugging Skill

Systematic approach to diagnosing and fixing issues in the trading bot.

## When to Use

- Bot finds no markets
- WebSocket won't connect
- Orders not executing
- Prices not updating
- Timezone calculations wrong
- Performance issues

## Debugging Workflow

### 1. Check Logs First

```bash
# Market discovery issues
tail -f log/finder.log

# Trading execution issues
tail -f log/trades.log

# Look for patterns
grep "ERROR\|WARN" log/*.log
```

**What to look for:**
- API errors (422, timeout, etc.)
- WebSocket connection failures
- Market filtering results (how many found?)
- Winning side determination
- Trigger and execution timestamps

### 2. Test Market Discovery

```bash
# Single poll test
uv run python main.py --once

# This will:
# - Query Gamma API
# - Filter markets
# - Report findings
# - Exit (no trading)

# Watch logs:
tail -f log/finder.log
```

**Expected output:**
```
[FINDER] Searching for markets...
[FINDER] Found N markets
[FINDER] Market: Bitcoin Up or Down - January 24, 8:00PM-8:05PM ET
[FINDER] Condition ID: 0x...
[FINDER] Tokens: YES=123..., NO=456...
```

**If "Found 0 markets":**
- Verify Gamma API is reachable
- Check time window (should be < 20 min)
- Verify market end time format
- Check if markets are active AND not closed

### 3. Test API Directly

Create debug script to test Gamma API:

```python
# test_gamma.py
import aiohttp
import asyncio

async def test_gamma():
    async with aiohttp.ClientSession() as session:
        url = "https://gamma-api.polymarket.com/public-search"
        params = {"q": "Bitcoin Up or Down - January 24, 8:"}
        
        async with session.get(url, params=params) as resp:
            print(f"Status: {resp.status}")
            data = await resp.json()
            print(f"Events: {len(data.get('events', []))}")
            
            for event in data.get('events', []):
                print(f"- {event.get('title')}")
                print(f"  Active: {event.get('active')}")
                print(f"  Closed: {event.get('closed')}")

asyncio.run(test_gamma())
```

Run: `uv run python test_gamma.py`

### 4. Test WebSocket Connection

```python
# test_websocket.py
import asyncio
import websockets
import json

async def test_websocket():
    async with websockets.connect(
        "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    ) as ws:
        # Subscribe to token
        message = {
            "assets_ids": ["TOKEN_ID_HERE"],
            "type": "MARKET"  # Must be uppercase!
        }
        await ws.send(json.dumps(message))
        
        # Receive messages
        for _ in range(5):
            msg = await ws.recv()
            print(msg)

asyncio.run(test_websocket())
```

**Key checks:**
- Connection establishes (no timeout)
- Messages received in <1 second
- Message format is valid JSON
- Contains price data (asks, bids)

### 5. Validate Configuration

**Check .env exists:**
```bash
ls -la .env
```

**Verify environment variables:**
```python
import os
from dotenv import load_dotenv

load_dotenv()
print(f"PRIVATE_KEY: {os.getenv('PRIVATE_KEY')[:10]}...")
print(f"CLOB_API_KEY: {os.getenv('CLOB_API_KEY')[:10]}...")
print(f"CLOB_PASSPHRASE: {os.getenv('CLOB_PASSPHRASE')[:10]}...")
print(f"POLYGON_CHAIN_ID: {os.getenv('POLYGON_CHAIN_ID')}")
```

Should show last 10 chars of secrets (don't print full secrets!).

### 6. Common Issues & Solutions

**Issue: "Found 0 markets"**
- [ ] Check Gamma API is reachable: `curl https://gamma-api.polymarket.com/public-search?q=Bitcoin`
- [ ] Verify current time is correct on system
- [ ] Try with specific time: "January 24, 8:" not just "Bitcoin"
- [ ] Check if markets exist on polymarket.com/predictions
- [ ] Verify market type (5m/15m, not 1h)

**Issue: "API returns 422"**
- [ ] Check parameter name: should be `q` NOT `query`
- [ ] Verify query string format (example: "Bitcoin Up or Down - January 24, 8:")
- [ ] No special characters in query

**Issue: "WebSocket connection failed"**
- [ ] Endpoint: `wss://ws-subscriptions-clob.polymarket.com/ws/market` (note wss, not ws)
- [ ] Subscription: `{"assets_ids": [TOKEN_ID], "type": "MARKET"}` (uppercase MARKET!)
- [ ] Token ID must be string, not integer
- [ ] Check network connectivity: `ping ws-subscriptions-clob.polymarket.com`

**Issue: "No price updates"**
- [ ] Messages may come as array `[{}]` — extract first element
- [ ] Check asks array exists before accessing: `asks[0]["price"]`
- [ ] Both YES and NO WebSocket connections active?
- [ ] Check logs for message format

**Issue: "Wrong winning side"**
- [ ] Remember: higher ask = winning side
- [ ] Verify comparing asks, not bids
- [ ] Check token ID mapping (which is YES, which is NO?)
- [ ] Confirm price > 0.50 logic (not < or ==)

**Issue: "Timezone off by 5 hours"**
- [ ] Storing ET time with "UTC" label? That's wrong!
- [ ] Always convert to UTC internally, display as ET
- [ ] Check: `end_time_utc = end_time.astimezone(pytz.UTC)`
- [ ] Verify: stored value uses UTC, not ET

**Issue: "Order doesn't execute"**
- [ ] Dry-run mode? Orders won't execute (normal!)
- [ ] Use `--live` flag to enable real trades
- [ ] Check CLOB credentials in .env
- [ ] Verify account has USDC balance
- [ ] Check order is on correct token (YES or NO)
- [ ] Verify price ($0.99) is not above best ask

### 7. Performance Debugging

**Slow market discovery:**
```bash
# Time the search
time uv run python main.py --once
```

Expected: <3 seconds for search + filter

**Slow WebSocket:**
```python
# Add timestamps to price updates
import time
start = time.time()
msg = await ws.recv()
latency = time.time() - start
print(f"Latency: {latency:.3f}s")
```

Expected: <500ms per message

### 8. Before Reporting Bugs

Collect this information:

```bash
# System info
python --version
uv --version

# Environment
echo $POLYGON_CHAIN_ID

# Recent logs
tail -20 log/finder.log
tail -20 log/trades.log

# Git status
git status
git log --oneline -5

# Test commands
uv run python main.py --once 2>&1 | head -50
```

### 9. Testing Checklist

Before committing changes:

- [ ] `uv run python main.py --once` completes without error
- [ ] Logs show expected behavior
- [ ] Check logs for any ERROR or WARN
- [ ] No environment variables printed in logs
- [ ] WebSocket messages parsed correctly
- [ ] Prices calculated correctly
- [ ] Winning side logic correct (higher ask wins)
- [ ] Trigger fires at correct time (≤1s)
- [ ] Order executes only in --live mode

## Useful Debugging Tools

| Tool | Command | Purpose |
|------|---------|---------|
| API test | `curl https://gamma-api.polymarket.com/public-search?q=Bitcoin` | Check API reachable |
| Network | `ping ws-subscriptions-clob.polymarket.com` | Check WebSocket endpoint |
| Logs | `tail -f log/*.log` | Real-time monitoring |
| Python test | `uv run python -c "import asyncio..."` | Quick Python tests |
| Git diff | `git diff main.py` | See what changed |
| Environment | `env \| grep CLOB` | Check env vars |

## References

- [Polymarket API Docs](https://polymarket.com)
- [py-clob-client Repo](https://github.com/polymarket/py-clob-client)
- [WebSocket Protocol](https://websockets.readthedocs.io/)
- Project docs: `docs/README.md`
