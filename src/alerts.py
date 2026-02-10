"""
Alert senders for Telegram and Slack notifications.

Provides real-time notifications for key trading events via Telegram and Slack.
"""

import asyncio
import logging
import time
from typing import Any

import aiohttp

from src.clob_types import ALERT_RATE_LIMIT_PER_MINUTE

logger = logging.getLogger(__name__)


class RateLimiter:
    """Simple rate limiter to prevent alert spam."""

    def __init__(self, max_per_minute: int = ALERT_RATE_LIMIT_PER_MINUTE):
        self.max_per_minute = max_per_minute
        self.timestamps: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        """
        Try to acquire permission to send an alert.

        Returns:
            True if under rate limit, False otherwise
        """
        async with self._lock:
            now = time.time()
            # Remove timestamps older than 1 minute
            self.timestamps = [t for t in self.timestamps if now - t < 60.0]

            if len(self.timestamps) < self.max_per_minute:
                self.timestamps.append(now)
                return True
            return False


class TelegramAlertSender:
    """Send trading alerts to Telegram via bot API."""

    def __init__(self, bot_token: str, chat_id: str):
        """
        Initialize Telegram alert sender.

        Args:
            bot_token: Telegram bot token from @BotFather
            chat_id: Chat ID to send messages to
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self.rate_limiter = RateLimiter()

    async def send_alert(self, message: str) -> bool:
        """
        Send a raw alert message to Telegram.

        Args:
            message: Message text to send

        Returns:
            True if successful, False otherwise
        """
        if not await self.rate_limiter.acquire():
            logger.warning("Telegram rate limit exceeded, alert dropped")
            return False

        try:
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML",  # Allow basic HTML formatting
                "disable_web_page_preview": True,
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.api_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        return True
                    else:
                        error_text = await resp.text()
                        logger.warning(
                            f"Telegram API error {resp.status}: {error_text[:200]}"
                        )
                        return False

        except asyncio.TimeoutError:
            logger.warning("Telegram API timeout")
            return False
        except aiohttp.ClientError as e:
            logger.warning(f"Telegram client error: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected Telegram error: {e}")
            return False

    async def send_trade_alert(self, trade_data: dict[str, Any]) -> bool:
        """
        Send a trade execution alert.

        Args:
            trade_data: Dict with keys: market, side, entry_price, timestamp

        Returns:
            True if successful, False otherwise
        """
        market = trade_data.get("market", "UNKNOWN")
        side = trade_data.get("side", "YES")
        entry_price = trade_data.get("entry_price", 0.0)
        amount = trade_data.get("amount", 0.0)
        pnl = trade_data.get("pnl", None)

        pnl_str = ""
        if pnl is not None:
            pnl_sign = "+" if pnl >= 0 else ""
            pnl_str = f" | PnL: {pnl_sign}{pnl:.2f}%"

        message = (
            f"🚀 <b>Trade executed:</b> {market} ({side}) @ ${entry_price:.4f} | "
            f"Size: ${amount:.2f}{pnl_str}"
        )
        return await self.send_alert(message)

    async def send_stop_loss_alert(self, market: str, pnl: float, entry_price: float | None = None, exit_price: float | None = None) -> bool:
        """
        Send a stop-loss trigger alert.

        Args:
            market: Market name
            pnl: Profit/loss percentage
            entry_price: Entry price (optional)
            exit_price: Exit price (optional)

        Returns:
            True if successful, False otherwise
        """
        pnl_sign = "" if pnl >= 0 else ""
        parts = [f"🚨 <b>STOP-LOSS:</b> {market} | PnL: {pnl_sign}{pnl:.2f}%"]
        if entry_price is not None:
            parts.append(f"Entry: ${entry_price:.4f}")
        if exit_price is not None:
            parts.append(f"Exit: ${exit_price:.4f}")
        message = " | ".join(parts)
        return await self.send_alert(message)

    async def send_take_profit_alert(self, market: str, pnl: float, entry_price: float | None = None, exit_price: float | None = None) -> bool:
        """
        Send a take-profit trigger alert.

        Args:
            market: Market name
            pnl: Profit/loss percentage
            entry_price: Entry price (optional)
            exit_price: Exit price (optional)

        Returns:
            True if successful, False otherwise
        """
        parts = [f"💰 <b>TAKE-PROFIT:</b> {market} | PnL: +{pnl:.2f}%"]
        if entry_price is not None:
            parts.append(f"Entry: ${entry_price:.4f}")
        if exit_price is not None:
            parts.append(f"Exit: ${exit_price:.4f}")
        message = " | ".join(parts)
        return await self.send_alert(message)

    async def send_oracle_guard_block(self, market: str, reason: str, detail: str = "") -> bool:
        """
        Send an Oracle Guard block alert.

        Args:
            market: Market name
            reason: Reason for block
            detail: Additional detail (optional)

        Returns:
            True if successful, False otherwise
        """
        detail_str = f" ({detail})" if detail else ""
        message = f"⛔ <b>Oracle Guard blocked:</b> {market} | Reason: {reason}{detail_str}"
        return await self.send_alert(message)

    async def send_daily_report_summary(self, report_summary: str) -> bool:
        """
        Send a daily report summary.

        Args:
            report_summary: Summary text (will be truncated to 500 chars)

        Returns:
            True if successful, False otherwise
        """
        # Telegram message limit is 4096 chars, but we keep it short
        summary = report_summary[:500]
        message = f"📊 <b>Daily Report:</b>\n\n{summary}"
        return await self.send_alert(message)


class SlackAlertSender:
    """Send trading alerts to Slack via webhook."""

    def __init__(self, webhook_url: str):
        """
        Initialize Slack alert sender.

        Args:
            webhook_url: Slack incoming webhook URL
        """
        self.webhook_url = webhook_url
        self.rate_limiter = RateLimiter()

    async def send_alert(self, message: str) -> bool:
        """
        Send a raw alert message to Slack.

        Args:
            message: Message text to send

        Returns:
            True if successful, False otherwise
        """
        if not await self.rate_limiter.acquire():
            logger.warning("Slack rate limit exceeded, alert dropped")
            return False

        try:
            payload = {"text": message}

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.webhook_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        return True
                    else:
                        error_text = await resp.text()
                        logger.warning(
                            f"Slack webhook error {resp.status}: {error_text[:200]}"
                        )
                        return False

        except asyncio.TimeoutError:
            logger.warning("Slack webhook timeout")
            return False
        except aiohttp.ClientError as e:
            logger.warning(f"Slack client error: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected Slack error: {e}")
            return False

    async def send_trade_alert(self, trade_data: dict[str, Any]) -> bool:
        """
        Send a trade execution alert.

        Args:
            trade_data: Dict with keys: market, side, entry_price, timestamp

        Returns:
            True if successful, False otherwise
        """
        market = trade_data.get("market", "UNKNOWN")
        side = trade_data.get("side", "YES")
        entry_price = trade_data.get("entry_price", 0.0)
        amount = trade_data.get("amount", 0.0)
        pnl = trade_data.get("pnl", None)

        pnl_str = ""
        if pnl is not None:
            pnl_sign = "+" if pnl >= 0 else ""
            pnl_str = f" | PnL: {pnl_sign}{pnl:.2f}%"

        message = (
            f"🚀 Trade executed: {market} ({side}) @ ${entry_price:.4f} | "
            f"Size: ${amount:.2f}{pnl_str}"
        )
        return await self.send_alert(message)

    async def send_stop_loss_alert(self, market: str, pnl: float, entry_price: float | None = None, exit_price: float | None = None) -> bool:
        """
        Send a stop-loss trigger alert.

        Args:
            market: Market name
            pnl: Profit/loss percentage
            entry_price: Entry price (optional)
            exit_price: Exit price (optional)

        Returns:
            True if successful, False otherwise
        """
        pnl_sign = "" if pnl >= 0 else ""
        parts = [f"🚨 STOP-LOSS: {market} | PnL: {pnl_sign}{pnl:.2f}%"]
        if entry_price is not None:
            parts.append(f"Entry: ${entry_price:.4f}")
        if exit_price is not None:
            parts.append(f"Exit: ${exit_price:.4f}")
        message = " | ".join(parts)
        return await self.send_alert(message)

    async def send_take_profit_alert(self, market: str, pnl: float, entry_price: float | None = None, exit_price: float | None = None) -> bool:
        """
        Send a take-profit trigger alert.

        Args:
            market: Market name
            pnl: Profit/loss percentage
            entry_price: Entry price (optional)
            exit_price: Exit price (optional)

        Returns:
            True if successful, False otherwise
        """
        parts = [f"💰 TAKE-PROFIT: {market} | PnL: +{pnl:.2f}%"]
        if entry_price is not None:
            parts.append(f"Entry: ${entry_price:.4f}")
        if exit_price is not None:
            parts.append(f"Exit: ${exit_price:.4f}")
        message = " | ".join(parts)
        return await self.send_alert(message)

    async def send_oracle_guard_block(self, market: str, reason: str, detail: str = "") -> bool:
        """
        Send an Oracle Guard block alert.

        Args:
            market: Market name
            reason: Reason for block
            detail: Additional detail (optional)

        Returns:
            True if successful, False otherwise
        """
        detail_str = f" ({detail})" if detail else ""
        message = f"⛔ Oracle Guard blocked: {market} | Reason: {reason}{detail_str}"
        return await self.send_alert(message)

    async def send_daily_report_summary(self, report_summary: str) -> bool:
        """
        Send a daily report summary.

        Args:
            report_summary: Summary text (will be truncated to 500 chars)

        Returns:
            True if successful, False otherwise
        """
        summary = report_summary[:500]
        message = f"📊 Daily Report:\n\n{summary}"
        return await self.send_alert(message)


class AlertManager:
    """
    Wrapper to broadcast alerts to multiple channels (Telegram, Slack).

    Error handling: if one channel fails, the others still get the alert.
    Never blocks trading on API failures.
    """

    def __init__(
        self,
        telegram: TelegramAlertSender | None = None,
        slack: SlackAlertSender | None = None,
    ):
        """
        Initialize alert manager.

        Args:
            telegram: TelegramAlertSender instance (optional)
            slack: SlackAlertSender instance (optional)
        """
        self.telegram = telegram
        self.slack = slack

        # Track which channels are enabled
        self._enabled = telegram is not None or slack is not None

    def is_enabled(self) -> bool:
        """Check if any alert channel is configured."""
        return self._enabled

    async def broadcast_alert(self, message: str) -> None:
        """
        Broadcast a raw alert message to all configured channels.

        Args:
            message: Message text to send
        """
        if not self._enabled:
            return

        # Send to both channels concurrently, ignore failures
        tasks = []
        if self.telegram:
            tasks.append(self.telegram.send_alert(message))
        if self.slack:
            tasks.append(self.slack.send_alert(message))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            # Log any exceptions for debugging
            for result in results:
                if isinstance(result, Exception):
                    logger.warning(f"Alert broadcast error: {result}")

    async def send_trade_alert(self, trade_data: dict[str, Any]) -> None:
        """
        Broadcast trade execution alert.

        Args:
            trade_data: Dict with keys: market, side, entry_price, amount, pnl (optional)
        """
        if not self._enabled:
            return

        tasks = []
        if self.telegram:
            tasks.append(self.telegram.send_trade_alert(trade_data))
        if self.slack:
            tasks.append(self.slack.send_trade_alert(trade_data))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def send_stop_loss_alert(self, market: str, pnl: float, entry_price: float | None = None, exit_price: float | None = None) -> None:
        """
        Broadcast stop-loss trigger alert.

        Args:
            market: Market name
            pnl: Profit/loss percentage
            entry_price: Entry price (optional)
            exit_price: Exit price (optional)
        """
        if not self._enabled:
            return

        tasks = []
        if self.telegram:
            tasks.append(self.telegram.send_stop_loss_alert(market, pnl, entry_price, exit_price))
        if self.slack:
            tasks.append(self.slack.send_stop_loss_alert(market, pnl, entry_price, exit_price))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def send_take_profit_alert(self, market: str, pnl: float, entry_price: float | None = None, exit_price: float | None = None) -> None:
        """
        Broadcast take-profit trigger alert.

        Args:
            market: Market name
            pnl: Profit/loss percentage
            entry_price: Entry price (optional)
            exit_price: Exit price (optional)
        """
        if not self._enabled:
            return

        tasks = []
        if self.telegram:
            tasks.append(self.telegram.send_take_profit_alert(market, pnl, entry_price, exit_price))
        if self.slack:
            tasks.append(self.slack.send_take_profit_alert(market, pnl, entry_price, exit_price))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def send_oracle_guard_block(self, market: str, reason: str, detail: str = "") -> None:
        """
        Broadcast Oracle Guard block alert.

        Args:
            market: Market name
            reason: Reason for block
            detail: Additional detail (optional)
        """
        if not self._enabled:
            return

        tasks = []
        if self.telegram:
            tasks.append(self.telegram.send_oracle_guard_block(market, reason, detail))
        if self.slack:
            tasks.append(self.slack.send_oracle_guard_block(market, reason, detail))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def send_daily_report_summary(self, report_summary: str) -> None:
        """
        Broadcast daily report summary.

        Args:
            report_summary: Summary text
        """
        if not self._enabled:
            return

        tasks = []
        if self.telegram:
            tasks.append(self.telegram.send_daily_report_summary(report_summary))
        if self.slack:
            tasks.append(self.slack.send_daily_report_summary(report_summary))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
