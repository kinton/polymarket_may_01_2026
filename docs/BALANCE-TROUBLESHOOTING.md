# Balance Troubleshooting Guide

## Problem: "not enough balance / allowance" Error

When you see this error but think you have enough money in Polymarket, here's what's happening:

### Understanding Polymarket Balances

Polymarket has **TWO types** of balances:

1. **Free USDC (Collateral)** 
   - Available for trading
   - Shown by `get_balance_allowance(AssetType.COLLATERAL)`
   - This is what our bot checks

2. **Locked in Positions (Conditional Tokens)**
   - USDC converted to YES/NO shares
   - Shows as "value" in Polymarket UI
   - **NOT available** for new trades until you sell!

### Example

You see **$6.65** in Polymarket UI:
- **$0.55** = Free USDC (available)
- **$6.10** = Locked in open positions (not available)

**Total portfolio = $6.65, but only $0.55 can be used for new trades!**

## Solution 1: Check Your Balances

```bash
# Check free USDC
uv run python scripts/check_balance.py

# Check ALL positions (free + locked)
uv run python scripts/check_all_positions.py
```

## Solution 2: Sell Open Positions

If you have money locked in positions and want to free it up:

```bash
# Check what positions you have
uv run python scripts/check_all_positions.py

# Sell all positions (dry run first!)
uv run python src/position_settler.py

# Sell for real
uv run python src/position_settler.py --live
```

## Solution 3: Deposit More USDC

If you need more trading capital:

1. Go to [Polymarket](https://polymarket.com)
2. Click "Deposit"
3. Transfer USDC from your wallet (min $2-10)
4. Wait for blockchain confirmation (~30 seconds)
5. Run: `uv run python scripts/check_balance.py`

## Understanding the Error

```
❌ Order failed: PolyApiException[status_code=400, 
   error_message={'error': 'not enough balance / allowance'}]
```

This means:
- ✅ Allowance is OK (we checked this)
- ❌ Free USDC balance < trade size
- 💡 **Check if money is locked in positions!**

## Quick Diagnostic

```bash
# 1. Check free USDC
uv run python scripts/check_balance.py
# Expected: $2+ for trading

# 2. Check locked positions
uv run python scripts/check_all_positions.py
# Shows: balance in positions + total value

# 3. If locked > free, sell positions
uv run python src/position_settler.py --live
```

## Prevention

Before running the bot:
1. **Always check free USDC**: `uv run python scripts/check_balance.py`
2. **Sell old positions**: Close winning/losing positions to free up capital
3. **Monitor positions**: After each trade, check if you want to hold or sell

## Technical Details

- USDC has 6 decimals (1 USDC = 1,000,000 micro-USDC)
- API returns balances in micro-USDC
- Code converts: `balance / 1e6` to get dollars
- `AssetType.COLLATERAL` = free USDC
- `AssetType.CONDITIONAL` = position tokens (YES/NO shares)
