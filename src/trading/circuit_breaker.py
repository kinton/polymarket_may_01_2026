"""
Circuit breaker pattern for API calls.

Prevents cascading failures by temporarily blocking calls to a failing service.
States: CLOSED (normal) → OPEN (blocked) → HALF_OPEN (probe) → CLOSED.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class CircuitState(enum.Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation, requests flow through
    OPEN = "open"  # Failures exceeded threshold, requests blocked
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitOpenError(Exception):
    """Raised when a call is attempted while the circuit is open."""

    def __init__(self, name: str, remaining_seconds: float) -> None:
        self.name = name
        self.remaining_seconds = remaining_seconds
        super().__init__(
            f"Circuit '{name}' is OPEN — retry in {remaining_seconds:.1f}s"
        )


class CircuitBreaker:
    """
    Circuit breaker for async callables.

    Args:
        failure_threshold: Number of consecutive failures to trip the circuit.
        recovery_timeout: Seconds the circuit stays OPEN before moving to HALF_OPEN.
        half_open_max_calls: Max probe calls allowed in HALF_OPEN state.
        name: Human-readable name for logging.
        on_state_change: Optional callback(name, old_state, new_state).
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 1,
        name: str = "default",
        on_state_change: Callable[[str, CircuitState, CircuitState], None] | None = None,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self.name = name
        self._on_state_change = on_state_change

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float = 0.0
        self._half_open_calls = 0
        self._lock = asyncio.Lock()

        # Stats
        self._total_calls = 0
        self._total_failures = 0
        self._total_blocked = 0
        self._total_successes = 0

    @property
    def state(self) -> CircuitState:
        """Current circuit state (may auto-transition OPEN → HALF_OPEN)."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.recovery_timeout:
                return CircuitState.HALF_OPEN
        return self._state

    @property
    def failure_count(self) -> int:
        return self._failure_count

    def stats(self) -> dict[str, Any]:
        """Return circuit breaker statistics."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "total_calls": self._total_calls,
            "total_failures": self._total_failures,
            "total_blocked": self._total_blocked,
            "total_successes": self._total_successes,
        }

    def _transition(self, new_state: CircuitState) -> None:
        """Transition to a new state with logging and optional callback."""
        old_state = self._state
        if old_state == new_state:
            return
        self._state = new_state
        logger.info(
            "⚡ Circuit '%s': %s → %s",
            self.name,
            old_state.value,
            new_state.value,
        )
        if self._on_state_change:
            try:
                self._on_state_change(self.name, old_state, new_state)
            except Exception:
                logger.exception("Error in circuit breaker state change callback")

    async def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """
        Execute an async function through the circuit breaker.

        Args:
            func: Async callable.
            *args: Positional arguments.
            **kwargs: Keyword arguments.

        Returns:
            Result of the function.

        Raises:
            CircuitOpenError: If circuit is OPEN and recovery timeout hasn't elapsed.
        """
        async with self._lock:
            current_state = self.state
            self._total_calls += 1

            if current_state == CircuitState.OPEN:
                self._total_blocked += 1
                remaining = self.recovery_timeout - (
                    time.monotonic() - self._last_failure_time
                )
                raise CircuitOpenError(self.name, max(remaining, 0.0))

            if current_state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.half_open_max_calls:
                    self._total_blocked += 1
                    raise CircuitOpenError(self.name, 0.0)
                self._half_open_calls += 1
                # Actually transition internal state so logging fires
                self._transition(CircuitState.HALF_OPEN)

        # Execute outside lock to avoid holding it during I/O
        try:
            result = await func(*args, **kwargs)
        except Exception as exc:
            await self._record_failure(exc)
            raise
        else:
            await self._record_success()
            return result

    async def _record_success(self) -> None:
        async with self._lock:
            self._total_successes += 1
            if self._state == CircuitState.HALF_OPEN or self.state == CircuitState.HALF_OPEN:
                # Probe succeeded — close circuit
                self._failure_count = 0
                self._success_count += 1
                self._half_open_calls = 0
                self._transition(CircuitState.CLOSED)
                logger.info(
                    "✅ Circuit '%s': probe succeeded, circuit CLOSED",
                    self.name,
                )
            else:
                self._failure_count = 0
                self._success_count += 1

    async def _record_failure(self, exc: Exception) -> None:
        async with self._lock:
            self._total_failures += 1
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN or self.state == CircuitState.HALF_OPEN:
                # Probe failed — re-open circuit
                self._half_open_calls = 0
                self._transition(CircuitState.OPEN)
                logger.warning(
                    "🔴 Circuit '%s': probe FAILED (%s), re-opening for %.0fs",
                    self.name,
                    exc,
                    self.recovery_timeout,
                )
            elif self._failure_count >= self.failure_threshold:
                self._transition(CircuitState.OPEN)
                logger.warning(
                    "🔴 Circuit '%s': %d consecutive failures, OPENING for %.0fs",
                    self.name,
                    self._failure_count,
                    self.recovery_timeout,
                )

    def reset(self) -> None:
        """Manually reset the circuit to CLOSED state."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._half_open_calls = 0
        self._last_failure_time = 0.0
        logger.info("🔄 Circuit '%s': manually reset to CLOSED", self.name)
