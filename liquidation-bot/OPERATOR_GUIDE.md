# Operator Guide

Quick reference for running and monitoring the liquidation bot.

## Daily Operations (5 minutes)

### Check Bot Status

```bash
# Is the service running?
systemctl status liquidation-bot

# Expected: ● liquidation-bot.service - Aave V3 Liquidation Bot
#           Loaded: loaded (...; enabled; ...)
#           Active: active (running)
```

### View Recent Activity

```bash
# Last 30 lines of logs
journalctl -u liquidation-bot -n 30

# Expected: Connected to Arbitrum
#           Starting main loop
#           Scan complete: X borrowers, Y liquidatable
```

### Check Health Status

```bash
# View JSON status (auto-updated every 5 min)
cat data/health_status.json | jq .

# Expected output:
# {
#   "uptime": "2h 45m 30s",
#   "total_scans": 823,
#   "total_liquidatable": 12,
#   "total_liquidations": 3,
#   "total_profit": 645.23,
#   "current_balance_usdc": 645.23,
#   "error_count": 0
# }
```

### Check Database

```bash
# View last 5 liquidations
sqlite3 data/liquidations.db \
  "SELECT user, debt_asset, status, actual_profit FROM liquidations ORDER BY id DESC LIMIT 5;"

# Expected: Shows recent liquidation attempts with profits
```

---

## If Bot Stops Running

### Step 1: Check Status

```bash
systemctl status liquidation-bot
```

**If "failed"**: Bot crashed. Check logs and restart.
**If "inactive"**: Bot was stopped. Restart it.

### Step 2: Check Logs

```bash
# Last 100 lines
journalctl -u liquidation-bot -n 100

# Search for errors
journalctl -u liquidation-bot | grep -i error | head -20

# Real-time monitoring
journalctl -u liquidation-bot -f
```

### Step 3: Restart Service

```bash
# Stop (if running)
sudo systemctl stop liquidation-bot

# Start again
sudo systemctl start liquidation-bot

# Verify
systemctl status liquidation-bot
```

### Step 4: Monitor for Stability

```bash
# Watch logs in real-time for 2 minutes
journalctl -u liquidation-bot -f &
sleep 120
kill %1

# Check final status
systemctl status liquidation-bot
```

---

## If Bot is Consuming Too Many Resources

### Check Resource Usage

```bash
# Monitor CPU and memory
top -p $(systemctl show -p MainPID --value liquidation-bot)

# If memory > 200MB: likely a memory leak
# If CPU > 50% consistently: likely hanging on RPC calls
```

### Quick Fix

```bash
# Restart the service
sudo systemctl restart liquidation-bot

# Monitor memory over next hour
watch -n 10 'ps aux | grep run_liquidation_bot | grep -v grep'
```

### Long-term Fix

If restarts don't help:
1. Check RPC endpoint is responsive: `curl https://arb1.arbitrum.io/rpc`
2. Consider switching to paid RPC (Alchemy, Quicknode)
3. Increase CHECK_INTERVAL in .env (12 → 30 seconds)

---

## If Bot is Not Finding Liquidations

### This is Normal!

Liquidatable positions are rare. The bot correctly identifies and skips unprofitable ones.

### Verify Bot is Working

```bash
# Check borrower count
sqlite3 data/liquidations.db "SELECT MAX(total_collateral_usd) FROM opportunities WHERE liquidatable=1;"

# If NULL: No liquidatable positions exist right now
# If number: Bot found them but profit < MIN_PROFIT_USD

# Check MIN_PROFIT_USD setting
grep MIN_PROFIT /opt/liquidation-bot/.env
```

### Lower the Threshold (Risky)

Only if you want to execute smaller profitable trades:

```bash
# Edit .env
nano /opt/liquidation-bot/.env

# Change MIN_PROFIT_USD from 5.0 to 1.0
# Save and restart

sudo systemctl restart liquidation-bot
```

**Warning**: Smaller profits = more transactions with same gas cost = lower ROI

---

## If a Liquidation Failed

### Check the Error

```bash
# View failed liquidation details
sqlite3 data/liquidations.db \
  "SELECT id, user, status, error_msg FROM liquidations WHERE status='failed' ORDER BY id DESC LIMIT 1;"

# Look at the error_msg column
```

