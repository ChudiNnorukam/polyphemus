# Passivbot Deployment Kit - Complete Index

**Status:** ✓ Complete and Ready for Deployment
**Created:** 2026-02-05
**Target VPS:** 142.93.143.178 (root)
**Exchange:** Bybit USDT Perpetuals
**Total Package:** 1,400+ lines across 9 files

---

## Documentation Files (Read These First)

| File | Purpose | Read Time |
|------|---------|-----------|
| **QUICKSTART.md** | 5-minute getting started guide | 3 min |
| **README.md** | Complete reference documentation | 15 min |
| **TROUBLESHOOTING.md** | Problem diagnosis and solutions | As needed |
| **INVENTORY.md** | What's included and where | 5 min |
| **INDEX.md** | This file (navigation guide) | 2 min |

**Recommended Reading Order:**
1. START: QUICKSTART.md (get it running in 5 minutes)
2. DEEP DIVE: README.md (understand everything)
3. REFERENCE: TROUBLESHOOTING.md (bookmark for later)
4. DETAILS: INVENTORY.md (know what you have)

---

## Executable Scripts

| File | Purpose | Usage |
|------|---------|-------|
| **deploy-passivbot.sh** | One-time VPS setup | `./deploy-passivbot.sh` |
| **setup-api-keys.sh** | Configure API credentials | `bash /opt/passivbot/setup-api-keys.sh` (on VPS) |
| **manage-bot.sh** | Daily bot management | `./manage-bot.sh [command]` (local) |

**Quick Command Reference:**
```bash
# First time (local machine)
./deploy-passivbot.sh

# After deployment (on VPS)
bash /opt/passivbot/setup-api-keys.sh
systemctl start passivbot

# Daily management (local machine)
./manage-bot.sh status        # Check if running
./manage-bot.sh logs 50       # View recent logs
./manage-bot.sh follow        # Real-time logs (Ctrl+C to exit)
./manage-bot.sh health        # Full health check
./manage-bot.sh restart       # Restart service
```

---

## Configuration Files

| File | Purpose | Size | Use Case |
|------|---------|------|----------|
| **bybit-100-conservative.json** | Conservative template | 42 lines | Learning, $50-200 capital |
| **bybit-500-balanced.json** | Balanced template | 45 lines | Active trading, $300-1000 capital |

**How to Activate:**
```bash
# Choose one
cp /opt/passivbot/configs/bybit-100-conservative.json \
   /opt/passivbot/configs/live/active.json

# Then restart
systemctl restart passivbot
```

---

## System File (Auto-Installed)

| File | Purpose |
|------|---------|
| **passivbot.service** | Systemd service definition |

**Installed to:** `/etc/systemd/system/passivbot.service`

---

## Directory Structure

```
passivbot-deploy/
├── INDEX.md                              ← You are here
├── QUICKSTART.md                         ← Start here
├── README.md                             ← Full documentation
├── TROUBLESHOOTING.md                    ← Problem solver
├── INVENTORY.md                          ← What's included
│
├── deploy-passivbot.sh                   ← Run this first (local)
├── manage-bot.sh                         ← Use this daily (local)
├── setup-api-keys.sh                     ← Run on VPS after deploy
├── passivbot.service                     ← Auto-installed to /etc/systemd/system/
│
└── configs/
    ├── bybit-100-conservative.json       ← Conservative ($100)
    └── bybit-500-balanced.json           ← Balanced ($500)
```

---

## Quick Start (3 Steps)

### Step 1: Deploy (5-10 minutes)
```bash
cd /Users/chudinnorukam/Projects/business/passivbot-deploy
./deploy-passivbot.sh
```

### Step 2: Configure (2 minutes)
```bash
ssh root@142.93.143.178
bash /opt/passivbot/setup-api-keys.sh
cp /opt/passivbot/configs/bybit-100-conservative.json /opt/passivbot/configs/live/active.json
```

### Step 3: Start (1 minute)
```bash
systemctl start passivbot
journalctl -u passivbot -f  # Watch logs
```

