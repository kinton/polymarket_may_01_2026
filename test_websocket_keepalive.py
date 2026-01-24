"""
Test WebSocket with keepalive/ping-pong
"""
import asyncio
import json
import websockets

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
TOKEN_ID_YES = "45357664351820458021802837270620664608973849241629991182885575327538100438884"
TOKEN_ID_NO = "24829516488761686269259292156186910945772910123654830804148452618893441357320"

async def test_websocket_with_ping():
    """Test WebSocket with ping-pong for keepalive"""
    try:
        print(f"Connecting to {WS_URL}...")
        
        # Add ping settings
        async with websockets.connect(
            WS_URL,
            ping_interval=20,  # Send ping every 20 seconds
            ping_timeout=10,   # Wait 10 seconds for pong
            close_timeout=10
        ) as ws:
            print("✓ Connected with ping enabled!")
            
            # Correct subscription format from official docs
            # For MARKET channel, auth is not required
            subscribe_msg = {
                "assets_ids": [TOKEN_ID_YES, TOKEN_ID_NO],
                "type": "MARKET"  # Must be uppercase!
            }
            
            print(f"\nSending correct subscription format:")
            print(json.dumps(subscribe_msg, indent=2))
            
            await ws.send(json.dumps(subscribe_msg))
            print("✓ Subscription sent\n")
            
            # Listen for messages
            print("Listening for messages (60 seconds)...\n")
            message_count = 0
            
            try:
                while True:
                    try:
                        message = await asyncio.wait_for(ws.recv(), timeout=60)
                        message_count += 1
                        print(f"\n[Message #{message_count}] at {asyncio.get_event_loop().time():.2f}s")
                        
                        # Try to parse as JSON
                        try:
                            data = json.loads(message)
                            print(json.dumps(data, indent=2)[:500])
                        except:
                            print(message[:500])
                        
                        if message_count >= 10:
                            break
                    except asyncio.TimeoutError:
                        print("\nTimeout reached")
                        break
                        
            except websockets.exceptions.ConnectionClosed:
                print("\nConnection closed by server")
                
            if message_count == 0:
                print("\n⚠️  No messages received!")
            else:
                print(f"\n✓ Received {message_count} message(s)")
                
    except websockets.exceptions.ConnectionClosed as e:
        print(f"\n✗ Connection closed: {e}")
        print(f"   Code: {e.code}, Reason: {e.reason}")
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_websocket_with_ping())
