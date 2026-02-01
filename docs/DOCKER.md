# Docker Deployment Guide

Complete guide for deploying the Polymarket trading bot using Docker and Docker Compose.

---

## 🐳 Quick Start

### Prerequisites
```bash
# Install Docker and Docker Compose
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER  # Add yourself to docker group
newgrp docker  # Activate group

# Verify installation
docker --version
docker compose version
```

### Initial Setup
```bash
# 1. Clone repository
git clone <your-repo-url>
cd baseTrader

# 2. Configure environment
cp .env.example .env
nano .env  # Set PRIVATE_KEY, POLYMARKET_PROXY_ADDRESS, etc.

# 3. Build and start
docker compose up -d --build

# 4. Check logs
docker compose logs -f
```

---

## 📦 Architecture

The system runs **two independent containers**:

```
┌─────────────────────────────────────────┐
│          Docker Host                    │
│                                         │
│  ┌────────────────────────────────┐   │
│  │   polymarket-trading-bot       │   │
│  │   Command: python main.py --live│   │
│  │   - Discovers markets           │   │
│  │   - Executes trades             │   │
│  │   - Logs to /app/log/          │   │
│  └────────────────────────────────┘   │
│                                         │
│  ┌────────────────────────────────┐   │
│  │  polymarket-position-settler   │   │
│  │  Command: position_settler     │   │
│  │  - Redeems winnings            │   │
│  │  - Tracks P&L                  │   │
│  │  - Logs to /app/log/          │   │
│  └────────────────────────────────┘   │
│                                         │
│  Volume: ./log ↔ /app/log             │
└─────────────────────────────────────────┘
```

---

## 🚀 Usage

### Build Images
```bash
# Build both images
docker compose build

# Build without cache (force rebuild)
docker compose build --no-cache

# Build specific service
docker compose build trading-bot
```

### Start Services
```bash
# Start all services (detached mode)
docker compose up -d

# Start specific service
docker compose up -d trading-bot

# Start and view logs (foreground)
docker compose up

# Start with build
docker compose up -d --build
```

### View Logs
```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f trading-bot
docker compose logs -f position-settler

# Last 100 lines
docker compose logs --tail=100 trading-bot

# Since specific time
docker compose logs --since 2026-02-01T12:00:00
```

### Stop Services
```bash
# Stop all services (keeps containers)
docker compose stop

# Stop specific service
docker compose stop trading-bot

# Stop and remove containers
docker compose down

# Stop, remove, and clean volumes
docker compose down -v
```

### Restart Services
```bash
# Restart all
docker compose restart

# Restart specific service
docker compose restart trading-bot

# Restart after code update
docker compose down
git pull
docker compose up -d --build
```

---

## 🔍 Monitoring

### Container Status
```bash
# List running containers
docker compose ps

# Detailed container info
docker inspect polymarket-trading-bot
```

### Resource Usage
```bash
# Real-time stats
docker stats

# Specific container
docker stats polymarket-trading-bot
```

### Execute Commands Inside Container
```bash
# Open shell
docker compose exec trading-bot bash

# Run Python REPL
docker compose exec trading-bot python

# Check Python version
docker compose exec trading-bot python --version

# View environment variables
docker compose exec trading-bot env

# Test dry run
docker compose exec trading-bot python main.py --once
```

### Health Checks
```bash
# Check container health
docker compose ps
# Look for "healthy" status

# Manual health check
docker compose exec trading-bot python -c "import sys; sys.exit(0)"
```

---

## 📂 File Structure

```
baseTrader/
├── Dockerfile              # Multi-stage build with uv
├── docker-compose.yml      # Service orchestration
├── .dockerignore          # Exclude files from build
├── .env                   # Environment variables (not in git)
├── pyproject.toml         # Python dependencies
├── uv.lock               # Locked dependencies
├── main.py               # Main trading bot
├── src/
│   ├── position_settler.py
│   ├── gamma_15m_finder.py
│   ├── hft_trader.py
│   └── ...
└── log/                  # Mounted volume (host ↔ container)
    ├── finder.log
    ├── trades.log
    ├── settler.log
    └── pnl.csv
```

---

## ⚙️ Configuration

### Environment Variables (.env)
```bash
# Polymarket Authentication
PRIVATE_KEY=0x...
POLYMARKET_PROXY_ADDRESS=0x...

# Network
POLYGON_CHAIN_ID=137
CLOB_HOST=https://clob.polymarket.com

# Optional: Customize behavior
# MARKET_QUERIES="Up or Down;Will"  # Override default queries
```

