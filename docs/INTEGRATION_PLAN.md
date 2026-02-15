# План интеграции SQLite и активации неподключённых модулей

> Дата: 2026-02-14  
> Статус: ПЛАН (код не написан)

---

## Текущая архитектура (краткий обзор)

- **hft_trader.py** (`LastSecondTrader`) — монолитный класс, оркестрирует всё: WS-подключение, orderbook, trigger logic, ордера
- **Менеджеры** уже выделены: `PositionManager`, `StopLossManager`, `RiskManager`, `OrderExecutionManager`, `AlertDispatcher`, `OracleGuardManager`
- **Неактивные модули**: `PositionPersister` (написан, не подключён), `EventRecorder`/`EventReplayer` (написаны, не подключены), `OrderbookWS` (написан, не подключён)
- **Хранилище**: JSON файлы (`daily_limits.json`, `alert_history.json`, `position_*.json`), JSONL (replay), логи
- **Тесты**: unit (`test_*.py`) + integration (`tests/integration/`)

---

## A. TradeDatabase — новый модуль `src/trading/trade_db.py`

### Схема таблиц

#### 1. `trades`
| Поле | Тип | Описание |
|------|-----|----------|
| id | INTEGER PRIMARY KEY AUTOINCREMENT | |
| timestamp | REAL NOT NULL | Unix timestamp |
| timestamp_iso | TEXT NOT NULL | ISO 8601 |
| market_name | TEXT NOT NULL | Короткое имя (BTC, ETH) |
| condition_id | TEXT NOT NULL | Polymarket condition ID |
| action | TEXT NOT NULL | 'buy' / 'sell' |
| side | TEXT NOT NULL | 'YES' / 'NO' |
| price | REAL NOT NULL | Цена исполнения |
| amount | REAL NOT NULL | Размер сделки в USDC |
| order_id | TEXT | ID ордера от Polymarket |
| status | TEXT | 'filled' / 'killed' / 'canceled' |
| pnl | REAL | P&L сделки (для sell) |
| pnl_pct | REAL | P&L в процентах |
| reason | TEXT | 'trigger' / 'stop-loss' / 'take-profit' / 'early-entry' |
| dry_run | INTEGER NOT NULL DEFAULT 1 | 0/1 |
| created_at | TEXT DEFAULT CURRENT_TIMESTAMP | |

**Индексы:**
- `idx_trades_timestamp` ON (timestamp)
- `idx_trades_market` ON (market_name)
- `idx_trades_condition` ON (condition_id)
- `idx_trades_date` ON (date(timestamp_iso))

#### 2. `positions`
| Поле | Тип | Описание |
|------|-----|----------|
| id | INTEGER PRIMARY KEY AUTOINCREMENT | |
| condition_id | TEXT NOT NULL UNIQUE | Один на market |
| market_name | TEXT NOT NULL | |
| side | TEXT NOT NULL | 'YES' / 'NO' |
| entry_price | REAL NOT NULL | |
| trailing_stop_price | REAL | |
| is_open | INTEGER NOT NULL DEFAULT 1 | |
| opened_at | REAL NOT NULL | Unix ts |
| closed_at | REAL | |
| close_reason | TEXT | 'stop-loss' / 'take-profit' / 'market-close' |
| updated_at | TEXT DEFAULT CURRENT_TIMESTAMP | |

**Индексы:**
- `idx_positions_open` ON (is_open) WHERE is_open = 1
- `idx_positions_condition` ON (condition_id)

#### 3. `order_book_snapshots`
| Поле | Тип | Описание |
|------|-----|----------|
| id | INTEGER PRIMARY KEY AUTOINCREMENT | |
| timestamp | REAL NOT NULL | |
| condition_id | TEXT NOT NULL | |
| best_ask_yes | REAL | |
| best_bid_yes | REAL | |
| best_ask_yes_size | REAL | |
| best_bid_yes_size | REAL | |
| best_ask_no | REAL | |
| best_bid_no | REAL | |
| best_ask_no_size | REAL | |
| best_bid_no_size | REAL | |
| winning_side | TEXT | |
| time_remaining | REAL | |

**Индексы:**
- `idx_ob_ts` ON (timestamp)
- `idx_ob_condition_ts` ON (condition_id, timestamp)

> ⚠️ Высокочастотная таблица — записи каждые 0.5–1с. Использовать batch INSERT (буфер 50–100 записей), авто-очистка >7 дней.

