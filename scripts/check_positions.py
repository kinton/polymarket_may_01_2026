"""
Check open positions and order history on Polymarket.
"""

import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds
except ImportError:
    print("Error: py-clob-client not installed")
    sys.exit(1)

# Load environment
load_dotenv()

CLOB_API_KEY = os.getenv("CLOB_API_KEY")
CLOB_API_SECRET = os.getenv("CLOB_API_SECRET")
CLOB_PASSPHRASE = os.getenv("CLOB_PASSPHRASE")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
CLOB_HOST = "https://clob.polymarket.com"
POLYGON_CHAIN_ID = 137


def main():
    """Check positions and orders."""
    print("=" * 80)
    print("POLYMARKET POSITIONS & ORDERS CHECK")
    print("=" * 80)

    # Initialize client
    creds = ApiCreds(
        api_key=CLOB_API_KEY,
        api_secret=CLOB_API_SECRET,
        api_passphrase=CLOB_PASSPHRASE,
    )

    client = ClobClient(
        host=CLOB_HOST,
        chain_id=POLYGON_CHAIN_ID,
        key=PRIVATE_KEY,
        creds=creds,
    )

    proxy_address = client.get_address()
    print(f"\n🔷 Proxy Wallet: {proxy_address}")

    # Check open orders
    print("\n" + "=" * 80)
    print("OPEN ORDERS")
    print("=" * 80)
    try:
        orders = client.get_orders()
        if orders:
            for order in orders:
                print(f"\nOrder ID: {order.get('id')}")
                print(f"  Market: {order.get('market', 'N/A')}")
                print(f"  Side: {order.get('side', 'N/A')}")
                print(f"  Size: {order.get('size', 'N/A')}")
                print(f"  Price: ${float(order.get('price', 0)):.4f}")
                print(f"  Status: {order.get('status', 'N/A')}")
        else:
            print("\n✅ No open orders")
    except Exception as e:
        print(f"❌ Error fetching orders: {e}")

    # Check positions (tokens balance)
    print("\n" + "=" * 80)
    print("POSITIONS (Token Balances)")
    print("=" * 80)
    # Note: py-clob-client doesn't have direct method for positions
    # Would need to query balance for each token ID separately
    print("ℹ️  Token positions require specific token IDs to query")
    print("ℹ️  Use Polygonscan to view all token balances:")
    print(f"   https://polygonscan.com/address/{proxy_address}#tokentxns")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
