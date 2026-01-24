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
    - Environment variables: PRIVATE_KEY, POLYGON_CHAIN_ID, CLOB_HOST, CLOB_API_KEY, CLOB_SECRET, CLOB_PASSPHRASE
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
    from py_clob_client.clob_types import OrderArgs, OrderType, ApiCreds
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
        token_id_yes: str,
        token_id_no: str,
        end_time: datetime,
        dry_run: bool = True,
        trade_size: float = 1.0
    ):
        """
        Initialize the trader.
        
        Args:
            condition_id: The market condition ID
            token_id_yes: The YES token ID
            token_id_no: The NO token ID
            end_time: Market end time (timezone-aware datetime)
            dry_run: If True, only print actions without executing
            trade_size: Size of trade in dollars
        """
        self.condition_id = condition_id
        self.token_id_yes = token_id_yes
        self.token_id_no = token_id_no
        self.end_time = end_time
        self.dry_run = dry_run
        self.trade_size = trade_size
        
        # Market state - track both YES and NO
        self.best_ask_yes: Optional[float] = None
        self.best_bid_yes: Optional[float] = None
        self.best_ask_no: Optional[float] = None
        self.best_bid_no: Optional[float] = None
        self.winning_token_id: Optional[str] = None  # Will be determined dynamically
        self.order_executed = False
        self.ws_yes = None
        self.ws_no = None
        
        # Initialize CLOB client
        load_dotenv()
        self.client = self._init_clob_client()
        
        print(f"{'='*80}")
        print("Last-Second Trader Initialized")
        print(f"{'='*80}")
        print(f"Mode: {'DRY RUN (Safe Mode)' if self.dry_run else '🔴 LIVE TRADING 🔴'}")
        print(f"Condition ID: {self.condition_id}")
        print(f"Token ID (YES): {self.token_id_yes}")
        print(f"Token ID (NO): {self.token_id_no}")
        print(f"End Time: {self.end_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"Trade Size: ${self.trade_size}")
        print(f"Buy Price: ${self.BUY_PRICE}")
        print(f"Trigger: <= {self.TRIGGER_THRESHOLD} second(s) remaining")
        print(f"Strategy: Auto-detect winning side (price > {self.PRICE_THRESHOLD})")
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
            api_key = os.getenv("CLOB_API_KEY")
            api_secret = os.getenv("CLOB_SECRET")
            api_passphrase = os.getenv("CLOB_PASSPHRASE")
            
            if not all([private_key, api_key, api_secret, api_passphrase]):
                print("Warning: Missing CLOB credentials in .env file")
                print(f"  PRIVATE_KEY: {'✓' if private_key else '✗'}")
                print(f"  CLOB_API_KEY: {'✓' if api_key else '✗'}")
                print(f"  CLOB_SECRET: {'✓' if api_secret else '✗'}")
                print(f"  CLOB_PASSPHRASE: {'✓' if api_passphrase else '✗'}")
                return None
            
            # Create ApiCreds object with all three parameters
            creds = ApiCreds(
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase
            )
            
            client = ClobClient(
                host=host,
                key=private_key,  # Fixed: use 'key' not 'private_key'
                chain_id=chain_id,
                creds=creds
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
        """Connect to Polymarket WebSocket and subscribe to both YES and NO tokens."""
        try:
            # Connect to YES token
            self.ws_yes = await websockets.connect(self.WS_URL)
            subscribe_msg_yes = {
                "auth": {},
                "markets": [self.token_id_yes],
                "assets_ids": [self.token_id_yes],
                "type": "market"
            }
            await self.ws_yes.send(json.dumps(subscribe_msg_yes))
            
            # Connect to NO token
            self.ws_no = await websockets.connect(self.WS_URL)
            subscribe_msg_no = {
                "auth": {},
                "markets": [self.token_id_no],
                "assets_ids": [self.token_id_no],
                "type": "market"
            }
            await self.ws_no.send(json.dumps(subscribe_msg_no))
            
            print(f"✓ Connected to WebSocket: {self.WS_URL}")
            print(f"✓ Subscribed to YES token: {self.token_id_yes}")
            print(f"✓ Subscribed to NO token: {self.token_id_no}\n")
            
            return True
            
        except Exception as e:
            print(f"Error connecting to WebSocket: {e}")
            return False
    
    async def process_market_update(self, data: Dict[str, Any], is_yes_token: bool):
        """
        Process incoming market data from WebSocket for YES or NO token.
        
        Expected format:
        {
            "asset_id": "<token_id>",
            "market": "<market_type>",
            "price": "0.75",  # Last trade price
            "bids": [["0.74", "100"], ...],  # [price, size]
            "asks": [["0.76", "100"], ...]
        }
        
        Args:
            data: Market data from WebSocket
            is_yes_token: True if this is YES token data, False for NO token
        """
        try:
            # Extract best bid and ask for the appropriate token
            if "asks" in data and len(data["asks"]) > 0:
                best_ask = float(data["asks"][0][0])
                if is_yes_token:
                    self.best_ask_yes = best_ask
                else:
                    self.best_ask_no = best_ask
            
            if "bids" in data and len(data["bids"]) > 0:
                best_bid = float(data["bids"][0][0])
                if is_yes_token:
                    self.best_bid_yes = best_bid
                else:
                    self.best_bid_no = best_bid
            
            # Determine winning side (price > 0.50)
            self._determine_winning_side()
            
            # Get time remaining
            time_remaining = self.get_time_remaining()
            
            # Log current state (throttled to avoid spam)
            if int(time_remaining) % 10 == 0 or time_remaining <= 5:
                print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] "
                      f"Time: {time_remaining:.2f}s | "
                      f"YES ask: ${self.best_ask_yes:.4f if self.best_ask_yes else 0:.4f} | "
                      f"NO ask: ${self.best_ask_no:.4f if self.best_ask_no else 0:.4f} | "
                      f"Winner: {self._get_winning_side_name()}")
            
            # Check trigger conditions
            await self.check_trigger(time_remaining)
            
        except Exception as e:
            print(f"Error processing market update: {e}")
    
    def _determine_winning_side(self):
        """Determine which token is winning based on price > 0.50."""
        if self.best_ask_yes and self.best_ask_yes > self.PRICE_THRESHOLD:
            self.winning_token_id = self.token_id_yes
        elif self.best_ask_no and self.best_ask_no > self.PRICE_THRESHOLD:
            self.winning_token_id = self.token_id_no
        else:
            self.winning_token_id = None
    
    def _get_winning_side_name(self) -> str:
        """Get human-readable name of winning side."""
        if self.winning_token_id == self.token_id_yes:
            return "YES"
        elif self.winning_token_id == self.token_id_no:
            return "NO"
        else:
            return "None"
    
    def _get_winning_ask(self) -> Optional[float]:
        """Get best ask price for winning side."""
        if self.winning_token_id == self.token_id_yes:
            return self.best_ask_yes
        elif self.winning_token_id == self.token_id_no:
            return self.best_ask_no
        else:
            return None
    
    async def check_trigger(self, time_remaining: float):
        """
        Check if trigger conditions are met and execute trade if appropriate.
        
        Trigger conditions:
        1. Time remaining <= 1 second (but > 0)
        2. Winning side is determined (price > 0.50)
        3. Best ask exists for winning side
        4. Best ask is below $0.99
        5. Order not already executed
        """
        # Already executed or market closed
        if self.order_executed or time_remaining <= 0:
            return
        
        # Check time trigger: <= 1 second but > 0
        if time_remaining > self.TRIGGER_THRESHOLD:
            return
        
        # Check if winning side is determined
        if self.winning_token_id is None:
            print(f"⚠️  Warning: No winning side determined at {time_remaining:.3f}s remaining")
            return
        
        # Get best ask for winning side
        winning_ask = self._get_winning_ask()
        if winning_ask is None:
            print(f"⚠️  Warning: No ask price available for winning side at {time_remaining:.3f}s remaining")
            return
        
        # Check if price is below our target
        if winning_ask >= self.BUY_PRICE:
            print(f"⚠️  Best ask ${winning_ask:.4f} >= ${self.BUY_PRICE} - not worth buying")
            return
        
        # All conditions met - execute trade!
        winning_side_name = self._get_winning_side_name()
        print(f"\n{'='*80}")
        print(f"🎯 TRIGGER ACTIVATED at {time_remaining:.3f}s remaining!")
        print(f"{'='*80}")
        print(f"Winning Side: {winning_side_name}")
        print(f"Best Ask: ${winning_ask:.4f}")
        print(f"Target Price: ${self.BUY_PRICE}")
        print(f"Trade Size: ${self.trade_size}")
        print(f"{'='*80}\n")
        
        await self.execute_order()
        self.order_executed = True
    
    async def execute_order(self):
        """
        Execute Fill-or-Kill (FOK) order on the winning side.
        In dry run mode, only prints the intended action.
        """
        winning_ask = self._get_winning_ask()
        winning_side_name = self._get_winning_side_name()
        
        if self.dry_run:
            print(f"{'='*80}")
            print("🔷 DRY RUN MODE - NO REAL TRADE EXECUTED")
            print(f"{'='*80}")
            print("WOULD BUY:")
            print(f"  Side: {winning_side_name}")
            print(f"  Token ID: {self.winning_token_id}")
            print(f"  Price: ${self.BUY_PRICE}")
            print(f"  Size: ${self.trade_size}")
            print("  Type: Fill-or-Kill (FOK)")
            print(f"  Current Best Ask: ${winning_ask:.4f}")
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
            
            # Create FOK order at $0.99 for winning side
            order_args = OrderArgs(
                token_id=self.winning_token_id,
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
        Main loop: Listen to both WebSocket connections and process market updates.
        Runs until market closes or connection is lost.
        """
        async def listen_to_ws(ws, is_yes_token: bool):
            """Listen to a single WebSocket connection."""
            try:
                token_name = "YES" if is_yes_token else "NO"
                print(f"[DEBUG] Starting to listen for {token_name} WebSocket messages...")
                message_count = 0
                async for message in ws:
                    message_count += 1
                    if message_count <= 5:  # Only show first few messages
                        print(f"[DEBUG] {token_name} message #{message_count}: {message[:150]}...")
                    data = json.loads(message)
                    
                    # Process market update
                    await self.process_market_update(data, is_yes_token)
                    
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
                print(f"\n⚠️  {token_name} WebSocket connection closed")
            except Exception as e:
                print(f"\n❌ Error in {token_name} market listener: {e}")
        
        # Listen to both WebSockets concurrently
        try:
            await asyncio.gather(
                listen_to_ws(self.ws_yes, True),
                listen_to_ws(self.ws_no, False)
            )
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
            if self.ws_yes:
                await self.ws_yes.close()
            if self.ws_no:
                await self.ws_no.close()
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
