#!/usr/bin/env python3
"""Debug: raw API call to balance endpoint"""
import os
import httpx
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import RequestArgs, AssetType, BalanceAllowanceParams
from py_clob_client.constants import POLYGON

load_dotenv()

pk = os.getenv("PRIVATE_KEY")
px = os.getenv("POLYMARKET_PROXY_ADDRESS")

# Init client
client = ClobClient("https://clob.polymarket.com", key=pk, chain_id=POLYGON, signature_type=2, funder=px)
client.set_api_creds(client.create_or_derive_api_creds())

print("=" * 70)
print("BALANCE CHECK VIA get_balance_allowance()")
print("=" * 70)

# Use the proper method
params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
result = client.get_balance_allowance(params)

print(f"Raw API response: {result}")
print()

balance_micro = int(result.get("balance", 0))
balance_usdc = balance_micro / 1e6

print(f"Balance (micro-USDC): {balance_micro}")
print(f"Balance (USDC): ${balance_usdc:.6f}")
print()

allowances = result.get("allowances", {})
print("Allowances:")
for contract, allowance in allowances.items():
    allowance_usdc = int(allowance) / 1e6
    print(f"  {contract}: ${allowance_usdc:.2f}")

print("=" * 70)
