# Passivbot Troubleshooting Guide

Comprehensive troubleshooting for common issues during deployment and operation.

---

## Deployment Issues

### deploy-passivbot.sh fails

**Problem:** Script exits with error during initial deployment

**Check:**
```bash
# 1. Verify SSH connectivity
ssh -v root@142.93.143.178 "echo OK"

# 2. Check VPS is online and responsive
ping 142.93.143.178

# 3. Verify your SSH key is configured
ssh-keygen -l -f ~/.ssh/id_rsa
```

**Fix:**
- Ensure you have SSH access to the VPS
- Check that `root` user login is enabled
- Verify SSH key permissions: `chmod 600 ~/.ssh/id_rsa`
- Try deployment again: `./deploy-passivbot.sh`

---

### "Cannot clone passivbot repository"

**Problem:** Script fails at `git clone https://github.com/enarjord/passivbot.git`

**Cause:** Network issue or GitHub is blocked/slow

**Fix:**
```bash
# SSH to VPS and try manually
ssh root@142.93.143.178
cd /opt/passivbot
git clone https://github.com/enarjord/passivbot.git .
# If this works, installation is OK

# If it fails, try with retry
git clone --depth 1 https://github.com/enarjord/passivbot.git . || \
  (sleep 5 && git clone --depth 1 https://github.com/enarjord/passivbot.git .)
```

---

### "pip install -r requirements.txt" fails

**Problem:** Python package installation fails

**Cause:** Missing system dependencies or broken packages

**Fix on VPS:**
```bash
source /opt/passivbot/venv/bin/activate

# 1. Upgrade pip, setuptools, wheel
pip install -U pip setuptools wheel

# 2. Try installing requirements again
pip install -r /opt/passivbot/requirements.txt

# 3. If specific package fails, install individually
pip install aiohttp numpy pandas websockets

# 4. Check what's installed
pip list | grep -i passivbot
```

---

### "Rust build failed" (non-critical)

**Problem:** Maturin/Rust compilation fails

**Fix:** This is optional for speed. Bot works fine without it.
```bash
# Optional: skip Rust, continue without it
echo "Passivbot will run slower but still functional"
systemctl start passivbot
```

---

## API Key Issues

### "API key invalid" or "401 Unauthorized"

**Problem:** Bot won't authenticate with Bybit

**Check:**
```bash
# 1. View stored API key
cat /opt/passivbot/api_keys/api-keys.json | python3 -m json.tool

# 2. Verify JSON is valid
python3 -m json.tool /opt/passivbot/api_keys/api-keys.json

# 3. Check file permissions
ls -la /opt/passivbot/api_keys/api-keys.json
# Should be: -rw------- (600)
```

**Fix:**
```bash
# 1. Reconfigure API keys
bash /opt/passivbot/setup-api-keys.sh

# 2. Verify on Bybit that:
#    - API key has "Trade" permission enabled
#    - API key has "Position" permission enabled
#    - IP whitelist includes 142.93.143.178
#    - API key is not expired or disabled

# 3. Restart bot
systemctl restart passivbot
journalctl -u passivbot -f
```

---

### "Account not found" or "User not found"

**Problem:** API key exists but is not recognized

**Cause:** Wrong exchange type or account type

**Fix:**
```bash
# Check config specifies correct exchange
grep '"exchange"' /opt/passivbot/configs/live/active.json
# Should be: "exchange": "bybit"

grep '"account_type"' /opt/passivbot/configs/live/active.json
# Should be: "account_type": "linear" (for perpetuals)
```

---

## Configuration Issues

### "Config not found" error

**Problem:** Bot starts but can't find configuration

**Fix:**
```bash
# 1. Ensure live config exists
ls -la /opt/passivbot/configs/live/active.json

# 2. If not, copy one
cp /opt/passivbot/configs/bybit-100-conservative.json \
   /opt/passivbot/configs/live/active.json

# 3. Verify it's valid JSON
python3 -m json.tool /opt/passivbot/configs/live/active.json

# 4. Restart bot
systemctl restart passivbot
```

---

### "Invalid configuration" or parsing errors

**Problem:** Config JSON has syntax errors

**Fix:**
```bash
# 1. Check syntax
python3 -m json.tool /opt/passivbot/configs/live/active.json

# 2. If syntax error shown, fix it:
nano /opt/passivbot/configs/live/active.json
# Common issues: missing commas, trailing commas, wrong quotes

# 3. Validate again
python3 -m json.tool /opt/passivbot/configs/live/active.json

# 4. Restart
systemctl restart passivbot
```

---

## Bot Operation Issues

### Bot not placing orders

**Problem:** Bot running but not trading

**Check:**
```bash
# 1. View logs for error messages
journalctl -u passivbot -p err --no-pager

# 2. Check if connected to Bybit
journalctl -u passivbot | grep -i "connected\|connection"

# 3. Check if position size is 0
journalctl -u passivbot | grep -i "position\|balance\|size"

# 4. View last 50 lines for context
journalctl -u passivbot -n 50
```