#### 4. `alerts`
| Поле | Тип | Описание |
|------|-----|----------|
| id | INTEGER PRIMARY KEY AUTOINCREMENT | |
| timestamp | REAL NOT NULL | |
| alert_type | TEXT NOT NULL | 'trade' / 'stop_loss' / 'take_profit' / 'oracle_guard' / 'daily_summary' |
| level | TEXT NOT NULL | 'INFO' / 'WARNING' / 'CRITICAL' |
| market_name | TEXT | |
| details_json | TEXT | JSON blob с деталями |
| created_at | TEXT DEFAULT CURRENT_TIMESTAMP | |

**Индексы:**
- `idx_alerts_ts` ON (timestamp)
- `idx_alerts_type` ON (alert_type)

#### 5. `daily_stats`
| Поле | Тип | Описание |
|------|-----|----------|
| date | TEXT PRIMARY KEY | 'YYYY-MM-DD' |
| initial_balance | REAL | |
| current_pnl | REAL NOT NULL DEFAULT 0 | |
| total_trades | INTEGER NOT NULL DEFAULT 0 | |
| winning_trades | INTEGER NOT NULL DEFAULT 0 | |
| losing_trades | INTEGER NOT NULL DEFAULT 0 | |
| total_volume | REAL NOT NULL DEFAULT 0 | |
| updated_at | TEXT DEFAULT CURRENT_TIMESTAMP | |

#### 6. `events` (для replay)
| Поле | Тип | Описание |
|------|-----|----------|
| id | INTEGER PRIMARY KEY AUTOINCREMENT | |
| session_id | TEXT NOT NULL | UUID сессии записи |
| timestamp | REAL NOT NULL | |
| event_type | TEXT NOT NULL | 'book_update' / 'trigger_check' / 'trade' / 'price_change' / 'stop_loss' / 'session_start' / 'session_end' |
| condition_id | TEXT | |
| market_name | TEXT | |
| data_json | TEXT NOT NULL | JSON blob |

**Индексы:**
- `idx_events_session` ON (session_id)
- `idx_events_session_ts` ON (session_id, timestamp)
- `idx_events_type` ON (event_type)

### Миграции
- Таблица `schema_version` (version INTEGER, applied_at TEXT)
- Начальная версия = 1 (все 6 таблиц выше)
- При старте: проверить версию, применить недостающие миграции
- Миграции как Python-функции в списке `MIGRATIONS = [(1, create_v1), (2, add_column_x), ...]`

### Thread safety / asyncio
- Использовать `aiosqlite` для неблокирующих операций
- Один экземпляр `TradeDatabase` на процесс
- WAL mode (`PRAGMA journal_mode=WAL`) для concurrent reads
- Для orderbook snapshots: in-memory буфер → batch flush каждые 1–2с
- Connection: single connection per TradeDatabase instance (aiosqlite handles this)

### API класса TradeDatabase
```
class TradeDatabase:
    async def initialize(db_path) → TradeDatabase
    async def close()
    
    # Trades
    async def insert_trade(...)
    async def get_trades(market=None, date=None, limit=100) → list
    
    # Positions
    async def save_position(condition_id, state_dict)
    async def load_position(condition_id) → dict | None
    async def close_position(condition_id, reason)
    async def get_open_positions() → list
    
    # Orderbook (buffered)
    async def buffer_orderbook_snapshot(...)
    async def flush_orderbook_buffer()
    
    # Alerts
    async def insert_alert(...)
    async def get_alerts(since=None, type=None) → list
    
    # Daily stats
    async def get_or_create_daily_stats(date) → dict
    async def update_daily_stats(date, pnl_delta, trade_count_delta)
    
    # Events (replay)
    async def insert_event(session_id, ...)
    async def get_events(session_id) → list
    async def list_sessions() → list
    
    # Maintenance
    async def cleanup_old_snapshots(days=7)
    async def migrate()
```

---

## B. Активация PositionPersist

### Текущее состояние
- `PositionPersister` в `position_persist.py` полностью написан (save/load/remove через JSON)
- Нигде не импортируется и не используется в `hft_trader.py`
- `PositionManager` (`position_manager.py`) хранит состояние только в памяти

### План подключения

