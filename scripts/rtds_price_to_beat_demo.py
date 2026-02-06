#!/usr/bin/env python3
"""
CLI demo: show Polymarket "Up or Down" oracle prices.

Behavior:
- Live market: stream Chainlink current price (RTDS).
  Also tries to capture price-to-beat (window open) and final price (window close)
  based on the market window boundaries.
- Closed market: fetch the exact price-to-beat/final price embedded in the
  Polymarket event page HTML and exit (matches the UI).

This script is intentionally a thin wrapper around src/updown_prices.py so the
logic can be reused elsewhere.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import aiohttp

# Ensure repo root is importable when executing as `python scripts/...`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.updown_prices import (
    ET_TZ,
    EventPageClient,
    GammaClient,
    RtdsClient,
    format_ts_local,
    guess_chainlink_symbol,
    parse_market_window,
)


async def run() -> None:
    parser = argparse.ArgumentParser(
        description="Show price-to-beat/current price for Polymarket 'Up or Down' markets."
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
    parser.add_argument(
        "--show-binance",
        action="store_true",
        help="Also print RTDS topic=crypto_prices (Binance) alongside Chainlink.",
    )
    args = parser.parse_args()

    async with aiohttp.ClientSession() as session:
        gamma = GammaClient(session)
        event_page = EventPageClient(session)

        slug = args.slug
        if slug is None:
            if args.query is None:
                raise SystemExit("Pass --slug or --query.")
            slug, matched = await gamma.find_market_slug_by_query(args.query)
            print(f"Resolved via search: slug={slug} (matched: {matched})")

        market = await gamma.fetch_market_by_slug(slug)
        window = parse_market_window(market.question, market.end_date)

        symbol = args.symbol or guess_chainlink_symbol(market.question)
        if symbol is None:
            raise SystemExit(
                "Could not guess symbol from market question. Pass --symbol."
            )

        print("\nMarket:")
        print(f"  slug: {market.slug}")
        print(f"  question: {market.question}")
        if market.start_date:
            print(f"  startDate: {market.start_date}")
        if market.end_date:
            print(f"  endDate: {market.end_date}")
        if window.start_iso_z:
            print(f"  window_start (UTC): {window.start_iso_z}")
        if window.end_ms is not None:
            # Human-readable ET is easier to eyeball.
            end_dt = window.end_ms / 1000.0
            # Convert to ET via timestamp.
            from datetime import datetime
            from zoneinfo import ZoneInfo

            dt_utc = datetime.fromtimestamp(end_dt, tz=ZoneInfo("UTC"))
            dt_et = dt_utc.astimezone(ET_TZ)
            print(f"  window_end (ET): {dt_et.isoformat()}")
        print(f"  symbol (Chainlink): {symbol}")

        if window.is_closed() and window.start_iso_z is not None:
            asset = symbol.split("/")[0].upper()
            open_p, close_p = await event_page.fetch_past_results(
                eslug=market.slug,
                asset=asset,
                cadence="fifteen",
                start_time_iso_z=window.start_iso_z,
            )
            if open_p is not None:
                print(f"  price_to_beat: {open_p:,.2f}")
            else:
                print("  price_to_beat: -")
            if close_p is not None:
                print(f"  final_price: {close_p:,.2f}")
            else:
                print("  final_price: -")
            print("\nMarket is closed; done.")
            return

    # Live streaming uses only websocket, so it doesn't keep the HTTP session open.
    print("\nStreaming current price (RTDS):")
    rtds = RtdsClient()
    topics = {"crypto_prices_chainlink"}
    if args.show_binance:
        topics.add("crypto_prices")

    open_price: float | None = None
    close_price: float | None = None

    async for tick in rtds.iter_prices(
        symbol=symbol, topics=topics, seconds=args.seconds
    ):
        # Capture open/close using Chainlink ticks only.
        if tick.topic == "crypto_prices_chainlink":
            if (
                open_price is None
                and window.start_ms is not None
                and tick.ts_ms >= window.start_ms
            ):
                open_price = tick.price
                print(f"Captured price_to_beat (open): {open_price:,.2f}")
            if (
                close_price is None
                and window.end_ms is not None
                and tick.ts_ms >= window.end_ms
            ):
                close_price = tick.price
                print(f"Captured final_price (close): {close_price:,.2f}")

        beat_str = "-" if open_price is None else f"{open_price:,.2f}"
        tag = "chainlink" if tick.topic == "crypto_prices_chainlink" else "binance"
        print(
            f"[{format_ts_local(tick.ts_ms)}] ({tag}) {tick.symbol} = {tick.price:,.2f} | price_to_beat: {beat_str}"
        )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
