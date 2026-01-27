#!/usr/bin/env python3
"""
Approve USDC allowance for Polymarket trading on Polygon mainnet.
"""

import os

from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import AssetType, BalanceAllowanceParams
from py_clob_client.constants import POLYGON


def main():
    # Load environment variables
    load_dotenv()
    private_key = os.getenv("PRIVATE_KEY")

    if not private_key:
        print("Error: PRIVATE_KEY not found in .env file")
        return

    try:
        # Initialize ClobClient on Polygon Mainnet (Chain ID 137)
        # signature_type=0 for EOA/MetaMask wallets (default)
        client = ClobClient(
            host="https://clob.polymarket.com",
            key=private_key,
            chain_id=POLYGON,  # Chain ID 137
        )

        # Create or derive API credentials - REQUIRED for authentication
        client.set_api_creds(client.create_or_derive_api_creds())

        # Update balance and allowance for the user
        # This authorizes the exchange to spend USDC (collateral asset)
        print("Updating balance and allowance for Polymarket exchange...")
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)  # type: ignore
        result = client.update_balance_allowance(params)

        print(f"Success! Result: {result}")

    except Exception as e:
        print(f"Error: {str(e)}")


if __name__ == "__main__":
    main()