1. **Добавить `PositionPersister` в `LastSecondTrader.__init__`** (после создания `position_manager`, ~строка 120):
   ```
   self.position_persister = PositionPersister(
       condition_id=condition_id,
       logger=self.logger,
   )
   ```

2. **Восстановление позиции при старте** — в `__init__` после создания persister:
   - `saved = self.position_persister.load()`
   - Если есть сохранённая позиция → восстановить в `position_manager`
   - Логировать восстановление

3. **Сохранение при открытии** — в `OrderExecutionManager.execute_order_for()`, после `position_manager.open_position()`:
   - Вызвать `self.position_persister.save({...})`

4. **Сохранение при обновлении trailing stop** — в `StopLossManager._update_trailing_stop()`:
   - После `position_manager.update_trailing_stop()` → persist

5. **Удаление при закрытии** — в `OrderExecutionManager.execute_sell()`, после `position_manager.close_position()`:
   - `self.position_persister.remove()`

6. **Сохранение при shutdown** — в `graceful_shutdown()` (~строка 165):
   - Если позиция открыта → `self.position_persister.save({...})`

### Переключение JSON → SQLite
- **Phase 1**: Подключить как есть (JSON файлы) — уже написано
- **Phase 2**: Добавить `SQLitePositionPersister` с тем же интерфейсом (save/load/remove), использующий `TradeDatabase.save_position()`
- **Phase 3**: Переключить через конфиг (`POSITION_BACKEND=sqlite|json`)
- Обратная совместимость: при миграции прочитать JSON → записать в SQLite → удалить JSON

---

## C. Активация DryRunReplay

### Текущее состояние
- `EventRecorder` и `EventReplayer` полностью написаны в `dry_run_replay.py`
- Записывают в JSONL файлы
- Нигде не подключены к `hft_trader.py`

### План подключения

1. **Создать EventRecorder в `LastSecondTrader.__init__`** (после всех менеджеров):
   ```
   self.event_recorder = EventRecorder(
       market_name=self.market_name,
       condition_id=condition_id,
   )
   ```

2. **Записывать book updates** — в `process_market_update()`, после обновления orderbook (~строка 365):
   - `self.event_recorder.record_book_update(side, ask, ask_size, bid, bid_size)`
   - Записывать только каждый N-й update (throttle, ~каждые 0.5с) чтобы не раздувать файл

3. **Записывать trigger checks** — в `check_trigger()`, в конце метода (~строка 470):
   - `self.event_recorder.record_trigger_check(time_remaining, trade_side, winning_ask, executed=True/False, reason=...)`

4. **Записывать trades** — в `OrderExecutionManager.execute_order_for()` и `execute_sell()`:
   - `self.event_recorder.record_trade(action, side, price, size, success, order_id, reason)`

5. **Записывать stop-loss events** — в `StopLossManager._check_stop_loss()` и `_check_take_profit()`:
   - Добавить callback или event для записи

6. **Закрывать при shutdown** — в `graceful_shutdown()`:
   - `self.event_recorder.close()`

### Формат: SQLite vs JSONL
- **Phase 1**: Оставить JSONL (уже работает, простой формат)
- **Phase 2**: Добавить `SQLiteEventRecorder` записывающий в таблицу `events`
- Преимущества SQLite: быстрый поиск по session_id, фильтрация по event_type, агрегация
- JSONL оставить как fallback / export формат

### Какие события записывать
| Событие | Частота | Приоритет |
|---------|---------|-----------|
| book_update (YES/NO) | ~1/с (throttled) | Средний |
| trigger_check | ~1/с в окне | Высокий |
| trade (buy/sell) | Редко | Критический |
| stop_loss / take_profit | Редко | Критический |
| price_change | При изменениях | Низкий |
| oracle_guard block | При блокировках | Средний |
| session_start / session_end | 1 раз | Высокий |

---

## D. Интеграция OrderbookWS

### Анализ: дублирование vs дополнение

**Встроенная логика в hft_trader.py:**
- `connect_websocket()` — подключение к WS, подписка на YES+NO токены
- `listen_to_market()` → `process_market_update()` — парсинг `book`, `price_change`, `best_bid_ask` событий
- Хранение в `OrderBook` dataclass (Level 1 — только best bid/ask)
- Нет reconnect логики (3 попытки при connect, но не при disconnect)
- Нет ping/pong management (websockets библиотека делает сама)

