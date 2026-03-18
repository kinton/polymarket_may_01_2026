"""
Main runner script for Polymarket 5/15-minute market trading bot.

Config-driven architecture: reads config/strategies.yaml and launches
all configured strategies in parallel (asyncio).

Usage:
    python main.py                          # default config
    python main.py --config path/to.yaml    # custom config
    python main.py --once                   # single poll cycle, then exit
"""

import asyncio
import argparse
import functools
import logging
import random
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from src.logging_config import setup_bot_loggers
from src.healthcheck import HealthCheckServer
from src.trading.market_feed_config import MarketFeedConfig
from src.trading.parallel_launcher import ParallelLauncher
from src.trading.trade_db import TradeDatabase
from src.watchdog import watchdog_loop

# Import our modules
from src.gamma_15m_finder import GammaAPI15mFinder
from src.hft_trader import LastSecondTrader
from src.market_feed import MarketFeed
from src.market_orchestrator import MarketOrchestrator
from src.strategy_registry import StrategyRegistration, StrategyRegistry
from src.trading.dry_run_simulator import DryRunSimulator
from strategies import discover_strategies, load_strategy
from strategies.base import MarketInfo


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

@dataclass
class StrategyConfig:
    """One strategy entry from strategies.yaml."""
    name: str = "convergence"
    version: str = "v1"
    mode: str = "test"           # "test" (dry-run) or "live"
    size: float = 1.0
    poll_interval: int = 90
    max_traders: int = 1
    universe: list[str] = field(default_factory=lambda: ["BTC", "ETH", "SOL"])

    @property
    def dry_run(self) -> bool:
        return self.mode != "live"

    @property
    def db_path(self) -> str:
        return f"data/{self.name}-{self.version}-{self.mode}.db"


