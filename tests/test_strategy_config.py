"""Tests for config-driven strategy loading (main.py config layer)."""

import os
import pytest
import tempfile
from pathlib import Path

from main import StrategyConfig, load_strategies_config


class TestStrategyConfig:
    def test_defaults(self):
        sc = StrategyConfig()
        assert sc.name == "convergence"
        assert sc.version == "v1"
        assert sc.mode == "test"
        assert sc.dry_run is True
        assert sc.size == 1.0
        assert sc.poll_interval == 90
        assert sc.max_traders == 1
        assert sc.universe == ["BTC", "ETH", "SOL"]

    def test_dry_run_live_mode(self):
        sc = StrategyConfig(mode="live")
        assert sc.dry_run is False

    def test_db_path(self):
        sc = StrategyConfig(name="convergence", version="v2", mode="live")
        assert sc.db_path == "data/convergence-v2-live.db"

    def test_db_path_test_mode(self):
        sc = StrategyConfig(name="convergence", version="v1", mode="test")
        assert sc.db_path == "data/convergence-v1-test.db"


class TestLoadStrategiesConfig:
    def _write_yaml(self, content: str) -> str:
        """Write YAML content to a temp file and return the path."""
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(content)
        f.close()
        return f.name

    def test_load_single_strategy(self):
        path = self._write_yaml("""
strategies:
  - name: convergence
    version: v1
    mode: test
    size: 2.0
    poll_interval: 60
    max_traders: 5
    universe: [BTC, ETH]
""")
        try:
            configs = load_strategies_config(path)
            assert len(configs) == 1
            sc = configs[0]
            assert sc.name == "convergence"
            assert sc.version == "v1"
            assert sc.mode == "test"
            assert sc.size == 2.0
            assert sc.poll_interval == 60
            assert sc.max_traders == 5
            assert sc.universe == ["BTC", "ETH"]
            assert sc.dry_run is True
            assert sc.db_path == "data/convergence-v1-test.db"
        finally:
            os.unlink(path)

    def test_load_multiple_strategies(self):
        path = self._write_yaml("""
strategies:
  - name: convergence
    version: v1
    mode: test
    size: 1.0

  - name: convergence
    version: v2
    mode: live
    size: 5.0
    universe: [BTC, ETH]
""")
        try:
            configs = load_strategies_config(path)
            assert len(configs) == 2
            assert configs[0].version == "v1"
            assert configs[0].dry_run is True
            assert configs[1].version == "v2"
            assert configs[1].dry_run is False
            assert configs[1].size == 5.0
            assert configs[1].universe == ["BTC", "ETH"]
        finally:
            os.unlink(path)

    def test_universe_string_format(self):
        path = self._write_yaml("""
strategies:
  - name: convergence
    version: v1
    universe: "BTC,ETH,SOL"
""")
        try:
            configs = load_strategies_config(path)
            assert configs[0].universe == ["BTC", "ETH", "SOL"]
        finally:
            os.unlink(path)

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_strategies_config("/nonexistent/path.yaml")

    def test_missing_strategies_key_raises(self):
        path = self._write_yaml("foo: bar\n")
        try:
            with pytest.raises(ValueError, match="strategies"):
                load_strategies_config(path)
        finally:
            os.unlink(path)

    def test_empty_strategies_raises(self):
        path = self._write_yaml("strategies: []\n")
        try:
            with pytest.raises(ValueError, match="non-empty"):
                load_strategies_config(path)
        finally:
            os.unlink(path)

    def test_defaults_applied(self):
        path = self._write_yaml("""
strategies:
  - name: convergence
    version: v1
""")
        try:
            configs = load_strategies_config(path)
            sc = configs[0]
            assert sc.mode == "test"
            assert sc.size == 1.0
            assert sc.poll_interval == 90
            assert sc.max_traders == 1
            assert sc.universe == ["BTC", "ETH", "SOL"]
        finally:
            os.unlink(path)

    def test_actual_config_file_loads(self):
        """Verify the real config/strategies.yaml loads correctly."""
        config_path = Path(__file__).parent.parent / "config" / "strategies.yaml"
        if not config_path.exists():
            pytest.skip("config/strategies.yaml not found")
        configs = load_strategies_config(str(config_path))
        assert len(configs) >= 1
        for sc in configs:
            assert sc.name
            assert sc.version
            assert sc.mode in ("test", "live")
            assert sc.size > 0
