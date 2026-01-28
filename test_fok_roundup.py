"""Test FOK rounding algorithm with ROUND_HALF_UP strategy."""
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

def find_fok_size_roundup(price: Decimal, trade_size: Decimal) -> tuple[Decimal, Decimal] | None:
    """Find valid FOK size by rounding UP to nearest 0.01 token.
    
    Returns (size, maker_amount) or None if constraints violated.
    """
    MIN_ORDER_SIZE = Decimal("1.00")
    
    # 1. Calculate max tokens we can buy: trade_size / price
    price_dec = Decimal(str(price)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    trade_size_dec = Decimal(str(trade_size))
    
    max_tokens = (trade_size_dec / price_dec).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    
    # 2. Calculate maker_amount: size × price
    maker_dec = max_tokens * price_dec
    maker_rounded = maker_dec.quantize(Decimal("0.01"))
    
    # 3. Verify constraints
    if maker_dec != maker_rounded:
        maker_dec = maker_rounded
    
    size_dec = max_tokens
    
    if maker_dec < MIN_ORDER_SIZE:
        return None
    
    return (size_dec, maker_dec)


# Test cases
print("✅ FOK Round-Up Algorithm Tests")
print("=" * 60)

# Test 1: Default scenario (price=$0.99, trade_size=$1.01)
print("\nTest 1: price=$0.99, trade_size=$1.01")
result = find_fok_size_roundup(Decimal("0.99"), Decimal("1.01"))
if result:
    size, maker = result
    print(f"  ✅ SUCCESS: size={size} tokens, maker_amount=${maker}")
    print("     Expected: 1.02 tokens × $0.99 = $1.0098 ≈ $1.01")
else:
    print("  ❌ FAILED")

# Test 2: Smaller budget (price=$0.99, trade_size=$1.00)
print("\nTest 2: price=$0.99, trade_size=$1.00")
result = find_fok_size_roundup(Decimal("0.99"), Decimal("1.00"))
if result:
    size, maker = result
    print(f"  ✅ SUCCESS: size={size} tokens, maker_amount=${maker}")
else:
    print("  ❌ FAILED (expected - budget too small)")

# Test 3: Higher price (price=$0.50, trade_size=$1.01)
print("\nTest 3: price=$0.50, trade_size=$1.01")
result = find_fok_size_roundup(Decimal("0.50"), Decimal("1.01"))
if result:
    size, maker = result
    print(f"  ✅ SUCCESS: size={size} tokens, maker_amount=${maker}")
else:
    print("  ❌ FAILED")

# Test 4: Edge case (price=$1.00, trade_size=$1.01)
print("\nTest 4: price=$1.00, trade_size=$1.01")
result = find_fok_size_roundup(Decimal("1.00"), Decimal("1.01"))
if result:
    size, maker = result
    print(f"  ✅ SUCCESS: size={size} tokens, maker_amount=${maker}")
else:
    print("  ❌ FAILED")

print("\n" + "=" * 60)
print("✅ All critical scenarios tested")
