#!/usr/bin/env python3
"""
Check balances on both EOA and Proxy wallets
"""
import os
from dotenv import load_dotenv
from eth_account import Account

load_dotenv()

private_key = os.getenv("PRIVATE_KEY")
proxy_address = os.getenv("POLYMARKET_PROXY_ADDRESS")

# Get EOA address from private key
account = Account.from_key(private_key)
eoa_address = account.address

print("=" * 70)
print("WALLET ADDRESSES")
print("=" * 70)
print(f"🔑 EOA (Main Wallet):  {eoa_address}")
print(f"🔷 Proxy Wallet:       {proxy_address}")
print()
print("Check these addresses on Polygonscan:")
print(f"  EOA:   https://polygonscan.com/address/{eoa_address}")
print(f"  Proxy: https://polygonscan.com/address/{proxy_address}")
print()
print("If money is on EOA, you need to:")
print("1. Go to Polymarket UI")
print("2. Deposit USDC from EOA to Polymarket")
print("3. Or change .env to use correct proxy address")
print("=" * 70)
