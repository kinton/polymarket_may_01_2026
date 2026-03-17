# Architecture Review: Parameter Ownership & Strategy Encapsulation

_Date: 2026-03-16_

---

## 1. Проблема: три источника правды для параметров стратегии

Параметры одной стратегии (`convergence_v1`) сейчас разбросаны по трём местам:

| Источник | Что хранит | Как используется |
|---|---|---|
| `strategies/convergence_v1.py` | `__init__` defaults: `threshold_pct=0.0003`, `min_cheap_price=0.14`, `min_skew=0.75`, `SUPPORTED_TICKERS` | Единственное место, где эти значения **должны** жить |
| `src/config.py` (`TradingConfig`) | `convergence_threshold_pct=0.0005`, `convergence_min_cheap_price=0.0`, `convergence_min_skew=0.65`, `convergence_max_cheap_price=0.35`, etc. | Пересылаются в стратегию через `hft_trader.py` |
| `src/clob_types.py` | `CONVERGENCE_THRESHOLD_PCT`, `CONVERGENCE_MIN_SKEW`, `CONVERGENCE_MAX_CHEAP_PRICE`, `CONVERGENCE_MIN_CHEAP_PRICE`, `CONVERGENCE_WINDOW_*`, etc. | Читаются в `hft_trader.py.__init__` и передаются в `load_strategy(...)` |

И ещё один дополнительный путь:

| Источник | Что хранит | Как используется |
|---|---|---|
| `main.py` CLI `--min-price` | `args.min_price` → `TradingBotRunner.min_cheap_price` | Передаётся в `LastSecondTrader(min_cheap_price=...)` |
| `main.py` CLI `--tickers` | `args.tickers` → `tickers_override` | Передаётся в `_filter_strategy.configure(tickers=...)` — это легитимный runtime override |
| `main.py` `VALID_TICKERS` | `["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE"]` | Валидация CLI — дублирует `SUPPORTED_TICKERS` из стратегии |

---

## 2. Текущая схема потока параметров

```
CLI args (main.py)
  --min-price=0.0
  --tickers=...
  --universe=...
       │
       ▼
TradingBotRunner.__init__
  self.min_cheap_price = args.min_price     ← хранит параметр стратегии
  self.tickers_override = args.tickers
  self.universe = ["BTC","ETH","SOL"]
  VALID_TICKERS = ["BTC","ETH","SOL",...]   ← дубль SUPPORTED_TICKERS
       │
       ├──► load_strategy("convergence","v1")
       │          → ConvergenceV1()           ← создаётся БЕЗ параметров (для filter)
       │          → configure(tickers=...)    ← тикеры приходят снаружи
       │
       ▼
start_trader_for_market()
  LastSecondTrader(
    ...,
    min_cheap_price=self.min_cheap_price,   ← параметр стратегии течёт снаружи
  )
       │
       ▼
LastSecondTrader.__init__
  from src.clob_types import (               ← второй источник
    CONVERGENCE_THRESHOLD_PCT,               ← = config.convergence_threshold_pct = 0.0005
    CONVERGENCE_MIN_SKEW,                    ← = config.convergence_min_skew = 0.65
    CONVERGENCE_MAX_CHEAP_PRICE,             ← = config.convergence_max_cheap_price = 0.35
    CONVERGENCE_MIN_CHEAP_PRICE,             ← = config.convergence_min_cheap_price = 0.0
    CONVERGENCE_WINDOW_START_S,
    CONVERGENCE_WINDOW_END_S,
    CONVERGENCE_MIN_OBSERVATIONS,
    CONVERGENCE_MIN_CONVERGENCE_RATE,
  )
  load_strategy(
    name=strategy,
    version=strategy_version,
    threshold_pct=CONVERGENCE_THRESHOLD_PCT, ← перезаписывает стратегию (0.0005 ≠ 0.0003)
    min_skew=CONVERGENCE_MIN_SKEW,           ← перезаписывает стратегию (0.65 ≠ 0.75)
    max_cheap_price=CONVERGENCE_MAX_CHEAP_PRICE,
    window_start_s=CONVERGENCE_WINDOW_START_S,
    window_end_s=CONVERGENCE_WINDOW_END_S,
    min_observations=CONVERGENCE_MIN_OBSERVATIONS,
    min_convergence_rate=CONVERGENCE_MIN_CONVERGENCE_RATE,
    # min_cheap_price НЕ передаётся здесь — уже убрали
    # но self._min_cheap_price_override всё ещё хранится в трейдере
  )
       │
       ▼
ConvergenceV1.__init__(
  threshold_pct=0.0005,     ← из config.py (было 0.0003 в __init__ defaults)
  min_skew=0.65,            ← из config.py (было 0.75 в __init__ defaults)
  max_cheap_price=0.35,     ← из config.py
  min_cheap_price=0.14,     ← стратегия берёт свой default (этот не перезаписывается)
  ...
)
```