**OrderbookWS модуль:**
- Полноценный Level 2 orderbook (все bids/asks, не только best)
- Auto-reconnect с exponential backoff
- Ping/pong heartbeat loop
- Subscribe/unsubscribe API
- Incremental updates (`price_change` с delta apply)

### Вердикт: ДОПОЛНЯЕТ

`OrderbookWS` **не дублирует**, а значительно расширяет:
1. **Level 2 data** — полная глубина стакана (нужно для ликвидности, slippage analysis)
2. **Auto-reconnect** — текущий код не восстанавливает соединение при обрыве
3. **Чистая абстракция** — можно подключить к нескольким рынкам одновременно

### План интеграции

**Вариант A (рекомендуемый): Постепенная замена**
1. Добавить в `LastSecondTrader` опциональный `OrderbookWS`
2. При включении — использовать его вместо встроенного WS
3. Маппинг: `OrderbookWS.get_best_bid/ask()` → `self.orderbook.best_bid_yes/no`
4. Добавить adapter layer для совместимости с текущим `OrderBook` dataclass
5. Включать через конфиг: `USE_ORDERBOOK_WS=true`

**Вариант B: Оставить как альтернативу**
- Для будущих use cases (Level 2 analytics, multi-market dashboard)
- Не трогать текущую логику в hft_trader.py

**Рекомендация**: Вариант A, но в последнюю очередь (Phase 4). Текущая WS логика работает, reconnect — единственная реальная проблема, и её можно добавить отдельным патчем.

---

## E. Миграция JSON → SQLite

### Файлы для миграции

| Источник | Целевая таблица | Стратегия |
|----------|----------------|-----------|
| `log/daily_limits.json` | `daily_stats` | Read JSON → INSERT → удалить JSON |
| `log/alert_history.json` | `alerts` | Read JSON array → batch INSERT → удалить JSON |
| `data/positions/position_*.json` | `positions` | Read each → INSERT → удалить файлы |
| `data/replays/*.jsonl` | `events` | Read JSONL → batch INSERT (optional, большие файлы) |

### Скрипт миграции `scripts/migrate_to_sqlite.py`

1. Создать БД и таблицы через `TradeDatabase.migrate()`
2. Для каждого источника:
   - Прочитать данные
   - Трансформировать в формат таблицы
   - INSERT
   - Rename оригинал в `*.bak` (не удалять сразу)
3. Проверить целостность: COUNT в SQLite == количество записей из JSON
4. Логировать результат

### Обратная совместимость
- Первые 2 недели: dual-write (JSON + SQLite)
- RiskManager.check_daily_limits() — читать из SQLite, fallback на JSON
- AlertDispatcher — писать в SQLite, прекратить JSON
- Через 2 недели: убрать JSON write path

---

## F. PnL Dashboard обновление

### Текущее состояние
- `pnl_dashboard.py` — CLI-скрипт, парсит log файлы regex'ом
- Читает `daily_limits.json` для текущего PnL
- Regex-парсинг ненадёжный (зависит от формата логов)

### План обновления

1. **Переключить на SQLite**:
   - `load_daily_limits()` → `TradeDatabase.get_or_create_daily_stats(today)`
   - `parse_trade_logs()` → `TradeDatabase.get_trades(date=today)` 
   - Удалить regex-парсинг полностью

2. **Новые аналитики**:
   - **Win rate по рынкам**: `SELECT market_name, COUNT(*) FILTER (pnl > 0) / COUNT(*) FROM trades GROUP BY market_name`
   - **Win rate по часам**: `SELECT strftime('%H', timestamp_iso) as hour, ...`
   - **Win rate по дням недели**: `SELECT strftime('%w', timestamp_iso) as dow, ...`
   - **Средний PnL по типу выхода**: `GROUP BY reason`
   - **Streak analysis**: последовательные win/loss

3. **Визуализация (опционально)**:
   - `rich` — для улучшенного CLI (таблицы, цвета)
   - `matplotlib` — для сохранения графиков в PNG:
     - Equity curve (кумулятивный PnL)
     - PnL по дням (bar chart)
     - Win rate heatmap по часам/дням

---

## G. Порядок реализации

