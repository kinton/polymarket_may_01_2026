#!/usr/bin/env bash
# LIVE: реальные деньги!
# BTC+ETH, цена >= $0.14, convergence v1
set -e

cd "$(dirname "$0")/.."

echo "⚠️  LIVE TRADING MODE — REAL MONEY"
echo "Strategy: convergence v1 | Tickers: BTC,ETH | Min price: 0.14"
echo ""
echo "Запуск через 5 секунд... (Ctrl+C для отмены)"
sleep 5

setsid bash -c 'nohup uv run python main.py \
  --mode live \
  --tickers BTC,ETH \
  --min-price 0.14 \
  --strategy convergence \
  --strategy-version v1 \
  --poll-interval 90 \
  --max-traders 3 \
  --size 1.0 \
  --no-health-server \
  > /tmp/polymarket-live.log 2>&1 &'

sleep 2
echo "PID: $(pgrep -f 'main.py.*--mode live' | head -1)"
echo "Log: tail -f /tmp/polymarket-live.log"
