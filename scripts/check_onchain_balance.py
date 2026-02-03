#!/usr/bin/env python3
"""
Check conditional token balances directly on blockchain.
This bypasses CLOB API and reads directly from CTF contract.
"""

import os
from decimal import Decimal

from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3

# Polygon RPC
POLYGON_RPC = "https://polygon-rpc.com"

# Addresses
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# Minimal ABI
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
    
    private_key = os.getenv("PRIVATE_KEY")
    if not private_key:
        print("❌ PRIVATE_KEY not found")
        return
    
    # Connect
    print("🔗 Connecting to Polygon...")
    w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))
    
    if not w3.is_connected():
        print("❌ Failed to connect")
        return
    
    print(f"✓ Connected (Chain ID: {w3.eth.chain_id})")
    
    # Get wallet
    account = Account.from_key(private_key)
    wallet = Web3.to_checksum_address(account.address)
    
    print(f"👛 Wallet: {wallet}")
    print()
    
    # Check USDC
    print("💵 USDC Balance:")
    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_ADDRESS), abi=USDC_ABI)
    usdc_balance_micro = usdc.functions.balanceOf(wallet).call()
    usdc_balance = Decimal(usdc_balance_micro) / Decimal(1_000_000)
    print(f"   ${usdc_balance:.6f} USDC")
    print()
    
    # Check known token IDs from your screenshot
    # These are example token IDs - you need the actual ones from your trades
    print("🎯 Checking conditional tokens:")
    print("   (Need actual token IDs from your trades)")
    print()
    print("To get your token IDs:")
    print("  1. Go to Polymarket UI")
    print("  2. Open browser DevTools (F12)")
    print("  3. Go to Network tab")
    print("  4. Click on your position")
    print("  5. Look for API calls to see token_id")
    print()
    print("Or check transaction logs on Polygonscan:")
    print(f"  https://polygonscan.com/address/{wallet}")


if __name__ == "__main__":
    main()
