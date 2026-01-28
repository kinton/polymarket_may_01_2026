"""
Test FOK order rounding logic for Polymarket precision requirements.

FOK market BUY orders require:
- maker amount (USDC): max 2 decimals
- taker amount (shares): max 4 decimals
- size × price must not exceed 2 decimals

See: https://github.com/Polymarket/py-clob-client/issues/121
"""

from decimal import ROUND_DOWN, Decimal


def round_size_for_fok(trade_size: float, price: float) -> tuple[float, float, float]:
    """
    Round size for FOK order to meet Polymarket precision requirements.

    Uses Decimal for exact arithmetic to avoid floating point errors.

    Args:
        trade_size: Amount in USDC to spend
        price: Price per token (will be rounded to 2 decimals)

    Returns:
        (rounded_price, rounded_size, actual_maker_amount)
    """
    # Convert to Decimal for exact arithmetic
    price_dec = Decimal(str(price)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    trade_size_dec = Decimal(str(trade_size))

    # Calculate max cents we can spend (round down to avoid exceeding budget)
    max_cents = (trade_size_dec * 100).to_integral_value(rounding=ROUND_DOWN)

    # Find largest size where size × price equals exactly N cents (N = integer)
    # Work backwards from max_cents to find valid combination
    for cents in range(int(max_cents), 0, -1):
        # maker_amount in dollars
        maker_dec = Decimal(cents) / 100

        # Calculate size: size = maker_amount / price
        size_dec = (maker_dec / price_dec).quantize(
            Decimal("0.0001"), rounding=ROUND_DOWN
        )

        # Verify: size × price = maker_amount (exactly, no more than 2 decimals)
        check_maker = size_dec * price_dec
        check_maker_rounded = check_maker.quantize(Decimal("0.01"))

        if check_maker == check_maker_rounded:
            # Found valid combination!
            return float(price_dec), float(size_dec), float(check_maker)

    # Fallback: should never reach here
    raise ValueError(
        f"Cannot find valid FOK size for trade_size={trade_size}, price={price}"
    )


def test_fok_rounding():
    """Test FOK rounding logic with known problematic cases."""
    test_cases = [
        # (trade_size, price, description)
        (1.0, 0.99, "Standard $1 at $0.99"),
        (5.0, 0.994, "Issue #121: 5.0 * 0.994"),
        (10.0, 0.994, "Issue #121: 10.0 * 0.994"),
        (5.1, 0.994, "Issue #121: 5.1 * 0.994 (should fail)"),
        (6.0, 0.994, "Issue #121: 6.0 * 0.994 (should fail)"),
        (1.74, 0.58, "Original issue: 1.74 * 0.58"),
        (20.10, 0.76, "Fractional: 20.10 * 0.76"),
        (5.214750, 0.76, "Fractional: 5.214750 * 0.76"),
    ]

    print("Testing FOK order rounding logic")
    print("=" * 80)

    for trade_size, price, description in test_cases:
        print(f"\n{description}")
        print(f"  Input: trade_size=${trade_size:.2f}, price=${price:.3f}")

        rounded_price, size, maker_amount = round_size_for_fok(trade_size, price)

        # Validate precision
        maker_decimals = (
            len(str(maker_amount).split(".")[-1]) if "." in str(maker_amount) else 0
        )
        size_decimals = len(str(size).split(".")[-1]) if "." in str(size) else 0

        valid = (
            round(maker_amount, 2) == maker_amount
            and maker_decimals <= 2
            and size_decimals <= 4
        )

        status = "✅ VALID" if valid else "❌ INVALID"
        print(f"  Output: price=${rounded_price:.2f}, size={size:.4f}")
        print(f"  Maker amount: ${maker_amount:.6f} ({maker_decimals} decimals)")
        print(f"  Size decimals: {size_decimals}")
        print(f"  Status: {status}")

        if not valid:
            print("  ⚠️  Would fail: maker amount has > 2 decimals!")


if __name__ == "__main__":
    test_fok_rounding()
