"""Tests for clob_types module (data structures and constants)."""

import pytest

from clob_types import BUY_PRICE, PRICE_TIE_EPS, TRIGGER_THRESHOLD, Market, OrderBook


class TestMarketDataclass:
    """Test Market dataclass structure and validation."""

    def test_market_creation(self):
        """Test creating a Market instance."""
        from datetime import datetime, timezone

        end_time = datetime(2026, 1, 26, 12, 0, 0, tzinfo=timezone.utc)
        market = Market(
            condition_id="test_condition",
            token_id_yes="token_yes_123",
            token_id_no="token_no_456",
            end_time=end_time,
            title="Test Market",
            slug="test-market",
        )
        assert market.condition_id == "test_condition"
        assert market.token_id_yes == "token_yes_123"
        assert market.token_id_no == "token_no_456"
        assert market.end_time == end_time

    def test_market_all_fields(self):
        """Test Market with all required fields."""
        from datetime import datetime, timezone

        end_time = datetime(2026, 1, 26, 12, 0, 0, tzinfo=timezone.utc)
        market = Market(
            condition_id="cond_123",
            token_id_yes="yes_id",
            token_id_no="no_id",
            end_time=end_time,
            title="Bitcoin Up or Down",
            slug="bitcoin-up-or-down",
        )
        assert market.title == "Bitcoin Up or Down"
        assert market.slug == "bitcoin-up-or-down"
        assert market.token_id_yes == "yes_id"


class TestOrderBook:
    """Test OrderBook dataclass for state management."""

    def test_orderbook_initialization(self):
        """Test OrderBook initializes with None values."""
        book = OrderBook()
        assert book.best_ask_yes is None
        assert book.best_bid_yes is None
        assert book.best_ask_no is None
        assert book.best_bid_no is None
        assert book.sum_asks is None

    def test_orderbook_update_method(self):
        """Test OrderBook.update() method."""
        book = OrderBook()
        book.best_ask_yes = 0.95
        book.best_bid_yes = 0.93
        book.best_ask_no = 0.06
        book.best_bid_no = 0.05
        book.sum_asks = 1.01

        # update() should complete without error
        book.update()

        # Values should persist
        assert book.best_ask_yes == 0.95
        assert book.best_ask_no == 0.06

    def test_orderbook_set_yes_prices(self):
        """Test setting YES token prices."""
        book = OrderBook()
        book.best_ask_yes = 0.75
        book.best_bid_yes = 0.74

        assert book.best_ask_yes == 0.75
        assert book.best_bid_yes == 0.74
        assert book.best_ask_no is None

    def test_orderbook_set_no_prices(self):
        """Test setting NO token prices."""
        book = OrderBook()
        book.best_ask_no = 0.25
        book.best_bid_no = 0.24

        assert book.best_ask_no == 0.25
        assert book.best_bid_no == 0.24
        assert book.best_ask_yes is None


class TestConstants:
    """Test module constants are correctly configured."""

    def test_buy_price(self):
        """Test BUY_PRICE constant."""
        assert BUY_PRICE == 0.99
        assert isinstance(BUY_PRICE, float)

    def test_trigger_threshold(self):
        """Test TRIGGER_THRESHOLD constant (in seconds)."""
        assert TRIGGER_THRESHOLD == 60.0
        assert isinstance(TRIGGER_THRESHOLD, float)

    def test_price_tie_epsilon(self):
        """Test PRICE_TIE_EPS for tie-breaking in winning side detection."""
        assert PRICE_TIE_EPS == 1e-6
        assert PRICE_TIE_EPS > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
