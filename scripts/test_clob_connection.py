"""
Diagnostic script to test CLOB client connection and API credentials.
Run this to diagnose 403 Cloudflare errors.
"""

import os

from dotenv import load_dotenv
from py_clob_client.client import ClobClient

load_dotenv()

print("=" * 80)
print("CLOB CLIENT DIAGNOSTIC TEST")
print("=" * 80)

# Check environment variables
print("\n1. Environment Variables:")
private_key = os.getenv("PRIVATE_KEY")
chain_id = os.getenv("POLYGON_CHAIN_ID", "137")
host = os.getenv("CLOB_HOST", "https://clob.polymarket.com")

print(
    f"   PRIVATE_KEY: {'✓ Set' if private_key else '✗ Missing'} ({len(private_key) if private_key else 0} chars)"
)
print(f"   POLYGON_CHAIN_ID: {chain_id}")
print(f"   CLOB_HOST: {host}")

if not private_key:
    print("\n✗ ERROR: PRIVATE_KEY not found in .env")
    exit(1)

# Initialize client
print("\n2. Initializing CLOB Client...")
try:
    client = ClobClient(
        host=host,
        key=private_key,
        chain_id=int(chain_id),
    )
    print("   ✓ Client created")
except Exception as e:
    print(f"   ✗ Error creating client: {e}")
    exit(1)

# Set API credentials
print("\n3. Deriving API Credentials...")
try:
    api_creds = client.create_or_derive_api_creds()
    client.set_api_creds(api_creds)
    print(f"   API Key: {api_creds.api_key[:10]}...{api_creds.api_key[-4:]}")
    print(f"   API Secret: {api_creds.api_secret[:10]}...{api_creds.api_secret[-4:]}")
    print(
        f"   API Passphrase: {api_creds.api_passphrase[:4]}...{api_creds.api_passphrase[-4:]}"
    )
    print("   ✓ Credentials set")
except Exception as e:
    print(f"   ✗ Error setting credentials: {e}")
    exit(1)

# Test API connection - get server time
print("\n4. Testing API Connection (get_server_time)...")
try:
    server_time = client.get_server_time()
    print(f"   ✓ Server responded: {server_time}")
    print("   ✓ API connection working!")
except Exception as e:
    error_str = str(e)
    if "403" in error_str:
        print("   ✗ 403 FORBIDDEN - Cloudflare blocked the request")
        print("\n   POSSIBLE CAUSES:")
        print("   1. Rate limiting - too many requests from your IP")
        print("   2. Cloudflare bot protection triggered")
        print("   3. Geographic restrictions or VPN detection")
        print("\n   TRY:")
        print("   - Wait 5-10 minutes before retrying")
        print("   - Try from different network (mobile hotspot, different WiFi)")
        print("   - Check Polymarket status: https://status.polymarket.com")
    else:
        print(f"   ✗ Error: {e}")
    exit(1)

# Test balance check
print("\n5. Testing Balance Check...")
try:
    from py_clob_client.clob_types import AssetType, BalanceAllowanceParams
    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)  # type: ignore
    balance_info_raw = client.get_balance_allowance(params)
    balance_info: dict = balance_info_raw  # type: ignore
    print("   ✓ Balance retrieved")
    if balance_info:
        balance = float(balance_info.get('balance', 0)) / 1e6  # Convert from micro-USDC
        print(f"   Available balance: ${balance:.2f}")
    print("   ✓ All API tests passed!")
except Exception as e:
    print(f"   ✗ Error getting balance: {e}")
    exit(1)

print("\n" + "=" * 80)
print("✓ ALL TESTS PASSED - CLOB client is working correctly!")
print("=" * 80)