### Common Errors & Solutions

| Error | Cause | Solution |
|-------|-------|----------|
| "contract reverted" | Slippage too tight or collateral seized wrong | Increase slippage_tolerance in config.py |
| "insufficient balance" | Account out of gas or USDC | Check balance: `curl https://arb1.arbitrum.io/rpc -X POST -d '...'` |
| "execution reverted" | User's health factor improved mid-tx | Normal, skip and continue |
| "timeout" | RPC too slow | Consider paid RPC (Alchemy) |

### Check Account Balance

```bash
# View current USDC balance (from health status)
cat data/health_status.json | grep current_balance

# Should be > $1 to continue operating
```

---

## Weekly Maintenance

### Backup Database

```bash
# Create backup with date
cp data/liquidations.db data/liquidations.db.$(date +%Y%m%d).bak

# Verify backup
ls -lh data/liquidations.db*

# Keep 4 most recent backups, delete older ones
ls -t data/liquidations.db.* | tail -n +5 | xargs rm
```

### Review Performance

```bash
# Last 7 days profit
sqlite3 data/liquidations.db \
  "SELECT DATE(created_at), COUNT(*), SUM(actual_profit) FROM liquidations WHERE status='success' AND created_at > datetime('now', '-7 days') GROUP BY DATE(created_at);"

# Expected: Positive profit trend
```

### Check Success Rate

```bash
# Overall success rate
sqlite3 data/liquidations.db \
  "SELECT COUNT(*), SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as success FROM liquidations;"

# Target: 70%+ success rate
```

---

## Monthly Maintenance

### Withdraw Profits

Profits accumulate in the smart contract. Withdraw them:

```bash
# View contract profit (ask in Telegram group or check block explorer)
# Then call contract.withdraw() from owner account

# After withdrawal, check status
cat data/health_status.json | grep current_balance
```

### Update Dependencies (Optional)

```bash
# Check for updates
source venv/bin/activate
pip list --outdated

# Update if recommended
pip install --upgrade web3 aiohttp
```

### Review Configuration

```bash
# Check if MIN_PROFIT_USD is appropriate
# Review profit distribution:
sqlite3 data/liquidations.db \
  "SELECT estimated_profit, COUNT(*) FROM liquidations WHERE status='success' GROUP BY ROUND(estimated_profit/100)*100 ORDER BY estimated_profit;"

# If most profits < $5: Consider lowering MIN_PROFIT_USD
# If most profits > $100: Consider lowering to catch more small ones
```

---

## Emergency Procedures

### Immediate Stop (Safeguard)

```bash
# Stop the service immediately
sudo systemctl stop liquidation-bot

# Verify it stopped
systemctl status liquidation-bot
# Should show "inactive (dead)"
```

### Emergency Restart

```bash
# Full restart (useful if bot unresponsive)
sudo systemctl restart liquidation-bot

# If that fails:
sudo systemctl stop liquidation-bot
sleep 5
sudo systemctl start liquidation-bot
```

### Check if Stuck

```bash
# If bot hasn't scanned in > 5 minutes:
journalctl -u liquidation-bot --since "5 minutes ago" | tail -20

# If last line is old, bot is stuck. Restart.
```

### Last Resort: Manual Recovery

```bash
# SSH to VPS
ssh root@142.93.143.178

# Check venv is OK
cd /opt/liquidation-bot
source venv/bin/activate

# Try running manually (for debugging)
python3 run_liquidation_bot.py

# Press Ctrl+C after 10 seconds
# If it crashes with error, fix in code or .env

# Restart service
sudo systemctl restart liquidation-bot
```

---

## Real-Time Monitoring

### Watch Bot in Action

```bash
# Terminal 1: Watch logs
journalctl -u liquidation-bot -f

# Terminal 2: Watch database growth
watch -n 5 'sqlite3 /opt/liquidation-bot/data/liquidations.db "SELECT COUNT(*) as liquidations, SUM(actual_profit) as profit FROM liquidations WHERE status=\"success\";"'

# Terminal 3: Watch status
watch -n 10 'cat /opt/liquidation-bot/data/health_status.json | jq .'
```

### Spot Liquidation Activity

