"""
High-Frequency Trading Script for Polymarket 15-minute markets.

This script connects to Polymarket's CLOB WebSocket to stream real-time market data
and executes a last-second trading strategy.

Strategy:
- Monitor Level 1 order book (best bid/ask) via WebSocket
- Track the winning side (price > 0.50)
- When time remaining <= 1 second (but > 0):
  - Check if winning side is available below $0.99
  - Execute Fill-or-Kill (FOK) order at $0.99

Safety Features:
- DRY_RUN mode (default: True) - only prints intended actions
- Configurable trade size (default: 1)
- No execution until explicitly enabled

Usage:
    # Dry run mode (safe, no real trades)
    python hft_trader.py --condition-id <CONDITION_ID> --token-id <TOKEN_ID> --end-time <ISO_TIME>
    
    # Live trading (DANGER!)
    python hft_trader.py --condition-id <CONDITION_ID> --token-id <TOKEN_ID> --end-time <ISO_TIME> --live
    
Requirements:
    - py-clob-client
    - websockets
    - python-dotenv
    - Environment variables: PRIVATE_KEY, POLYGON_CHAIN_ID, CLOB_HOST, CLOB_KEY, CLOB_SECRET
"""

import asyncio
import json
import websockets
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import os
from dotenv import load_dotenv

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType
except ImportError:
    print("Error: py-clob-client not installed. Run: uv pip install py-clob-client")
    exit(1)


