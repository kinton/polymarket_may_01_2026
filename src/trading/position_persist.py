"""
Position persistence for crash recovery.

Saves and restores position state to/from a JSON file on disk,
so the bot can recover open positions after a crash or restart.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


DEFAULT_PERSIST_DIR = "data/positions"


class PositionPersister:
    """
    Persists position state to a JSON file for crash recovery.

    Each trader instance gets its own file keyed by condition_id.
    On open/update/close, state is written atomically (write-tmp + rename).
    On startup, state can be loaded to restore an open position.
    """

    def __init__(
        self,
        condition_id: str,
        persist_dir: str | None = None,
        logger: Any | None = None,
    ):
        self.condition_id = condition_id
        self.persist_dir = Path(
            persist_dir or os.environ.get("POSITION_PERSIST_DIR", DEFAULT_PERSIST_DIR)
        )
        self.logger = logger
        self.persist_dir.mkdir(parents=True, exist_ok=True)

    @property
    def _filepath(self) -> Path:
        """Path to the position state file for this condition."""
        safe_id = self.condition_id.replace("/", "_").replace("\\", "_")
        return self.persist_dir / f"position_{safe_id}.json"

    def save(self, state: dict[str, Any]) -> None:
        """
        Atomically save position state to disk.

        Args:
            state: Position state dict (entry_price, side, trailing_stop, etc.)
        """
        data = {
            "condition_id": self.condition_id,
            "timestamp": time.time(),
            "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **state,
        }
        tmp_path = self._filepath.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(data, indent=2) + "\n")
            tmp_path.rename(self._filepath)
            if self.logger:
                self.logger.debug(f"Position state saved: {self._filepath.name}")
        except OSError as e:
            if self.logger:
                self.logger.error(f"Failed to save position state: {e}")

    def load(self) -> dict[str, Any] | None:
        """
        Load position state from disk.

        Returns:
            Position state dict, or None if no saved state exists.
        """
        try:
            if not self._filepath.exists():
                return None
            data = json.loads(self._filepath.read_text())
            if self.logger:
                self.logger.info(
                    f"Restored position state from {self._filepath.name} "
                    f"(saved at {data.get('timestamp_iso', 'unknown')})"
                )
            return data
        except (json.JSONDecodeError, OSError) as e:
            if self.logger:
                self.logger.warning(f"Failed to load position state: {e}")
            return None

    def remove(self) -> None:
        """Remove the position state file (called on position close)."""
        try:
            if self._filepath.exists():
                self._filepath.unlink()
                if self.logger:
                    self.logger.debug(f"Position state removed: {self._filepath.name}")
        except OSError as e:
            if self.logger:
                self.logger.warning(f"Failed to remove position state: {e}")

    def exists(self) -> bool:
        """Check if a saved position state exists."""
        return self._filepath.exists()
