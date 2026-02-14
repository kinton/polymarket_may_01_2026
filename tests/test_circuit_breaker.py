"""Tests for circuit breaker pattern."""

import asyncio

import pytest

from src.trading.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
)


@pytest.fixture
def cb() -> CircuitBreaker:
    """Create a circuit breaker with low thresholds for testing."""
    return CircuitBreaker(
        failure_threshold=3,
        recovery_timeout=0.5,
        half_open_max_calls=1,
        name="test",
    )


async def _success() -> str:
    return "ok"


async def _failure() -> str:
    raise ConnectionError("api down")


class TestCircuitBreakerBasic:
    """Test basic circuit breaker behavior."""

    @pytest.mark.asyncio
    async def test_starts_closed(self, cb: CircuitBreaker) -> None:
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_success_stays_closed(self, cb: CircuitBreaker) -> None:
        result = await cb.call(_success)
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    @pytest.mark.asyncio
    async def test_single_failure_stays_closed(self, cb: CircuitBreaker) -> None:
        with pytest.raises(ConnectionError):
            await cb.call(_failure)
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 1

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self, cb: CircuitBreaker) -> None:
        with pytest.raises(ConnectionError):
            await cb.call(_failure)
        with pytest.raises(ConnectionError):
            await cb.call(_failure)
        assert cb.failure_count == 2
        await cb.call(_success)
        assert cb.failure_count == 0


class TestCircuitOpens:
    """Test circuit opening on threshold failures."""

    @pytest.mark.asyncio
    async def test_opens_after_threshold(self, cb: CircuitBreaker) -> None:
        for _ in range(3):
            with pytest.raises(ConnectionError):
                await cb.call(_failure)
        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_blocks_calls_when_open(self, cb: CircuitBreaker) -> None:
        for _ in range(3):
            with pytest.raises(ConnectionError):
                await cb.call(_failure)

        with pytest.raises(CircuitOpenError) as exc_info:
            await cb.call(_success)
        assert "OPEN" in str(exc_info.value)
        assert exc_info.value.name == "test"

    @pytest.mark.asyncio
    async def test_circuit_open_error_has_remaining(self, cb: CircuitBreaker) -> None:
        for _ in range(3):
            with pytest.raises(ConnectionError):
                await cb.call(_failure)

        with pytest.raises(CircuitOpenError) as exc_info:
            await cb.call(_success)
        assert exc_info.value.remaining_seconds >= 0


class TestHalfOpen:
    """Test half-open state and recovery."""

    @pytest.mark.asyncio
    async def test_transitions_to_half_open(self, cb: CircuitBreaker) -> None:
        for _ in range(3):
            with pytest.raises(ConnectionError):
                await cb.call(_failure)
        assert cb.state == CircuitState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(0.6)
        assert cb.state == CircuitState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_probe_success_closes(self, cb: CircuitBreaker) -> None:
        for _ in range(3):
            with pytest.raises(ConnectionError):
                await cb.call(_failure)

        await asyncio.sleep(0.6)
        result = await cb.call(_success)
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_probe_failure_reopens(self, cb: CircuitBreaker) -> None:
        for _ in range(3):
            with pytest.raises(ConnectionError):
                await cb.call(_failure)

        await asyncio.sleep(0.6)
        with pytest.raises(ConnectionError):
            await cb.call(_failure)
        assert cb.state == CircuitState.OPEN


class TestStats:
    """Test statistics tracking."""

    @pytest.mark.asyncio
    async def test_stats_tracking(self, cb: CircuitBreaker) -> None:
        await cb.call(_success)
        await cb.call(_success)
        with pytest.raises(ConnectionError):
            await cb.call(_failure)

        stats = cb.stats()
        assert stats["name"] == "test"
        assert stats["total_calls"] == 3
        assert stats["total_successes"] == 2
        assert stats["total_failures"] == 1
        assert stats["total_blocked"] == 0

    @pytest.mark.asyncio
    async def test_blocked_counted(self, cb: CircuitBreaker) -> None:
        for _ in range(3):
            with pytest.raises(ConnectionError):
                await cb.call(_failure)

        with pytest.raises(CircuitOpenError):
            await cb.call(_success)

        stats = cb.stats()
        assert stats["total_blocked"] == 1


class TestReset:
    """Test manual reset."""

    @pytest.mark.asyncio
    async def test_reset_closes_circuit(self, cb: CircuitBreaker) -> None:
        for _ in range(3):
            with pytest.raises(ConnectionError):
                await cb.call(_failure)
        assert cb.state == CircuitState.OPEN

        cb.reset()
        assert cb.state == CircuitState.CLOSED
        result = await cb.call(_success)
        assert result == "ok"


class TestStateChangeCallback:
    """Test on_state_change callback."""

    @pytest.mark.asyncio
    async def test_callback_fires(self) -> None:
        changes: list[tuple[str, CircuitState, CircuitState]] = []

        def on_change(name: str, old: CircuitState, new: CircuitState) -> None:
            changes.append((name, old, new))

        cb = CircuitBreaker(
            failure_threshold=2,
            recovery_timeout=0.3,
            name="cb-test",
            on_state_change=on_change,
        )

        with pytest.raises(ConnectionError):
            await cb.call(_failure)
        with pytest.raises(ConnectionError):
            await cb.call(_failure)

        assert len(changes) == 1
        assert changes[0] == ("cb-test", CircuitState.CLOSED, CircuitState.OPEN)
