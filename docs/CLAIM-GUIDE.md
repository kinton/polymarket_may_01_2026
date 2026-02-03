# How to Claim Winnings from Polymarket

## Quick Start (Easiest)

**Use Polymarket UI:**
1. Go to [Polymarket](https://polymarket.com)
2. Profile → Portfolio → "Claim winnings"
3. Confirm in MetaMask
4. Done!

## Automatic Claiming via Bot

The bot uses `position_settler.py` to automatically:
1. Detect open positions from trade history
2. Sell positions when price ≥ $0.999 (99.9% win chance)
3. Claim USDC from resolved markets

**Run once:**
```bash
uv run python src/position_settler.py --once
```

**Run continuously (daemon mode):**
```bash
# Check every 5 minutes (default)
uv run python src/position_settler.py --daemon

# Custom interval (e.g., every 2 minutes)
uv run python src/position_settler.py --daemon --interval 120
```

**Live mode (real transactions):**
```bash
uv run python src/position_settler.py --once --live
```

## Limitations

⚠️ **Position settler only detects trades made via CLOB API** (i.e., trades made by this bot).

If you traded manually through the Polymarket UI, those positions won't appear in the API trade history. In that case:
- **Option 1:** Use Polymarket UI to claim (recommended)
- **Option 2:** Get token IDs manually (see advanced section below)

## Advanced: Manual Claiming (When Auto-Detection Fails)

## Advanced: Manual Claiming (When Auto-Detection Fails)

If the bot can't detect your positions (e.g., trades made through UI), you need to find token IDs manually.

### Finding Token IDs

**Method A: Polygonscan (Best for UI trades)**
1. Go to https://polygonscan.com/address/YOUR_PROXY_ADDRESS
2. Replace YOUR_PROXY_ADDRESS with value from .env: `POLYMARKET_PROXY_ADDRESS`
3. Click "Erc1155 Token Txns" tab
4. Look for transfers FROM CTF contract (0x4D97DCd97eC945f40cF65F87097ACe5EA0476045)
5. Copy the Token ID

## After Claiming

Check your balance:
```bash
uv run python scripts/check_balance.py
```

## Troubleshooting

**"No positions to process"**
- Bot only detects trades made via API (not UI trades)
- Use Polymarket UI to claim, or find token IDs manually

**"Transaction failed"**
- Wrong token_id or condition_id
- Market not resolved yet
- Insufficient MATIC for gas (~0.001 MATIC needed)

**"Not enough gas"**
- Need MATIC (Polygon) for transaction fees
- Transfer MATIC to your proxy address (see `POLYMARKET_PROXY_ADDRESS` in .env)

## Monitoring

Check all positions:
```bash
uv run python scripts/check_all_positions.py
```

This shows:
- All token balances
- Current prices
- Winning positions (price ≥ $0.99)

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