### Phase 1: Фундамент — TradeDatabase (3–4 часа)
**Зависимости:** нет  
**Задачи:**
- [ ] Создать `src/trading/trade_db.py` с классом `TradeDatabase`
- [ ] Все 6 таблиц + индексы + миграции
- [ ] WAL mode, aiosqlite
- [ ] Буферизация orderbook snapshots
- [ ] `async def initialize()`, `close()`, `migrate()`

**Тесты:**
- `tests/test_trade_db.py`: создание БД, CRUD для каждой таблицы, миграции, buffer flush

### Phase 2: Активация PositionPersist (1–2 часа)
**Зависимости:** нет (JSON версия уже написана)  
**Задачи:**
- [ ] Подключить `PositionPersister` в `hft_trader.py.__init__`
- [ ] Восстановление позиции при старте
- [ ] Сохранение при open/update/close/shutdown
- [ ] Передать persister в OrderExecutionManager и StopLossManager

**Тесты:**
- `tests/test_position_persist_integration.py`: crash recovery, open→save→load→close cycle
- Обновить `tests/integration/test_full_trading_workflow.py`

### Phase 3: Активация DryRunReplay (2–3 часа)
**Зависимости:** нет (JSONL версия уже написана)  
**Задачи:**
- [ ] Подключить `EventRecorder` в `hft_trader.py`
- [ ] Добавить запись событий в 5 точках (book, trigger, trade, stop-loss, session)
- [ ] Throttling для book updates
- [ ] Закрытие при shutdown

**Тесты:**
- Обновить `tests/test_dry_run_replay.py` с интеграционными сценариями
- Тест: запись → replay → проверка решений совпадают

### Phase 4: Миграция JSON → SQLite (2–3 часа)
**Зависимости:** Phase 1  
**Задачи:**
- [ ] `scripts/migrate_to_sqlite.py`
- [ ] `RiskManager`: dual-read (SQLite → JSON fallback)
- [ ] `AlertDispatcher`: писать в SQLite через TradeDatabase
- [ ] `PositionPersister`: SQLite backend (новый класс `SQLitePositionPersister`)
- [ ] `EventRecorder`: SQLite backend (новый класс `SQLiteEventRecorder`)

**Тесты:**
- `tests/test_migration.py`: миграция тестовых JSON → проверка данных в SQLite
- `tests/test_risk_manager.py`: обновить для SQLite backend

### Phase 5: PnL Dashboard v2 (1–2 часа)
**Зависимости:** Phase 1, Phase 4  
**Задачи:**
- [ ] Переписать `pnl_dashboard.py` на чтение из SQLite
- [ ] Добавить новые аналитики (win rate по рынкам/часам/дням)
- [ ] Rich CLI output
- [ ] Опционально: matplotlib графики

**Тесты:**
- Обновить `tests/test_pnl_dashboard.py`

### Phase 6: OrderbookWS интеграция (2–3 часа)
**Зависимости:** Phase 1 (для snapshot persistence)  
**Задачи:**
- [ ] Adapter: `OrderbookWS` → `OrderBook` dataclass
- [ ] Опциональное подключение в `hft_trader.py` через конфиг
- [ ] Auto-reconnect для текущего WS (независимо от OrderbookWS)
- [ ] Level 2 data logging в `order_book_snapshots`

**Тесты:**
- `tests/test_orderbook_ws_integration.py`
- `tests/integration/test_websocket_reconnection.py` обновить

---

### Суммарная оценка: **12–17 часов работы агента**

| Phase | Сложность | Часы | Риск |
|-------|-----------|------|------|
| 1. TradeDatabase | Средняя | 3–4 | Низкий |
| 2. PositionPersist | Низкая | 1–2 | Низкий |
| 3. DryRunReplay | Средняя | 2–3 | Низкий |
| 4. JSON→SQLite миграция | Средняя | 2–3 | Средний (данные) |
| 5. PnL Dashboard | Низкая | 1–2 | Низкий |
| 6. OrderbookWS | Высокая | 2–3 | Средний (WS) |

### Диаграмма зависимостей
```
Phase 1 (TradeDatabase)
    ├──→ Phase 4 (JSON→SQLite) ──→ Phase 5 (PnL Dashboard v2)
    └──→ Phase 6 (OrderbookWS)

Phase 2 (PositionPersist) — независимый
Phase 3 (DryRunReplay) — независимый
```

**Можно запускать параллельно:** Phase 1 + Phase 2 + Phase 3  
**Последовательно:** Phase 1 → Phase 4 → Phase 5
