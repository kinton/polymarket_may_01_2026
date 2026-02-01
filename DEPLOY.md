# 🚀 Production Deployment Guide

Пошаговая инструкция для запуска бота на сервере.

---

## 📋 Предварительные требования

### На локальной машине:
- Git
- Доступ к серверу по SSH

### На сервере:
- Ubuntu 20.04+ / Debian 11+
- Docker и Docker Compose
- 1GB+ RAM
- Доступ в интернет

---

## 🔧 Шаг 1: Подготовка сервера

```bash
# Подключаемся к серверу
ssh user@your-server.com

# Устанавливаем Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER
newgrp docker  # Активировать группу без logout

# Проверяем установку
docker --version
docker compose version

# Создаём директорию для проекта
mkdir -p ~/baseTrader
cd ~/baseTrader
```

---

## 📦 Шаг 2: Загрузка кода

### Вариант A: Через Git (рекомендуется)
```bash
# На сервере
cd ~/baseTrader
git clone https://github.com/your-username/baseTrader.git .
```

### Вариант B: Через SCP
```bash
# На локальной машине
cd /path/to/baseTrader
tar czf baseTrader.tar.gz --exclude=.git --exclude=__pycache__ --exclude=.env --exclude=log .
scp baseTrader.tar.gz user@server:~/

# На сервере
cd ~/baseTrader
tar xzf ~/baseTrader.tar.gz
rm ~/baseTrader.tar.gz
```

---

## 🔑 Шаг 3: Настройка .env файла

### На локальной машине:

```bash
cd /path/to/baseTrader

# Создаём .env из примера
cp .env.example .env
nano .env  # Заполняем реальными значениями
```

Обязательные параметры:
```env
PRIVATE_KEY=0x1234...abcd                    # Ваш приватный ключ
POLYMARKET_PROXY_ADDRESS=0x5678...efgh       # Адрес прокси-кошелька
POLYGON_CHAIN_ID=137                         # Polygon Mainnet
CLOB_API_URL=https://clob.polymarket.com
GAMMA_API_URL=https://gamma-api.polymarket.com
```

### Передача на сервер (безопасно):

```bash
# Устанавливаем права (только для владельца)
chmod 600 .env

# Копируем через SSH (зашифрованный канал)
scp .env user@server:~/baseTrader/.env

# Проверяем на сервере
ssh user@server
cd ~/baseTrader
ls -la .env  # Должно быть: -rw------- (600)
cat .env     # Проверить содержимое
```

**⚠️ ВАЖНО:** После проверки очистите терминал (`clear` или Cmd+K), чтобы не оставлять ключи в истории.

---

## 🐳 Шаг 4: Запуск через Docker

### Запуск в режиме dry-run (тестирование):

```bash
cd ~/baseTrader

# Сначала проверяем без реальных сделок
docker compose up
# Ctrl+C для остановки после проверки
```

Проверьте логи:
- ✅ "Trading Bot Runner Initialized"
- ✅ "Found X events, Y markets"
- ✅ "Trader initialized | DRY RUN"
- ✅ WebSocket подключился

### Запуск в live-режиме (реальные деньги):

```bash
# Редактируем docker-compose.yml
nano docker-compose.yml

# Меняем строку в сервисе trading-bot:
command: python main.py --live --size 10  # Измените --size на нужную сумму

# В сервисе position-settler:
command: python -m src.position_settler --daemon --live --interval 300

# Запускаем в фоне
docker compose up -d --build

# Проверяем статус
docker compose ps
docker compose logs -f  # Ctrl+C для выхода (контейнеры продолжат работать)
```

---

## 📊 Шаг 5: Мониторинг

### Просмотр логов:

```bash
# Все логи в реальном времени
docker compose logs -f

# Только trading-bot
docker compose logs -f trading-bot

# Только position-settler
docker compose logs -f position-settler

# Последние 100 строк
docker compose logs --tail=100
```

### Проверка логов на диске:

```bash
cd ~/baseTrader/log

# Лог поиска рынков и трейдов
tail -f finder.log

# Лог P&L
cat pnl.csv
```

### Проверка статуса контейнеров:

```bash
docker compose ps
# Должно быть: State = Up

# Подробная информация
docker compose top
```

---

## 🛠️ Управление

### Остановка:

```bash
cd ~/baseTrader
docker compose stop  # Мягкая остановка
docker compose down  # Полная остановка + удаление контейнеров
```

