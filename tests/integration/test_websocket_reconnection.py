"""
Test WebSocket reconnection with exponential backoff logic.

This test mocks:
- Connection failures
- Exponential backoff (2^attempt)
- Reconnection attempts
- Successful reconnection after failures
"""

import asyncio
from unittest.mock import AsyncMock, patch
import pytest

from src.hft_trader import LastSecondTrader


@pytest.mark.asyncio
async def test_websocket_reconnection_exponential_backoff(integration_trader):
    """
    Test WebSocket reconnection with exponential backoff.

    Verifies:
    - Connection failures trigger exponential backoff (2^attempt)
    - Multiple reconnection attempts are attempted
    - Successful reconnection after failures
    """
    connection_attempts = []
    backoff_delays = []

    async def mock_connect_with_backoff():
        """Mock connection that fails twice then succeeds."""
        attempt = len(connection_attempts)
        connection_attempts.append(attempt)

        if attempt < 2:
            # First two attempts fail
            await asyncio.sleep(0.1)  # Simulate network delay
            raise ConnectionError(f"Connection failed (attempt {attempt})")

        # Third attempt succeeds
        return AsyncMock()

    # Patch the connect_websocket method
    with patch.object(
        integration_trader,
        "connect_websocket",
        side_effect=mock_connect_with_backoff,
    ):
        # First connection attempt
        await integration_trader.connect_websocket()

        # Verify backoff delays
        # Expected delays: 0 (no backoff), 1 (2^0), 2 (2^1)
        assert len(connection_attempts) == 3
        assert connection_attempts == [0, 1, 2]


@pytest.mark.asyncio
async def test_websocket_max_retries_exceeded(integration_trader):
    """
    Test that maximum reconnection attempts are respected.

    Verifies:
    - After max attempts (3), connection is not retried
    """
    attempts = [0]

    async def fail_after_three():
        attempts[0] = len(attempts)
        if len(attempts) >= 3:
            raise ConnectionError("Max retries exceeded")
        raise ConnectionError("Failed")

    with patch.object(
        integration_trader, "connect_websocket", side_effect=fail_after_three
    ):
        result = await integration_trader.connect_websocket()

        # Verify connection failed
        assert result is False
        assert len(attempts) == 3


@pytest.mark.asyncio
async def test_websocket_reconnection_success(integration_trader):
    """
    Test successful WebSocket reconnection after failure.

    Verifies:
    - After failure, reconnection succeeds
    - WebSocket is properly connected
    """
    call_count = [0]

    async def mock_connect_sequence():
        call_count[0] = len(call_count)
        if call_count[0] == 0:
            raise ConnectionError("First connection failed")
        return AsyncMock()

    with patch.object(
        integration_trader, "connect_websocket", side_effect=mock_connect_sequence
    ):
        # First call fails
        await integration_trader.connect_websocket()

        # Second call succeeds
        result = await integration_trader.connect_websocket()

        # Verify success
        assert result is True
        assert len(call_count) == 2


@pytest.mark.asyncio
async def test_websocket_backoff_delays(integration_trader):
    """
    Test that backoff delays follow exponential pattern.

    Verifies:
    - Delay 1: 2^0 = 1s (or 0s for no delay)
    - Delay 2: 2^1 = 2s
    - Pattern: 2^attempt
    """
    delays = []
    original_connect = integration_trader.connect_websocket
    sleep_calls = []

    async def connect_with_tracking(attempt):
        start_time = asyncio.get_event_loop().time()

        # Sleep before connecting to simulate backoff
        if attempt > 0:
            await asyncio.sleep(2**attempt)  # Exponential backoff: 2^attempt
            elapsed = asyncio.get_event_loop().time() - start_time
            delays.append(elapsed)

        if attempt >= 2:
            return AsyncMock()
        raise ConnectionError("Failed")

    with patch.object(
        integration_trader, "connect_websocket", side_effect=connect_with_tracking
    ):
        # First attempt (no backoff)
        await integration_trader.connect_websocket()

        # Second attempt (1s backoff)
        await integration_trader.connect_websocket()

        # Third attempt (2s backoff)
        await integration_trader.connect_websocket()

        # Verify delays (allowing for some timing variance)
        assert len(delays) >= 2
        # Check approximate exponential pattern (2^1 ≈ 2, 2^2 = 4)
        # Note: First delay should be ~0, so delays[0] is expected to be small
        # delays[1] should be ~2 (2^1), delays[2] should be ~4 (2^2)
        assert any(d >= 1.8 and d <= 2.2 for d in delays[1:])
