# Plugin-архитектура стратегий — План реализации

> Дата: 2026-03-14
> Статус: Draft

## Цель

Превратить монолитную привязку `ConvergenceStrategy` в plugin-систему, где стратегии:
- живут в отдельных модулях (`strategies/`)
- наследуют единый `BaseStrategy`
- загружаются динамически через `importlib`
- подключаются к `LastSecondTrader` без правок в его коде

## Целевая структура

```
strategies/
  __init__.py              ← registry + loader (importlib)
  base.py                  ← BaseStrategy (ABC)
  convergence_v1.py        ← текущая логика из src/trading/convergence_strategy.py
```

## Ключевые типы

```python
@dataclass(frozen=True)
class MarketTick:
    time_remaining: float              # секунды до закрытия рынка
    oracle_snapshot: OracleSnapshot | None
    orderbook: OrderBook

@dataclass(frozen=True)
class Signal:
    side: str                          # "YES" | "NO"
    price: float                       # цена входа (ask cheap side)
    disable_stop_loss: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    # metadata хранит strategy-specific данные:
    #   convergence_rate, side_consistency, reason, expensive_price, ...
```

```python
class BaseStrategy(ABC):
    # --- identity (class-level) ---
    name: str                          # e.g. "convergence"
    version: str                       # e.g. "v1"
    default_tickers: list[str]         # ["BTC", "ETH", "SOL"]
    default_min_price: float           # 0.0
    default_max_price: float           # 0.35

    @abstractmethod
    def observe(self, tick: MarketTick) -> None: ...

    @abstractmethod
    def decide(self) -> Signal | None: ...

    @abstractmethod
    def reset(self) -> None: ...
```

---

## Шаги реализации

### Шаг 1. Создать `strategies/base.py` — BaseStrategy ABC

**Файлы:** `strategies/base.py` (новый)

**Что делаем:**
- Определяем `MarketTick`, `Signal` как frozen dataclasses
- Определяем `BaseStrategy(ABC)` с абстрактными методами `observe`, `decide`, `reset`
- Класс-атрибуты: `name`, `version`, `default_tickers`, `default_min_price`, `default_max_price`
- Добавляем конкретный метод `get_signal(tick) → Signal | None` — вызывает `observe` + `decide` (как сейчас в `ConvergenceStrategy.get_signal`)

**Зависимости:** `src/clob_types.OrderBook`, `src/oracle_tracker.OracleSnapshot`

**Риски:** Минимальные — новый файл, ничего не ломает.

---

### Шаг 2. Создать `strategies/__init__.py` — реестр и загрузчик

**Файлы:** `strategies/__init__.py` (новый)

**Что делаем:**
- `STRATEGY_REGISTRY: dict[str, type[BaseStrategy]]` — маппинг `"convergence/v1" → ConvergenceV1`
- `register(cls)` — декоратор, добавляет класс в реестр по `f"{cls.name}/{cls.version}"`
- `load_strategy(name: str, version: str, **kwargs) → BaseStrategy` — ищет в реестре, инстанцирует
- `discover_strategies(path: str)` — сканирует `strategies/*.py` через `importlib.import_module`, вызывая авто-регистрацию через декоратор `@register`

**Зависимости:** `importlib`, `pathlib`, `strategies.base`

**Риски:**
- Ошибки импорта в модулях стратегий должны логироваться, но не ронять весь процесс
- Конфликт имён в реестре → raise при дубликате `name/version`

---

### Шаг 3. Портировать `ConvergenceStrategy` → `strategies/convergence_v1.py`

**Файлы:** `strategies/convergence_v1.py` (новый), `src/trading/convergence_strategy.py` (останется как deprecated shim)

**Что делаем:**
- Копируем логику из `src/trading/convergence_strategy.py` (380 строк)
- Наследуем от `BaseStrategy`
- Адаптируем сигнатуры:
  - `observe(tick: MarketTick)` вместо `observe(time_remaining, oracle_snapshot, orderbook)`
  - `decide() → Signal | None` вместо `decide(...) → ConvergenceSignal | None`
  - `get_signal(tick)` наследуется из `BaseStrategy`
- `ConvergenceSignal` → `Signal` + `metadata` (convergence_rate, side_consistency, etc.)
- Декорируем `@register`
- Устанавливаем: `name = "convergence"`, `version = "v1"`, `default_tickers = ["BTC", "ETH", "SOL"]`

**Зависимости:** `strategies.base`, `src/oracle_tracker.OracleSnapshot`, `src/clob_types.OrderBook`

