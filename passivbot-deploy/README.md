# Passivbot Deployment Kit

Complete deployment kit for **Passivbot** market-making bot on Bybit perpetual futures.

**Target:** VPS `142.93.143.178` (root)
**Exchange:** Bybit USDT Perpetuals
**Strategy:** Grid trading with dynamic entry/exit
**Capital Range:** $100-$500+ (configurable)

---

## Quick Start

### 1. Deploy to VPS

From your local machine (in this directory):

```bash
chmod +x deploy-passivbot.sh
./deploy-passivbot.sh
```

This will:
- Install Python 3.12, system dependencies, and Rust toolchain
- Clone Passivbot repository
- Create Python virtual environment
- Install all Python dependencies
- Create directory structure
- Install systemd service

**Deployment time:** ~5-10 minutes

### 2. Set Up API Keys (on VPS)

SSH to the VPS:

```bash
ssh root@142.93.143.178
bash /opt/passivbot/setup-api-keys.sh
```

This interactive script will:
- Prompt for Bybit API Key and Secret
- Validate and confirm credentials
- Store securely in `/opt/passivbot/api_keys/api-keys.json` (600 permissions)

**Getting Bybit API Keys:**
1. Log into Bybit
2. Account → API Management
3. Create New API Key with permissions:
   - Trade: ✓ Enabled
   - Position: ✓ Enabled
   - Account: Read-only
   - IP Whitelist: Add `142.93.143.178`

### 3. Choose Configuration

Two pre-configured profiles:

**Conservative ($100 capital):**
```bash
cp /opt/passivbot/configs/bybit-100-conservative.json /opt/passivbot/configs/live/active.json
```

Features:
- Long-only (safer)
- 3x leverage
- $50 max exposure per position
- 0.5% grid spacing
- 1-3% take profit targets

**Balanced ($500 capital, BTC + ETH):**
```bash
cp /opt/passivbot/configs/bybit-500-balanced.json /opt/passivbot/configs/live/active.json
```

Features:
- Both long and short enabled
- 3x leverage
- $150 max exposure per symbol
- 0.4% grid spacing
- 0.8-1.2% take profit targets
- Trades BTC + ETH simultaneously

### 4. Backtest Configuration

Before going live, backtest your config:

```bash
cd /opt/passivbot
source venv/bin/activate
python3 src/passivbot.py --backtest --config /opt/passivbot/configs/live/active.json
```

This will:
- Download historical data
- Simulate trades over past period
- Show win rate, Sharpe ratio, max drawdown
- Identify if configuration is profitable

### 5. Start Bot

**Start the service:**
```bash
systemctl start passivbot
```

**Check status:**
```bash
systemctl status passivbot
```

**View logs in real-time:**
```bash
journalctl -u passivbot -f
```

**Stop bot:**
```bash
systemctl stop passivbot
```

---

## Configuration Files

### Conservative Config (`bybit-100-conservative.json`)

```json
{
  "leverage": 3,
  "wallet_exposure_limit": 0.5,      // Max 50% of $100 = $50 at risk
  "long": {
    "enabled": true,
    "grid_spacing": 0.005,            // 0.5% between grid levels
    "initial_qty_pct": 0.1,           // 10% of position per entry
    "close_grid_markup": 0.01,        // 1% take profit
    ...
  },
  "short": {
    "enabled": false                  // Don't trade shorts (safer)
  }
}
```

**Best for:**
- Learning the bot behavior
- Low capital ($50-$200)
- Risk-averse traders
- Single symbol (BTC only)

### Balanced Config (`bybit-500-balanced.json`)

```json
{
  "leverage": 3,
  "wallet_exposure_limit": 0.3,      // Max 30% per symbol = $150 each
  "symbols": ["BTCUSDT", "ETHUSDT"],
  "long": {
    "enabled": true,
    "grid_spacing": 0.004,            // 0.4% grid spacing
    "initial_qty_pct": 0.08,
    "close_grid_markup": 0.008,       // 0.8% take profit
    ...
  },
  "short": {
    "enabled": true                   // Trade both sides
  }
}
```

**Best for:**
- Mid-scale capital ($300-$1000)
- Experienced traders
- Diversified positions (BTC + ETH)
- Bidirectional market making

---

