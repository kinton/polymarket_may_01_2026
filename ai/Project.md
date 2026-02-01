# Documentation Index

> See `.github/copilot-instructions.md` for the main reference (VS Code Copilot Custom Instructions).

## 📚 Complete Documentation Structure

| File | Purpose |
|------|---------|
| **`.github/copilot-instructions.md`** | Main reference (VS Code Copilot) |
| **`ai/Soul.md`** | Project vision & constraints |
| **`docs/README.md`** | Documentation index |
| **`docs/ARCHITECTURE.md`** | System architecture & components |
| **`docs/API-INTEGRATION.md`** | API integration guide |
| **`docs/PROJECT.md`** | API endpoints & technical details |

## 🚀 Quick Start

```bash
uv run python main.py              # Dry run
uv run python main.py --live       # Live trading
uv run python main.py --once       # Single poll
```

## 💰 Position Settler (NEW)

Автоматический сборщик прибыли:

```bash
# Проверить позиции (dry run)
uv run python -m src.position_settler --once

# Запустить daemon mode (проверка каждые 5 минут)
uv run python -m src.position_settler --daemon --live

# Кастомный интервал (каждые 2 минуты)
uv run python -m src.position_settler --daemon --live --interval 120
```

### Стратегия работы:

1. **Fetch positions**: Получает историю трейдов → извлекает token_ids купленных токенов → проверяет баланс через `get_balance_allowance()`
2. **Check price**: Для каждой позиции получает текущую цену через `get_price(token_id, "BUY")`
3. **Sell if profitable**: Если цена >= $0.999 (99.9% вероятность выигрыша) → продаёт через market order (FOK)
4. **Hold otherwise**: Держит позицию до разрешения рынка (TODO: claim mechanism)

### API методы используемые:

- `client.get_trades(TradeParams(maker_address=...))` - история трейдов
- `client.get_balance_allowance(BalanceAllowanceParams(asset_type=CONDITIONAL, token_id=...))` - баланс токенов
- `client.get_price(token_id, "BUY")` - текущая цена (что платят за покупку = что мы получим при продаже)
- `client.create_market_order(MarketOrderArgs(token_id, amount, SELL))` - продажа токенов
- `client.post_order(signed_order, orderType=FOK)` - выполнение ордера

**For additional context, see `ai/Soul.md`**
