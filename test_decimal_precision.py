"""Test to verify exact decimal precision for API"""

from decimal import ROUND_DOWN, Decimal


def test_precision():
    """Test various price/size combinations to ensure correct decimal places"""

    test_cases = [
        (0.99, 1.0),  # Standard case
        (0.90, 1.0),  # BTC case from logs
        (0.68, 1.0),  # ETH case from logs
        (0.85, 1.0),  # Another case
        (0.99, 5.0),  # Larger trade
    ]

    for buy_price, trade_size in test_cases:
        print(f"\n{'=' * 60}")
        print(f"Testing: BUY_PRICE={buy_price}, TRADE_SIZE={trade_size}")
        print("=" * 60)

        price_decimal = Decimal(str(buy_price))
        trade_decimal = Decimal(str(trade_size))

        if trade_decimal < Decimal("1.00"):
            trade_decimal = Decimal("1.00")

        # Calculate tokens, round to 4 decimals
        tokens_decimal = (trade_decimal / price_decimal).quantize(
            Decimal("0.0001"), rounding=ROUND_DOWN
        )

        # Calculate maker_amount, round to 2 decimals
        maker_decimal = (price_decimal * tokens_decimal).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN
        )

        # Method 1: round() - what we're using now
        price_float_round = round(float(price_decimal), 2)
        tokens_float_round = round(float(tokens_decimal), 4)

        # Method 2: format string - guaranteed exact decimals
        price_str = f"{float(price_decimal):.2f}"
        tokens_str = f"{float(tokens_decimal):.4f}"

        print("\nDecimal calculations:")
        print(f"  price_decimal:  {price_decimal}")
        print(f"  tokens_decimal: {tokens_decimal}")
        print(f"  maker_decimal:  {maker_decimal}")

        print("\nMethod 1 - round():")
        print(
            f"  price_float:  {price_float_round} (type: {type(price_float_round).__name__})"
        )
        print(
            f"  tokens_float: {tokens_float_round} (type: {type(tokens_float_round).__name__})"
        )
        print(
            f"  repr: price={repr(price_float_round)}, tokens={repr(tokens_float_round)}"
        )

        print("\nMethod 2 - format string:")
        print(f"  price_str:  {price_str} (type: {type(price_str).__name__})")
        print(f"  tokens_str: {tokens_str} (type: {type(tokens_str).__name__})")

        # Check if conversion back shows correct decimals
        print("\nVerification (as they'd appear in JSON):")
        import json

        json_round = json.dumps(
            {"price": price_float_round, "size": tokens_float_round}
        )
        json_str = json.dumps({"price": float(price_str), "size": float(tokens_str)})
        print(f"  round() method: {json_round}")
        print(f"  string method: {json_str}")

        # Count actual decimal places in JSON
        import re

        price_match = re.search(r'"price":\s*([0-9.]+)', json_round)
        size_match = re.search(r'"size":\s*([0-9.]+)', json_round)
        if price_match:
            price_val = price_match.group(1)
            price_decimals = len(price_val.split(".")[-1]) if "." in price_val else 0
            print(f"  price decimals in JSON: {price_decimals}")
        if size_match:
            size_val = size_match.group(1)
            size_decimals = len(size_val.split(".")[-1]) if "." in size_val else 0
            print(f"  size decimals in JSON: {size_decimals}")


if __name__ == "__main__":
    test_precision()
