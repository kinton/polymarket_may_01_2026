# Market Filter Refactoring Plan

> Дата: 2026-03-14
> Цель: убрать `default_tickers`, `default_min_price`, `default_max_price` из BaseStrategy.
> Стратегия сама решает какие рынки ей нужны через `market_filter(MarketInfo) -> bool`.

---

## 1. Текущее состояние

### 1.1 BaseStrategy (strategies/base.py)

```python
class BaseStrategy(ABC):
    name: str = ""
    version: str = ""
    default_tickers: list[str] = []      # ← удалить
    default_min_price: float = 0.0       # ← удалить
    default_max_price: float = 0.35      # ← удалить
```

**Факт:** `default_tickers`, `default_min_price`, `default_max_price` **нигде не читаются** через
атрибут (нет вызовов `.default_tickers` и т.д.). Они существуют только как декларативные поля.
Их фактическая логика дублируется в двух местах:

1. **Finder** (`src/gamma_15m_finder.py`) — тикер-фильтр через `_matches_tickers()`.
   Тикеры приходят из CLI `--tickers` → `TradingBotRunner.tickers` → `GammaAPI15mFinder(tickers=...)`.
2. **ConvergenceV1** (`strategies/convergence_v1.py`) — ценовой фильтр через `max_cheap_price`/`min_cheap_price`
   внутри `decide()`. Эти значения приходят через `__init__` kwargs, не через `default_max_price`.

### 1.2 Finder → Market dict

`GammaAPI15mFinder.filter_markets()` возвращает `list[dict]` с ключами:

| Ключ              | Тип   | Пример                        |
|-------------------|-------|-------------------------------|
| `condition_id`    | str   | `"0x4433bc54..."`             |
| `token_id_yes`    | str   | `"1234567890..."`             |
| `token_id_no`     | str   | `"9876543210..."`             |
| `end_time`        | str   | `"14:30:00 EDT"`              |
| `end_time_utc`    | str   | `"2026-03-14 18:30:00 UTC"`   |
| `minutes_until_end` | float | `12.3`                      |
| `title`           | str   | `"Will BTC go up or down..."` |
| `ticker`          | str   | `"BTC"`                       |
| `slug`            | str?  | `"btc-15m-up-or-down"`        |

**Отсутствует:** `cheap_price` — в момент фильтрации ордербук ещё не подключён (WebSocket подключается внутри `LastSecondTrader.run()`).

### 1.3 Data flow: CLI → Finder → Trader → Strategy

```
CLI --tickers BTC,ETH --min-price 0.05
  ↓
TradingBotRunner(tickers=["BTC","ETH"], min_cheap_price=0.05)
  ↓
GammaAPI15mFinder(tickers=["BTC","ETH"])     ← тикер-фильтр в Finder
  ↓
filter_markets() → list[dict]                 ← возвращает рынки, прошедшие тикер-фильтр
  ↓
LastSecondTrader(min_cheap_price=0.05, strategy="convergence", strategy_version="v1")
  ↓
load_strategy("convergence", "v1", min_cheap_price=0.05, ...)
  ↓
ConvergenceV1(min_cheap_price=0.05)           ← ценовой фильтр внутри decide()
```

### 1.4 Когда стратегия видит рынок

Стратегия **не участвует в выборе рынка**. Она получает тик (`MarketTick`) уже внутри запущенного
`LastSecondTrader` для конкретного рынка. Стратегия не знает `condition_id`, `ticker`, `title` — она
видит только `time_remaining`, `oracle_snapshot`, `orderbook`.

---

## 2. Целевая архитектура

### 2.1 Новый MarketInfo (strategies/base.py)

```python
@dataclass(frozen=True)
class MarketInfo:
    """Snapshot of market metadata for strategy filtering."""
    condition_id: str
    ticker: str                # "BTC", "ETH", "SOL"
    title: str
    end_time_utc: str          # "2026-03-14 18:30:00 UTC"
    minutes_until_end: float
    token_id_yes: str
    token_id_no: str
```

**Почему нет `cheap_price`:** в момент фильтрации (Finder) ордербук ещё недоступен. Цена фильтруется
внутри `decide()` через `max_cheap_price`/`min_cheap_price` — это правильное место и менять его не нужно.

### 2.2 Новый BaseStrategy

```python
class BaseStrategy(ABC):
    name: str = ""
    version: str = ""

    # Убраны: default_tickers, default_min_price, default_max_price

    @abstractmethod
    def market_filter(self, market: MarketInfo) -> bool:
        """Хочет ли стратегия торговать этот рынок?
        Вызывается Finder/Runner для каждого найденного рынка.
        """
        ...

    @abstractmethod
    def observe(self, tick: MarketTick) -> None: ...

    @abstractmethod
    def decide(self, tick: MarketTick) -> Signal | None: ...

    @abstractmethod
    def reset(self) -> None: ...

    def get_signal(self, tick: MarketTick) -> Signal | None:
        self.observe(tick)
        return self.decide(tick)

    def configure(self, **kwargs) -> None:
        """Опционально: принять runtime-параметры (CLI, env)."""
        pass
```

