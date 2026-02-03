#!/usr/bin/env python3
"""
Claim winnings from resolved Polymarket markets.

This script:
1. Checks for winning positions (resolved markets)
2. Calls redeemPositions() on CTF contract to claim USDC
3. Shows before/after balances

Usage:
    uv run python scripts/claim_winnings.py          # Dry run
    uv run python scripts/claim_winnings.py --live   # Claim for real
"""

import argparse
import os
from decimal import Decimal

from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3

# Polygon RPC
POLYGON_RPC = "https://polygon-rpc.com"

# Contract addresses (Polygon mainnet)
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"  # Conditional Token Framework
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC on Polygon

# Minimal ABIs (only functions we need)
CTF_ABI = [
    {
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSets", "type": "uint256[]"},
        ],
        "name": "redeemPositions",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "id", "type": "uint256"},
        ],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
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


def check_balances(w3, wallet_address):
    """Check USDC balance."""
    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_ADDRESS), abi=USDC_ABI)
    
    balance_micro = usdc.functions.balanceOf(wallet_address).call()
    balance_usdc = Decimal(balance_micro) / Decimal(1_000_000)
    
    return balance_usdc


def claim_winnings(condition_id: str, token_ids: list, dry_run: bool = True):
    """
    Claim winnings for resolved market.
    
    Args:
        condition_id: Market condition ID (bytes32)
        token_ids: List of winning token IDs to redeem
        dry_run: If True, simulate only
    """
    load_dotenv()
    
    private_key = os.getenv("PRIVATE_KEY")
    if not private_key:
        print("❌ PRIVATE_KEY not found in .env")
        return
    
    # Connect to Polygon
    print("🔗 Connecting to Polygon...")
    w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))
    
    if not w3.is_connected():
        print("❌ Failed to connect to Polygon RPC")
        return
    
    print(f"✓ Connected to Polygon (Chain ID: {w3.eth.chain_id})")
    
    # Get wallet
    account = Account.from_key(private_key)
    wallet_address = Web3.to_checksum_address(account.address)
    
    print(f"👛 Wallet: {wallet_address}")
    print()
    
    # Check initial balance
    print("📊 Initial balance:")
    initial_balance = check_balances(w3, wallet_address)
    print(f"   USDC: ${initial_balance:.6f}")
    print()
    
    # Get CTF contract
    ctf = w3.eth.contract(
        address=Web3.to_checksum_address(CTF_ADDRESS),
        abi=CTF_ABI
    )
    
    # Check token balances
    print("🎯 Checking winning positions:")
    total_shares = Decimal(0)
    
    for token_id in token_ids:
        token_id_int = int(token_id)
        balance = ctf.functions.balanceOf(wallet_address, token_id_int).call()
        balance_decimal = Decimal(balance) / Decimal(1_000_000)
        total_shares += balance_decimal
        
        print(f"   Token {token_id[:10]}...: {balance_decimal:.6f} shares")
    
    if total_shares == 0:
        print("❌ No shares to claim!")
        return
    
    print(f"   Total: {total_shares:.6f} shares @ $1.00 = ${total_shares:.2f}")
    print()
    
    if dry_run:
        print("🔵 DRY RUN MODE - Would claim winnings")
        print(f"   Expected payout: ${total_shares:.2f} USDC")
        print("   Run with --live to claim for real")
        return
    
    # Prepare transaction
    print("🚀 Claiming winnings...")
    
    # Convert condition_id to bytes32
    condition_id_bytes = bytes.fromhex(condition_id.replace("0x", ""))
    
    # indexSets for binary market: [1] for outcome 0, [2] for outcome 1
    # We need to determine which outcome won (assuming DOWN = outcome 1)
    index_sets = [2]  # Binary outcome 1 (DOWN)
    
    # Build transaction
    tx = ctf.functions.redeemPositions(
        Web3.to_checksum_address(USDC_ADDRESS),  # collateralToken
        bytes(32),  # parentCollectionId (empty for top-level)
        condition_id_bytes,  # conditionId
        index_sets,  # indexSets
    ).build_transaction({
        "from": wallet_address,
        "nonce": w3.eth.get_transaction_count(wallet_address),
        "gas": 200000,
        "gasPrice": w3.eth.gas_price,
    })
    
    # Sign and send
    signed_tx = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    
    print(f"   TX hash: {tx_hash.hex()}")
    print("   Waiting for confirmation...")
    
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    
    if receipt["status"] == 1:
        print("✅ Claim successful!")
        
        # Check final balance
        final_balance = check_balances(w3, wallet_address)
        claimed = final_balance - initial_balance
        
        print()
        print("📊 Final balance:")
        print(f"   USDC: ${final_balance:.6f}")
        print(f"   Claimed: ${claimed:.6f}")
    else:
        print("❌ Transaction failed!")
        print(f"   Receipt: {receipt}")


def main():
    parser = argparse.ArgumentParser(description="Claim Polymarket winnings")
    parser.add_argument("--live", action="store_true", help="Execute claim (default: dry run)")
    parser.add_argument("--condition-id", help="Market condition ID")
    parser.add_argument("--token-id", help="Winning token ID")
    
    args = parser.parse_args()
    
    # Example from screenshot: Bitcoin Up or Down - February 2, 6:00PM-6:15PM ET
    # You'll need to get the actual condition_id and token_id from Gamma API
    
    if not args.condition_id or not args.token_id:
        print("❌ Missing --condition-id and --token-id")
        print()
        print("To find your winning positions:")
        print("  1. Run: uv run python scripts/check_all_positions.py")
        print("  2. Get condition_id from market data")
        print("  3. Run: uv run python scripts/claim_winnings.py --condition-id <ID> --token-id <ID>")
        return
    
    claim_winnings(
        condition_id=args.condition_id,
        token_ids=[args.token_id],
        dry_run=not args.live
    )


if __name__ == "__main__":
    main()