### docker-compose.yml Customization

**Change polling interval:**
```yaml
services:
  trading-bot:
    command: python main.py --live --poll-interval 60  # Check every 60s
```

**Change settler interval:**
```yaml
services:
  position-settler:
    command: python -m src.position_settler --daemon --live --interval 600  # 10 min
```

**Dry run mode (safe testing):**
```yaml
services:
  trading-bot:
    command: python main.py  # Remove --live flag

  position-settler:
    command: python -m src.position_settler --daemon  # Remove --live
```

---

## 🔄 Updates & Maintenance

### Update Code
```bash
# 1. Stop services
docker compose down

# 2. Pull latest code
git pull

# 3. Rebuild and restart
docker compose up -d --build
```

### View Logs on Host
```bash
# Logs are persisted in ./log/
tail -f log/finder.log
tail -f log/trades.log
tail -f log/settler.log
tail -f log/pnl.csv
```

### Backup Logs
```bash
# Create backup
tar -czf logs-backup-$(date +%Y%m%d).tar.gz log/

# Rotate old logs
rm log/*.log.old
```

### Clean Up Docker Resources
```bash
# Remove stopped containers
docker compose down

# Remove unused images
docker image prune -a

# Remove all unused resources
docker system prune -a --volumes

# Remove specific image
docker rmi polymarket-trading-bot
```

---

## 🐛 Troubleshooting

### Container Won't Start
```bash
# Check logs
docker compose logs trading-bot

# Check container status
docker compose ps

# Rebuild from scratch
docker compose down
docker compose build --no-cache
docker compose up -d
```

### "No such file or directory" Error
```bash
# Ensure .env exists
cp .env.example .env

# Check file permissions
ls -la .env
```

### "Cannot connect to Docker daemon"
```bash
# Start Docker service
sudo systemctl start docker

# Add user to docker group
sudo usermod -aG docker $USER
newgrp docker
```

### Container Crashes Immediately
```bash
# Check environment variables
docker compose config

# Test command manually
docker compose run --rm trading-bot python main.py --once

# Check for missing dependencies
docker compose exec trading-bot pip list
```

### High Memory Usage
```bash
# Set memory limits in docker-compose.yml
services:
  trading-bot:
    mem_limit: 512m
    mem_reservation: 256m
```

---

## 🔒 Security Best Practices

### 1. Protect Environment Variables
```bash
# Never commit .env to git
echo ".env" >> .gitignore

# Use Docker secrets (production)
docker secret create private_key ./private_key.txt
```

### 2. Run as Non-Root User
Add to Dockerfile:
```dockerfile
RUN useradd -m -u 1000 trader
USER trader
```

### 3. Network Isolation
```yaml
networks:
  polymarket:
    driver: bridge
    internal: true  # No external access
```

### 4. Read-Only Filesystem
```yaml
services:
  trading-bot:
    read_only: true
    tmpfs:
      - /tmp
```

---

## 📊 Production Deployment

### Option 1: Single Server with Docker Compose

**Setup systemd service:**
```ini
# /etc/systemd/system/polymarket-bot.service
[Unit]
Description=Polymarket Trading Bot
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/user/baseTrader
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable polymarket-bot
sudo systemctl start polymarket-bot
sudo systemctl status polymarket-bot
```

### Option 2: Docker Swarm (Multi-Node)
```bash
docker swarm init
docker stack deploy -c docker-compose.yml polymarket
docker stack services polymarket
```

### Option 3: Kubernetes (Advanced)
Convert compose to k8s:
```bash
kompose convert -f docker-compose.yml
kubectl apply -f .
```

---

## 📋 Quick Reference

| Task | Command |
|------|---------|
| Start all services | `docker compose up -d` |
| Stop all services | `docker compose down` |
| View logs | `docker compose logs -f` |
| Restart service | `docker compose restart trading-bot` |
| Rebuild images | `docker compose up -d --build` |
| Execute command | `docker compose exec trading-bot <command>` |
| View resource usage | `docker stats` |
| Clean up | `docker system prune -a` |
| Update code | `git pull && docker compose up -d --build` |

---

## 🆘 Support

### Debug Mode
Enable verbose logging:
```yaml
services:
  trading-bot:
    environment:
      - LOG_LEVEL=DEBUG
```

### Export Logs for Analysis
```bash
docker compose logs --no-color > debug.log
```

### Contact
- Check [docs/README.md](README.md) for additional documentation
- Review [docs/PROCESS-MANAGEMENT.md](PROCESS-MANAGEMENT.md) for alternatives to Docker
