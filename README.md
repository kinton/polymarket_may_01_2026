# Polymarket Trading Bot

High-frequency trading bot for Polymarket 5/15-minute Bitcoin/Ethereum markets.

## 🎯 Features

- **Automated Market Discovery** - Finds active 5m/15m markets via Gamma API
- **Real-time Price Monitoring** - WebSocket connection for orderbook updates
- **Last-Second Execution** - Buys winning side at $0.99 within final second
- **Position Settlement** - Auto-sells @ $0.999 and claims resolved markets
- **Dry-run Mode** - Test safely before live trading

## 🚀 Quick Start

```bash
# Install dependencies
uv sync

# Check balance
uv run python scripts/check_balance.py

# Test in dry-run mode
uv run python main.py --once

# Run live (DANGER!)
uv run python main.py --live --size 2
```

## 📚 Documentation

- **[docs/README.md](docs/README.md)** - Full documentation index
- **[TRADING_GUIDE.md](TRADING_GUIDE.md)** - Strategy configuration
- **[DEPLOY.md](DEPLOY.md)** - Production deployment
- **[.github/copilot-instructions.md](.github/copilot-instructions.md)** - AI quick reference

## ⚙️ Configuration

### Environment Variables
Required `.env` variables:
```bash
PRIVATE_KEY=0x...                           # Ethereum private key
POLYMARKET_PROXY_ADDRESS=0x...             # Polymarket proxy wallet
POLYGON_CHAIN_ID=137
CLOB_HOST=https://clob.polymarket.com
```

### Trading Parameters
Edit `src/clob_types.py` to adjust strategy:
```python
BUY_PRICE = 0.99           # Maximum price to pay (99¢)
MIN_CONFIDENCE = 0.75      # Only buy if ≥75% confidence
TRIGGER_THRESHOLD = 120.0  # Start trading 120s before close
```

**Example:** With `MIN_CONFIDENCE = 0.75`, bot will:
- ✅ Buy YES if price is $0.75-$0.99 (75-99% chance)
- ❌ Skip YES if price is $0.51 (51% chance - too risky!)

This prevents buying positions that can easily flip in final seconds.

## 📊 Position Management

```bash
# Check positions (dry-run)
uv run python src/position_settler.py --once

# Auto-settle positions (live)
uv run python src/position_settler.py --daemon --live
```

## ⚠️ Critical Notes

- **Always use `uv`** - Never run scripts with plain `python`
- **Test first** - Use `--once` flag for single-poll testing
- **Check balance** - Minimum $2 USDC required
- **Approve USDC** - Run `uv run python scripts/approve.py` before trading

## 🔒 Security

- Never commit `.env` file
- Private keys stored locally only
- Use `.env.example` as template

## 📈 Status

- ✅ Market discovery (Bitcoin/Ethereum)
- ✅ Real-time price monitoring
- ✅ Dynamic winning side detection
- ✅ Live trading capability
- ✅ Position settlement & claiming

## 📝 License

MIT

## 🤝 Support

For issues or questions, see documentation in `docs/` folder.
