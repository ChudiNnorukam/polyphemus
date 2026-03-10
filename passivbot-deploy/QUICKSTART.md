# Passivbot Quick Start (5 Minutes)

## Step 1: Deploy (Local Machine)

```bash
cd /Users/chudinnorukam/Projects/business/passivbot-deploy
chmod +x deploy-passivbot.sh
./deploy-passivbot.sh
```

**Wait 5-10 minutes for installation to complete.**

---

## Step 2: Configure API Keys (SSH to VPS)

```bash
ssh root@142.93.143.178
bash /opt/passivbot/setup-api-keys.sh
```

Enter your Bybit API credentials when prompted. See README.md for how to create API keys on Bybit.

---

## Step 3: Activate Configuration (SSH to VPS)

Choose one:

**Conservative ($100):**
```bash
cp /opt/passivbot/configs/bybit-100-conservative.json /opt/passivbot/configs/live/active.json
```

**Balanced ($500):**
```bash
cp /opt/passivbot/configs/bybit-500-balanced.json /opt/passivbot/configs/live/active.json
```

---

## Step 4: Backtest (SSH to VPS)

```bash
cd /opt/passivbot
source venv/bin/activate
python3 src/passivbot.py --backtest --config /opt/passivbot/configs/live/active.json
```

**Review the results:**
- Win rate should be > 50%
- Sharpe ratio > 1.0 is good
- Max drawdown < 20% is safer

---

## Step 5: Start Bot (SSH to VPS)

```bash
systemctl start passivbot
```

**Check if it's running:**
```bash
systemctl status passivbot
```

**View logs:**
```bash
journalctl -u passivbot -f
```

Expected output within 30 seconds:
```
Connected to Bybit
Loaded configuration: bybit-100-conservative.json
Starting market maker for BTCUSDT
Initial orders placed: 5 buy orders
```

---

## Step 6: Verify Orders on Bybit

1. Log into Bybit
2. Go to Derivatives → BTCUSDT
3. Look at the Order Book
4. You should see your grid orders (multiple orders at different prices)

---

## From Your Local Machine (Ongoing)

Use the management script:

```bash
cd /Users/chudinnorukam/Projects/business/passivbot-deploy

# Check status
./manage-bot.sh status

# View logs
./manage-bot.sh logs 100

# Follow logs in real-time
./manage-bot.sh follow

# Health check
./manage-bot.sh health

# Stop bot
./manage-bot.sh stop

# Restart bot
./manage-bot.sh restart
```

---

## Common Issues & Fixes

| Issue | Fix |
|-------|-----|
| "Connection refused" | Bot not running: `systemctl start passivbot` |
| "API key invalid" | Check `cat /opt/passivbot/api_keys/api-keys.json` |
| "Config not found" | Copy config: `cp /opt/passivbot/configs/bybit-*.json /opt/passivbot/configs/live/active.json` |
| "No orders placed" | Check Bybit balance has USDT, API key has trade permission |
| Bot crashes on start | View logs: `journalctl -u passivbot -p err` |

---

## Next Steps

1. **Monitor for 24 hours** — let the bot trade and verify it's working
2. **Check P&L daily** — use Bybit dashboard to monitor profits
3. **Review logs weekly** — look for errors or unusual behavior
4. **Scale up slowly** — only increase capital after 1-2 weeks of consistent profits

---

## Files Reference

| File | Purpose |
|------|---------|
| `deploy-passivbot.sh` | One-time deployment to VPS |
| `setup-api-keys.sh` | Configure Bybit credentials (run on VPS) |
| `manage-bot.sh` | Daily management commands (run locally) |
| `passivbot.service` | Systemd service (auto-installed) |
| `bybit-100-conservative.json` | Conservative config template |
| `bybit-500-balanced.json` | Balanced config template |
| `README.md` | Full documentation |

---

## Support

- Full docs: `README.md`
- Passivbot: https://github.com/enarjord/passivbot
- Bybit API: https://bybit-exchange.github.io/docs/linear/
- VPS: 142.93.143.178
