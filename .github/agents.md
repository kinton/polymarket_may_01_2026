# Agent Context for Copilot

Critical information that AI should always remember when working on this project.

## 🎯 Project Purpose

High-frequency trading bot for Polymarket 5/15-minute Bitcoin/Ethereum markets. Automatically:
1. Discovers active markets ending within 20 minutes
2. Launches traders 3 minutes before market close
3. Monitors real-time prices via WebSocket
4. Executes trades in final seconds (≤1 second before close)

## ⚠️ Critical Requirements (ALWAYS CHECK)

### 1. System Parameters (Source: Implementation Code)
- **Search Window:** 20 minutes (TZ requirement - not 30!)
- **Market Types:** BTC/ETH 5m & 15m only (not 1h or 30m)
- **Poll Interval:** 90 seconds
- **Trader Start Buffer:** 180 seconds (3 min before close)
- **Trigger Threshold:** ≤1.0 second remaining
- **Winning Side Logic:** price > 0.50 (YES wins if best_ask_yes > best_ask_no)
- **Buy Price:** $0.99 (max FOK price)

### 2. API Quirks (Very Important!)
- **Gamma API parameter:** Use `q` NOT `query` (returns 422 if wrong!)
- **WebSocket subscription:** Use uppercase `"type": "MARKET"` (not lowercase)
- **WebSocket format:** Messages come as arrays `[{}]` not single objects
- **Token IDs:** Always [YES_token, NO_token] from `clobTokenIds`

### 3. Timezone Handling
- **Internal storage:** UTC (always store as UTC)
- **Display format:** Eastern Time (ET)
- **Critical:** `end_time_utc` must be actual UTC, not ET mislabeled as UTC

### 4. Environment Variables (Required)
```bash
PRIVATE_KEY=0x...
POLYGON_CHAIN_ID=137
CLOB_API_KEY=...
CLOB_PASSPHRASE=...
```
Never commit `.env`. Never log environment variables.

### 5. Execution Modes
- **Dry-run (default):** No real trades, safe for testing
- **Live mode:** Real USDC trades, shows 5-second warning before starting
- **Once mode:** Single poll (testing), no infinite loop

### 6. WebSocket Messages
Structure:
```python
{
  "asset_id": "TOKEN_ID",
  "asks": [["0.76", "100"], ...]  # Extract asks[0]["price"]
}
```
Can be array or object - handle both cases.

## 📁 Code Locations

| What | Where |
|------|-------|
| Market polling loop | `main.py:240-265` |
| Gamma API search | `gamma_15m_finder.py:55-93` |
| Market filtering | `gamma_15m_finder.py:95-205` |
| WebSocket connection | `hft_trader.py:160-203` |
| Winning side calc | `hft_trader.py:295-310` |
| Trigger check | `hft_trader.py:320-360` |
| Order execution | `hft_trader.py:368-405` |

## 🚨 Common Mistakes (Avoid!)

1. ❌ Using 30 minutes instead of 20 (violates TZ requirement)
2. ❌ Using `query` parameter instead of `q` (API returns 422)
3. ❌ Lowercase `type: "market"` in WebSocket (won't work)
4. ❌ Storing ET time labeled as UTC (timezone bugs)
5. ❌ Not handling array responses from WebSocket
6. ❌ Using hardcoded YES token instead of dynamic selection
7. ❌ Committing `.env` file with secrets
8. ❌ Not testing with `--once` flag before live mode

## ✅ Before Committing

- [ ] Run `uv run pytest test_*.py -v` to verify all tests pass
- [ ] Run `uv run ruff check *.py` to check linting
- [ ] Test with `--once` flag (single poll test) for integration validation
- [ ] Verify logs show expected behavior
- [ ] Check all parameter values match table above
- [ ] Ensure no environment variables leaked in code
- [ ] Confirm both dry-run and live modes work (if modified)

## 📚 Documentation Files

| Purpose | File |
|---------|------|
| Vision & constraints | `ai/Soul.md` |
| Full docs index | `docs/README.md` |
| System architecture | `docs/ARCHITECTURE.md` |
| API integration guide | `docs/API-INTEGRATION.md` |
| Technical specs | `docs/PROJECT.md` |

## 🔗 Related Skills

- `.github/skills/market-discovery/SKILL.md` — Finding active markets
- `.github/skills/trading-execution/SKILL.md` — Executing trades
- `.github/skills/debugging/SKILL.md` — Debugging issues
