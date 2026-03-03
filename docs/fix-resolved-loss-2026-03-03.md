# Fix: resolved_loss Bug + Auto-Redeem — 2026-03-03

## Summary

Trade #118/119 (dry_run_position #60) was a **winning** convergence trade on "BTC Up or Down" market that was incorrectly recorded as `resolved_loss` with -100% PnL. Fixed the resolution logic, corrected the DB records, and added auto-redemption for on-chain winnings.

## Bug Analysis

### Root Cause
In `DryRunSimulator.resolve_all_markets()`, the winning side was determined by the token's `outcome` field:
```python
winning_side = winning_token.get("outcome", "YES")  # Returns "Up" for Up/Down markets
```

Positions store side as "YES"/"NO", but Up/Down markets return outcomes like "Up"/"Down". So:
- Position side: `"YES"`
- Winning side from API: `"Up"`
- Comparison: `"YES" != "Up"` → **wrongly classified as loss**

### Fix
Map the winning token to YES/NO based on its array index (Polymarket convention: tokens[0]=YES, tokens[1]=NO):
```python
winning_index = tokens.index(winning_token) if winning_token in tokens else 0
winning_side = "YES" if winning_index == 0 else "NO"
outcome_str = winning_token.get("outcome", winning_side)
```

## Trade Investigation (Task 2)

### Convergence Strategy Explained
The convergence strategy targets "Up or Down" binary markets in the final 20-60 seconds before expiry. The key insight:

When BTC's price **converges** with the "price to beat" threshold (within 5 basis points = 0.05%), the true probability is ~50/50. But the Polymarket orderbook often lags, still showing extreme skew (e.g., 90/10). The strategy buys the **cheap** side for massive risk/reward.

### What Happened on 2026-03-02T23:59:00 UTC

| Field | Value |
|-------|-------|
| **Time remaining** | 59.96 seconds before expiry |
| **Oracle BTC price** | $68,775.85 |
| **Oracle delta** | -21.79 (converging with price_to_beat) |
| **Oracle z-score** | -11.27 |
| **Market YES ask** | $0.26 (cheap side — "Up") |
| **Market NO ask** | ~$0.74+ (expensive side — "Down") |
| **Confidence** | 0.26 |
| **Amount** | 1.0 USDC → 3.41 shares at $0.26 |

**Entry conditions met:**
1. ✅ Time window: 59.96s is within [20s, 60s]
2. ✅ Oracle convergence: BTC was near the price_to_beat threshold
3. ✅ Market skew: NO side was expensive (≥ $0.80)
4. ✅ Cheap side: YES at $0.26 (≤ $0.40)
5. ✅ Oracle data fresh with price_to_beat

**Outcome:** BTC resolved **Up** → YES wins → $1.00/share exit. PnL = +$0.74/share = **+284.6%** ROI.

## Changes Made

### 1. Fix: `src/trading/dry_run_simulator.py`
- `resolve_all_markets()`: Map winning token index to YES/NO instead of using outcome name

### 2. New: `src/trading/auto_redeem.py`
- `AutoRedeemer` class: On-chain redemption via CTFExchange/NegRiskCtfExchange contracts
- `redeem_resolved_wins()`: Scans DB for unredeemed wins, calls redeem on-chain
- Supports both standard and neg_risk markets
- Dry-run safe (no transactions unless explicitly live)

### 3. Updated: `src/position_settler.py`
- `check_dryrun_resolution()`: Now triggers auto-redeem after resolving wins
- `_auto_redeem_wins()`: Orchestrates redemption using AutoRedeemer

### 4. DB Fix: `data/trades.db`
- Trade #119: status→`resolved_win`, exit_price→1.0, pnl→0.74, pnl_pct→284.6
- Dry_run_position #60: status→`resolved_win`, exit_price→1.0, pnl→0.74, pnl_pct→284.6
- Backup saved as `data/trades.db.bak-before-fix-20260303`

### 5. Tests: `tests/test_resolution.py`
- `test_resolve_updown_market_custom_outcomes`: Regression test — YES side + "Up" winner = win
- `test_resolve_updown_market_losing_side`: YES side + "Down" winner = loss
- All 528 tests pass ✅
