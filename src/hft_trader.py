"""
High-Frequency Trading Script for Polymarket 15-minute markets.

This script connects to Polymarket's CLOB WebSocket to stream real-time market data
and executes a late-window trading strategy (final 120s by default).

Strategy:
- Monitor Level 1 order book (best bid/ask) via WebSocket
- Track the winning side (price > 0.50)
- When time remaining <= TRIGGER_THRESHOLD seconds (default 120s, but > 0):
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
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from py_clob_client.client import ClobClient

import aiohttp
import websockets
from dotenv import load_dotenv

from src.alerts import (
    AlertManager,
    SlackAlertSender,
    TelegramAlertSender,
)
from src.clob_types import (
    CLOB_WS_URL,
    MAX_ENTRY_PRICE,
    MIN_ORDERBOOK_SIZE_USD,
    MIN_TRADE_USDC,
    PRICE_TIE_EPS,
    TRIGGER_THRESHOLD,
    OrderBook,
)
# Lazy-imported in __init__ to avoid circular: CONVERGENCE_MIN_CHEAP_PRICE, etc.
from src.market_parser import (
    determine_winning_side,
    extract_best_ask_with_size_from_book,
    extract_best_bid_with_size_from_book,
    get_winning_token_id,
)
from src.updown_prices import EventPageClient, RtdsClient
from src.trading.alert_dispatcher import AlertDispatcher
from src.trading.oracle_guard_manager import OracleGuardManager
from src.trading.order_execution_manager import OrderExecutionManager
from src.trading.position_manager import PositionManager
from src.trading.risk_manager import RiskManager
from src.trading.stop_loss_manager import StopLossManager
from src.trading.dry_run_replay import EventRecorder
from src.trading.dry_run_simulator import DryRunSimulator
from src.trading.orderbook_ws import OrderbookWS
from src.trading.orderbook_ws_adapter import OrderbookWSAdapter
from src.trading.websocket_client import WebSocketClient
from src.trading.orderbook_tracker import OrderbookTracker
from strategies import discover_strategies, load_strategy
from strategies.base import BaseStrategy, MarketTick, Signal

try:
    from py_clob_client.client import ClobClient
except ImportError:
    print("Error: py-clob-client not installed. Run: uv pip install py-clob-client")
    exit(1)


class LastSecondTrader:
    """
    High-frequency trader that monitors market data via WebSocket
    and executes trades in the final window before market close.
    """

    # Configuration
    DRY_RUN = True  # Safety: Default to dry run mode
    TRADE_SIZE = 1  # Default trade size in dollars
    TRIGGER_THRESHOLD = TRIGGER_THRESHOLD
    PRICE_THRESHOLD = 0.50  # Winning side threshold
    PRICE_TIE_EPS = PRICE_TIE_EPS

    WS_URL = CLOB_WS_URL
    WS_STALE_SECONDS = 2.0  # Require fresh WS data for trigger checks
    MIN_TRADE_USDC = MIN_TRADE_USDC

    def __init__(
        self,
        condition_id: str,
        token_id_yes: str,
        token_id_no: str,
        end_time: datetime,
        dry_run: bool = True,
        trade_size: float = 1.0,
        title: str | None = None,
        slug: str | None = None,
        trader_logger: logging.Logger | None = None,
        oracle_enabled: bool = False,
        oracle_guard_enabled: bool = True,
        oracle_min_points: int = 4,
        oracle_window_s: float = 60.0,
        book_log_every_s: float = 1.0,
        book_log_every_s_final: float = 0.5,
        replay_dir: str | None = None,
        replay_book_throttle_s: float = 0.5,
        use_orderbook_ws: bool | None = None,
        orderbook_ws_poll_interval: float = 0.1,
        trade_db: Any | None = None,
        convergence_enabled: bool | None = None,
        strategy: str = "convergence",
        strategy_version: str = "v1",
        mode: str = "test",
        min_cheap_price: float = 0.0,
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
        try:
            self.condition_id = condition_id
            self.token_id_yes = token_id_yes
            self.token_id_no = token_id_no
            self.end_time = end_time
            self.strategy = strategy
            self.strategy_version = strategy_version
            self.mode = mode
            self.dry_run = dry_run
            self.trade_size = trade_size
            self.title = title
            self.slug = slug
            self._min_cheap_price_override = min_cheap_price
            self.logger = trader_logger

            # Extract short market name for logging
            self.market_name = self._extract_market_name(title)

            # Market state
            self.orderbook = OrderBook()
            self.winning_side: str | None = None

            # Modular components
            self._ob_tracker = OrderbookTracker(
                orderbook=self.orderbook,
                token_id_yes=token_id_yes,
                token_id_no=token_id_no,
                tie_epsilon=self.PRICE_TIE_EPS,
            )
            self._ws_client = WebSocketClient(
                token_id_yes=token_id_yes,
                token_id_no=token_id_no,
                market_name=self.market_name,
                logger=trader_logger,
            )

            # Strategy plugin system
            from src.clob_types import (
                CONVERGENCE_ENABLED,
                CONVERGENCE_THRESHOLD_PCT,
                CONVERGENCE_MIN_SKEW,
                CONVERGENCE_MAX_CHEAP_PRICE,
                CONVERGENCE_MIN_CHEAP_PRICE,
                CONVERGENCE_WINDOW_START_S,
                CONVERGENCE_WINDOW_END_S,
                CONVERGENCE_MIN_OBSERVATIONS,
                CONVERGENCE_MIN_CONVERGENCE_RATE,
                CONVERGENCE_DISABLE_STOP_LOSS,
            )
            _conv_enabled = convergence_enabled if convergence_enabled is not None else CONVERGENCE_ENABLED
            # CLI --min-price overrides config if non-zero
            _effective_min_cheap = min_cheap_price if min_cheap_price > 0 else CONVERGENCE_MIN_CHEAP_PRICE
            if _conv_enabled and oracle_enabled:
                discover_strategies()
                self.strategy_instance: BaseStrategy | None = load_strategy(
                    name=strategy,
                    version=strategy_version,
                    threshold_pct=CONVERGENCE_THRESHOLD_PCT,
                    min_skew=CONVERGENCE_MIN_SKEW,
                    max_cheap_price=CONVERGENCE_MAX_CHEAP_PRICE,
                    min_cheap_price=_effective_min_cheap,
                    window_start_s=CONVERGENCE_WINDOW_START_S,
                    window_end_s=CONVERGENCE_WINDOW_END_S,
                    min_observations=CONVERGENCE_MIN_OBSERVATIONS,
                    min_convergence_rate=CONVERGENCE_MIN_CONVERGENCE_RATE,
                    logger=trader_logger,
                )
                self._convergence_disable_stop_loss = CONVERGENCE_DISABLE_STOP_LOSS
            else:
                self.strategy_instance = None
                self._convergence_disable_stop_loss = False
            self._strategy_trade = False  # flag: current position is from strategy



            # Shutdown flag
            self._shutting_down = False

            # Log throttling
            self.book_log_every_s = max(0.0, float(book_log_every_s))
            self.book_log_every_s_final = max(0.0, float(book_log_every_s_final))
            self._last_book_log_ts = 0.0
            self._last_logged_winner: str | None = None

            # Warning tracking
            self._logged_warnings = set()
            self._trigger_lock = asyncio.Lock()

            # In-memory stats for market lifecycle (no DB writes)
            self._market_stats: dict = {
                "ticks_total": 0,
                "skip_reasons": Counter(),
            }
            self._recorded_skip_guards: set[str] = set()  # one-shot skip recording
            self.last_ws_update_ts = 0.0
            self._last_stale_log_ts = 0.0
            self._planned_trade_side: str | None = None
            self.ws: websockets.WebSocketClientProtocol | None = None

            # Initialize managers
            load_dotenv()
            self.client = self._init_clob_client()

            # Oracle guard manager
            end_iso = self.end_time.strftime("%Y-%m-%dT%H:%M:%SZ")
            self.oracle_guard = OracleGuardManager(
                title=title or "Unknown",
                market_name=self.market_name,
                end_time=end_iso,
                enabled=oracle_enabled,
                guard_enabled=oracle_guard_enabled,
                min_points=oracle_min_points,
                window_s=oracle_window_s,
            )

            # Position, stop-loss, and risk managers
            self.position_manager = PositionManager(
                logger=self.logger,
                condition_id=condition_id,
            )
            # Restore position from disk if crash recovery data exists
            if self.position_manager.restore():
                self._log(
                    f"[{self.market_name}] ♻️ Position restored: "
                    f"{self.position_manager.position_side} @ "
                    f"${self.position_manager.entry_price:.4f}"
                )

            self.stop_loss_manager = StopLossManager(
                position_manager=self.position_manager,
                logger=self.logger,
            )
            self.risk_manager = RiskManager(
                client=self.client,
                market_name=self.market_name,
                trade_size=self.trade_size,
                logger=self.logger,
            )

            # Initialize trade_db before OrderExecutionManager (it references self._trade_db)
            self._trade_db = trade_db

            # Order execution manager
            self.order_execution = OrderExecutionManager(
                client=self.client,
                market_name=self.market_name,
                condition_id=condition_id,
                token_id_yes=token_id_yes,
                token_id_no=token_id_no,
                dry_run=dry_run,
                trade_size=trade_size,
                logger=self.logger,
                position_manager=self.position_manager,
                alert_dispatcher=None,  # Will be set after init
                risk_manager=self.risk_manager,
                trade_db=self._trade_db,
            )

            # Alert dispatcher
            self.alert_dispatcher = self._init_alert_dispatcher()
            self.order_execution.alert_dispatcher = self.alert_dispatcher

            # Set stop-loss sell callback
            self.stop_loss_manager.set_sell_callback(self.execute_sell)

            # Event recorder for dry-run replay
            self._replay_book_throttle_s = replay_book_throttle_s
            self._last_replay_book_ts = 0.0
            if replay_dir is not None:
                self.event_recorder: EventRecorder | None = EventRecorder(
                    replay_dir=replay_dir,
                    market_name=self.market_name,
                    condition_id=condition_id,
                )
                self._log(f"[{self.market_name}] EventRecorder enabled → {self.event_recorder.filepath}")
            else:
                self.event_recorder = None

            # Dry-run simulator (SQLite-backed decision recording)
            if trade_db is not None:
                self.dry_run_sim: DryRunSimulator | None = DryRunSimulator(
                    db=trade_db,
                    market_name=self.market_name,
                    condition_id=condition_id,
                    dry_run=dry_run,
                    strategy=self.strategy,
                    strategy_version=self.strategy_version,
                    mode=self.mode,
                )
                self._log(f"[{self.market_name}] DryRunSimulator enabled (SQLite)")
            else:
                self.dry_run_sim = None

            # OrderbookWS adapter (optional, enabled via USE_ORDERBOOK_WS env or param)
            if use_orderbook_ws is None:
                use_orderbook_ws = os.getenv("USE_ORDERBOOK_WS", "").strip().lower() in ("1", "true", "yes")
            self.use_orderbook_ws = use_orderbook_ws
            self._orderbook_ws_adapter: OrderbookWSAdapter | None = None
            if self.use_orderbook_ws:
                ws_client = OrderbookWS()
                self._orderbook_ws_adapter = OrderbookWSAdapter(
                    ws=ws_client,
                    orderbook=self.orderbook,
                    token_id_yes=self.token_id_yes,
                    token_id_no=self.token_id_no,
                    poll_interval=orderbook_ws_poll_interval,
                )
                self._log(f"[{self.market_name}] OrderbookWS adapter enabled (poll={orderbook_ws_poll_interval}s)")

            # Log init
            mode = "DRY RUN" if self.dry_run else "🔴 LIVE 🔴"
            self._log(
                f"[{self.market_name}] Trader initialized | {mode} | ${self.trade_size} | "
                f"strategy={'on (' + self.strategy_instance.name + '/' + self.strategy_instance.version + ')' if self.strategy_instance else 'off'}"
            )
            if self.oracle_guard.enabled:
                sym = self.oracle_guard.symbol or "unknown"
                parts = [f"oracle_tracking=on ({sym})"]
                if self.oracle_guard.guard_enabled:
                    parts.append(
                        f"guard=on (stale<={self.oracle_guard.max_stale_s}s, "
                        + f"min_pts>={self.oracle_guard.min_points}, "
                        + f"max_vol<={self.oracle_guard.max_vol_pct}, "
                        + f"|z|>={self.oracle_guard.min_abs_z})"
                    )
                else:
                    parts.append("guard=off")
                self._log(f"[{self.market_name}] " + " | ".join(parts))
            else:
                self._log(f"[{self.market_name}] oracle_tracking=off")

            # [LIFECYCLE] Trader initialized successfully
            self._log(f"[TRADER] [{self.market_name}] Trader initialized")

        except Exception as e:
            self._log(f"[TRADER] [{self.market_name}] ERROR during initialization: {e}")
            self._log(traceback.format_exc())
            # TODO: Add alert for initialization failures
            # if self.alert_dispatcher and self.alert_dispatcher.is_enabled():
            #     await self.alert_dispatcher.send_critical_alert(f"[{self.market_name}] CRITICAL: Initialization failed - {e}")
            raise

    async def graceful_shutdown(self, reason: str = "Unknown"):
        """
        Perform graceful shutdown of the trader.

        This method:
        1. Logs the shutdown reason
        2. Saves current state (positions, orders)
        3. Closes WebSocket connections cleanly
        4. Cancels all pending tasks
        5. Closes client sessions if any

        Args:
            reason: The reason for shutdown (e.g., "KeyboardInterrupt", "SIGTERM")
        """
        if self._shutting_down:
            self._log(f"[TRADER] [{self.market_name}] Shutdown already in progress")
            return

        self._shutting_down = True
        self._log(f"[TRADER] [{self.market_name}] Graceful shutdown initiated: {reason}")

        # Save state before shutdown
        try:
            # Persist position state to disk for crash recovery
            if self.position_manager and self.position_manager.is_open:
                self.position_manager._persist()
                position_info = {
                    "side": self.position_manager.position_side,
                    "entry_price": self.position_manager.entry_price,
                    "trailing_stop": self.position_manager.trailing_stop_price,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                self._log(f"[TRADER] [{self.market_name}] Position state persisted: {position_info}")

            # Log execution state
            if self.order_execution:
                execution_state = {
                    "executed": self.order_execution.is_executed(),
                    "in_progress": self.order_execution.is_in_progress(),
                    "attempts": self.order_execution.get_attempts()
                }
                self._log(f"[TRADER] [{self.market_name}] Saving execution state: {execution_state}")
        except Exception as e:
            self._log(f"[TRADER] [{self.market_name}] ERROR saving state: {e}")
            self._log(traceback.format_exc())

        # Stop OrderbookWS adapter
        try:
            if self._orderbook_ws_adapter is not None:
                await self._orderbook_ws_adapter.stop()
                self._log(f"[TRADER] [{self.market_name}] OrderbookWS adapter stopped")
        except Exception as e:
            self._log(f"[TRADER] [{self.market_name}] ERROR stopping OrderbookWS adapter: {e}")

        # Close event recorder
        try:
            if self.event_recorder is not None:
                self.event_recorder.close()
                self._log(f"[TRADER] [{self.market_name}] EventRecorder closed ({self.event_recorder.event_count} events)")
                self.event_recorder = None
        except Exception as e:
            self._log(f"[TRADER] [{self.market_name}] ERROR closing EventRecorder: {e}")

        # Close WebSocket connection
        if self.ws is not None:
            self._log(f"[TRADER] [{self.market_name}] Closing WebSocket connection...")
            try:
                await asyncio.wait_for(self.ws.close(), timeout=5.0)
                self._log(f"[TRADER] [{self.market_name}] WebSocket closed successfully")
            except asyncio.TimeoutError:
                self._log(f"[TRADER] [{self.market_name}] WebSocket close timeout")
            except Exception as e:
                self._log(f"[TRADER] [{self.market_name}] ERROR closing WebSocket: {e}")

        # Close client session if applicable
        try:
            if self.client and hasattr(self.client, 'close'):
                self._log(f"[TRADER] [{self.market_name}] Closing CLOB client session...")
                await asyncio.to_thread(self.client.close)
                self._log(f"[TRADER] [{self.market_name}] CLOB client session closed")
        except Exception as e:
            self._log(f"[TRADER] [{self.market_name}] ERROR closing client session: {e}")

        # Cancel all pending tasks in current event loop
        try:
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            if tasks:
                self._log(f"[TRADER] [{self.market_name}] Cancelling {len(tasks)} pending task(s)...")
                for task in tasks:
                    if not task.done():
                        task.cancel()
                # Wait for tasks to be cancelled
                await asyncio.gather(*tasks, return_exceptions=True)
                self._log(f"[TRADER] [{self.market_name}] All tasks cancelled")
        except Exception as e:
            self._log(f"[TRADER] [{self.market_name}] ERROR cancelling tasks: {e}")

        # [LIFECYCLE] Trader stopped
        self._log(f"[TRADER] [{self.market_name}] Trader stopped ({reason})")

    async def stop_trading(self):
        """
        Stop trading and cleanup resources.

        This is called when the trader needs to stop before the market closes.
        """
        self._log(f"[TRADER] [{self.market_name}] Stopping trading...")
        await self.graceful_shutdown("Manual stop")

    def _extract_market_name(self, title: str | None) -> str:
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

    def _get_ask_for_side(self, side: str) -> float | None:
        self._ob_tracker.orderbook = self.orderbook
        return self._ob_tracker.get_ask_for_side(side)

    def _get_bid_for_side(self, side: str) -> float | None:
        self._ob_tracker.orderbook = self.orderbook
        return self._ob_tracker.get_bid_for_side(side)

    def check_orderbook_liquidity(self) -> bool:
        """Check if orderbook has sufficient liquidity. Delegates to OrderbookTracker."""
        self._ob_tracker.orderbook = self.orderbook
        return self._ob_tracker.check_liquidity()

    def _log(self, message: str) -> None:
        """Log message to both console and file logger."""
        if self.logger:
            self.logger.info(message)
            return
        print(message)

    # Backward compatibility properties for tests
    @property
    def entry_price(self) -> float | None:
        """Get entry price from position manager."""
        return self.position_manager.entry_price

    @entry_price.setter
    def entry_price(self, value: float | None) -> None:
        """Set entry price in position manager."""
        self.position_manager.entry_price = value

    @property
    def position_side(self) -> str | None:
        """Get position side from position manager."""
        return self.position_manager.position_side

    @position_side.setter
    def position_side(self, value: str | None) -> None:
        """Set position side in position manager."""
        self.position_manager.position_side = value

    @property
    def position_open(self) -> bool:
        """Get position open status from position manager."""
        return self.position_manager.is_open

    @position_open.setter
    def position_open(self, value: bool) -> None:
        """Set position open status in position manager."""
        self.position_manager.position_open = value

    @property
    def trailing_stop_price(self) -> float | None:
        """Get trailing stop price from position manager."""
        return self.position_manager.trailing_stop_price

    @trailing_stop_price.setter
    def trailing_stop_price(self, value: float | None) -> None:
        """Set trailing stop price in position manager."""
        self.position_manager.trailing_stop_price = value

    def _init_clob_client(self) -> ClobClient | None:
        """Initialize the CLOB client for order execution."""
        if self.dry_run:
            self._log("Dry run mode: Skipping CLOB client initialization")
            return None

        try:
            private_key = os.getenv("PRIVATE_KEY")
            chain_id = int(os.getenv("POLYGON_CHAIN_ID", "137"))
            host = os.getenv("CLOB_HOST", "https://clob.polymarket.com")
            funder = os.getenv("POLYMARKET_PROXY_ADDRESS")

            if not private_key:
                self._log("⚠️ Missing PRIVATE_KEY in .env")
                return None

            if "clob.polymarket.com" not in host:
                self._log(
                    "⚠️ CLOB_HOST should be https://clob.polymarket.com (overriding)"
                )
                host = "https://clob.polymarket.com"

            signature_type = 2 if funder else 0
            client = ClobClient(
                host=host,
                key=private_key,
                chain_id=chain_id,
                signature_type=signature_type,
                funder=funder or "",
            )

            api_creds = client.create_or_derive_api_creds()
            client.set_api_creds(api_creds)

            self._log(f"✓ CLOB client initialized ({host})")
            if funder:
                self._log(f"  Proxy wallet: {funder}")
            return client

        except Exception as e:
            self._log(f"❌ CLOB init failed: {e}")
            return None

    def _init_alert_dispatcher(self) -> AlertDispatcher:
        """Initialize alert dispatcher with configured channels."""
        telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        slack_webhook_url = os.getenv("SLACK_WEBHOOK_URL")

        if not telegram_bot_token or not telegram_chat_id:
            return AlertDispatcher(alert_manager=None)

        telegram_sender = TelegramAlertSender(telegram_bot_token, telegram_chat_id)
        slack_sender = (
            SlackAlertSender(slack_webhook_url) if slack_webhook_url else None
        )
        alert_manager = AlertManager(telegram=telegram_sender, slack=slack_sender)

        self._log(
            f"[{self.market_name}] Alerts enabled: Telegram{' + Slack' if slack_sender else ''}"
        )
        return AlertDispatcher(alert_manager=alert_manager)

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
        result = await self._ws_client.connect()
        self.ws = self._ws_client.ws
        return result

    async def process_market_update(self, data: dict[str, Any]):
        """
        Process incoming market data from WebSocket.

        Args:
            data: Market data from WebSocket (can be array or dict)
        """
        try:
            if not data:
                return

            if isinstance(data, list) and len(data) > 0:
                data = data[0]  # type: ignore[arg-type]

            if not isinstance(data, dict):
                return

            received_asset_id = data.get("asset_id")
            if not received_asset_id:
                return

            is_yes_data = received_asset_id == self.token_id_yes
            is_no_data = received_asset_id == self.token_id_no

            if not is_yes_data and not is_no_data:
                return

            event_type = data.get("event_type")

            if event_type == "book":
                asks = data.get("asks", [])
                bids = data.get("bids", [])
                best_ask, best_ask_size = extract_best_ask_with_size_from_book(asks)
                best_bid, best_bid_size = extract_best_bid_with_size_from_book(bids)

                if best_ask is not None and 0.001 <= best_ask <= 0.999:
                    if is_yes_data:
                        self.orderbook.best_ask_yes = best_ask
                        self.orderbook.best_ask_yes_size = best_ask_size
                    else:
                        self.orderbook.best_ask_no = best_ask
                        self.orderbook.best_ask_no_size = best_ask_size

                if best_bid is not None and 0.001 <= best_bid <= 0.999:
                    if is_yes_data:
                        self.orderbook.best_bid_yes = best_bid
                        self.orderbook.best_bid_yes_size = best_bid_size
                    else:
                        self.orderbook.best_bid_no = best_bid
                        self.orderbook.best_bid_no_size = best_bid_size

            elif event_type == "price_change":
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
                            if 0.001 <= ask_val <= 0.999:
                                if is_yes_change:
                                    self.orderbook.best_ask_yes = ask_val
                                    self.orderbook.best_ask_yes_size = None
                                else:
                                    self.orderbook.best_ask_no = ask_val
                                    self.orderbook.best_ask_no_size = None
                        except (ValueError, TypeError):
                            pass

                    if best_bid is not None and best_bid != "":
                        try:
                            bid_val = float(best_bid)
                            if 0.001 <= bid_val <= 0.999:
                                if is_yes_change:
                                    self.orderbook.best_bid_yes = bid_val
                                    self.orderbook.best_bid_yes_size = None
                                else:
                                    self.orderbook.best_bid_no = bid_val
                                    self.orderbook.best_bid_no_size = None
                        except (ValueError, TypeError):
                            pass

            elif event_type == "best_bid_ask":
                best_ask = data.get("best_ask")
                best_bid = data.get("best_bid")

                if best_ask is not None and best_ask != "":
                    try:
                        val = float(best_ask)
                        if 0.001 <= val <= 0.999:
                            if is_yes_data:
                                self.orderbook.best_ask_yes = val
                                self.orderbook.best_ask_yes_size = None
                            else:
                                self.orderbook.best_ask_no = val
                                self.orderbook.best_ask_no_size = None
                    except (ValueError, TypeError):
                        pass

                if best_bid is not None and best_bid != "":
                    try:
                        val = float(best_bid)
                        if 0.001 <= val <= 0.999:
                            if is_yes_data:
                                self.orderbook.best_bid_yes = val
                                self.orderbook.best_bid_yes_size = None
                            else:
                                self.orderbook.best_bid_no = val
                                self.orderbook.best_bid_no_size = None
                    except (ValueError, TypeError):
                        pass

            self.orderbook.update()
            self._update_winning_side()
            self.last_ws_update_ts = time.time()

            # Record book update for replay (throttled)
            if self.event_recorder is not None:
                now_mono = time.time()
                if (now_mono - self._last_replay_book_ts) >= self._replay_book_throttle_s:
                    side = "YES" if is_yes_data else "NO"
                    self.event_recorder.record_book_update(
                        side=side,
                        best_ask=self.orderbook.best_ask_yes if is_yes_data else self.orderbook.best_ask_no,
                        best_ask_size=self.orderbook.best_ask_yes_size if is_yes_data else self.orderbook.best_ask_no_size,
                        best_bid=self.orderbook.best_bid_yes if is_yes_data else self.orderbook.best_bid_no,
                        best_bid_size=self.orderbook.best_bid_yes_size if is_yes_data else self.orderbook.best_bid_no_size,
                    )
                    self._last_replay_book_ts = now_mono

            time_remaining = self.get_time_remaining()

            now_ts = time.time()
            in_final_seconds = time_remaining <= 5.0
            interval_s = (
                self.book_log_every_s_final
                if in_final_seconds
                else self.book_log_every_s
            )
            winner_changed = (self.winning_side or None) != (
                self._last_logged_winner or None
            )
            time_due = (now_ts - self._last_book_log_ts) >= max(0.0, interval_s)
            should_log = winner_changed or time_due

            if should_log:
                yes_ask = self.orderbook.best_ask_yes
                yes_bid = self.orderbook.best_bid_yes
                yes_ask_sz = self.orderbook.best_ask_yes_size
                yes_bid_sz = self.orderbook.best_bid_yes_size
                no_ask = self.orderbook.best_ask_no
                no_bid = self.orderbook.best_bid_no
                no_ask_sz = self.orderbook.best_ask_no_size
                no_bid_sz = self.orderbook.best_bid_no_size

                def fmt(p):
                    return f"${p:.2f}" if p is not None else "-"

                def fmt_sz(s):
                    if s is None:
                        return "-"
                    if abs(s - round(s)) < 1e-9:
                        return str(int(round(s)))
                    return f"{s:.4f}".rstrip("0").rstrip(".")

                def fmt_notional(p, s):
                    if p is None or s is None:
                        return "-"
                    return f"${p * s:.2f}"

                msg = "".join(
                    [
                        f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] [{self.market_name}] ",
                        f"Time: {time_remaining:.2f}s | ",
                        f"YES bid: {fmt(yes_bid)} x {fmt_sz(yes_bid_sz)} (= {fmt_notional(yes_bid, yes_bid_sz)}) | ",
                        f"YES ask: {fmt(yes_ask)} x {fmt_sz(yes_ask_sz)} (= {fmt_notional(yes_ask, yes_ask_sz)}) | ",
                        f"NO bid: {fmt(no_bid)} x {fmt_sz(no_bid_sz)} (= {fmt_notional(no_bid, no_bid_sz)}) | ",
                        f"NO ask: {fmt(no_ask)} x {fmt_sz(no_ask_sz)} (= {fmt_notional(no_ask, no_ask_sz)}) | ",
                        f"Winner: {self.winning_side or 'None'}",
                    ]
                )
                self._log(msg)
                self._last_book_log_ts = now_ts
                self._last_logged_winner = self.winning_side

            await self.check_trigger(time_remaining)

            if self.position_manager.is_open and not self._strategy_trade:
                current_price = self._get_ask_for_side(
                    self.position_manager.position_side or ""
                )
                if current_price is not None:
                    await self.stop_loss_manager.check_and_execute(current_price)

            # Check virtual dry-run positions for simulated stop-loss/take-profit.
            # Pass side-specific prices so each position is checked against its
            # own side's ask (not blindly the winning side's price).
            if self.dry_run_sim:
                await self.dry_run_sim.check_virtual_positions(
                    yes_price=self._get_ask_for_side("YES"),
                    no_price=self._get_ask_for_side("NO"),
                )

        except Exception as e:
            self._log(f"Error processing market update: {e}")

    def _update_winning_side(self) -> None:
        """Update winning side based on current orderbook state. Delegates to OrderbookTracker."""
        self._ob_tracker.orderbook = self.orderbook
        self._ob_tracker.update_winning_side()
        self.winning_side = self._ob_tracker.winning_side

    def _get_winning_token_id(self) -> str | None:
        """Get token ID for the winning side."""
        self._ob_tracker.winning_side = self.winning_side
        return self._ob_tracker.get_winning_token_id()

    def _get_winning_ask(self) -> float | None:
        """Get best ask price for winning side."""
        self._ob_tracker.orderbook = self.orderbook
        self._ob_tracker.winning_side = self.winning_side
        return self._ob_tracker.get_winning_ask()

    def _get_winning_bid(self) -> float | None:
        """Get best bid price for winning side."""
        self._ob_tracker.orderbook = self.orderbook
        self._ob_tracker.winning_side = self.winning_side
        return self._ob_tracker.get_winning_bid()

    def _build_market_summary(self) -> str:
        """Build a summary string from market stats for the close record."""
        s = self._market_stats
        parts = [
            f"ticks={s['ticks_total']}",
        ]
        if s["skip_reasons"]:
            top = s["skip_reasons"].most_common(3)
            parts.append("reasons=" + ",".join(f"{k}:{v}" for k, v in top))
        return " | ".join(parts)

    async def check_trigger(self, time_remaining: float):
        """
        Check if trigger conditions are met and execute trade if appropriate.
        """
        async with self._trigger_lock:
            trigger_start_mono = time.monotonic()

            if (
                self.order_execution.is_executed()
                or self.order_execution.is_in_progress()
                or time_remaining <= 0
            ):
                return

            if not self.risk_manager.check_daily_limits():
                if self.dry_run_sim and "daily_loss_limit" not in self._recorded_skip_guards:
                    self._recorded_skip_guards.add("daily_loss_limit")
                    await self.dry_run_sim.record_skip(
                        reason="daily_loss_limit",
                        time_remaining=time_remaining,
                    )
                self.order_execution.mark_executed()
                return

            if (
                self.order_execution.get_attempts()
                >= self.order_execution.get_max_attempts()
            ):
                if "max_attempts" not in self._logged_warnings:
                    self._log(
                        f"⚠️  [{self.market_name}] Max order attempts ({self.order_execution.get_max_attempts()}) reached"
                    )
                    self._logged_warnings.add("max_attempts")
                    if self.dry_run_sim and "max_attempts" not in self._recorded_skip_guards:
                        self._recorded_skip_guards.add("max_attempts")
                        await self.dry_run_sim.record_skip(
                            reason="max_attempts",
                            time_remaining=time_remaining,
                        )
                return

            current_time = time.time()
            if (
                self.order_execution.get_attempts() > 0
                and (current_time - self.order_execution.get_last_attempt_time()) < 2.0
            ):
                return

            # Priority 1: Strategy plugin (e.g. convergence — buy CHEAP side when oracle near beat)
            if (
                self.strategy_instance is not None
                and self.oracle_guard.enabled
            ):
                tick = MarketTick(
                    time_remaining=time_remaining,
                    oracle_snapshot=self.oracle_guard.snapshot,
                    orderbook=self.orderbook,
                )
                signal = self.strategy_instance.get_signal(tick)
                if signal is not None:
                    # Oracle freshness check (staleness + min_points only).
                    # NOTE: z-score check intentionally skipped — convergence
                    # strategy WANTS low |z| (price near target). The standard
                    # quality_ok() filter is designed for directional trading
                    # (high |z| = strong signal) and would wrongly block the
                    # best convergence opportunities.
                    oracle_ok, oracle_block_reason, oracle_block_detail = (
                        self.oracle_guard.quality_ok_for_convergence()
                    )
                    if not oracle_ok:
                        strategy_name = self.strategy_instance.name
                        self._log(
                            f"⛔ [{self.market_name}] {strategy_name.upper()} blocked: "
                            f"oracle_quality={oracle_block_reason} ({oracle_block_detail})"
                        )
                        if self.dry_run_sim and oracle_block_reason not in self._recorded_skip_guards:
                            self._recorded_skip_guards.add(oracle_block_reason)
                            await self.dry_run_sim.record_skip(
                                reason=oracle_block_reason,
                                reason_detail=oracle_block_detail,
                                time_remaining=time_remaining,
                                oracle_snap=self.oracle_guard.snapshot,
                            )
                        return

                    meta = signal.metadata
                    strategy_name = self.strategy_instance.name
                    self._log(
                        f"🎯 [{self.market_name}] {strategy_name.upper()} TRIGGER! "
                        f"{signal.side} ({meta.get('side_label', '')}) @ ${signal.price:.4f} | "
                        f"delta_pct={meta.get('delta_pct', 0) * 100:+.4f}% | "
                        f"skew={meta.get('expensive_price', 0):.2f}/{signal.price:.2f} | "
                        f"obs={meta.get('observations', 0)} | "
                        f"conv_rate={meta.get('convergence_rate', 0):.0%} | "
                        f"side_cons={meta.get('side_consistency', 0):.0%} | "
                        f"t={time_remaining:.1f}s"
                    )

                    self._planned_trade_side = signal.side
                    self._strategy_trade = True
                    _was_executed = self.order_execution.is_executed()
                    await self.execute_order()

                    # Record buy AFTER confirming execution to avoid phantom
                    # positions when the FOK order is killed (live mode).
                    # In dry_run mode execute_order() always succeeds, so the
                    # condition is always True there too.
                    if self.dry_run_sim and not _was_executed and self.order_execution.is_executed():
                        await self.dry_run_sim.record_buy(
                            side=signal.side,
                            price=signal.price,
                            amount=self.trade_size,
                            confidence=meta.get("confidence", 0.0),
                            time_remaining=time_remaining,
                            reason=meta.get("reason", strategy_name),
                            oracle_snap=self.oracle_guard.snapshot,
                            disable_stop_loss=signal.disable_stop_loss,
                        )
                    return

            # No strategy triggered, wait for next tick.
            self._market_stats["ticks_total"] += 1


    async def verify_order(self, order_id: str) -> bool:
        """Verify order status after submission by querying the API."""
        if not self.client:
            return False

        self._log(f"🔎 [{self.market_name}] Verifying order {order_id}...")
        try:
            await asyncio.sleep(0.5)

            order_data_raw = await asyncio.to_thread(self.client.get_order, order_id)
            if not isinstance(order_data_raw, dict):
                self._log(
                    f"⚠️  [{self.market_name}] Unexpected order data type: {type(order_data_raw)}"
                )
                return False
            order_data: dict[str, Any] = order_data_raw

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

        return True

    async def execute_order(self) -> None:
        side = self._planned_trade_side or self.winning_side or "YES"
        is_strategy = self._strategy_trade
        self._planned_trade_side = None
        winning_ask = self._get_ask_for_side(side)

        if winning_ask is not None and winning_ask > MAX_ENTRY_PRICE:
            self._log(
                f"[{self.market_name}] Price {winning_ask:.4f} above max entry {MAX_ENTRY_PRICE} — skipping"
            )
            return

        was_executed_before = self.order_execution.is_executed()
        await self.order_execution.execute_order_for(side, winning_ask)
        # Record buy trade for replay
        if self.event_recorder is not None and not was_executed_before and self.order_execution.is_executed():
            reason = self.strategy_instance.name if (is_strategy and self.strategy_instance) else "trigger"
            self.event_recorder.record_trade(
                action="buy",
                side=side,
                price=winning_ask or 0.0,
                size=self.trade_size,
                success=True,
                reason=reason,
            )

    async def execute_order_for(self, side: str) -> None:
        """Execute order (deprecated - use execute_order instead)."""
        await self.execute_order()

    async def execute_sell(self, reason: str) -> None:
        """Execute sell order using order execution manager."""
        position_side = (
            self.position_manager.position_side if self.position_manager else ""
        )
        current_price = self._get_ask_for_side(position_side or "")
        await self.order_execution.execute_sell(reason, current_price)
        self._strategy_trade = False  # Reset convergence flag on sell
        # Record sell trade for replay
        if self.event_recorder is not None:
            self.event_recorder.record_trade(
                action="sell",
                side=position_side or "",
                price=current_price or 0.0,
                size=self.trade_size,
                success=True,
                reason=reason,
            )

    async def listen_to_market(self):
        """Listen to WebSocket and process market updates until market closes."""
        self._ws_client.ws = self.ws  # sync ws reference
        await self._ws_client.listen(
            on_update=self.process_market_update,
            should_stop=lambda: self.get_time_remaining() <= 0,
            on_close=self._record_market_close,
        )

    async def run(self):
        """Main entry point: Connect and start trading."""
        # Reset strategy accumulator for this market cycle
        if self.strategy_instance is not None:
            self.strategy_instance.reset()
        try:
            if self._orderbook_ws_adapter is not None:
                # Use OrderbookWS adapter — it handles connect, subscribe, reconnect
                await self._orderbook_ws_adapter.start()
                self._log(f"[{self.market_name}] OrderbookWS connected (Level 2)")
                tasks = [self._trigger_check_loop()]
            else:
                # Legacy built-in WebSocket
                connected = await self.connect_websocket()
                if not connected:
                    self._log("Failed to connect to WebSocket. Exiting.")
                    return
                tasks = [self.listen_to_market(), self._trigger_check_loop()]

            if self.oracle_guard.enabled:
                tasks.append(self._oracle_price_loop())
            await asyncio.gather(*tasks)

        except KeyboardInterrupt:
            self._log("⚠️  Interrupted by user. Shutting down...")
        finally:
            # Stop OrderbookWS adapter if active
            if self._orderbook_ws_adapter is not None:
                try:
                    await self._orderbook_ws_adapter.stop()
                except Exception:
                    pass

            if self.ws:
                await self.ws.close()

            # Record final decision if no trade was executed
            try:
                await self._record_market_close()
            except Exception as e:
                self._log(f"[{self.market_name}] Error recording market close in finally: {e}")

            # Close event recorder if still open
            if self.event_recorder is not None:
                try:
                    self.event_recorder.close()
                except Exception:
                    pass
                self.event_recorder = None

            if self.oracle_guard.enabled:
                self.oracle_guard.log_block_summary(self.logger)
            self._log("✓ Trader shut down cleanly")

    async def _oracle_price_loop(self) -> None:
        """
        Stream Chainlink oracle prices from RTDS and compute lightweight metrics.

        This is intentionally independent from the CLOB websocket and does not
        hit polymarket.com except a best-effort single HTML fetch for price_to_beat
        when the trader starts late (Cloudflare risk).
        """
        if self.oracle_guard.symbol is None:
            self._log(
                f"⚠️  [{self.market_name}] Oracle tracking enabled but symbol is unknown"
            )
            return
        if self.oracle_guard.tracker is None:
            return

        start_ms = (
            getattr(self.oracle_guard.window, "start_ms", None)
            if self.oracle_guard.window
            else None
        )
        end_ms = (
            getattr(self.oracle_guard.window, "end_ms", None)
            if self.oracle_guard.window
            else None
        )
        now_ms = int(time.time() * 1000)
        missed_start = False
        if start_ms is None:
            self._log(
                f"⚠️  [{self.market_name}] Oracle window start not parsed; price_to_beat capture may be unavailable"
            )
            missed_start = True
        else:
            lag_ms = now_ms - start_ms
            if lag_ms > self.oracle_guard.beat_max_lag_ms:
                self._log(
                    f"⚠️  [{self.market_name}] Oracle start missed by {lag_ms / 1000:.1f}s (max_lag={self.oracle_guard.beat_max_lag_ms / 1000:.1f}s); price_to_beat will be unavailable"
                )
                missed_start = True

        if (
            missed_start
            and not self.oracle_guard.html_beat_attempted
            and self.slug
            and self.oracle_guard.window is not None
            and self.oracle_guard.window.start_iso_z is not None
            and self.oracle_guard.tracker.price_to_beat is None
        ):
            self.oracle_guard.html_beat_attempted = True
            try:
                asset = self.market_name
                cadence = "fifteen"
                if start_ms is not None and end_ms is not None:
                    dur_ms = end_ms - start_ms
                    if abs(dur_ms - 300_000) <= 15_000:
                        cadence = "five"
                    elif abs(dur_ms - 900_000) <= 30_000:
                        cadence = "fifteen"

                async with aiohttp.ClientSession() as session:
                    event_page = EventPageClient(session)
                    open_price, _close_price = await event_page.fetch_past_results(
                        eslug=self.slug,
                        asset=asset,
                        cadence=cadence,
                        start_time_iso_z=self.oracle_guard.window.start_iso_z,
                    )

                if open_price is not None:
                    self.oracle_guard.tracker.price_to_beat = float(open_price)
                    self._log(
                        f"✓ [{self.market_name}] price_to_beat from event HTML: {open_price:,.2f}"
                    )
                else:
                    self._log(
                        f"⚠️  [{self.market_name}] Could not fetch price_to_beat from event HTML (Cloudflare or format change)"
                    )
            except Exception as e:
                self._log(
                    f"⚠️  [{self.market_name}] Event HTML price_to_beat fetch failed: {e}"
                )

        if self.slug and (
            self.oracle_guard.up_side is None or self.oracle_guard.down_side is None
        ):
            try:
                url = f"https://gamma-api.polymarket.com/markets/slug/{self.slug}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=15)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                        else:
                            data = None

                if isinstance(data, dict):
                    outcomes_raw = data.get("outcomes")
                    token_ids_raw = data.get("clobTokenIds")
                    outcomes = (
                        json.loads(outcomes_raw)
                        if isinstance(outcomes_raw, str)
                        else outcomes_raw
                    )
                    token_ids = (
                        json.loads(token_ids_raw)
                        if isinstance(token_ids_raw, str)
                        else token_ids_raw
                    )

                    if (
                        isinstance(outcomes, list)
                        and isinstance(token_ids, list)
                        and len(outcomes) == 2
                        and len(token_ids) == 2
                    ):
                        up_idx = next(
                            (
                                i
                                for i, o in enumerate(outcomes)
                                if isinstance(o, str) and o.strip().lower() == "up"
                            ),
                            None,
                        )
                        down_idx = next(
                            (
                                i
                                for i, o in enumerate(outcomes)
                                if isinstance(o, str) and o.strip().lower() == "down"
                            ),
                            None,
                        )
                        if up_idx is not None and down_idx is not None:
                            up_token = str(token_ids[up_idx])
                            down_token = str(token_ids[down_idx])

                            if up_token == self.token_id_yes:
                                self.oracle_guard.up_side = "YES"
                            elif up_token == self.token_id_no:
                                self.oracle_guard.up_side = "NO"

                            if down_token == self.token_id_yes:
                                self.oracle_guard.down_side = "YES"
                            elif down_token == self.token_id_no:
                                self.oracle_guard.down_side = "NO"

                            if (
                                self.oracle_guard.up_side
                                and self.oracle_guard.down_side
                            ):
                                self._log(
                                    f"✓ [{self.market_name}] Oracle outcome mapping: Up→{self.oracle_guard.up_side}, Down→{self.oracle_guard.down_side}"
                                )
                            else:
                                self._log(
                                    f"⚠️  [{self.market_name}] Oracle mapping unresolved (token ids mismatch)"
                                )
            except Exception as e:
                self._log(f"⚠️  [{self.market_name}] Oracle mapping fetch failed: {e}")

        self._log(
            f"✓ [{self.market_name}] Oracle tracking enabled (RTDS Chainlink) symbol={self.oracle_guard.symbol}"
        )

        rtds = RtdsClient()
        topics = {"crypto_prices_chainlink"}

        while self.get_time_remaining() > 0:
            try:
                async for tick in rtds.iter_prices(
                    symbol=self.oracle_guard.symbol, topics=topics, seconds=15.0
                ):
                    self.oracle_guard.last_update_ts = time.time()

                    if start_ms is not None:
                        self.oracle_guard.tracker.maybe_set_price_to_beat(
                            ts_ms=tick.ts_ms,
                            price=tick.price,
                            start_ms=start_ms,
                            max_lag_ms=self.oracle_guard.beat_max_lag_ms,
                        )
                    # Ultimate fallback: if price_to_beat is still None,
                    # use current oracle price. Within the window, delta will
                    # be small and convergence detection works correctly.
                    if self.oracle_guard.tracker.price_to_beat is None:
                        self.oracle_guard.tracker.price_to_beat = tick.price
                        self._log(
                            f"[{self.market_name}] Using first oracle price as price_to_beat: {tick.price:,.2f}"
                        )

                    self.oracle_guard.snapshot = self.oracle_guard.tracker.update(
                        ts_ms=tick.ts_ms, price=tick.price
                    )

                    # Fallback: if beat still missing 10s after start, try HTML
                    if (
                        self.oracle_guard.tracker.price_to_beat is None
                        and not self.oracle_guard.html_beat_attempted
                        and self.slug
                        and start_ms is not None
                        and (tick.ts_ms - start_ms) > 10_000
                    ):
                        self.oracle_guard.html_beat_attempted = True
                        self._log(
                            f"⚠️  [{self.market_name}] price_to_beat still missing after 30s, trying HTML fallback..."
                        )
                        try:
                            asset = self.market_name
                            cadence = "fifteen"
                            if start_ms is not None and end_ms is not None:
                                dur_ms = end_ms - start_ms
                                if abs(dur_ms - 300_000) <= 15_000:
                                    cadence = "five"
                            async with aiohttp.ClientSession() as session:
                                event_page = EventPageClient(session)
                                open_price, _ = await event_page.fetch_past_results(
                                    eslug=self.slug, asset=asset, cadence=cadence,
                                    start_time_iso_z=self.oracle_guard.window.start_iso_z if self.oracle_guard.window else None,
                                )
                            if open_price is not None:
                                self.oracle_guard.tracker.price_to_beat = float(open_price)
                                self._log(f"✓ [{self.market_name}] price_to_beat from HTML fallback: {open_price:,.2f}")
                            else:
                                # Last resort: use first oracle tick as approximate beat
                                if self.oracle_guard.tracker._points:
                                    first_price = self.oracle_guard.tracker._points[0][1]
                                    self.oracle_guard.tracker.price_to_beat = first_price
                                    self._log(f"⚠️  [{self.market_name}] price_to_beat from first oracle tick (approx): {first_price:,.2f}")
                                else:
                                    self._log(f"⚠️  [{self.market_name}] HTML fallback failed and no oracle ticks available")
                        except Exception as e:
                            self._log(f"⚠️  [{self.market_name}] HTML fallback failed: {e}")
                            # Last resort: first oracle tick
                            if self.oracle_guard.tracker._points and self.oracle_guard.tracker.price_to_beat is None:
                                first_price = self.oracle_guard.tracker._points[0][1]
                                self.oracle_guard.tracker.price_to_beat = first_price
                                self._log(f"⚠️  [{self.market_name}] price_to_beat from first oracle tick (approx): {first_price:,.2f}")

                    now_ts = time.time()
                    if (now_ts - self.oracle_guard._last_log_ts) >= 1.0:
                        snap = self.oracle_guard.snapshot
                        beat = (
                            f"{snap.price_to_beat:,.2f}"
                            if snap.price_to_beat is not None
                            else "-"
                        )
                        delta = f"{snap.delta:,.2f}" if snap.delta is not None else "-"
                        delta_pct = (
                            f"{snap.delta_pct * 100:.4f}%"
                            if snap.delta_pct is not None
                            else "-"
                        )
                        z = f"{snap.zscore:.2f}" if snap.zscore is not None else "-"
                        msg = (
                            f"[{self.market_name}] ORACLE {self.oracle_guard.symbol}={snap.price:,.2f} | "
                            f"beat={beat} | Δ={delta} | Δ%={delta_pct} | z={z}"
                        )
                        self._log(msg)
                        self.oracle_guard._last_log_ts = now_ts

                    if end_ms is not None and tick.ts_ms >= end_ms:
                        return

            except Exception as e:
                self._log(f"⚠️  [{self.market_name}] Oracle RTDS error: {e}")
                await asyncio.sleep(2.0)

    async def _record_market_close(self) -> None:
        """Record a final skip decision when market closes without a trade."""
        self._log(f"[{self.market_name}] _record_market_close called (executed={self.order_execution.is_executed()}, sim={self.dry_run_sim is not None})")
        if self.order_execution.is_executed():
            return
        if not self.dry_run_sim:
            return
        winning_ask = self._get_winning_ask()
        summary = self._build_market_summary()
        self._log(f"[{self.market_name}] Recording market_closed_no_trigger (no trade executed) | {summary}")
        await self.dry_run_sim.record_skip(
            reason="market_closed_no_trigger",
            reason_detail=summary,
            side=self.winning_side,
            price=winning_ask,
            confidence=None,
            time_remaining=0.0,
            oracle_snap=self.oracle_guard.snapshot if self.oracle_guard.enabled else None,
        )

    async def _trigger_check_loop(self):
        """Fallback loop for time-based checks without trading on stale data."""
        while True:
            time_remaining = self.get_time_remaining()
            if time_remaining <= 0:
                await self._record_market_close()
                break

            if (
                self.orderbook.best_ask_yes is not None
                or self.orderbook.best_ask_no is not None
            ):
                now_ts = time.time()
                # When using OrderbookWS adapter, use adapter's sync timestamp
                last_update = (
                    self._orderbook_ws_adapter.last_sync_ts
                    if self._orderbook_ws_adapter is not None
                    else self.last_ws_update_ts
                )
                ws_fresh = (now_ts - last_update) <= self.WS_STALE_SECONDS
                if ws_fresh:
                    await self.check_trigger(time_remaining)
                else:
                    if now_ts - self._last_stale_log_ts >= 5.0:
                        stale_msg = "".join(
                            [
                                f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] ",
                                f"[{self.market_name}] WS stale ({now_ts - self.last_ws_update_ts:.1f}s). ",
                                f"Time: {time_remaining:.2f}s",
                            ]
                        )

                        self._log(stale_msg)
                        self._last_stale_log_ts = now_ts

            await asyncio.sleep(1.0)

    # Backward-compatibility properties for order execution state
    @property
    def order_executed(self) -> bool:
        """Get order executed status from order execution manager."""
        return self.order_execution.is_executed()

    # Backward-compatibility alias to allow tests to override planned trade amount
    @property
    def _planned_trade_amount(self) -> float | None:
        """Backward-compatibility alias to risk_manager's planned trade amount used by tests."""
        return self.risk_manager.planned_trade_amount if self.risk_manager else None

    @_planned_trade_amount.setter
    def _planned_trade_amount(self, value: float | None) -> None:
        self.risk_manager.planned_trade_amount = value

    # Backward-compatibility property for client (needed for tests that mock the client)
    @property
    def client(self) -> ClobClient | None:
        """Get/set the CLOB client, updating managers when set."""
        return self._client

    @client.setter
    def client(self, value: ClobClient | None) -> None:
        self._client = value
        # Update all managers that depend on the client
        if hasattr(self, "order_execution") and self.order_execution:
            self.order_execution.client = value
        if hasattr(self, "risk_manager") and self.risk_manager:
            self.risk_manager.client = value

    # Keep for backward compatibility with tests
    def _track_daily_pnl(self, trade_amount: float, pnl: float = 0.0) -> None:
        """Deprecated: Use risk_manager.track_daily_pnl() instead."""
        # This method is kept for backward compatibility with existing tests
        # but delegates to the new manager
        return self.risk_manager.track_daily_pnl(trade_amount, pnl)

    # Keep for backward compatibility with tests
    async def _check_balance(self) -> bool:
        """Deprecated: Use risk_manager.check_balance() instead."""
        return await self.risk_manager.check_balance()

    # Keep for backward compatibility with tests
    def _check_daily_limits(self) -> bool:
        """Deprecated: Use risk_manager.check_daily_limits() instead."""
        # This method is kept for backward compatibility with existing tests
        # but delegates to the new manager
        return self.risk_manager.check_daily_limits()

    # Keep for backward compatibility with tests
    def _get_daily_limits_path(self) -> str:
        """Deprecated: Use risk_manager._get_daily_limits_path() instead."""
        # This method is kept for backward compatibility with existing tests
        # but delegates to the new manager
        return self.risk_manager._get_daily_limits_path()

    # Keep for backward compatibility with tests
    async def _check_stop_loss_take_profit(self) -> bool:
        """Deprecated: Use stop_loss_manager.check_and_execute() instead."""
        # This method is kept for backward compatibility with existing tests
        # but delegates to the new manager
        if not self.position_manager.is_open:
            return False
        current_price = self._get_ask_for_side(
            self.position_manager.position_side or ""
        )
        if current_price is None:
            return False

        # Delegate to StopLossManager which handles throttling
        return await self.stop_loss_manager.check_and_execute(current_price)
