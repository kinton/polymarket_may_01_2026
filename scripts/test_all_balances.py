#!/usr/bin/env python3
"""
Test balance checking with all signature types.
"""

import os
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import AssetType, BalanceAllowanceParams
from py_clob_client.constants import POLYGON

load_dotenv()
private_key = os.getenv("PRIVATE_KEY")
proxy = os.getenv("POLYMARKET_PROXY_ADDRESS")

print("=" * 60)
print("TESTING ALL SIGNATURE TYPES")
print("=" * 60)
print()

# Test 1: EOA Wallet
print("1. EOA Wallet (signature_type=0, funder=None):")
try:
    client = ClobClient(
        host="https://clob.polymarket.com",
        key=private_key,
        chain_id=POLYGON,
        signature_type=0,
        funder=None,
    )
    client.set_api_creds(client.create_or_derive_api_creds())

    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    balance_data = client.get_balance_allowance(params)

    balance = float(balance_data.get("balance", 0)) / 1e6
    print(f"   💰 Balance: ${balance:.6f} USDC")
    print(f"   📊 Raw: {balance_data.get('balance')}")
except Exception as e:
    print(f"   ❌ Error: {e}")

print()

# Test 2: Gnosis Safe
print("2. Gnosis Safe (signature_type=1, funder=proxy):")
try:
    client = ClobClient(
        host="https://clob.polymarket.com",
        key=private_key,
        chain_id=POLYGON,
        signature_type=1,
        funder=proxy,
    )
    client.set_api_creds(client.create_or_derive_api_creds())

    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    balance_data = client.get_balance_allowance(params)

    balance = float(balance_data.get("balance", 0)) / 1e6
    print(f"   💰 Balance: ${balance:.6f} USDC")
    print(f"   📊 Raw: {balance_data.get('balance')}")
except Exception as e:
    print(f"   ❌ Error: {e}")

print()

# Test 3: Polymarket Proxy (current config)
print("3. Polymarket Proxy (signature_type=2, funder=proxy):")
try:
    client = ClobClient(
        host="https://clob.polymarket.com",
        key=private_key,
        chain_id=POLYGON,
        signature_type=2,
        funder=proxy,
    )
    client.set_api_creds(client.create_or_derive_api_creds())

    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    balance_data = client.get_balance_allowance(params)

    balance = float(balance_data.get("balance", 0)) / 1e6
    print(f"   💰 Balance: ${balance:.6f} USDC")
    print(f"   📊 Raw: {balance_data.get('balance')}")
    print(f"   📋 Full response: {balance_data}")
except Exception as e:
    print(f"   ❌ Error: {e}")

print()

# Test 4: EOA with proxy as funder
print("4. EOA with proxy funder (signature_type=0, funder=proxy):")
try:
    client = ClobClient(
        host="https://clob.polymarket.com",
        key=private_key,
        chain_id=POLYGON,
        signature_type=0,
        funder=proxy,
    )
    client.set_api_creds(client.create_or_derive_api_creds())

    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    balance_data = client.get_balance_allowance(params)

    balance = float(balance_data.get("balance", 0)) / 1e6
    print(f"   💰 Balance: ${balance:.6f} USDC")
    print(f"   📊 Raw: {balance_data.get('balance')}")
except Exception as e:
    print(f"   ❌ Error: {e}")

print()
print("=" * 60)
