"""StrategyRunner — lightweight wrapper: one strategy instance + execution context.

Replaces the StrategySlot dataclass with a class that owns its on_tick()
logic, separating per-strategy execution state from the shared market
infrastructure (WS, oracle, orderbook) that lives in MarketFeed.

Usage::

    runner = StrategyRunner(
        strategy_name="convergence",
        strategy_version="v2",
        strategy_instance=my_strategy,
        order_execution=oem,
        dry_run_sim=sim,
        dry_run=True,
        mode="test",
        market_name="BTC",
    )
    await runner.on_tick(tick, oracle_guard, risk_manager)
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.clob_types import MAX_ENTRY_PRICE
from src.trading.oracle_guard_manager import OracleGuardManager
from src.trading.risk_manager import RiskManager
from strategies.base import MarketTick, Signal


class StrategyRunner:
    """Per-strategy execution context for one active market.

    All runners for the same market share the same ``MarketFeed``
    (WS + oracle + orderbook). Each runner owns its own:
    - strategy instance
    - order execution manager (OEM)
    - dry-run simulator
    - recorded_skip_guards set (one-shot skip dedup)
    """

    def __init__(
        self,
        *,
        strategy_name: str,
        strategy_version: str,
        strategy_instance: Any,        # BaseStrategy
        order_execution: Any,          # OrderExecutionManager
        dry_run_sim: Any | None,       # DryRunSimulator | None
        dry_run: bool,
        mode: str,
        market_name: str = "UNKNOWN",
        logger: logging.Logger | None = None,
    ) -> None:
        self.strategy_name = strategy_name
        self.strategy_version = strategy_version
        self.strategy_instance = strategy_instance
        self.order_execution = order_execution
        self.dry_run_sim = dry_run_sim
        self.dry_run = dry_run
        self.mode = mode
        self.market_name = market_name
        self._logger = logger
        self.recorded_skip_guards: set[str] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def on_tick(
        self,
        tick: MarketTick,
        oracle_guard: OracleGuardManager,
        risk_manager: RiskManager,
        get_ask_for_side: Any,   # Callable[[str], float | None]
        trade_size: float,
        _log: Any,               # Callable[[str], None]
    ) -> bool:
        """Evaluate one market tick and execute if strategy signals.

        Returns True if execution was attempted (signal fired, oracle ok,
        price ok, execute_order_for called), False otherwise.

        Called by LastSecondTrader.check_trigger() for each runner.
        """
        time_remaining = tick.time_remaining

        if self.order_execution.is_executed() or self.order_execution.is_in_progress():
            return False

        # Per-slot attempt limit
        if self.order_execution.get_attempts() >= self.order_execution.get_max_attempts():
            if "max_attempts" not in self.recorded_skip_guards:
                self.recorded_skip_guards.add("max_attempts")
                _log(
                    f"⚠️  [{self.market_name}] Max order attempts "
                    f"({self.order_execution.get_max_attempts()}) reached "
                    f"({self.strategy_name}/{self.strategy_version})"
                )
                if self.dry_run_sim:
                    await self.dry_run_sim.record_skip(
                        reason="max_attempts",
                        time_remaining=time_remaining,
                    )
            return False

        # Per-slot retry cooldown (2 s between attempts)
        if (
            self.order_execution.get_attempts() > 0
            and (time.time() - self.order_execution.get_last_attempt_time()) < 2.0
        ):
            return False

        signal: Signal | None = self.strategy_instance.get_signal(tick)
        if signal is None:
            return False

        # Oracle freshness check
        oracle_ok, block_reason, block_detail = oracle_guard.quality_ok_for_convergence()
        if not oracle_ok:
            _log(
                f"⛔ [{self.market_name}] {self.strategy_name.upper()}/{self.strategy_version} "
                f"blocked: oracle_quality={block_reason} ({block_detail})"
            )
            if self.dry_run_sim and block_reason not in self.recorded_skip_guards:
                self.recorded_skip_guards.add(block_reason)
                await self.dry_run_sim.record_skip(
                    reason=block_reason,
                    reason_detail=block_detail,
                    time_remaining=time_remaining,
                    oracle_snap=oracle_guard.snapshot,
                )
            return False

        meta = signal.metadata
        _log(
            f"🎯 [{self.market_name}] {self.strategy_name.upper()}/{self.strategy_version} TRIGGER! "
            f"{signal.side} ({meta.get('side_label', '')}) @ ${signal.price:.4f} | "
            f"delta_pct={meta.get('delta_pct', 0) * 100:+.4f}% | "
            f"skew={meta.get('expensive_price', 0):.2f}/{signal.price:.2f} | "
            f"obs={meta.get('observations', 0)} | "
            f"conv_rate={meta.get('convergence_rate', 0):.0%} | "
            f"side_cons={meta.get('side_consistency', 0):.0%} | "
            f"t={time_remaining:.1f}s"
        )

        side = signal.side
        winning_ask = get_ask_for_side(side)
        if winning_ask is not None and winning_ask > MAX_ENTRY_PRICE:
            _log(
                f"[{self.market_name}] Price {winning_ask:.4f} above max entry "
                f"{MAX_ENTRY_PRICE} — skipping {self.strategy_name}/{self.strategy_version}"
            )
            return False

        _was_executed = self.order_execution.is_executed()
        await self.order_execution.execute_order_for(side, winning_ask)

        if (
            self.dry_run_sim
            and self.dry_run
            and not _was_executed
            and self.order_execution.is_executed()
        ):
            await self.dry_run_sim.record_buy(
                side=signal.side,
                price=signal.price,
                amount=trade_size,
                confidence=meta.get("confidence", 0.0),
                time_remaining=time_remaining,
                reason=meta.get("reason", self.strategy_name),
                oracle_snap=oracle_guard.snapshot,
                disable_stop_loss=signal.disable_stop_loss,
            )

        return True

    def reset(self) -> None:
        """Reset strategy accumulator for a new market cycle."""
        if self.strategy_instance is not None:
            self.strategy_instance.reset()

    async def shutdown(self) -> None:
        """Persist any open position state before stopping."""
        pass  # Position persistence is handled by PositionManager in the trader
