#!/bin/bash
# Trade report: entry, oracle price vs target, result, totals
# Usage: ./report.sh [DB_PATH] [--strategy NAME] [--version VER] [--mode MODE] [--tickers BTC,ETH] [--min-price 0.14]

DB=""
STRATEGY=""
VERSION=""
MODE=""
TICKERS=""
MIN_PRICE=""

# Parse arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --strategy)  STRATEGY="$2"; shift 2 ;;
    --version)   VERSION="$2"; shift 2 ;;
    --mode)      MODE="$2"; shift 2 ;;
    --tickers)   TICKERS="$2"; shift 2 ;;
    --min-price) MIN_PRICE="$2"; shift 2 ;;
    *)
      if [[ -z "$DB" ]]; then DB="$1"; fi
      shift ;;
  esac
done

DB="${DB:-data/trades.db}"
cd "$(dirname "$0")/.." || exit 1

# Build dynamic WHERE clause
WHERE="t.action = 'buy'"
if [[ -n "$STRATEGY" ]]; then
  WHERE="$WHERE AND t.strategy = '$STRATEGY'"
fi
if [[ -n "$VERSION" ]]; then
  WHERE="$WHERE AND t.strategy_version = '$VERSION'"
fi
if [[ -n "$MODE" ]]; then
  WHERE="$WHERE AND t.mode = '$MODE'"
fi
if [[ -n "$TICKERS" ]]; then
  # Convert comma-separated to SQL IN clause
  IN_LIST=$(echo "$TICKERS" | sed "s/,/','/g")
  WHERE="$WHERE AND t.market_name IN ('$IN_LIST')"
fi
if [[ -n "$MIN_PRICE" ]]; then
  WHERE="$WHERE AND t.price >= $MIN_PRICE"
fi

sqlite3 -header -column "$DB" "
SELECT
  substr(t.timestamp_iso, 12, 8) as time,
  t.market_name as asset,
  t.side,
  printf('%.2f', t.price) as entry,
  printf('%.2f', d.oracle_price) as oracle,
  printf('%.2f', d.oracle_price - d.oracle_delta) as target,
  printf('%.4f%%', d.oracle_delta / d.oracle_price * 100) as delta_pct,
  printf('%.0fs', d.time_remaining) as t_left,
  COALESCE(t.strategy, 'convergence') as strat,
  COALESCE(t.mode, 'test') as mode,
  CASE
    WHEN p.close_reason LIKE 'resolved_win%' THEN '✅ WIN'
    WHEN p.close_reason LIKE 'resolved_loss%' THEN '❌ LOSS'
    WHEN p.close_reason IS NULL THEN '⏳ OPEN'
    ELSE '📊 ' || p.close_reason
  END as result,
  COALESCE(printf('%+.2f', p.pnl), '—') as pnl
FROM trades t
LEFT JOIN trade_decisions d ON d.timestamp = t.timestamp AND d.market_name = t.market_name AND d.action = 'buy'
LEFT JOIN dry_run_positions p ON p.trade_id = t.id
WHERE $WHERE
ORDER BY t.timestamp;

SELECT '─────────────────────────────────────────────────────────';

SELECT
  'ИТОГО: ' ||
  count(*) || ' сделок | ' ||
  sum(CASE WHEN p.close_reason LIKE 'resolved_win%' THEN 1 ELSE 0 END) || ' побед | ' ||
  sum(CASE WHEN p.close_reason LIKE 'resolved_loss%' THEN 1 ELSE 0 END) || ' проигрышей | ' ||
  sum(CASE WHEN p.close_reason IS NULL THEN 1 ELSE 0 END) || ' открытых | ' ||
  'PnL: ' || printf('%+.2f', COALESCE(sum(p.pnl), 0)) || ' USD | ' ||
  'Win rate: ' || printf('%.0f%%',
    CASE WHEN sum(CASE WHEN p.close_reason IS NOT NULL THEN 1 ELSE 0 END) > 0
    THEN sum(CASE WHEN p.close_reason LIKE 'resolved_win%' THEN 1 ELSE 0 END) * 100.0 /
         sum(CASE WHEN p.close_reason IS NOT NULL THEN 1 ELSE 0 END)
    ELSE 0 END
  ) as summary
FROM trades t
LEFT JOIN dry_run_positions p ON p.trade_id = t.id
WHERE $WHERE;

SELECT '─────────────────────────────────────────────────────────';

