"""Tests for market_parser module (parsing utilities and business logic)."""

import pytest
from market_parser import (
    extract_best_ask_from_book,
    extract_best_bid_from_book,
    extract_prices_from_price_change,
    determine_winning_side,
    get_winning_token_id,
    validate_price_sum,
)


class TestExtractBestAsk:
    """Test extraction of best ask price from orderbook."""

    def test_extract_best_ask_normal(self):
        """Test extracting best ask (lowest ask price)."""
        asks = [
            {"price": "0.99", "size": "100"},
            {"price": "0.98", "size": "50"},
            {"price": "0.95", "size": "20"},
        ]
        result = extract_best_ask_from_book(asks)
        assert abs(result - 0.95) < 1e-9

    def test_extract_best_ask_single(self):
        """Test with single ask."""
        asks = [{"price": "0.88", "size": "100"}]
        result = extract_best_ask_from_book(asks)
        assert abs(result - 0.88) < 1e-9

    def test_extract_best_ask_empty(self):
        """Test with empty asks list."""
        result = extract_best_ask_from_book([])
        assert result is None

    def test_extract_best_ask_string_conversion(self):
        """Test that string prices are correctly converted to float."""
        asks = [{"price": "0.750", "size": "10"}]
        result = extract_best_ask_from_book(asks)
        assert abs(result - 0.75) < 1e-9


class TestExtractBestBid:
    """Test extraction of best bid price from orderbook."""

    def test_extract_best_bid_normal(self):
        """Test extracting best bid (highest bid price)."""
        bids = [
            {"price": "0.10", "size": "10"},
            {"price": "0.93", "size": "5"},
            {"price": "0.50", "size": "20"},
        ]
        result = extract_best_bid_from_book(bids)
        assert abs(result - 0.93) < 1e-9

    def test_extract_best_bid_single(self):
        """Test with single bid."""
        bids = [{"price": "0.72", "size": "50"}]
        result = extract_best_bid_from_book(bids)
        assert abs(result - 0.72) < 1e-9

    def test_extract_best_bid_empty(self):
        """Test with empty bids list."""
        result = extract_best_bid_from_book([])
        assert result is None

    def test_extract_best_bid_string_conversion(self):
        """Test that string prices are correctly converted to float."""
        bids = [{"price": "0.8750", "size": "5"}]
        result = extract_best_bid_from_book(bids)
        assert abs(result - 0.875) < 1e-9


class TestExtractPricesFromPriceChange:
    """Test extraction of prices from price change event."""

    def test_extract_prices_normal(self):
        """Test extracting prices from price_change message."""
        changes = [
            {
                "asset_id": "token_yes_123",
                "best_bid": "0.93",
                "best_ask": "0.95",
            }
        ]
        ask, bid = extract_prices_from_price_change(changes, "token_yes_123")
        assert abs(ask - 0.95) < 1e-9
        assert abs(bid - 0.93) < 1e-9

    def test_extract_prices_multiple_assets(self):
        """Test extracting from multiple assets (should find matching one)."""
        changes = [
            {"asset_id": "token_no_456", "best_bid": "0.05", "best_ask": "0.06"},
            {"asset_id": "token_yes_123", "best_bid": "0.93", "best_ask": "0.95"},
            {"asset_id": "token_other", "best_bid": "0.10", "best_ask": "0.12"},
        ]
        ask, bid = extract_prices_from_price_change(changes, "token_yes_123")
        assert abs(ask - 0.95) < 1e-9
        assert abs(bid - 0.93) < 1e-9

    def test_extract_prices_not_found(self):
        """Test when asset_id not found in changes."""
        changes = [
            {"asset_id": "token_no_456", "best_bid": "0.05", "best_ask": "0.06"},
        ]
        ask, bid = extract_prices_from_price_change(changes, "token_yes_123")
        assert ask is None
        assert bid is None

    def test_extract_prices_empty_changes(self):
        """Test with empty changes list."""
        ask, bid = extract_prices_from_price_change([], "token_yes_123")
        assert ask is None
        assert bid is None


class TestDetermineWinningSide:
    """Test winning side determination logic."""

    def test_winning_side_yes_wins(self):
        """Test YES wins when best_ask_yes > best_ask_no."""
        result = determine_winning_side(0.95, 0.06)
        assert result == "YES"

    def test_winning_side_no_wins(self):
        """Test NO wins when best_ask_no > best_ask_yes."""
        result = determine_winning_side(0.06, 0.95)
        assert result == "NO"

    def test_winning_side_tie_within_epsilon(self):
        """Test tie when difference is within epsilon."""
        eps = 1e-6
        ask_yes = 0.50
        ask_no = 0.50 + eps / 2  # Within epsilon
        result = determine_winning_side(ask_yes, ask_no, eps)
        assert result is None

    def test_winning_side_almost_tie(self):
        """Test almost tie (just outside epsilon)."""
        eps = 1e-6
        ask_yes = 0.50
        ask_no = 0.50 + eps * 2  # Outside epsilon, NO slightly higher
        result = determine_winning_side(ask_yes, ask_no, eps)
        assert result == "NO"

    def test_winning_side_with_none_values(self):
        """Test with None values."""
        result = determine_winning_side(None, 0.95)
        assert result is None
        result = determine_winning_side(0.95, None)
        assert result is None


class TestGetWinningTokenId:
    """Test token ID lookup for winning side."""

    def test_get_winning_token_id_yes(self):
        """Test getting token ID when YES wins."""
        token_id = get_winning_token_id("YES", "token_yes_123", "token_no_456")
        assert token_id == "token_yes_123"

    def test_get_winning_token_id_no(self):
        """Test getting token ID when NO wins."""
        token_id = get_winning_token_id("NO", "token_yes_123", "token_no_456")
        assert token_id == "token_no_456"

    def test_get_winning_token_id_none(self):
        """Test getting token ID when no winner (tie)."""
        token_id = get_winning_token_id(None, "token_yes_123", "token_no_456")
        assert token_id is None


class TestValidatePriceSum:
    """Test price sum validation (YES + NO should sum to ~$1.00)."""

    def test_validate_price_sum_valid(self):
        """Test valid price sum (should be close to 1.00)."""
        result = validate_price_sum(0.95, 0.06)
        assert result is True

    def test_validate_price_sum_invalid_too_high(self):
        """Test invalid price sum (too high)."""
        result = validate_price_sum(0.99, 0.50)  # Sums to 1.49
        assert result is False

    def test_validate_price_sum_invalid_too_low(self):
        """Test invalid price sum (too low)."""
        result = validate_price_sum(0.30, 0.30)  # Sums to 0.60
        assert result is False

    def test_validate_price_sum_zero(self):
        """Test with zero values (invalid)."""
        result = validate_price_sum(0.0, 0.0)
        assert result is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
