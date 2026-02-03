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

Required `.env` variables:
```bash
PRIVATE_KEY=0x...                           # Ethereum private key
POLYMARKET_PROXY_ADDRESS=0x...             # Polymarket proxy wallet
POLYGON_CHAIN_ID=137
CLOB_HOST=https://clob.polymarket.com
```

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
