"""MarketOrchestrator — single orchestration point replacing SharedFinder + TradingBotRunner.

Polls GammaAPI once per interval using the union of all registered strategy
tickers.  For each discovered market it creates one MarketFeed (owns WS +
oracle) and one LastSecondTrader per matching strategy registration.  All
feeds and traders for a market run concurrently under a single asyncio task.

This replaces:
- ``SharedFinder`` (API polling + dispatch to runner queues)
- Multiple ``TradingBotRunner`` instances (one per strategy)

Health server and watchdog remain standalone (injected via constructor).
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict

from src.gamma_15m_finder import GammaAPI15mFinder
from src.hft_trader import LastSecondTrader
from src.logging_config import setup_bot_loggers
from src.market_feed import MarketFeed
from src.strategy_registry import StrategyRegistration, StrategyRegistry
from src.trading.market_feed_config import MarketFeedConfig
from src.trading.trade_db import TradeDatabase
from strategies import load_strategy
from strategies.base import MarketInfo


class MarketOrchestrator:
    """Single orchestration point for all strategies across all markets.

    Replaces ``SharedFinder`` + multiple ``TradingBotRunner`` instances.

    One ``MarketFeed`` per active market; one ``LastSecondTrader`` per
    (market × strategy registration) combination.
    """

    TRADER_START_WINDOW_MAX = 900  # don't start a trader with > 15 min left
    TRADER_START_WINDOW_MIN = 20   # don't start a trader with < 20 s left
    RESOLVE_EVERY_N_CYCLES = 10

    def __init__(
        self,
        registry: StrategyRegistry,
        strategy_configs: list[Any],   # list[StrategyConfig] — avoid circular import
        feed_config: MarketFeedConfig,
        poll_interval: int,
        shutdown_event: asyncio.Event,
        run_once: bool = False,
        max_concurrent: int = 3,
        logger: logging.Logger | None = None,
    ) -> None:
        self.registry = registry
        self.strategy_configs = strategy_configs
        self.feed_config = feed_config
        self.poll_interval = poll_interval
        self.shutdown_event = shutdown_event
        self.run_once = run_once
        self.max_concurrent = max_concurrent

        # Logging
        self._finder_logger, self._trader_logger, _ = setup_bot_loggers()
        if logger:
            self._finder_logger = logger

        # Per-strategy trade databases (keyed by db_path)
        self._trade_dbs: dict[str, TradeDatabase] = {}

        # Per-strategy filter instances (for market_filter())
        self._filter_strategies: dict[str, Any] = {}

        # Active feeds and market tracking
        self.active_feeds: dict[str, MarketFeed] = {}
        self.active_tasks: dict[str, asyncio.Task] = {}
        self.monitored_markets: set[str] = set()
        self.monitored_tickers: set[str] = set()
        # Ticker-level dedup that survives restarts: market_name → expiry timestamp.
        # Populated from DB on startup so a new 15-min window for the same ticker
        # doesn't fire a duplicate alert immediately after a docker restart.
        self._preloaded_tickers_expiry: dict[str, float] = {}

        self._poll_cycle = 0

        # Note: filter strategies loaded lazily in run() after discover_strategies()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Main orchestration loop: poll → create feeds → run traders."""
        await self._init_trade_dbs()
        await self._preload_monitored_markets()

        # Load filter strategies now (after discover_strategies() called in main)
        for sc in self.strategy_configs:
            key = f"{sc.name}/{sc.version}/{sc.mode}"
            try:
                self._filter_strategies[key] = load_strategy(sc.name, sc.version)
            except Exception as e:
                self._finder_logger.warning(
                    f"Could not load filter strategy {sc.name}/{sc.version}: {e}"
                )

        self._finder_logger.info(
            f"[Orchestrator] Starting | tickers={self.registry.all_tickers()} "
            f"poll={self.poll_interval}s registrations={len(self.registry.all_registrations())}"
        )

        while not self.shutdown_event.is_set():
            try:
                markets = await self._poll_once()
                if markets:
                    await self._process_batch(markets)

                self._poll_cycle += 1
                if self._poll_cycle % self.RESOLVE_EVERY_N_CYCLES == 0:
                    await self._maybe_resolve_positions()

                if self.run_once:
                    self._finder_logger.info("[Orchestrator] run_once: exiting after single poll")
                    break

                if not self.shutdown_event.is_set():
                    # Interruptible sleep: wake up early if shutdown is requested
                    try:
                        await asyncio.wait_for(
                            self.shutdown_event.wait(),
                            timeout=self.poll_interval,
                        )
                    except asyncio.TimeoutError:
                        pass

            except Exception as e:
                self._finder_logger.error(f"[Orchestrator] Error in poll loop: {e}", exc_info=True)
                if not self.shutdown_event.is_set():
                    await asyncio.sleep(min(self.poll_interval, 30))

        await self._shutdown_all()
        await self._close_trade_dbs()
        self._finder_logger.info("[Orchestrator] Shut down cleanly")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _init_trade_dbs(self) -> None:
        """Open one SQLite DB per strategy config."""
        for sc in self.strategy_configs:
            db_path = sc.db_path
            if db_path not in self._trade_dbs:
                db = await TradeDatabase.initialize(db_path)
                self._trade_dbs[db_path] = db
                self._finder_logger.info(
                    f"[Orchestrator] DB opened: {db_path} ({sc.name}/{sc.version}/{sc.mode})"
                )

    async def _close_trade_dbs(self) -> None:
        for db in self._trade_dbs.values():
            try:
                await db.close()
            except Exception:
                pass

    async def _preload_monitored_markets(self) -> None:
        """Pre-populate monitored_markets/tickers from DB to prevent duplicate alerts on restart.

        Two layers of dedup:
        1. condition_id (1-hour window): blocks the exact same 15-min market from restarting.
        2. market_name/ticker (2× window): blocks the NEXT 15-min window for a ticker that
           was traded recently.  New 15-min BTC markets have new condition_ids, so they
           escape layer-1; this layer catches them.  Expiry is stored per-ticker so the
           block lifts automatically once the window passes.
        """
        now = time.time()
        condition_cutoff = now - 3600
        # 2× TRADER_START_WINDOW_MAX covers the current + next 15-min window
        ticker_cutoff = now - self.TRADER_START_WINDOW_MAX * 2
        for db in self._trade_dbs.values():
            try:
                # Layer 1: same condition_id
                async with db._db.execute(
                    "SELECT DISTINCT condition_id FROM trades WHERE timestamp > ? AND action = 'buy'",
                    (condition_cutoff,),
                ) as cur:
                    rows = await cur.fetchall()
                for row in rows:
                    self.monitored_markets.add(row[0])

                # Layer 2: same ticker (market_name) — survives condition_id rotation
                async with db._db.execute(
                    "SELECT market_name, MAX(timestamp) as last_ts"
                    " FROM trades WHERE timestamp > ? AND action = 'buy'"
                    " GROUP BY market_name",
                    (ticker_cutoff,),
                ) as cur:
                    rows = await cur.fetchall()
                for row in rows:
                    name, last_ts = row[0], row[1]
                    if name and last_ts:
                        expiry = last_ts + self.TRADER_START_WINDOW_MAX * 2
                        # Keep the latest expiry if the same ticker appears in multiple DBs
                        if expiry > self._preloaded_tickers_expiry.get(name, 0):
                            self._preloaded_tickers_expiry[name] = expiry
            except Exception as e:
                self._finder_logger.warning(f"[Orchestrator] Could not preload markets from DB: {e}")
        if self.monitored_markets:
            self._finder_logger.info(
                f"[Orchestrator] Restart dedup: {len(self.monitored_markets)} condition_id(s) pre-loaded"
            )
        if self._preloaded_tickers_expiry:
            remaining = {k: f"{max(0, v - now):.0f}s" for k, v in self._preloaded_tickers_expiry.items()}
            self._finder_logger.info(
                f"[Orchestrator] Restart dedup: ticker block(s) pre-loaded: {remaining}"
            )

    async def _poll_once(self) -> list[Dict[str, Any]]:
        """Fetch active markets from GammaAPI."""
        tickers = self.registry.all_tickers()
        self._finder_logger.info(f"[Orchestrator] Polling for active markets... tickers={tickers}")
        try:
            finder = GammaAPI15mFinder(logger=self._finder_logger, tickers=tickers)
            markets = await finder.find_active_market()
            return markets or []
        except Exception as e:
            self._finder_logger.error(f"[Orchestrator] Error polling API: {e}", exc_info=True)
            return []

    def _should_start_market(self, market: Dict[str, Any]) -> bool:
        """Check if this market should be started (time window + dedup)."""
        condition_id = market.get("condition_id")
        minutes_until_end = market.get("minutes_until_end", 0)
        seconds_until_end = minutes_until_end * 60

        if condition_id in self.monitored_markets:
            return False

        ticker = market.get("ticker", "")
        if ticker and ticker in self.monitored_tickers:
            self._finder_logger.info(
                f"[Orchestrator] Skipping {condition_id}: ticker {ticker!r} already active"
            )
            return False

        # Ticker dedup from DB preload: block a new condition_id for this ticker
        # if it was traded recently (within 2× the market window on the previous run).
        expiry = self._preloaded_tickers_expiry.get(ticker, 0)
        if expiry > time.time():
            self._finder_logger.info(
                f"[Orchestrator] Skipping {condition_id}: ticker {ticker!r} recently traded "
                f"(restart dedup expires in {expiry - time.time():.0f}s)"
            )
            return False

        if seconds_until_end > self.TRADER_START_WINDOW_MAX:
            self._finder_logger.debug(
                f"[Orchestrator] {condition_id}: too early ({seconds_until_end:.0f}s left)"
            )
            return False

        if seconds_until_end < self.TRADER_START_WINDOW_MIN:
            self._finder_logger.info(
                f"[Orchestrator] {condition_id}: too late ({seconds_until_end:.0f}s left), skipping"
            )
            return False

        return True

    async def _process_batch(self, markets: list[Dict[str, Any]]) -> None:
        """Filter markets and launch traders for eligible ones."""
        # Filter: each strategy's market_filter must accept it
        # A market is eligible if AT LEAST ONE registered strategy accepts it
        eligible_markets = []
        seen_cids: set[str] = set()
        seen_tickers: set[str] = set()

        for m in markets:
            cid = m.get("condition_id")
            ticker = m.get("ticker", "")
            if not cid or cid in seen_cids:
                continue
            if ticker and ticker in seen_tickers:
                continue
            if not self._should_start_market(m):
                continue

            # Check that at least one registered strategy wants this market
            mi = MarketInfo(
                condition_id=cid,
                ticker=ticker,
                title=m.get("title", ""),
                end_time_utc=m.get("end_time_utc", ""),
                minutes_until_end=m.get("minutes_until_end", 0.0),
                token_id_yes=m.get("token_id_yes", ""),
                token_id_no=m.get("token_id_no", ""),
                slug=m.get("slug") or "",
            )

            matching_regs = [
                reg for reg in self.registry.runners_for(ticker)
                if self._strategy_accepts_market(reg, mi)
            ]
            if not matching_regs:
                continue

            seen_cids.add(cid)
            if ticker:
                seen_tickers.add(ticker)
            eligible_markets.append((m, matching_regs))

        if not eligible_markets:
            self._finder_logger.info("[Orchestrator] No eligible markets this cycle")
            return

        self._finder_logger.info(
            f"[Orchestrator] Launching {len(eligible_markets)} market(s) in parallel"
        )

        launched = 0
        for market, regs in eligible_markets:
            cid = market.get("condition_id")
            ticker = market.get("ticker", "")
            self.monitored_markets.add(cid)
            if ticker:
                self.monitored_tickers.add(ticker)
            task = asyncio.create_task(self._run_market(market, regs))
            self.active_tasks[cid] = task
            launched += 1

        self._finder_logger.info(
            f"[Orchestrator] Launched {launched}/{len(eligible_markets)} market(s)"
        )

    def _strategy_accepts_market(
        self, reg: StrategyRegistration, mi: MarketInfo
    ) -> bool:
        key = f"{reg.name}/{reg.version}/{reg.mode}"
        fs = self._filter_strategies.get(key)
        if fs is None:
            return True  # no filter → accept
        try:
            return fs.market_filter(mi)
        except Exception:
            return True

    async def _run_market(
        self, market: Dict[str, Any], regs: list[StrategyRegistration]
    ) -> None:
        """Create MarketFeed + traders for all matching registrations, run together."""
        condition_id = market.get("condition_id")
        token_id_yes = market.get("token_id_yes", "")
        token_id_no = market.get("token_id_no", "")
        end_time_utc = market.get("end_time_utc", "")
        title = market.get("title", "")
        ticker = market.get("ticker", "")

        try:
            end_time = datetime.fromisoformat(end_time_utc.replace(" UTC", "+00:00"))
            minutes_until_end = (
                end_time - datetime.now(timezone.utc)
            ).total_seconds() / 60.0

            market_info = MarketInfo(
                condition_id=condition_id,
                ticker=ticker,
                title=title,
                end_time_utc=end_time_utc,
                minutes_until_end=minutes_until_end,
                token_id_yes=token_id_yes,
                token_id_no=token_id_no,
                slug=market.get("slug") or "",
            )

            feed = MarketFeed(
                market=market_info,
                feed_config=self.feed_config,
                logger=self._trader_logger,
            )
            self.active_feeds[condition_id] = feed

            traders = []
            for reg in regs:
                sc = next(
                    (c for c in self.strategy_configs
                     if c.name == reg.name and c.version == reg.version and c.mode == reg.mode),
                    None,
                )
                if sc is None:
                    continue
                trade_db = self._trade_dbs.get(sc.db_path)

                trader = LastSecondTrader(
                    condition_id=condition_id,
                    token_id_yes=token_id_yes,
                    token_id_no=token_id_no,
                    end_time=end_time,
                    dry_run=sc.dry_run,
                    trade_size=sc.size,
                    title=title,
                    slug=market.get("slug"),
                    trader_logger=self._trader_logger,
                    feed_config=self.feed_config,
                    feed=feed,
                    trade_db=trade_db,
                    strategy=sc.name,
                    strategy_version=sc.version,
                    mode=sc.mode,
                )
                traders.append(trader)

            if not traders:
                self._finder_logger.warning(
                    f"[Orchestrator] No traders created for {condition_id}"
                )
                return

            self._finder_logger.info(
                f"[Orchestrator] Running {len(traders)} trader(s) for {ticker} {condition_id}"
            )

            await asyncio.gather(
                feed.run(),
                *(t.run() for t in traders),
            )
            self._finder_logger.info(f"[Orchestrator] Market finished: {condition_id}")

        except Exception as e:
            self._finder_logger.error(
                f"[Orchestrator] Error running market {condition_id}: {e}", exc_info=True
            )
        finally:
            self.active_feeds.pop(condition_id, None)
            self.active_tasks.pop(condition_id, None)
            if ticker:
                self.monitored_tickers.discard(ticker)

    async def _shutdown_all(self) -> None:
        """Gracefully shut down all active feeds and wait for tasks."""
        if not self.active_feeds:
            return
        self._finder_logger.info(
            f"[Orchestrator] Shutting down {len(self.active_feeds)} active feed(s)..."
        )
        for feed in list(self.active_feeds.values()):
            try:
                await feed.shutdown()
            except Exception:
                pass
        if self.active_tasks:
            await asyncio.gather(*self.active_tasks.values(), return_exceptions=True)

    async def _maybe_resolve_positions(self) -> None:
        """Periodically resolve open dry-run positions."""
        from src.trading.dry_run_simulator import DryRunSimulator
        for sc in self.strategy_configs:
            if not sc.dry_run:
                continue
            db = self._trade_dbs.get(sc.db_path)
            if db is None:
                continue
            try:
                open_positions = await db.get_open_dry_run_positions()
                if not open_positions:
                    continue
                sim = DryRunSimulator(
                    db=db,
                    market_name="resolver",
                    condition_id="resolver",
                    dry_run=sc.dry_run,
                    strategy=sc.name,
                    strategy_version=sc.version,
                    mode=sc.mode,
                )
                from src.position_settler import _create_clob_client
                try:
                    client = _create_clob_client(self._finder_logger)
                    resolved = await sim.resolve_all_markets(client)
                    if resolved:
                        wins = sum(1 for r in resolved if r["status"] == "resolved_win")
                        losses = sum(1 for r in resolved if r["status"] == "resolved_loss")
                        total_pnl = sum(r["pnl"] for r in resolved)
                        self._finder_logger.info(
                            f"[Orchestrator] Resolved {len(resolved)} positions "
                            f"({sc.name}/{sc.version}): {wins}W/{losses}L PnL=${total_pnl:.4f}"
                        )
                except (SystemExit, Exception):
                    pass
            except Exception as e:
                self._finder_logger.error(f"[Orchestrator] Resolution error: {e}", exc_info=True)
