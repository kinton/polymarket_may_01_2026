# Secure .env File Guide

## ⚠️ NEVER commit .env to git!

Always keep `.env` in `.gitignore`. Never send via email/Slack.

---

## 🔒 Secure Transfer Methods

### Method 1: SSH/SCP (Recommended)

Transfer directly to server:
```bash
# Copy .env to server
scp .env user@server:/path/to/baseTrader/.env

# Set secure permissions
ssh user@server "chmod 600 /path/to/baseTrader/.env"
```

**Benefits:**
- ✅ Encrypted in transit (SSH)
- ✅ No intermediary storage
- ✅ Direct to production location

---

### Method 2: Encrypted File

Use `age` or `gpg` for temporary sharing:

**Using `age` (simpler):**
```bash
# Encrypt
brew install age  # or: apt install age
age -p -o .env.age .env
# Enter passphrase, share .env.age

# Decrypt on server
age -d .env.age > .env
rm .env.age  # Delete immediately
chmod 600 .env
```

**Using `gpg` (if already using PGP keys):**
```bash
# Encrypt
gpg -c .env  # Creates .env.gpg

# Decrypt on server
gpg -d .env.gpg > .env
rm .env.gpg
chmod 600 .env
```

---

### Method 3: Password Manager

**1Password / Bitwarden / LastPass:**
```bash
Share via 1Password, LastPass, Bitwarden:

1. Create "Secure Note" in password manager
2. Paste .env contents
3. Share note with teammate
4. They copy to server, set permissions
5. Delete shared note

**Benefits:**
- ✅ Encrypted at rest
- ✅ Audit log of access
- ✅ Revocable sharing

---

### Method 4: Manual Entry (Most Secure)

For critical production systems:
```bash
# On server, create .env manually
nano .env
# Type each line manually (never leaves your terminal)
chmod 600 .env
```

**When to use:**
- Production environments with high security requirements
- When .env contains highly sensitive keys

## 🚫 What NOT to Do

### ❌ Never:
1. **Commit .env to git**
   ```bash
   # Check if .env is tracked
   git ls-files | grep .env
   
   # Remove from git history
   git filter-branch --force --index-filter \
     'git rm --cached --ignore-unmatch .env' \
     --prune-empty --tag-name-filter cat -- --all
   ```

2. **Send via unencrypted channels**
   - Email (can be intercepted)
   - Slack/Discord (logs persist)
   - SMS (unencrypted)

3. **Store in public cloud storage**
   - Google Drive, Dropbox (unless encrypted)

4. **Share via screenshots** (OCR can read them)

---

## ✅ Best Practices

### 1. Use .env.example (Template)
```bash
# .env.example (safe to commit)
PRIVATE_KEY=your_private_key_here
POLYMARKET_PROXY_ADDRESS=your_proxy_address_here
POLYGON_CHAIN_ID=137
```

### 2. Separate Dev/Prod
```bash
.env.development  # Lower-value keys for testing
.env.production   # High-value keys, never committed
```

### 3. Rotate Keys Regularly
```bash
# Generate new key
# Update secrets
# Revoke old key
```

### 4. Use Hardware Wallets (Best)
- Ledger/Trezor for transaction signing
- Never expose private key to server

---

## 🔐 Quick Setup for Docker Secrets

```bash
# 1. Create secret files
mkdir -p secrets
echo "$PRIVATE_KEY" > secrets/private_key.txt
echo "$PROXY_ADDRESS" > secrets/polymarket_proxy_address.txt
chmod 600 secrets/*

# 2. Update code to read from files
# In hft_trader.py and position_settler.py:
```

```python
import os

def get_private_key():
    """Read private key from secret file or env var."""
    key_file = os.getenv("PRIVATE_KEY_FILE")
    if key_file and os.path.exists(key_file):
        with open(key_file) as f:
            return f.read().strip()
    return os.getenv("PRIVATE_KEY")
```

```bash
# 3. Deploy
docker compose up -d
```

---�️ Best Practices

### � If .env Leaked

**Immediate Actions:**

1. **Rotate ALL credentials** in leaked .env:
   ```bash
   # Generate new private key
   # Update POLYMARKET_PROXY_ADDRESS if needed
   # Change any API keys
   ```

2. **Check git history:**
   ```bash
   git log --all --full-history -- .env
   # If found, use BFG Repo-Cleaner to purge
   ```

3. **Revoke access:**
   - If sent via cloud storage, revoke share links
   - If sent via password manager, revoke access
   - Check audit logs for unauthorized access

4. **Monitor accounts:**
   - Check Polymarket balance
   - Review recent transactions
   - Enable alerts

---

## ✅ Recommended Setup for Production

```bash
# 1. On local machine, prepare .env
cp .env.example .env
nano .env  # Fill in real values
chmod 600 .env

# 2. Transfer via SCP (most reliable)
scp .env user@server:/opt/baseTrader/.env
ssh user@server "chmod 600 /opt/baseTrader/.env"

# 3. Verify on server
ssh user@server
cd /opt/baseTrader
ls -la .env  # Check permissions
cat .env     # Verify content (then clear terminal!)

# 4. Start containers
docker compose up -d

# 5. Remove local .env (if not needed for development)
rm .env  # Optional, but safer
```

---

## 📋 Security Checklist

Before deployment:
- [ ] `.env` in `.gitignore`
- [ ] `.env` has `chmod 600` permissions
- [ ] No `.env` commits in git history
- [ ] Transferred via encrypted channel (SSH/age/gpg)
- [ ] PRIVATE_KEY never logged or printed
- [ ] Docker logs don't expose secrets
- [ ] Regular key rotation schedule established
- [ ] Team knows leak response procedure

---

## 🔗 Related Documentation

- [Docker Deployment](DOCKER.md) - Container setup
- [Architecture](ARCHITECTURE.md) - System overview
- [Project Specs](PROJECT.md) - API details