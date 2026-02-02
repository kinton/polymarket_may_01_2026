# Polymarket Trading Bot

High-frequency trading bot for Polymarket 5/15-minute Bitcoin/Ethereum markets. Automatically discovers, monitors, and executes trades in final seconds before market close.

## ⚠️ CRITICAL: Always use `uv`

This project uses `uv` as package manager and runner. **NEVER** use `python`, `python3`, or `pip` directly.

### Correct commands:
```bash
uv run python main.py              # Dry run (safe)
uv run python main.py --live       # Live trading (real money)
uv run python main.py --once       # Single poll (testing)
uv run python check_balance.py    # Check USDC balance
uv run pytest test_*.py            # Run tests
```

### Wrong commands (DO NOT USE):
```bash
python main.py          # ❌ Missing uv
python3 script.py       # ❌ Missing uv
pip install package     # ❌ Use: uv add package
```

## 🧪 Testing

After implementing a feature, run tests to validate:

```bash
uv run pytest test_*.py -v                    # Run all tests
uv run pytest test_clob_types.py -v           # Test types & constants
uv run pytest test_market_parser.py -v        # Test parsing logic
```

Before committing:
```bash
uv run pytest test_*.py -v && uv run ruff check *.py && git add -A && git commit -m "message"
```

## 🔧 Development Commands

```bash
# Run scripts (always prefix with 'uv run python')
uv run python main.py --once                  # Test single poll
uv run python -m src.gamma_15m_finder         # Test market finder
uv run python check_current_markets.py        # Check available markets

# Install packages
uv add package-name                           # Add new dependency
uv sync                                       # Sync dependencies

# Code quality
uv run ruff check *.py src/*.py              # Lint code
uv run ruff format *.py src/*.py             # Format code
```

## 📚 Documentation

- **[.github/agents.md](.github/agents.md)** — Critical context (always relevant)
- **[docs/README.md](../docs/README.md)** — Full documentation index
- **[ai/Soul.md](../ai/Soul.md)** — Project vision

## 🛠️ Skills

- **[market-discovery](.github/skills/market-discovery/)** — Find active markets
- **[trading-execution](.github/skills/trading-execution/)** — Execute trades
- **[debugging](.github/skills/debugging/)** — Debug issues