### Конфликты значений (сейчас)

| Параметр | Default в стратегии | Значение из config.py | Победитель |
|---|---|---|---|
| `threshold_pct` | `0.0003` (3bp) | `0.0005` (5bp) | **config.py** перезаписывает |
| `min_skew` | `0.75` | `0.65` | **config.py** перезаписывает |
| `min_cheap_price` | `0.14` | `0.0` (не передаётся) | **стратегия** (пока не трогаем) |
| `max_cheap_price` | `0.35` | `0.35` | Совпадают |

---

## 3. Что конкретно нужно изменить

### 3.1. `src/hft_trader.py` — главная проблема

**Строки 187–213** — весь блок импорта `CONVERGENCE_*` из `clob_types` и передачи в `load_strategy()`:

```python
# УБРАТЬ: строки 187–213
from src.clob_types import (
    CONVERGENCE_ENABLED,
    CONVERGENCE_THRESHOLD_PCT,
    CONVERGENCE_MIN_SKEW,
    CONVERGENCE_MAX_CHEAP_PRICE,
    CONVERGENCE_MIN_CHEAP_PRICE,
    CONVERGENCE_WINDOW_START_S,
    CONVERGENCE_WINDOW_END_S,
    CONVERGENCE_MIN_OBSERVATIONS,
    CONVERGENCE_MIN_CONVERGENCE_RATE,
    CONVERGENCE_DISABLE_STOP_LOSS,
)
_conv_enabled = convergence_enabled if convergence_enabled is not None else CONVERGENCE_ENABLED
if _conv_enabled and oracle_enabled:
    discover_strategies()
    self.strategy_instance = load_strategy(
        name=strategy,
        version=strategy_version,
        threshold_pct=CONVERGENCE_THRESHOLD_PCT,   # ← убрать
        min_skew=CONVERGENCE_MIN_SKEW,             # ← убрать
        max_cheap_price=CONVERGENCE_MAX_CHEAP_PRICE, # ← убрать
        window_start_s=CONVERGENCE_WINDOW_START_S, # ← убрать
        window_end_s=CONVERGENCE_WINDOW_END_S,     # ← убрать
        min_observations=CONVERGENCE_MIN_OBSERVATIONS, # ← убрать
        min_convergence_rate=CONVERGENCE_MIN_CONVERGENCE_RATE, # ← убрать
        logger=trader_logger,
    )
    self._convergence_disable_stop_loss = CONVERGENCE_DISABLE_STOP_LOSS  # ← убрать
```

**Заменить на:**
```python
if oracle_enabled:
    discover_strategies()
    self.strategy_instance = load_strategy(
        name=strategy,
        version=strategy_version,
        logger=trader_logger,
    )
    self._convergence_disable_stop_loss = getattr(
        self.strategy_instance, "disable_stop_loss", False
    )
```

**Строка 137** — параметр `min_cheap_price` в `__init__`:
```python
# УБРАТЬ: min_cheap_price из сигнатуры __init__
min_cheap_price: float = 0.0,
```

**Строка 162** — хранение override:
```python
# УБРАТЬ: self._min_cheap_price_override = min_cheap_price
```

**Также убрать** `convergence_enabled: bool | None = None` из `__init__` (строка 133) — это решение стратегии, не трейдера.

### 3.2. `main.py` — дублирование параметров стратегии

**Строки 615, 748–751** — `VALID_TICKERS` и валидация в CLI:
```python
# main.py строка 615
VALID_TICKERS = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE"]  # ← дублирует SUPPORTED_TICKERS
```
Это должно браться из стратегии:
```python
# Заменить на:
from strategies import load_strategy, discover_strategies
discover_strategies()
_strategy = load_strategy(args.strategy, args.strategy_version)
VALID_TICKERS = list(_strategy.SUPPORTED_TICKERS)  # или расширенный universe
```

**Строки 656–660** — `--min-price` CLI флаг:
```python
# УБРАТЬ: этот аргумент — он принадлежит стратегии
parser.add_argument(
    "--min-price",
    ...
)
```

**Строки 77–78, 107, 299–300** — `min_cheap_price` в `TradingBotRunner`:
```python
# УБРАТЬ: из __init__ сигнатуры, self.min_cheap_price, и передачи в LastSecondTrader
min_cheap_price: float = 0.0,           # строка 77
self.min_cheap_price = min_cheap_price  # строка 107
min_cheap_price=self.min_cheap_price,   # строка 299
```

### 3.3. `src/config.py` — `CONVERGENCE_*` поля

