"""
Shared fixtures for integration tests.

This module provides reusable fixtures for testing end-to-end workflows
with mocked external APIs (Gamma API, CLOB WebSocket, RTDS WebSocket).
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.hft_trader import LastSecondTrader


@pytest.fixture
async def mock_clob_client():
    """Create a mocked CLOB client for testing."""
    client = MagicMock()
    client.get_balance_allowance = MagicMock(
        return_value={
            "balance": int(100 * 1e6),
            "allowances": {
                "0xC5d563A36AE78145C45a50134d48A1215220f80a": int(100 * 1e6)
            },
        }
    )
    return client


@pytest.fixture
async def mock_websocket():
    """Create a mocked WebSocket for testing."""
    return AsyncMock()


@pytest.fixture
def mock_event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


@pytest.fixture
def sample_market_data():
    """Sample market data for testing."""
    return {
        "condition_id": "0xtest123",
        "token_id_yes": "token_yes_abc",
        "token_id_no": "token_no_def",
        "end_time": datetime.now(timezone.utc),
        "title": "BTC Up or Down",
        "slug": "btc-up-or-down",
    }


@pytest.fixture
async def integration_trader(sample_market_data, mock_clob_client):
    """Create a LastSecondTrader instance for integration tests."""
    with patch("src.hft_trader.load_dotenv"):
        trader = LastSecondTrader(
            condition_id=sample_market_data["condition_id"],
            token_id_yes=sample_market_data["token_id_yes"],
            token_id_no=sample_market_data["token_id_no"],
            end_time=sample_market_data["end_time"],
            dry_run=True,
            trade_size=1.0,
            title=sample_market_data["title"],
            slug=sample_market_data["slug"],
            oracle_enabled=False,
            oracle_guard_enabled=False,
        )
        # Use the configured mock client directly, not its return_value
        trader.client = mock_clob_client
    return trader