SELECT
  'БЕЗ LOW_PRICE: ' ||
  count(*) || ' сделок | ' ||
  sum(CASE WHEN p.close_reason LIKE 'resolved_win%' THEN 1 ELSE 0 END) || ' побед | ' ||
  sum(CASE WHEN p.close_reason LIKE 'resolved_loss%' THEN 1 ELSE 0 END) || ' проигрышей | ' ||
  sum(CASE WHEN p.close_reason IS NULL THEN 1 ELSE 0 END) || ' открытых | ' ||
  'PnL: ' || printf('%+.2f', COALESCE(sum(p.pnl), 0)) || ' USD | ' ||
  'Win rate: ' || printf('%.0f%%',
    CASE WHEN sum(CASE WHEN p.close_reason IS NOT NULL THEN 1 ELSE 0 END) > 0
    THEN sum(CASE WHEN p.close_reason LIKE 'resolved_win%' THEN 1 ELSE 0 END) * 100.0 /
         sum(CASE WHEN p.close_reason IS NOT NULL THEN 1 ELSE 0 END)
    ELSE 0 END
  ) as summary
FROM trades t
LEFT JOIN dry_run_positions p ON p.trade_id = t.id
WHERE $WHERE
  AND NOT (t.price > 0 AND t.price < 0.10 AND t.reason = 'convergence');

SELECT '─────────────────────────────────────────────────────────';

SELECT
  'БЕЗ SOL: ' ||
  count(*) || ' сделок | ' ||
  sum(CASE WHEN p.close_reason LIKE 'resolved_win%' THEN 1 ELSE 0 END) || ' побед | ' ||
  sum(CASE WHEN p.close_reason LIKE 'resolved_loss%' THEN 1 ELSE 0 END) || ' проигрышей | ' ||
  sum(CASE WHEN p.close_reason IS NULL THEN 1 ELSE 0 END) || ' открытых | ' ||
  'PnL: ' || printf('%+.2f', COALESCE(sum(p.pnl), 0)) || ' USD | ' ||
  'Win rate: ' || printf('%.0f%%',
    CASE WHEN sum(CASE WHEN p.close_reason IS NOT NULL THEN 1 ELSE 0 END) > 0
    THEN sum(CASE WHEN p.close_reason LIKE 'resolved_win%' THEN 1 ELSE 0 END) * 100.0 /
         sum(CASE WHEN p.close_reason IS NOT NULL THEN 1 ELSE 0 END)
    ELSE 0 END
  ) as summary
FROM trades t
LEFT JOIN dry_run_positions p ON p.trade_id = t.id
WHERE $WHERE
  AND t.market_name != 'SOL';

SELECT
  'БЕЗ SOL + БЕЗ LOW_PRICE: ' ||
  count(*) || ' сделок | ' ||
  sum(CASE WHEN p.close_reason LIKE 'resolved_win%' THEN 1 ELSE 0 END) || ' побед | ' ||
  sum(CASE WHEN p.close_reason LIKE 'resolved_loss%' THEN 1 ELSE 0 END) || ' проигрышей | ' ||
  sum(CASE WHEN p.close_reason IS NULL THEN 1 ELSE 0 END) || ' открытых | ' ||
  'PnL: ' || printf('%+.2f', COALESCE(sum(p.pnl), 0)) || ' USD | ' ||
  'Win rate: ' || printf('%.0f%%',
    CASE WHEN sum(CASE WHEN p.close_reason IS NOT NULL THEN 1 ELSE 0 END) > 0
    THEN sum(CASE WHEN p.close_reason LIKE 'resolved_win%' THEN 1 ELSE 0 END) * 100.0 /
         sum(CASE WHEN p.close_reason IS NOT NULL THEN 1 ELSE 0 END)
    ELSE 0 END
  ) as summary
FROM trades t
LEFT JOIN dry_run_positions p ON p.trade_id = t.id
WHERE $WHERE
  AND t.market_name != 'SOL'
  AND NOT (t.price > 0 AND t.price < 0.10 AND t.reason = 'convergence');

SELECT '─────────────────────────────────────────────────────────';

SELECT
  'ТОЛЬКО BTC+ETH ЦЕНА>=0.14: ' ||
  count(*) || ' сделок | ' ||
  sum(CASE WHEN p.close_reason LIKE 'resolved_win%' THEN 1 ELSE 0 END) || ' побед | ' ||
  sum(CASE WHEN p.close_reason LIKE 'resolved_loss%' THEN 1 ELSE 0 END) || ' проигрышей | ' ||
  sum(CASE WHEN p.close_reason IS NULL THEN 1 ELSE 0 END) || ' открытых | ' ||
  'PnL: ' || printf('%+.2f', COALESCE(sum(p.pnl), 0)) || ' USD | ' ||
  'Win rate: ' || printf('%.0f%%',
    CASE WHEN sum(CASE WHEN p.close_reason IS NOT NULL THEN 1 ELSE 0 END) > 0
    THEN sum(CASE WHEN p.close_reason LIKE 'resolved_win%' THEN 1 ELSE 0 END) * 100.0 /
         sum(CASE WHEN p.close_reason IS NOT NULL THEN 1 ELSE 0 END)
    ELSE 0 END
  ) as summary
