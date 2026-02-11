"""
Order Execution Manager - Handles order creation and execution.

Manages buy and sell orders with FOK (Fill-or-Kill) market orders.
"""

import asyncio
from typing import TYPE_CHECKING, Any

from src.clob_types import (
    MAX_BUY_PRICE,
    STOP_LOSS_ABSOLUTE,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
)
from src.market_parser import get_winning_token_id

if TYPE_CHECKING:
    from py_clob_client.clob_types import (
        CreateOrderOptions,
        MarketOrderArgs,
        OrderType,
    )
else:
    try:
        from py_clob_client.clob_types import (
            CreateOrderOptions,
            MarketOrderArgs,
            OrderType,
        )
    except ImportError:
        ClobClient = None
        CreateOrderOptions = None
        MarketOrderArgs = None
        OrderType = None


class OrderExecutionManager:
    """
    Manages order creation and execution for trading.
    """

    def __init__(
        self,
        client: Any | None,
        market_name: str,
        condition_id: str,
        token_id_yes: str,
        token_id_no: str,
        dry_run: bool = True,
        trade_size: float = 1.0,
        logger: Any | None = None,
        position_manager: Any | None = None,
        alert_dispatcher: Any | None = None,
        risk_manager: Any | None = None,
    ):
        """
        Initialize the order execution manager.

        Args:
            client: CLOB client instance
            market_name: Market name for logging
            condition_id: Market condition ID
            token_id_yes: YES token ID
            token_id_no: NO token ID
            dry_run: If True, only log actions without executing
            trade_size: Trade size in dollars
            logger: Logger instance
            position_manager: Position manager instance
            alert_dispatcher: Alert dispatcher instance
            risk_manager: Risk manager instance
        """
        self.client = client
        self.market_name = market_name
        self.condition_id = condition_id
        self.token_id_yes = token_id_yes
        self.token_id_no = token_id_no
        self.dry_run = dry_run
        self.trade_size = trade_size
        self.logger = logger
        self.position_manager = position_manager
        self.alert_dispatcher = alert_dispatcher
        self.risk_manager = risk_manager

        # Order state
        self.order_executed = False
        self.order_in_progress = False
        self.order_attempts = 0
        self.max_order_attempts = 3
        self.last_order_attempt_time = 0.0
        self._order_nonce: int | None = None
        self._order_side: str | None = None
        self._order_token_id: str | None = None
        self._order_amount: float | None = None
        self._order_price: float | None = None

    # Getter methods for order state
    def is_executed(self) -> bool:
        """Check if an order has been executed."""
        return self.order_executed

    def mark_executed(self) -> None:
        self.order_executed = True

    def is_in_progress(self) -> bool:
        """Check if an order is currently in progress."""
        return self.order_in_progress

    def get_attempts(self) -> int:
        """Get the number of order attempts made."""
        return self.order_attempts

    def get_max_attempts(self) -> int:
        """Get the maximum number of order attempts allowed."""
        return self.max_order_attempts

    def get_last_attempt_time(self) -> float:
        """Get the timestamp of the last order attempt."""
        return self.last_order_attempt_time

    def _log(self, message: str) -> None:
        """Log message to console or logger."""
        if self.logger:
            self.logger.info(message)
        else:
            print(message)

    async def execute_order_for(
        self,
        side: str,
        winning_ask: float | None,
    ) -> bool:
        """
        Execute Fill-or-Kill (FOK) market order for a specific side.

        Args:
            side: "YES" or "NO"
            winning_ask: Current ask price for the side

        Returns:
            True if order was executed, False otherwise
        """
        winning_token_id = get_winning_token_id(
            side, self.token_id_yes, self.token_id_no
        )

        if not winning_token_id:
            self._log(f"❌ [{self.market_name}] Error: No winning token ID available")
            return False

        amount = (
            self.risk_manager.planned_trade_amount
            if self.risk_manager and self.risk_manager.planned_trade_amount is not None
            else max(round(self.trade_size, 2), 1.00)
        )
        price = round(MAX_BUY_PRICE, 2)

        if self._order_nonce is None:
            self._order_nonce = 0
            self._order_side = side
            self._order_token_id = winning_token_id
            self._order_amount = amount
            self._order_price = price
        else:
            side = self._order_side or side
            winning_token_id = self._order_token_id or winning_token_id
            amount = self._order_amount if self._order_amount is not None else amount
            price = self._order_price if self._order_price is not None else price

        if self.dry_run:
            self._log(f"🔷 [{self.market_name}] DRY RUN - WOULD BUY:")
            self._log(f"  Side: {side}, Amount: ${amount}, Max Price: ${price}")
            ask_str = f"{winning_ask:.4f}" if winning_ask is not None else "-"
            self._log(f"  Best Ask: ${ask_str}, Type: FOK MARKET")

            if winning_ask is not None and self.position_manager:
                trailing_stop_price = max(
                    winning_ask * (1 - STOP_LOSS_PCT),
                    STOP_LOSS_ABSOLUTE,
                )
                self.position_manager.open_position(
                    entry_price=winning_ask,
                    side=side,
                    trailing_stop_price=trailing_stop_price,
                )
                self._log(
                    (
                        f"  📍 Position opened @ ${winning_ask:.4f} | "
                        f"Stop-loss: ${trailing_stop_price:.4f} | "
                        f"Take-profit: ${winning_ask * (1 + TAKE_PROFIT_PCT):.4f}"
                    )
                )

                if self.alert_dispatcher and self.alert_dispatcher.is_enabled():
                    await self.alert_dispatcher.send_trade_alert(
                        self.market_name, side, winning_ask, amount
                    )

            if self.risk_manager:
                self.risk_manager.track_daily_pnl(amount)

            self.order_executed = True
            return True

        if (
            not self.client
            or not MarketOrderArgs
            or not CreateOrderOptions
            or not OrderType
        ):
            self._log(f"❌ [{self.market_name}] CLOB client or types not initialized")
            return False

        try:
            order_args = MarketOrderArgs(
                token_id=winning_token_id,
                amount=amount,
                price=price,
                side="BUY",
                nonce=self._order_nonce,
            )

            created_order = await asyncio.to_thread(
                self.client.create_order,
                order_args,
                CreateOrderOptions(tick_size="0.01", neg_risk=False),
            )

            response = await asyncio.to_thread(
                self.client.post_order,
                created_order,
                OrderType.FOK,
            )

            self._log(f"✓ [{self.market_name}] Order posted: {response}")

            if winning_ask is not None and self.position_manager:
                trailing_stop_price = max(
                    winning_ask * (1 - STOP_LOSS_PCT),
                    STOP_LOSS_ABSOLUTE,
                )
                self.position_manager.open_position(
                    entry_price=winning_ask,
                    side=side,
                    trailing_stop_price=trailing_stop_price,
                )
                self._log(
                    (
                        f"  📍 Position opened @ ${winning_ask:.4f} | "
                        f"Stop-loss: ${trailing_stop_price:.4f} | "
                        f"Take-profit: ${winning_ask * (1 + TAKE_PROFIT_PCT):.4f}"
                    )
                )

                if self.alert_dispatcher and self.alert_dispatcher.is_enabled():
                    await self.alert_dispatcher.send_trade_alert(
                        self.market_name, side, winning_ask, amount
                    )

            if self.risk_manager:
                self.risk_manager.track_daily_pnl(amount)

            self.order_executed = True
            return True

        except Exception as e:
            self._log(f"❌ [{self.market_name}] Order failed: {e}")
            self.order_attempts += 1
            self.last_order_attempt_time = asyncio.get_event_loop().time()
            return False

    async def execute_sell(
        self,
        reason: str,
        current_price: float | None,
    ) -> bool:
        """
        Execute a market sell order to exit the position.

        Args:
            reason: Reason for the sell (e.g., "STOP-LOSS", "TAKE-PROFIT")
            current_price: Current price for PnL calculation

        Returns:
            True if sell was executed, False otherwise
        """
        if not self.position_manager:
            self._log(f"❌ [{self.market_name}] No position manager")
            return False

        if (
            not self.position_manager.is_open
            or self.position_manager.position_side is None
        ):
            self._log(f"❌ [{self.market_name}] No position to sell")
            return False

        sell_token_id = get_winning_token_id(
            self.position_manager.position_side,
            self.token_id_yes,
            self.token_id_no,
        )

        if self.dry_run:
            if current_price is not None:
                pnl_pct = (
                    (current_price - self.position_manager.entry_price)
                    / self.position_manager.entry_price
                ) * 100
                pnl_sign = "+" if pnl_pct >= 0 else ""
                self._log(
                    (
                        f"🔷 [{self.market_name}] DRY RUN - WOULD SELL ({reason}): "
                        f"{self.position_manager.position_side} @ ${current_price:.4f} | "
                        f"PnL: {pnl_sign}{pnl_pct:.2f}%"
                    )
                )

                if self.alert_dispatcher and self.alert_dispatcher.is_enabled():
                    if reason == "STOP-LOSS":
                        await self.alert_dispatcher.send_stop_loss_alert(
                            self.market_name,
                            pnl_pct,
                            self.position_manager.entry_price,
                            current_price,
                        )
                    elif reason == "TAKE-PROFIT":
                        await self.alert_dispatcher.send_take_profit_alert(
                            self.market_name,
                            pnl_pct,
                            self.position_manager.entry_price,
                            current_price,
                        )

            trade_amount = max(round(self.trade_size, 2), 1.00)
            if current_price is not None:
                pnl_amount = trade_amount * (
                    (current_price - self.position_manager.entry_price)
                    / self.position_manager.entry_price
                )
                if self.risk_manager:
                    self.risk_manager.track_daily_pnl(trade_amount, pnl_amount)

            self.position_manager.close_position()
            return True

        if (
            not self.client
            or not MarketOrderArgs
            or not CreateOrderOptions
            or not OrderType
        ):
            self._log(f"❌ [{self.market_name}] CLOB client or types not initialized")
            return False

        if not sell_token_id:
            self._log(f"❌ [{self.market_name}] Error: No token ID to sell")
            return False

        try:
            self._log(
                f"🔴 [{self.market_name}] SELL ORDER ({reason}): {self.position_manager.position_side}"
            )

            amount = max(round(self.trade_size, 2), 1.00)

            order_args = MarketOrderArgs(
                token_id=sell_token_id,
                amount=amount,
                price=0.01,
                side="SELL",
                nonce=0,
            )

            created_order = await asyncio.to_thread(
                self.client.create_market_order,
                order_args,
                CreateOrderOptions(tick_size="0.01", neg_risk=False),
            )

            response = await asyncio.to_thread(
                self.client.post_order,
                created_order,
                OrderType.FOK,
            )

            self._log(f"✓ [{self.market_name}] Sell order posted: {response}")

            if current_price is not None:
                pnl_pct = (
                    (current_price - self.position_manager.entry_price)
                    / self.position_manager.entry_price
                ) * 100
                pnl_sign = "+" if pnl_pct >= 0 else ""
                self._log(
                    (
                        f"💰 [{self.market_name}] Sold @ ${current_price:.4f} | "
                        f"PnL: {pnl_sign}{pnl_pct:.2f}%"
                    )
                )

                if self.alert_dispatcher and self.alert_dispatcher.is_enabled():
                    if reason == "STOP-LOSS":
                        await self.alert_dispatcher.send_stop_loss_alert(
                            self.market_name,
                            pnl_pct,
                            self.position_manager.entry_price,
                            current_price,
                        )
                    elif reason == "TAKE-PROFIT":
                        await self.alert_dispatcher.send_take_profit_alert(
                            self.market_name,
                            pnl_pct,
                            self.position_manager.entry_price,
                            current_price,
                        )

            self.position_manager.close_position()
            self._log(f"✓ [{self.market_name}] Position closed ({reason})")

            if current_price is not None:
                pnl_amount = amount * (
                    (current_price - self.position_manager.entry_price)
                    / self.position_manager.entry_price
                )
                if self.risk_manager:
                    self.risk_manager.track_daily_pnl(amount, pnl_amount)

            return True

        except Exception as e:
            self._log(f"❌ [{self.market_name}] Sell order failed: {e}")
            return False

    async def verify_order(self, order_id: str) -> None:
        """
        Verify order status by checking order book.

        Args:
            order_id: Order ID to verify
        """
        self._log(f"🔍 [{self.market_name}] Verifying order {order_id}...")
        # Verification logic would go here
        # For now, this is a placeholder
