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
import time
from dataclasses import dataclass
from typing import Any

import aiohttp
import websockets


RTDS_WS_URL = "wss://ws-live-data.polymarket.com"
GAMMA_MARKET_BY_SLUG_URL = "https://gamma-api.polymarket.com/markets/slug/{slug}"


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


async def stream_chainlink_price(symbol: str, seconds: float) -> None:
    sub = {
        "action": "subscribe",
        "subscriptions": [
            {
                "topic": "crypto_prices_chainlink",
                "type": "*",
                "filters": json.dumps({"symbol": symbol}),
            }
        ],
    }

    async with websockets.connect(RTDS_WS_URL, ping_interval=20, ping_timeout=10) as ws:
        await ws.send(json.dumps(sub))
        start = time.time()

        print(f"RTDS connected: {RTDS_WS_URL}")
        print(f"Subscribed: crypto_prices_chainlink symbol={symbol}")

        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if not isinstance(msg, dict):
                continue

            if msg.get("topic") != "crypto_prices_chainlink":
                continue

            payload = msg.get("payload") or {}
            if not isinstance(payload, dict):
                continue

            if payload.get("symbol") != symbol:
                continue

            value = payload.get("value")
            ts_ms = payload.get("timestamp")
            price = _to_float(value)

            if price is None:
                continue

            ts_s = (int(ts_ms) / 1000.0) if isinstance(ts_ms, (int, float)) else None
            ts_str = (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts_s))
                if ts_s is not None
                else "-"
            )
            print(f"[{ts_str}] {symbol} = {price:,.2f}")

            if (time.time() - start) >= seconds:
                return


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch 'price to beat' (Gamma) and stream current price (RTDS)."
    )
    parser.add_argument(
        "--slug",
        required=True,
        help="Polymarket market slug (e.g. 'bitcoin-up-or-down-...').",
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
        market = await fetch_market_by_slug(session, args.slug)

    # "Price to beat" usually appears as groupItemThreshold for numeric markets.
    price_to_beat = market.group_item_threshold or market.y_axis_value

    print("\nMarket:")
    print(f"  slug: {market.slug}")
    print(f"  question: {market.question}")
    if market.start_date:
        print(f"  startDate: {market.start_date}")
    if market.end_date:
        print(f"  endDate: {market.end_date}")
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
    await stream_chainlink_price(symbol=symbol, seconds=args.seconds)


if __name__ == "__main__":
    asyncio.run(main())

