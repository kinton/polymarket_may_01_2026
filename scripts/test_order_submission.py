"""
Test script to diagnose post_order 403 error.
Creates a minimal test order to identify the exact failure point.
"""

import os
import time

from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import CreateOrderOptions, OrderArgs, OrderType

load_dotenv()

print("=" * 80)
print("ORDER SUBMISSION TEST")
print("=" * 80)

# Initialize client
print("\n1. Initializing CLOB Client...")
private_key = os.getenv("PRIVATE_KEY")
chain_id = int(os.getenv("POLYGON_CHAIN_ID", "137"))
host = os.getenv("CLOB_HOST", "https://clob.polymarket.com")

client = ClobClient(host=host, key=private_key or '', chain_id=chain_id)
api_creds = client.create_or_derive_api_creds()
client.set_api_creds(api_creds)
print("   ✓ Client initialized")

# Use the NO token from ETH market (from your log)
token_id = (
    "12944723019710129167079886592973401728716444789112002605733045777647382186359"
)
print("\n2. Test Order Parameters:")
print(f"   Token ID: {token_id[:16]}...")
print("   Price: $0.99")
print("   Size: 1.0 tokens")
print("   Side: BUY")

# Create order with exact same parameters as your live run
print("\n3. Creating order...")
try:
    order_args = OrderArgs(
        token_id=token_id,
        price=0.99,
        size=1.0,
        side="BUY",
    )

    created_order = client.create_order(
        order_args,
        CreateOrderOptions(tick_size="0.01", neg_risk=False),  # type: ignore
    )
    print("   ✓ Order created successfully")
    print(f"   Order type: {type(created_order)}")

    if hasattr(created_order, "order") and hasattr(created_order.order, "values"):
        values = created_order.order.values
        print(f"   makerAmount: {values.get('makerAmount')} (should be 990000)")
        print(f"   takerAmount: {values.get('takerAmount')} (should be 1000000)")

except Exception as e:
    print(f"   ✗ Error creating order: {e}")
    exit(1)

# Try to post the order
print("\n4. Attempting to post order (FOK)...")
print("   NOTE: This will attempt a real order submission!")
print("   Waiting 3 seconds... (Ctrl+C to cancel)")

time.sleep(3)

try:
    response = client.post_order(created_order, OrderType.FOK)  # type: ignore
    print("   ✓ Order posted successfully!")
    print(f"   Response: {response}")

except Exception as e:
    error_str = str(e)
    print(f"   ✗ Error posting order: {type(e).__name__}")

    if "403" in error_str:
        print("\n   ANALYSIS: 403 Forbidden Error")

        # Check if it's Cloudflare
        if "cloudflare" in error_str.lower():
            print("   Cause: Cloudflare Bot Protection")
            print(
                "\n   This is NOT a code issue - Polymarket's Cloudflare is blocking your IP"
            )
            print("\n   SOLUTIONS:")
            print("   1. ⏰ WAIT: Rate limit cooldown (5-15 minutes)")
            print("   2. 🌐 NETWORK: Switch to mobile hotspot or VPN")
            print("   3. 📧 CONTACT: support@polymarket.com with your IP")
            print("   4. 🔄 UPDATE: uv pip install -U py-clob-client")
        else:
            print("   Possible causes:")
            print("   - Invalid API credentials")
            print("   - Insufficient permissions")
            print("   - Market closed or unavailable")

    print(f"\n   Full error:\n   {str(e)[:500]}")
    exit(1)

print("\n" + "=" * 80)
print("✓ ORDER SUBMISSION TEST PASSED!")
print("=" * 80)
