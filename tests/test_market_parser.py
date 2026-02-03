"""
Unit tests for market_parser utilities.
"""

from src.market_parser import (
    determine_winning_side,
    extract_best_ask_from_book,
    extract_best_bid_from_book,
    get_winning_token_id,
)


def test_extract_best_ask_from_book_dicts():
    asks = [{"price": "0.60"}, {"price": "0.58"}, {"price": "0.62"}]
    assert extract_best_ask_from_book(asks) == 0.58


def test_extract_best_ask_from_book_lists():
    asks = [["0.61", "100"], ["0.59", "50"]]
    assert extract_best_ask_from_book(asks) == 0.59


def test_extract_best_bid_from_book_dicts():
    bids = [{"price": "0.41"}, {"price": "0.43"}, {"price": "0.40"}]
    assert extract_best_bid_from_book(bids) == 0.43


def test_extract_best_bid_from_book_lists():
    bids = [["0.44", "100"], ["0.42", "50"]]
    assert extract_best_bid_from_book(bids) == 0.44


def test_determine_winning_side_from_bids():
    assert determine_winning_side(0.55, 0.45) == "YES"
    assert determine_winning_side(0.45, 0.55) == "NO"


def test_determine_winning_side_tie():
    assert determine_winning_side(0.50, 0.50, tie_epsilon=1e-6) is None


def test_determine_winning_side_from_asks_fallback():
    assert determine_winning_side(None, None, best_ask_yes=0.60, best_ask_no=0.40) == "YES"
    assert determine_winning_side(None, None, best_ask_yes=0.40, best_ask_no=0.60) == "NO"


def test_determine_winning_side_single_side():
    assert determine_winning_side(0.51, None) == "YES"
    assert determine_winning_side(None, 0.52) == "NO"
    assert determine_winning_side(0.49, None) is None
    assert determine_winning_side(None, 0.49) is None


def test_get_winning_token_id():
    assert get_winning_token_id("YES", "yes_id", "no_id") == "yes_id"
    assert get_winning_token_id("NO", "yes_id", "no_id") == "no_id"
    assert get_winning_token_id(None, "yes_id", "no_id") is None
