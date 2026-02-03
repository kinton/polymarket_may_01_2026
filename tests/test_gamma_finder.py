"""
Unit tests for GammaAPI15mFinder filtering logic.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.gamma_15m_finder import GammaAPI15mFinder


def _fixed_now_et():
    return datetime(2026, 2, 3, 12, 0, 0, tzinfo=ZoneInfo("America/New_York"))


def _to_utc_iso(dt_et: datetime) -> str:
    return dt_et.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_filter_markets_in_window_binary_only():
    finder = GammaAPI15mFinder(max_minutes_ahead=20)
    finder.get_current_time_et = _fixed_now_et  # type: ignore

    now_et = _fixed_now_et()
    end_et = now_et.replace(minute=10)
    end_utc = _to_utc_iso(end_et)

    events = [
        {
            "id": "evt1",
            "active": True,
            "ticker": "btc-updown-5m-123",
            "markets": [
                {
                    "conditionId": "0xabc",
                    "clobTokenIds": '["yes_token","no_token"]',
                    "active": True,
                    "endDate": end_utc,
                    "question": "Bitcoin Up or Down",
                }
            ],
        }
    ]

    filtered = finder.filter_markets(events, max_minutes_ahead=20)
    assert len(filtered) == 1
    assert filtered[0]["condition_id"] == "0xabc"
    assert filtered[0]["token_id_yes"] == "yes_token"
    assert filtered[0]["token_id_no"] == "no_token"


def test_filter_markets_skips_outside_window():
    finder = GammaAPI15mFinder(max_minutes_ahead=20)
    finder.get_current_time_et = _fixed_now_et  # type: ignore

    now_et = _fixed_now_et()
    end_et = now_et.replace(minute=40)  # 40 minutes ahead
    end_utc = _to_utc_iso(end_et)

    events = [
        {
            "id": "evt2",
            "active": True,
            "markets": [
                {
                    "conditionId": "0xdef",
                    "clobTokenIds": '["yes_token","no_token"]',
                    "active": True,
                    "endDate": end_utc,
                    "question": "Ethereum Up or Down",
                }
            ],
        }
    ]

    filtered = finder.filter_markets(events, max_minutes_ahead=20)
    assert filtered == []


def test_filter_markets_skips_non_binary():
    finder = GammaAPI15mFinder(max_minutes_ahead=20)
    finder.get_current_time_et = _fixed_now_et  # type: ignore

    now_et = _fixed_now_et()
    end_et = now_et.replace(minute=5)
    end_utc = _to_utc_iso(end_et)

    events = [
        {
            "id": "evt3",
            "active": True,
            "markets": [
                {
                    "conditionId": "0xghi",
                    "clobTokenIds": '["a","b","c"]',
                    "active": True,
                    "endDate": end_utc,
                    "question": "Non-binary market",
                }
            ],
        }
    ]

    filtered = finder.filter_markets(events, max_minutes_ahead=20)
    assert filtered == []
