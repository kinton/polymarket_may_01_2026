"""Tests for position persistence and crash recovery."""

import os
import tempfile

import pytest

from src.trading.position_manager import PositionManager
from src.trading.position_persist import PositionPersister


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


class TestPositionPersister:
    def test_save_and_load(self, tmp_dir):
        p = PositionPersister("cond-123", persist_dir=tmp_dir)
        state = {"entry_price": 0.95, "position_side": "YES", "position_open": True}
        p.save(state)
        loaded = p.load()
        assert loaded is not None
        assert loaded["entry_price"] == 0.95
        assert loaded["position_side"] == "YES"
        assert loaded["condition_id"] == "cond-123"

    def test_load_no_file(self, tmp_dir):
        p = PositionPersister("cond-999", persist_dir=tmp_dir)
        assert p.load() is None

    def test_remove(self, tmp_dir):
        p = PositionPersister("cond-123", persist_dir=tmp_dir)
        p.save({"test": True})
        assert p.exists()
        p.remove()
        assert not p.exists()

    def test_exists(self, tmp_dir):
        p = PositionPersister("cond-123", persist_dir=tmp_dir)
        assert not p.exists()
        p.save({"test": True})
        assert p.exists()

    def test_atomic_write(self, tmp_dir):
        """Ensure no .tmp file left after save."""
        p = PositionPersister("cond-123", persist_dir=tmp_dir)
        p.save({"test": True})
        files = os.listdir(tmp_dir)
        assert not any(f.endswith(".tmp") for f in files)

    def test_corrupted_file(self, tmp_dir):
        p = PositionPersister("cond-123", persist_dir=tmp_dir)
        p._filepath.write_text("not json{{{")
        assert p.load() is None

    def test_special_chars_in_condition_id(self, tmp_dir):
        p = PositionPersister("cond/with\\slashes", persist_dir=tmp_dir)
        p.save({"test": True})
        assert p.exists()
        assert p.load()["test"] is True

    def test_env_var_persist_dir(self, tmp_dir, monkeypatch):
        monkeypatch.setenv("POSITION_PERSIST_DIR", tmp_dir)
        p = PositionPersister("cond-env")
        p.save({"from_env": True})
        assert p.load()["from_env"] is True


class TestPositionManagerPersistence:
    def test_open_persists(self, tmp_dir):
        pm = PositionManager(condition_id="c1", persist_dir=tmp_dir)
        pm.open_position(0.95, "YES", 0.90)
        # Check file exists
        persister = PositionPersister("c1", persist_dir=tmp_dir)
        data = persister.load()
        assert data["entry_price"] == 0.95
        assert data["position_side"] == "YES"
        assert data["position_open"] is True

    def test_close_removes(self, tmp_dir):
        pm = PositionManager(condition_id="c2", persist_dir=tmp_dir)
        pm.open_position(0.95, "YES", 0.90)
        pm.close_position()
        persister = PositionPersister("c2", persist_dir=tmp_dir)
        assert not persister.exists()

    def test_trailing_stop_persists(self, tmp_dir):
        pm = PositionManager(condition_id="c3", persist_dir=tmp_dir)
        pm.open_position(0.95, "YES", 0.90)
        pm.update_trailing_stop(0.93)
        persister = PositionPersister("c3", persist_dir=tmp_dir)
        data = persister.load()
        assert data["trailing_stop_price"] == 0.93

    def test_restore(self, tmp_dir):
        # Save state
        pm1 = PositionManager(condition_id="c4", persist_dir=tmp_dir)
        pm1.open_position(0.96, "NO", 0.91)

        # New manager restores
        pm2 = PositionManager(condition_id="c4", persist_dir=tmp_dir)
        assert not pm2.is_open
        restored = pm2.restore()
        assert restored is True
        assert pm2.is_open
        assert pm2.entry_price == 0.96
        assert pm2.position_side == "NO"
        assert pm2.trailing_stop_price == 0.91

    def test_restore_no_state(self, tmp_dir):
        pm = PositionManager(condition_id="c5", persist_dir=tmp_dir)
        assert pm.restore() is False

    def test_no_persistence_without_condition_id(self, tmp_dir):
        """Without condition_id, persistence is disabled (backward compatible)."""
        pm = PositionManager()
        pm.open_position(0.95, "YES", 0.90)
        pm.close_position()
        # No crash, just works without persistence

    def test_to_dict(self):
        pm = PositionManager()
        pm.open_position(0.95, "YES", 0.90)
        d = pm.to_dict()
        assert d == {
            "entry_price": 0.95,
            "position_side": "YES",
            "position_open": True,
            "trailing_stop_price": 0.90,
        }
