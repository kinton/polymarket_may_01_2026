#!/usr/bin/env python3
"""
Check USDC balance and allowance for Polymarket trading.
"""

import os

from dotenv import load_dotenv
from eth_account import Account
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import AssetType, BalanceAllowanceParams
from py_clob_client.constants import POLYGON


def main():
    load_dotenv()
    private_key = os.getenv("PRIVATE_KEY")
    proxy_address = os.getenv("POLYMARKET_PROXY_ADDRESS")

    if not private_key:
        print("❌ PRIVATE_KEY not found in .env")
        return

    try:
        account = Account.from_key(private_key)
        print(f"🔑 EOA Wallet: {account.address}")
        print(f"🔷 Proxy Wallet: {proxy_address or 'Not set'}")
        print()

        # Use signature_type=2 for Polymarket proxy wallets
        client = ClobClient(
            host="https://clob.polymarket.com",
            key=private_key,
            chain_id=POLYGON,
            signature_type=2,  # POLY_PROXY
            funder=proxy_address or "",
        )
        client.set_api_creds(client.create_or_derive_api_creds())

        # Get balance and allowance
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)  # type: ignore
        balance_info = client.get_balance_allowance(params)

        print("=" * 50)
        print("POLYMARKET BALANCE CHECK")
        print("=" * 50)
        print(f"Raw response: {balance_info}")
        print()

        # Parse balance (usually in wei/smallest unit)
        if isinstance(balance_info, dict):
            balance = balance_info.get("balance", "N/A")
            allowance = balance_info.get("allowance", "N/A")

            # Convert from smallest unit (6 decimals for USDC)
            try:
                balance_usdc = int(balance) / 1_000_000 if balance != "N/A" else "N/A"
                allowance_usdc = (
                    int(allowance) / 1_000_000 if allowance != "N/A" else "N/A"
                )
            except (ValueError, TypeError):
                balance_usdc = balance
                allowance_usdc = allowance

            print(f"💰 USDC Balance: ${balance_usdc}")
            print(f"✅ Allowance: ${allowance_usdc}")
            print()

            if isinstance(balance_usdc, (int, float)) and balance_usdc < 2:
                print("⚠️  WARNING: Balance too low for trading!")
                print("   Need at least $2 USDC to trade")

            if isinstance(allowance_usdc, (int, float)) and allowance_usdc < 1000:
                print("⚠️  WARNING: Allowance may be too low!")
                print("   Run: uv run python approve.py")
        else:
            print(f"Balance info: {balance_info}")

    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    main()
