"""Tests for the rate limiter module."""

import asyncio
import time

import pytest

from src.trading.rate_limiter import MultiRateLimiter, RateLimiter, rate_limited


@pytest.mark.asyncio
async def test_basic_acquire():
    """Acquire tokens within capacity should be instant."""
    limiter = RateLimiter(max_tokens=5.0, refill_rate=1.0, name="test")
    waited = await limiter.acquire()
    assert waited == 0.0
    assert limiter._total_acquired == 1


@pytest.mark.asyncio
async def test_burst_capacity():
    """Should allow burst up to max_tokens without waiting."""
    limiter = RateLimiter(max_tokens=3.0, refill_rate=1.0, name="test")
    for _ in range(3):
        waited = await limiter.acquire()
        assert waited == 0.0
    assert limiter._total_acquired == 3


@pytest.mark.asyncio
async def test_rate_limit_wait():
    """Should wait when tokens are exhausted."""
    limiter = RateLimiter(max_tokens=1.0, refill_rate=10.0, name="test")
    # First acquire: instant
    await limiter.acquire()
    # Second: must wait ~0.1s
    t0 = time.monotonic()
    waited = await limiter.acquire()
    elapsed = time.monotonic() - t0
    assert waited > 0
    assert elapsed >= 0.05  # at least some wait


@pytest.mark.asyncio
async def test_refill_over_time():
    """Tokens should refill over time."""
    limiter = RateLimiter(max_tokens=2.0, refill_rate=100.0, name="test")
    await limiter.acquire(2.0)  # drain
    await asyncio.sleep(0.05)  # ~5 tokens refilled (capped to 2)
    waited = await limiter.acquire(1.0)
    assert waited == 0.0


@pytest.mark.asyncio
async def test_available_tokens_property():
    """available_tokens should reflect approximate state."""
    limiter = RateLimiter(max_tokens=10.0, refill_rate=1.0, name="test")
    assert limiter.available_tokens <= 10.0
    assert limiter.available_tokens >= 9.9


@pytest.mark.asyncio
async def test_stats():
    """Stats should reflect usage."""
    limiter = RateLimiter(max_tokens=5.0, refill_rate=1.0, name="test-stats")
    await limiter.acquire()
    await limiter.acquire()
    stats = limiter.stats
    assert stats["name"] == "test-stats"
    assert stats["total_acquired"] == 2
    assert stats["max_tokens"] == 5.0


@pytest.mark.asyncio
async def test_multi_rate_limiter_add_and_acquire():
    """MultiRateLimiter should manage multiple named limiters."""
    multi = MultiRateLimiter()
    multi.add("orders", max_tokens=5.0, refill_rate=1.0)
    multi.add("reads", max_tokens=20.0, refill_rate=5.0)

    waited = await multi.acquire("orders")
    assert waited == 0.0
    waited = await multi.acquire("reads")
    assert waited == 0.0


@pytest.mark.asyncio
async def test_multi_rate_limiter_unknown_name():
    """Acquiring from unknown limiter should return 0."""
    multi = MultiRateLimiter()
    waited = await multi.acquire("nonexistent")
    assert waited == 0.0


@pytest.mark.asyncio
async def test_multi_rate_limiter_get():
    """Get should return the right limiter or None."""
    multi = MultiRateLimiter()
    multi.add("api", max_tokens=3.0)
    assert multi.get("api") is not None
    assert multi.get("missing") is None


@pytest.mark.asyncio
async def test_multi_rate_limiter_stats():
    """Stats should aggregate all limiters."""
    multi = MultiRateLimiter()
    multi.add("a", max_tokens=1.0)
    multi.add("b", max_tokens=2.0)
    stats = multi.stats
    assert "a" in stats
    assert "b" in stats


@pytest.mark.asyncio
async def test_rate_limited_decorator():
    """rate_limited decorator should throttle calls."""

    class FakeClient:
        def __init__(self):
            self.rate_limiter = RateLimiter(max_tokens=5.0, refill_rate=10.0)
            self.call_count = 0

        @rate_limited()
        async def do_request(self):
            self.call_count += 1
            return "ok"

    client = FakeClient()
    result = await client.do_request()
    assert result == "ok"
    assert client.call_count == 1


@pytest.mark.asyncio
async def test_rate_limited_decorator_with_multi():
    """rate_limited with MultiRateLimiter should use named limiter."""

    class FakeClient:
        def __init__(self):
            self.rate_limiter = MultiRateLimiter()
            self.rate_limiter.add("api", max_tokens=3.0, refill_rate=10.0)
            self.call_count = 0

        @rate_limited(limiter_name="api")
        async def do_request(self):
            self.call_count += 1
            return "done"

    client = FakeClient()
    result = await client.do_request()
    assert result == "done"


@pytest.mark.asyncio
async def test_rate_limited_decorator_no_limiter():
    """If no limiter attribute, decorator should pass through."""

    class NoLimiter:
        @rate_limited(limiter_attr="missing_attr")
        async def do_request(self):
            return 42

    obj = NoLimiter()
    assert await obj.do_request() == 42


@pytest.mark.asyncio
async def test_concurrent_acquire():
    """Multiple concurrent acquires should be properly serialized."""
    limiter = RateLimiter(max_tokens=2.0, refill_rate=100.0, name="concurrent")
    results = await asyncio.gather(
        limiter.acquire(),
        limiter.acquire(),
        limiter.acquire(),
    )
    # First two should be instant, third may wait briefly
    assert sum(1 for r in results if r == 0.0) >= 2


@pytest.mark.asyncio
async def test_acquire_multiple_tokens():
    """Should be able to acquire multiple tokens at once."""
    limiter = RateLimiter(max_tokens=5.0, refill_rate=1.0, name="test")
    waited = await limiter.acquire(3.0)
    assert waited == 0.0
    assert limiter._total_acquired == 1