def load_strategies_config(config_path: str) -> list[StrategyConfig]:
    """Load strategy list from YAML config file."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with open(path) as f:
        raw = yaml.safe_load(f)
    if not raw or "strategies" not in raw:
        raise ValueError(f"Config file must contain 'strategies' key: {config_path}")
    entries = raw["strategies"]
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"'strategies' must be a non-empty list: {config_path}")
    configs = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"Strategy entry {i} must be a dict")
        # Normalize universe to list of uppercase strings
        universe = entry.get("universe", ["BTC", "ETH", "SOL"])
        if isinstance(universe, str):
            universe = [t.strip().upper() for t in universe.split(",") if t.strip()]
        else:
            universe = [str(t).upper() for t in universe]
        configs.append(StrategyConfig(
            name=entry.get("name", "convergence"),
            version=entry.get("version", "v1"),
            mode=entry.get("mode", "test"),
            size=float(entry.get("size", 1.0)),
            poll_interval=int(entry.get("poll_interval", 90)),
            max_traders=int(entry.get("max_traders", 1)),
            universe=universe,
        ))
    return configs


# ---------------------------------------------------------------------------
# TradingBotRunner — one per strategy entry
# ---------------------------------------------------------------------------

class TradingBotRunner:
    """
    Main orchestrator for one strategy instance.
    Manages market discovery and trader execution.
    """

    TRADER_START_WINDOW_MAX = 900
    TRADER_START_WINDOW_MIN = 20

    def __init__(
        self,
        strategy_config: StrategyConfig,
        *,
        run_once: bool = False,
        feed_config: MarketFeedConfig | None = None,
        health_server_enabled: bool = True,
        watchdog_hours: float = 3.0,
        shutdown_event: asyncio.Event | None = None,
        market_queue: asyncio.Queue | None = None,
        shared_traders: dict | None = None,
    ):
        sc = strategy_config
        self.dry_run = sc.dry_run
        self.trade_size = sc.size
        self.poll_interval = sc.poll_interval
        self.run_once = run_once
        self.max_traders = sc.max_traders
        self.feed_config: MarketFeedConfig = feed_config or MarketFeedConfig()
        # Expose individual feed params for backward compat with existing call sites
        self.book_log_every_s = self.feed_config.book_log_every_s
        self.book_log_every_s_final = self.feed_config.book_log_every_s_final
        self.oracle_enabled = self.feed_config.oracle_enabled
        self.oracle_guard_enabled = self.feed_config.oracle_guard_enabled
        self.oracle_min_points = self.feed_config.oracle_min_points
        self.oracle_window_s = self.feed_config.oracle_window_s
        self.strategy = sc.name
        self.strategy_version = sc.version
        self.mode = sc.mode
        self.universe = sc.universe
        self.tickers_override: list[str] | None = None
        self.health_server_enabled = health_server_enabled
        self.db_path = sc.db_path
        self.watchdog_hours = watchdog_hours

        # Shared trader registry: condition_id → LastSecondTrader.
        # Runners that share this dict will inject strategy slots into an
        # already-running trader instead of opening a second WebSocket.
        # Passing None (default) gives this runner its own empty dict, which
        # preserves the original single-strategy behaviour.
        self._shared_traders: dict = shared_traders if shared_traders is not None else {}

        # Market queue — fed by SharedFinder (or self if running standalone)
        self.market_queue: asyncio.Queue = market_queue or asyncio.Queue()

        # Active traders (track running tasks)
        self.active_traders: dict[str, asyncio.Task] = {}
        self.monitored_markets: set[str] = set()
        # Per-ticker lock: prevents multiple simultaneous traders for same ticker
        self.monitored_tickers: set[str] = set()

        # Graceful shutdown state — shared across all runners
        self._shutdown_event = shutdown_event or asyncio.Event()
        self._traders: Dict[str, Any] = {}

        # Health check server (only first runner starts it)
        self._health: Optional[HealthCheckServer] = (
            HealthCheckServer() if self.health_server_enabled else None
        )

        # Trade database
        self._trade_db: Optional[TradeDatabase] = None

        # Periodic resolution counter
        self._poll_cycle = 0
        self.RESOLVE_EVERY_N_CYCLES = 10

        # Setup logging
        self.setup_logging()

        # Discover strategy plugins
        n_strategies = discover_strategies()
        self.finder_logger.info(f"Discovered {n_strategies} strategy plugin(s)")

        # Create a filter strategy instance for market_filter()
        self._filter_strategy = load_strategy(self.strategy, self.strategy_version)

        self.finder_logger.info("=" * 80)
        self.finder_logger.info("Trading Bot Runner Initialized")
        self.finder_logger.info("=" * 80)
        self.finder_logger.info(
            f"Strategy: {self.strategy} {self.strategy_version} | "
            f"Mode: {self.mode} | "
            f"Universe: {','.join(self.universe)}"
        )
        self.finder_logger.info(
            f"Mode: {'DRY RUN (Safe Mode)' if self.dry_run else 'LIVE TRADING'}"
        )
        self.finder_logger.info(f"Trade Size: ${self.trade_size}")
        self.finder_logger.info(f"Poll Interval: {self.poll_interval}s")
        self.finder_logger.info(f"Max Concurrent Traders: {self.max_traders}")
        self.finder_logger.info(f"DB: {self.db_path}")
        self.finder_logger.info("=" * 80)

    def setup_logging(self):
        self.finder_logger, self.trader_logger, self.trader_log_file = (
            setup_bot_loggers()
        )

    async def find_active_markets(self) -> Optional[list]:
        try:
            finder = GammaAPI15mFinder(
                logger=self.finder_logger,
                tickers=self.universe,
            )
            markets = await finder.find_active_market()
            return markets
        except Exception as e:
            self.finder_logger.error(f"Error finding markets: {e}", exc_info=True)
            return None

    def should_start_trader(self, market: Dict[str, Any]) -> bool:
        condition_id = market.get("condition_id")
        minutes_until_end = market.get("minutes_until_end", 0)
        seconds_until_end = minutes_until_end * 60

        if condition_id in self.monitored_markets:
            return False

        ticker = market.get("ticker", "")
        if ticker and ticker in self.monitored_tickers:
            self.finder_logger.debug(
                f"Skipping market {condition_id} — ticker {ticker!r} already has an active trader"
            )
            return False

        if seconds_until_end < self.TRADER_START_WINDOW_MIN:
            self.finder_logger.warning(
                f"Market {condition_id} ends in {seconds_until_end:.1f}s - too late to start trader"
            )
            return False

        if seconds_until_end > self.TRADER_START_WINDOW_MAX:
            self.finder_logger.info(
                f"Market {condition_id} ends in {minutes_until_end:.1f}m - waiting to start trader"
            )
            return False

        if len(self.active_traders) >= self.max_traders:
            self.finder_logger.info(
                f"Skipping market {condition_id} because a trader is already running (limit={self.max_traders})"
            )
            return False

        return True

    async def start_trader_for_market(self, market: Dict[str, Any]):
        condition_id = market.get("condition_id")
        token_id_yes = market.get("token_id_yes")
        token_id_no = market.get("token_id_no")
        end_time_utc = market.get("end_time_utc")
        title = market.get("title")

        self.monitored_markets.add(condition_id)

        # ----------------------------------------------------------------
        # Shared-feed fast path: another runner already owns the MarketFeed
        # for this market → subscribe our own trader to that feed instead of
        # opening a second WebSocket connection.
        # ----------------------------------------------------------------
        existing_feed = self._shared_traders.get(condition_id)
        if existing_feed is not None:
            self.trader_logger.info(
                f"[SharedFeed] Subscribing {self.strategy}/{self.strategy_version} "
                f"trader to existing feed for {condition_id}"
            )
            try:
                end_time_utc_str = end_time_utc or ""
                end_time = datetime.fromisoformat(
                    end_time_utc_str.replace(" UTC", "+00:00")
                )
                trader = LastSecondTrader(
                    condition_id=condition_id,
                    token_id_yes=token_id_yes or "",
                    token_id_no=token_id_no or "",
                    end_time=end_time,
                    dry_run=self.dry_run,
                    trade_size=self.trade_size,
                    title=title,
                    slug=market.get("slug"),
                    trader_logger=self.trader_logger,
                    feed_config=self.feed_config,
                    feed=existing_feed,
                    trade_db=self._trade_db,
                    strategy=self.strategy,
                    strategy_version=self.strategy_version,
                    mode=self.mode,
                )
                await trader.run()
            except Exception as exc:
                self.trader_logger.error(
                    f"[SharedFeed] Trader failed for {condition_id}: {exc}",
                    exc_info=True,
                )
            finally:
                if condition_id in self.active_traders:
                    del self.active_traders[condition_id]
                ticker = market.get("ticker", "")
                if ticker:
                    self.monitored_tickers.discard(ticker)
            return

        # ----------------------------------------------------------------
        # Normal path: we are the first runner for this market — create the
        # trader, register it in the shared registry, and run it.
        # ----------------------------------------------------------------
        self.trader_logger.info("=" * 80)
        self.trader_logger.info(f"Starting trader for market: {title}")
        self.trader_logger.info(f"Condition ID: {condition_id}")
        self.trader_logger.info(f"Token ID (YES): {token_id_yes}")
        self.trader_logger.info(f"Token ID (NO): {token_id_no}")
        self.trader_logger.info(f"End Time: {end_time_utc}")
        self.trader_logger.info(f"Mode: {'DRY RUN' if self.dry_run else 'LIVE'}")
        self.trader_logger.info("=" * 80)

        try:
            if not end_time_utc:
                self.trader_logger.error(f"Missing end time for market {condition_id}")
                return

            end_time = datetime.fromisoformat(end_time_utc.replace(" UTC", "+00:00"))

            if token_id_yes == "N/A" or token_id_no == "N/A":
                self.trader_logger.error(f"Invalid token IDs for market {condition_id}")
                return

            if not condition_id or condition_id == "N/A":
                self.trader_logger.error("Invalid condition ID")
                return

            assert (
                token_id_yes
                and token_id_no
                and token_id_yes != "N/A"
                and token_id_no != "N/A"
            )

            # Build MarketInfo for the feed
            minutes_until_end = (
                end_time - datetime.now(timezone.utc)
            ).total_seconds() / 60.0
            market_info = MarketInfo(
                condition_id=condition_id,
                ticker=market.get("ticker", ""),
                title=title or "",
                end_time_utc=end_time_utc,
                minutes_until_end=minutes_until_end,
                token_id_yes=token_id_yes,
                token_id_no=token_id_no,
                slug=market.get("slug") or "",
            )

            # Create the shared MarketFeed (owns WS + oracle for this market)
            feed = MarketFeed(
                market=market_info,
                feed_config=self.feed_config,
                logger=self.trader_logger,
            )

            trader = LastSecondTrader(
                condition_id=condition_id,
                token_id_yes=token_id_yes,
                token_id_no=token_id_no,
                end_time=end_time,
                dry_run=self.dry_run,
                trade_size=self.trade_size,
                title=title,
                slug=market.get("slug"),
                trader_logger=self.trader_logger,
                feed_config=self.feed_config,
                feed=feed,
                trade_db=self._trade_db,
                strategy=self.strategy,
                strategy_version=self.strategy_version,
                mode=self.mode,
            )

            # Register the FEED in the shared registry BEFORE awaiting run()
            # so that other runners can subscribe their own traders to it.
            self._shared_traders[condition_id] = feed
            self._traders[condition_id] = trader
            await asyncio.gather(feed.run(), trader.run())
            self.trader_logger.info(f"Trader finished for market {condition_id}")

        except Exception as e:
            self.trader_logger.error(
                f"Error running trader for market {condition_id}: {e}", exc_info=True
            )
        finally:
            if condition_id in self.active_traders:
                del self.active_traders[condition_id]
            self._traders.pop(condition_id, None)
            # Remove from shared registry — unblocks any runners waiting on this market.
            self._shared_traders.pop(condition_id, None)
            # Release the ticker slot so a future market for the same ticker can run
            ticker = market.get("ticker", "")
            if ticker:
                self.monitored_tickers.discard(ticker)

    async def _preload_monitored_markets(self) -> None:
        """Pre-populate monitored_markets from DB on startup to prevent duplicate traders.

        On process restart the in-memory set is empty, so the poll loop would
        happily start a new trader for a market that's still live in the DB.
        We block condition_ids that received a 'buy' trade in the last hour.
        """
        if self._trade_db is None:
            return
        cutoff = time.time() - 3600  # 1 hour lookback covers any live 15-min window
        try:
            async with self._trade_db._db.execute(
                "SELECT DISTINCT condition_id FROM trades WHERE timestamp > ? AND action = 'buy'",
                (cutoff,),
            ) as cur:
                rows = await cur.fetchall()
            if rows:
                for row in rows:
                    self.monitored_markets.add(row[0])
                self.finder_logger.info(
                    f"Restart dedup: pre-loaded {len(rows)} condition_id(s) into "
                    f"monitored_markets from DB (last 1h buys)"
                )
            # NOTE: Do NOT preload monitored_tickers — ticker locks are only
            # meaningful for the current process lifetime. On restart there are
            # no running traders, so all tickers should be available.
        except Exception as e:
            self.finder_logger.warning(f"Could not pre-load monitored_markets from DB: {e}")

    async def _shutdown_traders(self) -> None:
        if not self._traders:
            return
        self.finder_logger.info(
            f"Shutting down {len(self._traders)} active trader(s) gracefully..."
        )
        shutdown_tasks = []
        for cid, trader in list(self._traders.items()):
            self.finder_logger.info(f"  -> Shutting down trader for {cid}")
            shutdown_tasks.append(trader.graceful_shutdown(reason="Signal shutdown"))
        if shutdown_tasks:
            results = await asyncio.gather(*shutdown_tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    self.finder_logger.error(f"Error during trader shutdown: {result}")
        self.finder_logger.info("All traders shut down")

    async def _maybe_resolve_positions(self) -> None:
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
                dry_run=self.dry_run,
                strategy=self.strategy,
                strategy_version=self.strategy_version,
                mode=self.mode,
            )
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
        # Start health check server
        if self._health is not None:
            try:
                await self._health.start()
                self._health.set_status("running")
                self._health.set_extra("dry_run", self.dry_run)
                self._health.set_extra("trade_size", self.trade_size)
            except Exception as exc:
                self.finder_logger.warning(f"Health check server failed to start: {exc}")

        self.finder_logger.info("Starting market processing loop (fed by SharedFinder)...")

        while not self._shutdown_event.is_set():
            try:
                # Wait for a batch of markets from SharedFinder.
                # Timeout is generous — just a backstop to recheck shutdown.
                try:
                    batch = await asyncio.wait_for(
                        self.market_queue.get(),
                        timeout=self.poll_interval * 2,
                    )
                except asyncio.TimeoutError:
                    continue

                # None sentinel means SharedFinder has finished (run_once or shutdown)
                if batch is None:
                    self.finder_logger.info("No more batches from SharedFinder — exiting loop")
                    break

                markets: list[Dict[str, Any]] = batch

                if self._health is not None:
                    self._health.record_poll()
                    self._health.set_active_traders(len(self.active_traders))

                if markets:
                    self.finder_logger.info(f"Received {len(markets)} market(s) from SharedFinder")
                    self.finder_logger.debug(
                        f"Batch condition_ids: {[m.get('condition_id') for m in markets]} | "
                        f"monitored={self.monitored_markets} | monitored_tickers={self.monitored_tickers}"
                    )

                    # Strategy market_filter
                    filtered = []
                    for m in markets:
                        mi = MarketInfo(
                            condition_id=m.get("condition_id", ""),
                            ticker=m.get("ticker", ""),
                            title=m.get("title", ""),
                            end_time_utc=m.get("end_time_utc", ""),
                            minutes_until_end=m.get("minutes_until_end", 0.0),
                            token_id_yes=m.get("token_id_yes", ""),
                            token_id_no=m.get("token_id_no", ""),
                        )
                        if self._filter_strategy.market_filter(mi):
                            filtered.append(m)
                        else:
                            self.finder_logger.debug(
                                f"Strategy rejected market: {mi.ticker} {mi.title}"
                            )

                    if len(filtered) < len(markets):
                        self.finder_logger.info(
                            f"Strategy market_filter: {len(filtered)}/{len(markets)} markets accepted"
                        )

                    # Deduplicate within the batch:
                    # 1. by condition_id — prevent same market twice in one batch
                    # 2. by ticker     — prevent two different markets for the same
                    #                    ticker (e.g. two simultaneous BTC markets)
                    _seen_cids: set[str] = set()
                    _seen_tickers: set[str] = set()
                    eligible = []
                    for m in filtered:
                        cid = m.get("condition_id")
                        ticker = m.get("ticker", "")
                        if not cid:
                            continue
                        if cid in _seen_cids:
                            self.finder_logger.debug(
                                f"Batch dedup: skipping duplicate condition_id {cid}"
                            )
                            continue
                        if ticker and ticker in _seen_tickers:
                            self.finder_logger.debug(
                                f"Batch dedup: skipping second {ticker!r} market {cid} "
                                f"(already queued {_seen_tickers})"
                            )
                            continue
                        if not self.should_start_trader(m):
                            continue
                        _seen_cids.add(cid)
                        if ticker:
                            _seen_tickers.add(ticker)
                        eligible.append(m)

                    if eligible:
                        self.finder_logger.info(
                            f"Launching {len(eligible)} trader(s) in parallel"
                        )

                        launcher = ParallelLauncher(
                            max_concurrency=self.max_traders,
                            timeout=None,
                        )

                        async def _start_and_track(market: Dict[str, Any]) -> None:
                            cid = market.get("condition_id")
                            ticker = market.get("ticker", "")
                            # Eagerly mark as monitored so the next poll cycle cannot
                            # start a second trader before the background task runs.
                            self.monitored_markets.add(cid)
                            if ticker:
                                self.monitored_tickers.add(ticker)
                            self.finder_logger.debug(
                                f"Launching trader: cid={cid} ticker={ticker!r} | "
                                f"monitored_tickers now={self.monitored_tickers}"
                            )
                            task = asyncio.create_task(
                                self.start_trader_for_market(market)
                            )
                            self.active_traders[cid] = task

                        batch_result = await launcher.launch(eligible, _start_and_track)
                        self.finder_logger.info(
                            f"Parallel launch: {batch_result.succeeded}/{batch_result.total} ok "
                            f"in {batch_result.elapsed_ms:.0f}ms"
                        )
                else:
                    self.finder_logger.info("No markets for this strategy in current batch")

                await self._maybe_resolve_positions()

                if self.active_traders:
                    self.finder_logger.info(
                        f"Currently running {len(self.active_traders)} trader(s)"
                    )

                if self.run_once:
                    self.finder_logger.info("Run-once mode: exiting after single poll")
                    break

            except Exception as e:
                self.finder_logger.error(f"Error in poll loop: {e}", exc_info=True)
                if not self._shutdown_event.is_set():
                    await asyncio.sleep(self.poll_interval)

        await self._shutdown_traders()

        if self.active_traders:
            self.finder_logger.info(
                f"Waiting for {len(self.active_traders)} trader task(s) to finish..."
            )
            await asyncio.gather(*self.active_traders.values(), return_exceptions=True)

        if self._health is not None:
            self._health.set_status("stopped")
            await self._health.stop()

        self.finder_logger.info("Trading bot shut down cleanly")

    def _create_alert_manager(self):
        import os
        from src.alerts import AlertManager, TelegramAlertSender, SlackAlertSender

        context = {
            "strategy": self.strategy,
            "version": self.strategy_version,
            "mode": self.mode,
        }
        telegram = None
        slack = None
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if bot_token and chat_id:
            telegram = TelegramAlertSender(bot_token, chat_id, context=context)
        webhook = os.getenv("SLACK_WEBHOOK_URL")
        if webhook:
            slack = SlackAlertSender(webhook, context=context)
        return AlertManager(telegram=telegram, slack=slack)

    async def run(self):
        watchdog_task = None
        try:
            db_path = Path(self.db_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._trade_db = await TradeDatabase.initialize(str(db_path))
            self.finder_logger.info(f"TradeDatabase initialized: {db_path}")
            await self._preload_monitored_markets()

            alert_mgr = self._create_alert_manager()
            context = {
                "strategy": self.strategy,
                "version": self.strategy_version,
                "mode": self.mode,
            }
            watchdog_task = asyncio.create_task(
                watchdog_loop(
                    self._trade_db,
                    alert_mgr,
                    threshold_hours=self.watchdog_hours,
                    context=context,
                )
            )
            self.finder_logger.info(
                f"Watchdog active, threshold: {self.watchdog_hours:g}h"
            )

            await self.poll_and_trade()
        except KeyboardInterrupt:
            self.finder_logger.info("\nInterrupted by user")
            self._shutdown_event.set()
        except Exception as e:
            self.finder_logger.error(f"Fatal error: {e}", exc_info=True)
        finally:
            if watchdog_task is not None:
                watchdog_task.cancel()
                try:
                    await watchdog_task
                except asyncio.CancelledError:
                    pass
            if self._trade_db:
                await self._trade_db.close()
            self.finder_logger.info("=" * 80)
            self.finder_logger.info("Trading Bot Runner Stopped")
            self.finder_logger.info("=" * 80)


# ---------------------------------------------------------------------------
# SharedFinder — one API poller for all strategy runners
# ---------------------------------------------------------------------------

class SharedFinder:
    """
    Single Gamma API poller shared across all TradingBotRunner instances.

    Polls once per interval using the union of all runners' universes.
    Each found market is dispatched to every runner whose universe includes
    that ticker.  Runners receive a list[dict] batch via their market_queue;
    an empty list means "no markets this cycle" and None means "done".
    """

    def __init__(
        self,
        runners: list["TradingBotRunner"],
        shutdown_event: asyncio.Event,
        logger: logging.Logger,
    ):
        self.runners = runners
        self.shutdown_event = shutdown_event
        self.logger = logger
        # Union of all runner universes — fetch everything in one API call
        self.universe: list[str] = sorted(
            {ticker for r in runners for ticker in r.universe}
        )
        # Use the minimum poll interval so no strategy has to wait longer than needed
        self.poll_interval: int = min(r.poll_interval for r in runners)

        self.logger.info(
            f"[SharedFinder] universe={self.universe}, "
            f"poll_interval={self.poll_interval}s, "
            f"runners={len(runners)}"
        )

    async def _poll_once(self) -> list[Dict[str, Any]] | None:
        """Fetch markets from the API. Returns list (possibly empty) or None on error."""
        self.logger.info(
            f"[SharedFinder] Polling for active markets... (every {self.poll_interval}s)"
        )
        try:
            finder = GammaAPI15mFinder(logger=self.logger, tickers=self.universe)
            markets = await finder.find_active_market()
            return markets or []
        except Exception as e:
            self.logger.error(f"[SharedFinder] Error finding markets: {e}", exc_info=True)
            return None

    async def _dispatch(self, markets: list[Dict[str, Any]] | None) -> None:
        """Put each runner's slice of markets into its queue."""
        for idx, runner in enumerate(self.runners):
            if markets is None:
                runner_batch: list[Dict[str, Any]] = []
            else:
                runner_batch = [
                    m for m in markets if m.get("ticker", "") in runner.universe
                ]
            self.logger.debug(
                f"[SharedFinder] dispatch runner[{idx}] "
                f"strategy={runner.strategy}/{runner.strategy_version} "
                f"batch_size={len(runner_batch)} "
                f"condition_ids={[m.get('condition_id') for m in runner_batch]}"
            )
            await runner.market_queue.put(runner_batch)

    async def run(self, run_once: bool = False) -> None:
        """Main poll loop. Runs until shutdown_event is set or run_once=True."""
        while not self.shutdown_event.is_set():
            markets = await self._poll_once()
            if markets:
                self.logger.info(
                    f"[SharedFinder] Found {len(markets)} market(s), dispatching..."
                )
            else:
                self.logger.info("[SharedFinder] No active markets found")

            await self._dispatch(markets)

            if run_once:
                break

            jitter = random.uniform(0.85, 1.15)
            sleep_for = max(1, int(self.poll_interval * jitter))
            self.logger.info(
                f"[SharedFinder] Sleeping {sleep_for}s before next poll (jitter {jitter:.2f}x)"
            )
            try:
                await asyncio.wait_for(
                    self.shutdown_event.wait(), timeout=sleep_for
                )
                self.logger.info("[SharedFinder] Shutdown requested — stopping")
                break
            except asyncio.TimeoutError:
                pass

        # Send None sentinel to all runners so they can exit cleanly
        for runner in self.runners:
            await runner.market_queue.put(None)


