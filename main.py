"""
Main runner script for Polymarket 5/15-minute market trading bot.

This script orchestrates the entire trading workflow:
1. Periodically polls for active 5/15-minute Bitcoin/Ethereum markets
2. When a market is found, launches the HFT trader
3. Manages timing to ensure trader is running before market closes
4. Logs all activities to separate log files

Architecture:
- Async-based for non-blocking operations
- Separate loggers for finder and trader activities
- Handles market lifecycle from discovery to trading execution

Usage:
    # Dry run mode (safe, default)
    python main.py

    # Live trading mode (DANGER!)
    python main.py --live

    # Custom polling interval (seconds)
    python main.py --poll-interval 30
"""

import asyncio
import argparse
import functools
import random
import signal
from datetime import datetime
from typing import Any, Dict, Optional

from src.logging_config import setup_bot_loggers
from src.healthcheck import HealthCheckServer
from src.trading.parallel_launcher import ParallelLauncher
from src.trading.trade_db import TradeDatabase

# Import our modules
from src.gamma_15m_finder import GammaAPI15mFinder
from src.hft_trader import LastSecondTrader
from src.trading.dry_run_simulator import DryRunSimulator


class TradingBotRunner:
    """
    Main orchestrator for the trading bot.
    Manages market discovery and trader execution.
    """

    # Configuration
    POLL_INTERVAL = 120  # Check for new markets every 120 seconds to reduce API load
    TRADER_START_WINDOW_MAX = 900  # Start trader up to 15 minutes before market close (INCREASED from 300s)
    TRADER_START_WINDOW_MIN = 20  # But not less than 20 seconds before close (too late)

    def __init__(
        self,
        dry_run: bool = True,
        trade_size: float = LastSecondTrader.MIN_TRADE_USDC,
        poll_interval: int = 90,
        run_once: bool = False,
        max_traders: int = 1,
        oracle_enabled: bool = False,
        oracle_guard_enabled: bool = True,
        oracle_min_points: int = 4,
        oracle_window_s: float = 60.0,
        book_log_every_s: float = 1.0,
        book_log_every_s_final: float = 0.5,
    ):
        """
        Initialize the trading bot runner.

        Args:
            dry_run: If True, run in simulation mode (no real trades)
            trade_size: Size of trades in dollars (will buy trade_size/price tokens)
            poll_interval: How often to poll for new markets (seconds)
            run_once: If True, run once and exit (default: continuous loop)
        """
        self.dry_run = dry_run
        self.trade_size = trade_size
        self.poll_interval = poll_interval
        self.run_once = run_once
        self.max_traders = max_traders
        self.book_log_every_s = book_log_every_s
        self.book_log_every_s_final = book_log_every_s_final
        self.oracle_enabled = bool(oracle_enabled)
        self.oracle_guard_enabled = bool(oracle_guard_enabled)
        self.oracle_min_points = int(oracle_min_points)
        self.oracle_window_s = float(oracle_window_s)

        # Active traders (track running tasks)
        self.active_traders = {}  # condition_id -> asyncio.Task
        self.monitored_markets = set()  # Track markets we've already processed

        # Graceful shutdown state
        self._shutdown_event = asyncio.Event()
        self._traders: Dict[str, Any] = {}  # condition_id -> LastSecondTrader instance

        # Health check server
        self._health: Optional[HealthCheckServer] = HealthCheckServer()

        # Trade database (SQLite) for dry-run recording
        self._trade_db: Optional[TradeDatabase] = None

        # Periodic resolution counter (resolve every N poll cycles)
        self._poll_cycle = 0
        self.RESOLVE_EVERY_N_CYCLES = 10  # ~15 min at 90s poll interval

        # Setup logging
        self.setup_logging()

        self.finder_logger.info("=" * 80)
        self.finder_logger.info("Trading Bot Runner Initialized")
        self.finder_logger.info("=" * 80)
        self.finder_logger.info(
            f"Mode: {'DRY RUN (Safe Mode)' if self.dry_run else '🔴 LIVE TRADING 🔴'}"
        )
        self.finder_logger.info(f"Trade Size: ${self.trade_size}")
        self.finder_logger.info(f"Poll Interval: {self.poll_interval}s")
        self.finder_logger.info(f"Max Concurrent Traders: {self.max_traders}")
        self.finder_logger.info(
            f"Trader Start Window: {self.TRADER_START_WINDOW_MIN}s - {self.TRADER_START_WINDOW_MAX}s before market close"
        )
        self.finder_logger.info("=" * 80)

    def setup_logging(self):
        """Setup separate loggers with rotating file handlers.

        Uses src.logging_config for centralized configuration.
        Log rotation prevents unbounded disk growth (default 10MB × 5 backups).
        Override via env: LOG_MAX_BYTES, LOG_BACKUP_COUNT, LOG_LEVEL, LOG_CONSOLE.
        """
        self.finder_logger, self.trader_logger, self.trader_log_file = (
            setup_bot_loggers()
        )

    async def find_active_markets(self) -> Optional[list]:
        """
        Query for active 5/15-minute markets.
        Returns list of markets or None if none found.
        """
        try:
            finder = GammaAPI15mFinder(
                logger=self.finder_logger
            )  # Default: 20 minutes search window
            markets = await finder.find_active_market()
            return markets
        except Exception as e:
            self.finder_logger.error(f"Error finding markets: {e}", exc_info=True)
            return None

    def should_start_trader(self, market: Dict[str, Any]) -> bool:
        """
        Determine if we should start a trader for this market.

        Logic:
        - Market must be within start window (60-240 seconds before close)
        - Market must not already be monitored
        - Limit to 1 concurrent trader
        """
        condition_id = market.get("condition_id")
        minutes_until_end = market.get("minutes_until_end", 0)
        seconds_until_end = minutes_until_end * 60

        # Check if already monitoring
        if condition_id in self.monitored_markets:
            return False

        # Check if too late to start (less than 20 seconds)
        if seconds_until_end < self.TRADER_START_WINDOW_MIN:
            self.finder_logger.warning(
                f"Market {condition_id} ends in {seconds_until_end:.1f}s - too late to start trader"
            )
            return False

        # Check if too early to start (more than 240 seconds)
        if seconds_until_end > self.TRADER_START_WINDOW_MAX:
            self.finder_logger.info(
                f"Market {condition_id} ends in {minutes_until_end:.1f}m - waiting to start trader"
            )
            return False

        # Limit concurrent traders to reduce API load
        if len(self.active_traders) >= self.max_traders:
            self.finder_logger.info(
                f"Skipping market {condition_id} because a trader is already running (limit={self.max_traders})"
            )
            return False

        return True

    async def start_trader_for_market(self, market: Dict[str, Any]):
        """
        Start an HFT trader for a specific market.
        Runs in background as async task.
        """
        condition_id = market.get("condition_id")
        token_id_yes = market.get("token_id_yes")
        token_id_no = market.get("token_id_no")
        end_time_utc = market.get("end_time_utc")
        title = market.get("title")

        # Mark as monitored
        self.monitored_markets.add(condition_id)

        self.trader_logger.info("=" * 80)
        self.trader_logger.info(f"Starting trader for market: {title}")
        self.trader_logger.info(f"Condition ID: {condition_id}")
        self.trader_logger.info(f"Token ID (YES): {token_id_yes}")
        self.trader_logger.info(f"Token ID (NO): {token_id_no}")
        self.trader_logger.info(f"End Time: {end_time_utc}")
        self.trader_logger.info(f"Mode: {'DRY RUN' if self.dry_run else '🔴 LIVE 🔴'}")
        self.trader_logger.info("=" * 80)

        try:
            # Parse end time
            if not end_time_utc:
                self.trader_logger.error(f"Missing end time for market {condition_id}")
                return

            end_time = datetime.fromisoformat(end_time_utc.replace(" UTC", "+00:00"))

            # Validate token IDs
            if token_id_yes == "N/A" or token_id_no == "N/A":
                self.trader_logger.error(f"Invalid token IDs for market {condition_id}")
                return

            # Validate condition_id
            if not condition_id or condition_id == "N/A":
                self.trader_logger.error("Invalid condition ID")
                return

            # Type assertions: values are validated above
            assert (
                token_id_yes
                and token_id_no
                and token_id_yes != "N/A"
                and token_id_no != "N/A"
            )

            # Create and run trader with both token IDs
            # Trader will dynamically determine winning side based on prices
            trader = LastSecondTrader(
                condition_id=condition_id,
                token_id_yes=token_id_yes,
                token_id_no=token_id_no,
                end_time=end_time,
                dry_run=self.dry_run,
                trade_size=self.trade_size,
                title=market.get("title"),
                slug=market.get("slug"),
                trader_logger=self.trader_logger,
                oracle_enabled=self.oracle_enabled,
                oracle_guard_enabled=self.oracle_guard_enabled,
                oracle_min_points=self.oracle_min_points,
                oracle_window_s=self.oracle_window_s,
                book_log_every_s=self.book_log_every_s,
                book_log_every_s_final=self.book_log_every_s_final,
                trade_db=self._trade_db,
            )

            # Track trader instance for graceful shutdown
            self._traders[condition_id] = trader

            # Run trader (this will block until market closes or trader finishes)
            await trader.run()

            self.trader_logger.info(f"Trader finished for market {condition_id}")

        except Exception as e:
            self.trader_logger.error(
                f"Error running trader for market {condition_id}: {e}", exc_info=True
            )
        finally:
            # Clean up
            if condition_id in self.active_traders:
                del self.active_traders[condition_id]
            self._traders.pop(condition_id, None)

    def _register_signal_handlers(self, loop: asyncio.AbstractEventLoop) -> None:
        """Register OS signal handlers for graceful shutdown (SIGINT, SIGTERM)."""
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(
                sig,
                functools.partial(self._handle_signal, sig),
            )
        self.finder_logger.info("Signal handlers registered (SIGINT, SIGTERM)")

    def _handle_signal(self, sig: signal.Signals) -> None:
        """Handle shutdown signal by setting the shutdown event."""
        sig_name = sig.name
        self.finder_logger.warning(f"Received {sig_name} — initiating graceful shutdown...")
        self._shutdown_event.set()

    async def _shutdown_traders(self) -> None:
        """Gracefully shut down all active traders."""
        if not self._traders:
            return
        self.finder_logger.info(
            f"Shutting down {len(self._traders)} active trader(s) gracefully..."
        )
        shutdown_tasks = []
        for cid, trader in list(self._traders.items()):
            self.finder_logger.info(f"  → Shutting down trader for {cid}")
            shutdown_tasks.append(trader.graceful_shutdown(reason="Signal shutdown"))
        if shutdown_tasks:
            results = await asyncio.gather(*shutdown_tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    self.finder_logger.error(f"Error during trader shutdown: {result}")
        self.finder_logger.info("All traders shut down")

    async def _maybe_resolve_positions(self) -> None:
        """Periodically resolve dry-run positions (every N poll cycles)."""
        self._poll_cycle += 1
        if self._poll_cycle % self.RESOLVE_EVERY_N_CYCLES != 0:
            return
        if self._trade_db is None:
            return
        try:
            open_positions = await self._trade_db.get_open_dry_run_positions()
            if not open_positions:
                return
            self.finder_logger.info(
                f"Periodic resolution check: {len(open_positions)} open dry-run position(s)"
            )
            sim = DryRunSimulator(
                db=self._trade_db,
                market_name="resolver",
                condition_id="resolver",
                dry_run=True,
            )
            # resolve_all_markets needs a CLOB client; in dry-run mode we don't have one,
            # so we create a lightweight one just for market queries
            from src.position_settler import _create_clob_client
            try:
                client = _create_clob_client(self.finder_logger)
            except (SystemExit, Exception):
                self.finder_logger.debug("No CLOB client available for resolution check")
                return
            resolved = await sim.resolve_all_markets(client)
            if resolved:
                wins = sum(1 for r in resolved if r["status"] == "resolved_win")
                losses = sum(1 for r in resolved if r["status"] == "resolved_loss")
                total_pnl = sum(r["pnl"] for r in resolved)
                self.finder_logger.info(
                    f"Resolved {len(resolved)} positions: {wins} wins, {losses} losses, PnL=${total_pnl:.4f}"
                )
        except Exception as e:
            self.finder_logger.error(f"Error in periodic resolution: {e}", exc_info=True)

    async def poll_and_trade(self):
        """
        Main loop: continuously poll for markets and start traders as needed.
        Exits cleanly on SIGINT/SIGTERM via the shutdown event.
        """
        # Register signal handlers on the running event loop
        loop = asyncio.get_running_loop()
        self._register_signal_handlers(loop)

        # Start health check server
        if self._health is not None:
            try:
                await self._health.start()
                self._health.set_status("running")
                self._health.set_extra("dry_run", self.dry_run)
                self._health.set_extra("trade_size", self.trade_size)
            except Exception as exc:
                self.finder_logger.warning(f"Health check server failed to start: {exc}")

        self.finder_logger.info("Starting market polling loop...")

        while not self._shutdown_event.is_set():
            try:
                self.finder_logger.info(
                    f"Polling for active markets... (every {self.poll_interval}s)"
                )

                # Record poll in health check
                if self._health is not None:
                    self._health.record_poll()
                    self._health.set_active_traders(len(self.active_traders))

                # Find active markets
                markets = await self.find_active_markets()

                if markets:
                    self.finder_logger.info(f"Found {len(markets)} active market(s)")

                    # Filter eligible markets
                    eligible = [m for m in markets if self.should_start_trader(m)]

                    if eligible:
                        self.finder_logger.info(
                            f"Launching {len(eligible)} trader(s) in parallel"
                        )

                        # Parallel launch: start all eligible traders concurrently
                        launcher = ParallelLauncher(
                            max_concurrency=self.max_traders,
                            timeout=None,  # traders run until market close
                        )

                        async def _start_and_track(market: Dict[str, Any]) -> None:
                            cid = market.get("condition_id")
                            task = asyncio.create_task(
                                self.start_trader_for_market(market)
                            )
                            self.active_traders[cid] = task

                        batch = await launcher.launch(eligible, _start_and_track)
                        self.finder_logger.info(
                            f"Parallel launch: {batch.succeeded}/{batch.total} ok "
                            f"in {batch.elapsed_ms:.0f}ms"
                        )
                else:
                    self.finder_logger.info("No active markets found")

                # Periodic dry-run position resolution
                await self._maybe_resolve_positions()

                # Log active traders
                if self.active_traders:
                    self.finder_logger.info(
                        f"Currently running {len(self.active_traders)} trader(s)"
                    )

                # Exit if run_once mode
                if self.run_once:
                    self.finder_logger.info("Run-once mode: exiting after single poll")
                    break

                # Wait before next poll with jitter to avoid synchronized bursts
                jitter = random.uniform(0.85, 1.15)
                sleep_for = max(1, int(self.poll_interval * jitter))
                self.finder_logger.info(
                    f"Sleeping {sleep_for}s before next poll (jitter {jitter:.2f}x)"
                )
                # Interruptible sleep: wake early on shutdown signal
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(), timeout=sleep_for
                    )
                    # If we get here, shutdown was requested during sleep
                    self.finder_logger.info("Shutdown requested during sleep — exiting loop")
                    break
                except asyncio.TimeoutError:
                    pass  # Normal: sleep finished, continue polling

            except Exception as e:
                self.finder_logger.error(f"Error in poll loop: {e}", exc_info=True)
                if not self._shutdown_event.is_set():
                    await asyncio.sleep(self.poll_interval)

        # Graceful shutdown: stop all active traders properly
        await self._shutdown_traders()

        # Wait for all trader tasks to complete
        if self.active_traders:
            self.finder_logger.info(
                f"Waiting for {len(self.active_traders)} trader task(s) to finish..."
            )
            await asyncio.gather(*self.active_traders.values(), return_exceptions=True)

        # Stop health check server
        if self._health is not None:
            self._health.set_status("stopped")
            await self._health.stop()

        self.finder_logger.info("Trading bot shut down cleanly")

    async def run(self):
        """Main entry point."""
        try:
            # Initialize trade database for dry-run recording
            from pathlib import Path
            db_path = Path("data/trades.db")
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._trade_db = await TradeDatabase.initialize(str(db_path))
            self.finder_logger.info(f"TradeDatabase initialized: {db_path}")

            await self.poll_and_trade()
        except KeyboardInterrupt:
            self.finder_logger.info("\n⚠️  Interrupted by user")
            self._shutdown_event.set()
            await self._shutdown_traders()
        except Exception as e:
            self.finder_logger.error(f"Fatal error: {e}", exc_info=True)
        finally:
            if self._trade_db:
                await self._trade_db.close()
            self.finder_logger.info("=" * 80)
            self.finder_logger.info("Trading Bot Runner Stopped")
            self.finder_logger.info("=" * 80)