## File Structure

```
/opt/passivbot/
├── src/                              # Passivbot source code
├── requirements.txt                  # Python dependencies
├── venv/                             # Python virtual environment
├── api_keys/
│   └── api-keys.json                # Bybit API credentials (600 perms)
├── configs/
│   ├── bybit-100-conservative.json   # Conservative template
│   ├── bybit-500-balanced.json       # Balanced template
│   └── live/
│       └── active.json               # ← Live config (symlink or copy)
├── data/                             # Backtesting & historical data
├── logs/                             # Runtime logs
└── .env                              # Environment variables (optional)
```

---

## Monitoring & Management

### View Logs

**Last 50 lines:**
```bash
journalctl -u passivbot -n 50
```

**Follow in real-time:**
```bash
journalctl -u passivbot -f
```

**Show errors only:**
```bash
journalctl -u passivbot -p err
```

**Since last boot:**
```bash
journalctl -u passivbot -b
```

### Check Bot Status

```bash
systemctl status passivbot
```

Output example:
```
● passivbot.service - Passivbot Market Maker (Bybit Perpetuals)
   Loaded: loaded (/etc/systemd/system/passivbot.service; enabled)
   Active: active (running) since Wed 2026-02-05 14:32:15 UTC
  Process: 12345 ExecStart=/opt/passivbot/venv/bin/python3 src/passivbot.py ...
 Main PID: 12346 (python3)
```

### Restart Bot

```bash
systemctl restart passivbot
```

### Disable Auto-Start

```bash
systemctl disable passivbot
systemctl stop passivbot
```

---

## Configuration Tuning

### Key Parameters

**Exposure & Leverage:**
- `leverage`: 1-5 (higher = riskier)
- `wallet_exposure_limit`: 0.1-0.5 (% of capital at risk)
  - 0.5 = aggressive ($50 on $100 account)
  - 0.3 = moderate ($30 on $100 account)
  - 0.1 = conservative ($10 on $100 account)

**Grid Trading:**
- `grid_spacing`: 0.002-0.01 (% between orders)
  - 0.002 = tight grid (many small fills)
  - 0.01 = loose grid (fewer large fills)
- `grid_coefficient`: 1.1-2.0 (size multiplier per level)

**Entry:**
- `initial_qty_pct`: 0.05-0.2 (% of position size for first order)
- `entry_qty_pct`: 0.05-0.15 (% per grid level)
- `initial_eprice_ema_dist`: 0.005-0.02 (distance from EMA)

**Exit:**
- `close_grid_markup`: 0.005-0.05 (% profit target)
  - 0.01 = 1% profit per order
  - 0.03 = 3% profit per order
- `markup`: 0.01-0.03 (normal exit markup)

### Modify Configuration

1. Stop the bot:
```bash
systemctl stop passivbot
```

2. Edit config:
```bash
nano /opt/passivbot/configs/live/active.json
```

3. Validate syntax:
```bash
python3 -m json.tool /opt/passivbot/configs/live/active.json
```

4. Test with backtest (optional):
```bash
cd /opt/passivbot
python3 src/passivbot.py --backtest --config /opt/passivbot/configs/live/active.json
```

5. Restart bot:
```bash
systemctl start passivbot
```

---

## Troubleshooting

### Bot won't start

Check logs:
```bash
journalctl -u passivbot -p err
```

Common issues:
- **"ModuleNotFoundError"**: Reinstall requirements
  ```bash
  cd /opt/passivbot
  source venv/bin/activate
  pip install -r requirements.txt
  ```

- **"API key invalid"**: Check `/opt/passivbot/api_keys/api-keys.json`
  ```bash
  cat /opt/passivbot/api_keys/api-keys.json
  ```

- **"Config not found"**: Ensure `/opt/passivbot/configs/live/active.json` exists
  ```bash
  ls -la /opt/passivbot/configs/live/
  ```

### Bot is not placing orders

1. Check that API key has trade permissions:
   - Bybit Account → API Management → Verify "Trade" is enabled

2. Check balance:
   - Bybit → Wallet → Spot/Derivatives → Verify USDT available

3. Check logs for API errors:
   ```bash
   journalctl -u passivbot -f | grep -i "error\|trade"
   ```

