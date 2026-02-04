#!/usr/bin/env python3
"""
Demo: fetch Polymarket "Price to beat" (from Gamma market metadata) and
stream "Current price" (oracle/spot) via Polymarket RTDS.

Typical use case: "Up or Down" 15m markets where the UI shows:
  - PRICE TO BEAT: fixed reference/threshold for the window
  - CURRENT PRICE: live BTC/ETH/SOL price

Notes:
  - RTDS is a separate websocket feed from the CLOB orderbook websocket.
  - RTDS supports Binance (crypto_prices) and Chainlink (crypto_prices_chainlink).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
import websockets


RTDS_WS_URL = "wss://ws-live-data.polymarket.com"
GAMMA_MARKET_BY_SLUG_URL = "https://gamma-api.polymarket.com/markets/slug/{slug}"
GAMMA_PUBLIC_SEARCH_URL = "https://gamma-api.polymarket.com/public-search"


def _to_float(value: Any) -> float | None:
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


def _guess_chainlink_symbol(market_question: str) -> str | None:
    q = market_question.lower()
    if "bitcoin" in q or "btc" in q:
        return "btc/usd"
    if "ethereum" in q or "eth" in q:
        return "eth/usd"
    if "solana" in q or "sol " in q or " sol" in q:
        return "sol/usd"
    return None


def _parse_market_window_start_ms(
    question: str, end_date_iso: str | None
) -> int | None:
    """
    Parse market question like:
      "Bitcoin Up or Down - February 4, 5:00AM-5:15AM ET"
    and return the start timestamp in milliseconds (ET timezone).
    """
    match = re.search(
        r"-\s*([A-Za-z]+)\s+(\d{1,2}),\s*(\d{1,2}:\d{2})(AM|PM)-(\d{1,2}:\d{2})(AM|PM)\s*ET",
        question,
    )
    if not match:
        return None

    month, day, start_time, start_ampm, _end_time, _end_ampm = match.groups()

    year: int | None = None
    if end_date_iso:
        try:
            end_dt = datetime.fromisoformat(end_date_iso.replace("Z", "+00:00"))
            year = end_dt.year
        except ValueError:
            year = None
    if year is None:
        year = datetime.now(tz=ZoneInfo("America/New_York")).year

    dt_str = f"{month} {day} {year} {start_time}{start_ampm}"
    for fmt in ("%B %d %Y %I:%M%p", "%b %d %Y %I:%M%p"):
        try:
            naive = datetime.strptime(dt_str, fmt)
            break
        except ValueError:
            naive = None
    if naive is None:
        return None

    et = ZoneInfo("America/New_York")
    start_dt = naive.replace(tzinfo=et)
    return int(start_dt.timestamp() * 1000)


def _pick_price_to_beat_from_points(
    points: list[dict[str, Any]], start_ts_ms: int
) -> float | None:
    best_ts: int | None = None
    best_price: float | None = None

    for point in points:
        ts_val = point.get("timestamp")
        if not isinstance(ts_val, (int, float)):
            continue
        ts_ms = int(ts_val)
        if ts_ms < start_ts_ms:
            continue
        price = _to_float(point.get("value"))
        if price is None:
            continue
        if best_ts is None or ts_ms < best_ts:
            best_ts = ts_ms
            best_price = price

    if best_price is not None:
        return best_price

    # Fallback: nearest BEFORE start time (within 5 minutes)
    max_skew_ms = 5 * 60 * 1000
    for point in reversed(points):
        ts_val = point.get("timestamp")
        if not isinstance(ts_val, (int, float)):
            continue
        ts_ms = int(ts_val)
        if ts_ms > start_ts_ms:
            continue
        if start_ts_ms - ts_ms > max_skew_ms:
            break
        price = _to_float(point.get("value"))
        if price is not None:
            return price

    return None


@dataclass(frozen=True)
class MarketMeta:
    slug: str
    question: str
    end_date: str | None
    start_date: str | None
    group_item_threshold: float | None
    y_axis_value: float | None
    lower_bound: float | None
    upper_bound: float | None


async def fetch_market_by_slug(session: aiohttp.ClientSession, slug: str) -> MarketMeta:
    url = GAMMA_MARKET_BY_SLUG_URL.format(slug=slug)
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        resp.raise_for_status()
        data = await resp.json()

    question = str(data.get("question") or "")
    return MarketMeta(
        slug=str(data.get("slug") or slug),
        question=question,
        end_date=data.get("endDate"),
        start_date=data.get("startDate"),
        group_item_threshold=_to_float(data.get("groupItemThreshold")),
        y_axis_value=_to_float(data.get("yAxisValue")),
        lower_bound=_to_float(data.get("lowerBound")),
        upper_bound=_to_float(data.get("upperBound")),
    )


async def find_market_slug_by_query(
    session: aiohttp.ClientSession, query: str
) -> tuple[str, str]:
    """
    Best-effort slug discovery using Gamma public-search.
    Returns (slug, question/title).
    """
    async with session.get(
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
                text = m.get("question") or m.get("title") or event.get("title") or ""
                if isinstance(slug, str) and slug:
                    candidates.append((slug, str(text)))
        else:
            slug = event.get("slug")
            text = event.get("question") or event.get("title") or ""
            if isinstance(slug, str) and slug:
                candidates.append((slug, str(text)))

    if not candidates:
        raise ValueError(f"No markets with slugs found for query={query!r}")

    # Prefer exact-ish matches on title/question if available.
    q_norm = query.strip().lower()
    for slug, text in candidates:
        if text.strip().lower() == q_norm:
            return slug, text
    for slug, text in candidates:
        if q_norm in text.strip().lower():
            return slug, text

    return candidates[0]


async def stream_chainlink_price(
    symbol: str,
    seconds: float,
    price_to_beat: float | None,
    start_ts_ms: int | None,
) -> None:
    subscriptions = [
        {
            "topic": "crypto_prices_chainlink",
            "type": "*",
            "filters": json.dumps({"symbol": symbol}),
        },
        {
            "topic": "crypto_prices",
            "type": "*",
            "filters": json.dumps({"symbol": symbol}),
        },
    ]
    sub = {"action": "subscribe", "subscriptions": subscriptions}

    async with websockets.connect(
        RTDS_WS_URL,
        ping_interval=20,
        ping_timeout=10,
        open_timeout=10,
    ) as ws:
        await ws.send(json.dumps(sub))
        start = time.time()
        deadline = start + seconds

        print(f"RTDS connected: {RTDS_WS_URL}")
        print(
            f"Subscribed: crypto_prices_chainlink + crypto_prices, symbol={symbol}"
        )

        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return

            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=min(2.0, remaining))
            except asyncio.TimeoutError:
                continue

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if not isinstance(msg, dict):
                continue

            if msg.get("topic") not in {"crypto_prices_chainlink", "crypto_prices"}:
                continue

            payload = msg.get("payload") or {}
            if not isinstance(payload, dict):
                continue

            payload_symbol = payload.get("symbol")
            if isinstance(payload_symbol, str) and payload_symbol != symbol:
                continue

            price: float | None = None
            ts_ms: int | float | None = None

            if "value" in payload:
                price = _to_float(payload.get("value"))
                ts_ms = payload.get("timestamp")  # type: ignore[assignment]
            else:
                data = payload.get("data")
                if isinstance(data, list) and data:
                    if price_to_beat is None and start_ts_ms is not None:
                        pick = _pick_price_to_beat_from_points(data, start_ts_ms)
                        if pick is not None:
                            price_to_beat = pick
                            print(
                                f"Resolved price_to_beat from RTDS: {price_to_beat:,.2f}"
                            )
                    point = data[-1]
                    if isinstance(point, dict):
                        price = _to_float(point.get("value"))
                        ts_ms = point.get("timestamp")  # type: ignore[assignment]

            if price is None:
                continue

            ts_s = (int(ts_ms) / 1000.0) if isinstance(ts_ms, (int, float)) else None
            ts_str = (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts_s))
                if ts_s is not None
                else "-"
            )
            if price_to_beat is None:
                print(f"[{ts_str}] {symbol} = {price:,.2f} | price_to_beat: -")
            else:
                print(
                    f"[{ts_str}] {symbol} = {price:,.2f} | price_to_beat: {price_to_beat:,.2f}"
                )


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch 'price to beat' (Gamma) and stream current price (RTDS)."
    )
    parser.add_argument("--slug", default=None, help="Polymarket market slug.")
    parser.add_argument(
        "--query",
        default=None,
        help="Market title/question to find via Gamma public-search (best-effort).",
    )
    parser.add_argument(
        "--symbol",
        default=None,
        help="Override Chainlink symbol (default guessed from market question). Example: btc/usd",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=15.0,
        help="How long to stream RTDS prices before exiting.",
    )
    args = parser.parse_args()

    async with aiohttp.ClientSession() as session:
        slug = args.slug
        if slug is None:
            if args.query is None:
                raise SystemExit("Pass --slug or --query.")
            slug, matched_text = await find_market_slug_by_query(session, args.query)
            print(f"Resolved via search: slug={slug} (matched: {matched_text})")

        market = await fetch_market_by_slug(session, slug)

    # "Price to beat" usually appears as groupItemThreshold for numeric markets.
    price_to_beat = market.group_item_threshold or market.y_axis_value
    start_ts_ms = _parse_market_window_start_ms(market.question, market.end_date)

    print("\nMarket:")
    print(f"  slug: {market.slug}")
    print(f"  question: {market.question}")
    if market.start_date:
        print(f"  startDate: {market.start_date}")
    if market.end_date:
        print(f"  endDate: {market.end_date}")
    if start_ts_ms is not None:
        start_dt = datetime.fromtimestamp(start_ts_ms / 1000, tz=ZoneInfo("UTC"))
        print(f"  window_start (UTC): {start_dt.isoformat()}")
    if price_to_beat is not None:
        print(f"  price_to_beat (Gamma): {price_to_beat:,.2f}")
    else:
        print("  price_to_beat (Gamma): <missing> (no groupItemThreshold/yAxisValue)")
    if market.lower_bound is not None or market.upper_bound is not None:
        print(f"  bounds: {market.lower_bound} .. {market.upper_bound}")

    symbol = args.symbol or _guess_chainlink_symbol(market.question)
    if symbol is None:
        raise SystemExit(
            "Could not guess symbol from market question. Pass --symbol (e.g. btc/usd)."
        )

    print("\nStreaming current price (RTDS):")
    await stream_chainlink_price(
        symbol=symbol,
        seconds=args.seconds,
        price_to_beat=price_to_beat,
        start_ts_ms=start_ts_ms,
    )


if __name__ == "__main__":
    asyncio.run(main())
