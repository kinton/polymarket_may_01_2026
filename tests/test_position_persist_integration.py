"""
Integration tests for PositionPersister activation in hft_trader.py.

Tests crash recovery: open position → persist → new trader → restore.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta

import pytest

from src.hft_trader import LastSecondTrader
from src.trading.position_manager import PositionManager
from src.trading.position_persist import PositionPersister


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trader(
    condition_id: str = "0xabc123",
    persist_dir: str | None = None,
    **kwargs,
) -> LastSecondTrader:
    """Create a minimal dry-run trader for testing.
    
    Uses POSITION_PERSIST_DIR env var to control persist directory
    so that restore() in __init__ reads from the right place.
    """
    old_env = os.environ.get("POSITION_PERSIST_DIR")
    if persist_dir:
        os.environ["POSITION_PERSIST_DIR"] = persist_dir
    try:
        end_time = datetime.now(timezone.utc) + timedelta(minutes=5)
        trader = LastSecondTrader(
            condition_id=condition_id,
            token_id_yes="tok_yes",
            token_id_no="tok_no",
            end_time=end_time,
            dry_run=True,
            trade_size=1.0,
            title="Bitcoin test",
            **kwargs,
        )
    finally:
        if old_env is None:
            os.environ.pop("POSITION_PERSIST_DIR", None)
        else:
            os.environ["POSITION_PERSIST_DIR"] = old_env
    return trader


# ---------------------------------------------------------------------------
# Tests: PositionManager gets persister via condition_id
# ---------------------------------------------------------------------------

class TestPositionPersistActivation:
    """Test persistence is disabled for dry-run traders and enabled for live."""

    def test_dry_run_trader_has_no_persister(self):
        """Dry-run trader passes condition_id=None to PositionManager — no disk I/O."""
        trader = _make_trader()
        assert trader.position_manager._persister is None

    def test_position_manager_without_condition_id_has_no_persister(self):
        """PositionManager without condition_id has no persister."""
        pm = PositionManager(logger=None)
        assert pm._persister is None

    def test_position_manager_with_condition_id_has_persister(self, tmp_path):
        """PositionManager with condition_id creates a persister."""
        pm = PositionManager(logger=None, condition_id="0xabc123", persist_dir=str(tmp_path))
        assert pm._persister is not None
        assert pm._persister.condition_id == "0xabc123"


# ---------------------------------------------------------------------------
# Tests: Crash recovery cycle
# ---------------------------------------------------------------------------

class TestCrashRecovery:
    """Test open → persist → restore cycle via PositionManager (live-mode behavior)."""

    def _make_pm(self, condition_id: str, persist_dir: str) -> PositionManager:
        """Create a live-mode PositionManager with persistence."""
        return PositionManager(logger=None, condition_id=condition_id, persist_dir=persist_dir)

    def test_open_persist_restore(self, tmp_path):
        """Position opened in one manager can be restored in a new one."""
        condition_id = "0xcrash_test"

        pm1 = self._make_pm(condition_id, str(tmp_path))
        pm1.open_position(entry_price=0.95, side="YES", trailing_stop_price=0.90)

        files = list(tmp_path.glob("position_*.json"))
        assert len(files) == 1

        pm2 = self._make_pm(condition_id, str(tmp_path))
        pm2.restore()
        assert pm2.is_open is True
        assert pm2.position_side == "YES"
        assert pm2.entry_price == 0.95
        assert pm2.trailing_stop_price == 0.90

    def test_close_removes_persist_file(self, tmp_path):
        """Closing a position removes the persist file."""
        condition_id = "0xclose_test"

        pm1 = self._make_pm(condition_id, str(tmp_path))
        pm1.open_position(entry_price=0.92, side="NO", trailing_stop_price=0.88)
        assert list(tmp_path.glob("position_*.json"))

        pm1.close_position()
        assert not list(tmp_path.glob("position_*.json"))

        pm2 = self._make_pm(condition_id, str(tmp_path))
        pm2.restore()
        assert pm2.is_open is False

    def test_trailing_stop_update_persisted(self, tmp_path):
        """Trailing stop updates are persisted to disk."""
        condition_id = "0xtrailing"

        pm1 = self._make_pm(condition_id, str(tmp_path))
        pm1.open_position(entry_price=0.94, side="YES", trailing_stop_price=0.89)
        pm1.update_trailing_stop(0.91)

        files = list(tmp_path.glob("position_*.json"))
        data = json.loads(files[0].read_text())
        assert data["trailing_stop_price"] == 0.91

        pm2 = self._make_pm(condition_id, str(tmp_path))
        pm2.restore()
        assert pm2.trailing_stop_price == 0.91


# ---------------------------------------------------------------------------
# Tests: Graceful shutdown persists
# ---------------------------------------------------------------------------

class TestGracefulShutdownPersist:
    """Test graceful_shutdown persistence behavior for dry-run vs live."""

    @pytest.mark.asyncio
    async def test_dry_run_shutdown_never_writes_files(self, tmp_path):
        """Dry-run trader graceful_shutdown writes no position files."""
        trader = _make_trader(condition_id="0xshutdown", persist_dir=str(tmp_path))
        trader.position_manager.open_position(
            entry_price=0.96,
            side="YES",
            trailing_stop_price=0.91,
        )
        await trader.graceful_shutdown(reason="test")
        assert not list(tmp_path.glob("position_*.json"))

    @pytest.mark.asyncio
    async def test_shutdown_no_persist_when_no_position(self, tmp_path):
        """Graceful shutdown with no open position doesn't create files."""
        trader = _make_trader(condition_id="0xempty", persist_dir=str(tmp_path))
        await trader.graceful_shutdown(reason="test")
        assert not list(tmp_path.glob("position_*.json"))


# ---------------------------------------------------------------------------
# Tests: PositionPersister unit (supplement existing tests)
# ---------------------------------------------------------------------------

class TestPersisterAtomicWrite:
    """Supplementary tests for atomic write behavior."""

    def test_no_tmp_file_left_after_save(self, tmp_path):
        """Atomic write should not leave .tmp files."""
        p = PositionPersister(condition_id="0xatomic", persist_dir=str(tmp_path))
        p.save({"entry_price": 0.95, "position_side": "YES", "position_open": True})
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_load_returns_none_for_missing(self, tmp_path):
        """Load returns None when no file exists."""
        p = PositionPersister(condition_id="0xmissing", persist_dir=str(tmp_path))
        assert p.load() is None

    def test_remove_idempotent(self, tmp_path):
        """Remove on non-existent file is a no-op."""
        p = PositionPersister(condition_id="0xnoop", persist_dir=str(tmp_path))
        p.remove()  # Should not raise
