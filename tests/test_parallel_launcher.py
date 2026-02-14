"""Tests for src/trading/parallel_launcher.py — ParallelLauncher."""

import asyncio
import pytest
from src.trading.parallel_launcher import (
    ParallelLauncher,
    LaunchResult,
    BatchLaunchResult,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _market(cid: str = "0xabc") -> dict:
    return {"condition_id": cid, "title": f"market-{cid}"}


async def _ok_start(market: dict) -> None:
    """Simulates a successful (fast) trader start."""
    await asyncio.sleep(0.01)


async def _fail_start(market: dict) -> None:
    raise RuntimeError("boom")


async def _slow_start(market: dict) -> None:
    await asyncio.sleep(5)


# ---------------------------------------------------------------------------
# LaunchResult / BatchLaunchResult dataclasses
# ---------------------------------------------------------------------------


def test_launch_result_fields():
    r = LaunchResult(condition_id="0x1", success=True, elapsed_ms=42.0)
    assert r.success is True
    assert r.error is None


def test_batch_launch_result_all_ok():
    b = BatchLaunchResult(total=2, succeeded=2, failed=0)
    assert b.all_ok is True


def test_batch_launch_result_has_failures():
    b = BatchLaunchResult(total=2, succeeded=1, failed=1)
    assert b.all_ok is False


def test_batch_empty():
    b = BatchLaunchResult()
    assert b.total == 0
    assert b.all_ok is True


# ---------------------------------------------------------------------------
# ParallelLauncher — basic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_launch_empty_list():
    launcher = ParallelLauncher()
    result = await launcher.launch([], _ok_start)
    assert result.total == 0
    assert result.succeeded == 0


@pytest.mark.asyncio
async def test_launch_single_market():
    launcher = ParallelLauncher()
    result = await launcher.launch([_market("0x1")], _ok_start)
    assert result.total == 1
    assert result.succeeded == 1
    assert result.failed == 0
    assert result.all_ok


@pytest.mark.asyncio
async def test_launch_multiple_markets():
    markets = [_market(f"0x{i}") for i in range(4)]
    launcher = ParallelLauncher(max_concurrency=4)
    result = await launcher.launch(markets, _ok_start)
    assert result.total == 4
    assert result.succeeded == 4
    assert result.all_ok


@pytest.mark.asyncio
async def test_launch_with_failure():
    launcher = ParallelLauncher()
    result = await launcher.launch([_market("0xfail")], _fail_start)
    assert result.total == 1
    assert result.failed == 1
    assert not result.all_ok
    assert "boom" in result.results[0].error


@pytest.mark.asyncio
async def test_launch_mixed_success_and_failure():
    call_count = 0

    async def _alternating(market: dict) -> None:
        nonlocal call_count
        call_count += 1
        if market["condition_id"] == "0xbad":
            raise ValueError("bad market")

    markets = [_market("0xgood"), _market("0xbad"), _market("0xok")]
    launcher = ParallelLauncher(max_concurrency=3)
    result = await launcher.launch(markets, _alternating)
    assert result.total == 3
    assert result.succeeded == 2
    assert result.failed == 1


# ---------------------------------------------------------------------------
# Concurrency / semaphore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_semaphore_limits_concurrency():
    """Verify that max_concurrency limits parallel execution."""
    running = 0
    max_running = 0

    async def _track(market: dict) -> None:
        nonlocal running, max_running
        running += 1
        max_running = max(max_running, running)
        await asyncio.sleep(0.05)
        running -= 1

    launcher = ParallelLauncher(max_concurrency=2)
    markets = [_market(f"0x{i}") for i in range(6)]
    result = await launcher.launch(markets, _track)
    assert result.succeeded == 6
    assert max_running <= 2


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_launch_timeout():
    launcher = ParallelLauncher(timeout=0.05)
    result = await launcher.launch([_market("0xslow")], _slow_start)
    assert result.failed == 1
    assert "Timeout" in result.results[0].error


@pytest.mark.asyncio
async def test_launch_no_timeout():
    """With timeout=None, fast tasks complete normally."""
    launcher = ParallelLauncher(timeout=None)
    result = await launcher.launch([_market("0x1")], _ok_start)
    assert result.succeeded == 1


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_invalid_max_concurrency():
    with pytest.raises(ValueError, match="max_concurrency"):
        ParallelLauncher(max_concurrency=0)


# ---------------------------------------------------------------------------
# Elapsed timing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_elapsed_ms_is_positive():
    launcher = ParallelLauncher()
    result = await launcher.launch([_market("0x1")], _ok_start)
    assert result.elapsed_ms > 0
    assert result.results[0].elapsed_ms > 0


# ---------------------------------------------------------------------------
# Parallel is actually faster than sequential
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_faster_than_sequential():
    """4 × 50ms tasks with concurrency=4 should take ~50ms, not ~200ms."""

    async def _sleep50(market: dict) -> None:
        await asyncio.sleep(0.05)

    launcher = ParallelLauncher(max_concurrency=4)
    markets = [_market(f"0x{i}") for i in range(4)]
    result = await launcher.launch(markets, _sleep50)
    # Should be well under 200ms (sequential would be ~200ms)
    assert result.elapsed_ms < 150
    assert result.succeeded == 4