# ---------------------------------------------------------------------------
# Multi-strategy orchestrator
# ---------------------------------------------------------------------------

def _register_signal_handlers(
    loop: asyncio.AbstractEventLoop,
    shutdown_event: asyncio.Event,
    logger: logging.Logger,
) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig,
            functools.partial(_handle_signal, sig, shutdown_event, logger),
        )
    logger.info("Signal handlers registered (SIGINT, SIGTERM)")


def _register_sigint_only(
    loop: asyncio.AbstractEventLoop,
    cancel_event: asyncio.Event,
    logger: logging.Logger,
) -> None:
    """Register SIGINT-only handler for the startup countdown.

    During the countdown we want Ctrl+C to abort but SIGTERM (sent by Docker
    on restart) must NOT abort — it will be handled properly after countdown.
    """
    loop.add_signal_handler(
        signal.SIGINT,
        functools.partial(_handle_signal, signal.SIGINT, cancel_event, logger),
    )


def _handle_signal(
    sig: signal.Signals,
    shutdown_event: asyncio.Event,
    logger: logging.Logger,
) -> None:
    logger.warning(f"Received {sig.name} — initiating graceful shutdown...")
    shutdown_event.set()


async def _send_startup_notification(strategy_configs: list[StrategyConfig]) -> None:
    """Send a Telegram startup notification if bot token/chat id are configured."""
    import os
    import aiohttp

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        return

    lines = ["🤖 *Polymarket bot started*\n"]
    for sc in strategy_configs:
        mode_emoji = "🔴" if sc.mode == "live" else "🧪"
        lines.append(
            f"{mode_emoji} `{sc.name}/{sc.version}` | mode=`{sc.mode}` | "
            f"universe=`{','.join(sc.universe)}` | size=`${sc.size}`"
        )
    text = "\n".join(lines)

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logging.getLogger("main").warning(
                        f"Startup notification failed: HTTP {resp.status} — {body[:200]}"
                    )
    except Exception as e:
        logging.getLogger("main").warning(f"Startup notification error: {e}")