**Риски:**
- Регрессия в торговой логике при портировании — **необходимо snapshot-тестирование**
- `ConvergenceSignal` используется в `hft_trader.py` (поля `.side`, `.price`, `.convergence_rate`, `.side_consistency`) — нужен маппинг через `Signal.metadata`

---

### Шаг 4. Формирование `MarketTick` в `hft_trader.py`

**Файлы:** `src/hft_trader.py` (изменение)

**Что делаем:**
- В `check_trigger()` (строка ~958) собираем `MarketTick`:
  ```python
  tick = MarketTick(
      time_remaining=time_remaining,
      oracle_snapshot=self.oracle_guard.snapshot if self.oracle_guard.enabled else None,
      orderbook=self.orderbook,
  )
  ```
- Сейчас эти три значения уже передаются в `convergence_strategy.get_signal()` отдельными аргументами — собираем в единый объект

**Зависимости:** `strategies.base.MarketTick`

**Риски:** Минимальные — пакуем существующие данные в dataclass.

---

### Шаг 5. Заменить прямую инстанциацию на `load_strategy()`

**Файлы:** `src/hft_trader.py` (изменение, `__init__` строки 186–217)

**Что делаем:**
- Убираем `from src.trading.convergence_strategy import ConvergenceStrategy`
- Добавляем `from strategies import load_strategy, discover_strategies`
- В `__init__`:
  ```python
  discover_strategies("strategies")
  self.strategy = load_strategy(
      name=strategy,          # из конфига, e.g. "convergence"
      version=strategy_version,  # e.g. "v1"
      # передаём параметры из конфига
      threshold_pct=CONVERGENCE_THRESHOLD_PCT,
      ...
  )
  ```
- Убираем хардкод `if _conv_enabled and oracle_enabled`

**Зависимости:** `strategies` package, конфиг-параметры

**Риски:**
- **Высокий**: это ключевая точка связки. Если `load_strategy` упадёт — трейдер не запустится
- Нужен fallback: если стратегия не найдена → raise чёткая ошибка + alert
- Параметры стратегии сейчас хардкожены в `clob_types.py` как константы → нужна передача через kwargs

---

### Шаг 6. Универсализировать `check_trigger()` для любой стратегии

**Файлы:** `src/hft_trader.py` (изменение, `check_trigger()` строки 910–1026)

**Что делаем:**
- Заменяем блок `if self.convergence_strategy is not None` на:
  ```python
  if self.strategy is not None:
      tick = MarketTick(time_remaining, oracle_snapshot, orderbook)
      signal = self.strategy.get_signal(tick)
      if signal is not None:
          # oracle quality check (стратегия может указать через metadata)
          ...
          self._planned_trade_side = signal.side
          await self.execute_order()
  ```
- `_convergence_trade` флаг → обобщить в `_strategy_trade`
- `disable_stop_loss` берётся из `signal.disable_stop_loss`
- `confidence` для dry_run_sim: `signal.metadata.get("confidence", 0.0)`
- `reason` для dry_run_sim: `signal.metadata.get("reason", self.strategy.name)`

**Зависимости:** `Signal`, `MarketTick`, `BaseStrategy`

**Риски:**
- **Высокий**: это hot path, ошибка = пропущенная сделка или ложный вход
- Oracle quality check сейчас convergence-specific (`quality_ok_for_convergence()`) — нужно решить: стратегия сама проверяет oracle, или trader делает generic check?
- Рекомендация: стратегия возвращает `signal.metadata["requires_oracle_quality"] = True`, trader вызывает соответствующую проверку

---

### Шаг 7. Вынести конфигурацию стратегий из `TradingConfig`

**Файлы:** `src/config.py` (изменение)

**Что делаем:**
- Оставляем в `TradingConfig` только: `strategy`, `strategy_version`, `mode`, `tickers`, `min_cheap_price`
- Параметры convergence (`convergence_threshold_pct`, `convergence_min_skew`, ...) переносим в `strategies/convergence_v1.py` как defaults класса
- Стратегия читает свои параметры из env vars с префиксом `STRATEGY_{NAME}_{PARAM}` или принимает через `__init__(**kwargs)`
- Альтернатива: YAML/TOML-файл `strategies/convergence_v1.yaml` — **отложить до v2**

**Зависимости:** `src/config.py`, `strategies/convergence_v1.py`

**Риски:**
- Ломается обратная совместимость env-переменных (`CONVERGENCE_THRESHOLD_PCT` → `STRATEGY_CONVERGENCE_THRESHOLD_PCT`)
- Решение: на переходный период поддерживать оба варианта

