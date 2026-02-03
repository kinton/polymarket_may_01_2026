# How to Claim Winnings

## Option 1: Use Polymarket UI (Recommended)

**Easiest way:**
1. Go to [Polymarket](https://polymarket.com)
2. Click on your profile → Portfolio  
3. Click "Claim winnings" button
4. Confirm transaction in your wallet
5. Done! USDC will be transferred to your balance

## Option 2: Use Command Line (Advanced)

### Step 1: Find Your Token IDs

**Method A: Browser DevTools**
1. Open Polymarket in browser
2. Press F12 (Developer Tools)
3. Go to Network tab
4. Click on your winning position
5. Look for API calls containing `token_id`
6. Copy the token_id (long number)

**Method B: Polygonscan**
1. Go to https://polygonscan.com/address/0x35d26795bE15E060A2C7AA42C2aCF9527E3acE47
2. Click "Erc1155 Token Txns" tab
3. Look for recent transfers FROM CTF contract
4. Token ID is in the "Token ID" column
5. Click on it to see balance

### Step 2: Find Condition ID

Each market has a unique `condition_id`. You need it to claim.

**Method A: From Gamma API**
```bash
# Search for your market
curl "https://gamma-api.polymarket.com/public-search?q=Bitcoin%20Up%20or%20Down%20February%202"

# Look for conditionId in response
```

**Method B: From Market URL**
1. Go to the market page on Polymarket
2. URL looks like: `polymarket.com/event/bitcoin-up-or-down`
3. Open DevTools → Network
4. Look for API call with condition_id

### Step 3: Claim via Script

```bash
# Dry run first (safe)
uv run python scripts/claim_winnings.py \
  --condition-id <CONDITION_ID> \
  --token-id <TOKEN_ID>

# If looks good, claim for real
uv run python scripts/claim_winnings.py \
  --condition-id <CONDITION_ID> \
  --token-id <TOKEN_ID> \
  --live
```

## Example

From your screenshot, you have:
- **3.04 shares** @ $1.00 = **$3.04** to claim
- Market: "Bitcoin Up or Down - February 2, 6:00PM-6:15PM ET"
- Position: "Down" (winning side)

To claim this:
1. **Option 1 (Easy):** Click "Claim winnings" in UI ✅
2. **Option 2 (Manual):** Find token_id and run claim script

## After Claiming

Check your new balance:
```bash
uv run python scripts/check_balance.py
```

Should show:
- Before: $0.55 USDC
- After: $3.59 USDC ($0.55 + $3.04)

## Troubleshooting

**"No trades found"**
- Trades were made through UI, not API
- Use Polygonscan to find token IDs

**"Transaction failed"**
- Wrong condition_id or token_id
- Market not resolved yet
- Insufficient MATIC for gas (need ~0.001 MATIC)

**"Not enough gas"**
- Need MATIC for transaction fees
- Get some MATIC from faucet or exchange
- Transfer to proxy address: `0x35d26795bE15E060A2C7AA42C2aCF9527E3acE47`

## Technical Details

**Claiming works by:**
1. Calling `redeemPositions()` on CTF contract
2. Burning your winning conditional tokens
3. Receiving USDC 1:1 for winning shares
4. Transaction requires ~0.0001-0.0005 MATIC gas

**Contracts (Polygon):**
- CTF: `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`
- USDC: `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`

## Quick Reference

```bash
# Check current balance
uv run python scripts/check_balance.py

# Check on-chain (including tokens)
uv run python scripts/check_proxy_onchain.py

# Claim winnings (UI is easiest!)
# Or use claim_winnings.py with token IDs
```

**Recommendation:** Just use the Polymarket UI "Claim winnings" button! It's faster and handles everything automatically.
