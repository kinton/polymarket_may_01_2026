#!/usr/bin/env bash
# Dry run: тест без реальных денег
# BTC+ETH, цена >= $0.14, convergence v1
set -e

cd "$(dirname "$0")/.."

echo "Starting DRY RUN bot..."
echo "Strategy: convergence v1 | Tickers: BTC,ETH | Min price: 0.14"

setsid bash -c 'nohup uv run python main.py \
  --mode test \
  --tickers BTC,ETH \
  --min-price 0.14 \
  --strategy convergence \
  --strategy-version v1 \
  --poll-interval 90 \
  --max-traders 3 \
  --size 1.0 \
  --no-health-server \
  > /tmp/polymarket-dryrun.log 2>&1 &'

sleep 2
echo "PID: $(pgrep -f 'main.py.*--mode test' | head -1)"
echo "Log: tail -f /tmp/polymarket-dryrun.log"
