"""Test FOK rounding algorithm with $1 minimum constraint."""
from decimal import Decimal, ROUND_DOWN

def find_fok_size(price: Decimal, trade_size: Decimal) -> tuple[Decimal, Decimal] | None:
    """Find valid FOK size that meets precision and minimum constraints.
    
    Returns (size, maker_amount) or None if no valid combination found.
    """
    MIN_ORDER_SIZE = Decimal("1.00")
    max_cents = (trade_size * 100).to_integral_value(rounding=ROUND_DOWN)
    
    for cents in range(int(max_cents), 99, -1):
        candidate_maker = Decimal(cents) / 100
        
        if candidate_maker < MIN_ORDER_SIZE:
            continue
            
        candidate_size = (candidate_maker / price).quantize(
            Decimal("0.0001"), rounding=ROUND_DOWN
        )
        check_maker = candidate_size * price
        check_maker_rounded = check_maker.quantize(Decimal("0.01"))
        
        if check_maker == check_maker_rounded:
            return (candidate_size, check_maker)
    
    return None


# Test 1: Current scenario (BUY_PRICE=$0.99, trade_size=$1.01)
print("Test 1: price=$0.99, trade_size=$1.01")
result = find_fok_size(Decimal("0.99"), Decimal("1.01"))
if result:
    print(f"  ✅ Found: size={result[0]}, maker_amount=${result[1]}")
else:
    print("  ❌ NOT FOUND")

# Test 2: Increase trade_size to $2.00
print("\nTest 2: price=$0.99, trade_size=$2.00")
result = find_fok_size(Decimal("0.99"), Decimal("2.00"))
if result:
    print(f"  ✅ Found: size={result[0]}, maker_amount=${result[1]}")
else:
    print("  ❌ NOT FOUND")

# Test 3: Change BUY_PRICE to $1.00
print("\nTest 3: price=$1.00, trade_size=$1.01")
result = find_fok_size(Decimal("1.00"), Decimal("1.01"))
if result:
    print(f"  ✅ Found: size={result[0]}, maker_amount=${result[1]}")
else:
    print("  ❌ NOT FOUND")

# Test 4: BUY_PRICE $0.50 (half)
print("\nTest 4: price=$0.50, trade_size=$1.01")
result = find_fok_size(Decimal("0.50"), Decimal("1.01"))
if result:
    print(f"  ✅ Found: size={result[0]}, maker_amount=${result[1]}")
else:
    print("  ❌ NOT FOUND")
