from __future__ import annotations

from src.updown_prices import extract_past_results_from_event_html, parse_market_window


def test_extract_past_results_from_event_html() -> None:
    html = (
        '... "queryKey":["past-results","BTC","fifteen","2026-02-04T10:00:00Z"] ...'
        '"state":{"data":{"openPrice":75999.14312433584,"closePrice":76035.12727031851},'
        '"dataUpdateCount":1} ...'
    )
    open_p, close_p = extract_past_results_from_event_html(
        html, asset="BTC", cadence="fifteen", start_time_iso_z="2026-02-04T10:00:00Z"
    )
    assert open_p is not None
    assert close_p is not None
    assert round(open_p, 2) == 75999.14
    assert round(close_p, 2) == 76035.13


def test_parse_market_window_returns_end_ms_from_end_date() -> None:
    window = parse_market_window(
        "Bitcoin Up or Down - February 4, 5:00AM-5:15AM ET",
        "2026-02-04T10:15:00Z",
    )
    assert window.start_ms is not None
    assert window.end_ms is not None

