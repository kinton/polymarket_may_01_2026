# Position Settler — План доработки

## Проблемы (текущие)

### P0 — Критические

**1. `get_trades` возвращает пустой список (причина логов "No trade history found")**
- Файл: `src/position_settler.py:171`
- Причина: `TradeParams(maker_address=self.client.get_address())` — возвращает EOA (адрес из `PRIVATE_KEY`).
  Но бот торгует через proxy-кошелёк (`POLYMARKET_PROXY_ADDRESS`). CLOB API записывает трейды
  под адресом proxy, а не EOA. Запрос на EOA → 0 трейдов.
- Fix: `maker_address=os.getenv("POLYMARKET_PROXY_ADDRESS") or self.client.get_address()`

**2. `check_dryrun_resolution` открывает неверный DB файл**
- Файл: `src/position_settler.py:575`
- Причина: захардкожен путь `data/trades.db`.
  Главный бот пишет в `data/{strategy}-{version}-{mode}.db`
  (например: `data/convergence-v1-test.db`, `data/convergence-v2-live.db`).
  `data/trades.db` — устаревший legacy-файл, трейды там не появляются.
- Fix: settler должен знать реальные пути к БД или сканировать `data/*.db`

### P1 — Важные

**3. Settler не знает о конфигурации стратегий**
- В `docker-compose.yml` settler не получает volume `/config`.
  Он не читает `config/strategies.yaml`, поэтому не может динамически
  находить актуальные DB-файлы.

**4. `calculate_pnl` использует захардкоженный `entry_price=0.99`**
- Файл: `src/position_settler.py:302`
- Реальная цена входа лежит в БД (поле `price` в таблице `trades`).
  P&L в CSV будет неточным.

**5. Одна инстанция settler для нескольких стратегий**
- v1 (test/dryrun) и v2 (live) используют разные DB.
  Settler должен обходить обе, но сейчас не настроен на это.

### P2 — Некритические

**6. `log_pnl_to_csv` пишет в `log/pnl.csv` без разбивки по стратегиям**
- При нескольких стратегиях всё смешивается в один файл.

**7. `sell_position_if_profitable` использует FOK-ордер с фиксированным price=0.99**
- В реальности цена может быть меньше. Ордер будет отклонён, если market bid < 0.99.
  Стоит использовать market order или динамически определять цену.

**8. Settler не алертит о статусе (живой/мёртвый)**
- Нет Telegram-уведомления при старте и при критической ошибке в daemon-режиме.

---

## Задачи

| # | Задача | Приоритет | Оценка (ч) |
|---|--------|-----------|------------|
| T1 | Исправить `maker_address` → использовать proxy-адрес если задан | P0 | 0.5 |
| T2 | Исправить DB path: убрать хардкод `data/trades.db`, передавать пути через конфиг/env | P0 | 2 |
| T3 | Добавить volume `/config` в settler в docker-compose.yml | P1 | 0.5 |
| T4 | Читать `config/strategies.yaml` в settler и строить список DB-путей | P1 | 2 |
| T5 | Брать `entry_price` из таблицы `trades` вместо константы 0.99 | P1 | 1 |
| T6 | Разбить `log/pnl.csv` по стратегиям (`log/pnl-{strategy}-{version}.csv`) | P2 | 1 |
| T7 | Добавить Telegram-уведомление при старте settler и при fatal error | P2 | 1 |
| T8 | Покрыть T1/T2 юнит-тестами | P1 | 2 |

**Итого P0:** ~2.5 ч → решает "No trade history found" полностью
**Итого P0+P1:** ~8 ч → settler полностью функционален

---

## Архитектурные решения

### Вариант A: Передача DB-путей через env (рекомендуется, быстрее)

```
SETTLER_DB_PATHS=data/convergence-v1-test.db,data/convergence-v2-live.db
```

- Settler читает `SETTLER_DB_PATHS` из `.env`
- Итерирует по всем файлам при `check_dryrun_resolution`
- Не нужна зависимость от `config/strategies.yaml`
- `docker-compose.yml` обновляется на новые стратегии вместе с `.env`

### Вариант B: Читать `config/strategies.yaml` (сложнее, но автоматически)

- Settler читает конфиг стратегий, строит `db_path` по той же логике что main.py
  (`data/{name}-{version}-{mode}.db`)
- Добавить `/config` volume в settler в docker-compose
- Плюс: добавление новой стратегии автоматически подхватывается settler
- Минус: coupling между settler и форматом конфига

**Решение:** Реализовать вариант A (env vars) как быстрый fix (T1+T2), затем добавить
вариант B опционально (T4).

### Про maker_address

```python
# Текущий (сломанный)
params=TradeParams(maker_address=self.client.get_address() or "")

# Исправленный
proxy = os.getenv("POLYMARKET_PROXY_ADDRESS")
address = proxy or self.client.get_address() or ""
params=TradeParams(maker_address=address)
```

Логика: если proxy задан — торговля идёт от его имени, все трейды в CLOB
записаны под ним. get_address() возвращает EOA, который используется только
для подписи транзакций.
