"""
Unit tests for alert senders (Telegram, Slack, AlertManager).
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.alerts import (
    AlertManager,
    RateLimiter,
    SlackAlertSender,
    TelegramAlertSender,
)


class TestRateLimiter:
    """Test rate limiting logic."""

    def test_rate_limiter_allows_under_limit(self):
        """Test that rate limiter allows when under limit."""
        limiter = RateLimiter(max_per_minute=10)

        async def test():
            for _ in range(10):
                assert await limiter.acquire() is True
            # 11th should fail
            assert await limiter.acquire() is False

        asyncio.run(test())

    def test_rate_limiter_resets_after_minute(self):
        """Test that rate limiter resets after timestamps expire."""
        limiter = RateLimiter(max_per_minute=2)

        async def test():
            assert await limiter.acquire() is True
            assert await limiter.acquire() is True
            assert await limiter.acquire() is False

            # Manually expire timestamps
            limiter.timestamps = []

            # Should allow again
            assert await limiter.acquire() is True

        asyncio.run(test())

    def test_rate_limiter_default_from_const(self):
        """Test that rate limiter uses default constant."""
        from src.clob_types import ALERT_RATE_LIMIT_PER_MINUTE

        limiter = RateLimiter()

        async def test():
            # Use default constant value
            for _ in range(ALERT_RATE_LIMIT_PER_MINUTE):
                assert await limiter.acquire() is True
            assert await limiter.acquire() is False

        asyncio.run(test())


class TestTelegramAlertSender:
    """Test Telegram alert sender."""

    def test_init(self):
        """Test TelegramAlertSender initialization."""
        sender = TelegramAlertSender("token123", "chat456")
        assert sender.bot_token == "token123"
        assert sender.chat_id == "chat456"
        assert "bottoken123/sendMessage" in sender.api_url

    @pytest.mark.asyncio
    async def test_send_alert_success(self):
        """Test successful alert sending."""
        sender = TelegramAlertSender("token", "chat")

        with patch("aiohttp.ClientSession.post") as mock_post:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.text = AsyncMock(return_value="OK")
            mock_post.return_value.__aenter__.return_value = mock_response

            result = await sender.send_alert("Test message")
            assert result is True
            mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_alert_failure(self):
        """Test alert sending on API error."""
        sender = TelegramAlertSender("token", "chat")

        with patch("aiohttp.ClientSession.post") as mock_post:
            mock_response = AsyncMock()
            mock_response.status = 401
            mock_response.text = AsyncMock(return_value="Unauthorized")
            mock_post.return_value.__aenter__.return_value = mock_response

            result = await sender.send_alert("Test message")
            assert result is False

    @pytest.mark.asyncio
    async def test_send_alert_timeout(self):
        """Test alert sending on timeout."""
        sender = TelegramAlertSender("token", "chat")

        with patch("aiohttp.ClientSession.post") as mock_post:
            mock_post.side_effect = asyncio.TimeoutError()

            result = await sender.send_alert("Test message")
            assert result is False

    @pytest.mark.asyncio
    async def test_send_trade_alert(self):
        """Test trade alert formatting."""
        sender = TelegramAlertSender("token", "chat")

        with patch.object(sender, "send_alert", return_value=True) as mock_send:
            result = await sender.send_trade_alert({
                "market": "BTC",
                "side": "YES",
                "entry_price": 0.95,
                "amount": 10.0,
            })

            assert result is True
            call_args = mock_send.call_args[0][0]
            assert "BTC" in call_args
            assert "YES" in call_args
            assert "0.9500" in call_args

    @pytest.mark.asyncio
    async def test_send_trade_alert_with_pnl(self):
        """Test trade alert with PnL."""
        sender = TelegramAlertSender("token", "chat")

        with patch.object(sender, "send_alert", return_value=True) as mock_send:
            result = await sender.send_trade_alert({
                "market": "ETH",
                "side": "NO",
                "entry_price": 0.90,
                "amount": 5.0,
                "pnl": 5.5,
            })

            assert result is True
            call_args = mock_send.call_args[0][0]
            assert "+5.50%" in call_args

    @pytest.mark.asyncio
    async def test_send_stop_loss_alert(self):
        """Test stop-loss alert formatting."""
        sender = TelegramAlertSender("token", "chat")

        with patch.object(sender, "send_alert", return_value=True) as mock_send:
            result = await sender.send_stop_loss_alert("BTC", -24.5, 0.95, 0.72)

            assert result is True
            call_args = mock_send.call_args[0][0]
            assert "STOP-LOSS" in call_args
            assert "BTC" in call_args
            assert "-24.50%" in call_args

    @pytest.mark.asyncio
    async def test_send_take_profit_alert(self):
        """Test take-profit alert formatting."""
        sender = TelegramAlertSender("token", "chat")

        with patch.object(sender, "send_alert", return_value=True) as mock_send:
            result = await sender.send_take_profit_alert("ETH", 10.0, 0.90, 0.99)

            assert result is True
            call_args = mock_send.call_args[0][0]
            assert "TAKE-PROFIT" in call_args
            assert "ETH" in call_args
            assert "+10.00%" in call_args

    @pytest.mark.asyncio
    async def test_send_oracle_guard_block(self):
        """Test Oracle Guard block alert formatting."""
        sender = TelegramAlertSender("token", "chat")

        with patch.object(sender, "send_alert", return_value=True) as mock_send:
            result = await sender.send_oracle_guard_block("SOL", "oracle_vol_high", "0.003 > 0.002")

            assert result is True
            call_args = mock_send.call_args[0][0]
            assert "Oracle Guard blocked" in call_args
            assert "SOL" in call_args
            assert "oracle_vol_high" in call_args

    @pytest.mark.asyncio
    async def test_send_daily_report_summary(self):
        """Test daily report summary truncation."""
        sender = TelegramAlertSender("token", "chat")

        # Create a long report (1000 chars)
        long_report = "X" * 1000

        with patch.object(sender, "send_alert", return_value=True) as mock_send:
            result = await sender.send_daily_report_summary(long_report)

            assert result is True
            call_args = mock_send.call_args[0][0]
            # Should be truncated to 500 chars (plus "Daily Report:" prefix)
            assert len(call_args) < 600


class TestSlackAlertSender:
    """Test Slack alert sender."""

    def test_init(self):
        """Test SlackAlertSender initialization."""
        sender = SlackAlertSender("https://hooks.slack.com/services/xxx")
        assert sender.webhook_url == "https://hooks.slack.com/services/xxx"

    @pytest.mark.asyncio
    async def test_send_alert_success(self):
        """Test successful alert sending."""
        sender = SlackAlertSender("https://hooks.slack.com/services/xxx")

        with patch("aiohttp.ClientSession.post") as mock_post:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.text = AsyncMock(return_value="OK")
            mock_post.return_value.__aenter__.return_value = mock_response

            result = await sender.send_alert("Test message")
            assert result is True
            mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_alert_failure(self):
        """Test alert sending on webhook error."""
        sender = SlackAlertSender("https://hooks.slack.com/services/xxx")

        with patch("aiohttp.ClientSession.post") as mock_post:
            mock_response = AsyncMock()
            mock_response.status = 404
            mock_response.text = AsyncMock(return_value="Not Found")
            mock_post.return_value.__aenter__.return_value = mock_response

            result = await sender.send_alert("Test message")
            assert result is False

    @pytest.mark.asyncio
    async def test_send_trade_alert(self):
        """Test trade alert formatting."""
        sender = SlackAlertSender("https://hooks.slack.com/services/xxx")

        with patch.object(sender, "send_alert", return_value=True) as mock_send:
            result = await sender.send_trade_alert({
                "market": "BTC",
                "side": "YES",
                "entry_price": 0.95,
                "amount": 10.0,
            })

            assert result is True
            call_args = mock_send.call_args[0][0]
            assert "BTC" in call_args
            assert "YES" in call_args

    @pytest.mark.asyncio
    async def test_send_stop_loss_alert(self):
        """Test stop-loss alert formatting."""
        sender = SlackAlertSender("https://hooks.slack.com/services/xxx")

        with patch.object(sender, "send_alert", return_value=True) as mock_send:
            result = await sender.send_stop_loss_alert("BTC", -24.5)

            assert result is True
            call_args = mock_send.call_args[0][0]
            assert "STOP-LOSS" in call_args
            assert "BTC" in call_args
            assert "-24.50%" in call_args


class TestAlertManager:
    """Test AlertManager wrapper."""

    def test_init_with_both_channels(self):
        """Test AlertManager initialization with both channels."""
        telegram = TelegramAlertSender("token", "chat")
        slack = SlackAlertSender("https://hooks.slack.com/services/xxx")

        manager = AlertManager(telegram=telegram, slack=slack)
        assert manager.telegram is telegram
        assert manager.slack is slack
        assert manager.is_enabled()

    def test_init_with_only_telegram(self):
        """Test AlertManager initialization with only Telegram."""
        telegram = TelegramAlertSender("token", "chat")

        manager = AlertManager(telegram=telegram)
        assert manager.telegram is telegram
        assert manager.slack is None
        assert manager.is_enabled()

    def test_init_with_only_slack(self):
        """Test AlertManager initialization with only Slack."""
        slack = SlackAlertSender("https://hooks.slack.com/services/xxx")

        manager = AlertManager(slack=slack)
        assert manager.telegram is None
        assert manager.slack is slack
        assert manager.is_enabled()

    def test_init_with_none(self):
        """Test AlertManager initialization with no channels."""
        manager = AlertManager()
        assert manager.telegram is None
        assert manager.slack is None
        assert not manager.is_enabled()

    @pytest.mark.asyncio
    async def test_broadcast_alert(self):
        """Test broadcasting alert to both channels."""
        telegram = TelegramAlertSender("token", "chat")
        slack = SlackAlertSender("https://hooks.slack.com/services/xxx")

        with patch.object(telegram, "send_alert", return_value=True) as mock_telegram:
            with patch.object(slack, "send_alert", return_value=True) as mock_slack:
                manager = AlertManager(telegram=telegram, slack=slack)

                await manager.broadcast_alert("Test message")

                mock_telegram.assert_called_once_with("Test message")
                mock_slack.assert_called_once_with("Test message")

    @pytest.mark.asyncio
    async def test_broadcast_alert_with_one_failure(self):
        """Test broadcasting when one channel fails."""
        telegram = TelegramAlertSender("token", "chat")
        slack = SlackAlertSender("https://hooks.slack.com/services/xxx")

        with patch.object(telegram, "send_alert", return_value=False) as mock_telegram:
            with patch.object(slack, "send_alert", return_value=True) as mock_slack:
                manager = AlertManager(telegram=telegram, slack=slack)

                await manager.broadcast_alert("Test message")

                # Both should be called, even if one fails
                mock_telegram.assert_called_once()
                mock_slack.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_trade_alert(self):
        """Test trade alert broadcast."""
        telegram = TelegramAlertSender("token", "chat")
        slack = SlackAlertSender("https://hooks.slack.com/services/xxx")

        with patch.object(telegram, "send_trade_alert", return_value=True):
            with patch.object(slack, "send_trade_alert", return_value=True):
                manager = AlertManager(telegram=telegram, slack=slack)

                await manager.send_trade_alert({
                    "market": "BTC",
                    "side": "YES",
                    "entry_price": 0.95,
                    "amount": 10.0,
                })

    @pytest.mark.asyncio
    async def test_send_stop_loss_alert(self):
        """Test stop-loss alert broadcast."""
        telegram = TelegramAlertSender("token", "chat")
        slack = SlackAlertSender("https://hooks.slack.com/services/xxx")

        with patch.object(telegram, "send_stop_loss_alert", return_value=True):
            with patch.object(slack, "send_stop_loss_alert", return_value=True):
                manager = AlertManager(telegram=telegram, slack=slack)

                await manager.send_stop_loss_alert("BTC", -24.5)

    @pytest.mark.asyncio
    async def test_send_take_profit_alert(self):
        """Test take-profit alert broadcast."""
        telegram = TelegramAlertSender("token", "chat")
        slack = SlackAlertSender("https://hooks.slack.com/services/xxx")

        with patch.object(telegram, "send_take_profit_alert", return_value=True):
            with patch.object(slack, "send_take_profit_alert", return_value=True):
                manager = AlertManager(telegram=telegram, slack=slack)

                await manager.send_take_profit_alert("ETH", 10.0)

    @pytest.mark.asyncio
    async def test_send_oracle_guard_block(self):
        """Test Oracle Guard block alert broadcast."""
        telegram = TelegramAlertSender("token", "chat")
        slack = SlackAlertSender("https://hooks.slack.com/services/xxx")

        with patch.object(telegram, "send_oracle_guard_block", return_value=True):
            with patch.object(slack, "send_oracle_guard_block", return_value=True):
                manager = AlertManager(telegram=telegram, slack=slack)

                await manager.send_oracle_guard_block("SOL", "oracle_vol_high")

    @pytest.mark.asyncio
    async def test_send_daily_report_summary(self):
        """Test daily report summary broadcast."""
        telegram = TelegramAlertSender("token", "chat")
        slack = SlackAlertSender("https://hooks.slack.com/services/xxx")

        with patch.object(telegram, "send_daily_report_summary", return_value=True):
            with patch.object(slack, "send_daily_report_summary", return_value=True):
                manager = AlertManager(telegram=telegram, slack=slack)

                await manager.send_daily_report_summary("Report text")

    @pytest.mark.asyncio
    async def test_no_alert_when_disabled(self):
        """Test that alerts are not sent when manager is disabled."""
        manager = AlertManager()  # No channels configured

        # Should not raise any errors
        await manager.broadcast_alert("Test")
        await manager.send_trade_alert({"market": "BTC", "side": "YES"})
        await manager.send_stop_loss_alert("BTC", -10.0)
        await manager.send_take_profit_alert("BTC", 10.0)
        await manager.send_oracle_guard_block("BTC", "reason")
        await manager.send_daily_report_summary("Report")


class TestAlertFormatting:
    """Test alert message formatting."""

    @pytest.mark.asyncio
    async def test_telegram_uses_html_formatting(self):
        """Test that Telegram uses HTML formatting."""
        sender = TelegramAlertSender("token", "chat")

        with patch("aiohttp.ClientSession.post") as mock_post:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.text = AsyncMock(return_value="OK")
            mock_post.return_value.__aenter__.return_value = mock_response

            await sender.send_trade_alert({
                "market": "BTC",
                "side": "YES",
                "entry_price": 0.95,
                "amount": 10.0,
            })

            # Verify that parse_mode is set to HTML
            call_args = mock_post.call_args
            payload = call_args[1]["json"]
            assert payload.get("parse_mode") == "HTML"

    @pytest.mark.asyncio
    async def test_slack_uses_plain_text(self):
        """Test that Slack uses plain text."""
        sender = SlackAlertSender("https://hooks.slack.com/services/xxx")

        with patch("aiohttp.ClientSession.post") as mock_post:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.text = AsyncMock(return_value="OK")
            mock_post.return_value.__aenter__.return_value = mock_response

            await sender.send_alert("Test message")

            # Verify payload structure
            call_args = mock_post.call_args
            payload = call_args[1]["json"]
            assert "text" in payload
            assert payload["text"] == "Test message"


class TestErrorHandling:
    """Test error handling in alert senders."""

    @pytest.mark.asyncio
    async def test_telegram_network_error(self):
        """Test Telegram handling of network errors."""
        sender = TelegramAlertSender("token", "chat")

        with patch("aiohttp.ClientSession.post") as mock_post:
            from aiohttp import ClientError

            mock_post.side_effect = ClientError("Network error")

            result = await sender.send_alert("Test message")
            assert result is False

    @pytest.mark.asyncio
    async def test_slack_network_error(self):
        """Test Slack handling of network errors."""
        sender = SlackAlertSender("https://hooks.slack.com/services/xxx")

        with patch("aiohttp.ClientSession.post") as mock_post:
            from aiohttp import ClientError

            mock_post.side_effect = ClientError("Network error")

            result = await sender.send_alert("Test message")
            assert result is False

    @pytest.mark.asyncio
    async def test_alert_manager_continues_on_failure(self):
        """Test that AlertManager continues even if one channel fails."""
        telegram = TelegramAlertSender("token", "chat")
        slack = SlackAlertSender("https://hooks.slack.com/services/xxx")

        with patch.object(telegram, "send_alert", side_effect=Exception("Telegram error")):
            with patch.object(slack, "send_alert", return_value=True) as mock_slack:
                manager = AlertManager(telegram=telegram, slack=slack)

                # Should not raise exception
                await manager.broadcast_alert("Test")

                # Slack should still be called
                mock_slack.assert_called_once_with("Test")
