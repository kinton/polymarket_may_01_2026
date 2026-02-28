#!/usr/bin/env python3
"""
Backtest convergence strategy using historical Binance kline data.

Simulates Polymarket "Up or Down" 5-minute markets:
1. Downloads 1-second klines from Binance (or 1-minute if 1s unavailable)
2. Creates synthetic 5m windows with known outcomes
3. Simulates orderbook skew based on price distance from beat price
4. Applies convergence strategy and tracks PnL

Usage:
    uv run python tools/backtest_convergence.py
    uv run python tools/backtest_convergence.py --symbol ETHUSDT --days 30
    uv run python tools/backtest_convergence.py --window 15  # 15-minute markets
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


# ──────────────────────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────────────────────

@dataclass
class Kline:
    """Single price point."""
    ts_ms: int
    price: float  # close price


@dataclass
class MarketWindow:
    """Simulated 5/15-minute Up or Down market."""
    start_ms: int
    end_ms: int
    price_to_beat: float  # open price at window start
    close_price: float    # final price at window end
    outcome: str          # "UP" or "DOWN"
    prices: list[Kline] = field(default_factory=list)  # all ticks in window

    @property
    def duration_s(self) -> float:
        return (self.end_ms - self.start_ms) / 1000


@dataclass
class Trade:
    """A simulated trade."""
    window_idx: int
    side: str           # "UP" or "DOWN" (what we bought)
    entry_price: float  # price we paid (e.g., 0.12)
    time_remaining: float
    oracle_price: float
    price_to_beat: float
    delta_pct: float
    outcome: str        # actual market outcome
    pnl: float          # +0.88 or -0.12
    won: bool


@dataclass
class BacktestResult:
    """Summary of backtest run."""
    symbol: str
    days: int
    window_minutes: int
    total_windows: int
    total_trades: int
    wins: int
    losses: int
    total_pnl: float
    avg_entry_price: float
    avg_win_payout: float
    avg_loss: float
    win_rate: float
    ev_per_trade: float
    max_drawdown: float
    convergence_opportunities: int  # windows where convergence was detected
    trades: list[Trade] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────
# Binance data fetcher
# ──────────────────────────────────────────────────────────────

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"

async def fetch_klines(
    symbol: str = "BTCUSDT",
    interval: str = "1s",
    start_ms: int = 0,
    end_ms: int = 0,
    limit: int = 1000,
    session: aiohttp.ClientSession | None = None,
) -> list[Kline]:
    """Fetch klines from Binance API."""
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()

    try:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": limit,
        }
        async with session.get(BINANCE_KLINES_URL, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 429:
                # Rate limited — wait and retry
                await asyncio.sleep(2)
                async with session.get(BINANCE_KLINES_URL, params=params) as resp2:
                    data = await resp2.json()
            elif resp.status != 200:
                text = await resp.text()
                print(f"Binance API error {resp.status}: {text}")
                return []
            else:
                data = await resp.json()

        klines = []
        for k in data:
            # Kline format: [open_time, open, high, low, close, volume, close_time, ...]
            klines.append(Kline(ts_ms=int(k[0]), price=float(k[4])))  # close price
        return klines

    finally:
        if own_session:
            await session.close()


async def fetch_all_klines(
    symbol: str,
    interval: str,
    start_time: datetime,
    end_time: datetime,
    cache_dir: str = "data/backtest_cache",
) -> list[Kline]:
    """Fetch all klines for a date range, with caching."""
    os.makedirs(cache_dir, exist_ok=True)

    start_ms = int(start_time.timestamp() * 1000)
    end_ms = int(end_time.timestamp() * 1000)

    # Check cache
    cache_key = f"{symbol}_{interval}_{start_ms}_{end_ms}"
    cache_file = os.path.join(cache_dir, f"{cache_key}.json")

    if os.path.exists(cache_file):
        print(f"📦 Loading cached data: {cache_file}")
        with open(cache_file) as f:
            raw = json.load(f)
        return [Kline(ts_ms=k["ts_ms"], price=k["price"]) for k in raw]

    print(f"📡 Fetching {symbol} {interval} klines from Binance...")
    print(f"   {start_time.strftime('%Y-%m-%d %H:%M')} → {end_time.strftime('%Y-%m-%d %H:%M')}")

    all_klines: list[Kline] = []
    current_start = start_ms

    # For 1m interval: 1000 candles = ~16.6 hours per request
    # For 1s interval: 1000 candles = ~16.6 minutes per request
    max_per_request = 1000
    interval_ms = {"1s": 1000, "1m": 60_000, "5m": 300_000}[interval]
    chunk_ms = max_per_request * interval_ms

    total_chunks = max(1, (end_ms - start_ms) // chunk_ms + 1)
    fetched = 0

    async with aiohttp.ClientSession() as session:
        while current_start < end_ms:
            chunk_end = min(current_start + chunk_ms, end_ms)
            klines = await fetch_klines(
                symbol=symbol,
                interval=interval,
                start_ms=current_start,
                end_ms=chunk_end,
                limit=max_per_request,
                session=session,
            )

            if not klines:
                break

            all_klines.extend(klines)
            current_start = klines[-1].ts_ms + interval_ms
            fetched += 1

            if fetched % 20 == 0:
                pct = min(100, fetched * 100 // total_chunks)
                print(f"   ... {pct}% ({len(all_klines)} klines)")

            # Rate limit: ~10 requests/sec
            await asyncio.sleep(0.1)

    print(f"   ✅ Fetched {len(all_klines)} klines total")

    # Cache
    with open(cache_file, "w") as f:
        json.dump([{"ts_ms": k.ts_ms, "price": k.price} for k in all_klines], f)
    print(f"   💾 Cached to {cache_file}")

    return all_klines


# ──────────────────────────────────────────────────────────────
# Market window generator
# ──────────────────────────────────────────────────────────────

def generate_windows(klines: list[Kline], window_minutes: int = 5) -> list[MarketWindow]:
    """
    Split klines into synthetic market windows.

    Each window is `window_minutes` long. The first price is price_to_beat,
    the last price determines outcome (UP if close > open, DOWN otherwise).
    """
    if not klines:
        return []

    window_ms = window_minutes * 60 * 1000
    windows: list[MarketWindow] = []

    # Find first window-aligned start
    first_ts = klines[0].ts_ms
    # Align to window boundary
    window_start = first_ts - (first_ts % window_ms)
    if window_start < first_ts:
        window_start += window_ms

    last_ts = klines[-1].ts_ms
    kline_idx = 0

    while window_start + window_ms <= last_ts:
        window_end = window_start + window_ms

        # Collect klines in this window
        window_klines: list[Kline] = []
        while kline_idx < len(klines) and klines[kline_idx].ts_ms < window_start:
            kline_idx += 1

        scan_idx = kline_idx
        while scan_idx < len(klines) and klines[scan_idx].ts_ms < window_end:
            window_klines.append(klines[scan_idx])
            scan_idx += 1

        if len(window_klines) >= 2:
            price_to_beat = window_klines[0].price
            close_price = window_klines[-1].price
            outcome = "UP" if close_price >= price_to_beat else "DOWN"

            windows.append(MarketWindow(
                start_ms=window_start,
                end_ms=window_end,
                price_to_beat=price_to_beat,
                close_price=close_price,
                outcome=outcome,
                prices=window_klines,
            ))

        window_start = window_end

    return windows


# ──────────────────────────────────────────────────────────────
# Orderbook skew simulator
# ──────────────────────────────────────────────────────────────

def simulate_orderbook_skew(
    current_price: float,
    price_to_beat: float,
    time_remaining_s: float,
    window_duration_s: float = 300,
) -> tuple[float, float]:
    """
    Simulate orderbook ask prices for UP and DOWN sides.

    Models market behavior:
    - When current >> beat: UP side expensive (0.90+), DOWN cheap (0.10-)
    - When current ≈ beat: prices should be ~50/50 BUT we add market lag
    - Lag effect: market reacts slowly, so after a move towards beat,
      prices may still show old skew

    Returns: (ask_up, ask_down) where ask_up + ask_down ≈ 1.0
    """
    if price_to_beat == 0:
        return 0.50, 0.50

    delta_pct = (current_price - price_to_beat) / price_to_beat

    # Base probability from delta:
    # Large positive delta → UP likely → UP expensive
    # We use a sigmoid-like function
    # At ±0.1% (10bp): moderate skew
    # At ±0.5% (50bp): strong skew (90/10)
    import math

    # Scale factor: how many bp maps to 90/10 skew
    # For BTC, ~50bp ($42 on $85k) creates strong directional signal
    sensitivity = 2000  # higher = more sensitive to small moves

    # Time factor: closer to expiry, market is more confident
    time_factor = 1.0 + (1.0 - time_remaining_s / window_duration_s) * 0.5

    z = delta_pct * sensitivity * time_factor
    prob_up = 1 / (1 + math.exp(-z))

    # Clamp to realistic range [0.02, 0.98]
    prob_up = max(0.02, min(0.98, prob_up))

    # Add market lag: prices react slower than oracle
    # This creates the convergence opportunity!
    # When price reverts to beat (delta→0), market still shows old skew
    lag_factor = 0.85  # market captures 85% of the "true" move
    prob_up_lagged = 0.5 + (prob_up - 0.5) * lag_factor

    # Ask prices (with small spread)
    spread = 0.01  # 1% spread
    ask_up = prob_up_lagged + spread / 2
    ask_down = 1.0 - prob_up_lagged + spread / 2

    # Clamp
    ask_up = max(0.03, min(0.98, ask_up))
    ask_down = max(0.03, min(0.98, ask_down))

    return ask_up, ask_down


def simulate_orderbook_skew_realistic(
    prices: list[Kline],
    current_idx: int,
    price_to_beat: float,
    time_remaining_s: float,
    window_duration_s: float,
    lag_ticks: int = 30,
) -> tuple[float, float]:
    """
    More realistic skew simulation using price history within the window.

    The market reacts with a lag of `lag_ticks` data points.
    So the orderbook reflects where the price WAS, not where it IS.
    """
    import math

    if price_to_beat == 0:
        return 0.50, 0.50

    # Current oracle price
    current_price = prices[current_idx].price

    # Lagged price (what the market "sees")
    lagged_idx = max(0, current_idx - lag_ticks)
    lagged_price = prices[lagged_idx].price

    # Market's perceived delta (lagged)
    lagged_delta_pct = (lagged_price - price_to_beat) / price_to_beat

    # Real delta (oracle)
    real_delta_pct = (current_price - price_to_beat) / price_to_beat

    sensitivity = 2500
    time_factor = 1.0 + (1.0 - time_remaining_s / window_duration_s) * 0.8

    # Market's view (based on lagged price)
    z_market = lagged_delta_pct * sensitivity * time_factor
    prob_up_market = 1 / (1 + math.exp(-z_market))
    prob_up_market = max(0.03, min(0.97, prob_up_market))

    spread = 0.01
    ask_up = prob_up_market + spread / 2
    ask_down = 1.0 - prob_up_market + spread / 2

    ask_up = max(0.03, min(0.98, ask_up))
    ask_down = max(0.03, min(0.98, ask_down))

    return ask_up, ask_down


# ──────────────────────────────────────────────────────────────
# Convergence strategy (standalone for backtest)
# ──────────────────────────────────────────────────────────────

@dataclass
class ConvergenceConfig:
    threshold_pct: float = 0.0005   # 5 basis points
    min_skew: float = 0.80          # expensive side >= 80¢
    max_cheap_price: float = 0.40   # cheap side <= 40¢
    window_start_s: float = 60.0    # check from 60s before expiry
    window_end_s: float = 20.0      # to 20s before expiry
    trade_size: float = 1.10        # $1.10 per trade


def check_convergence(
    current_price: float,
    price_to_beat: float,
    ask_up: float,
    ask_down: float,
    time_remaining_s: float,
    cfg: ConvergenceConfig,
) -> tuple[bool, str, float] | None:
    """
    Check if convergence entry conditions are met.

    Returns (should_enter, side_to_buy, entry_price) or None.
    """
    # Time window
    if time_remaining_s < cfg.window_end_s or time_remaining_s > cfg.window_start_s:
        return None

    # Delta check
    if price_to_beat == 0:
        return None
    delta_pct = abs((current_price - price_to_beat) / price_to_beat)
    if delta_pct > cfg.threshold_pct:
        return None

    # Skew check
    expensive = max(ask_up, ask_down)
    cheap = min(ask_up, ask_down)

    if expensive < cfg.min_skew:
        return None
    if cheap > cfg.max_cheap_price:
        return None

    # Buy the cheap side
    if ask_up <= ask_down:
        return True, "UP", ask_up
    else:
        return True, "DOWN", ask_down


# ──────────────────────────────────────────────────────────────
# Backtester
# ──────────────────────────────────────────────────────────────

def run_backtest(
    windows: list[MarketWindow],
    cfg: ConvergenceConfig,
    use_realistic_skew: bool = True,
    lag_ticks: int = 30,
) -> BacktestResult:
    """
    Run convergence strategy backtest over all windows.

    For each window, scans all ticks in the entry zone (20-60s before close)
    and checks convergence conditions. Takes first valid entry per window.
    """
    trades: list[Trade] = []
    convergence_detected = 0
    cumulative_pnl = 0.0
    peak_pnl = 0.0
    max_drawdown = 0.0

    for i, w in enumerate(windows):
        if len(w.prices) < 10:
            continue

        window_duration_s = w.duration_s
        entered = False

        for j, kline in enumerate(w.prices):
            time_elapsed_ms = kline.ts_ms - w.start_ms
            time_remaining_s = (w.end_ms - kline.ts_ms) / 1000

            if time_remaining_s > cfg.window_start_s or time_remaining_s < cfg.window_end_s:
                continue

            # Simulate orderbook
            if use_realistic_skew:
                ask_up, ask_down = simulate_orderbook_skew_realistic(
                    w.prices, j, w.price_to_beat, time_remaining_s, window_duration_s, lag_ticks
                )
            else:
                ask_up, ask_down = simulate_orderbook_skew(
                    kline.price, w.price_to_beat, time_remaining_s, window_duration_s
                )

            result = check_convergence(
                kline.price, w.price_to_beat,
                ask_up, ask_down, time_remaining_s, cfg,
            )

            if result is not None:
                _, side, entry_price = result
                convergence_detected += 1

                if entered:
                    continue  # one trade per window

                # Execute trade
                won = (side == w.outcome)
                if won:
                    pnl = (1.0 - entry_price) * cfg.trade_size
                else:
                    pnl = -entry_price * cfg.trade_size

                delta_pct = abs((kline.price - w.price_to_beat) / w.price_to_beat) if w.price_to_beat else 0

                trade = Trade(
                    window_idx=i,
                    side=side,
                    entry_price=entry_price,
                    time_remaining=time_remaining_s,
                    oracle_price=kline.price,
                    price_to_beat=w.price_to_beat,
                    delta_pct=delta_pct,
                    outcome=w.outcome,
                    pnl=pnl,
                    won=won,
                )
                trades.append(trade)
                entered = True

                cumulative_pnl += pnl
                peak_pnl = max(peak_pnl, cumulative_pnl)
                drawdown = peak_pnl - cumulative_pnl
                max_drawdown = max(max_drawdown, drawdown)

    # Compute stats
    wins = sum(1 for t in trades if t.won)
    losses = len(trades) - wins
    win_rate = wins / len(trades) if trades else 0
    avg_entry = sum(t.entry_price for t in trades) / len(trades) if trades else 0
    avg_win = sum(t.pnl for t in trades if t.won) / wins if wins else 0
    avg_loss = sum(t.pnl for t in trades if not t.won) / losses if losses else 0
    ev = cumulative_pnl / len(trades) if trades else 0

    return BacktestResult(
        symbol="",
        days=0,
        window_minutes=0,
        total_windows=len(windows),
        total_trades=len(trades),
        wins=wins,
        losses=losses,
        total_pnl=cumulative_pnl,
        avg_entry_price=avg_entry,
        avg_win_payout=avg_win,
        avg_loss=avg_loss,
        win_rate=win_rate,
        ev_per_trade=ev,
        max_drawdown=max_drawdown,
        convergence_opportunities=convergence_detected,
        trades=trades,
    )


def print_result(result: BacktestResult) -> None:
    """Pretty-print backtest results."""
    print("\n" + "=" * 70)
    print(f"📊 BACKTEST RESULTS: {result.symbol} ({result.window_minutes}m windows)")
    print("=" * 70)
    print(f"Period:           {result.days} days")
    print(f"Total windows:    {result.total_windows}")
    print(f"Convergence opps: {result.convergence_opportunities}")
    print(f"Trades taken:     {result.total_trades}")
    print(f"Opp rate:         {result.convergence_opportunities / result.total_windows * 100:.1f}% of windows" if result.total_windows else "N/A")
    print("-" * 70)
    print(f"Wins:             {result.wins}")
    print(f"Losses:           {result.losses}")
    print(f"Win rate:         {result.win_rate * 100:.1f}%")
    print(f"Avg entry price:  ${result.avg_entry_price:.4f}")
    print(f"Avg win payout:   ${result.avg_win_payout:+.4f}")
    print(f"Avg loss:         ${result.avg_loss:+.4f}")
    print("-" * 70)
    print(f"Total PnL:        ${result.total_pnl:+.2f}")
    print(f"EV per trade:     ${result.ev_per_trade:+.4f}")
    print(f"Max drawdown:     ${result.max_drawdown:.2f}")

    if result.total_pnl > 0:
        print(f"\n✅ PROFITABLE — ${result.total_pnl:+.2f} over {result.total_trades} trades")
    else:
        print(f"\n❌ UNPROFITABLE — ${result.total_pnl:+.2f} over {result.total_trades} trades")

    # Show first 10 trades
    if result.trades:
        print(f"\n📝 Sample trades (first 10 of {len(result.trades)}):")
        print(f"{'#':>4} {'Side':>4} {'Entry':>7} {'PnL':>8} {'Won':>4} {'ΔPct':>8} {'T_rem':>6}")
        for i, t in enumerate(result.trades[:10]):
            print(
                f"{i+1:4d} {t.side:>4} ${t.entry_price:.4f} "
                f"${t.pnl:+.4f} {'✅' if t.won else '❌':>4} "
                f"{t.delta_pct*100:.4f}% {t.time_remaining:.0f}s"
            )

    # PnL curve stats
    if result.trades:
        print(f"\n📈 PnL curve:")
        running = 0.0
        checkpoints = [len(result.trades) * p // 4 for p in range(1, 5)]
        for i, t in enumerate(result.trades):
            running += t.pnl
            if i in checkpoints:
                print(f"   After {i+1} trades: ${running:+.2f}")

    print("=" * 70)


# ──────────────────────────────────────────────────────────────
# Sensitivity analysis
# ──────────────────────────────────────────────────────────────

def run_sensitivity(windows: list[MarketWindow]) -> None:
    """Run backtest with different parameter combinations."""
    print("\n📊 SENSITIVITY ANALYSIS")
    print("=" * 90)
    print(f"{'Threshold':>10} {'MinSkew':>8} {'MaxCheap':>9} {'Lag':>4} {'Trades':>7} {'WinRate':>8} {'PnL':>10} {'EV/trade':>9}")
    print("-" * 90)

    for threshold in [0.0003, 0.0005, 0.0008, 0.001, 0.002]:
        for min_skew in [0.75, 0.80, 0.85]:
            for max_cheap in [0.30, 0.40]:
                for lag in [15, 30, 60]:
                    cfg = ConvergenceConfig(
                        threshold_pct=threshold,
                        min_skew=min_skew,
                        max_cheap_price=max_cheap,
                    )
                    result = run_backtest(windows, cfg, lag_ticks=lag)
                    if result.total_trades > 0:
                        print(
                            f"{threshold*10000:>8.1f}bp "
                            f"{min_skew:>8.2f} "
                            f"${max_cheap:>7.2f} "
                            f"{lag:>4d} "
                            f"{result.total_trades:>7d} "
                            f"{result.win_rate*100:>7.1f}% "
                            f"${result.total_pnl:>+9.2f} "
                            f"${result.ev_per_trade:>+8.4f}"
                        )

    print("=" * 90)


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Backtest convergence strategy")
    parser.add_argument("--symbol", default="BTCUSDT", help="Binance symbol (default: BTCUSDT)")
    parser.add_argument("--days", type=int, default=7, help="Days of history (default: 7)")
    parser.add_argument("--window", type=int, default=5, help="Window minutes (default: 5)")
    parser.add_argument("--interval", default="1m", choices=["1s", "1m"], help="Kline interval (default: 1m)")
    parser.add_argument("--threshold", type=float, default=0.0005, help="Convergence threshold (default: 0.0005 = 5bp)")
    parser.add_argument("--min-skew", type=float, default=0.80, help="Min expensive side price (default: 0.80)")
    parser.add_argument("--max-cheap", type=float, default=0.40, help="Max cheap side price (default: 0.40)")
    parser.add_argument("--lag", type=int, default=30, help="Market lag in ticks (default: 30)")
    parser.add_argument("--sensitivity", action="store_true", help="Run sensitivity analysis")
    parser.add_argument("--both", action="store_true", help="Run both BTC and ETH")
    args = parser.parse_args()

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=args.days)

    symbols = ["BTCUSDT", "ETHUSDT"] if args.both else [args.symbol]

    for symbol in symbols:
        print(f"\n{'='*70}")
        print(f"🚀 Backtest: {symbol} | {args.days} days | {args.window}m windows | {args.interval} klines")
        print(f"{'='*70}")

        # Fetch data
        klines = await fetch_all_klines(
            symbol=symbol,
            interval=args.interval,
            start_time=start_time,
            end_time=end_time,
        )

        if not klines:
            print(f"❌ No data for {symbol}")
            continue

        print(f"📊 Data: {len(klines)} klines, {klines[0].price:.2f} → {klines[-1].price:.2f}")

        # Generate windows
        windows = generate_windows(klines, window_minutes=args.window)
        print(f"📊 Generated {len(windows)} market windows")

        up_count = sum(1 for w in windows if w.outcome == "UP")
        print(f"   UP: {up_count} ({up_count/len(windows)*100:.1f}%) | DOWN: {len(windows)-up_count} ({(len(windows)-up_count)/len(windows)*100:.1f}%)")

        if args.sensitivity:
            run_sensitivity(windows)
        else:
            cfg = ConvergenceConfig(
                threshold_pct=args.threshold,
                min_skew=args.min_skew,
                max_cheap_price=args.max_cheap,
            )
            result = run_backtest(windows, cfg, lag_ticks=args.lag)
            result.symbol = symbol
            result.days = args.days
            result.window_minutes = args.window
            print_result(result)


if __name__ == "__main__":
    asyncio.run(main())