async def main():
    """Main entry point with command line argument parsing."""
    parser = argparse.ArgumentParser(
        description="Polymarket 15-minute market trading bot runner"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Enable live trading mode (default: dry run)",
    )
    parser.add_argument(
        "--size",
        type=float,
        default=LastSecondTrader.MIN_TRADE_USDC,
        help=f"Trade size in dollars (default: ${LastSecondTrader.MIN_TRADE_USDC:.2f})",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=90,
        help="How often to poll for markets in seconds (default: 90)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit (default: continuous loop)",
    )
    parser.add_argument(
        "--max-traders",
        type=int,
        default=1,
        help="Maximum concurrent traders (default: 1)",
    )
    parser.add_argument(
        "--oracle",
        action="store_true",
        help="Enable oracle tracking (Chainlink RTDS)",
    )
    parser.add_argument(
        "--no-oracle-guard",
        action="store_true",
        help="Disable oracle guard (tracking stays on if --oracle is set)",
    )
    parser.add_argument(
        "--oracle-min-points",
        type=int,
        default=4,
        help="Minimum oracle points required in rolling window (default: 4)",
    )
    parser.add_argument(
        "--oracle-window-s",
        type=float,
        default=60.0,
        help="Oracle rolling window size in seconds (default: 60.0)",
    )
    parser.add_argument(
        "--book-log-every",
        type=float,
        default=1.0,
        help="Orderbook log interval in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--book-log-every-final",
        type=float,
        default=0.5,
        help="Orderbook log interval in final 5 seconds (default: 0.5)",
    )

    args = parser.parse_args()

    if args.size <= 0:
        parser.error("--size must be a positive number")
    if args.max_traders < 1:
        parser.error("--max-traders must be at least 1")
    if args.oracle_min_points < 0:
        parser.error("--oracle-min-points must be >= 0")
    if args.oracle_window_s <= 0:
        parser.error("--oracle-window-s must be > 0")
    if args.book_log_every < 0:
        parser.error("--book-log-every must be >= 0")
    if args.book_log_every_final < 0:
        parser.error("--book-log-every-final must be >= 0")

    # Safety warning for live mode
    if args.live:
        print("\n" + "=" * 80)
        print("🔴 WARNING: LIVE TRADING MODE ENABLED 🔴")
        print("=" * 80)
        print("This bot will execute REAL trades with REAL money!")
        print("Press Ctrl+C within 5 seconds to cancel...")
        print("=" * 80 + "\n")
        await asyncio.sleep(5)

    # Create and run bot
    runner = TradingBotRunner(
        dry_run=not args.live,
        trade_size=args.size,
        poll_interval=args.poll_interval,
        run_once=args.once,
        max_traders=args.max_traders,
        oracle_enabled=args.oracle,
        oracle_guard_enabled=(args.oracle and (not args.no_oracle_guard)),
        oracle_min_points=args.oracle_min_points,
        oracle_window_s=args.oracle_window_s,
        book_log_every_s=args.book_log_every,
        book_log_every_s_final=args.book_log_every_final,
    )

    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
