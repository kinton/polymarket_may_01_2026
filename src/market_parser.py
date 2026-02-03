"""
Utilities for parsing WebSocket market data and determining trade logic.
"""


def extract_best_ask_from_book(asks: list) -> float | None:
    """Extract minimum ask price from orderbook asks array."""
    if not asks:
        return None
    try:
        return min(
            float(a["price"]) if isinstance(a, dict) else float(a[0]) for a in asks
        )
    except (ValueError, IndexError, KeyError):
        return None


def extract_best_bid_from_book(bids: list) -> float | None:
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
) -> tuple[float | None, float | None]:
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
    best_bid_yes: float | None,
    best_bid_no: float | None,
    best_ask_yes: float | None = None,
    best_ask_no: float | None = None,
    tie_epsilon: float = 1e-6,
) -> str | None:
    """
    Determine winning side based on BID prices (who is willing to pay more).

    The winning side is the one with higher bid price, meaning the market
    believes that outcome is more likely.

    Args:
        best_bid_yes: Best bid for YES token (buyers willing to pay)
        best_bid_no: Best bid for NO token (buyers willing to pay)
        best_ask_yes: Best ask for YES (optional, for fallback)
        best_ask_no: Best ask for NO (optional, for fallback)
        tie_epsilon: Threshold for treating bids as tied

    Returns:
        "YES" if YES wins, "NO" if NO wins, None if tie or insufficient data
    """
    # Try to use bids first (most reliable indicator of market sentiment)
    if best_bid_yes is not None and best_bid_no is not None:
        if abs(best_bid_yes - best_bid_no) < tie_epsilon:
            return None  # Tie
        return "YES" if best_bid_yes > best_bid_no else "NO"

    # Fallback: derive from asks (1 - opposite_ask = implied_bid)
    # If NO ask = 0.99, implied YES bid = 0.01, so NO wins
    if best_ask_yes is not None and best_ask_no is not None:
        if abs(best_ask_yes - best_ask_no) < tie_epsilon:
            return None  # Tie
        # Higher ask means that side is winning (more valuable)
        return "YES" if best_ask_yes > best_ask_no else "NO"

    # Single-side fallback: if only one side has data
    if best_bid_yes is not None and best_bid_no is None:
        # Only YES has bids - check if it's high enough to indicate winner
        if best_bid_yes > 0.5:
            return "YES"
    if best_bid_no is not None and best_bid_yes is None:
        if best_bid_no > 0.5:
            return "NO"

    # Ask-based single-side: high ask means that side is winning
    if best_ask_yes is not None and best_ask_no is None:
        if best_ask_yes > 0.5:
            return "YES"
    if best_ask_no is not None and best_ask_yes is None:
        if best_ask_no > 0.5:
            return "NO"

    return None


def get_winning_token_id(
    winning_side: str | None,
    token_id_yes: str,
    token_id_no: str,
) -> str | None:
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
