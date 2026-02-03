#!/usr/bin/env python3
"""
Check all token positions (bought YES/NO shares)
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import AssetType, BalanceAllowanceParams, TradeParams
from py_clob_client.constants import POLYGON

load_dotenv()


async def main():
    private_key = os.getenv("PRIVATE_KEY")
    proxy = os.getenv("POLYMARKET_PROXY_ADDRESS")

    # Initialize client
    client = ClobClient(
        host="https://clob.polymarket.com",
        key=private_key,
        chain_id=POLYGON,
        signature_type=2,
        funder=proxy,
    )
    client.set_api_creds(client.create_or_derive_api_creds())

    print("=" * 70)
    print("CHECKING ALL TOKEN POSITIONS")
    print("=" * 70)
    print()

    # Get trade history
    print("📊 Fetching trade history...")
    trades = client.get_trades(params=TradeParams(maker_address=client.get_address()))

    if not trades:
        print("   No trades found")
        return

    print(f"   Found {len(trades)} trades")
    print()

    # Extract unique token IDs from BUY orders
    token_ids = set()
    token_market_map = {}  # Map token_id to market/condition info
    
    for trade in trades:
        if trade.get("side") == "BUY":
            token_id = trade.get("asset_id")
            if token_id:
                token_ids.add(token_id)
                # Store market info for later claim
                token_market_map[token_id] = {
                    "market": trade.get("market"),
                    "timestamp": trade.get("timestamp"),
                }

    print(f"🔍 Checking balances for {len(token_ids)} tokens...")
    print()

    total_value = 0.0
    positions = []
    winning_positions = []

    for token_id in token_ids:
        try:
            balance_info = client.get_balance_allowance(
                params=BalanceAllowanceParams(
                    asset_type=AssetType.CONDITIONAL, token_id=token_id
                )
            )

            balance = float(balance_info.get("balance", 0)) / 1e6  # Convert from micro-units

            if balance > 0.01:  # Skip dust
                # Get current price
                try:
                    price_info = client.get_price(token_id, "SELL")  # Price we can sell for
                    price = float(price_info.get("price", 0))
                except:
                    price = 0.0

                value = balance * price
                total_value += value

                position = {
                    "token_id": token_id,
                    "balance": balance,
                    "price": price,
                    "value": value,
                    "market": token_market_map.get(token_id, {}).get("market", "Unknown"),
                }
                positions.append(position)

                # Check if this is a winning position (price = $1.00 or $0.99+)
                is_winning = price >= 0.99
                if is_winning:
                    winning_positions.append(position)

                emoji = "🏆" if is_winning else "💰"
                print(f"{emoji} Token: {token_id[:10]}...")
                print(f"   Balance: {balance:.6f} shares")
                print(f"   Price: ${price:.4f}")
                print(f"   Value: ${value:.2f}")
                if is_winning:
                    print(f"   ✨ WINNING POSITION - Ready to claim!")
                print()

        except Exception as e:
            print(f"❌ Error checking {token_id[:10]}...: {e}")
            print()

    print("=" * 70)
    print(f"📊 SUMMARY")
    print("=" * 70)
    print(f"Open positions: {len(positions)}")
    print(f"Total value: ${total_value:.2f}")
    print()

    # Check USDC balance
    usdc_params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    usdc_data = client.get_balance_allowance(usdc_params)
    usdc_balance = float(usdc_data.get("balance", 0)) / 1e6

    print(f"💵 Free USDC: ${usdc_balance:.2f}")
    print(f"🔒 Locked in positions: ${total_value:.2f}")
    print(f"📊 Total portfolio: ${usdc_balance + total_value:.2f}")
    print()

    # Show winning positions that can be claimed
    if winning_positions:
        print("=" * 70)
        print("🏆 WINNING POSITIONS - READY TO CLAIM")
        print("=" * 70)
        total_claimable = sum(p["value"] for p in winning_positions)
        print(f"Total claimable: ${total_claimable:.2f}")
        print()
        
        for pos in winning_positions:
            print(f"Token: {pos['token_id']}")
            print(f"  Shares: {pos['balance']:.6f}")
            print(f"  Value: ${pos['value']:.2f}")
            print(f"  Market: {pos['market']}")
            print()
        
        print("To claim winnings:")
        print("  1. Get condition_id for each market from Gamma API")
        print("  2. Run: uv run python scripts/claim_winnings.py --condition-id <ID> --token-id <TOKEN>")
        print()
    
    if positions and not winning_positions:
        print("💡 To free up USDC, sell these positions:")
        print("   uv run python src/position_settler.py")


if __name__ == "__main__":
    asyncio.run(main())
