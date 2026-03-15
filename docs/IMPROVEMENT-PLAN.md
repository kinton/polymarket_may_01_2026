# Polymarket Trading Bot — Improvement Plan

> Generated: 2026-03-15
> Status: Planning (not yet implemented)

---

## 1. New Tokens to Add

### Research Results (Polymarket "Up or Down" active markets)

| Token     | In TICKER_MAP? | In VALID_TICKERS? | In Strategy? | Status       |
|-----------|----------------|--------------------|--------------|--------------|
| Bitcoin   | Yes (BTC)      | Yes                | Yes          | Active       |
| Ethereum  | Yes (ETH)      | Yes                | Yes          | Active       |
| Solana    | Yes (SOL)      | Yes                | No (removed) | Finder only  |
| XRP       | Yes (XRP)      | Yes                | No           | Finder only  |
| BNB       | **No**         | **No**             | No           | **NEW**      |
| Dogecoin  | **No**         | **No**             | No           | **NEW**      |
| HYPE      | **No**         | **No**             | No           | **NEW**      |

### Plan: Add BNB, Dogecoin (DOGE), HYPE

**Files to change:**

| File | Change |
|------|--------|
| `src/gamma_15m_finder.py` | Add to `TICKER_MAP`: `"BNB": ["BNB"]`, `"DOGE": ["Dogecoin", "DOGE"]`, `"HYPE": ["HYPE"]` |
| `main.py:565` | Add `"BNB"`, `"DOGE"`, `"HYPE"` to `VALID_TICKERS` |

**Steps:**
1. Add entries to `TICKER_MAP` in `src/gamma_15m_finder.py`
2. Add to `VALID_TICKERS` in `main.py`
3. Do NOT add to `ConvergenceV1.SUPPORTED_TICKERS` yet — dry-run data needed first
4. Run with `--universe BTC,ETH,BNB,DOGE,HYPE` to start collecting data
5. After 50+ trades per token, analyze win rates before enabling in strategy

**Complexity:** S | **Priority:** P1

---

## 2. Telegram Alerts: Add Strategy Name/Version/Mode

### Current State

Alert messages in `src/alerts.py` contain trade data (market, side, price, PnL) but **no strategy metadata**. When running multiple strategies or modes simultaneously, it's impossible to tell which instance generated a trade from the Telegram message alone.

### Plan: Enrich alert context

**Files to change:**

| File | Change |
|------|--------|
| `src/alerts.py` | Add optional `context: dict` param to `send_trade_alert()`, `send_stop_loss_alert()`, `send_take_profit_alert()`. Render as header line: `[convergence/v1/dryrun]` |
| `src/hft_trader.py` | Pass `{"strategy": name, "version": ver, "mode": mode}` when calling alert methods |
| `src/position_settler.py` | Pass strategy context in resolution/redeem alerts |
| `src/trading/alert_dispatcher.py` | Thread context through to underlying sender |

**Alert format change (example):**

```
Before:  🚀 Trade executed: BTC (YES) @ $0.2500 | Size: $5.00
After:   [convergence/v1/dryrun] 🚀 Trade executed: BTC (YES) @ $0.2500 | Size: $5.00
```

**Steps:**
1. Add `context: dict | None = None` param to `TelegramAlertSender.send_trade_alert()` and peers
2. If context provided, prepend `[{strategy}/{version}/{mode}]` to message
3. Mirror in `SlackAlertSender` and `AlertManager` broadcast methods
4. Pass context from `hft_trader.py` where alerts are dispatched (strategy name/version available from the loaded strategy object, mode from `self.dry_run`)
5. Pass context from `position_settler.py` (can use "settler" as strategy name)

**Complexity:** S | **Priority:** P0

---

## 3. Analytics: Auto-Discover DBs

### Current State

`scripts/report.sh` takes a single `DB_PATH` argument (default: `data/trades.db`). With the recent `--db-path` CLI arg for parallel instances, there are now multiple DBs:

```
data/trades.db                    (legacy)
data/convergence-v1-live.db       (live strategy instance)
data/convergence-v1-dryrun.db     (dryrun strategy instance)
```

Running reports requires manually specifying each DB path.

### Plan: Auto-discover and aggregate

**Files to change:**

| File | Change |
|------|--------|
| `scripts/report.sh` | Add `--all` flag that globs `data/*.db`, runs report per DB, then prints aggregate summary |
| `scripts/report.sh` | Print DB name as header before each report section |

**Steps:**
1. Add `--all` flag parsing to the argument parser at top of `report.sh`
2. When `--all`: glob `data/*.db`, skip `-shm`/`-wal` files
3. For each DB: print `═══ {db_name} ═══` header, run existing SQL queries
4. After per-DB reports: run aggregate query using `ATTACH DATABASE` to combine results
5. Alternatively (simpler): just loop and print, skip cross-DB aggregation in v1