**Fix:**
```bash
# Most common issues:

# 1. No USDT balance
#    → Deposit USDT to Bybit account

# 2. Leverage not set correctly
#    → Check config: "leverage": 3
#    → Set leverage on Bybit manually if needed

# 3. API key missing trade permission
#    → Go to Bybit → Account → API Management
#    → Edit key → Enable "Trade"

# 4. Position is open from before
#    → Go to Bybit → Positions → Close any open positions

# 5. Market is in maintenance
#    → Wait for market to reopen

# 6. Grid orders already exist (from previous run)
#    → Cancel all orders on Bybit
#    → Wait 5 seconds
#    → Restart bot: systemctl restart passivbot
```

---

### Bot crashes with traceback

**Problem:** Bot starts then crashes with Python error

**Fix:**
```bash
# 1. Get the full error
journalctl -u passivbot -p err --no-pager | tail -100

# 2. Check Python version
/opt/passivbot/venv/bin/python3 --version
# Should be Python 3.12.x

# 3. Check if dependencies are installed
source /opt/passivbot/venv/bin/activate
python3 -c "import passivbot; print('OK')"

# 4. Reinstall if needed
pip install -r /opt/passivbot/requirements.txt --force-reinstall

# 5. Restart
systemctl restart passivbot
```

---

### "Market is closed" or "Symbol not found"

**Problem:** Bot can't trade BTCUSDT/ETHUSDT

**Fix:**
```bash
# 1. Check symbol in config
grep '"symbol"' /opt/passivbot/configs/live/active.json
# Should be: "symbol": "BTCUSDT" (exactly, case-sensitive)

# 2. Verify symbol is available on Bybit
#    → Go to Bybit → Derivatives → BTCUSDT
#    → Should show 24/7 trading available

# 3. Check market hours
#    Bybit perpetuals trade 24/7, so this shouldn't happen

# 4. If symbol issue, fix config:
nano /opt/passivbot/configs/live/active.json
# Change symbol to correct name
systemctl restart passivbot
```

---

## Performance Issues

### High CPU usage (>80%)

**Problem:** Bot consuming too much CPU

**Fix:**
```bash
# 1. Check what's consuming CPU
top -p $(pgrep -f passivbot.py)

# 2. Check if backtesting is running (high CPU)
ps aux | grep passivbot
# Should show only one process

# 3. If high CPU normal, can limit with cgroup
systemctl cat passivbot | grep CPU
# Already set to 50% in service file

# 4. Reduce bot frequency if needed (in config)
nano /opt/passivbot/configs/live/active.json
# Check grid_spacing (wider = fewer orders = less CPU)
```

---

### High memory usage (>400M)

**Problem:** Bot using excessive memory

**Fix:**
```bash
# 1. Check memory usage
ps aux | grep passivbot | grep -v grep

# 2. Check service limits
systemctl cat passivbot | grep Memory

# 3. If above 512M limit, restart
systemctl restart passivbot

# 4. Check for memory leak in logs
journalctl -u passivbot | tail -50 | grep -i "memory\|leak\|error"

# 5. If persistent, update service limit:
nano /etc/systemd/system/passivbot.service
# Change: MemoryLimit=1G
systemctl daemon-reload
systemctl restart passivbot
```

---

### Slow order execution / High latency

**Problem:** Orders take too long to fill

**Fix:**
```bash
# 1. Check network latency to Bybit
ping api.bybit.com  # Should be < 100ms

# 2. Check bot's ping time in logs
journalctl -u passivbot | grep -i "ping\|latency"

# 3. Can't reduce latency much, but can:
#    - Widen grid_spacing (fewer orders)
#    - Reduce entry_qty_pct (smaller orders)
#    - Use larger markup (easier to fill)

# 4. If consistently slow (>500ms), might be:
#    - Network issue → restart bot
#    - Bybit API under load → wait and retry
#    - VPS network saturation → check `nethogs`
```

---

## Monitoring & Logging Issues

### Can't view logs with manage-bot.sh

**Problem:** `./manage-bot.sh logs` or `follow` fails

**Fix:**
```bash
# 1. Check SSH connectivity
ssh root@142.93.143.178 "echo OK"

# 2. Check manage-bot.sh is executable
ls -la /Users/chudinnorukam/Projects/business/passivbot-deploy/manage-bot.sh
chmod +x /Users/chudinnorukam/Projects/business/passivbot-deploy/manage-bot.sh

# 3. Try manually:
ssh root@142.93.143.178 "journalctl -u passivbot -n 50"

# 4. If manual SSH works, script should too
```

---

### No logs being generated

**Problem:** `journalctl -u passivbot` returns nothing

