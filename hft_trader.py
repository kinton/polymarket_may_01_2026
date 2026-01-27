"""
High-Frequency Trading Script for Polymarket 15-minute markets.

This script connects to Polymarket's CLOB WebSocket to stream real-time market data
and executes a last-second trading strategy.

Strategy:
- Monitor Level 1 order book (best bid/ask) via WebSocket
- Track the winning side (price > 0.50)
- When time remaining <= 60 seconds (but > 0):
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
import os
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from typing import Any, Dict, Optional

import websockets
from dotenv import load_dotenv

from clob_types import (
    BUY_PRICE,
    CLOB_WS_URL,
    PRICE_TIE_EPS,
    TRIGGER_THRESHOLD,
    OrderBook,
)
from market_parser import (
    determine_winning_side,
    extract_best_ask_from_book,
    extract_best_bid_from_book,
    get_winning_token_id,
)

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType
except ImportError:
    print("Error: py-clob-client not installed. Run: uv pip install py-clob-client")
    exit(1)


class LastSecondTrader:
    """
    High-frequency trader that monitors market data via WebSocket
    and executes trades in the final seconds before market close.
    """

    # Configuration
    DRY_RUN = True  # Safety: Default to dry run mode
    TRADE_SIZE = 1  # Default trade size in dollars
    TRIGGER_THRESHOLD = TRIGGER_THRESHOLD
    PRICE_THRESHOLD = 0.50  # Winning side threshold
    BUY_PRICE = BUY_PRICE
    PRICE_TIE_EPS = PRICE_TIE_EPS

    WS_URL = CLOB_WS_URL

    def __init__(
        self,
        condition_id: str,
        token_id_yes: str,
        token_id_no: str,
        end_time: datetime,
        dry_run: bool = True,
        trade_size: float = 1.0,
        title: Optional[str] = None,
        slug: Optional[str] = None,
    ):
        """
        Initialize the trader.

        Args:
            condition_id: The market condition ID
            token_id_yes: The YES token ID
            token_id_no: The NO token ID
            end_time: Market end time (timezone-aware datetime)
            dry_run: If True, only print actions without executing
            trade_size: Size of trade in dollars (will buy trade_size/price tokens)
        """
        self.condition_id = condition_id
        self.token_id_yes = token_id_yes
        self.token_id_no = token_id_no
        self.end_time = end_time
        self.dry_run = dry_run
        self.trade_size = trade_size  # dollars to spend
        self.title = title
        self.slug = slug

        # Extract short market name for logging (e.g. "BTC", "ETH", "SOL")
        self.market_name = self._extract_market_name(title)

        # Market state - track both YES and NO
        self.orderbook = OrderBook()
        self.winning_side: Optional[str] = None  # "YES" or "NO"
        self.order_executed = False
        self.ws_yes = None
        self.ws_no = None

        # Track last log time to avoid spam
        self.last_log_time = 0.0
        self.last_logged_state = None

        # Initialize CLOB client
        load_dotenv()
        self.client = self._init_clob_client()

        print(f"{'=' * 80}")
        print("Last-Second Trader Initialized")
        print(f"{'=' * 80}")
        print(
            f"Mode: {'DRY RUN (Safe Mode)' if self.dry_run else '🔴 LIVE TRADING 🔴'}"
        )
        print(f"Condition ID: {self.condition_id}")
        print(f"Token ID (YES): {self.token_id_yes}")
        print(f"Token ID (NO): {self.token_id_no}")
        print(f"End Time: {self.end_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"Trade Size: ${self.trade_size}")
        print(f"Buy Price: ${self.BUY_PRICE}")
        print(f"Trigger: <= {self.TRIGGER_THRESHOLD} second(s) remaining")
        print("Strategy: Auto-detect winning side (higher ask wins)")
        if self.slug:
            print(f"Market Link: https://polymarket.com/market/{self.slug}")
        print(f"{'=' * 80}\n")

    def _extract_market_name(self, title: Optional[str]) -> str:
        """Extract short market name from title for logging."""
        if not title:
            return "UNKNOWN"

        # Extract cryptocurrency name (e.g., "Bitcoin" -> "BTC")
        title_lower = title.lower()
        if "bitcoin" in title_lower or "btc" in title_lower:
            return "BTC"
        elif "ethereum" in title_lower or "eth" in title_lower:
            return "ETH"
        elif "solana" in title_lower or "sol" in title_lower:
            return "SOL"
        elif "xrp" in title_lower or "ripple" in title_lower:
            return "XRP"
        else:
            # Fallback: use first word of title
            return title.split()[0][:8].upper()

    def _init_clob_client(self) -> Optional[ClobClient]:
        """Initialize the CLOB client for order execution."""
        if self.dry_run:
            print("Dry run mode: Skipping CLOB client initialization\n")
            return None

        try:
            private_key = os.getenv("PRIVATE_KEY")
            chain_id = int(os.getenv("POLYGON_CHAIN_ID", "137"))
            host = os.getenv("CLOB_HOST", "https://clob.polymarket.com")

            if not private_key:
                print("Warning: Missing PRIVATE_KEY in .env file")
                return None

            # Initialize client with just private key, host, and chain_id
            client = ClobClient(
                host=host,
                key=private_key,
                chain_id=chain_id,
            )

            # Create or derive API credentials from private key
            # This is REQUIRED for authentication - without it, you get 403 errors
            client.set_api_creds(client.create_or_derive_api_creds())

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
            self.ws_yes = await websockets.connect(
                self.WS_URL, ping_interval=20, ping_timeout=10
            )
            subscribe_msg_yes = {
                "assets_ids": [self.token_id_yes],
                "type": "MARKET",  # Must be uppercase per official docs
            }
            await self.ws_yes.send(json.dumps(subscribe_msg_yes))

            # Connect to NO token
            self.ws_no = await websockets.connect(
                self.WS_URL, ping_interval=20, ping_timeout=10
            )
            subscribe_msg_no = {"assets_ids": [self.token_id_no], "type": "MARKET"}
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

        Expected format from official docs:
        {
            "event_type": "book" or "price_change" or "best_bid_ask",
            "asset_id": "<token_id>",
            "market": "<condition_id>",
            "bids": [{"price": "0.74", "size": "100"}, ...],  # for book event
            "asks": [{"price": "0.76", "size": "100"}, ...],  # for book event
            "best_bid": "0.74",  # for price_change/best_bid_ask
            "best_ask": "0.76",  # for price_change/best_bid_ask
            "timestamp": "123456789000"
        }

        Note: WebSocket sends data as an array [{}], so we extract first element

        Args:
            data: Market data from WebSocket (can be array or dict)
            is_yes_token: True if this is the YES WebSocket connection (used for debugging only)
        """
        try:
            # Skip empty confirmation messages
            if not data:
                return

            # Handle array response - extract first element
            if isinstance(data, list):
                if len(data) == 0:
                    return
                data = data[0]  # Get first element

            # CRITICAL: Determine which token this data is for based on asset_id in the data itself
            # NOT based on which WebSocket connection it came from!
            received_asset_id = data.get("asset_id")
            if not received_asset_id:
                # No asset_id in message - skip
                return

            # Determine if this is YES or NO token data based on actual asset_id
            is_yes_data = received_asset_id == self.token_id_yes
            is_no_data = received_asset_id == self.token_id_no

            if not is_yes_data and not is_no_data:
                # Data for a different market/token - ignore
                return

            # DEBUG: Log if data came from unexpected WebSocket
            ws_label = "YES" if is_yes_token else "NO"
            data_label = "YES" if is_yes_data else "NO"
            if is_yes_token != is_yes_data:
                print(
                    f"[DEBUG] {ws_label} WebSocket received {data_label} token data (asset_id: {received_asset_id[:16]}...)"
                )

            event_type = data.get("event_type")

            # Extract best bid and ask based on event type
            if event_type == "book":
                # Full orderbook snapshot
                asks = data.get("asks", [])
                bids = data.get("bids", [])
                best_ask = extract_best_ask_from_book(asks)
                best_bid = extract_best_bid_from_book(bids)

                if best_ask is not None:
                    if is_yes_data:
                        self.orderbook.best_ask_yes = best_ask
                    else:
                        self.orderbook.best_ask_no = best_ask

                if best_bid is not None:
                    if is_yes_data:
                        self.orderbook.best_bid_yes = best_bid
                    else:
                        self.orderbook.best_bid_no = best_bid

            elif event_type == "price_change":
                # Price update — price_changes array contains data for BOTH tokens
                # We must process ALL elements, not just the one matching received_asset_id
                changes = data.get("price_changes", [])

                # Process all price changes in this event
                for change in changes:
                    change_asset_id = change.get("asset_id")
                    if not change_asset_id:
                        continue

                    # Determine which token this change is for
                    is_yes_change = change_asset_id == self.token_id_yes
                    is_no_change = change_asset_id == self.token_id_no

                    if not is_yes_change and not is_no_change:
                        # Not our market
                        continue

                    # Extract best_ask and best_bid from this change
                    best_ask = change.get("best_ask")
                    best_bid = change.get("best_bid")

                    if best_ask is not None and best_ask != "":
                        try:
                            ask_val = float(best_ask)
                            if is_yes_change:
                                self.orderbook.best_ask_yes = ask_val
                            else:
                                self.orderbook.best_ask_no = ask_val
                        except (ValueError, TypeError):
                            pass

                    if best_bid is not None and best_bid != "":
                        try:
                            bid_val = float(best_bid)
                            if is_yes_change:
                                self.orderbook.best_bid_yes = bid_val
                            else:
                                self.orderbook.best_bid_no = bid_val
                        except (ValueError, TypeError):
                            pass

            elif event_type == "best_bid_ask":
                # Some events may provide top-level best_bid/best_ask
                best_ask = data.get("best_ask")
                best_bid = data.get("best_bid")

                if best_ask is not None and best_ask != "":
                    try:
                        val = float(best_ask)
                        if is_yes_data:
                            self.orderbook.best_ask_yes = val
                        else:
                            self.orderbook.best_ask_no = val
                    except (ValueError, TypeError):
                        pass

                if best_bid is not None and best_bid != "":
                    try:
                        val = float(best_bid)
                        if is_yes_data:
                            self.orderbook.best_bid_yes = val
                        else:
                            self.orderbook.best_bid_no = val
                    except (ValueError, TypeError):
                        pass

            # Update derived values and determine winning side
            self.orderbook.update()
            self._update_winning_side()

            # Get time remaining
            time_remaining = self.get_time_remaining()

            # Log current state (with deduplication to avoid spam from dual WebSocket streams)
            current_time = time_remaining
            current_state = (
                self.orderbook.best_ask_yes,
                self.orderbook.best_ask_no,
                self.winning_side,
            )

            # Only log if:
            # 1. Time changed by at least 0.5s OR
            # 2. State actually changed (winning side or prices) OR
            # 3. We're in final 5 seconds and time changed
            time_changed = abs(current_time - self.last_log_time) >= 0.5
            state_changed = current_state != self.last_logged_state
            in_final_seconds = time_remaining <= 5.0

            should_log = (
                time_changed and (in_final_seconds or state_changed)
            ) or state_changed

            if should_log:
                yes_price = self.orderbook.best_ask_yes or 0.0
                no_price = self.orderbook.best_ask_no or 0.0
                print(
                    f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] "
                    f"[{self.market_name}] "
                    f"Time: {time_remaining:.2f}s | "
                    f"YES ask: ${yes_price:.4f} | "
                    f"NO ask: ${no_price:.4f} | "
                    f"Sum: ${self.orderbook.sum_asks or 0.0:.4f} | "
                    f"Winner: {self.winning_side or 'None'}"
                )
                self.last_log_time = current_time
                self.last_logged_state = current_state

            # Check trigger conditions
            await self.check_trigger(time_remaining)

        except Exception as e:
            print(f"Error processing market update: {e}")

    def _update_winning_side(self) -> None:
        """Update winning side based on current orderbook state."""
        self.winning_side = determine_winning_side(
            self.orderbook.best_ask_yes,
            self.orderbook.best_ask_no,
            self.PRICE_TIE_EPS,
        )

    def _get_winning_token_id(self) -> Optional[str]:
        """Get token ID for the winning side."""
        if self.winning_side is None:
            return None
        return get_winning_token_id(
            self.winning_side, self.token_id_yes, self.token_id_no
        )

    def _get_winning_ask(self) -> Optional[float]:
        """Get best ask price for winning side."""
        if self.winning_side == "YES":
            return self.orderbook.best_ask_yes
        elif self.winning_side == "NO":
            return self.orderbook.best_ask_no
        return None

    async def check_trigger(self, time_remaining: float):
        """
        Check if trigger conditions are met and execute trade if appropriate.

        Trigger conditions:
        1. Time remaining <= 1 second (but > 0)
        2. Winning side is determined (price > 0.50)
        3. Best ask exists for winning side
        4. Best ask is <= $0.99 (at or better than our limit)
        5. Order not already executed
        """
        # Already executed or market closed
        if self.order_executed or time_remaining <= 0:
            return

        # Check time trigger: <= 1 second but > 0
        if time_remaining > self.TRIGGER_THRESHOLD:
            return

        # Check if winning side is determined
        if self.winning_side is None:
            # Don't spam warnings - only log once when we enter trigger window
            if not hasattr(self, "_logged_no_winner"):
                print(
                    f"⚠️  [{self.market_name}] Warning: No winning side determined at {time_remaining:.3f}s remaining"
                )
                self._logged_no_winner = True
            return

        # Get best ask for winning side
        winning_ask = self._get_winning_ask()
        if winning_ask is None:
            if not hasattr(self, "_logged_no_ask"):
                print(
                    f"⚠️  [{self.market_name}] Warning: No ask price available for winning side at {time_remaining:.3f}s remaining"
                )
                self._logged_no_ask = True
            return

        # Check if price is above our target (we can execute at BUY_PRICE or better)
        if winning_ask > self.BUY_PRICE:
            if not hasattr(self, "_logged_price_high"):
                print(
                    f"⚠️  [{self.market_name}] Best ask ${winning_ask:.4f} > ${self.BUY_PRICE} - not worth buying"
                )
                self._logged_price_high = True
            return

        # All conditions met - execute trade!
        print(f"\n{'=' * 80}")
        print(
            f"🎯 [{self.market_name}] TRIGGER ACTIVATED at {time_remaining:.3f}s remaining!"
        )
        print(f"{'=' * 80}")
        print(f"Market: {self.title}")
        print(f"Winning Side: {self.winning_side}")
        print(f"Best Ask: ${winning_ask:.4f}")
        print(f"Target Price: ${self.BUY_PRICE}")
        print(f"Trade Size: ${self.trade_size}")
        print(f"{'=' * 80}\n")

        await self.execute_order()
        self.order_executed = True

    async def execute_order(self) -> None:
        """
        Execute Fill-or-Kill (FOK) order on the winning side.
        In dry run mode, only prints the intended action.
        """
        winning_ask = self._get_winning_ask()
        winning_token_id = self._get_winning_token_id()

        if self.dry_run:
            print(f"{'=' * 80}")
            print(f"🔷 [{self.market_name}] DRY RUN MODE - NO REAL TRADE EXECUTED")
            print(f"{'=' * 80}")
            print("WOULD BUY:")
            print(f"  Market: {self.title}")
            print(f"  Side: {self.winning_side}")
            print(f"  Token ID: {winning_token_id}")
            print(f"  Price: ${self.BUY_PRICE}")
            print(f"  Size: ${self.trade_size}")
            print("  Type: Fill-or-Kill (FOK)")
            print(f"  Current Best Ask: ${winning_ask:.4f}")
            print(f"{'=' * 80}\n")
            return

        # Live trading mode
        if not self.client:
            print(
                f"❌ [{self.market_name}] Error: CLOB client not initialized. Cannot execute order."
            )
            return

        try:
            print(f"{'=' * 80}")
            print(f"🔴 [{self.market_name}] EXECUTING LIVE ORDER...")
            print(f"{'=' * 80}")

            # Calculate order size with API precision: maker max 2 decimals, taker max 4 decimals
            # Use string formatting to guarantee exact decimal places
            price_decimal = Decimal(str(self.BUY_PRICE))
            trade_decimal = Decimal(str(self.trade_size))
            
            # Ensure maker >= $1.00 minimum
            if trade_decimal < Decimal("1.00"):
                print(
                    f"⚠️  [{self.market_name}] Raised trade size to $1.00 (API minimum) from ${self.trade_size}"
                )
                trade_decimal = Decimal("1.00")

            # Calculate tokens needed, round to 4 decimals
            tokens_decimal = (trade_decimal / price_decimal).quantize(
                Decimal("0.0001"), rounding=ROUND_DOWN
            )
            
            # Verify maker_amount has exactly 2 decimals
            maker_check = (price_decimal * tokens_decimal).quantize(
                Decimal("0.01"), rounding=ROUND_DOWN
            )
            
            # Convert to float with explicit rounding to prevent float precision issues
            price_float = round(float(price_decimal), 2)
            tokens_float = round(float(tokens_decimal), 4)
            
            print(f"[DEBUG] Order params: price={price_float}, size={tokens_float}, maker_amount={float(maker_check)}")

            order_args = OrderArgs(
                token_id=winning_token_id,
                price=price_float,
                size=tokens_float,
                side="BUY",
            )

            created_order = await asyncio.to_thread(
                self.client.create_order, order_args
            )
            print(f"✓ Order created: {created_order}")

            response = await asyncio.to_thread(
                self.client.post_order, created_order, OrderType.FOK
            )

            print("✓ Order posted as FOK successfully!")
            print(f"Response: {json.dumps(response, indent=2)}")
            print(f"{'=' * 80}\n")

        except Exception as e:
            print(f"❌ [{self.market_name}] Error executing order: {e}")
            print(f"{'=' * 80}\n")

    async def listen_to_market(self):
        """
        Main loop: Listen to both WebSocket connections and process market updates.
        Runs until market closes or connection is lost.
        """

        async def listen_to_ws(ws, is_yes_token: bool):
            """Listen to a single WebSocket connection."""
            try:
                token_name = "YES" if is_yes_token else "NO"
                token_id = self.token_id_yes if is_yes_token else self.token_id_no
                print(
                    f"[DEBUG] Starting to listen for {token_name} WebSocket messages (token_id: {token_id[:16]}...)"
                )
                message_count = 0
                async for message in ws:
                    message_count += 1

                    # Parse JSON message
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        # Skip non-JSON messages
                        continue

                    # Skip empty confirmation messages
                    if not data or (isinstance(data, list) and len(data) == 0):
                        if message_count <= 2:
                            print(f"[{token_name}] Subscription confirmed")
                        continue

                    # DEBUG: Show first 5 messages with full details
                    if message_count <= 5:
                        print(f"\n[DEBUG {token_name} MSG #{message_count}] FULL:")
                        print(json.dumps(data, indent=2))

                    # Process market update(s)
                    # WebSocket can return an array of updates
                    if isinstance(data, list):
                        for update in data:
                            await self.process_market_update(update, is_yes_token)
                    else:
                        await self.process_market_update(data, is_yes_token)

                    # Check if market closed
                    time_remaining = self.get_time_remaining()
                    if time_remaining <= 0:
                        print(f"\n{'=' * 80}")
                        print(
                            f"⏰ [{self.market_name}] Market closed. Time remaining: {time_remaining:.2f}s"
                        )
                        if not self.order_executed:
                            print("No order was executed.")
                        print(f"{'=' * 80}\n")
                        break

            except websockets.exceptions.ConnectionClosed:
                print(
                    f"\n⚠️  [{self.market_name}] {token_name} WebSocket connection closed"
                )
            except Exception as e:
                print(
                    f"\n❌ [{self.market_name}] Error in {token_name} market listener: {e}"
                )

        # Listen to both WebSockets concurrently
        try:
            await asyncio.gather(
                listen_to_ws(self.ws_yes, True), listen_to_ws(self.ws_no, False)
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
        print(
            "Usage: python hft_trader.py --condition-id <ID> --token-id <ID> --end-time <ISO_TIME> [--live] [--size <SIZE>]"
        )
        print("\nExample (dry run):")
        print(
            f"  python hft_trader.py --condition-id {EXAMPLE_CONDITION_ID} --token-id {EXAMPLE_TOKEN_ID} --end-time 2026-01-24T12:30:00Z"
        )
        print("\nExample (live trading - DANGER!):")
        print(
            f"  python hft_trader.py --condition-id {EXAMPLE_CONDITION_ID} --token-id {EXAMPLE_TOKEN_ID} --end-time 2026-01-24T12:30:00Z --live --size 10"
        )
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
        print(
            "❌ Error: Missing required arguments (--condition-id, --token-id, --end-time)"
        )
        return

    # Create and run trader
    trader = LastSecondTrader(
        condition_id=condition_id,
        token_id=token_id,
        end_time=end_time,
        dry_run=dry_run,
        trade_size=trade_size,
    )

    await trader.run()


if __name__ == "__main__":
    asyncio.run(main())
