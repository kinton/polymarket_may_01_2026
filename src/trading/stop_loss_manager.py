"""
Stop-loss manager for handling stop-loss, take-profit, and trailing-stop logic.

Monitors position and triggers sell orders when thresholds are breached.
"""

import time
from typing import Any

from src.clob_types import (
    STOP_LOSS_ABSOLUTE,
    STOP_LOSS_CHECK_INTERVAL_S,
    STOP_LOSS_PCT,
    TAKE_PROFIT_CHECK_INTERVAL_S,
    TAKE_PROFIT_PCT,
    TRAILING_STOP_PCT,
)
from src.trading.position_manager import PositionManager


class StopLossManager:
    """
    Manages stop-loss, take-profit, and trailing-stop logic.

    When thresholds are breached, executes sell orders via the provided callback.
    """

    def __init__(
        self,
        position_manager: PositionManager,
        logger: Any | None = None,
    ):
        """
        Initialize stop-loss manager.

        Args:
            position_manager: PositionManager instance to track position state
            logger: Optional logger for logging events
        """
        self.position_manager = position_manager
        self.logger = logger

        # Throttling state
        self._last_stop_loss_check_ts = 0.0
        self._last_take_profit_check_ts = 0.0
        self._last_trailing_stop_update_ts = 0.0

        # Callback for executing sell orders
        self._sell_callback: Any | None = None

    def set_sell_callback(self, callback: Any) -> None:
        """
        Set callback for executing sell orders.

        Args:
            callback: Async function that takes reason argument ("STOP-LOSS" or "TAKE-PROFIT")
        """
        self._sell_callback = callback

    async def check_and_execute(self, current_price: float) -> bool:
        """
        Check stop-loss, take-profit, and trailing-stop conditions.

        Args:
            current_price: Current market price for the position side

        Returns:
            True if a position was closed, False otherwise
        """
        if not self.position_manager.is_open or not self.position_manager.has_entry:
            return False

        now = time.time()

        # Check stop-loss (throttled)
        stop_triggered = await self._check_stop_loss(current_price, now)
        if stop_triggered:
            return True

        # Check take-profit (throttled)
        take_profit_triggered = await self._check_take_profit(current_price, now)
        if take_profit_triggered:
            return True

        # Update trailing stop (throttled)
        self._update_trailing_stop(current_price, now)

        return False

    async def _check_stop_loss(self, current_price: float, now: float) -> bool:
        """
        Check if stop-loss should trigger.

        Args:
            current_price: Current market price
            now: Current timestamp

        Returns:
            True if stop-loss triggered and executed
        """
        if (now - self._last_stop_loss_check_ts) < STOP_LOSS_CHECK_INTERVAL_S:
            return False

        self._last_stop_loss_check_ts = now

        # Use trailing stop if available, otherwise use initial stop level
        stop_price = (
            self.position_manager.trailing_stop_price
            if self.position_manager.trailing_stop_price is not None
            else (
                max(
                    self.position_manager.entry_price * (1 - STOP_LOSS_PCT),
                    STOP_LOSS_ABSOLUTE,
                )
                if self.position_manager.entry_price is not None
                else STOP_LOSS_ABSOLUTE
            )
        )

        if current_price < stop_price:
            pnl_pct = 0.0
            if self.position_manager.entry_price is not None:
                pnl_pct = (
                    (current_price - self.position_manager.entry_price)
                    / self.position_manager.entry_price
                ) * 100

            if self.logger:
                self.logger.info(
                    f"STOP-LOSS TRIGGERED: Price ${current_price:.4f} < "
                    + f"Stop ${stop_price:.4f} | PnL: {pnl_pct:.2f}%"
                )

            if self._sell_callback:
                await self._sell_callback("STOP-LOSS")
            return True

        return False

    async def _check_take_profit(self, current_price: float, now: float) -> bool:
        """
        Check if take-profit should trigger.

        Args:
            current_price: Current market price
            now: Current timestamp

        Returns:
            True if take-profit triggered and executed
        """
        if (now - self._last_take_profit_check_ts) < TAKE_PROFIT_CHECK_INTERVAL_S:
            return False

        self._last_take_profit_check_ts = now

        # Return False if no entry price set
        if self.position_manager.entry_price is None:
            return False

        take_profit_price = self.position_manager.entry_price * (1 + TAKE_PROFIT_PCT)

        if current_price > take_profit_price:
            pnl_pct = (
                (current_price - self.position_manager.entry_price)
                / self.position_manager.entry_price
            ) * 100

            if self.logger:
                self.logger.info(
                    f"TAKE-PROFIT TRIGGERED: Price ${current_price:.4f} > "
                    + f"Target ${take_profit_price:.4f} | PnL: +{pnl_pct:.2f}%"
                )

            if self._sell_callback:
                await self._sell_callback("TAKE-PROFIT")
            return True

        return False

    def _update_trailing_stop(self, current_price: float, now: float) -> None:
        """
        Update trailing stop if price moved in our favor.

        Args:
            current_price: Current market price
            now: Current timestamp
        """
        if (now - self._last_trailing_stop_update_ts) < STOP_LOSS_CHECK_INTERVAL_S:
            return

        self._last_trailing_stop_update_ts = now

        # Calculate new trailing stop level based on current high water mark
        new_trailing_stop = max(
            current_price * (1 - TRAILING_STOP_PCT),
            STOP_LOSS_ABSOLUTE,
        )

        # Initialize trailing stop if not set yet
        if self.position_manager.trailing_stop_price is None:
            self.position_manager.update_trailing_stop(new_trailing_stop)
        # Only raise the stop, never lower it
        elif new_trailing_stop > self.position_manager.trailing_stop_price:
            self.position_manager.update_trailing_stop(new_trailing_stop)

    def get_stop_loss_price(self) -> float | None:
        """
        Get current stop-loss price.

        Returns:
            Current stop-loss price, or None if no position
        """
        if not self.position_manager.is_open:
            return None

        if self.position_manager.trailing_stop_price is not None:
            return self.position_manager.trailing_stop_price

        if self.position_manager.entry_price is None:
            return None

        return max(
            self.position_manager.entry_price * (1 - STOP_LOSS_PCT),
            STOP_LOSS_ABSOLUTE,
        )

    def get_take_profit_price(self) -> float | None:
        """
        Get current take-profit price.

        Returns:
            Take-profit price, or None if no position
        """
        if not self.position_manager.is_open or not self.position_manager.has_entry:
            return None

        if self.position_manager.entry_price is None:
            return None

        return self.position_manager.entry_price * (1 + TAKE_PROFIT_PCT)