class LastSecondTrader:
    """
    High-frequency trader that monitors market data via WebSocket
    and executes trades in the final second before market close.
    """
    
    # Configuration
    DRY_RUN = True  # Safety: Default to dry run mode
    TRADE_SIZE = 1   # Default trade size in dollars
    TRIGGER_THRESHOLD = 1.0  # Trigger when <= 1 second remaining
    PRICE_THRESHOLD = 0.50   # Winning side threshold
    BUY_PRICE = 0.99         # Target buy price
    
    WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    
    def __init__(
        self,
        condition_id: str,
        token_id: str,
        end_time: datetime,
        dry_run: bool = True,
        trade_size: float = 1.0
    ):
        """
        Initialize the trader.
        
        Args:
            condition_id: The market condition ID
            token_id: The token ID to trade (YES or NO)
            end_time: Market end time (timezone-aware datetime)
            dry_run: If True, only print actions without executing
            trade_size: Size of trade in dollars
        """
        self.condition_id = condition_id
        self.token_id = token_id
        self.end_time = end_time
        self.dry_run = dry_run
        self.trade_size = trade_size
        
        # Market state
        self.best_ask: Optional[float] = None
        self.best_bid: Optional[float] = None
        self.order_executed = False
        self.ws = None
        
        # Initialize CLOB client
        load_dotenv()
        self.client = self._init_clob_client()
        
        print(f"{'='*80}")
        print("Last-Second Trader Initialized")
        print(f"{'='*80}")
        print(f"Mode: {'DRY RUN (Safe Mode)' if self.dry_run else '🔴 LIVE TRADING 🔴'}")
        print(f"Condition ID: {self.condition_id}")
        print(f"Token ID: {self.token_id}")
        print(f"End Time: {self.end_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"Trade Size: ${self.trade_size}")
        print(f"Buy Price: ${self.BUY_PRICE}")
        print(f"Trigger: <= {self.TRIGGER_THRESHOLD} second(s) remaining")
        print(f"{'='*80}\n")
    
    def _init_clob_client(self) -> Optional[ClobClient]:
        """Initialize the CLOB client for order execution."""
        if self.dry_run:
            print("Dry run mode: Skipping CLOB client initialization\n")
            return None
        
        try:
            private_key = os.getenv("PRIVATE_KEY")
            chain_id = int(os.getenv("POLYGON_CHAIN_ID", "137"))
            host = os.getenv("CLOB_HOST", "https://clob.polymarket.com")
            key = os.getenv("CLOB_KEY")
            secret = os.getenv("CLOB_SECRET")
            
            if not all([private_key, key, secret]):
                print("Warning: Missing CLOB credentials in .env file")
                return None
            
            client = ClobClient(
                host=host,
                key=key,
                secret=secret,
                chain_id=chain_id,
                private_key=private_key
            )
            print("✓ CLOB client initialized for live trading\n")
            return client
            
        except Exception as e:
            print(f"Error initializing CLOB client: {e}")
            return None
    
    def get_time_remaining(self) -> float:
        """
        Calculate time remaining until market close in seconds.
        
        Returns:
            Seconds remaining (can be negative if market closed)
        """
        now = datetime.now(timezone.utc)
        delta = (self.end_time - now).total_seconds()
        return delta
    
    async def connect_websocket(self):
        """Connect to Polymarket WebSocket and subscribe to market data."""
        try:
            self.ws = await websockets.connect(self.WS_URL)
            
            # Subscribe to market updates (Level 1 - best bid/ask)
            subscribe_msg = {
                "auth": {},
                "markets": [self.token_id],
                "assets_ids": [self.token_id],
                "type": "market"
            }
            
            await self.ws.send(json.dumps(subscribe_msg))
            print(f"✓ Connected to WebSocket: {self.WS_URL}")
            print(f"✓ Subscribed to token: {self.token_id}\n")
            
            return True
            
        except Exception as e:
            print(f"Error connecting to WebSocket: {e}")
            return False
    
    async def process_market_update(self, data: Dict[str, Any]):
        """
        Process incoming market data from WebSocket.
        
        Expected format:
        {
            "asset_id": "<token_id>",
            "market": "<market_type>",
            "price": "0.75",  # Last trade price
            "bids": [["0.74", "100"], ...],  # [price, size]
            "asks": [["0.76", "100"], ...]
        }
        """
        try:
            # Extract best bid and ask
            if "asks" in data and len(data["asks"]) > 0:
                self.best_ask = float(data["asks"][0][0])
            
            if "bids" in data and len(data["bids"]) > 0:
                self.best_bid = float(data["bids"][0][0])
            
            # Get time remaining
            time_remaining = self.get_time_remaining()
            
            # Log current state (throttled to avoid spam)
            if int(time_remaining) % 10 == 0 or time_remaining <= 5:
                print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] "
                      f"Time remaining: {time_remaining:.2f}s | "
                      f"Best Bid: ${self.best_bid:.4f if self.best_bid else 0:.4f} | "
                      f"Best Ask: ${self.best_ask:.4f if self.best_ask else 0:.4f}")
            
            # Check trigger conditions
            await self.check_trigger(time_remaining)
            
        except Exception as e:
            print(f"Error processing market update: {e}")
    
    async def check_trigger(self, time_remaining: float):
        """
        Check if trigger conditions are met and execute trade if appropriate.
        
        Trigger conditions:
        1. Time remaining <= 1 second (but > 0)
        2. Best ask exists
        3. Best ask indicates winning side (price > 0.50)
        4. Best ask is below $0.99
        5. Order not already executed
        """
        # Already executed or market closed
        if self.order_executed or time_remaining <= 0:
            return
        
        # Check time trigger: <= 1 second but > 0
        if time_remaining > self.TRIGGER_THRESHOLD:
            return
        
        # Check if we have market data
        if self.best_ask is None:
            print(f"⚠️  Warning: No ask price available at {time_remaining:.3f}s remaining")
            return
        
        # Check if it's the winning side (price > 0.50)
        if self.best_ask <= self.PRICE_THRESHOLD:
            print(f"⚠️  Best ask ${self.best_ask:.4f} not on winning side (need > ${self.PRICE_THRESHOLD})")
            return
        
        # Check if price is below our target
        if self.best_ask >= self.BUY_PRICE:
            print(f"⚠️  Best ask ${self.best_ask:.4f} >= ${self.BUY_PRICE} - not worth buying")
            return
        
        # All conditions met - execute trade!
        print(f"\n{'='*80}")
        print(f"🎯 TRIGGER ACTIVATED at {time_remaining:.3f}s remaining!")
        print(f"{'='*80}")
        print(f"Best Ask: ${self.best_ask:.4f}")
        print(f"Winning Side: YES (price > ${self.PRICE_THRESHOLD})")
        print(f"Target Price: ${self.BUY_PRICE}")
        print(f"Trade Size: ${self.trade_size}")
        print(f"{'='*80}\n")
        
        await self.execute_order()
        self.order_executed = True
    
    async def execute_order(self):
        """
        Execute Fill-or-Kill (FOK) order.
        In dry run mode, only prints the intended action.
        """
        if self.dry_run:
            print(f"{'='*80}")
            print("🔷 DRY RUN MODE - NO REAL TRADE EXECUTED")
            print(f"{'='*80}")
            print("WOULD BUY:")
            print(f"  Token ID: {self.token_id}")
            print(f"  Price: ${self.BUY_PRICE}")
            print(f"  Size: ${self.trade_size}")
            print("  Type: Fill-or-Kill (FOK)")
            print(f"  Current Best Ask: ${self.best_ask:.4f}")
            print(f"{'='*80}\n")
            return
        
        # Live trading mode
        if not self.client:
            print("❌ Error: CLOB client not initialized. Cannot execute order.")
            return
        
        try:
            print(f"{'='*80}")
            print("🔴 EXECUTING LIVE ORDER...")
            print(f"{'='*80}")
            
            # Create FOK order at $0.99
            order_args = OrderArgs(
                token_id=self.token_id,
                price=self.BUY_PRICE,
                size=self.trade_size,
                side="BUY",
                order_type=OrderType.FOK  # Fill-or-Kill
            )
            
            # Submit order
            response = await asyncio.to_thread(
                self.client.create_and_post_order,
                order_args
            )
            
            print("✓ Order submitted successfully!")
            print(f"Response: {json.dumps(response, indent=2)}")
            print(f"{'='*80}\n")
            
        except Exception as e:
            print(f"❌ Error executing order: {e}")
            print(f"{'='*80}\n")
    
    async def listen_to_market(self):
        """
        Main loop: Listen to WebSocket messages and process market updates.
        Runs until market closes or connection is lost.
        """
        try:
            async for message in self.ws:
                data = json.loads(message)
                
                # Process market update
                await self.process_market_update(data)
                
                # Check if market closed
                time_remaining = self.get_time_remaining()
                if time_remaining <= 0:
                    print(f"\n{'='*80}")
                    print(f"⏰ Market closed. Time remaining: {time_remaining:.2f}s")
                    if not self.order_executed:
                        print("No order was executed.")
                    print(f"{'='*80}\n")
                    break
                
        except websockets.exceptions.ConnectionClosed:
            print("\n⚠️  WebSocket connection closed")
        except Exception as e:
            print(f"\n❌ Error in market listener: {e}")
    
    async def run(self):
        """Main entry point: Connect and start trading."""
        try:
            # Connect to WebSocket
            connected = await self.connect_websocket()
            if not connected:
                print("Failed to connect to WebSocket. Exiting.")
                return
            
            # Start listening to market data
            await self.listen_to_market()
            
        except KeyboardInterrupt:
            print("\n\n⚠️  Interrupted by user. Shutting down...")
        finally:
            if self.ws:
                await self.ws.close()
            print("\n✓ Trader shut down cleanly\n")


