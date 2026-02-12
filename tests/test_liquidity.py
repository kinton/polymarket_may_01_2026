"""Tests for orderbook liquidity filtering."""

from datetime import datetime, timezone

import pytest

from src.clob_types import MIN_ORDERBOOK_SIZE_USD, OrderBook
from src.hft_trader import LastSecondTrader


# Common end_time for tests
END_TIME = datetime(2026, 2, 12, 12, 0, 0, tzinfo=timezone.utc)


class TestLiquidityFiltering:
    """Test orderbook liquidity filtering functionality."""

    def test_check_orderbook_liquidity_sufficient(self):
        """Test that orderbook with sufficient liquidity passes the check."""
        # Create an OrderBook with > $100 total liquidity
        book = OrderBook(
            best_bid_yes=0.90,
            best_bid_yes_size=50.0,  # $50
            best_ask_yes=0.91,
            best_ask_yes_size=50.0,  # $50
            best_bid_no=0.10,
            best_bid_no_size=50.0,  # $50
            best_ask_no=0.11,
            best_ask_no_size=50.0,  # $50
        )

        # Total liquidity = 50 + 50 + 50 + 50 = $200 > $100
        trader = LastSecondTrader(
            condition_id="test", token_id_yes="yes", token_id_no="no", end_time=END_TIME, dry_run=True
        )
        trader.orderbook = book

        assert trader.check_orderbook_liquidity() is True

    def test_check_orderbook_liquidity_insufficient(self):
        """Test that orderbook with insufficient liquidity fails the check."""
        # Create an OrderBook with < $100 total liquidity
        book = OrderBook(
            best_bid_yes=0.90,
            best_bid_yes_size=20.0,  # $20
            best_ask_yes=0.91,
            best_ask_yes_size=20.0,  # $20
            best_bid_no=0.10,
            best_bid_no_size=20.0,  # $20
            best_ask_no=0.11,
            best_ask_no_size=20.0,  # $20
        )

        # Total liquidity = 20 + 20 + 20 + 20 = $80 < $100
        trader = LastSecondTrader(
            condition_id="test", token_id_yes="yes", token_id_no="no", end_time=END_TIME, dry_run=True
        )
        trader.orderbook = book

        assert trader.check_orderbook_liquidity() is False

    def test_check_orderbook_liquidity_exactly_at_threshold(self):
        """Test that orderbook with exactly $100 liquidity passes."""
        # Create an OrderBook with exactly $100 total liquidity
        book = OrderBook(
            best_bid_yes=0.90,
            best_bid_yes_size=25.0,  # $25
            best_ask_yes=0.91,
            best_ask_yes_size=25.0,  # $25
            best_bid_no=0.10,
            best_bid_no_size=25.0,  # $25
            best_ask_no=0.11,
            best_ask_no_size=25.0,  # $25
        )

        # Total liquidity = 25 + 25 + 25 + 25 = $100 = threshold
        trader = LastSecondTrader(
            condition_id="test", token_id_yes="yes", token_id_no="no", end_time=END_TIME, dry_run=True
        )
        trader.orderbook = book

        assert trader.check_orderbook_liquidity() is True

    def test_check_orderbook_liquidity_partial_data(self):
        """Test handling of orderbook with partial data (some sizes are None)."""
        # Create an OrderBook with only some sizes populated
        book = OrderBook(
            best_bid_yes=0.90,
            best_bid_yes_size=60.0,  # $60
            best_ask_yes=0.91,
            best_ask_yes_size=None,  # No data
            best_bid_no=0.10,
            best_bid_no_size=60.0,  # $60
            best_ask_no=0.11,
            best_ask_no_size=None,  # No data
        )

        # Total liquidity = 60 + 60 = $120 > $100
        trader = LastSecondTrader(
            condition_id="test", token_id_yes="yes", token_id_no="no", end_time=END_TIME, dry_run=True
        )
        trader.orderbook = book

        assert trader.check_orderbook_liquidity() is True

    def test_check_orderbook_liquidity_empty_orderbook(self):
        """Test handling of empty orderbook (all sizes are None)."""
        # Create an empty OrderBook
        book = OrderBook()

        # Total liquidity = 0 < $100, but since there's no data, allow trade
        trader = LastSecondTrader(
            condition_id="test", token_id_yes="yes", token_id_no="no", end_time=END_TIME, dry_run=True
        )
        trader.orderbook = book

        # Empty orderbook should return True (allows trade since data may arrive later)
        assert trader.check_orderbook_liquidity() is True

    def test_check_orderbook_liquidity_single_side_sufficient(self):
        """Test that high liquidity on one side can pass the check."""
        # Create an OrderBook with high liquidity on YES side only
        book = OrderBook(
            best_bid_yes=0.90,
            best_bid_yes_size=150.0,  # $150 > $100 threshold
            best_ask_yes=0.91,
            best_ask_yes_size=None,
            best_bid_no=0.10,
            best_bid_no_size=None,
            best_ask_no=0.11,
            best_ask_no_size=None,
        )

        # Total liquidity = 150 > $100
        trader = LastSecondTrader(
            condition_id="test", token_id_yes="yes", token_id_no="no", end_time=END_TIME, dry_run=True
        )
        trader.orderbook = book

        assert trader.check_orderbook_liquidity() is True

    def test_min_orderbook_size_constant(self):
        """Test that MIN_ORDERBOOK_SIZE_USD is correctly defined."""
        assert MIN_ORDERBOOK_SIZE_USD == 100.0
