# Process Management Guide

## 🚀 How to Run the System

The trading system consists of TWO independent processes:

### 1. Main Trading Bot (market discovery + trading)
```bash
# Dry run (safe, recommended for testing)
uv run python main.py

# Live trading (REAL MONEY)
uv run python main.py --live

# One-time check and exit
uv run python main.py --once
```

### 2. Position Settler (P&L tracking + redemptions)
```bash
# Dry run (safe)
uv run python -m src.position_settler --once

# Live mode (real redemptions)
uv run python -m src.position_settler --daemon --live

# Custom interval (e.g., every 10 minutes)
uv run python -m src.position_settler --daemon --live --interval 600
```

---

## 🎮 Process Control

### ⚠️ Important: uv is NOT a process manager

`uv` is a Python package manager and runner. It does NOT:
- Keep processes running in background
- Restart processes on crash
- Provide process monitoring

When you run `uv run python main.py`, it launches Python and exits when the script stops.

### Recommended Tools for Process Management:

#### Option 1: tmux (Recommended for simplicity)
```bash
# Start main bot
tmux new -s trading
uv run python main.py --live
# Detach: Ctrl+B, then D

# Start settler
tmux new -s settler
uv run python -m src.position_settler --daemon --live
# Detach: Ctrl+B, then D

# List sessions
tmux ls

# Reattach to session
tmux attach -t trading
tmux attach -t settler

# Kill session
tmux kill-session -t trading
```

#### Option 2: screen
```bash
# Start bot
screen -S trading
uv run python main.py --live
# Detach: Ctrl+A, then D

# List sessions
screen -ls

# Reattach
screen -r trading
```

#### Option 3: nohup (Simple but no monitoring)
```bash
# Start in background
nohup uv run python main.py --live > log/main.out 2>&1 &
nohup uv run python -m src.position_settler --daemon --live > log/settler.out 2>&1 &

# View logs
tail -f log/main.out
tail -f log/settler.out

# Find PIDs
ps aux | grep python

# Kill process
kill <PID>
```

#### Option 4: supervisor (Production-grade)
```ini
# /etc/supervisor/conf.d/polymarket-bot.conf
[program:polymarket-trading]
command=/home/user/.local/bin/uv run python main.py --live
directory=/home/user/baseTrader
autostart=true
autorestart=true
stderr_logfile=/var/log/polymarket-trading.err.log
stdout_logfile=/var/log/polymarket-trading.out.log

[program:polymarket-settler]
command=/home/user/.local/bin/uv run python -m src.position_settler --daemon --live
directory=/home/user/baseTrader
autostart=true
autorestart=true
stderr_logfile=/var/log/polymarket-settler.err.log
stdout_logfile=/var/log/polymarket-settler.out.log
```

```bash
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl status
sudo supervisorctl restart polymarket-trading
```

---

## 🔄 Is main.py a "daemon"?

**No, but it behaves like one:**
- ✅ Runs continuously in infinite loop
- ✅ Polls for markets every 90 seconds
- ✅ Logs to files
- ✅ Handles graceful shutdown on Ctrl+C

**What it's missing:**
- ❌ Fork to background (stays in foreground)
- ❌ PID file management
- ❌ Signal handling (SIGHUP, SIGTERM)
- ❌ Auto-restart on crash

**Solution:** Use tmux/screen/supervisor to run it as a background service.

---

## 📊 Monitoring

### Check if processes are running:
```bash
ps aux | grep "python main.py"
ps aux | grep "position_settler"
```

### Check logs:
```bash
tail -f log/finder.log    # Market discovery
tail -f log/trades.log    # Trading activity
tail -f log/settler.log   # Position settlement
tail -f log/pnl.csv       # Profit/loss tracking
```

### System resource usage:
```bash
htop
# or
top -p $(pgrep -f "python main.py")
```

---

## 🛑 Stopping the System

### If running in tmux/screen:
```bash
tmux attach -t trading
# Press Ctrl+C to stop gracefully

tmux attach -t settler
# Press Ctrl+C
```

### If running with nohup:
```bash
ps aux | grep "python main.py"
kill <PID>  # Graceful shutdown

# Or force kill
kill -9 <PID>
```

### If running with supervisor:
```bash
sudo supervisorctl stop polymarket-trading
sudo supervisorctl stop polymarket-settler
```

---

## 🔧 Troubleshooting

### Process died unexpectedly?
```bash
# Check logs
tail -100 log/finder.log
tail -100 log/trades.log

# Check system logs
journalctl -u polymarket-bot  # if using systemd
tail -f /var/log/syslog | grep python
```

### Update code while running:
```bash
# 1. Stop processes
tmux attach -t trading  # Ctrl+C
tmux attach -t settler  # Ctrl+C

# 2. Update code
git pull
uv sync

# 3. Restart
tmux attach -t trading
uv run python main.py --live

tmux attach -t settler
uv run python -m src.position_settler --daemon --live
```

---

## 📋 Quick Reference

| Task | Command |
|------|---------|
| Start trading bot | `tmux new -s trading` → `uv run python main.py --live` |
| Start settler | `tmux new -s settler` → `uv run python -m src.position_settler --daemon --live` |
| Detach from tmux | `Ctrl+B`, then `D` |
| List tmux sessions | `tmux ls` |
| Reattach to session | `tmux attach -t trading` |
| View logs | `tail -f log/finder.log` |
| Stop process | `Ctrl+C` (in tmux session) |
| Kill tmux session | `tmux kill-session -t trading` |
