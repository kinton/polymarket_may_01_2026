"""
Alert dispatcher for sending trading alerts.

Provides a simple interface for sending alerts through AlertManager.
"""

from src.alerts import AlertManager


class AlertDispatcher:
    """
    Dispatcher for trading alerts.

    Wraps AlertManager to provide a clean interface for sending various
    types of trading alerts (trade execution, stop-loss, take-profit, etc.).
    """

    def __init__(self, alert_manager: AlertManager | None):
        """
        Initialize alert dispatcher.

        Args:
            alert_manager: AlertManager instance (None if alerts disabled)
        """
        self.alert_manager = alert_manager

    def is_enabled(self) -> bool:
        """Check if alerts are enabled."""
        return self.alert_manager is not None and self.alert_manager.is_enabled()

    async def send_trade_alert(
        self,
        market: str,
        side: str,
        entry_price: float,
        amount: float,
        pnl: float | None = None,
    ) -> None:
        """
        Send a trade execution alert.

        Args:
            market: Market name
            side: Trade side (YES/NO)
            entry_price: Entry price
            amount: Trade amount in USDC
            pnl: Optional profit/loss percentage (for sells)
        """
        alert_manager = self.alert_manager
        if alert_manager is None or not alert_manager.is_enabled():
            return

        trade_data = {
            "market": market,
            "side": side,
            "entry_price": entry_price,
            "amount": amount,
        }
        if pnl is not None:
            trade_data["pnl"] = pnl

        await alert_manager.send_trade_alert(trade_data)

    async def send_stop_loss_alert(
        self,
        market: str,
        pnl: float,
        entry_price: float | None = None,
        exit_price: float | None = None,
    ) -> None:
        """
        Send a stop-loss trigger alert.

        Args:
            market: Market name
            pnl: Profit/loss percentage
            entry_price: Entry price (optional)
            exit_price: Exit price (optional)
        """
        alert_manager = self.alert_manager
        if alert_manager is None or not alert_manager.is_enabled():
            return

        await alert_manager.send_stop_loss_alert(market, pnl, entry_price, exit_price)

    async def send_take_profit_alert(
        self,
        market: str,
        pnl: float,
        entry_price: float | None = None,
        exit_price: float | None = None,
    ) -> None:
        """
        Send a take-profit trigger alert.

        Args:
            market: Market name
            pnl: Profit/loss percentage
            entry_price: Entry price (optional)
            exit_price: Exit price (optional)
        """
        alert_manager = self.alert_manager
        if alert_manager is None or not alert_manager.is_enabled():
            return

        await alert_manager.send_take_profit_alert(market, pnl, entry_price, exit_price)

    async def send_oracle_guard_block(
        self,
        market: str,
        reason: str,
        detail: str = "",
    ) -> None:
        """
        Send an Oracle Guard block alert.

        Args:
            market: Market name
            reason: Reason for block
            detail: Additional detail (optional)
        """
        alert_manager = self.alert_manager
        if alert_manager is None or not alert_manager.is_enabled():
            return

        await alert_manager.send_oracle_guard_block(market, reason, detail)

    async def send_daily_report_summary(self, report_summary: str) -> None:
        """
        Send a daily report summary.

        Args:
            report_summary: Summary text
        """
        alert_manager = self.alert_manager
        if alert_manager is None or not alert_manager.is_enabled():
            return

        await alert_manager.send_daily_report_summary(report_summary)