Поля в `TradingConfig` (строки 103–112) которые являются параметрами стратегии:
```python
# УБРАТЬ (или оставить только как env-var источник для самой стратегии):
convergence_enabled: bool = Field(default=True)
convergence_threshold_pct: float = Field(default=0.0005)
convergence_min_skew: float = Field(default=0.65)
convergence_max_cheap_price: float = Field(default=0.35)
convergence_min_cheap_price: float = Field(default=0.0)
convergence_window_start_s: float = Field(default=200.0)
convergence_window_end_s: float = Field(default=20.0)
convergence_min_observations: int = Field(default=5)
convergence_min_convergence_rate: float = Field(default=0.40)
convergence_disable_stop_loss: bool = Field(default=True)
```

### 3.4. `src/clob_types.py` — `CONVERGENCE_*` константы

Строки 97–106 — весь блок экспортируемых `CONVERGENCE_*`:
```python
# УБРАТЬ: все CONVERGENCE_* константы (строки 97–106)
CONVERGENCE_ENABLED = _cfg.convergence_enabled
CONVERGENCE_THRESHOLD_PCT = _cfg.convergence_threshold_pct
CONVERGENCE_MIN_SKEW = _cfg.convergence_min_skew
CONVERGENCE_MAX_CHEAP_PRICE = _cfg.convergence_max_cheap_price
CONVERGENCE_MIN_CHEAP_PRICE = _cfg.convergence_min_cheap_price
CONVERGENCE_WINDOW_START_S = _cfg.convergence_window_start_s
CONVERGENCE_WINDOW_END_S = _cfg.convergence_window_end_s
CONVERGENCE_MIN_OBSERVATIONS = _cfg.convergence_min_observations
CONVERGENCE_MIN_CONVERGENCE_RATE = _cfg.convergence_min_convergence_rate
CONVERGENCE_DISABLE_STOP_LOSS = _cfg.convergence_disable_stop_loss
```

---

## 4. Целевая архитектура

```
strategies/convergence_v1.py
  ConvergenceV1.__init__(
    threshold_pct=0.0003,    ← единственное место
    min_skew=0.75,           ← единственное место
    min_cheap_price=0.14,    ← единственное место
    max_cheap_price=0.35,    ← единственное место
    SUPPORTED_TICKERS=("BTC","ETH"),  ← единственное место
    ...
  )
       ▲
       │ load_strategy("convergence","v1", logger=...)
       │ (без kwargs параметров стратегии)
       │
  hft_trader.py               main.py
  - только передаёт:          - только передаёт:
    strategy name/version        strategy name/version
    logger                       --universe (для finder)
    oracle settings              --tickers (runtime override)
    trade settings
```

### Легитимные runtime overrides (оставить)

Не все внешние настройки — это нарушение. Следующее **нормально**:

| Override | Место | Обоснование |
|---|---|---|
| `--tickers` CLI → `strategy.configure(tickers=...)` | `main.py` → `ConvergenceV1.configure()` | Явный runtime override, задокументированный в `configure()` |
| `oracle_enabled`, `oracle_min_points`, etc. | `main.py` → `hft_trader.py` | Это параметры инфраструктуры, не стратегии |
| `trade_size`, `dry_run` | `main.py` → `hft_trader.py` | Параметры исполнения, не стратегии |

### Опциональный env-var механизм для стратегии

Если нужно переопределять параметры стратегии через env-vars (для деплоя), лучший паттерн — стратегия читает env сама:

```python
# strategies/convergence_v1.py
import os

def __init__(self, ...):
    self.threshold_pct = float(os.getenv("CONVERGENCE_THRESHOLD_PCT", "0.0003"))
    self.min_cheap_price = float(os.getenv("CONVERGENCE_MIN_CHEAP_PRICE", "0.14"))
    ...
```

Это сохраняет стратегию единственным источником правды, но позволяет ops-команде менять значения без правки кода.

---

## 5. Резюме изменений

| Файл | Действие | Строки |
|---|---|---|
| `src/hft_trader.py` | Убрать весь блок импорта `CONVERGENCE_*` и передачи в `load_strategy()` | 187–213 |
| `src/hft_trader.py` | Убрать `min_cheap_price` из `__init__` и `self._min_cheap_price_override` | 137, 162 |
| `src/hft_trader.py` | Убрать `convergence_enabled` из `__init__` | 133 |
| `main.py` | Убрать `--min-price` CLI флаг | 656–660 |
| `main.py` | Убрать `min_cheap_price` из `TradingBotRunner.__init__` | 77, 107, 299 |
| `main.py` | Заменить hardcoded `VALID_TICKERS` на значения из стратегии | 615, 748–751 |
| `src/config.py` | Убрать `convergence_*` поля из `TradingConfig` | 103–112 |
| `src/clob_types.py` | Убрать весь блок `CONVERGENCE_*` констант | 97–106 |
| `strategies/convergence_v1.py` | Выровнять defaults под реальные рабочие значения (сейчас `threshold_pct=0.0003`, а config говорит `0.0005`) | 86–90 |
