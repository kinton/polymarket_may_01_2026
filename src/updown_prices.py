"""
Utilities for Polymarket "Up or Down" (recurring) markets.

This module focuses on two prices shown in the Polymarket UI:
- price_to_beat: the reference/oracle price at the START of the window
- final_price: the oracle price at the END of the window (for closed markets)

Sources:
- Gamma API: market metadata (slug, question, endDate). Threshold fields are not
  reliable for this market family and may be placeholders.
- RTDS websocket: live oracle prices (Chainlink) and spot prices (Binance).
- Polymarket event page: embeds exact open/close oracle prices for past windows.
"""

from __future__ import annotations

import json
import re
import time
import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import aiohttp
import websockets

RTDS_WS_URL = "wss://ws-live-data.polymarket.com"
GAMMA_MARKET_BY_SLUG_URL = "https://gamma-api.polymarket.com/markets/slug/{slug}"
GAMMA_PUBLIC_SEARCH_URL = "https://gamma-api.polymarket.com/public-search"
POLY_EVENT_URL = "https://polymarket.com/event/{eslug}"

ET_TZ = ZoneInfo("America/New_York")
UTC_TZ = ZoneInfo("UTC")


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        if value.strip() == "":
            return None
        try:
            return float(value)
        except ValueError:
            return None
    return None


def guess_chainlink_symbol(market_question: str) -> str | None:
    q = market_question.lower()
    if "bitcoin" in q or "btc" in q:
        return "btc/usd"
    if "ethereum" in q or "eth" in q:
        return "eth/usd"
    if "solana" in q or "sol " in q or " sol" in q:
        return "sol/usd"
    return None


@dataclass(frozen=True)
class MarketMeta:
    slug: str
    question: str
    end_date: str | None
    start_date: str | None


@dataclass(frozen=True)
class MarketWindow:
    start_ms: int | None
    end_ms: int | None

    @property
    def start_iso_z(self) -> str | None:
        if self.start_ms is None:
            return None
        start_dt = datetime.fromtimestamp(self.start_ms / 1000, tz=UTC_TZ)
        return start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    def is_closed(self, now_ms: int | None = None) -> bool:
        if self.end_ms is None:
            return False
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        return now_ms >= self.end_ms


def parse_market_window(question: str, end_date_iso: str | None) -> MarketWindow:
    """
    Parse titles like:
      "Bitcoin Up or Down - February 4, 5:00AM-5:15AM ET"
    and return the window start/end timestamps.
    """
    match = re.search(
        r"-\s*([A-Za-z]+)\s+(\d{1,2}),\s*(\d{1,2}:\d{2})(AM|PM)-(\d{1,2}:\d{2})(AM|PM)\s*ET",
        question,
    )
    if not match:
        return MarketWindow(start_ms=None, end_ms=_end_ms_from_iso(end_date_iso))

    month, day, start_time, start_ampm, _end_time, _end_ampm = match.groups()
    year = _year_from_end_iso(end_date_iso)
    if year is None:
        year = datetime.now(tz=ET_TZ).year

    start_ms = _parse_et_timestamp_ms(
        dt_str=f"{month} {day} {year} {start_time}{start_ampm}"
    )
    end_ms = _end_ms_from_iso(end_date_iso)
    return MarketWindow(start_ms=start_ms, end_ms=end_ms)


