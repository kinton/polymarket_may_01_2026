---
name: market-discovery
description: Find active Polymarket 5/15-minute Bitcoin/Ethereum markets within 20-minute search window. Queries Gamma API, filters by status and timing, extracts token IDs.
---

# Market Discovery Skill

Finds active short-duration prediction markets on Polymarket that are ending soon, ready for trading.

## When to Use

- Finding new markets to trade in
- Debugging market discovery issues
- Adding new market types or filters
- Validating that API returns correct data

## Key Implementation

**File:** `gamma_15m_finder.py`

### Search Parameters (CRITICAL)

- **Search window:** 20 minutes (find markets ending in < 20 min)
- **Market types:** Bitcoin/Ethereum 5-minute or 15-minute only
- **Status:** Active=true AND closed=false
- **API parameter:** Use `q` (NOT `query` - returns 422!)

### Step-by-Step Process

1. **Query Gamma API**
   ```python
   # Endpoint: https://gamma-api.polymarket.com/public-search
   # Parameter: q (query string)
   # Example: "Bitcoin Up or Down - January 24, 8:"
   ```

2. **Parse Response**
   - API returns `{events: [{markets: [...]}]}`
   - Extract nested markets from events
   - Get `clobTokenIds` (array: [YES_token, NO_token])

3. **Filter Markets**
   - Check `active=true` and `closed=false` (both must pass!)
   - Verify end time is within 20 minutes
   - Verify market type is 5m or 15m (not 1h or 30m)
   - Validate BTC or ETH in title

4. **Extract Token IDs**
   - Parse `clobTokenIds` string to get individual tokens
   - First element = YES token
   - Second element = NO token

5. **Return Results**
   - Return condition_id, token_id_yes, token_id_no, end_time

### Common Issues

**"No markets found" error:**
- Check if API is returning any events at all
- Verify end time calculation (must be < 20 min from now)
- Confirm market `active=true` and `closed=false`
- Test with exact time format: "January 24, 8:"

**API returns 422:**
- You're using `query` instead of `q` parameter
- Always use: `q=...` in URL

**Token extraction fails:**
- `clobTokenIds` is a string, not array
- Must parse string like `"[\"123...\", \"456...\"]"`
- Use JSON parsing or regex to extract individual IDs

**Timezone off:**
- Always store times as UTC
- Display as Eastern Time (ET)
- When filtering: compare UTC times to UTC now
- Bug: storing ET time with "UTC" label (wrong!)

## Testing

```bash
# Single poll to test market discovery
uv run python main.py --once

# Check logs
tail -f log/finder.log
```

Watch for:
- Found N markets
- Market end times
- Token ID extraction
- Active status confirmation

## API Response Example

```json
{
  "events": [
    {
      "id": "178316",
      "title": "Bitcoin Up or Down - January 24, 8:00PM-8:05PM ET",
      "ticker": "btc-updown-5m-1769214600",
      "active": true,
      "closed": false,
      "endDate": "2026-01-25T01:05:00Z",
      "markets": [
        {
          "conditionId": "0xfe3abe7c...",
          "clobTokenIds": "[\"10351064302...\", \"9749632838...\"]",
          "active": true,
          "closed": false
        }
      ]
    }
  ]
}
```

Key observations:
- Structure: events → markets (nested!)
- `endDate` is ISO format with Z (UTC)
- `clobTokenIds` is JSON string (not array!)
- Both `active` and `closed` flags must be checked