**Total time:** ~10 minutes to live trading

---

## Deployment Checklist

Before starting, verify you have:
- [ ] SSH access to 142.93.143.178 (test: `ssh root@142.93.143.178`)
- [ ] Bybit account with perpetuals enabled
- [ ] USDT deposited to Bybit futures wallet
- [ ] All scripts in this directory

Before going live:
- [ ] Run `./deploy-passivbot.sh` successfully
- [ ] Run `bash /opt/passivbot/setup-api-keys.sh` with correct credentials
- [ ] Copy config to `/opt/passivbot/configs/live/active.json`
- [ ] Backtest config (see README.md)
- [ ] Start bot: `systemctl start passivbot`
- [ ] Verify orders appear on Bybit within 5 minutes
- [ ] Monitor logs for 24 hours: `./manage-bot.sh follow`

---

## Configuration Comparison

### Conservative (bybit-100-conservative.json)
- **Capital:** $100 target
- **Max Exposure:** $50 (0.5 wallet limit)
- **Leverage:** 3x
- **Symbols:** BTCUSDT (single)
- **Sides:** Long only (no shorts)
- **Grid Spacing:** 0.5%
- **Takeprofit:** 1-3%
- **Best For:** Learning, low risk

### Balanced (bybit-500-balanced.json)
- **Capital:** $500 target
- **Max Exposure:** $150/symbol (0.3 wallet limit per)
- **Leverage:** 3x
- **Symbols:** BTCUSDT + ETHUSDT (dual)
- **Sides:** Both long and short
- **Grid Spacing:** 0.4%
- **Takeprofit:** 0.8-1.2%
- **Best For:** Active trading, moderate capital

---

## Key Files on VPS After Deployment

```
/opt/passivbot/                          # Main installation
├── src/                                 # Passivbot source code (from git)
├── venv/                                # Python virtual environment
├── api_keys/api-keys.json               # ← Your API credentials (SECURE)
├── configs/live/active.json             # ← Active configuration (copy template here)
├── logs/                                # Runtime logs
└── data/                                # Historical data for backtesting

/etc/systemd/system/passivbot.service    # Service configuration
```

---

## Daily Management Commands

```bash
# Status and monitoring
./manage-bot.sh status          # Full status report
./manage-bot.sh health          # Health check
./manage-bot.sh logs 100        # Show last 100 lines
./manage-bot.sh follow          # Real-time logs (Ctrl+C exit)
./manage-bot.sh errors          # Show errors only

# Service control
./manage-bot.sh start           # Start bot
./manage-bot.sh stop            # Stop bot
./manage-bot.sh restart         # Restart bot

# Configuration
./manage-bot.sh config          # Show active config
./manage-bot.sh view-config FILE # View any config file
./manage-bot.sh update-config FILE # Upload new config and restart

# Diagnostics
./manage-bot.sh balance         # Check Bybit balance
./manage-bot.sh positions       # Show open positions
./manage-bot.sh test            # Connection test
```

---

## Troubleshooting Quick Links

| Problem | Solution |
|---------|----------|
| Bot won't start | See TROUBLESHOOTING.md → "Bot won't start" |
| No orders placed | See TROUBLESHOOTING.md → "Bot not placing orders" |
| API key error | See TROUBLESHOOTING.md → "API Key Issues" |
| High CPU/Memory | See TROUBLESHOOTING.md → "Performance Issues" |
| Can't connect SSH | See TROUBLESHOOTING.md → "Network Issues" |
| Deployment failed | See TROUBLESHOOTING.md → "Deployment Issues" |

**Full troubleshooting guide:** TROUBLESHOOTING.md

---

## Performance Expectations

After first 24 hours of trading:

- **Win Rate:** 50-70% (depends on market conditions)
- **Daily P&L:** $5-50 (depends on capital and config)
- **Sharpe Ratio:** 1.0+ is good
- **Max Drawdown:** <10% is ideal

