"""Tests for the strategy plugin system (registry, loader, discovery)."""

import pytest

from strategies import (
    STRATEGY_REGISTRY,
    discover_strategies,
    load_strategy,
    register,
)
from strategies.base import BaseStrategy, MarketInfo, MarketTick, Signal
from src.clob_types import OrderBook


# -- helpers ------------------------------------------------------------------

class _DummyStrategy(BaseStrategy):
    """Minimal concrete strategy for testing."""

    name = "dummy"
    version = "v1"

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._observed = False

    def market_filter(self, market: MarketInfo) -> bool:
        return market.ticker.upper() in ("BTC", "ETH")

    def observe(self, tick: MarketTick) -> None:
        self._observed = True

    def decide(self, tick: MarketTick) -> Signal | None:
        if self._observed:
            return Signal(side="YES", price=0.10)
        return None

    def reset(self) -> None:
        self._observed = False


# -- tests --------------------------------------------------------------------

class TestRegister:
    def setup_method(self):
        # Clean up registry before each test
        STRATEGY_REGISTRY.pop("dummy/v1", None)

    def teardown_method(self):
        STRATEGY_REGISTRY.pop("dummy/v1", None)

    def test_register_adds_to_registry(self):
        register(_DummyStrategy)
        assert "dummy/v1" in STRATEGY_REGISTRY
        assert STRATEGY_REGISTRY["dummy/v1"] is _DummyStrategy

    def test_register_duplicate_same_class_ok(self):
        register(_DummyStrategy)
        register(_DummyStrategy)  # same class, no error
        assert STRATEGY_REGISTRY["dummy/v1"] is _DummyStrategy

    def test_register_duplicate_different_class_raises(self):
        register(_DummyStrategy)

        class AnotherDummy(BaseStrategy):
            name = "dummy"
            version = "v1"
            def observe(self, tick): ...
            def decide(self, tick): return None
            def reset(self): ...

        with pytest.raises(ValueError, match="Duplicate strategy key"):
            register(AnotherDummy)

    def test_register_missing_name_raises(self):
        class NoName(BaseStrategy):
            name = ""
            version = "v1"
            def observe(self, tick): ...
            def decide(self, tick): return None
            def reset(self): ...

        with pytest.raises(ValueError, match="non-empty"):
            register(NoName)


class TestLoadStrategy:
    def setup_method(self):
        STRATEGY_REGISTRY.pop("dummy/v1", None)
        register(_DummyStrategy)

    def teardown_method(self):
        STRATEGY_REGISTRY.pop("dummy/v1", None)

    def test_load_returns_instance(self):
        s = load_strategy("dummy", "v1")
        assert isinstance(s, _DummyStrategy)

    def test_load_forwards_kwargs(self):
        s = load_strategy("dummy", "v1", foo="bar", baz=42)
        assert s.kwargs == {"foo": "bar", "baz": 42}

    def test_load_not_found_raises(self):
        with pytest.raises(KeyError, match="not found"):
            load_strategy("nonexistent", "v99")


class TestDiscoverStrategies:
    def test_discover_finds_convergence(self):
        # convergence_v1.py is in strategies/ — discover should find it
        n = discover_strategies()
        assert "convergence/v1" in STRATEGY_REGISTRY

    def test_discover_returns_count(self):
        # After clearing + removing cached module, re-discover finds strategies
        import sys
        STRATEGY_REGISTRY.clear()
        sys.modules.pop("strategies.convergence_v1", None)
        n = discover_strategies()
        assert n >= 1  # at least convergence_v1


class TestBaseStrategyABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            BaseStrategy()

    def test_get_signal_calls_observe_and_decide(self):
        STRATEGY_REGISTRY.pop("dummy/v1", None)
        register(_DummyStrategy)
        s = load_strategy("dummy", "v1")
        ob = OrderBook(best_ask_yes=0.10, best_ask_no=0.90)
        tick = MarketTick(time_remaining=100.0, oracle_snapshot=None, orderbook=ob)
        sig = s.get_signal(tick)
        assert sig is not None
        assert sig.side == "YES"
        assert sig.price == 0.10
        STRATEGY_REGISTRY.pop("dummy/v1", None)


class TestMarketTickAndSignal:
    def test_market_tick_frozen(self):
        ob = OrderBook()
        tick = MarketTick(time_remaining=50.0, oracle_snapshot=None, orderbook=ob)
        with pytest.raises(AttributeError):
            tick.time_remaining = 10.0

    def test_signal_frozen(self):
        sig = Signal(side="YES", price=0.15)
        with pytest.raises(AttributeError):
            sig.side = "NO"

    def test_signal_metadata_default(self):
        sig = Signal(side="YES", price=0.15)
        assert sig.metadata == {}

    def test_signal_with_metadata(self):
        sig = Signal(side="NO", price=0.20, metadata={"reason": "convergence"})
        assert sig.metadata["reason"] == "convergence"


class TestMarketInfo:
    def test_market_info_frozen(self):
        mi = MarketInfo(
            condition_id="abc", ticker="BTC", title="Test",
            end_time_utc="2026-03-14 18:30:00 UTC",
            minutes_until_end=5.0,
            token_id_yes="tok_yes", token_id_no="tok_no",
        )
        with pytest.raises(AttributeError):
            mi.ticker = "ETH"

    def test_market_info_fields(self):
        mi = MarketInfo(
            condition_id="abc", ticker="BTC", title="Test",
            end_time_utc="2026-03-14 18:30:00 UTC",
            minutes_until_end=5.0,
            token_id_yes="tok_yes", token_id_no="tok_no",
        )
        assert mi.condition_id == "abc"
        assert mi.ticker == "BTC"


class TestMarketFilter:
    def setup_method(self):
        STRATEGY_REGISTRY.pop("dummy/v1", None)
        register(_DummyStrategy)

    def teardown_method(self):
        STRATEGY_REGISTRY.pop("dummy/v1", None)

    def test_market_filter_accepts(self):
        s = load_strategy("dummy", "v1")
        mi = MarketInfo(
            condition_id="c1", ticker="BTC", title="BTC Up or Down",
            end_time_utc="2026-03-14 18:30:00 UTC",
            minutes_until_end=5.0,
            token_id_yes="ty", token_id_no="tn",
        )
        assert s.market_filter(mi) is True

    def test_market_filter_rejects(self):
        s = load_strategy("dummy", "v1")
        mi = MarketInfo(
            condition_id="c2", ticker="SOL", title="SOL Up or Down",
            end_time_utc="2026-03-14 18:30:00 UTC",
            minutes_until_end=5.0,
            token_id_yes="ty", token_id_no="tn",
        )
        assert s.market_filter(mi) is False
