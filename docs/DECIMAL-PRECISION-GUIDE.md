# Decimal Precision Guide: Fixing "maker amount supports max accuracy of 2 decimals"

## Problem
You're getting an API error: **"maker amount supports a max accuracy of 2 decimals, taker amount a max of 4 decimals"**

This means the amounts being sent to Polymarket have too many decimal places.

## Root Cause
The `py-clob-client` library uses a **two-step process** for decimal handling:

1. **Price/Size Rounding** → Based on `tick_size` precision (0.01, 0.001, etc.)
2. **Token Decimals Conversion** → Scale to 6 decimals via `to_token_decimals()`

The issue occurs when the **raw price/amount calculation** has too many decimal places **BEFORE** being converted to token decimals.

## Solution: Use ROUNDING_CONFIG

The `py-clob-client` library has a built-in `ROUNDING_CONFIG` that defines the correct decimal precision for each tick size:

```python
ROUNDING_CONFIG = {
    "0.1": RoundConfig(price=1, size=2, amount=3),
    "0.01": RoundConfig(price=2, size=2, amount=4),
    "0.001": RoundConfig(price=3, size=2, amount=5),
    "0.0001": RoundConfig(price=4, size=2, amount=6),
}
```

**Key meanings:**
- **price**: Decimal places to round price to
- **size**: Decimal places to round size to  
- **amount**: Decimal places for the raw maker/taker amounts (BEFORE token scaling)

## How the OrderBuilder Works

### Step 1: Round price and size
```python
raw_price = round_normal(price, round_config.price)  # Round to tick precision
raw_taker_amt = round_down(size, round_config.size)   # Round size down
```

### Step 2: Calculate raw amounts
```python
raw_maker_amt = raw_taker_amt * raw_price
```

### Step 3: CHECK DECIMAL PLACES (Critical!)
```python
if decimal_places(raw_maker_amt) > round_config.amount:
    # If decimals exceed limit, round up then check again
    raw_maker_amt = round_up(raw_maker_amt, round_config.amount + 4)
    if decimal_places(raw_maker_amt) > round_config.amount:
        # Still too many decimals? Round down to the limit
        raw_maker_amt = round_down(raw_maker_amt, round_config.amount)
```

### Step 4: Convert to token decimals
```python
maker_amount = to_token_decimals(raw_maker_amt)  # Multiplies by 10^6
taker_amount = to_token_decimals(raw_taker_amt)
```

The `to_token_decimals()` function:
```python
def to_token_decimals(x: float) -> int:
    f = (10**6) * x
    if decimal_places(f) > 0:
        f = round_normal(f, 0)
    return int(f)
```

## Examples from Tests

### Example 1: tick_size="0.01", price=0.56, size=21.04 (BUY)
```
RoundConfig(price=2, size=2, amount=4)

Step 1: Round
  raw_price = 0.56
  raw_taker_amt = 21.04

Step 2: Calculate
  raw_maker_amt = 21.04 * 0.56 = 11.7824

Step 3: Check decimals
  decimal_places(11.7824) = 4  ✓ Equals amount=4, OK

Step 4: Convert
  maker_amount = to_token_decimals(11.7824) = 11782400
  taker_amount = to_token_decimals(21.04) = 21040000

Result: makerAmount=11782400, takerAmount=21040000 ✓
```

### Example 2: tick_size="0.0001", price=0.0056, size=21.04 (BUY)
```
RoundConfig(price=4, size=2, amount=6)

Step 1: Round
  raw_price = 0.0056
  raw_taker_amt = 21.04

Step 2: Calculate
  raw_maker_amt = 21.04 * 0.0056 = 0.117824

Step 3: Check decimals
  decimal_places(0.117824) = 6  ✓ Equals amount=6, OK

Step 4: Convert
  maker_amount = to_token_decimals(0.117824) = 117824
  taker_amount = to_token_decimals(21.04) = 21040000

Result: makerAmount=117824, takerAmount=21040000 ✓
```

## How to Fix Your Order Creation

