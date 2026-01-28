"""Verify FOK algorithm now works with updated trade_size=$2.00."""
from decimal import Decimal, ROUND_DOWN

def find_fok_size(price: Decimal, trade_size: Decimal) -> tuple[Decimal, Decimal] | None:
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

# Final test: New defaults (price=$0.99, trade_size=$2.00)
print("✅ Final Configuration Test")
print("price=$0.99, trade_size=$2.00")
result = find_fok_size(Decimal("0.99"), Decimal("2.00"))
if result:
    size, maker = result
    print(f"  ✅ SUCCESS: size={size} tokens, maker_amount=${maker}")
    print("  Ready for live trading!")
else:
    print("  ❌ FAILED")