### Перезапуск:

```bash
docker compose restart
```

### Обновление кода:

```bash
cd ~/baseTrader

# Остановка
docker compose down

# Обновление через git
git pull origin main

# Или через SCP (с локальной машины)
scp -r . user@server:~/baseTrader/

# Пересборка и запуск
docker compose up -d --build
```

### Изменение параметров:

```bash
# Изменить сумму трейда
nano docker-compose.yml  # Поменять --size 10 на нужное значение
docker compose up -d --force-recreate

# Изменить интервал проверки позиций
nano docker-compose.yml  # Поменять --interval 300 на нужное
docker compose restart position-settler
```

---

## 🔍 Диагностика проблем

### Контейнер не запускается:

```bash
# Проверить логи ошибок
docker compose logs trading-bot
docker compose logs position-settler

# Проверить .env файл
cat .env | grep -v "^#"
```

### Нет сделок:

```bash
# Проверить, что найдены рынки
docker compose logs trading-bot | grep "Found"

# Проверить, что трейдер инициализирован
docker compose logs trading-bot | grep "Trader initialized"

# Проверить баланс
cd ~/baseTrader
docker compose exec trading-bot python check_balance.py
```

### WebSocket ошибки:

```bash
# Проверить подключение к CLOB
docker compose logs trading-bot | grep -i websocket

# Перезапустить контейнер
docker compose restart trading-bot
```

### Проблемы с PRIVATE_KEY:

```bash
# Проверить формат ключа в .env
cat .env | grep PRIVATE_KEY
# Должен быть: PRIVATE_KEY=0x...

# Проверить, что ключ загружен в контейнер
docker compose exec trading-bot env | grep PRIVATE_KEY
```

---

## 🔒 Безопасность

### Проверка прав доступа:

```bash
# .env должен быть 600 (только владелец)
ls -la .env

# Если нет:
chmod 600 .env
```

### Проверка git:

```bash
# Убедитесь, что .env НЕ в git
git check-ignore .env  # Должно вернуть: .env

# Проверка истории
git log --all --full-history -- .env  # Должно быть пусто
```

### Регулярная ротация ключей:

```bash
# Каждый месяц:
# 1. Сгенерировать новый PRIVATE_KEY
# 2. Обновить .env на сервере
# 3. Перезапустить: docker compose restart
# 4. Аннулировать старый ключ
```

---

## 📈 Оптимизация

### Производительность:

```bash
# Увеличить размер swap (если мало RAM)
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

### Автозапуск при перезагрузке:

Docker Compose уже настроен с `restart: unless-stopped` - контейнеры автоматически запустятся после перезагрузки сервера.

Проверка:
```bash
# Перезагрузить сервер
sudo reboot

# После перезагрузки (через 1-2 минуты)
ssh user@server
docker compose ps  # Контейнеры должны быть запущены
```

---

## 📋 Чеклист перед продакшн запуском

- [ ] `.env` файл настроен с реальными ключами
- [ ] `.env` имеет права 600
- [ ] `.env` НЕ в git
- [ ] Проверили dry-run режим: `docker compose up` (без `-d`)
- [ ] Логи показывают успешную инициализацию
- [ ] WebSocket подключён
- [ ] Баланс USDC достаточен для трейдов
- [ ] Установлена нужная сумма в `--size`
- [ ] Настроен мониторинг логов
- [ ] Знаете как остановить: `docker compose down`

---

## 🆘 Экстренная остановка

```bash
# НЕМЕДЛЕННАЯ остановка всех контейнеров
docker compose kill

# Или через подключение к серверу
ssh user@server "cd ~/baseTrader && docker compose kill"
```

---

## 📞 Полезные команды

```bash
# Статус всего
docker compose ps && docker compose logs --tail=20

# Баланс кошелька
docker compose exec trading-bot python check_balance.py

# Список активных позиций
docker compose exec position-settler python -m src.position_settler --once

# Рестарт только одного сервиса
docker compose restart trading-bot

# Посмотреть использование ресурсов
docker stats

# Очистка старых образов (освободить место)
docker system prune -a
```

---

## 📚 Дополнительная документация

- [docs/DOCKER.md](docs/DOCKER.md) - Детальная документация по Docker
- [docs/SECURITY.md](docs/SECURITY.md) - Безопасная передача .env
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - Архитектура системы
- [docs/PROJECT.md](docs/PROJECT.md) - Технические спецификации
