"""
Parallel market launcher — starts multiple traders concurrently.

Instead of launching traders one-by-one in a sequential for-loop,
ParallelLauncher uses asyncio.gather to initialize and start all
eligible markets in parallel, reducing startup latency when multiple
markets are discovered simultaneously.

Usage:
    launcher = ParallelLauncher(max_concurrency=3)
    results = await launcher.launch(markets, start_fn)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional, Sequence


logger = logging.getLogger(__name__)


@dataclass
class LaunchResult:
    """Result of a single market launch attempt."""

    condition_id: str
    success: bool
    elapsed_ms: float
    error: Optional[str] = None


@dataclass
class BatchLaunchResult:
    """Aggregated result of a parallel launch batch."""

    total: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    elapsed_ms: float = 0.0
    results: List[LaunchResult] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return self.failed == 0


StartFn = Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]


class ParallelLauncher:
    """
    Launch multiple market traders concurrently via asyncio.gather.

    Args:
        max_concurrency: Maximum number of traders to launch simultaneously.
            Uses asyncio.Semaphore to cap parallelism.
        timeout: Per-launch timeout in seconds.  None = no timeout.
    """

    def __init__(
        self,
        max_concurrency: int = 5,
        timeout: Optional[float] = 30.0,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self.max_concurrency = max_concurrency
        self.timeout = timeout
        self._semaphore: Optional[asyncio.Semaphore] = None

    async def _launch_one(
        self,
        market: Dict[str, Any],
        start_fn: StartFn,
    ) -> LaunchResult:
        """Launch a single trader with semaphore + optional timeout."""
        condition_id = market.get("condition_id", "unknown")
        sem = self._semaphore or asyncio.Semaphore(self.max_concurrency)
        t0 = time.monotonic()

        try:
            async with sem:
                if self.timeout is not None:
                    await asyncio.wait_for(start_fn(market), timeout=self.timeout)
                else:
                    await start_fn(market)

            elapsed = (time.monotonic() - t0) * 1000
            logger.info(
                f"Launched trader for {condition_id} in {elapsed:.0f}ms"
            )
            return LaunchResult(
                condition_id=condition_id,
                success=True,
                elapsed_ms=elapsed,
            )

        except asyncio.TimeoutError:
            elapsed = (time.monotonic() - t0) * 1000
            msg = f"Timeout ({self.timeout}s) launching trader for {condition_id}"
            logger.warning(msg)
            return LaunchResult(
                condition_id=condition_id,
                success=False,
                elapsed_ms=elapsed,
                error=msg,
            )

        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            msg = f"Error launching trader for {condition_id}: {exc}"
            logger.error(msg, exc_info=True)
            return LaunchResult(
                condition_id=condition_id,
                success=False,
                elapsed_ms=elapsed,
                error=str(exc),
            )

    async def launch(
        self,
        markets: Sequence[Dict[str, Any]],
        start_fn: StartFn,
    ) -> BatchLaunchResult:
        """
        Launch traders for all markets concurrently.

        Args:
            markets: List of market dicts (must contain 'condition_id').
            start_fn: Async callable that starts a trader for one market.

        Returns:
            BatchLaunchResult with per-market outcomes.
        """
        if not markets:
            return BatchLaunchResult()

        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        t0 = time.monotonic()

        tasks = [self._launch_one(m, start_fn) for m in markets]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        batch = BatchLaunchResult(
            total=len(markets),
            elapsed_ms=(time.monotonic() - t0) * 1000,
        )

        for r in results:
            if isinstance(r, Exception):
                # Should not happen (exceptions caught inside _launch_one),
                # but guard defensively.
                batch.failed += 1
                batch.results.append(
                    LaunchResult(
                        condition_id="unknown",
                        success=False,
                        elapsed_ms=0,
                        error=str(r),
                    )
                )
            else:
                batch.results.append(r)
                if r.success:
                    batch.succeeded += 1
                else:
                    batch.failed += 1

        logger.info(
            f"Parallel launch complete: {batch.succeeded}/{batch.total} ok, "
            f"{batch.failed} failed, {batch.elapsed_ms:.0f}ms total"
        )
        return batch
