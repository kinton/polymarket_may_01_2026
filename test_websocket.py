"""
Test WebSocket connection to Polymarket CLOB
"""

import asyncio
import json

import websockets

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# Example token IDs from recent search
TOKEN_ID_YES = (
    "18269720079033639379961248727495249836141135931642359166038905477926358972326"
)
TOKEN_ID_NO = (
    "89505363283803033516158230288929444795069867161314509275989004799702425074573"
)


async def test_websocket():
    """Test WebSocket connection and subscription"""
    try:
        print(f"Connecting to {WS_URL}...")
        async with websockets.connect(WS_URL) as ws:
            print("✓ Connected!")

            # Try different subscription formats
            formats_to_try = [
                # Format 1: Full format from docs
                {
                    "auth": {},
                    "markets": [TOKEN_ID_YES],
                    "assets_ids": [TOKEN_ID_YES],
                    "type": "market",
                },
                # Format 2: Simplified
                {"type": "subscribe", "channel": "market", "market": TOKEN_ID_YES},
                # Format 3: Asset ID only
                {"type": "subscribe", "asset_id": TOKEN_ID_YES},
            ]

            for idx, subscribe_msg in enumerate(formats_to_try, 1):
                print(f"\n=== Trying format #{idx} ===")
                print(json.dumps(subscribe_msg, indent=2))

                await ws.send(json.dumps(subscribe_msg))
                print("✓ Message sent")

                # Wait a bit for response
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=3.0)
                    print("✓ Response received:")
                    print(message[:500])
                    break  # If we got a response, stop trying
                except asyncio.TimeoutError:
                    print("✗ No response (timeout)")
                except websockets.exceptions.ConnectionClosed as e:
                    print(f"✗ Connection closed: {e}")
                    break

    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_websocket())