**Complexity:** S | **Priority:** P2

---

## 4. Health Watchdog: Alert if No Trades in N Hours

### Current State

The bot has a basic healthcheck server (`src/healthcheck.py`) for liveness probes but **no staleness detection**. If the bot silently stops finding markets or crashes in a non-obvious way, there's no alert.

### Plan: Add trade staleness watchdog

**Files to change:**

| File | Change |
|------|--------|
| `src/alerts.py` | Add `send_watchdog_alert(hours_since_last: float, expected_max: float)` method |
| `src/healthcheck.py` (or new `src/watchdog.py`) | Add async watchdog loop that queries last trade timestamp from DB |
| `main.py` | Launch watchdog as background task alongside the main trading loop |
| `src/trading/trade_db.py` | Add `get_last_trade_timestamp() -> datetime | None` query |

**Steps:**
1. Add `get_last_trade_timestamp()` to `TradeDatabase` — simple `SELECT MAX(timestamp) FROM trades`
2. Create watchdog coroutine: every 30 min, check last trade time
3. If `now - last_trade > N hours` (configurable, default 4h), send TG alert
4. Suppress repeated alerts (only fire once per staleness event, re-arm after a new trade)
5. Also alert on startup: "Bot started, watchdog active"
6. Wire into `main.py` as `asyncio.create_task(watchdog_loop(db, alert_mgr))`

**Alert format:**
```
⚠️ WATCHDOG: No trades in 4.2 hours (threshold: 4h)
Last trade: 2026-03-15 08:23 UTC | DB: convergence-v1-dryrun.db
```

**Complexity:** M | **Priority:** P0

---

## 5. Oracle Compatibility for New Tokens

### Current State

The oracle system uses **Chainlink prices via Polymarket's RTDS WebSocket** (`wss://ws-live-data.polymarket.com`). The `RtdsClient` in `src/updown_prices.py` subscribes to `crypto_prices_chainlink` topic and receives prices as `{TICKER}/USD` symbols (e.g., `BTC/USD`).

The `guess_chainlink_symbol()` function converts tickers to the `{ticker}/usd` format. This means any token with a Chainlink feed on Polymarket's RTDS should work automatically.

### Compatibility Assessment

| Token | Chainlink Feed Expected | Risk |
|-------|------------------------|------|
| BNB   | `bnb/usd` — Chainlink has BNB/USD feed, likely available on RTDS | Low |
| DOGE  | `doge/usd` — Chainlink has DOGE/USD feed, likely available on RTDS | Low |
| HYPE  | `hype/usd` — HYPE (Hyperliquid) is newer, Chainlink feed uncertain | **High** |

### Plan: Validate and handle missing oracle feeds

**Files to change:**

| File | Change |
|------|--------|
| `src/updown_prices.py` | Add `ORACLE_SYMBOL_OVERRIDES` dict for non-standard mappings (e.g., if HYPE uses different symbol) |
| `src/trading/oracle_guard_manager.py` | Graceful degradation: if no oracle data after N seconds, allow trade with warning (currently blocks forever) |
| `src/oracle_tracker.py` | No changes needed — already generic |

**Steps:**
1. **Validate feeds exist**: Run bot in dry-run with `--universe BNB,DOGE,HYPE` and monitor logs for oracle price reception
2. If BNB/DOGE feeds work: no code changes needed (just add to TICKER_MAP)
3. If HYPE feed missing: either
   - a) Add fallback price source (e.g., Binance spot `crypto_prices` topic already streamed via RTDS), or
   - b) Skip HYPE until Chainlink feed is available, or
   - c) Add `ORACLE_OPTIONAL_TICKERS` set — for these tickers, oracle guard passes with a warning instead of blocking
4. Add oracle feed validation on startup: log which tickers have live oracle data within first 60s

**Complexity:** M (if all feeds exist) / L (if HYPE needs fallback) | **Priority:** P1

---

## Summary

| # | Improvement | Complexity | Priority | Dependencies |
|---|------------|------------|----------|--------------|
| 1 | New tokens (BNB, DOGE, HYPE) | S | P1 | #5 (oracle validation) |
| 2 | TG alerts with strategy context | S | P0 | None |
| 3 | Analytics auto-discover DBs | S | P2 | None |
| 4 | Health watchdog | M | P0 | None |
| 5 | Oracle compatibility for new tokens | M-L | P1 | None |

**Recommended execution order:** #2 → #4 → #5 → #1 → #3
