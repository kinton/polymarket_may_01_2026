"""
Retry logic for API calls with exponential backoff.

Provides a decorator and utility function for retrying failed API calls
with configurable delays, max attempts, and retriable exception types.
"""

import asyncio
import functools
import logging
import random
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Default retriable exceptions (network/transient errors)
DEFAULT_RETRIABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
)


async def retry_async(
    func: Callable[..., Any],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: bool = True,
    retriable_exceptions: tuple[type[BaseException], ...] | None = None,
    operation_name: str = "",
    **kwargs: Any,
) -> Any:
    """
    Retry an async function with exponential backoff.

    Args:
        func: Async callable to retry.
        *args: Positional arguments for func.
        max_retries: Maximum number of retry attempts (0 = no retries).
        base_delay: Base delay in seconds between retries.
        max_delay: Maximum delay cap in seconds.
        jitter: Add random jitter to delay to avoid thundering herd.
        retriable_exceptions: Tuple of exception types to retry on.
            Defaults to ConnectionError, TimeoutError, OSError.
        operation_name: Human-readable name for logging.
        **kwargs: Keyword arguments for func.

    Returns:
        Result of the function call.

    Raises:
        The last exception if all retries are exhausted.
    """
    if retriable_exceptions is None:
        retriable_exceptions = DEFAULT_RETRIABLE_EXCEPTIONS

    last_exception: BaseException | None = None
    op_label = f" [{operation_name}]" if operation_name else ""

    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except retriable_exceptions as e:
            last_exception = e
            if attempt >= max_retries:
                logger.error(
                    "❌%s Failed after %d attempts: %s",
                    op_label,
                    attempt + 1,
                    e,
                )
                raise

            delay = min(base_delay * (2**attempt), max_delay)
            if jitter:
                delay = delay * (0.5 + random.random() * 0.5)  # noqa: S311

            logger.warning(
                "⚠️%s Attempt %d/%d failed: %s — retrying in %.1fs",
                op_label,
                attempt + 1,
                max_retries + 1,
                e,
                delay,
            )
            await asyncio.sleep(delay)

    # Should not reach here, but just in case
    if last_exception:
        raise last_exception
    raise RuntimeError("retry_async: unexpected state")


async def retry_api_call(
    client_method: Callable[..., Any],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    operation_name: str = "",
    **kwargs: Any,
) -> Any:
    """
    Retry a synchronous CLOB client method via asyncio.to_thread with exponential backoff.

    This is a convenience wrapper for the common pattern of:
        await asyncio.to_thread(client.some_method, arg1, arg2)

    Args:
        client_method: Synchronous method to call in a thread.
        *args: Positional arguments for the method.
        max_retries: Maximum retry attempts.
        base_delay: Base delay between retries.
        max_delay: Maximum delay cap.
        operation_name: Human-readable operation name for logs.
        **kwargs: Keyword arguments for the method.

    Returns:
        Result of the client method call.
    """

    async def _wrapped() -> Any:
        return await asyncio.to_thread(client_method, *args, **kwargs)

    return await retry_async(
        _wrapped,
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
        operation_name=operation_name,
    )


def with_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retriable_exceptions: tuple[type[BaseException], ...] | None = None,
) -> Callable[..., Any]:
    """
    Decorator for async functions to add retry logic with exponential backoff.

    Usage:
        @with_retry(max_retries=3, base_delay=1.0)
        async def fetch_data():
            ...
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await retry_async(
                func,
                *args,
                max_retries=max_retries,
                base_delay=base_delay,
                max_delay=max_delay,
                retriable_exceptions=retriable_exceptions,
                operation_name=func.__name__,
                **kwargs,
            )

        return wrapper

    return decorator