### 2.3 ConvergenceV1 — реализация market_filter

```python
class ConvergenceV1(BaseStrategy):
    name = "convergence"
    version = "v1"

    # Тикеры и цены теперь НЕ в базовом классе — они локальны для этой стратегии
    SUPPORTED_TICKERS = ("BTC", "ETH", "SOL")

    def market_filter(self, market: MarketInfo) -> bool:
        return market.ticker.upper() in self.SUPPORTED_TICKERS
```

---

## 3. План реализации по шагам

### Шаг 1: Добавить MarketInfo в strategies/base.py

**Файл:** `strategies/base.py`

- Добавить `@dataclass(frozen=True) class MarketInfo` (перед `BaseStrategy`)
- Обновить `__all__`-like экспорты если есть

**Риск:** Минимальный — добавление нового класса.

### Шаг 2: Обновить BaseStrategy

**Файл:** `strategies/base.py`

- Удалить `default_tickers`, `default_min_price`, `default_max_price`
- Добавить `@abstractmethod market_filter(self, market: MarketInfo) -> bool`
- Добавить `def configure(self, **kwargs) -> None: pass`

**Риск:** Ломает все наследники, если они не реализуют `market_filter()`.

### Шаг 3: Обновить ConvergenceV1

**Файл:** `strategies/convergence_v1.py`

- Удалить `default_tickers = ["BTC", "ETH", "SOL"]`, `default_min_price`, `default_max_price`
- Добавить `SUPPORTED_TICKERS = ("BTC", "ETH", "SOL")` (класс-константа)
- Реализовать `market_filter()`:
  ```python
  def market_filter(self, market: MarketInfo) -> bool:
      return market.ticker.upper() in self.SUPPORTED_TICKERS
  ```
- Опционально: реализовать `configure()` для приёма тикеров из CLI
  ```python
  def configure(self, **kwargs) -> None:
      tickers = kwargs.get("tickers")
      if tickers:
          self._active_tickers = tuple(t.upper() for t in tickers)
  ```

**Риск:** Низкий — ценовые фильтры (`max_cheap_price`, `min_cheap_price`) остаются в `__init__` kwargs и `decide()`.

### Шаг 4: Обновить strategies/__init__.py

**Файл:** `strategies/__init__.py`

- Добавить `MarketInfo` в импорт и `__all__`

**Риск:** Минимальный.

### Шаг 5: Интегрировать market_filter в data flow

**Файл:** `main.py` → `TradingBotRunner`

Сейчас тикер-фильтр живёт в двух местах:
1. `GammaAPI15mFinder(tickers=...)` — при поиске
2. `filter_markets() → _matches_tickers()` — при фильтрации

**Изменения:**

В `should_start_trader()` или в новый метод `strategy_accepts_market()`:
```python
def strategy_accepts_market(self, market: dict) -> bool:
    """Проверяет, хочет ли текущая стратегия торговать этот рынок."""
    from strategies.base import MarketInfo
    info = MarketInfo(
        condition_id=market["condition_id"],
        ticker=market.get("ticker", ""),
        title=market.get("title", ""),
        end_time_utc=market.get("end_time_utc", ""),
        minutes_until_end=market.get("minutes_until_end", 0.0),
        token_id_yes=market["token_id_yes"],
        token_id_no=market["token_id_no"],
    )
    return self._strategy_instance.market_filter(info)
```

**Критический вопрос:** убирать ли тикер-фильтр из Finder?

- **Рекомендация: НЕТ, не убирать.** Finder использует тикеры для **построения поисковых запросов**
  к Gamma API (`"Bitcoin Up or Down"`, `"ETH 15 Minute Up or Down"`). Без тикеров он не знает
  какие запросы отправлять. Тикер-фильтр в Finder — это оптимизация поиска, не бизнес-логика стратегии.
- `market_filter()` стратегии работает как **дополнительный слой** — стратегия может отказаться
  от рынка, который Finder нашёл.
- В будущем, если стратегия ищет рынки по другим критериям (не тикеры), можно добавить
  другой Finder или расширить текущий.

**Изменения в main.py:**
1. Создать `_strategy_instance` раньше — в `__init__` TradingBotRunner (сейчас стратегия
   создаётся внутри каждого `LastSecondTrader`). Или: создавать временный экземпляр только для фильтрации.
2. В `poll_and_trade()`, после `should_start_trader()`, добавить `strategy_accepts_market()` проверку.

