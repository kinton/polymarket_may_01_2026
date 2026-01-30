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
    from py_clob_client.clob_types import (
        CreateOrderOptions,
        MarketOrderArgs,
        OrderType,
    )
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
        trader_logger: Optional[Any] = None,
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
        self.logger = trader_logger

        # Extract short market name for logging (e.g. "BTC", "ETH", "SOL")
        self.market_name = self._extract_market_name(title)

        # Market state
        self.orderbook = OrderBook()
        self.winning_side: Optional[str] = None  # "YES" or "NO"
        self.order_executed = False
        self.ws = None

        # Track last log time to avoid spam
        self.last_log_time = 0.0
        self.last_logged_state = None

        # Initialize CLOB client
        load_dotenv()
        self.client = self._init_clob_client()

        # Log init
        mode = "DRY RUN" if self.dry_run else "🔴 LIVE 🔴"
        self._log(
            f"[{self.market_name}] Trader initialized | {mode} | ${self.trade_size} @ ${self.BUY_PRICE}"
        )

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

    def _log(self, message: str) -> None:
        """Log message to both console and file logger."""
        print(message)
        if self.logger:
            self.logger.info(message)

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
                self._log("⚠️ Missing PRIVATE_KEY in .env")
                return None

            client = ClobClient(host=host, key=private_key, chain_id=chain_id)

            # Derive API credentials from private key (required for auth)
            api_creds = client.create_or_derive_api_creds()
            client.set_api_creds(api_creds)

            self._log(f"✓ CLOB client initialized ({host})")
            return client

        except Exception as e:
            self._log(f"❌ CLOB init failed: {e}")
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
            # Single WebSocket connection for both tokens
            self.ws = await websockets.connect(
                self.WS_URL, ping_interval=20, ping_timeout=10
            )
            subscribe_msg = {
                "assets_ids": [self.token_id_yes, self.token_id_no],
                "type": "MARKET",
            }
            await self.ws.send(json.dumps(subscribe_msg))

            self._log("✓ WebSocket connected, subscribed to YES+NO tokens")
            return True

        except Exception as e:
            self._log(f"❌ WebSocket connection failed: {e}")
            return False

    async def process_market_update(self, data: Dict[str, Any]):
        """
        Process incoming market data from WebSocket.

        Args:
            data: Market data from WebSocket (can be array or dict)
        """
        try:
            if not data:
                return

            # Handle array response - extract first element
            if isinstance(data, list):
                if len(data) == 0:
                    return
                data = data[0]  # type: ignore
            if not isinstance(data, dict):
                return

            # Determine which token this data is for
            received_asset_id = data.get("asset_id")
            if not received_asset_id:
                return

            is_yes_data = received_asset_id == self.token_id_yes
            is_no_data = received_asset_id == self.token_id_no

            if not is_yes_data and not is_no_data:
                return

            event_type = data.get("event_type")

            # Extract best bid and ask based on event type
            if event_type == "book":
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
                # price_changes array contains data for BOTH tokens
                changes = data.get("price_changes", [])

                for change in changes:
                    change_asset_id = change.get("asset_id")
                    if not change_asset_id:
                        continue

                    is_yes_change = change_asset_id == self.token_id_yes
                    is_no_change = change_asset_id == self.token_id_no

                    if not is_yes_change and not is_no_change:
                        continue

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
                # Show both bid and ask for better diagnosis
                yes_ask = self.orderbook.best_ask_yes
                yes_bid = self.orderbook.best_bid_yes
                no_ask = self.orderbook.best_ask_no
                no_bid = self.orderbook.best_bid_no
                
                # Format prices: show "-" if None
                def fmt(p): return f"${p:.2f}" if p is not None else "-"
                
                self._log(
                    f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] "
                    f"[{self.market_name}] "
                    f"Time: {time_remaining:.2f}s | "
                    f"YES bid/ask: {fmt(yes_bid)}/{fmt(yes_ask)} | "
                    f"NO bid/ask: {fmt(no_bid)}/{fmt(no_ask)} | "
                    f"Winner: {self.winning_side or 'None'}"
                )
                self.last_log_time = current_time
                self.last_logged_state = current_state

            # Check trigger conditions
            await self.check_trigger(time_remaining)

        except Exception as e:
            self._log(f"Error processing market update: {e}")

    def _update_winning_side(self) -> None:
        """Update winning side based on current orderbook state."""
        self.winning_side = determine_winning_side(
            best_bid_yes=self.orderbook.best_bid_yes,
            best_bid_no=self.orderbook.best_bid_no,
            best_ask_yes=self.orderbook.best_ask_yes,
            best_ask_no=self.orderbook.best_ask_no,
            tie_epsilon=self.PRICE_TIE_EPS,
        )

    def _get_winning_token_id(self) -> Optional[str]:
        """Get token ID for the winning side."""
        if self.winning_side is None:
            return None
        return get_winning_token_id(
            self.winning_side, self.token_id_yes, self.token_id_no
        )

    def _get_winning_ask(self) -> Optional[float]:
        """
        Get best ask price for winning side.
        
        If direct ask is not available, compute implied price from opposite side:
        - If NO ask = 0.99, implied YES ask ≈ 0.01 (but we can't buy at that)
        - We need actual ask to buy, so return None if no direct ask
        """
        if self.winning_side == "YES":
            # Direct ask available
            if self.orderbook.best_ask_yes is not None:
                return self.orderbook.best_ask_yes
            # Try implied from NO bid: if someone bids 0.99 for NO, YES should be ~0.01
            # But we need someone SELLING YES, not implied price
            return None
        elif self.winning_side == "NO":
            if self.orderbook.best_ask_no is not None:
                return self.orderbook.best_ask_no
            return None
        return None
    
    def _get_winning_bid(self) -> Optional[float]:
        """Get best bid price for winning side (what buyers are willing to pay)."""
        if self.winning_side == "YES":
            return self.orderbook.best_bid_yes
        elif self.winning_side == "NO":
            return self.orderbook.best_bid_no
        return None

    async def check_trigger(self, time_remaining: float):
        """
        Check if trigger conditions are met and execute trade if appropriate.

        Trigger conditions:
        1. Time remaining <= TRIGGER_THRESHOLD seconds (but > 0)
        2. Winning side is determined (higher ask price)
        3. Best ask <= $0.99 (at or better than our limit)
        4. Order not already executed
        """
        if self.order_executed or time_remaining <= 0:
            return

        if time_remaining > self.TRIGGER_THRESHOLD:
            return

        if self.winning_side is None:
            if not hasattr(self, "_logged_no_winner"):
                self._log(
                    f"⚠️  [{self.market_name}] No winning side at {time_remaining:.3f}s"
                )
                self._logged_no_winner = True
            return

        winning_ask = self._get_winning_ask()
        if winning_ask is None:
            if not hasattr(self, "_logged_no_ask"):
                self._log(
                    f"⚠️  [{self.market_name}] No ask price at {time_remaining:.3f}s"
                )
                self._logged_no_ask = True
            return

        if winning_ask > self.BUY_PRICE + self.PRICE_TIE_EPS:
            if not hasattr(self, "_logged_price_high"):
                self._log(
                    f"⚠️  [{self.market_name}] Ask ${winning_ask:.4f} > ${self.BUY_PRICE}"
                )
                self._logged_price_high = True
            return

        # All conditions met - execute trade!
        self._log(
            f"🎯 [{self.market_name}] TRIGGER at {time_remaining:.3f}s! {self.winning_side} @ ${winning_ask:.4f}"
        )

        await self.execute_order()

    async def verify_order(self, order_id: str) -> None:
        """
        Verify order status after submission by querying the API.
        """
        if not self.client:
            return

        self._log(f"🔎 [{self.market_name}] Verifying order {order_id}...")
        try:
            # Wait briefly for propagation
            await asyncio.sleep(0.5)

            # Client.get_order returns a dictionary
            order_data = await asyncio.to_thread(self.client.get_order, order_id)

            # Exact key per py-clob-client documentation
            status = order_data.get("status", "unknown").lower()

            if status == "matched":
                self._log(
                    f"✅ [{self.market_name}] Order {order_id} CONFIRMED FILLED (Status: {status})"
                )
            elif status in ["canceled", "killed"]:
                self._log(
                    f"⚠️  [{self.market_name}] Order {order_id} WAS KILLED/CANCELED (Status: {status})"
                )
            else:
                self._log(f"ℹ️  [{self.market_name}] Order {order_id} status: {status}")

        except Exception as e:
            self._log(f"⚠️  [{self.market_name}] Verification failed: {e}")

    def _calculate_valid_size(self, price: float, target_dollars: float) -> float:
        """
        Calculate a valid order size that satisfies Polymarket's precision requirements.

        NOTE: This function is kept for reference but we now use market orders instead,
        which take amount (dollars) instead of size (tokens), avoiding the precision issue.
        """
        from math import gcd

        price_cents = int(round(price * 100))
        raw_size = target_dollars / price
        size_cents = int(raw_size * 100)

        divisor = 10000 // gcd(price_cents, 10000)

        if size_cents % divisor != 0:
            size_cents = ((size_cents // divisor) + 1) * divisor

        min_size_cents = int(100 / price) + 1
        if min_size_cents % divisor != 0:
            min_size_cents = ((min_size_cents // divisor) + 1) * divisor

        size_cents = max(size_cents, min_size_cents)
        return size_cents / 100.0

    async def execute_order(self) -> None:
        """
        Execute Fill-or-Kill (FOK) market order on the winning side.

        Uses MarketOrderArgs which takes 'amount' (dollars to spend) instead of 'size' (tokens).
        This avoids the precision issue where price × size must have ≤2 decimal places.

        For market orders:
        - amount = dollars to spend (must have ≤2 decimal places)
        - price = worst acceptable price (limit)
        - The API calculates the appropriate token quantity
        """
        winning_ask = self._get_winning_ask()
        winning_token_id = self._get_winning_token_id()

        if not winning_token_id:
            self._log(f"❌ [{self.market_name}] Error: No winning token ID available")
            return

        # For market orders: specify amount (dollars) with 2 decimal places
        # Ensure minimum $1.00
        amount = max(round(self.trade_size, 2), 1.00)
        price = round(self.BUY_PRICE, 2)  # Worst price we'll accept

        if self.dry_run:
            self._log(f"🔷 [{self.market_name}] DRY RUN - WOULD BUY:")
            self._log(
                f"  Side: {self.winning_side}, Amount: ${amount}, Max Price: ${price}"
            )
            self._log(f"  Best Ask: ${winning_ask:.4f}, Type: FOK MARKET")
            self.order_executed = True
            return

        if not self.client:
            self._log(f"❌ [{self.market_name}] CLOB client not initialized")
            return

        try:
            self._log(
                f"🔴 [{self.market_name}] MARKET ORDER: {self.winning_side} ${amount} @ max ${price}"
            )

            # Use MarketOrderArgs - specifies amount (dollars) not size (tokens)
            order_args = MarketOrderArgs(
                token_id=winning_token_id,
                amount=amount,  # Dollars to spend
                price=price,  # Maximum price we'll accept
                side="BUY",
            )

            # Create market order
            created_order = await asyncio.to_thread(
                self.client.create_market_order,
                order_args,
                CreateOrderOptions(tick_size="0.01", neg_risk=False),
            )
            self._log(f"✓ [{self.market_name}] Market order created")

            # Post as Fill-or-Kill
            response: dict[str, Any] = await asyncio.to_thread(
                self.client.post_order,
                created_order,
                OrderType.FOK,
            )

            self._log(f"✓ [{self.market_name}] FOK order posted: {response}")

            # Extract and verify
            try:
                order_id = response["orderID"]
                await self.verify_order(str(order_id))
            except KeyError:
                self._log(
                    f"⚠️  [{self.market_name}] 'orderID' missing in response: {response}"
                )
            except Exception as e:
                self._log(f"⚠️  [{self.market_name}] Verification setup failed: {e}")

            # Set flag only after successful post
            self.order_executed = True

        except Exception as e:
            error_str = str(e)
            self._log(f"❌ [{self.market_name}] Order failed: {error_str}")

            if "403" in error_str:
                self._log("  → Possible rate limit. Wait 5-10 min or switch IP.")

    async def listen_to_market(self):
        """Listen to WebSocket and process market updates until market closes."""
        try:
            async for message in self.ws:
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    continue

                if not data or (isinstance(data, list) and len(data) == 0):
                    continue

                # Process market update(s)
                if isinstance(data, list):
                    for update in data:
                        await self.process_market_update(update)
                else:
                    await self.process_market_update(data)

                # Check if market closed
                if self.get_time_remaining() <= 0:
                    self._log(f"⏰ [{self.market_name}] Market closed")
                    break

        except websockets.exceptions.ConnectionClosed:
            self._log(f"⚠️  [{self.market_name}] WebSocket connection closed")
        except Exception as e:
            self._log(f"❌ [{self.market_name}] Error in market listener: {e}")

    async def _trigger_check_loop(self):
        """Periodically check trigger conditions every 5 seconds.

        This ensures we don't miss the trigger window even if WebSocket
        is silent (no orderbook changes).
        """
        while True:
            time_remaining = self.get_time_remaining()

            if time_remaining <= 0:
                break

            # Check trigger if we have any price data
            if (
                self.orderbook.best_ask_yes is not None
                or self.orderbook.best_ask_no is not None
            ):
                await self.check_trigger(time_remaining)

            await asyncio.sleep(5)  # Check every 5 seconds

    async def run(self):
        """Main entry point: Connect and start trading."""
        try:
            connected = await self.connect_websocket()
            if not connected:
                self._log("Failed to connect to WebSocket. Exiting.")
                return

            # Run both WebSocket listener and periodic trigger check concurrently
            await asyncio.gather(
                self.listen_to_market(),
                self._trigger_check_loop(),
            )

        except KeyboardInterrupt:
            self._log("⚠️  Interrupted by user. Shutting down...")
        finally:
            if self.ws:
                await self.ws.close()
            self._log("✓ Trader shut down cleanly")
