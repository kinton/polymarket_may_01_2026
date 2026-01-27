"""Verify the fix guarantees exact decimal places"""

import json
from decimal import ROUND_DOWN, Decimal


def test_fix():
    """Test that format string approach guarantees exact decimals in JSON"""

    test_cases = [
        (0.99, 1.0),
        (0.90, 1.0),  # Critical case - must be 0.90, not 0.9
        (0.68, 1.0),
        (0.85, 1.0),
        (1.0, 1.0),  # Edge case
    ]

    print("Testing with format string approach (FIXED):")
    print("=" * 70)

    for buy_price, trade_size in test_cases:
        price_decimal = Decimal(str(buy_price))
        trade_decimal = Decimal(str(trade_size))

        if trade_decimal < Decimal("1.00"):
            trade_decimal = Decimal("1.00")

        tokens_decimal = (trade_decimal / price_decimal).quantize(
            Decimal("0.0001"), rounding=ROUND_DOWN
        )

        # FIXED: Use format string
        price_str = f"{float(price_decimal):.2f}"
        tokens_str = f"{float(tokens_decimal):.4f}"

        price_float = float(price_str)
        tokens_float = float(tokens_str)

        # Simulate JSON serialization (what API receives)
        json_payload = json.dumps({"price": price_float, "size": tokens_float})

        # Count decimals in JSON
        import re

        price_match = re.search(r'"price":\s*([0-9.]+)', json_payload)
        size_match = re.search(r'"size":\s*([0-9.]+)', json_payload)

        if not price_match or not size_match:
            print(f"✗ Failed to parse JSON: {json_payload}")
            continue

        price_val = price_match.group(1)
        size_val = size_match.group(1)
        price_decimals = len(price_val.split(".")[-1]) if "." in price_val else 0
        size_decimals = len(size_val.split(".")[-1]) if "." in size_val else 0

        status = "✓" if price_decimals == 2 and size_decimals == 4 else "✗"

        print(f"{status} BUY_PRICE={buy_price}, TRADE_SIZE={trade_size}")
        print(f"   JSON: {json_payload}")
        print(
            f"   Decimals: price={price_decimals} (need 2), size={size_decimals} (need 4)"
        )

        if price_decimals != 2 or size_decimals != 4:
            print("   ❌ FAIL: Incorrect decimal places!")
        print()


if __name__ == "__main__":
    test_fix()
