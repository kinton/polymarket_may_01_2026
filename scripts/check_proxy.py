#!/usr/bin/env python3
"""
Check balance for Polymarket proxy wallet.
"""

import os

from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import AssetType, BalanceAllowanceParams
from py_clob_client.constants import POLYGON

PROXY_ADDRESS = "0x35d26795bE15E060A2C7AA42C2aCF9527E3acE47"


def main():
    load_dotenv()

    private_key = os.getenv("PRIVATE_KEY")
    api_key = os.getenv("CLOB_API_KEY")
    api_secret = os.getenv("CLOB_SECRET")
    api_passphrase = os.getenv("CLOB_PASSPHRASE")

    if not all([private_key, api_key, api_secret, api_passphrase]):
        print("❌ Missing credentials in .env")
        return

    print(f"Testing with proxy address: {PROXY_ADDRESS}")
    print("=" * 60)

    # Test different signature types - derive new creds each time
    for sig_type in [0, 1, 2]:
        sig_name = {0: "EOA", 1: "POLY_GNOSIS_SAFE", 2: "POLY_PROXY"}
        try:
            client = ClobClient(
                host="https://clob.polymarket.com",
                key=private_key,
                chain_id=POLYGON,
                signature_type=sig_type,
                funder=PROXY_ADDRESS,
            )
            # Derive new API creds (not from .env)
            client.set_api_creds(client.create_or_derive_api_creds())

            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            balance_info = client.get_balance_allowance(params)
            bal = int(balance_info.get("balance", 0)) / 1_000_000

            allowances = balance_info.get("allowances", {})
            max_allowance = 0
            for addr, val in allowances.items():
                try:
                    max_allowance = max(max_allowance, int(val) / 1_000_000)
                except (ValueError, TypeError):
                    pass

            print(f"signature_type={sig_type} ({sig_name.get(sig_type, '?')})")
            print(f"  Balance: ${bal:.2f}")
            print(f"  Max Allowance: ${max_allowance:.2f}")
            print()

        except Exception as e:
            print(f"signature_type={sig_type}: Error - {e}")
            print()


if __name__ == "__main__":
    main()
