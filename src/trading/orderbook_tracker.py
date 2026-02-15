"""Orderbook tracking and analysis for Polymarket CLOB markets."""

import time
from typing import Any

from src.clob_types import (
    MIN_ORDERBOOK_SIZE_USD,
    PRICE_TIE_EPS,
    OrderBook,
)
from src.market_parser import (
    determine_winning_side,
    extract_best_ask_with_size_from_book,
    extract_best_bid_with_size_from_book,
    get_winning_token_id,
)


class OrderbookTracker:
    """Tracks orderbook state and determines winning side."""

    def __init__(
        self,
        orderbook: OrderBook,
        token_id_yes: str,
        token_id_no: str,
        tie_epsilon: float = PRICE_TIE_EPS,
    ):
        self.orderbook = orderbook
        self.token_id_yes = token_id_yes
        self.token_id_no = token_id_no
        self.tie_epsilon = tie_epsilon
        self.winning_side: str | None = None
        self.last_ws_update_ts: float = 0.0

    def process_market_update(self, data: dict[str, Any]) -> bool:
        """
        Process incoming market data and update orderbook state.

        Returns True if orderbook was updated, False otherwise.
        """
        if not data:
            return False

        if isinstance(data, list) and len(data) > 0:
            data = data[0]

        if not isinstance(data, dict):
            return False

        received_asset_id = data.get("asset_id")
        if not received_asset_id:
            return False

        is_yes_data = received_asset_id == self.token_id_yes
        is_no_data = received_asset_id == self.token_id_no

        if not is_yes_data and not is_no_data:
            return False

        event_type = data.get("event_type")

        if event_type == "book":
            self._process_book_event(data, is_yes_data)
        elif event_type == "price_change":
            self._process_price_change_event(data)
        elif event_type == "best_bid_ask":
            self._process_best_bid_ask_event(data, is_yes_data)

        self.orderbook.update()
        self.update_winning_side()
        self.last_ws_update_ts = time.time()
        return True

    def _process_book_event(self, data: dict[str, Any], is_yes_data: bool) -> None:
        asks = data.get("asks", [])
        bids = data.get("bids", [])
        best_ask, best_ask_size = extract_best_ask_with_size_from_book(asks)
        best_bid, best_bid_size = extract_best_bid_with_size_from_book(bids)

        if best_ask is not None and 0.001 <= best_ask <= 0.999:
            if is_yes_data:
                self.orderbook.best_ask_yes = best_ask
                self.orderbook.best_ask_yes_size = best_ask_size
            else:
                self.orderbook.best_ask_no = best_ask
                self.orderbook.best_ask_no_size = best_ask_size

        if best_bid is not None and 0.001 <= best_bid <= 0.999:
            if is_yes_data:
                self.orderbook.best_bid_yes = best_bid
                self.orderbook.best_bid_yes_size = best_bid_size
            else:
                self.orderbook.best_bid_no = best_bid
                self.orderbook.best_bid_no_size = best_bid_size

    def _process_price_change_event(self, data: dict[str, Any]) -> None:
        changes = data.get("price_changes", [])
        for change in changes:
            change_asset_id = change.get("asset_id")
            if not change_asset_id:
                continue

            is_yes_change = change_asset_id == self.token_id_yes
            is_no_change = change_asset_id == self.token_id_no

            if not is_yes_change and not is_no_change:
                continue

            best_ask = change.get("best_ask")
            best_bid = change.get("best_bid")

            if best_ask is not None and best_ask != "":
                try:
                    ask_val = float(best_ask)
                    if 0.001 <= ask_val <= 0.999:
                        if is_yes_change:
                            self.orderbook.best_ask_yes = ask_val
                            self.orderbook.best_ask_yes_size = None
                        else:
                            self.orderbook.best_ask_no = ask_val
                            self.orderbook.best_ask_no_size = None
                except (ValueError, TypeError):
                    pass

            if best_bid is not None and best_bid != "":
                try:
                    bid_val = float(best_bid)
                    if 0.001 <= bid_val <= 0.999:
                        if is_yes_change:
                            self.orderbook.best_bid_yes = bid_val
                            self.orderbook.best_bid_yes_size = None
                        else:
                            self.orderbook.best_bid_no = bid_val
                            self.orderbook.best_bid_no_size = None
                except (ValueError, TypeError):
                    pass

    def _process_best_bid_ask_event(self, data: dict[str, Any], is_yes_data: bool) -> None:
        best_ask = data.get("best_ask")
        best_bid = data.get("best_bid")

        if best_ask is not None and best_ask != "":
            try:
                val = float(best_ask)
                if 0.001 <= val <= 0.999:
                    if is_yes_data:
                        self.orderbook.best_ask_yes = val
                        self.orderbook.best_ask_yes_size = None
                    else:
                        self.orderbook.best_ask_no = val
                        self.orderbook.best_ask_no_size = None
            except (ValueError, TypeError):
                pass

        if best_bid is not None and best_bid != "":
            try:
                val = float(best_bid)
                if 0.001 <= val <= 0.999:
                    if is_yes_data:
                        self.orderbook.best_bid_yes = val
                        self.orderbook.best_bid_yes_size = None
                    else:
                        self.orderbook.best_bid_no = val
                        self.orderbook.best_bid_no_size = None
            except (ValueError, TypeError):
                pass

    def update_winning_side(self) -> None:
        """Update winning side based on current orderbook state."""
        self.winning_side = determine_winning_side(
            best_bid_yes=self.orderbook.best_bid_yes,
            best_bid_no=self.orderbook.best_bid_no,
            best_ask_yes=self.orderbook.best_ask_yes,
            best_ask_no=self.orderbook.best_ask_no,
            tie_epsilon=self.tie_epsilon,
        )

    def get_winning_token_id(self) -> str | None:
        """Get token ID for the winning side."""
        if self.winning_side is None:
            return None
        return get_winning_token_id(
            self.winning_side, self.token_id_yes, self.token_id_no
        )

    def get_winning_ask(self) -> float | None:
        """Get best ask price for winning side."""
        if self.winning_side == "YES":
            return self.orderbook.best_ask_yes
        elif self.winning_side == "NO":
            return self.orderbook.best_ask_no
        return None

    def get_winning_bid(self) -> float | None:
        """Get best bid price for winning side."""
        if self.winning_side == "YES":
            return self.orderbook.best_bid_yes
        elif self.winning_side == "NO":
            return self.orderbook.best_bid_no
        return None

    def get_ask_for_side(self, side: str) -> float | None:
        if side == "YES":
            return self.orderbook.best_ask_yes
        if side == "NO":
            return self.orderbook.best_ask_no
        return None

    def get_bid_for_side(self, side: str) -> float | None:
        if side == "YES":
            return self.orderbook.best_bid_yes
        if side == "NO":
            return self.orderbook.best_bid_no
        return None

    def check_liquidity(self) -> bool:
        """
        Check if orderbook has sufficient liquidity.

        Returns True if total liquidity >= MIN_ORDERBOOK_SIZE_USD.
        Returns True if no data available yet.
        """
        total_size = 0.0
        has_data = False

        for sz in (
            self.orderbook.best_bid_yes_size,
            self.orderbook.best_ask_yes_size,
            self.orderbook.best_bid_no_size,
            self.orderbook.best_ask_no_size,
        ):
            if sz is not None:
                total_size += sz
                has_data = True

        if not has_data:
            return True

        return total_size >= MIN_ORDERBOOK_SIZE_USD

    def is_yes_data(self, asset_id: str) -> bool:
        return asset_id == self.token_id_yes
