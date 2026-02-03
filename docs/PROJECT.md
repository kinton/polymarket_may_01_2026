# Polymarket Trading Bot - API Documentation & Technical Details

## 🔌 Important APIs and Endpoints

### 1. Polymarket Gamma API (Market Search)
**Endpoint:** `https://gamma-api.polymarket.com/public-search`

**Параметры:**
- `q` (query) - поисковый запрос для рынков

**Примеры запросов:**
```bash
# Поиск Bitcoin рынков по времени (12-часовой формат)
curl 'https://gamma-api.polymarket.com/public-search?q=Bitcoin%20Up%20or%20Down%20-%20January%2024,%207:'

# Общий поиск
curl 'https://gamma-api.polymarket.com/public-search?q=Bitcoin%20Up%20or%20Down'
```

**Формат ответа:**
```json
{
  "events": [
    {
      "id": "178316",
      "title": "Bitcoin Up or Down - January 24, 7:30PM-7:35PM ET",
      "ticker": "btc-updown-5m-1769214600",
      "active": true,
      "closed": false,
      "endDate": "2026-01-24T00:35:00Z",
      "markets": [
        {
          "conditionId": "0xfe3abe7c...",
          "clobTokenIds": "[\"10351064302...\", \"9749632838...\"]",
          "active": true,
          "closed": false
        }
      ]
    }
  ]
}
```

**Особенности:**
- API возвращает структуру `events`, а не `markets`
- События могут содержать вложенный массив `markets`
- Поиск лучше работает с date-specific queries (January 24, 7:)
- Используется 12-часовой формат времени в названиях рынков

---

### 2. Polymarket CLOB WebSocket (Order Book Stream)
**Endpoint:** `wss://ws-subscriptions-clob.polymarket.com/ws/market`

**Сообщение подписки:**
```json
{
  "auth": {},
  "markets": ["TOKEN_ID"],
  "assets_ids": ["TOKEN_ID"],
  "type": "market"
}
```

**Формат сообщений (Level 1 - Best Bid/Ask):**
```json
{
  "asset_id": "TOKEN_ID",
  "market": "market_type",
  "price": "0.75",
  "bids": [["0.74", "100"], ...],
  "asks": [["0.76", "100"], ...]
}
```

**Использование:**
- Подписываемся на token_id для получения обновлений цен
- Мониторим `asks[0][0]` для best ask price
- Используется для real-time мониторинга в последние секунды

---

### 3. Polymarket CLOB API (Order Execution)
**Base URL:** `https://clob.polymarket.com`

**Аутентификация:**
Требуется в `.env`:
```bash
PRIVATE_KEY=0x...
POLYGON_CHAIN_ID=137
CLOB_HOST=https://clob.polymarket.com
CLOB_API_KEY=...
CLOB_SECRET=...
CLOB_PASSPHRASE=...
```

**Основные операции:**
1. **Approve USDC** - `client.set_allowance()`
2. **Create Order** - `client.create_and_post_order(order_args)`

**Order Args:**
```python
OrderArgs(
    token_id="TOKEN_ID",
    price=0.99,
    size=1.0,
    side="BUY",
    order_type=OrderType.FOK  # Fill-or-Kill
)
```

---

### 4. Polymarket Web Interface
**Predictions Page:** `https://polymarket.com/predictions/15M`

**Полезно для:**
- Проверки доступных рынков
- Визуальной валидации данных API
- Понимания структуры названий рынков

---

## Важные константы и форматы

### Форматы времени
- **API endDate:** ISO 8601 с Z (`2026-01-24T00:35:00Z`)
- **Названия рынков:** 12-часовой формат (`7:30PM-7:35PM ET`)
- **Timezone:** Eastern Time (UTC-5)

### Token IDs
- Большие числа (256-bit)
- Возвращаются как строки в `clobTokenIds`
- Первый элемент = YES, второй = NO

### Condition IDs
- Hex строки с префиксом `0x`
- Уникальный идентификатор рынка
- Используется для отслеживания

---

## Типы рынков

### По длительности:
- **5-минутные** (5m): `7:30PM-7:35PM ET`
- **15-минутные** (15m): `7:30PM-7:45PM ET`
- **30-минутные** (30m): `7:30PM-8:00PM ET`
- **Часовые** (1h): `7PM ET`

### Ticker format:
- 5m: `btc-updown-5m-TIMESTAMP`
- 15m: `btc-updown-15m-TIMESTAMP`

---

## Стратегия и timing

### Параметры системы:
- **Poll Interval:** 90 секунд (как часто проверяем рынки) - оптимизировано для снижения нагрузки
- **Search Window:** 20 минут (ищем рынки ending in < 20 min) - согласно ТЗ
- **Trader Start Buffer:** 180 секунд (запускаем трейдер за 3 мин до закрытия)
- **Trigger Threshold:** 120 секунд (триггер срабатывает при ≤120s)
- **Price Threshold:** 0.50 (winning side определяется как price > 0.50)
- **Buy Price:** 0.99 (покупаем по $0.99)
- **Trade Size:** $1.01 (минимум $1 требуется Polymarket для market BUY orders)

### Workflow:
1. **Поиск** → Gamma API каждые 90 сек
2. **Фильтрация** → Рынки ending in < 20 min (5/15-минутные) - согласно ТЗ
3. **Запуск трейдера** → За 3 минуты до закрытия
4. **WebSocket мониторинг** → Real-time цены
5. **Триггер** → В окне ≤120 секунд
6. **Execution** → FOK ордер по $0.99

---

## 🔗 Reference Links

- **Teletype статья:** https://teletype.in/@maycluben/W7FTLpduOBQ
- **X статья:** https://x.com/thejayden/status/1995878076681535731
- **Binance (resolution source):** https://www.binance.com/en/trade/BTC_USDT
