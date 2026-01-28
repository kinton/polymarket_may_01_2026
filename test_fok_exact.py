"""Test FOK algorithm - find sizes with EXACTLY 2-decimal maker amounts."""
from decimal import Decimal, ROUND_DOWN

def find_fok_size_exact(price: float, trade_size: float) -> tuple[Decimal, Decimal] | None:
    """Find valid FOK size where size × price has EXACTLY 2 decimals."""
    price_dec = Decimal(str(price)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    trade_size_dec = Decimal(str(trade_size))
    MIN_ORDER_SIZE = Decimal("1.00")

    max_tokens = (trade_size_dec / price_dec).quantize(Decimal("0.01"))
    max_cents_tokens = int(max_tokens * 100)

    for cents_tokens in range(max_cents_tokens, 99, -1):
        candidate_size = Decimal(cents_tokens) / 100
        candidate_maker = candidate_size * price_dec

        # Check if maker has EXACTLY 2 decimals
        candidate_maker_rounded = candidate_maker.quantize(Decimal("0.01"))

        if candidate_maker == candidate_maker_rounded and candidate_maker >= MIN_ORDER_SIZE:
            return (candidate_size, candidate_maker)

    return None


print("✅ FOK Exact Precision Tests")
print("=" * 70)

# Test 1: $0.99 price, $1.01 budget
print("\nTest 1: price=$0.99, trade_size=$1.01")
result = find_fok_size_exact(0.99, 1.01)
if result:
    size, maker = result
    print(f"  ✅ Found: {size} tokens × $0.99 = ${maker}")
    print(f"     Decimals check: {maker.as_tuple().exponent} == -2? {maker.as_tuple().exponent == -2}")
else:
    print("  ❌ NOT FOUND")

# Test 2: $0.50 price, $1.01 budget
print("\nTest 2: price=$0.50, trade_size=$1.01")
result = find_fok_size_exact(0.50, 1.01)
if result:
    size, maker = result
    print(f"  ✅ Found: {size} tokens × $0.50 = ${maker}")
else:
    print("  ❌ NOT FOUND")

# Test 3: $1.00 price, $1.01 budget
print("\nTest 3: price=$1.00, trade_size=$1.01")
result = find_fok_size_exact(1.00, 1.01)
if result:
    size, maker = result
    print(f"  ✅ Found: {size} tokens × $1.00 = ${maker}")
else:
    print("  ❌ NOT FOUND")

print("\n" + "=" * 70)
