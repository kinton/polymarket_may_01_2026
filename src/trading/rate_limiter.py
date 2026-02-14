"""
Rate limiter for Polymarket API calls using token bucket algorithm.

Prevents hitting API rate limits by throttling outgoing requests.
Thread-safe and async-compatible.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Token bucket rate limiter for API calls.

    Allows up to `max_tokens` requests, refilling at `refill_rate` tokens/second.
    When no tokens available, callers wait until a token is replenished.

    Args:
        max_tokens: Maximum burst capacity (bucket size).
        refill_rate: Tokens added per second.
        name: Human-readable name for logging.
    """

    def __init__(
        self,
        max_tokens: float = 10.0,
        refill_rate: float = 2.0,
        name: str = "default",
    ) -> None:
        self.max_tokens = max_tokens
        self.refill_rate = refill_rate
        self.name = name
        self._tokens = max_tokens
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()
        self._total_acquired = 0
        self._total_waited = 0.0

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        new_tokens = elapsed * self.refill_rate
        self._tokens = min(self.max_tokens, self._tokens + new_tokens)
        self._last_refill = now

    async def acquire(self, tokens: float = 1.0) -> float:
        """
        Acquire tokens, waiting if necessary.

        Args:
            tokens: Number of tokens to consume (default 1).

        Returns:
            Time waited in seconds (0.0 if immediate).
        """
        waited = 0.0
        async with self._lock:
            self._refill()

            while self._tokens < tokens:
                deficit = tokens - self._tokens
                wait_time = deficit / self.refill_rate
                logger.debug(
                    "⏳ [%s] Rate limited, waiting %.2fs (%.1f/%.1f tokens)",
                    self.name,
                    wait_time,
                    self._tokens,
                    self.max_tokens,
                )
                # Release lock while sleeping so other coroutines aren't blocked
                # on the lock itself. We re-check tokens after.
                self._lock.release()
                await asyncio.sleep(wait_time)
                waited += wait_time
                await self._lock.acquire()
                self._refill()

            self._tokens -= tokens
            self._total_acquired += 1
            self._total_waited += waited

        if waited > 0:
            logger.info(
                "⏳ [%s] Rate limit wait: %.2fs",
                self.name,
                waited,
            )
        return waited

    @property
    def available_tokens(self) -> float:
        """Current available tokens (approximate, no lock)."""
        elapsed = time.monotonic() - self._last_refill
        return min(self.max_tokens, self._tokens + elapsed * self.refill_rate)

    @property
    def stats(self) -> dict[str, Any]:
        """Return rate limiter statistics."""
        return {
            "name": self.name,
            "max_tokens": self.max_tokens,
            "refill_rate": self.refill_rate,
            "available_tokens": round(self.available_tokens, 2),
            "total_acquired": self._total_acquired,
            "total_waited_seconds": round(self._total_waited, 3),
        }


class MultiRateLimiter:
    """
    Manages multiple named rate limiters for different API endpoints.

    Usage:
        limiter = MultiRateLimiter()
        limiter.add("orders", max_tokens=5, refill_rate=1.0)
        limiter.add("reads", max_tokens=20, refill_rate=5.0)
        await limiter.acquire("orders")
    """

    def __init__(self) -> None:
        self._limiters: dict[str, RateLimiter] = {}

    def add(
        self,
        name: str,
        max_tokens: float = 10.0,
        refill_rate: float = 2.0,
    ) -> RateLimiter:
        """Add a named rate limiter."""
        limiter = RateLimiter(
            max_tokens=max_tokens,
            refill_rate=refill_rate,
            name=name,
        )
        self._limiters[name] = limiter
        return limiter

    def get(self, name: str) -> RateLimiter | None:
        """Get a rate limiter by name."""
        return self._limiters.get(name)

    async def acquire(self, name: str, tokens: float = 1.0) -> float:
        """Acquire tokens from a named limiter. Returns wait time."""
        limiter = self._limiters.get(name)
        if limiter is None:
            return 0.0
        return await limiter.acquire(tokens)

    @property
    def stats(self) -> dict[str, Any]:
        """Return stats for all limiters."""
        return {name: lim.stats for name, lim in self._limiters.items()}


def rate_limited(
    limiter_attr: str = "rate_limiter",
    limiter_name: str | None = None,
    tokens: float = 1.0,
) -> Callable[..., Any]:
    """
    Decorator for async methods to apply rate limiting.

    Args:
        limiter_attr: Name of the attribute on `self` holding the RateLimiter
            or MultiRateLimiter.
        limiter_name: If using MultiRateLimiter, the specific limiter name.
        tokens: Tokens to consume per call.

    Usage:
        class MyClient:
            def __init__(self):
                self.rate_limiter = RateLimiter(max_tokens=5)

            @rate_limited()
            async def call_api(self):
                ...
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        async def wrapper(self_obj: Any, *args: Any, **kwargs: Any) -> Any:
            limiter = getattr(self_obj, limiter_attr, None)
            if limiter is not None:
                if isinstance(limiter, MultiRateLimiter) and limiter_name:
                    await limiter.acquire(limiter_name, tokens)
                elif isinstance(limiter, RateLimiter):
                    await limiter.acquire(tokens)
            return await func(self_obj, *args, **kwargs)

        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper

    return decorator