---

### Шаг 8. Обновить `main.py` и `parallel_launcher.py`

**Файлы:** `main.py`, `src/trading/parallel_launcher.py` (изменения)

**Что делаем:**
- `main.py`: вызываем `discover_strategies()` один раз при старте бота
- `parallel_launcher.py`: передаём `strategy`/`strategy_version` при создании каждого `LastSecondTrader`
- Убираем импорт `ConvergenceStrategy` из всех файлов кроме deprecated shim
- Добавляем CLI-аргумент `--strategy convergence/v1` (опционально)

**Зависимости:** `strategies.__init__`, `src/hft_trader.py`

**Риски:** Минимальные — меняем только точку импорта.

---

### Шаг 9. Обновить dry_run_sim и trade_db

**Файлы:** `src/trading/dry_run_simulator.py`, `src/trading/trade_db.py` (изменения)

**Что делаем:**
- `record_buy()` сейчас принимает `reason="convergence"` — обобщить на `reason=strategy.name`
- `confidence` вычисляется из `signal.metadata` вместо хардкода `convergence_rate * side_consistency`
- В trade_db schema уже есть `strategy` поле — убедиться что оно заполняется из `strategy.name`
- Сохранять `signal.metadata` как JSON в отдельное поле (если нет — добавить миграцию)

**Зависимости:** `Signal`, SQLite schema

**Риски:**
- Миграция SQLite schema — нужен `ALTER TABLE ADD COLUMN` или пересоздание
- Решение: `metadata` поле как TEXT (JSON) с fallback на пустой `{}`

---

### Шаг 10. Тесты и документация

**Файлы:** `tests/test_strategy_plugin.py` (новый), `tests/test_convergence_v1.py` (новый), `docs/STRATEGY.md` (обновить)

**Что делаем:**
- **Snapshot-тест**: прогоняем одинаковые тики через старую `ConvergenceStrategy` и новую `convergence_v1` → сигналы должны совпадать
- **Unit-тесты BaseStrategy**: проверяем что ABC нельзя инстанцировать без реализации
- **Тест реестра**: `register`, `load_strategy`, `discover_strategies` с mock-стратегией
- **Integration-тест**: `LastSecondTrader` + загруженная стратегия → signal flow
- **Тест новой стратегии**: создать `strategies/dummy_v1.py` (always-buy) для проверки plugin-механизма
- Обновить `docs/STRATEGY.md` с инструкцией "как написать свою стратегию"

**Зависимости:** `pytest`, все модули выше

**Риски:**
- Convergence regression — критически важно поймать до деплоя
- Integration test требует mock WebSocket + mock Oracle

---

## Порядок и зависимости

```
Шаг 1 (base.py)
  ↓
Шаг 2 (__init__.py — registry)
  ↓
Шаг 3 (convergence_v1.py — портирование)
  ↓
Шаг 4 (MarketTick в hft_trader) ──┐
  ↓                                │
Шаг 5 (load_strategy в init)      │  ← можно параллельно 4+5
  ↓                                │
Шаг 6 (check_trigger generic) ────┘
  ↓
Шаг 7 (config refactor)
  ↓
Шаг 8 (main.py + launcher)
  ↓
Шаг 9 (dry_run + trade_db)
  ↓
Шаг 10 (тесты + docs)
```

## Ключевые решения

| Вопрос | Решение |
|--------|---------|
| Oracle check — в стратегии или в trader? | В trader, стратегия сообщает через `metadata["requires_oracle_quality"]` |
| Конфиг стратегии — env vars или YAML? | Env vars (фаза 1), YAML (фаза 2) |
| `ConvergenceSignal` — оставить или убрать? | Убрать, заменить на `Signal` + `metadata` |
| Старый `src/trading/convergence_strategy.py`? | Deprecated shim: `from strategies.convergence_v1 import *` |
| Несколько стратегий одновременно? | Нет — один trader = одна стратегия. Разные стратегии на разных рынках через `parallel_launcher` |

## Оценка рисков

| Риск | Вероятность | Влияние | Митигация |
|------|-------------|---------|-----------|
| Регрессия convergence при портировании | Средняя | Критическое | Snapshot-тесты, параллельный запуск old/new |
| `importlib` ошибки при загрузке | Низкая | Высокое | try/except + alert + чёткие сообщения |
| Потеря env-var совместимости | Высокая | Среднее | Переходный период с поддержкой обоих форматов |
| Ошибка в generic check_trigger | Средняя | Критическое | Поэтапный деплой: сначала dry_run mode |
