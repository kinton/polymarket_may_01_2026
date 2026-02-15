"""Trade strategy / trigger logic for Polymarket last-second trading."""

import time
import logging
from datetime import datetime, timezone
from typing import Any

from src.clob_types import (
    MAX_BUY_PRICE,
    MIN_BUY_PRICE,
    MIN_CONFIDENCE,
    MIN_ORDERBOOK_SIZE_USD,
    PRICE_TIE_EPS,
    EARLY_ENTRY_ENABLED,
    EARLY_ENTRY_CONFIDENCE_THRESHOLD,
    EARLY_ENTRY_START_TIME_S,
    EARLY_ENTRY_END_TIME_S,
    TRIGGER_THRESHOLD,
)
from src.trading.orderbook_tracker import OrderbookTracker


class TradeStrategy:
    """Encapsulates trigger/entry decision logic."""

    def __init__(
        self,
        orderbook_tracker: OrderbookTracker,
        market_name: str = "UNKNOWN",
        trigger_threshold: float = TRIGGER_THRESHOLD,
        max_buy_price: float = MAX_BUY_PRICE,
        min_buy_price: float = MIN_BUY_PRICE,
        min_confidence: float = MIN_CONFIDENCE,
        price_tie_eps: float = PRICE_TIE_EPS,
        end_time: datetime | None = None,
        logger: logging.Logger | None = None,
    ):
        self.ob = orderbook_tracker
        self.market_name = market_name
        self.TRIGGER_THRESHOLD = trigger_threshold
        self.MAX_BUY_PRICE = max_buy_price
        self.MIN_BUY_PRICE = min_buy_price
        self.MIN_CONFIDENCE = min_confidence
        self.PRICE_TIE_EPS = price_tie_eps
        self.end_time = end_time
        self.logger = logger

    def _log(self, message: str) -> None:
        if self.logger:
            self.logger.info(message)
        else:
            print(message)

    def check_early_entry_eligibility(self, early_entry_enabled: bool | None = None) -> bool:
        """
        Check if early entry conditions are met.

        Returns True if:
        - Early entry is enabled
        - Time remaining is in the early entry window
        - Winning side confidence >= threshold
        - Orderbook has sufficient liquidity
        """
        enabled = early_entry_enabled if early_entry_enabled is not None else EARLY_ENTRY_ENABLED
        if not enabled:
            return False

        if self.end_time is None:
            return False

        time_remaining = (self.end_time - datetime.now(timezone.utc)).total_seconds()

        if not (EARLY_ENTRY_END_TIME_S <= time_remaining <= EARLY_ENTRY_START_TIME_S):
            return False

        if self.ob.winning_side is None:
            return False

        winning_bid = self.ob.get_winning_bid()
        if winning_bid is None or winning_bid < EARLY_ENTRY_CONFIDENCE_THRESHOLD:
            return False

        if not self.ob.check_liquidity():
            return False

        return True