async def main():
    """
    Main entry point with example usage.
    
    In production, pass these values from command line or from gamma_15m_finder.py
    """
    import sys
    
    # Example values - REPLACE WITH ACTUAL VALUES
    EXAMPLE_CONDITION_ID = "0x1234567890abcdef"
    EXAMPLE_TOKEN_ID = "12345678"
    # EXAMPLE_END_TIME = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=300)  # 5 min from now
    
    # Parse command line args (simple version)
    if len(sys.argv) < 2:
        print("Usage: python hft_trader.py --condition-id <ID> --token-id <ID> --end-time <ISO_TIME> [--live] [--size <SIZE>]")
        print("\nExample (dry run):")
        print(f"  python hft_trader.py --condition-id {EXAMPLE_CONDITION_ID} --token-id {EXAMPLE_TOKEN_ID} --end-time 2026-01-24T12:30:00Z")
        print("\nExample (live trading - DANGER!):")
        print(f"  python hft_trader.py --condition-id {EXAMPLE_CONDITION_ID} --token-id {EXAMPLE_TOKEN_ID} --end-time 2026-01-24T12:30:00Z --live --size 10")
        print("\n⚠️  WARNING: Default is DRY_RUN=True and SIZE=1 for safety!")
        return
    
    # Simple arg parsing
    args = sys.argv[1:]
    condition_id = None
    token_id = None
    end_time = None
    dry_run = True  # Default to safe mode
    trade_size = 1.0  # Default size
    
    i = 0
    while i < len(args):
        if args[i] == "--condition-id" and i + 1 < len(args):
            condition_id = args[i + 1]
            i += 2
        elif args[i] == "--token-id" and i + 1 < len(args):
            token_id = args[i + 1]
            i += 2
        elif args[i] == "--end-time" and i + 1 < len(args):
            end_time = datetime.fromisoformat(args[i + 1].replace("Z", "+00:00"))
            i += 2
        elif args[i] == "--live":
            dry_run = False
            i += 1
        elif args[i] == "--size" and i + 1 < len(args):
            trade_size = float(args[i + 1])
            i += 2
        else:
            i += 1
    
    if not all([condition_id, token_id, end_time]):
        print("❌ Error: Missing required arguments (--condition-id, --token-id, --end-time)")
        return
    
    # Create and run trader
    trader = LastSecondTrader(
        condition_id=condition_id,
        token_id=token_id,
        end_time=end_time,
        dry_run=dry_run,
        trade_size=trade_size
    )
    
    await trader.run()


if __name__ == "__main__":
    asyncio.run(main())
