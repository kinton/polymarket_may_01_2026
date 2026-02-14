"""Tests for retry logic with exponential backoff."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.trading.retry import (
    DEFAULT_RETRIABLE_EXCEPTIONS,
    retry_api_call,
    retry_async,
    with_retry,
)


@pytest.mark.asyncio
async def test_retry_async_success_first_attempt():
    """Function succeeds on first attempt — no retries."""
    func = AsyncMock(return_value="ok")
    result = await retry_async(func, max_retries=3, base_delay=0.01)
    assert result == "ok"
    assert func.call_count == 1


@pytest.mark.asyncio
async def test_retry_async_success_after_retries():
    """Function fails twice, then succeeds."""
    func = AsyncMock(side_effect=[ConnectionError("fail"), ConnectionError("fail"), "ok"])
    result = await retry_async(func, max_retries=3, base_delay=0.01)
    assert result == "ok"
    assert func.call_count == 3


@pytest.mark.asyncio
async def test_retry_async_exhausted():
    """All retries exhausted — raises last exception."""
    func = AsyncMock(side_effect=ConnectionError("down"))
    with pytest.raises(ConnectionError, match="down"):
        await retry_async(func, max_retries=2, base_delay=0.01)
    assert func.call_count == 3  # 1 initial + 2 retries


@pytest.mark.asyncio
async def test_retry_async_non_retriable_exception():
    """Non-retriable exception is raised immediately."""
    func = AsyncMock(side_effect=ValueError("bad input"))
    with pytest.raises(ValueError, match="bad input"):
        await retry_async(func, max_retries=3, base_delay=0.01)
    assert func.call_count == 1  # No retries


@pytest.mark.asyncio
async def test_retry_async_custom_retriable_exceptions():
    """Custom retriable exceptions are respected."""
    func = AsyncMock(side_effect=[ValueError("retry me"), "ok"])
    result = await retry_async(
        func,
        max_retries=3,
        base_delay=0.01,
        retriable_exceptions=(ValueError,),
    )
    assert result == "ok"
    assert func.call_count == 2


@pytest.mark.asyncio
async def test_retry_async_zero_retries():
    """max_retries=0 means no retries at all."""
    func = AsyncMock(side_effect=ConnectionError("fail"))
    with pytest.raises(ConnectionError):
        await retry_async(func, max_retries=0, base_delay=0.01)
    assert func.call_count == 1


@pytest.mark.asyncio
async def test_retry_async_passes_args():
    """Arguments are correctly forwarded to the function."""
    func = AsyncMock(return_value="result")
    await retry_async(func, "a", "b", max_retries=1, base_delay=0.01, key="val")
    func.assert_called_once_with("a", "b", key="val")


@pytest.mark.asyncio
async def test_retry_api_call_success():
    """retry_api_call wraps sync method via to_thread with retry."""
    mock_method = MagicMock(return_value={"status": "matched"})
    result = await retry_api_call(
        mock_method,
        "order_123",
        max_retries=2,
        base_delay=0.01,
        operation_name="test:get_order",
    )
    assert result == {"status": "matched"}
    mock_method.assert_called_once_with("order_123")


@pytest.mark.asyncio
async def test_retry_api_call_retries_on_connection_error():
    """retry_api_call retries sync method on ConnectionError."""
    call_count = 0

    def flaky_method(arg: str) -> str:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError("connection reset")
        return f"success:{arg}"

    result = await retry_api_call(
        flaky_method,
        "test",
        max_retries=3,
        base_delay=0.01,
    )
    assert result == "success:test"
    assert call_count == 3


@pytest.mark.asyncio
async def test_retry_api_call_timeout_error():
    """retry_api_call retries on TimeoutError."""
    mock_method = MagicMock(side_effect=[TimeoutError("timeout"), "ok"])
    result = await retry_api_call(mock_method, max_retries=2, base_delay=0.01)
    assert result == "ok"
    assert mock_method.call_count == 2


@pytest.mark.asyncio
async def test_with_retry_decorator():
    """@with_retry decorator adds retry logic."""
    call_count = 0

    @with_retry(max_retries=2, base_delay=0.01)
    async def flaky_func() -> str:
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise ConnectionError("fail")
        return "ok"

    result = await flaky_func()
    assert result == "ok"
    assert call_count == 2


@pytest.mark.asyncio
async def test_with_retry_decorator_exhausted():
    """@with_retry raises after exhausting retries."""

    @with_retry(max_retries=1, base_delay=0.01)
    async def always_fails() -> str:
        raise OSError("disk error")

    with pytest.raises(OSError, match="disk error"):
        await always_fails()


@pytest.mark.asyncio
async def test_retry_async_exponential_delay():
    """Verify delays increase exponentially (roughly)."""
    delays: list[float] = []

    async def mock_sleep(seconds: float) -> None:
        delays.append(seconds)
        # Don't actually sleep in tests

    func = AsyncMock(side_effect=[ConnectionError("1"), ConnectionError("2"), "ok"])

    with patch("src.trading.retry.asyncio.sleep", side_effect=mock_sleep):
        result = await retry_async(func, max_retries=3, base_delay=1.0, jitter=False)

    assert result == "ok"
    assert len(delays) == 2
    # First delay: 1.0 * 2^0 = 1.0, second: 1.0 * 2^1 = 2.0
    assert delays[0] == pytest.approx(1.0)
    assert delays[1] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_retry_async_max_delay_cap():
    """Delay is capped at max_delay."""
    delays: list[float] = []

    async def mock_sleep(seconds: float) -> None:
        delays.append(seconds)

    func = AsyncMock(
        side_effect=[ConnectionError()] * 5 + ["ok"]
    )

    with patch("src.trading.retry.asyncio.sleep", side_effect=mock_sleep):
        result = await retry_async(
            func, max_retries=5, base_delay=1.0, max_delay=5.0, jitter=False
        )

    assert result == "ok"
    # All delays should be <= 5.0
    assert all(d <= 5.0 for d in delays)


def test_default_retriable_exceptions():
    """Default retriable exceptions include expected types."""
    assert ConnectionError in DEFAULT_RETRIABLE_EXCEPTIONS
    assert TimeoutError in DEFAULT_RETRIABLE_EXCEPTIONS
    assert OSError in DEFAULT_RETRIABLE_EXCEPTIONS
