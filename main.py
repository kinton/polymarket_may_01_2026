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
import logging
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# Import our modules
from src.gamma_15m_finder import GammaAPI15mFinder
from src.hft_trader import LastSecondTrader


class TradingBotRunner:
    """
    Main orchestrator for the trading bot.
    Manages market discovery and trader execution.
    """

    # Configuration
    POLL_INTERVAL = 120  # Check for new markets every 120 seconds to reduce API load
    TRADER_START_WINDOW_MAX = 300  # Start trader up to 5 minutes before market close (INCREASED from 240s)
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
        """Setup separate loggers for finder and trader activities."""
        # Create log directory if it doesn't exist
        log_dir = Path("log")
        log_dir.mkdir(exist_ok=True)

        # Finder logger (market discovery)
        self.finder_logger = logging.getLogger("finder")
        self.finder_logger.setLevel(logging.INFO)

        # Clear existing handlers to prevent accumulation on restarts
        if self.finder_logger.hasHandlers():
            self.finder_logger.handlers.clear()

        finder_handler = logging.FileHandler(log_dir / "finder.log")
        finder_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        self.finder_logger.addHandler(finder_handler)

        # Console handler for finder (optional)
        finder_console = logging.StreamHandler(sys.stdout)
        finder_console.setFormatter(
            logging.Formatter("%(asctime)s - [FINDER] - %(message)s")
        )
        self.finder_logger.addHandler(finder_console)

        # Trader logger (trading execution) — create per-run file trades-<timestamp>.log
        run_ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.trader_log_file = log_dir / f"trades-{run_ts}.log"

        self.trader_logger = logging.getLogger("trader")
        self.trader_logger.setLevel(logging.INFO)

        # Clear existing handlers to prevent accumulation on restarts
        if self.trader_logger.hasHandlers():
            self.trader_logger.handlers.clear()

        trader_handler = logging.FileHandler(self.trader_log_file)
        trader_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        self.trader_logger.addHandler(trader_handler)

        # Console handler for trader
        trader_console = logging.StreamHandler(sys.stdout)
        trader_console.setFormatter(
            logging.Formatter("%(asctime)s - [TRADER] - %(message)s")
        )
        self.trader_logger.addHandler(trader_console)

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
            )

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

    async def poll_and_trade(self):
        """
        Main loop: continuously poll for markets and start traders as needed.
        """
        self.finder_logger.info("Starting market polling loop...")

        while True:
            try:
                self.finder_logger.info(
                    f"Polling for active markets... (every {self.poll_interval}s)"
                )

                # Find active markets
                markets = await self.find_active_markets()

                if markets:
                    self.finder_logger.info(f"Found {len(markets)} active market(s)")

                    for market in markets:
                        condition_id = market.get("condition_id")

                        # Check if we should start a trader for this market
                        if self.should_start_trader(market):
                            self.finder_logger.info(
                                f"Starting trader for market: {market.get('title')}"
                            )

                            # Start trader as background task
                            task = asyncio.create_task(
                                self.start_trader_for_market(market)
                            )
                            self.active_traders[condition_id] = task
                else:
                    self.finder_logger.info("No active markets found")

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
                await asyncio.sleep(sleep_for)

            except KeyboardInterrupt:
                self.finder_logger.info("Received shutdown signal...")
                break
            except Exception as e:
                self.finder_logger.error(f"Error in poll loop: {e}", exc_info=True)
                await asyncio.sleep(self.poll_interval)

        # Cleanup: wait for all active traders to finish
        if self.active_traders:
            self.finder_logger.info(
                f"Waiting for {len(self.active_traders)} active trader(s) to finish..."
            )
            await asyncio.gather(*self.active_traders.values(), return_exceptions=True)

        self.finder_logger.info("Trading bot shut down cleanly")

    async def run(self):
        """Main entry point."""
        try:
            await self.poll_and_trade()
        except KeyboardInterrupt:
            self.finder_logger.info("\n⚠️  Interrupted by user")
        except Exception as e:
            self.finder_logger.error(f"Fatal error: {e}", exc_info=True)
        finally:
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
