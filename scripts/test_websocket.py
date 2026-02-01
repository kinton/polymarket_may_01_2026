#!/usr/bin/env python3
"""Test WebSocket data format."""
import asyncio
import json
import websockets

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# Active market tokens (find from gamma API or recent logs)
# Using Solana 6AM market
YES_TOKEN = "13370581172933891391113066595640479132762698108064247290749242889130004119813"
NO_TOKEN = "104789774317139575915064985340875383502299301877454707730952703602618274765937"

async def test_websocket():
    print(f"Connecting to {WS_URL}...")
    
    async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=10) as ws:
        # Subscribe to both tokens
        subscribe_msg = {
            "assets_ids": [YES_TOKEN, NO_TOKEN],
            "type": "MARKET",
        }
        await ws.send(json.dumps(subscribe_msg))
        print("Subscribed to YES and NO tokens\n")
        
        # Read first 10 messages
        for i in range(10):
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                data = json.loads(msg)
                
                print(f"=== Message {i+1} ===")
                print(f"Raw: {msg[:500]}...")
                
                # Parse the data
                if isinstance(data, list):
                    data = data[0] if data else {}
                
                asset_id = data.get("asset_id", "unknown")
                event_type = data.get("event_type", "unknown")
                
                is_yes = asset_id == YES_TOKEN
                token_name = "YES" if is_yes else "NO" if asset_id == NO_TOKEN else "???"
                
                print(f"Token: {token_name}")
                print(f"Event type: {event_type}")
                
                if event_type == "book":
                    asks = data.get("asks", [])
                    bids = data.get("bids", [])
                    print(f"Asks (first 3): {asks[:3]}")
                    print(f"Bids (first 3): {bids[:3]}")
                    
                elif event_type == "price_change":
                    changes = data.get("price_changes", [])
                    print(f"Price changes: {changes}")
                    
                elif event_type == "best_bid_ask":
                    print(f"Best ask: {data.get('best_ask')}")
                    print(f"Best bid: {data.get('best_bid')}")
                
                print()
                
            except asyncio.TimeoutError:
                print(f"Timeout waiting for message {i+1}")
                break

if __name__ == "__main__":
    asyncio.run(test_websocket())