async def main():
    """Config-driven entry point. Reads strategies.yaml, launches all in parallel."""
    default_config = str(Path(__file__).parent / "config" / "strategies.yaml")

    parser = argparse.ArgumentParser(
        description="Polymarket trading bot — config-driven multi-strategy runner"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=default_config,
        help=f"Path to strategies YAML config (default: {default_config})",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one poll cycle per strategy, then exit",
    )
    args = parser.parse_args()

    # Load config
    strategy_configs = load_strategies_config(args.config)
    print(f"Loaded {len(strategy_configs)} strategy config(s) from {args.config}")

    # Check for live mode — safety warning
    has_live = any(sc.mode == "live" for sc in strategy_configs)
    if has_live:
        print("\n" + "=" * 80)
        print("WARNING: LIVE TRADING MODE ENABLED for some strategies")
        print("=" * 80)
        print("This bot will execute REAL trades with REAL money!")
        print("Press Ctrl+C within 5 seconds to cancel...")
        print("=" * 80 + "\n")

    # Shared shutdown event across all runners
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    root_logger = logging.getLogger("main")

    if has_live:
        # During the 5-second countdown only SIGINT (Ctrl+C) should abort.
        # SIGTERM arrives during Docker restarts and must NOT cancel the bot —
        # we will register the full SIGTERM handler after the countdown.
        _cancel_event = asyncio.Event()
        _register_sigint_only(loop, _cancel_event, root_logger)
        try:
            await asyncio.wait_for(_cancel_event.wait(), timeout=5)
            root_logger.info("Startup cancelled by SIGINT during countdown")
            return  # Ctrl+C during countdown
        except asyncio.TimeoutError:
            pass
        # Unregister the temporary SIGINT handler before registering both
        loop.remove_signal_handler(signal.SIGINT)

    # Register full signal handlers (SIGINT + SIGTERM → graceful shutdown)
    _register_signal_handlers(loop, shutdown_event, root_logger)

    # Discover strategy plugins (must be called before MarketOrchestrator.run())
    n_discovered = discover_strategies()
    root_logger.info(f"Discovered {n_discovered} strategy plugin(s)")

    # Build StrategyRegistry from loaded configs
    registry = StrategyRegistry()
    for sc in strategy_configs:
        # Use universe from YAML config as the ticker list
        reg = StrategyRegistration(
            name=sc.name,
            version=sc.version,
            mode=sc.mode,
            size=sc.size,
            tickers=sc.universe,
            dry_run=sc.dry_run,
        )
        registry.register(reg)
        print(f"  Registered {sc.name}/{sc.version} mode={sc.mode} size={sc.size} "
              f"universe={','.join(sc.universe)} db={sc.db_path}")

    # Feed config (shared across all strategies)
    feed_config = MarketFeedConfig()

    # Determine poll interval from configs (use smallest non-zero)
    poll_intervals = [sc.poll_interval for sc in strategy_configs if sc.poll_interval > 0]
    poll_interval = min(poll_intervals) if poll_intervals else 90

    # Send startup notification to Telegram
    await _send_startup_notification(strategy_configs)

    # Health server — start once (equivalent to first runner's health server)
    health_server = HealthCheckServer()
    asyncio.create_task(health_server.start())

    # MarketOrchestrator — single orchestration point (replaces SharedFinder + TradingBotRunner)
    orchestrator = MarketOrchestrator(
        registry=registry,
        strategy_configs=strategy_configs,
        feed_config=feed_config,
        poll_interval=poll_interval,
        shutdown_event=shutdown_event,
        run_once=args.once,
        logger=root_logger,
    )

    await orchestrator.run()


if __name__ == "__main__":
    asyncio.run(main())
