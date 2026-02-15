# Trading Strategy Guide

## Market Discovery Modes

### 1. **Default Mode** (Bitcoin/Ethereum only)
```bash
uv run python main.py --live
```
Searches for:
- Bitcoin Up or Down
- Ethereum Up or Down  
- BTC Up or Down
- ETH Up or Down

✅ **Best for:** High-frequency 5m/15m Bitcoin/Ethereum markets  
✅ **Finds:** ~1-3 markets per hour during active times

---

### 2. **Custom Markets Mode** (Add your own)
```bash
export MARKET_QUERIES="Trump;Election;Will"
uv run python main.py --live
```

Searches for:
- Bitcoin/Ethereum (default)
- **+** Trump markets
- **+** Election markets  
- **+** "Will" questions

✅ **Best for:** Trading specific events you're tracking  
✅ **Finds:** More markets, but requires manual curation

---

### 3. **Wide Discovery Mode** (Everything)
```bash
export USE_WIDE_SEARCH=true
uv run python main.py --live
```

Searches for:
- Bitcoin/Ethereum (default)
- **+** All markets via broad queries (a, b, c, d, e)

✅ **Best for:** Maximum market coverage  
⚠️ **Warning:** May find irrelevant markets

---

## How to Add Custom Markets

### Step 1: Find Markets You Want
Go to [Polymarket.com](https://polymarket.com) and note the market names:
- "Will Trump win 2024?"
- "Will Bitcoin reach $100k?"
- "Will interest rates drop?"

### Step 2: Extract Keywords
From the titles, pick distinctive words:
- Trump → "Trump"
- Bitcoin $100k → "Bitcoin", "100k"  
- Interest rates → "rates", "interest"

### Step 3: Set Environment Variable
```bash
# In .env file:
MARKET_QUERIES="Trump;100k;rates;interest;Will"

# Or in docker-compose.yml:
environment:
  - MARKET_QUERIES=Trump;100k;rates;interest;Will
```

### Step 4: Test Discovery
```bash
uv run python -m src.gamma_15m_finder --once
```

Check output to see if your markets are found.

---

## Docker Deployment Examples

### Default (Bitcoin/Ethereum only)
```yaml
# docker-compose.yml
services:
  trading-bot:
    command: python main.py --live --size 2
```

### Custom Markets
```yaml
# docker-compose.yml
services:
  trading-bot:
    command: python main.py --live --size 2
    environment:
      - MARKET_QUERIES=Trump;Election;Fed;rates
```

### Wide Search
```yaml
# docker-compose.yml
services:
  trading-bot:
    command: python main.py --live --size 2
    environment:
      - USE_WIDE_SEARCH=true
```

---

## Tips for Market Selection

### ✅ Good Query Keywords:
- **Specific events:** "Trump", "Fed", "Apple"
- **Time-sensitive:** "Up or Down", "Q1", "January"
- **Binary outcomes:** "Will", "or", "versus"

### ❌ Avoid Generic Keywords:
- "yes", "no" (too broad)
- "the", "a", "is" (too common)
- Single letters: "a", "b" (unless using wide search)

### 🎯 Best Practice:
1. Start with Bitcoin/Ethereum (proven to work)
2. Add 2-3 specific keywords for events you track
3. Test with `--once` flag before going live
4. Monitor logs to ensure markets are found

---

## Market Timing

5-minute markets appear **5 minutes** before close  
15-minute markets appear **15 minutes** before close  

Bot starts trading **3 minutes** before market close (`TRADER_START_BUFFER=180`)

Example timeline for 15-minute market:
- **02:00 ET** - Market appears in API
- **02:12 ET** - Bot starts monitoring (3 min before close)
- **02:14:59 ET** - Bot executes trade (last second)
- **02:15 ET** - Market closes

---

## Monitoring

### Check What Markets Are Found:
```bash
docker compose logs trading-bot | grep "Found.*matching"
```

### Check Trade Execution:
```bash
cat log/trades-*.log
```

### Check Position Settlement:
```bash
docker compose logs position-settler
```

---

## Troubleshooting

**Q: No markets found?**  
A: Check if markets exist at Polymarket.com for your timeframe (next 20 minutes)

**Q: Markets found but no trades?**  
A: Check `log/trades-*.log` - if empty, trader is not executing (possibly timing/balance issues)

**Q: Want to trade more markets?**  
A: Add `MARKET_QUERIES` with relevant keywords, test with `--once` first

**Q: Too many irrelevant markets?**  
A: Remove generic keywords, make queries more specific
