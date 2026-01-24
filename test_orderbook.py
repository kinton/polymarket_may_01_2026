"""
Test getting orderbook data via REST API
"""

from dotenv import load_dotenv
from py_clob_client import ClobClient

load_dotenv()

# Example token IDs from recent search - use the 7:45 market
TOKEN_ID_YES = (
    "45357664351820458021802837270620664608973849241629991182885575327538100438884"
)
TOKEN_ID_NO = (
    "24829516488761686269259292156186910945772910123654830804148452618893441357320"
)


def test_orderbook():
    """Test getting orderbook via REST API"""
    try:
        # Initialize client (Level 0 - no auth needed for public data)
        client = ClobClient("https://clob.polymarket.com")

        print("Testing orderbook retrieval...")
        print(f"Token ID (YES): {TOKEN_ID_YES}\n")

        # Get orderbook for YES token
        orderbook = client.get_order_book(TOKEN_ID_YES)

        print("✓ Orderbook retrieved!")
        print(f"\nOrderbook object: {orderbook}")
        print(f"Type: {type(orderbook)}")
        print(f"Attributes: {dir(orderbook)}")

        # Access attributes directly
        print("\nOrderbook data:")
        print(
            f"  Market: {orderbook.market if hasattr(orderbook, 'market') else 'N/A'}"
        )
        print(
            f"  Asset ID: {orderbook.asset_id if hasattr(orderbook, 'asset_id') else 'N/A'}"
        )
        print(
            f"  Timestamp: {orderbook.timestamp if hasattr(orderbook, 'timestamp') else 'N/A'}"
        )

        # Get bids and asks
        bids = orderbook.bids if hasattr(orderbook, "bids") else []
        asks = orderbook.asks if hasattr(orderbook, "asks") else []

        print(f"\n  Bids ({len(bids)} levels):")
        for i, bid in enumerate(bids[:5], 1):  # Show top 5
            print(f"    {i}. Price: ${float(bid.price):.4f}, Size: {bid.size}")

        print(f"\n  Asks ({len(asks)} levels):")
        for i, ask in enumerate(asks[:5], 1):  # Show top 5
            print(f"    {i}. Price: ${float(ask.price):.4f}, Size: {ask.size}")

        if asks and len(asks) > 0:
            best_ask = float(asks[0].price)
            print(f"\n✓ Best Ask: ${best_ask:.4f}")

            if best_ask > 0.50:
                print("  → YES is winning (price > 0.50)")
            else:
                print("  → NO is winning (price <= 0.50)")

    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    test_orderbook()