4. Verify config:
   ```bash
   python3 -m json.tool /opt/passivbot/configs/live/active.json
   ```

### High CPU/Memory usage

Check resource limits in service file:
```bash
systemctl cat passivbot | grep -E "Memory|CPU"
```

Adjust in `/etc/systemd/system/passivbot.service`:
```ini
MemoryLimit=512M
CPUQuota=50%
```

Then reload:
```bash
systemctl daemon-reload
systemctl restart passivbot
```

### Performance issues

1. Check if Rust extension is installed (speeds up backtesting):
   ```bash
   cd /opt/passivbot
   python3 -c "import pyjitted; print('Rust extension OK')"
   ```

2. If not installed, try again:
   ```bash
   source venv/bin/activate
   pip install maturin
   maturin develop --release
   ```

---

## Safety & Best Practices

### Start Small

1. Deploy with **conservative config** ($100)
2. Run for **24-48 hours** without issues
3. Monitor logs and P&L closely
4. Increase capital ONLY after proven stability

### IP Whitelisting

Always whitelist your VPS IP on Bybit:
1. Go to Bybit → Account → API Management
2. Edit API key → IP Whitelist
3. Add `142.93.143.178`
4. Save

### API Key Security

- Store API keys **only in `/opt/passivbot/api_keys/api-keys.json`**
- Permissions: **600 (readable by root only)**
- **Never** share or commit API keys to git
- Rotate keys every **3-6 months**

### Position Limits

Conservative limits are pre-set to prevent blowups:
- `wallet_exposure_limit`: Prevents overleveraging
- `min_cost`: Prevents dust orders
- `leverage`: Limited to 3x (safe for learning)

### Monitoring

Set up alerts for:
- **Uptime**: Bot stopped unexpectedly
- **Balance**: Drops below threshold
- **P&L**: Large losses trigger manual review
- **Errors**: Check logs daily

---

## Advanced Usage

### Run Multiple Bots

To run bots on different symbols:

1. Create additional service files:
```bash
cp /etc/systemd/system/passivbot.service \
   /etc/systemd/system/passivbot-eth.service
```

2. Edit the new service:
```bash
nano /etc/systemd/system/passivbot-eth.service
```

3. Change config path:
```ini
ExecStart=/opt/passivbot/venv/bin/python3 src/passivbot.py --live --config /opt/passivbot/configs/live/active-eth.json
```

4. Enable and start:
```bash
systemctl enable passivbot-eth
systemctl start passivbot-eth
```

### Manual Backtest with Different Dates

```bash
cd /opt/passivbot
python3 src/passivbot.py \
  --backtest \
  --config /opt/passivbot/configs/live/active.json \
  --start-date 2025-01-01 \
  --end-date 2025-02-01
```

### Update Passivbot

```bash
cd /opt/passivbot
systemctl stop passivbot
git pull
source venv/bin/activate
pip install -r requirements.txt
systemctl start passivbot
```

---

## Support & Resources

- **Passivbot GitHub:** https://github.com/enarjord/passivbot
- **Passivbot Docs:** https://enarjord.github.io/passivbot/
- **Bybit API Docs:** https://bybit-exchange.github.io/docs/linear/
- **Community:** Discord (link in Passivbot README)

---

## Deployment Checklist

- [ ] Run `./deploy-passivbot.sh` successfully
- [ ] SSH to VPS and verify `/opt/passivbot` exists
- [ ] Run `bash /opt/passivbot/setup-api-keys.sh` and enter API keys
- [ ] Copy config: `cp /opt/passivbot/configs/bybit-100-conservative.json /opt/passivbot/configs/live/active.json`
- [ ] Backtest config: `python3 src/passivbot.py --backtest --config /opt/passivbot/configs/live/active.json`
- [ ] Review backtest results (win rate, Sharpe ratio, max drawdown)
- [ ] Start bot: `systemctl start passivbot`
- [ ] Check logs: `journalctl -u passivbot -f`
- [ ] Verify orders on Bybit (should see grid orders within 5 minutes)
- [ ] Monitor for 24-48 hours before scaling
- [ ] Document any customizations made

---

**Version:** 1.0
**Created:** 2026-02-05
**Target VPS:** 142.93.143.178
**Exchange:** Bybit Perpetuals (USDT)
