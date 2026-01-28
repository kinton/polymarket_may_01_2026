"""Test various prices to find ones compatible with $1.01 budget."""
from decimal import Decimal

budget = Decimal("1.01")
MIN_ORDER_SIZE = Decimal("1.00")

print("Testing prices to find which work with $1.01 budget:")
print("=" * 70)

# Test common Polymarket prices
test_prices = [0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.99, 1.00]

for price_float in test_prices:
    price = Decimal(str(price_float))
    found = False
    
    max_tokens = (budget / price).quantize(Decimal("0.01"))
    max_cents = int(max_tokens * 100)
    
    for cents in range(max_cents, 99, -1):
        candidate_size = Decimal(cents) / 100
        candidate_maker = candidate_size * price
        candidate_maker_rounded = candidate_maker.quantize(Decimal("0.01"))
        
        if candidate_maker == candidate_maker_rounded and candidate_maker >= MIN_ORDER_SIZE:
            print(f"✅ Price ${price_float}: {candidate_size} tokens × ${price} = ${candidate_maker}")
            found = True
            break
    
    if not found:
        print(f"❌ Price ${price_float}: NO VALID SIZE FOUND")

print("=" * 70)
