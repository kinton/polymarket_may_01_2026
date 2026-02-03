#!/usr/bin/env python3
"""
Get token IDs from your proxy wallet by analyzing transaction logs.
"""

import os
from decimal import Decimal

from dotenv import load_dotenv
from web3 import Web3

# Polygon RPC  
POLYGON_RPC = "https://polygon-rpc.com"

# Addresses
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# ABIs
CTF_ABI = [
    {
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "id", "type": "uint256"},
        ],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]

USDC_ABI = [
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]


def main():
    load_dotenv()
    
    proxy = os.getenv("POLYMARKET_PROXY_ADDRESS")
    if not proxy:
        print("❌ POLYMARKET_PROXY_ADDRESS not found")
        return
    
    # Connect
    print("🔗 Connecting to Polygon...")
    w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))
    
    if not w3.is_connected():
        print("❌ Failed to connect")
        return
    
    print(f"✓ Connected (Chain ID: {w3.eth.chain_id})")
    
    proxy_addr = Web3.to_checksum_address(proxy)
    print(f"🔷 Proxy: {proxy_addr}")
    print()
    
    # Check USDC on proxy
    print("💵 Proxy USDC Balance:")
    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_ADDRESS), abi=USDC_ABI)
    usdc_balance_micro = usdc.functions.balanceOf(proxy_addr).call()
    usdc_balance = Decimal(usdc_balance_micro) / Decimal(1_000_000)
    print(f"   ${usdc_balance:.6f} USDC")
    print()
    
    # Check ETH/MATIC balance
    eth_balance = w3.eth.get_balance(proxy_addr)
    eth_balance_ether = Decimal(eth_balance) / Decimal(10**18)
    print(f"💎 MATIC Balance: {eth_balance_ether:.6f} MATIC")
    print()
    
    print("To find your conditional tokens:")
    print(f"  1. Check transactions: https://polygonscan.com/address/{proxy_addr}")
    print("  2. Look for 'Transfer' events from CTF contract")
    print(f"     CTF: {CTF_ADDRESS}")
    print("  3. Token IDs are in the 'id' field of Transfer events")


if __name__ == "__main__":
    main()
