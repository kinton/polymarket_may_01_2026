# Backlog задач по улучшению проекта

В этот список добавлены задачи на основе code review и security analysis.

## ✅ Production-Ready Status

Система готова к запуску на проде:
- ✅ Hybrid market discovery (Bitcoin/Ethereum + custom queries via MARKET_QUERIES)
- ✅ Race condition fix + FOK verification
- ✅ Pre-trade balance check
- ✅ Logger handler accumulation fix
- ✅ docker-compose.yml configured with MARKET_QUERIES
- ✅ All critical bugs resolved

**Используемые рынки:** 
- Crypto: Bitcoin, Ethereum, BTC, ETH, Solana, SOL (5m/15m "Up or Down")
- Political: Trump, Election, President
- Economic: Fed
- Binary: Will

## 🔴 High Priority (Critical Fixes)
Эти задачи влияют на безопасность средств и корректность исполнения сделок.

- [x] **Fix Race Condition в `execute_order`**
  - В `hft_trader.py`: флаг `self._order_submitted` ставится *до* вызова `execute_order`. Если ордер падает с ошибкой, повторной попытки не будет.
  - *Решение*: Переместить установку флага в блок `try` после успешного исполнения или в `else` (если нет exception).
  - *Статус*: ✅ Resolved - флаг теперь устанавливается после successful execution

- [x] **Verify Order Filled (FOK Verification)**
  - Сейчас мы делаем `create_order`, но не проверяем его статус.
  - *Решение*: После отправки ордера запрашивать его статус через `get_order(order_id)`. Логировать успех/неудачу. Если FOK отменен — алерт.
  - *Статус*: ✅ Resolved - добавлен метод `verify_order()` в `hft_trader.py`

- [x] **Pre-trade Balance Check**
  - Бот может выставить ордер без денег.
  - *Решение*: В `LastSecondTrader` перед торгами (за 60 сек) проверять `get_balance()` для USDC. Если недостаточно — `logger.error` и выход.
  - *Статус*: ✅ **COMPLETED 2026-02-01** - добавлен метод `_check_balance()` с интеграцией в `check_trigger()`, создан test suite (10 tests)

- [x] **Fix USDC Balance Conversion**
  - API возвращает баланс в 6-десятичных единицах (micro-USDC), но код не делал конвертацию.
  - *Решение*: Добавить `/1e6` для конвертации balance и allowance в доллары.
  - *Статус*: ✅ **COMPLETED 2026-02-03** - исправлена конвертация, теперь отображается реальный баланс ($0.55 вместо $550,884)
- [x] **Implement Claim Winnings**
  - После выигрыша нужно забрать USDC из выигрышных позиций.
  - *Решение*: Добавить web3.py для взаимодействия с CTF контрактом, создать скрипты для claim.
  - *Статус*: ✅ **COMPLETED 2026-02-03** - добавлен position_settler.py для автоматического claim, документация в CLAIM-GUIDE.md
- [x] **Clean Up Debug/Obsolete Scripts**
  - Много дублирующихся и устаревших скриптов для диагностики (debug_*, claim_*, check_proxy*, check_all_positions.py).
  - *Решение*: Удалить все debug скрипты и дубликаты, оставить только essential: check_balance.py, approve.py, position_settler.py
  - *Статус*: ✅ **COMPLETED 2026-02-03** - удалено 13 скриптов (включая check_all_positions.py), обновлена документация
## 🟡 Medium Priority (Reliability & Stability)
Задачи для повышения стабильности работы 24/7.

- [x] **Fix Logger Handler Accumulation**
  - В `_setup_logger` (main/finder) хендлеры добавляются при каждом вызове. Логи дублируются.
  - *Решение*: Проверять `if logger.hasHandlers(): logger.handlers.clear()` перед добавлением.
  - *Статус*: ✅ **COMPLETED 2026-02-01** - добавлены проверки в `setup_logging()`, создан test suite (7 tests)

## 🟢 Low Priority (Enhancements)
Улучшения качества кода и новые фичи.

- [ ] **Strict Type Hints**
  - Убрать `Any` и `# type: ignore`.
  - *Решение*: Добавить нормальные типы для возвращаемых значений API.

- [ ] **Position Settlement & P&L Collection**
  - После покупки токенов по $0.99 нужно собирать profit после резолюции рынка.
  - *Проблема*: Сейчас бот покупает токены, но не выкупает (redeem) их после завершения. Winning tokens конвертируются в $1.00 автоматически Polymarket'ом, но нужно отслеживать P&L.
  - *Архитектура*:
    - **Вариант A (рекомендуется)**: Создать отдельный модуль `position_settler.py`
      - Независимый процесс, запускается периодически (каждые 5 мин)
      - Сканирует открытые позиции через `GET /positions`
      - Проверяет статус резолюции рынков через `GET /markets/{condition_id}`
      - Логирует P&L в `log/pnl.csv` (Timestamp, Market, Side, Entry Price, Exit Value, Profit/Loss)
      - Может работать как отдельный скрипт: `uv run python position_settler.py`
    - **Вариант B**: Интегрировать в `hft_trader.py`
      - После `execute_order()` ждать резолюцию (5-10 мин)
      - Минусы: блокирует trader, нет batch processing
  - *Реализация (Вариант A)*:
    1. Создать `position_settler.py` с классом `PositionSettler`
    2. Методы:
       - `get_open_positions()` - через CLOB API
       - `check_market_resolution(condition_id)` - статус (pending/resolved/closed)
       - `calculate_pnl(position)` - (exit_value - entry_cost)
       - `log_pnl_to_csv(position, pnl)` - запись в `log/pnl.csv`
    3. Main loop: каждые 5 минут проверять позиции
    4. CLI: `python position_settler.py --once` (single run) или `--daemon` (continuous)
  - *P&L Tracking Format* (`log/pnl.csv`):
    ```csv
    timestamp,market_title,condition_id,side,entry_price,tokens_bought,cost,exit_value,profit_loss,roi_percent
    2026-02-01 12:05:23,Bitcoin 15m,0xabc...,YES,0.99,10.2,10.10,10.20,+0.10,+0.99%
    ```
  - *API Endpoints*:
    - `GET /positions?asset_type=CONDITIONAL` - список позиций
    - `GET /markets/{condition_id}` - market info + resolution status
    - Note: Polymarket автоматически конвертирует winning tokens в USDC после resolution
  - *Дополнительные фичи*:
    - Email/Telegram notifications о P&L
    - Daily/Weekly summaries
    - Automatic reinvestment калькулятор
  - *Приоритет*: Средний (можно отслеживать P&L вручную через UI, но automation улучшит workflow)