Make sure you're passing the correct `tick_size` to `CreateOrderOptions`:

```python
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, PartialCreateOrderOptions

client = ClobClient(...)

# Example: Bitcoin 5-minute market with 0.01 tick size
order_args = OrderArgs(
    token_id="YOUR_TOKEN_ID",
    price=0.55,
    size=100,
    side=BUY,
    nonce=0,
    expiration=0,
)

# MUST provide tick_size that matches the market!
signed_order = client.builder.create_order(
    order_args,
    options=CreateOrderOptions(
        tick_size="0.01",    # ← This determines precision!
        neg_risk=False,
    ),
)
```

## Available Tick Sizes & Precision Limits

| Tick Size | Price DP | Size DP | Amount DP | Use Case |
|-----------|----------|---------|-----------|----------|
| 0.1       | 1        | 2       | 3         | Low precision markets |
| 0.01      | 2        | 2       | 4         | **Standard Bitcoin/Ethereum** |
| 0.001     | 3        | 2       | 5         | Medium precision |
| 0.0001    | 4        | 2       | 6         | High precision markets |

## Troubleshooting

### Error: "maker amount supports a max accuracy of 2 decimals"
- **Cause**: Raw maker amount has > 4 decimal places (when tick="0.01")
- **Fix**: Ensure you're passing the correct `tick_size` to `CreateOrderOptions`
- **Check**: Print the ROUNDING_CONFIG for your tick size

### Error: "taker amount a max of 4 decimals"  
- **Cause**: Raw taker amount has too many decimal places
- **Fix**: Same as above - verify `tick_size` parameter

### Values appear correct but still fail
- **Cause**: Floating point precision issues
- **Fix**: The OrderBuilder already handles this with `decimal_places()` and rounding logic
- **Debug**: Log the `raw_maker_amt` and `raw_taker_amt` before `to_token_decimals()`

## Key Helper Functions

```python
from py_clob_client.order_builder.helpers import (
    to_token_decimals,      # Multiply by 10^6
    round_down,             # Floor to N decimal places
    round_normal,           # Round to N decimal places
    round_up,               # Ceiling to N decimal places
    decimal_places,         # Count decimal places
)
```

## Don't Do This

❌ **Wrong**: Manually calculating amounts without rounding
```python
# BAD - Creates precision issues!
maker_amount = int(price * size * 1e6)
taker_amount = int(size * 1e6)
```

❌ **Wrong**: Using wrong tick_size
```python
# BAD - tick_size doesn't match market!
create_order(..., options=CreateOrderOptions(tick_size="0.1", ...))
```

❌ **Wrong**: Passing Decimal objects directly
```python
# BAD - The API expects integers after scaling!
from decimal import Decimal
order.makerAmount = Decimal("11.7824")  # Wrong format!
```

## Do This

✅ **Correct**: Use OrderBuilder with proper tick_size
```python
order = builder.create_order(
    order_args=OrderArgs(price=0.55, size=100, ...),
    options=CreateOrderOptions(tick_size="0.01", neg_risk=False)
)
# OrderBuilder handles ALL rounding and scaling automatically
```

✅ **Correct**: If doing manual calculations, use helper functions
```python
from py_clob_client.order_builder.builder import ROUNDING_CONFIG
from py_clob_client.order_builder.helpers import (
    round_normal, round_down, to_token_decimals, decimal_places
)

config = ROUNDING_CONFIG["0.01"]
raw_price = round_normal(price, config.price)
raw_size = round_down(size, config.size)
raw_amount = raw_size * raw_price

# Check precision before converting
if decimal_places(raw_amount) > config.amount:
    raw_amount = round_down(raw_amount, config.amount)

maker_amount = to_token_decimals(raw_amount)
```

## Reference

- **Library**: `py-clob-client`
- **Main file**: `py_clob_client/order_builder/builder.py`
- **Helpers**: `py_clob_client/order_builder/helpers.py`
- **Tests**: `tests/order_builder/test_builder.py` (extensive decimal precision tests)

The tests in the repository are excellent reference for all tick sizes and edge cases!