**Fix:**
```bash
# 1. Check if service is running
systemctl status passivbot

# 2. Check if journalctl has any logs
journalctl -u passivbot --all --no-pager

# 3. If service is running but no logs:
systemctl restart passivbot

# 4. Check if stdout/stderr are configured
systemctl cat passivbot | grep -i "standard"
# Should have: StandardOutput=journal StandardError=journal

# 5. Check journal is working
journalctl -f  # In another terminal, should show system logs

# 6. If still no logs, may be permission issue:
journalctl --stat  # Check journal status
```

---

## Network & Connectivity Issues

### "Connection refused" or "Network unreachable"

**Problem:** Bot can't connect to Bybit API

**Fix:**
```bash
# 1. Check VPS has internet
ssh root@142.93.143.178 "ping 8.8.8.8"

# 2. Check if Bybit API is reachable
ssh root@142.93.143.178 "curl -I https://api.bybit.com"
# Should return HTTP 200 or 301

# 3. Check if firewall is blocking
ufw status
# If active, ensure port 443 is open: sudo ufw allow 443

# 4. Try connecting manually
ssh root@142.93.143.178
curl -I https://api.bybit.com

# 5. If Bybit API is down, wait for it to recover
#    Check status: https://status.bybit.com

# 6. If VPS has no internet, contact provider
```

---

### Frequent disconnections (reconnecting every minute)

**Problem:** Bot logs show repeated "reconnecting"

**Cause:** Unstable connection or Bybit rate limiting

**Fix:**
```bash
# 1. Check VPS network stability
ssh root@142.93.143.178
ping api.bybit.com  # Should be consistent latency

# 2. Check for rate limiting in logs
journalctl -u passivbot | grep -i "rate\|429\|too many"

# 3. If rate limiting, reduce request frequency:
nano /opt/passivbot/configs/live/active.json
# Increase grid_spacing (fewer orders = fewer API calls)

# 4. Check if other processes are using network
nethogs -p  # Show network usage by process

# 5. Restart bot
systemctl restart passivbot

# 6. If reconnecting is normal, that's OK
#    (bot will reconnect on temporary network hiccup)
```

---

## Emergency Procedures

### Stop bot immediately (emergency)

```bash
# Stop the service
systemctl stop passivbot

# Verify it stopped
systemctl status passivbot

# Close all open positions on Bybit manually:
# 1. Go to Bybit → Positions
# 2. For each open position, click "Close" or "Liquidate"
# 3. Confirm and wait for fill
```

---

### Restart bot cleanly

```bash
# 1. Stop bot
systemctl stop passivbot

# 2. Wait 5 seconds
sleep 5

# 3. Restart
systemctl start passivbot

# 4. Check startup
sleep 2
journalctl -u passivbot -n 20
```

---

### Reset everything and start fresh

```bash
# WARNING: This deletes everything except configs

ssh root@142.93.143.178

# 1. Stop bot
systemctl stop passivbot

# 2. Backup current config
cp /opt/passivbot/configs/live/active.json /tmp/backup.json

# 3. Clean data/logs
rm -rf /opt/passivbot/data/*
rm -rf /opt/passivbot/logs/*

# 4. Restore config
cp /tmp/backup.json /opt/passivbot/configs/live/active.json

# 5. Start fresh
systemctl start passivbot
journalctl -u passivbot -f
```

---

## Getting Help

### Collect diagnostic info

When seeking help, provide:

```bash
# 1. Bot status
./manage-bot.sh health

# 2. Last 100 lines of logs
./manage-bot.sh logs 100 > /tmp/passivbot-logs.txt

# 3. Configuration (redact API keys)
cat /opt/passivbot/configs/live/active.json | python3 -m json.tool

# 4. System info
ssh root@142.93.143.178 << 'EOF'
echo "=== System ==="
uname -a
echo "=== Python ==="
/opt/passivbot/venv/bin/python3 --version
echo "=== Disk ==="
df -h /opt/passivbot
echo "=== Memory ==="
free -h
EOF
```

### Support Resources

- **Passivbot Issues:** https://github.com/enarjord/passivbot/issues
- **Bybit API Status:** https://status.bybit.com
- **Documentation:** See README.md in this kit

---

## Prevention: Maintenance Checklist

Run weekly to prevent issues:

```bash
# 1. Check bot is running
./manage-bot.sh status

# 2. Review error logs
./manage-bot.sh errors

# 3. Check balance on Bybit
# (Manual check in Bybit UI)

# 4. Review P&L
# (Manual check in Bybit UI)

# 5. Check disk space
ssh root@142.93.143.178 "df -h /opt/passivbot"

# 6. Check memory usage
ssh root@142.93.143.178 "free -h"

# 7. Update passivbot (optional)
ssh root@142.93.143.178
cd /opt/passivbot
git pull
systemctl restart passivbot
```

---

**Last Updated:** 2026-02-05
**Version:** 1.0