**Лучший вариант:** создать стратегию один раз в `TradingBotRunner.__init__()` для фильтрации,
а `LastSecondTrader` продолжает создавать свой экземпляр для торговли (чтобы `reset()` работал изолированно).

**Файл:** `src/hft_trader.py`

- Без изменений в основном flow. `LastSecondTrader` продолжает создавать свой экземпляр стратегии.
- Можно передать `MarketInfo` в стратегию при создании (через `configure()`), чтобы стратегия знала
  ticker/title в runtime, но это **опционально** и не обязательно для первой итерации.

**Риск:** Средний — нужно убедиться что стратегия создаётся до фильтрации и не мешает
существующему flow.

### Шаг 6: Обновить тесты

**Файл:** `tests/test_strategy_plugin.py`

- `_DummyStrategy`: удалить `default_tickers`, `default_min_price`, `default_max_price`
- Добавить `market_filter()`:
  ```python
  def market_filter(self, market: MarketInfo) -> bool:
      return True  # dummy accepts everything
  ```
- Добавить тесты для `MarketInfo`:
  - Создание, frozen-проверка
  - `market_filter` возвращает True/False

**Файл:** `tests/test_convergence_v1.py`

- Добавить тест:
  ```python
  def test_market_filter_accepts_btc(self):
      cs = ConvergenceV1()
      info = MarketInfo(condition_id="0x...", ticker="BTC", ...)
      assert cs.market_filter(info) is True

  def test_market_filter_rejects_unknown(self):
      cs = ConvergenceV1()
      info = MarketInfo(condition_id="0x...", ticker="DOGE", ...)
      assert cs.market_filter(info) is False
  ```

**Файл:** `tests/test_convergence_strategy.py` (legacy shim)

- Проверить не сломался ли shim. Shim (`src/trading/convergence_strategy.py`) может не наследовать
  BaseStrategy напрямую — нужно проверить.

**Риск:** Низкий — тесты конвергенции не используют `default_*` поля.

### Шаг 7: Обновить docs/PLUGIN_STRATEGY_PLAN.md

- Убрать упоминания `default_tickers`, `default_min_price`, `default_max_price`
- Добавить `market_filter()` и `MarketInfo` в описание интерфейса

---

## 4. Порядок мёрджа (безопасный)

1. **Шаг 1-2:** `MarketInfo` + обновлённый `BaseStrategy` (с `market_filter`)
2. **Шаг 3:** `ConvergenceV1` адаптация
3. **Шаг 4:** `strategies/__init__.py` экспорт
4. **Шаг 6:** Тесты (можно параллельно с 2-3)
5. **Шаг 5:** Интеграция в `main.py` (последний — зависит от всего выше)
6. **Шаг 7:** Docs

Можно сделать одним коммитом (все шаги атомарны), но безопаснее двумя:
- **Коммит A:** Шаги 1-4, 6 — новый интерфейс + адаптация стратегии + тесты
- **Коммит B:** Шаг 5 — интеграция в main.py

---

## 5. Что НЕ меняется

| Компонент | Почему не трогаем |
|-----------|-------------------|
| `GammaAPI15mFinder` | Тикеры нужны для построения API-запросов. Finder фильтрует по тикерам как оптимизация, `market_filter` — дополнительный слой |
| `LastSecondTrader` | Продолжает создавать свой экземпляр стратегии, flow не меняется |
| `MarketTick` | Остаётся без изменений — стратегия видит тики как раньше |
| `Signal` | Без изменений |
| `OracleSnapshot`, `OrderBook` | Без изменений |
| Ценовой фильтр в `decide()` | `max_cheap_price`/`min_cheap_price` остаются в ConvergenceV1 kwargs — это правильное место |

---

## 6. Открытые вопросы

### Q1: configure() — нужен ли?

**Рекомендация:** Добавить как `def configure(self, **kwargs) -> None: pass` (не abstract).
Полезен для передачи CLI-параметров (`tickers`, `mode`) без изменения `__init__` сигнатуры.
Конкретные стратегии переопределяют по необходимости.

### Q2: Где создавать стратегию для фильтрации?

**Варианты:**
- A) Создать в `TradingBotRunner.__init__()` один экземпляр для фильтрации
- B) Использовать classmethod `ConvergenceV1.market_filter()` (без экземпляра)
- C) Создавать временный экземпляр на каждый poll

**Рекомендация:** Вариант A — один экземпляр `_filter_strategy` в Runner.
Он используется только для `market_filter()`, не для торговли.

### Q3: Передавать ли ticker в стратегию при торговле?

Сейчас стратегия не знает ticker внутри `decide()`. Это не нужно для convergence, но может
пригодиться для будущих стратегий (например, разный threshold для BTC vs SOL).

**Рекомендация:** Пока не добавлять. Если понадобится — расширить `MarketTick` или использовать
`configure(ticker="BTC")` при создании.
