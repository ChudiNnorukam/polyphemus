# Passivbot Deployment Kit - Inventory

**Created:** 2026-02-05
**Target:** VPS 142.93.143.178 (root@142.93.143.178)
**Exchange:** Bybit USDT Perpetuals
**Total Lines:** 1,393

---

## Files Created

### 1. Deploy Script
**File:** `deploy-passivbot.sh` (141 lines)
- Fully automated SSH deployment
- Installs Python 3.12, Rust toolchain, system dependencies
- Clones Passivbot repo from GitHub
- Creates virtual environment and installs Python packages
- Attempts Rust compilation (optional, for speed)
- Uploads config templates and systemd service
- **Executable:** ✓ Yes

### 2. Setup Script (VPS)
**File:** `setup-api-keys.sh` (64 lines)
- Interactive API key configuration
- Prompts for Bybit API Key and Secret
- Validates input and confirms credentials
- Securely stores in `/opt/passivbot/api_keys/api-keys.json` (600 perms)
- Run once on VPS after deployment
- **Executable:** ✓ Yes

### 3. Management Script (Local)
**File:** `manage-bot.sh` (383 lines)
- Remote bot control from local machine
- Commands: status, start, stop, restart, logs, follow, errors, config, balance, positions, health, update-config, test
- Color-coded output (green/red/yellow/blue)
- SSH-based, no port forwarding needed
- Can upload/activate new configs remotely
- **Executable:** ✓ Yes

### 4. Systemd Service
**File:** `passivbot.service` (31 lines)
- Auto-install via deploy script
- Type=simple for straightforward process management
- Restart=always with 15s backoff
- Resource limits: 512M memory, 50% CPU quota
- Logging to journalctl
- Security: NoNewPrivileges, PrivateTmp, ProtectSystem
- Auto-starts on boot if enabled

### 5. Configuration Templates

#### Conservative ($100 capital)
**File:** `configs/bybit-100-conservative.json` (42 lines)
- Single symbol: BTCUSDT
- Long-only (no shorts)
- 3x leverage
- 0.5 wallet exposure limit ($50 max at risk)
- 0.5% grid spacing
- 1-3% take profit targets
- Safe for learning/small accounts

#### Balanced ($500 capital)
**File:** `configs/bybit-500-balanced.json` (45 lines)
- Multiple symbols: BTCUSDT, ETHUSDT
- Both long and short enabled
- 3x leverage
- 0.3 wallet exposure limit per symbol ($150 max each)
- 0.4% grid spacing
- 0.8-1.2% take profit targets
- For experienced traders with more capital

### 6. Documentation

#### Quick Start
**File:** `QUICKSTART.md` (100 lines)
- 5-minute getting started guide
- Step-by-step workflow
- Copy-paste commands
- Common issues and fixes
- File reference table

#### Full Documentation
**File:** `README.md` (408 lines)
- Complete deployment guide
- Configuration explanations
- Monitoring and management
- Tuning parameters
- Troubleshooting section
- Safety best practices
- Advanced usage examples
- Support resources
- Deployment checklist

#### This Inventory
**File:** `INVENTORY.md` (this file)
- Complete file listing
- Purpose and usage for each file
- Quick reference

---

## Quick Reference: Commands

### Local Machine (First Time)
```bash
cd /Users/chudinnorukam/Projects/business/passivbot-deploy
chmod +x *.sh                    # Already done
./deploy-passivbot.sh            # 5-10 min deployment
```

### VPS (After Deployment)
```bash
ssh root@142.93.143.178
bash /opt/passivbot/setup-api-keys.sh
cp /opt/passivbot/configs/bybit-100-conservative.json /opt/passivbot/configs/live/active.json
systemctl start passivbot
journalctl -u passivbot -f
```

### Local Machine (Ongoing Management)
```bash
./manage-bot.sh status           # Check bot status
./manage-bot.sh logs 100         # View last 100 lines
./manage-bot.sh follow           # Real-time logs
./manage-bot.sh health           # Full health check
./manage-bot.sh restart          # Restart bot
./manage-bot.sh update-config ./configs/bybit-500-balanced.json
```

---

## Deployment Flow

```
1. Local: ./deploy-passivbot.sh
   ↓ (SSH to VPS, installs everything)
2. VPS: bash /opt/passivbot/setup-api-keys.sh
   ↓ (Configure API credentials)
3. VPS: cp /opt/passivbot/configs/bybit-*.json /opt/passivbot/configs/live/active.json
   ↓ (Activate configuration)
4. VPS: systemctl start passivbot
   ↓ (Start bot service)
5. VPS: journalctl -u passivbot -f
   ↓ (Monitor logs for success)
6. Local: ./manage-bot.sh status
   ↓ (Verify from local machine)
7. Bybit: Check order book for grid orders
   ↓ (Confirm orders are live)
8. Monitor: Daily via ./manage-bot.sh logs
```

---

## File Locations on VPS