FROM trades t
LEFT JOIN dry_run_positions p ON p.trade_id = t.id
WHERE $WHERE
  AND t.market_name IN ('BTC', 'ETH')
  AND t.price >= 0.14;

SELECT
  'ТОЛЬКО BTC+ETH ЦЕНА>=0.10: ' ||
  count(*) || ' сделок | ' ||
  sum(CASE WHEN p.close_reason LIKE 'resolved_win%' THEN 1 ELSE 0 END) || ' побед | ' ||
  sum(CASE WHEN p.close_reason LIKE 'resolved_loss%' THEN 1 ELSE 0 END) || ' проигрышей | ' ||
  sum(CASE WHEN p.close_reason IS NULL THEN 1 ELSE 0 END) || ' открытых | ' ||
  'PnL: ' || printf('%+.2f', COALESCE(sum(p.pnl), 0)) || ' USD | ' ||
  'Win rate: ' || printf('%.0f%%',
    CASE WHEN sum(CASE WHEN p.close_reason IS NOT NULL THEN 1 ELSE 0 END) > 0
    THEN sum(CASE WHEN p.close_reason LIKE 'resolved_win%' THEN 1 ELSE 0 END) * 100.0 /
         sum(CASE WHEN p.close_reason IS NOT NULL THEN 1 ELSE 0 END)
    ELSE 0 END
  ) as summary
FROM trades t
LEFT JOIN dry_run_positions p ON p.trade_id = t.id
WHERE $WHERE
  AND t.market_name IN ('BTC', 'ETH')
  AND t.price >= 0.10;

SELECT '─────────────────────────────────────────────────────────';

SELECT
  'С SOL ЦЕНА>=0.14: ' ||
  count(*) || ' сделок | ' ||
  sum(CASE WHEN p.close_reason LIKE 'resolved_win%' THEN 1 ELSE 0 END) || ' побед | ' ||
  sum(CASE WHEN p.close_reason LIKE 'resolved_loss%' THEN 1 ELSE 0 END) || ' проигрышей | ' ||
  sum(CASE WHEN p.close_reason IS NULL THEN 1 ELSE 0 END) || ' открытых | ' ||
  'PnL: ' || printf('%+.2f', COALESCE(sum(p.pnl), 0)) || ' USD | ' ||
  'Win rate: ' || printf('%.0f%%',
    CASE WHEN sum(CASE WHEN p.close_reason IS NOT NULL THEN 1 ELSE 0 END) > 0
    THEN sum(CASE WHEN p.close_reason LIKE 'resolved_win%' THEN 1 ELSE 0 END) * 100.0 /
         sum(CASE WHEN p.close_reason IS NOT NULL THEN 1 ELSE 0 END)
    ELSE 0 END
  ) as summary
FROM trades t
LEFT JOIN dry_run_positions p ON p.trade_id = t.id
WHERE $WHERE
  AND t.price >= 0.14;

SELECT
  'С SOL ЦЕНА>=0.10: ' ||
  count(*) || ' сделок | ' ||
  sum(CASE WHEN p.close_reason LIKE 'resolved_win%' THEN 1 ELSE 0 END) || ' побед | ' ||
  sum(CASE WHEN p.close_reason LIKE 'resolved_loss%' THEN 1 ELSE 0 END) || ' проигрышей | ' ||
  sum(CASE WHEN p.close_reason IS NULL THEN 1 ELSE 0 END) || ' открытых | ' ||
  'PnL: ' || printf('%+.2f', COALESCE(sum(p.pnl), 0)) || ' USD | ' ||
  'Win rate: ' || printf('%.0f%%',
    CASE WHEN sum(CASE WHEN p.close_reason IS NOT NULL THEN 1 ELSE 0 END) > 0
    THEN sum(CASE WHEN p.close_reason LIKE 'resolved_win%' THEN 1 ELSE 0 END) * 100.0 /
         sum(CASE WHEN p.close_reason IS NOT NULL THEN 1 ELSE 0 END)
    ELSE 0 END
  ) as summary
FROM trades t
LEFT JOIN dry_run_positions p ON p.trade_id = t.id
WHERE $WHERE
  AND t.price >= 0.10;
"
