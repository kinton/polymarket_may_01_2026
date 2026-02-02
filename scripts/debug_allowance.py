#!/usr/bin/env python3
"""Debug allowance check to see raw API response"""

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

load_dotenv()


def main():
    # Initialize CLOB client (same as hft_trader.py)
    host = os.getenv("CLOB_HOST", "https://clob.polymarket.com")
    private_key = os.getenv("PRIVATE_KEY")
    funder = os.getenv("POLYMARKET_PROXY_ADDRESS")
    chain_id = int(os.getenv("CHAIN_ID", "137"))

    print(f"🔑 Private Key: {'✓' if private_key else '❌'}")
    print(f"💼 Proxy Address: {funder}")
    print(f"🔗 Chain ID: {chain_id}")
    print(f"🌐 Host: {host}\n")

    client = ClobClient(
        host=host,
        key=private_key,
        chain_id=chain_id,
        signature_type=2,  # POLY_PROXY for Polymarket proxy wallets
        funder=funder,
    )

    api_creds = client.create_or_derive_api_creds()
    client.set_api_creds(api_creds)
    print("✅ CLOB client initialized\n")

    print("🔍 Calling get_balance_allowance()...")
    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    result = client.get_balance_allowance(params)

    print(f"\n📦 Raw API response:\n{result}")
    print(f"\n📝 Type: {type(result)}")

    if isinstance(result, dict):
        print(f"\n💰 Balance: {result.get('balance')}")
        print(f"✅ Allowances dict: {result.get('allowances')}")

        # Get Exchange contract allowance
        EXCHANGE_CONTRACT = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
        allowances_dict = result.get("allowances", {})
        exchange_allowance = allowances_dict.get(EXCHANGE_CONTRACT, 0)
        print(f"\n🎯 Exchange contract allowance: {exchange_allowance}")

        # Try converting to float
        try:
            balance_float = float(result.get("balance", 0))
            print(f"\n🔢 Balance as float: {balance_float}")
        except Exception as e:
            print(f"❌ Error converting balance: {e}")

        try:
            allowance_float = float(exchange_allowance)
            print(f"🔢 Exchange allowance as float: {allowance_float}")
            print(
                "✅ Allowance is sufficient!"
                if allowance_float > 0
                else "❌ No allowance!"
            )
        except Exception as e:
            print(f"❌ Error converting allowance: {e}")


if __name__ == "__main__":
    main()