```bash
# Watch for liquidation logs in real-time
journalctl -u liquidation-bot -f | grep -E "Executing|Liquidation successful|profit"

# Example output:
# Executing liquidation for 0xAbCdEf...
# Liquidation successful, estimated profit: $245.67
```

---

## Alerting Setup

### Email Alerts (Optional)

```bash
# Send email on bot crash
echo "0 * * * * systemctl is-active liquidation-bot || mail -s 'Bot Down' admin@example.com" | crontab -

# Send daily profit report
echo "0 8 * * * sqlite3 /opt/liquidation-bot/data/liquidations.db 'SELECT SUM(actual_profit) FROM liquidations WHERE created_at > datetime(now, -1 day)' | mail -s 'Daily Profit' admin@example.com" | crontab -
```

### Telegram Alerts (Already Built-In)

Bot automatically sends messages to Telegram for:
- ✅ Bot startup
- 💰 Successful liquidations
- ⚠️ Errors
- 🛑 Bot shutdown

Check your Telegram for notifications.

---

## Performance Metrics

### Key Metrics to Track

| Metric | Target | Check Command |
|--------|--------|---------------|
| Uptime | 99%+ | `systemctl status liquidation-bot` |
| Scan Time | < 10s | `sqlite3 data/liquidations.db "SELECT AVG(scan_duration_ms) FROM scan_metrics;"` |
| Success Rate | 70%+ | Count success/total in liquidations table |
| Profit/Day | $50-500 | Sum actual_profit where created_at > yesterday |
| Error Count | < 5/day | Count errors in liquidations table |

### Generate Weekly Report

```bash
#!/bin/bash
sqlite3 /opt/liquidation-bot/data/liquidations.db << EOF
.mode column
.headers on
SELECT
  'Weekly Report' as metric,
  COUNT(*) as total_attempts,
  SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as successful,
  ROUND(100.0 * SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) / COUNT(*), 1) as success_rate_pct,
  ROUND(SUM(actual_profit), 2) as total_profit
FROM liquidations
WHERE created_at > datetime('now', '-7 days');
EOF
```

---

## Escalation Matrix

| Situation | Action | Urgency |
|-----------|--------|----------|
| Bot running, no errors | Continue monitoring daily | Low |
| Scan time > 20s | Check RPC, may need upgrade | Medium |
| Success rate < 50% | Review error logs, adjust config | Medium |
| Bot not running | Restart service | High |
| Repeated crashes | Check logs, may need code fix | High |
| Out of gas funds | Deposit more ARB to account | Urgent |
| Contract profit withdrawal | Call withdraw() on contract | Medium |

---

## Quick Command Reference

```bash
# Status
systemctl status liquidation-bot

# Logs (real-time)
journalctl -u liquidation-bot -f

# Logs (last N lines)
journalctl -u liquidation-bot -n 100

# Health status
cat data/health_status.json | jq .

# Database query
sqlite3 data/liquidations.db "SELECT * FROM liquidations ORDER BY id DESC LIMIT 5;"

# Restart
sudo systemctl restart liquidation-bot

# Stop
sudo systemctl stop liquidation-bot

# Start
sudo systemctl start liquidation-bot

# Enable auto-start
sudo systemctl enable liquidation-bot

# Disable auto-start
sudo systemctl disable liquidation-bot

# View service file
systemctl cat liquidation-bot.service

# View recent logs (colored)
journalctl -u liquidation-bot --no-pager -n 50 --output=cat
```

---

## When to Call for Help

Contact if:
- Bot crashes repeatedly despite restarts
- Repeated "RPC timeout" errors
- Success rate drops below 40%
- Bot consuming > 500MB RAM
- Unable to access VPS via SSH

Have ready:
- Last 100 lines of logs: `journalctl -u liquidation-bot -n 100`
- Current health status: `cat data/health_status.json`
- Recent liquidations: `sqlite3 data/liquidations.db "SELECT * FROM liquidations ORDER BY id DESC LIMIT 10;"`
- Configuration: `cat .env` (without private key)

---

**Remember**: The bot is designed to be resilient. Systemd automatically restarts it if it crashes. Daily monitoring (5 minutes) is usually sufficient.

**Monitor less, stress less, profit more!**
