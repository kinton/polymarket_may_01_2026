#!/usr/bin/env python3
"""
Live demo: stream Chainlink oracle price for an "Up or Down" market and print
simple live metrics:
- current price
- price_to_beat (captured at the start of the market window)
- delta / delta%
- rolling volatility (stddev of % returns)
- slope (USD/sec)
- z-score (delta normalized by volatility)

This is intended as a stepping stone for integration into the trader.
No Polymarket webpage scraping; uses only Gamma + RTDS websocket.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
import time

import aiohttp

# Ensure repo root is importable when executing as `python scripts/...`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.oracle_tracker import OracleTracker
from src.updown_prices import GammaClient, RtdsClient, format_ts_local, guess_chainlink_symbol, parse_market_window


def _fmt(v: float | None, *, pct: bool = False) -> str:
    if v is None:
        return "-"
    if pct:
        return f"{v*100:.4f}%"
    return f"{v:,.2f}"


async def run() -> None:
    parser = argparse.ArgumentParser(description="Stream live oracle metrics for an Up/Down market.")
    parser.add_argument("--slug", default=None, help="Market slug (recommended).")
    parser.add_argument("--query", default=None, help="Market title/question for Gamma search.")
    parser.add_argument("--symbol", default=None, help="Override Chainlink symbol (e.g. btc/usd).")
    parser.add_argument("--seconds", type=float, default=30.0, help="How long to stream RTDS before exiting.")
    parser.add_argument(
        "--stats-window",
        type=float,
        default=60.0,
        help="Rolling window in seconds for volatility/slope metrics.",
    )
    args = parser.parse_args()

    async with aiohttp.ClientSession() as session:
        gamma = GammaClient(session)

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
            raise SystemExit("Could not guess symbol; pass --symbol.")

    print("\nMarket:")
    print(f"  slug: {market.slug}")
    print(f"  question: {market.question}")
    if window.start_iso_z:
        print(f"  window_start (UTC): {window.start_iso_z}")
    if window.end_ms is not None:
        print(f"  window_end_ms: {window.end_ms}")
    print(f"  symbol: {symbol}")

    now_ms = int(time.time() * 1000)
    if window.end_ms is not None and now_ms >= window.end_ms:
        print("\nMarket is closed; this live script won't reconstruct price_to_beat.")
        print(
            "Use: uv run python scripts/rtds_price_to_beat_demo.py --slug "
            f"{market.slug}"
        )
        return

    allow_capture_beat = True
    if window.start_ms is not None and now_ms > (window.start_ms + 10_000):
        allow_capture_beat = False
        print(
            "\nNote: window already started >10s ago; price_to_beat capture was likely missed."
        )

    tracker = OracleTracker(window_seconds=args.stats_window)
    rtds = RtdsClient()

    print("\nStreaming (Chainlink RTDS):")
    async for tick in rtds.iter_prices(
        symbol=symbol, topics={"crypto_prices_chainlink"}, seconds=args.seconds
    ):
        if allow_capture_beat and window.start_ms is not None:
            tracker.maybe_set_price_to_beat(
                ts_ms=tick.ts_ms, price=tick.price, start_ms=window.start_ms
            )
        snap = tracker.update(ts_ms=tick.ts_ms, price=tick.price)

        print(
            f"[{format_ts_local(snap.ts_ms)}] {symbol}={snap.price:,.2f} "
            f"| beat={_fmt(snap.price_to_beat)} "
            f"| Δ={_fmt(snap.delta)} "
            f"| Δ%={_fmt(snap.delta_pct, pct=True)} "
            f"| vol={_fmt(snap.vol_pct, pct=True)} "
            f"| slope={_fmt(snap.slope_usd_per_s)}$/s "
            f"| z={_fmt(snap.zscore)}"
        )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
