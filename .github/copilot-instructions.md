# Polymarket Trading Bot

High-frequency trading bot for Polymarket 5/15-minute Bitcoin/Ethereum markets. Automatically discovers, monitors, and executes trades in final seconds before market close.

## 🚀 Quick Start

```bash
uv run python main.py              # Dry run (safe)
uv run python main.py --live       # Live trading (real money)
uv run python main.py --once       # Single poll (testing)
```

## 🧪 Testing

After implementing a feature, run tests to validate:

```bash
uv run pytest test_*.py -v         # Run all tests
uv run pytest test_clob_types.py -v          # Test types & constants
uv run pytest test_market_parser.py -v       # Test parsing logic
```

Before committing:
```bash
uv run pytest test_*.py -v && uv run ruff check *.py && git add -A && git commit -m "message"
```

## 📚 Documentation

- **[.github/agents.md](.github/agents.md)** — Critical context (always relevant)
- **[docs/README.md](../docs/README.md)** — Full documentation index
- **[ai/Soul.md](../ai/Soul.md)** — Project vision

## 🛠️ Skills

- **[market-discovery](.github/skills/market-discovery/)** — Find active markets
- **[trading-execution](.github/skills/trading-execution/)** — Execute trades
- **[debugging](.github/skills/debugging/)** — Debug issues