```
/opt/passivbot/
├── src/                          # Passivbot source (from git)
├── venv/                         # Python virtual environment
├── venv/bin/python3              # Python interpreter
├── requirements.txt              # Dependencies (from git)
├── api_keys/
│   └── api-keys.json            # ← Your API credentials (SECURE!)
├── configs/
│   ├── bybit-100-conservative.json  # Template (from deploy)
│   ├── bybit-500-balanced.json      # Template (from deploy)
│   └── live/
│       └── active.json           # ← Active config (copy one template here)
├── data/                         # Historical data for backtesting
├── logs/                         # Runtime logs
└── .env                          # Optional environment variables

/etc/systemd/system/
└── passivbot.service             # ← Service file (auto-installed)
```

---

## Configuration Activation

**Before starting bot, must copy one config to `/opt/passivbot/configs/live/active.json`:**

```bash
# Conservative (recommended first)
cp /opt/passivbot/configs/bybit-100-conservative.json \
   /opt/passivbot/configs/live/active.json

# Or balanced (after proven stability)
cp /opt/passivbot/configs/bybit-500-balanced.json \
   /opt/passivbot/configs/live/active.json
```

Bot reads from `/opt/passivbot/configs/live/active.json` on startup.

---

## Verification Checklist

After deployment, verify:

- [ ] `./deploy-passivbot.sh` completes without errors
- [ ] SSH to 142.93.143.178 works
- [ ] `/opt/passivbot` directory exists with all subdirs
- [ ] `bash /opt/passivbot/setup-api-keys.sh` completes
- [ ] `/opt/passivbot/api_keys/api-keys.json` exists (600 perms)
- [ ] Config copied to `/opt/passivbot/configs/live/active.json`
- [ ] `systemctl status passivbot` shows "active (running)"
- [ ] `journalctl -u passivbot -f` shows "Connected to Bybit"
- [ ] Bybit order book shows grid orders within 5 minutes
- [ ] `./manage-bot.sh health` shows all green checks
- [ ] `./manage-bot.sh logs 10` shows recent activity

---

## Support & Customization

### If You Need to:

| Task | File to Edit | Notes |
|------|--------------|-------|
| Change leverage | `configs/live/active.json` | Restart bot after |
| Adjust grid spacing | `configs/live/active.json` | Backtest first |
| Scale position size | `configs/live/active.json` | Test on paper first |
| Change start time | `/etc/systemd/system/passivbot.service` | `systemctl daemon-reload` after |
| View bot logs | `journalctl -u passivbot` | Use `manage-bot.sh follow` |
| Update Passivbot | `git pull` in `/opt/passivbot` | Restart and test |
| Rotate API keys | `bash /opt/passivbot/setup-api-keys.sh` | Restart bot |
| Disable auto-start | `systemctl disable passivbot` | Prevents restart on reboot |

### Customizing Configs

Both configs are standard Passivbot JSON. You can:

1. Edit the active config:
   ```bash
   nano /opt/passivbot/configs/live/active.json
   systemctl restart passivbot
   ```

2. Or create custom configs:
   ```bash
   cp /opt/passivbot/configs/bybit-100-conservative.json \
      /opt/passivbot/configs/my-custom.json
   nano /opt/passivbot/configs/my-custom.json
   cp /opt/passivbot/configs/my-custom.json \
      /opt/passivbot/configs/live/active.json
   systemctl restart passivbot
   ```

---

## Troubleshooting Reference

### Bot Won't Start
1. Check config validity: `python3 -m json.tool /opt/passivbot/configs/live/active.json`
2. View errors: `journalctl -u passivbot -p err`
3. Restart: `systemctl restart passivbot`

### No Orders Placed
1. Check Bybit balance has USDT
2. Verify API key has "Trade" permission
3. Check logs: `journalctl -u passivbot -f | grep -i "order\|error"`

### High Memory/CPU
1. Check resource limits: `systemctl cat passivbot | grep -E "Memory|CPU"`
2. Adjust in service file if needed
3. Reload: `systemctl daemon-reload && systemctl restart passivbot`

### API Key Invalid
1. Re-run: `bash /opt/passivbot/setup-api-keys.sh`
2. Restart: `systemctl restart passivbot`
3. Check logs for auth errors

---

## Next Steps After Deployment

1. **Monitor First 24h:** Use `manage-bot.sh follow` to watch in real-time
2. **Check P&L Daily:** View Bybit portfolio value
3. **Review Weekly:** Check logs for errors or unusual behavior
4. **Scale Gradually:** Only increase capital after 1-2 weeks of profits
5. **Backtest Changes:** Always backtest config changes before live deployment

---

## Key Contacts & Resources

- **Passivbot GitHub:** https://github.com/enarjord/passivbot
- **Passivbot Docs:** https://enarjord.github.io/passivbot/
- **Bybit API Docs:** https://bybit-exchange.github.io/docs/linear/
- **VPS IP:** 142.93.143.178
- **VPS User:** root

---

## Version Info

| Component | Version |
|-----------|---------|
| Kit Version | 1.0 |
| Created | 2026-02-05 |
| Python Target | 3.12 |
| Exchange | Bybit Linear (USDT Perpetuals) |
| Default Leverage | 3x |

---

**Deployment Kit Ready for Use!**
