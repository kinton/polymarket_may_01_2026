#!/usr/bin/env python3
"""Test get_balance_allowance with different parameters"""
import os
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import AssetType, BalanceAllowanceParams
from py_clob_client.constants import POLYGON

load_dotenv()

pk = os.getenv("PRIVATE_KEY")
px = os.getenv("POLYMARKET_PROXY_ADDRESS")

client = ClobClient("https://clob.polymarket.com", key=pk, chain_id=POLYGON, signature_type=2, funder=px)
client.set_api_creds(client.create_or_derive_api_creds())

print("=" * 70)
print("Testing get_balance_allowance() with different params")
print("=" * 70)
print()

# Test 1: No params
print("1. get_balance_allowance(params=None):")
try:
    result = client.get_balance_allowance(params=None)
    print(f"   Result: {result}")
except Exception as e:
    print(f"   Error: {e}")

print()

# Test 2: Empty params
print("2. get_balance_allowance(params=BalanceAllowanceParams()):")
try:
    result = client.get_balance_allowance(params=BalanceAllowanceParams())
    print(f"   Result: {result}")
except Exception as e:
    print(f"   Error: {e}")

print()

# Test 3: COLLATERAL (current method)
print("3. get_balance_allowance(AssetType.COLLATERAL):")
try:
    result = client.get_balance_allowance(params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    balance_usdc = float(result.get("balance", 0)) / 1e6
    print(f"   Balance: ${balance_usdc:.6f} USDC")
    print(f"   Full result: {result}")
except Exception as e:
    print(f"   Error: {e}")

print()
print("=" * 70)