def _year_from_end_iso(end_date_iso: str | None) -> int | None:
    if not end_date_iso:
        return None
    try:
        end_dt = datetime.fromisoformat(end_date_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return end_dt.year


def _end_ms_from_iso(end_date_iso: str | None) -> int | None:
    if not end_date_iso:
        return None
    try:
        end_dt = datetime.fromisoformat(end_date_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(end_dt.timestamp() * 1000)


def _parse_et_timestamp_ms(dt_str: str) -> int | None:
    for fmt in ("%B %d %Y %I:%M%p", "%b %d %Y %I:%M%p"):
        try:
            naive = datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
        start_dt = naive.replace(tzinfo=ET_TZ)
        return int(start_dt.timestamp() * 1000)
    return None


def extract_past_results_from_event_html(
    html: str, asset: str, cadence: str, start_time_iso_z: str
) -> tuple[float | None, float | None]:
    """
    Extract open/close prices embedded in the event page HTML.

    The page includes dehydrated state with a query key like:
      queryKey=["past-results","BTC","fifteen","2026-02-04T10:00:00Z"]
    """
    key = f'"queryKey":["past-results","{asset}","{cadence}","{start_time_iso_z}"]'
    idx = html.find(key)
    if idx == -1:
        return None, None

    window = html[idx : idx + 2500]
    # For live windows, closePrice can be null/missing. We extract openPrice
    # and closePrice if present.
    m_open = re.search(r'"openPrice":([0-9.]+)', window)
    if not m_open:
        return None, None
    open_price = to_float(m_open.group(1))

    m_close = re.search(r'"closePrice":(null|[0-9.]+)', window)
    close_price = None
    if m_close:
        close_raw = m_close.group(1)
        if close_raw != "null":
            close_price = to_float(close_raw)

    return open_price, close_price


class GammaClient:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def fetch_market_by_slug(self, slug: str) -> MarketMeta:
        url = GAMMA_MARKET_BY_SLUG_URL.format(slug=slug)
        async with self._session.get(
            url, timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()

        question = str(data.get("question") or "")
        return MarketMeta(
            slug=str(data.get("slug") or slug),
            question=question,
            end_date=data.get("endDate"),
            start_date=data.get("startDate"),
        )

    async def find_market_slug_by_query(self, query: str) -> tuple[str, str]:
        async with self._session.get(
            GAMMA_PUBLIC_SEARCH_URL,
            params={"q": query},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()

        events = data.get("events", [])
        if not isinstance(events, list) or not events:
            raise ValueError(f"No events returned for query={query!r}")

        candidates: list[tuple[str, str]] = []
        for event in events:
            if not isinstance(event, dict):
                continue

            markets = event.get("markets")
            if isinstance(markets, list) and markets:
                for m in markets:
                    if not isinstance(m, dict):
                        continue
                    slug = m.get("slug")
                    text = (
                        m.get("question") or m.get("title") or event.get("title") or ""
                    )
                    if isinstance(slug, str) and slug:
                        candidates.append((slug, str(text)))
            else:
                slug = event.get("slug")
                text = event.get("question") or event.get("title") or ""
                if isinstance(slug, str) and slug:
                    candidates.append((slug, str(text)))

        if not candidates:
            raise ValueError(f"No markets with slugs found for query={query!r}")

        q_norm = query.strip().lower()
        for slug, text in candidates:
            if text.strip().lower() == q_norm:
                return slug, text
        for slug, text in candidates:
            if q_norm in text.strip().lower():
                return slug, text

        return candidates[0]


class EventPageClient:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def fetch_past_results(
        self,
        eslug: str,
        asset: str,
        cadence: str,
        start_time_iso_z: str,
    ) -> tuple[float | None, float | None]:
        """
        Fetches the event page HTML and extracts open/close prices.

        Touches polymarket.com (Cloudflare risk). Prefer using RTDS snapshots for
        live markets if possible.
        """
        url = POLY_EVENT_URL.format(eslug=eslug)
        headers = {"User-Agent": "Mozilla/5.0"}
        async with self._session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)
        ) as resp:
            if resp.status != 200:
                return None, None
            html = await resp.text()
        return extract_past_results_from_event_html(
            html, asset=asset, cadence=cadence, start_time_iso_z=start_time_iso_z
        )


@dataclass(frozen=True)
class PriceTick:
    topic: str
    symbol: str
    ts_ms: int
    price: float


class RtdsClient:
    def __init__(self, ws_url: str = RTDS_WS_URL) -> None:
        self._ws_url = ws_url

    async def iter_prices(
        self, *, symbol: str, topics: set[str], seconds: float
    ) -> AsyncIterator[PriceTick]:
        """
        Yields price ticks for the requested topics.

        RTDS messages can arrive in two shapes:
        - payload has value/timestamp (single tick)
        - payload has data[] (buffered series, last element is the latest)
        """
        subscriptions = [
            {"topic": topic, "type": "*", "filters": json.dumps({"symbol": symbol})}
            for topic in sorted(topics)
        ]
        sub_msg = {"action": "subscribe", "subscriptions": subscriptions}

        deadline = time.time() + seconds
        async with websockets.connect(
            self._ws_url,
            ping_interval=20,
            ping_timeout=10,
            open_timeout=10,
        ) as ws:
            await ws.send(json.dumps(sub_msg))

            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(2.0, remaining))
                except asyncio.TimeoutError:
                    continue

                if not raw:
                    continue

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue

                msg_topic = msg.get("topic")
                if not isinstance(msg_topic, str):
                    continue
                if msg_topic not in {"crypto_prices", "crypto_prices_chainlink"}:
                    continue

                payload = msg.get("payload")
                if not isinstance(payload, dict):
                    continue

                payload_symbol = payload.get("symbol")
                if isinstance(payload_symbol, str) and payload_symbol != symbol:
                    continue

                # RTDS currently sends msg.topic="crypto_prices" even when subscribing
                # to "crypto_prices_chainlink". We derive a canonical topic based on
                # the symbol format.
                canonical_topic = (
                    "crypto_prices_chainlink" if "/" in symbol else "crypto_prices"
                )
                if canonical_topic not in topics:
                    continue

                tick = _tick_from_payload(
                    topic=canonical_topic, symbol=symbol, payload=payload
                )
                if tick is None:
                    continue
                yield tick


def _tick_from_payload(topic: str, symbol: str, payload: dict[str, Any]) -> PriceTick | None:
    if "value" in payload:
        price = to_float(payload.get("value"))
        ts_ms = payload.get("timestamp")
        if price is None or not isinstance(ts_ms, (int, float)):
            return None
        return PriceTick(topic=topic, symbol=symbol, ts_ms=int(ts_ms), price=price)

    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return None
    point = data[-1]
    if not isinstance(point, dict):
        return None
    price = to_float(point.get("value"))
    ts_ms = point.get("timestamp")
    if price is None or not isinstance(ts_ms, (int, float)):
        return None
    return PriceTick(topic=topic, symbol=symbol, ts_ms=int(ts_ms), price=price)


def format_ts_local(ts_ms: int) -> str:
    ts_s = ts_ms / 1000.0
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts_s))


def build_series_endpoint_hint(series_slug: str) -> str:
    """
    Helpful debug string: the UI queries an internal endpoint. We don't rely on it
    for correctness, but it's useful to spot changes.
    """
    qs = urlencode({"slug": series_slug})
    return f"https://polymarket.com/api/series?{qs}"
