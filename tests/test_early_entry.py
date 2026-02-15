"""Tests for early entry mode functionality."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from src.clob_types import (
    EARLY_ENTRY_ENABLED,
    EARLY_ENTRY_CONFIDENCE_THRESHOLD,
    EARLY_ENTRY_START_TIME_S,
    EARLY_ENTRY_END_TIME_S,
)
from src.hft_trader import LastSecondTrader


class TestEarlyEntryMode:
    """Test early entry mode functionality."""

    @pytest.fixture
    def trader(self):
        """Create a trader instance for testing."""
        end_time = datetime.now(timezone.utc) + timedelta(minutes=15)
        trader = LastSecondTrader(
            condition_id="test_condition",
            token_id_yes="yes_token",
            token_id_no="no_token",
            end_time=end_time,
            dry_run=True,
            trade_size=1.0,
            title="Test BTC Up or Down",
            slug="test-btc-up-or-down",
            trader_logger=None,
            oracle_enabled=False,
            oracle_guard_enabled=False,
        )
        return trader

    @patch("src.hft_trader.EARLY_ENTRY_ENABLED", True)
    def test_early_entry_with_high_confidence(self, trader):
        """Test that early entry triggers when confidence is >= 90%."""
        # Setup: 10 minutes before close (within early entry window)
        time_offset = timedelta(minutes=10)
        trader.end_time = datetime.now(timezone.utc) + time_offset

        # Setup: YES is winning with 95% confidence (> 90% threshold)
        trader.orderbook.best_bid_yes = 0.95
        trader.orderbook.best_ask_yes = 0.96
        trader.orderbook.best_bid_yes_size = 60.0  # $60 YES liquidity
        trader.orderbook.best_ask_yes_size = 60.0
        trader.orderbook.best_bid_no = 0.05
        trader.orderbook.best_ask_no = 0.04
        trader.orderbook.best_bid_no_size = 60.0  # $60 NO liquidity
        trader.orderbook.best_ask_no_size = 60.0
        trader._update_winning_side()

        # Should be eligible for early entry
        assert trader._check_early_entry_eligibility() is True

    @patch("src.hft_trader.EARLY_ENTRY_ENABLED", True)
    def test_early_entry_with_low_confidence(self, trader):
        """Test that early entry does NOT trigger when confidence < 90%."""
        # Setup: 10 minutes before close (within early entry window)
        time_offset = timedelta(minutes=10)
        trader.end_time = datetime.now(timezone.utc) + time_offset

        # Setup: YES is winning with only 85% confidence (< 90% threshold)
        trader.orderbook.best_bid_yes = 0.85
        trader.orderbook.best_ask_yes = 0.86
        trader.orderbook.best_bid_yes_size = 60.0
        trader.orderbook.best_ask_yes_size = 60.0
        trader.orderbook.best_bid_no = 0.15
        trader.orderbook.best_ask_no = 0.14
        trader.orderbook.best_bid_no_size = 60.0
        trader.orderbook.best_ask_no_size = 60.0
        trader._update_winning_side()

        # Should NOT be eligible for early entry
        assert trader._check_early_entry_eligibility() is False

    @patch("src.hft_trader.EARLY_ENTRY_ENABLED", True)
    def test_early_entry_time_window_start(self, trader):
        """Test early entry at exactly 10 minutes (600s) before close."""
        # Setup: Exactly 600 seconds before close
        time_offset = timedelta(seconds=600)
        trader.end_time = datetime.now(timezone.utc) + time_offset

        # Setup: High confidence (95%)
        trader.orderbook.best_bid_yes = 0.95
        trader.orderbook.best_ask_yes = 0.96
        trader.orderbook.best_bid_yes_size = 60.0
        trader.orderbook.best_ask_yes_size = 60.0
        trader.orderbook.best_bid_no = 0.05
        trader.orderbook.best_ask_no = 0.04
        trader.orderbook.best_bid_no_size = 60.0
        trader.orderbook.best_ask_no_size = 60.0
        trader._update_winning_side()

        # Should be eligible (at start of window)
        assert trader._check_early_entry_eligibility() is True

    @patch("src.hft_trader.EARLY_ENTRY_ENABLED", True)
    def test_early_entry_time_window_end(self, trader):
        """Test early entry near 60 seconds before close (end of window)."""
        # Setup: 65 seconds before close (near end of early entry window, accounting for execution time)
        time_offset = timedelta(seconds=65)
        trader.end_time = datetime.now(timezone.utc) + time_offset

        # Setup: High confidence (95%)
        trader.orderbook.best_bid_yes = 0.95
        trader.orderbook.best_ask_yes = 0.96
        trader.orderbook.best_bid_yes_size = 60.0
        trader.orderbook.best_ask_yes_size = 60.0
        trader.orderbook.best_bid_no = 0.05
        trader.orderbook.best_ask_no = 0.04
        trader.orderbook.best_bid_no_size = 60.0
        trader.orderbook.best_ask_no_size = 60.0
        trader._update_winning_side()

        # Should be eligible (at end of window)
        assert trader._check_early_entry_eligibility() is True

    @patch("src.hft_trader.EARLY_ENTRY_ENABLED", True)
    def test_early_entry_before_time_window(self, trader):
        """Test that early entry does NOT trigger > 10 minutes before close."""
        # Setup: 15 minutes before close (before early entry window)
        time_offset = timedelta(minutes=15)
        trader.end_time = datetime.now(timezone.utc) + time_offset

        # Setup: High confidence (95%)
        trader.orderbook.best_bid_yes = 0.95
        trader.orderbook.best_ask_yes = 0.96
        trader.orderbook.best_bid_yes_size = 60.0
        trader.orderbook.best_ask_yes_size = 60.0
        trader.orderbook.best_bid_no = 0.05
        trader.orderbook.best_ask_no = 0.04
        trader.orderbook.best_bid_no_size = 60.0
        trader.orderbook.best_ask_no_size = 60.0
        trader._update_winning_side()

        # Should NOT be eligible (too early)
        assert trader._check_early_entry_eligibility() is False

    @patch("src.hft_trader.EARLY_ENTRY_ENABLED", True)
    def test_early_entry_after_time_window(self, trader):
        """Test that early entry does NOT trigger < 60 seconds before close."""
        # Setup: 30 seconds before close (after early entry window)
        time_offset = timedelta(seconds=30)
        trader.end_time = datetime.now(timezone.utc) + time_offset

        # Setup: High confidence (95%)
        trader.orderbook.best_bid_yes = 0.95
        trader.orderbook.best_ask_yes = 0.96
        trader.orderbook.best_bid_yes_size = 60.0
        trader.orderbook.best_ask_yes_size = 60.0
        trader.orderbook.best_bid_no = 0.05
        trader.orderbook.best_ask_no = 0.04
        trader.orderbook.best_bid_no_size = 60.0
        trader.orderbook.best_ask_no_size = 60.0
        trader._update_winning_side()

        # Should NOT be eligible (too late - falls into late window)
        assert trader._check_early_entry_eligibility() is False

    @patch("src.hft_trader.EARLY_ENTRY_ENABLED", True)
    def test_early_entry_requires_sufficient_liquidity(self, trader):
        """Test that early entry requires sufficient orderbook liquidity."""
        # Setup: 10 minutes before close
        time_offset = timedelta(minutes=10)
        trader.end_time = datetime.now(timezone.utc) + time_offset

        # Setup: High confidence (95%)
        trader.orderbook.best_bid_yes = 0.95
        trader.orderbook.best_ask_yes = 0.96
        trader.orderbook.best_bid_no = 0.05
        trader.orderbook.best_ask_no = 0.04
        trader._update_winning_side()

        # Setup: LOW liquidity (only $20 total, < $100 threshold)
        trader.orderbook.best_bid_yes_size = 5.0
        trader.orderbook.best_ask_yes_size = 5.0
        trader.orderbook.best_bid_no_size = 5.0
        trader.orderbook.best_ask_no_size = 5.0

        # Should NOT be eligible (insufficient liquidity)
        assert trader._check_early_entry_eligibility() is False

    @patch("src.hft_trader.EARLY_ENTRY_ENABLED", True)
    def test_early_entry_with_sufficient_liquidity(self, trader):
        """Test that early entry works with sufficient liquidity."""
        # Setup: 10 minutes before close
        time_offset = timedelta(minutes=10)
        trader.end_time = datetime.now(timezone.utc) + time_offset

        # Setup: High confidence (95%)
        trader.orderbook.best_bid_yes = 0.95
        trader.orderbook.best_ask_yes = 0.96
        trader.orderbook.best_bid_no = 0.05
        trader.orderbook.best_ask_no = 0.04
        trader._update_winning_side()

        # Setup: HIGH liquidity ($200 total, > $100 threshold)
        trader.orderbook.best_bid_yes_size = 50.0
        trader.orderbook.best_ask_yes_size = 50.0
        trader.orderbook.best_bid_no_size = 50.0
        trader.orderbook.best_ask_no_size = 50.0

        # Should be eligible (all conditions met)
        assert trader._check_early_entry_eligibility() is True

    @patch("src.hft_trader.EARLY_ENTRY_ENABLED", True)
    def test_early_entry_with_no_winning_side(self, trader):
        """Test that early entry requires a winning side."""
        # Setup: 10 minutes before close
        time_offset = timedelta(minutes=10)
        trader.end_time = datetime.now(timezone.utc) + time_offset

        # Setup: No winning side determined
        trader.winning_side = None

        # Should NOT be eligible (no winner)
        assert trader._check_early_entry_eligibility() is False

    def test_early_entry_constants(self):
        """Test that early entry constants are properly defined."""
        assert isinstance(EARLY_ENTRY_ENABLED, bool)
        assert isinstance(EARLY_ENTRY_CONFIDENCE_THRESHOLD, float)
        assert isinstance(EARLY_ENTRY_START_TIME_S, float)
        assert isinstance(EARLY_ENTRY_END_TIME_S, float)

        assert EARLY_ENTRY_ENABLED is False
        assert EARLY_ENTRY_CONFIDENCE_THRESHOLD == 0.90
        assert EARLY_ENTRY_START_TIME_S == 600.0
        assert EARLY_ENTRY_END_TIME_S == 60.0