**These are estimates** — actual performance varies by:
- Market volatility
- Capital size
- Configuration parameters
- Time of day
- Market conditions

---

## Safety Reminders

1. **Start Small:** Use conservative config first ($100)
2. **Monitor Constantly:** Watch logs for first 24 hours
3. **Verify Orders:** Check Bybit order book to confirm trades
4. **API Security:** Keep API keys secret, rotate every 3 months
5. **Risk Management:** Leverage (3x) is built into configs
6. **Backup Config:** Save custom configs outside VPS

---

## Support & Resources

**For Passivbot Questions:**
- GitHub: https://github.com/enarjord/passivbot
- Documentation: https://enarjord.github.io/passivbot/
- Community: Discord (link in GitHub README)

**For Bybit Questions:**
- API Docs: https://bybit-exchange.github.io/docs/linear/
- Status: https://status.bybit.com
- Support: https://www.bybit.com/en/help-center/

**For Deployment Questions:**
- See README.md (full documentation)
- See TROUBLESHOOTING.md (problem solver)
- See QUICKSTART.md (5-minute guide)

---

## File Sizes & Stats

| File | Lines | Size | Type |
|------|-------|------|------|
| deploy-passivbot.sh | 141 | 3.6K | Executable |
| manage-bot.sh | 383 | 8.8K | Executable |
| setup-api-keys.sh | 64 | 2.0K | Executable |
| passivbot.service | 31 | 884B | Config |
| bybit-100-conservative.json | 42 | 1.5K | Config |
| bybit-500-balanced.json | 45 | 1.6K | Config |
| README.md | 408 | 11K | Documentation |
| QUICKSTART.md | 100 | 3.4K | Documentation |
| TROUBLESHOOTING.md | 542 | 18K | Documentation |
| INVENTORY.md | 312 | 10K | Documentation |
| **TOTAL** | **2,068** | **61K** | **Complete Kit** |

---

## Version & Timeline

| Date | Version | Changes |
|------|---------|---------|
| 2026-02-05 | 1.0 | Initial release |

---

## What's Next After Deployment

1. **Monitor First 24h:** Real-time with `manage-bot.sh follow`
2. **Review Daily:** Check P&L on Bybit dashboard
3. **Check Weekly:** Review logs for errors or anomalies
4. **Scale Gradually:** Only increase capital after 1-2 weeks of profits
5. **Optimize:** Adjust parameters after you understand the bot

---

## Frequently Asked Questions

**Q: How long does deployment take?**
A: 5-10 minutes. Mostly waiting for downloads and installations.

**Q: What's the minimum capital to start?**
A: $100 with conservative config. Start small to learn.

**Q: Can I run multiple bots?**
A: Yes, see README.md → "Advanced Usage" → "Run Multiple Bots"

**Q: How often should I check on the bot?**
A: Daily for first week, then weekly. Logs are persistent.

**Q: Can I modify the configuration?**
A: Yes, but backtest changes first. See README.md → "Configuration Tuning"

**Q: What if I need to stop the bot?**
A: `systemctl stop passivbot` or `./manage-bot.sh stop`

**Q: Is my API key safe?**
A: Yes, stored with 600 permissions (readable by root only). Rotate every 3 months.

**Q: Can I run on mainnet with real money?**
A: Yes, but start with conservative config and small capital. Real trading is risky.

---

## Ready to Deploy?

1. Read **QUICKSTART.md** (3 minutes)
2. Run `./deploy-passivbot.sh` (wait 5-10 min)
3. SSH to VPS: `ssh root@142.93.143.178`
4. Run `bash /opt/passivbot/setup-api-keys.sh` (1 min)
5. Activate config: `cp /opt/passivbot/configs/bybit-100-conservative.json /opt/passivbot/configs/live/active.json`
6. Start bot: `systemctl start passivbot`
7. Monitor: `journalctl -u passivbot -f`

**Happy trading!**

---

**Questions?** See TROUBLESHOOTING.md or README.md
**Need help?** Check the appropriate section above or reach out to Passivbot community
