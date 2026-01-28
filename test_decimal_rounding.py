"""Verify decimal rounding for FOK orders."""
from decimal import Decimal, ROUND_HALF_UP

# Симуляция: 1.02 токена × $0.99
size = Decimal("1.02")
price = Decimal("0.99")
maker = size * price

print(f"1.02 × $0.99 = ${maker}")
print(f"Decimals: {maker.as_tuple().exponent}")

# Что делает quantize?
maker_rounded_default = maker.quantize(Decimal("0.01"))  # default ROUND_HALF_EVEN
print(f"После quantize (default ROUND_HALF_EVEN): ${maker_rounded_default}")

maker_rounded_up = maker.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
print(f"После quantize (ROUND_HALF_UP): ${maker_rounded_up}")

# Проверка: равны ли они?
print(f"\n${maker} == ${maker_rounded_default}? {maker == maker_rounded_default}")
print(f"${maker} == ${maker_rounded_up}? {maker == maker_rounded_up}")

# Проверка валидности для API
print("\n✅ Polymarket требует: maker_amount имеет ровно 2 decimals")
print(f"   ${maker_rounded_default} - 2 decimals? {maker_rounded_default.as_tuple().exponent == -2}")
print(f"   ${maker_rounded_default} >= $1.00? {maker_rounded_default >= Decimal('1.00')}")
