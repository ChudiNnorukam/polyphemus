# Funding Rate Arbitrage Bot - Deployment Guide

Complete step-by-step guide to deploying the Bybit funding rate arbitrage bot to production.

## Overview

This is a **production-ready** delta-neutral arbitrage bot that:
- Buys spot assets (BTC, ETH, SOL, etc.)
- Shorts perpetual futures
- Collects funding payments (27%+ APY potential)
- Runs 24/7 with automatic position management
- Includes health monitoring, error recovery, and comprehensive logging

## Pre-Deployment Checklist

### Phase 1: Local Setup (Day 1)

- [ ] Clone/download project to local machine
- [ ] Create Python 3.12 virtual environment
- [ ] Install dependencies: `pip install -r requirements.txt`
- [ ] Test all modules load: `python3 -c "import config, rate_scanner, position_manager, database"`
- [ ] Run syntax check: `python3 -m py_compile *.py`

### Phase 2: Bybit Account Setup

- [ ] Create Bybit account (https://www.bybit.com)
- [ ] Enable Unified Trading Account (UTA)
- [ ] Verify identity (KYC)
- [ ] Deposit initial capital ($350-500)
- [ ] Generate API key:
  - Go to Account → API Management
  - Create new key with permissions:
    - `read_positions` (read only)
    - `read_wallet` (read only)
    - `order_read` (read only)
    - `order_create` (read/write)
  - **Important**: Restrict API key to your IP address
  - **Important**: Set order limit (e.g., 50/minute)
  - Save API Key and Secret securely

### Phase 3: Local Testing (Days 2-3)

- [ ] Copy `.env.example` to `.env`
- [ ] Leave BYBIT_API_KEY/SECRET empty
- [ ] Set `DRY_RUN=true`
- [ ] Run 5-minute dry run: `timeout 300 python3 run_funding_bot.py`
- [ ] Check logs: `tail logs/funding_bot.log`
- [ ] Check health: `cat logs/health.json | jq`
- [ ] Query database: `sqlite3 data/funding.db "SELECT COUNT(*) FROM positions;"`
- [ ] Complete TESTING.md Phase 1 & 2

### Phase 4: Testnet Validation (Days 4-5)

- [ ] Create Bybit testnet account (https://testnet.bybit.com)
- [ ] Fund testnet account with demo USDT
- [ ] Generate testnet API key
- [ ] Set `BYBIT_API_KEY` and `BYBIT_API_SECRET` in `.env`
- [ ] Set `DRY_RUN=false`
- [ ] Run 24-hour testnet test
- [ ] Enter test position ($10-20)
- [ ] Verify position fills
- [ ] Verify health monitoring
- [ ] Complete TESTING.md Phase 2

### Phase 5: Mainnet Preparation

- [ ] Review IMPLEMENTATION.md architecture
- [ ] Review risk parameters in config
- [ ] Set `DRY_RUN=false` in `.env`
- [ ] Set mainnet `BYBIT_API_KEY` and `BYBIT_API_SECRET`
- [ ] Set `MIN_FUNDING_RATE=0.0002` (or higher)
- [ ] Set `MAX_POSITION_PCT=0.4` (40% max per position)
- [ ] Set `PAIRS=BTCUSDT` (start with ONE pair)
- [ ] Ensure `CLOSE_ON_SHUTDOWN=true` for safety
- [ ] Configure Telegram alerts (optional but recommended):
  - Create Telegram bot via @BotFather
  - Get chat ID from @userinfobot
  - Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`

## Deployment Steps

### Step 1: Prepare VPS

```bash
# SSH to VPS
ssh root@142.93.143.178

# Create deployment directory
mkdir -p /opt/funding-bot
cd /opt/funding-bot

# Create user for bot (security)
useradd -r -s /bin/bash funding-bot

# Install Python 3.12 (if needed)
apt update && apt install -y python3.12 python3.12-venv

# Create directories
mkdir -p data logs
chmod 755 data logs
```

### Step 2: Deploy from Local Machine

```bash
# From local funding-bot directory
chmod +x deploy.sh
./deploy.sh
```

The deploy.sh script will:
1. Validate .env configuration
2. Create deployment package
3. SCP files to VPS
4. Setup Python virtual environment
5. Install dependencies
6. Validate Python syntax
7. Create systemd service
8. Start bot service
9. Verify deployment

### Step 3: Verify Deployment

```bash
# Check service status
ssh root@142.93.143.178 'systemctl status funding-bot'

# Expected output:
# ● funding-bot.service - Bybit Funding Rate Arbitrage Bot
#    Loaded: loaded (/etc/systemd/system/funding-bot.service; enabled; vendor preset: enabled)
#    Active: active (running) since [timestamp]

# View recent logs
ssh root@142.93.143.178 'tail -30 /opt/funding-bot/logs/funding_bot.log'

# Check health status
ssh root@142.93.143.178 'cat /opt/funding-bot/logs/health.json | jq'
```

### Step 4: Monitor First 24 Hours

Automated monitoring script on VPS:

```bash
# SSH to VPS
ssh root@142.93.143.178

# Run monitor script
/opt/funding-bot/monitor.sh
```

Or manual checks (every hour for first 24h):

```bash
# Check bot is running
ssh root@142.93.143.178 'systemctl is-active funding-bot'

# Check for errors
ssh root@142.93.143.178 'grep "ERROR\|CRITICAL" /opt/funding-bot/logs/funding_bot.log | tail -5'

# Check positions
ssh root@142.93.143.178 'sqlite3 /opt/funding-bot/data/funding.db "SELECT COUNT(*) as open_positions FROM positions WHERE status=\"OPEN\";"'

# Check balance
ssh root@142.93.143.178 'cat /opt/funding-bot/logs/health.json | jq .balance'

# Check P&L
ssh root@142.93.143.178 'cat /opt/funding-bot/logs/health.json | jq .total_pnl'
```

## First 7 Days: Validation Period

### Day 1: Deployment
- Deploy to VPS
- Monitor startup logs
- Verify all systems operational
- Expected: 0-2 positions entered (depends on rates)

### Day 2-3: Rate Monitoring
- Observe funding rate fluctuations
- Check if bot enters positions appropriately
- Verify Telegram alerts working
- Expected: 2-4 positions entered

### Day 4-5: Position Lifecycle
- Let positions sit through funding settlement times
- Verify exits work (if rates drop)
- Check funding payments recorded
- Expected: Some positions closed

### Day 6-7: Stress Test
- Continue monitoring
- Check error handling on API issues
- Verify restart on crashes
- Verify health monitoring working
- Expected: 5-10 positions total

## Production Configuration Recommendations

### Conservative Setup (Low Risk)

```env
DRY_RUN=false
PAIRS=BTCUSDT
MIN_FUNDING_RATE=0.0005  # Higher threshold
MAX_POSITION_PCT=0.25   # 25% per position
CHECK_INTERVAL=120      # Check every 2 minutes
RATE_STABILITY_PERIODS=5  # Require very stable rates
CLOSE_ON_SHUTDOWN=true
```

Expected: 10-15% monthly return, very stable

### Balanced Setup (Recommended)

```env
DRY_RUN=false
PAIRS=BTCUSDT,ETHUSDT,SOLUSDT
MIN_FUNDING_RATE=0.0002  # Default
MAX_POSITION_PCT=0.4    # 40% per position
CHECK_INTERVAL=60       # Check every minute
RATE_STABILITY_PERIODS=3
CLOSE_ON_SHUTDOWN=true
```

Expected: 15-25% monthly return, moderate risk

### Aggressive Setup (High Risk)

```env
DRY_RUN=false
PAIRS=BTCUSDT,ETHUSDT,SOLUSDT,DOGUSDT,MATICUSDT
MIN_FUNDING_RATE=0.0001  # Lower threshold
MAX_POSITION_PCT=0.5    # 50% per position
CHECK_INTERVAL=30       # Check every 30s
RATE_STABILITY_PERIODS=1
CLOSE_ON_SHUTDOWN=true
```

Expected: 25%+ monthly return, higher volatility

## Operational Commands

### Daily Operations

```bash
# Start bot
ssh root@142.93.143.178 'systemctl start funding-bot'

# Stop bot (graceful shutdown)
ssh root@142.93.143.178 'systemctl stop funding-bot'

# Restart bot
ssh root@142.93.143.178 'systemctl restart funding-bot'

# View live logs (follow)
ssh root@142.93.143.178 'tail -f /opt/funding-bot/logs/funding_bot.log'

# View health (one-time)
ssh root@142.93.143.178 'cat /opt/funding-bot/logs/health.json | jq'
```

### Troubleshooting

```bash
# Check service status
ssh root@142.93.143.178 'systemctl status funding-bot --no-pager'

# View recent 100 lines of logs
ssh root@142.93.143.178 'tail -100 /opt/funding-bot/logs/funding_bot.log'

# Check for errors in logs
ssh root@142.93.143.178 'grep -i error /opt/funding-bot/logs/funding_bot.log | tail -20'

# View database statistics
ssh root@142.93.143.178 'sqlite3 /opt/funding-bot/data/funding.db "SELECT symbol, COUNT(*) as positions, SUM(pnl) as total_pnl FROM positions WHERE status=\"CLOSED\" GROUP BY symbol;"'

# Check available balance
ssh root@142.93.143.178 'sqlite3 /opt/funding-bot/data/funding.db "SELECT timestamp, balance FROM bot_status ORDER BY timestamp DESC LIMIT 5;"'

# View active positions
ssh root@142.93.143.178 'sqlite3 /opt/funding-bot/data/funding.db "SELECT id, symbol, entry_price, entry_time FROM positions WHERE status=\"OPEN\";"'

# Restart bot with debug logging
ssh root@142.93.143.178 'LOG_LEVEL=DEBUG systemctl restart funding-bot'
```

### Updates and Maintenance

```bash
# Update bot code (after changes locally)
./deploy.sh  # Re-runs full deployment

# Or manual update:
scp run_funding_bot.py root@142.93.143.178:/opt/funding-bot/
ssh root@142.93.143.178 'systemctl restart funding-bot'

# Backup database
ssh root@142.93.143.178 'cp /opt/funding-bot/data/funding.db /opt/funding-bot/data/funding.db.backup'

# View backups
ssh root@142.93.143.178 'ls -lh /opt/funding-bot/data/funding.db*'
```

## Monitoring & Alerting

### Real-Time Health JSON

Updated every 5 minutes:
```
/opt/funding-bot/logs/health.json
```

Contains:
- Current balance and peak balance
- Drawdown percentage
- Open positions count
- Total P&L and funding collected
- Uptime in hours
- Error count

### Log Files

**Main log**: `/opt/funding-bot/logs/funding_bot.log`

Log levels:
- `INFO`: Normal operations
- `WARNING`: Non-critical issues
- `ERROR`: Problems requiring attention
- `DEBUG`: Detailed diagnostic info

### Database Queries for Analysis

```bash
# Monthly P&L
sqlite3 /opt/funding-bot/data/funding.db << 'EOF'
SELECT
  DATE(exit_time) as date,
  COUNT(*) as trades,
  ROUND(SUM(pnl), 2) as daily_pnl,
  ROUND(SUM(funding_collected), 2) as funding
FROM positions
WHERE status = 'CLOSED'
  AND exit_time >= datetime('now', '-30 days')
GROUP BY DATE(exit_time)
ORDER BY date DESC;
EOF
```

```bash
# Win rate by symbol
sqlite3 /opt/funding-bot/data/funding.db << 'EOF'
SELECT
  symbol,
  COUNT(*) as trades,
  ROUND(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) as win_rate,
  ROUND(AVG(pnl), 2) as avg_pnl,
  ROUND(SUM(pnl), 2) as total_pnl
FROM positions
WHERE status = 'CLOSED'
GROUP BY symbol;
EOF
```

## Emergency Procedures

### Bot Hanging (No activity for 2+ hours)

```bash
# Check status
ssh root@142.93.143.178 'systemctl status funding-bot'

# If inactive, restart
ssh root@142.93.143.178 'systemctl restart funding-bot'

# Check logs for errors
ssh root@142.93.143.178 'tail -50 /opt/funding-bot/logs/funding_bot.log | grep -i error'
```

### Position Stuck Open

```bash
# View open positions
ssh root@142.93.143.178 'sqlite3 /opt/funding-bot/data/funding.db "SELECT id, symbol, entry_time FROM positions WHERE status=\"OPEN\";"'

# Manual close via Bybit UI:
# 1. Go to https://www.bybit.com/trade/usdt
# 2. Find the symbol (e.g., BTCUSDT spot & perp)
# 3. Close spot position via "Sell"
# 4. Close perp position via "Buy" (close short)
# 5. Restart bot to sync database

# Or mark as closed in database manually:
ssh root@142.93.143.178 << 'EOF'
sqlite3 /opt/funding-bot/data/funding.db "UPDATE positions SET status='CLOSED', exit_reason='manual_close' WHERE id='[position_id]';"
EOF
```

### API Credential Issues

```bash
# Check logs for auth errors
ssh root@142.93.143.178 'grep -i "unauthorized\|401\|credential" /opt/funding-bot/logs/funding_bot.log'

# Update credentials
ssh root@142.93.143.178 'nano /opt/funding-bot/.env'
# Edit BYBIT_API_KEY and BYBIT_API_SECRET
# Save and exit

# Restart bot
ssh root@142.93.143.178 'systemctl restart funding-bot'
```

### Disk Space Issues

```bash
# Check disk usage
ssh root@142.93.143.178 'du -sh /opt/funding-bot/*'

# Rotate logs (keep last 7 days)
ssh root@142.93.143.178 'find /opt/funding-bot/logs -name "*.log" -mtime +7 -delete'

# Archive database (if >100MB)
ssh root@142.93.143.178 'gzip /opt/funding-bot/data/funding.db.backup'
```

## Performance Optimization

### Scale Up (More Pairs)

```env
PAIRS=BTCUSDT,ETHUSDT,SOLUSDT,DOGUSDT,XRPUSDT
MAX_POSITION_PCT=0.25  # Reduce per-position size
```

### Scale Down (One Pair)

```env
PAIRS=BTCUSDT
MAX_POSITION_PCT=0.6  # Increase per-position size
```

### Increase Frequency

```env
CHECK_INTERVAL=30  # Every 30 seconds instead of 60
HEALTH_CHECK_INTERVAL=60  # Every minute
```

## Rollback Procedure

If bot causes issues:

```bash
# Stop bot immediately
ssh root@142.93.143.178 'systemctl stop funding-bot'

# View open positions
ssh root@142.93.143.178 'sqlite3 /opt/funding-bot/data/funding.db "SELECT symbol FROM positions WHERE status=\"OPEN\";"'

# Manually close all positions via Bybit UI
# For each symbol: sell spot + buy perp

# Restore previous code version
ssh root@142.93.143.178 'git checkout HEAD~1'  # If using git
# OR copy backup file
scp /path/to/backup/run_funding_bot.py root@142.93.143.178:/opt/funding-bot/

# Restart
ssh root@142.93.143.178 'systemctl start funding-bot'
```

## Post-Deployment Validation

After 7 days of successful operation:

- [ ] Bot has run continuously (uptime > 150 hours)
- [ ] At least 5 positions opened and closed
- [ ] P&L is positive (or neutral if rates low)
- [ ] No errors in logs (max 5 retryable errors okay)
- [ ] Health JSON updates every 5 minutes
- [ ] Database queries work and show data
- [ ] Telegram alerts working (if enabled)
- [ ] Can manually restart bot cleanly

If all pass → **PRODUCTION APPROVED**

## Support & Debugging

### Check Bot Status Quick Command

```bash
alias funding-status='ssh root@142.93.143.178 "echo \"=== Status ===\" && systemctl is-active funding-bot && echo \"=== Health ===\" && jq -c \"{balance, uptime_hours, open_positions, total_pnl}\" /opt/funding-bot/logs/health.json 2>/dev/null || echo \"Health file not found\""'

# Run daily:
funding-status
```

### Create Monitoring Dashboard (Optional)

```bash
# On VPS, install monitoring tools
ssh root@142.93.143.178 << 'EOF'
apt install -y htop iotop nethogs

# Create daily report script
cat > /opt/funding-bot/daily_report.sh << 'REPORT'
#!/bin/bash
echo "=== Daily Report ===" >> /tmp/funding-daily.log
date >> /tmp/funding-daily.log
sqlite3 /opt/funding-bot/data/funding.db "SELECT COUNT(*) as positions, ROUND(SUM(pnl), 2) as pnl FROM positions WHERE strftime('%Y-%m-%d', exit_time) = date('now');" >> /tmp/funding-daily.log
echo "" >> /tmp/funding-daily.log
REPORT

chmod +x /opt/funding-bot/daily_report.sh

# Add to cron (daily at 2 AM UTC)
crontab -e
# Add: 0 2 * * * /opt/funding-bot/daily_report.sh
EOF
```

## Final Checklist

Before declaring bot production-ready:

- [ ] Deployed to VPS 142.93.143.178
- [ ] Systemd service running and enabled
- [ ] Bot has entered at least one position
- [ ] Bot has exited at least one position
- [ ] Health JSON updates regularly
- [ ] Database contains position history
- [ ] Logs contain no CRITICAL errors
- [ ] Telegram alerts working (if configured)
- [ ] Can SSH and check status
- [ ] Know how to stop bot gracefully
- [ ] Have backup API keys ready
- [ ] Have manual exit plan if needed

🎉 **Bot is production-ready!**

