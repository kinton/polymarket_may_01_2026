"""
Utilities for parsing WebSocket market data and determining trade logic.
"""
from typing import Optional


def extract_best_ask_from_book(asks: list) -> Optional[float]:
    """Extract minimum ask price from orderbook asks array."""
    if not asks:
        return None
    try:
        return min(
            float(a["price"]) if isinstance(a, dict) else float(a[0]) for a in asks
        )
    except (ValueError, IndexError, KeyError):
        return None


def extract_best_bid_from_book(bids: list) -> Optional[float]:
    """Extract maximum bid price from orderbook bids array."""
    if not bids:
        return None
    try:
        return max(
            float(b["price"]) if isinstance(b, dict) else float(b[0]) for b in bids
        )
    except (ValueError, IndexError, KeyError):
        return None


def extract_prices_from_price_change(
    changes: list, expected_token_id: str
) -> tuple[Optional[float], Optional[float]]:
    """
    Extract best_ask and best_bid from price_change event's changes list.
    Returns (best_ask, best_bid) for the matching token.
    """
    if not isinstance(changes, list):
        return None, None

    for ch in changes:
        if ch.get("asset_id") != expected_token_id:
            continue

        ask = None
        bid = None

        ch_best_ask = ch.get("best_ask")
        if ch_best_ask is not None and ch_best_ask != "":
            try:
                ask = float(ch_best_ask)
            except (ValueError, TypeError):
                pass

        ch_best_bid = ch.get("best_bid")
        if ch_best_bid is not None and ch_best_bid != "":
            try:
                bid = float(ch_best_bid)
            except (ValueError, TypeError):
                pass

        return ask, bid

    return None, None


def determine_winning_side(
    best_ask_yes: Optional[float],
    best_ask_no: Optional[float],
    tie_epsilon: float = 1e-6,
) -> Optional[str]:
    """
    Determine winning side based on higher ask price.

    Args:
        best_ask_yes: Best ask for YES token
        best_ask_no: Best ask for NO token
        tie_epsilon: Threshold for treating asks as tied

    Returns:
        "YES" if YES wins, "NO" if NO wins, None if tie or insufficient data
    """
    if best_ask_yes is None or best_ask_no is None:
        return None

    if abs(best_ask_yes - best_ask_no) < tie_epsilon:
        return None  # Tie

    return "YES" if best_ask_yes > best_ask_no else "NO"


def get_winning_token_id(
    winning_side: Optional[str],
    token_id_yes: str,
    token_id_no: str,
) -> Optional[str]:
    """Get token ID for the winning side."""
    if winning_side == "YES":
        return token_id_yes
    elif winning_side == "NO":
        return token_id_no
    return None


def validate_price_sum(best_ask_yes: float, best_ask_no: float) -> bool:
    """Check if price sum is reasonable (~1.0)."""
    price_sum = best_ask_yes + best_ask_no
    return 0.95 <= price_sum <= 1.05  # Allow 5% deviation
