#!/usr/bin/env python3
"""Quick balance check"""
import os, sys
sys.path.insert(0, '/Users/kinton/Projects/Trading/Polymarket/May/baseTrader')
from dotenv import load_dotenv
load_dotenv()

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import AssetType, BalanceAllowanceParams
from py_clob_client.constants import POLYGON

pk = os.getenv("PRIVATE_KEY")
px = os.getenv("POLYMARKET_PROXY_ADDRESS")

# Current setup (signature_type=2)
c = ClobClient("https://clob.polymarket.com", key=pk, chain_id=POLYGON, signature_type=2, funder=px)
c.set_api_creds(c.create_or_derive_api_creds())

p = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
d = c.get_balance_allowance(p)

print(f"Balance: {float(d.get('balance', 0)) / 1e6:.6f} USDC")
print(f"Full data: {d}")
