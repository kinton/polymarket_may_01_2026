"""Find minimum budget needed for $0.99 price."""
from decimal import Decimal

price = Decimal("0.99")
MIN_ORDER_SIZE = Decimal("1.00")

print("Searching for valid sizes at $0.99 price:")
print("=" * 60)

for cents_tokens in range(500, 99, -1):
    candidate_size = Decimal(cents_tokens) / 100
    candidate_maker = candidate_size * price
    candidate_maker_rounded = candidate_maker.quantize(Decimal("0.01"))

    if candidate_maker == candidate_maker_rounded and candidate_maker >= MIN_ORDER_SIZE:
        budget_needed = candidate_size * price
        print(f"✅ Size: {candidate_size} tokens × $0.99 = ${candidate_maker}")
        print(f"   Budget needed: ${budget_needed}")
        break
else:
    print("❌ No valid size found")
