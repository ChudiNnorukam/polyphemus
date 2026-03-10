# Deployment Checklist

Complete verification checklist before going live on Arbitrum mainnet.

## Pre-Deployment (Do This First)

### Smart Contract

- [ ] Contract compiled with Solidity 0.8.20
- [ ] Contract deployed to Arbitrum mainnet
- [ ] Contract verified on Arbiscan
- [ ] Constructor args correct:
  - [ ] PoolAddressesProvider: `0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb`
  - [ ] UniswapRouter: `0xE592427A0AEce92De3Edee1F18E0157C05861564`
- [ ] Contract address copied to `.env` as `LIQUIDATOR_CONTRACT`
- [ ] Owner can call `withdraw()` function

### Account & Funding

- [ ] Private key secured (exported from Metamask/hardware wallet)
- [ ] Account has 0.1-0.5 ARB for gas
- [ ] Account has no other sensitive assets
- [ ] Private key added to `.env` as `PRIVATE_KEY`
- [ ] .env file in .gitignore (never commit!)

### RPC Configuration

- [ ] Arbitrum RPC tested with curl
- [ ] RPC responds to `eth_blockNumber`
- [ ] RPC in `.env` as `ARBITRUM_RPC`
- [ ] Consider upgrading to paid RPC if hitting rate limits:
  - [ ] Alchemy: https://www.alchemy.com/
  - [ ] Quicknode: https://www.quicknode.com/
  - [ ] Infura: https://www.infura.io/

### Local Testing

- [ ] Python 3.10+ installed: `python3 --version`
- [ ] Virtual environment created: `python3 -m venv venv`
- [ ] Dependencies installed: `pip install -r requirements.txt`
- [ ] All imports work: `python3 -c "import web3; print('OK')"`
- [ ] Configuration loads: `python3 -c "from config import config; config.validate(); print('OK')"`
- [ ] Syntax check: `python3 -m py_compile *.py`
- [ ] RPC connectivity test passes
- [ ] Monitor initializes without errors
- [ ] Executor initializes without errors

## VPS Setup

### Infrastructure

- [ ] VPS running Ubuntu 20.04 or later
- [ ] VPS IP: 142.93.143.178
- [ ] SSH access confirmed
- [ ] Root password updated (if using VPS)
- [ ] Firewall configured:
  - [ ] Allow SSH (port 22)
  - [ ] Allow HTTPS outbound (port 443)
  - [ ] Block everything else

### User & Permissions

- [ ] Non-root user created: `liquidbot`
- [ ] User has sudo access (for systemd)
- [ ] SSH key added to user (if available)
- [ ] Directory permissions: 755 for bot dir, 600 for .env

### Environment

- [ ] Python 3.12 installed on VPS
- [ ] pip works: `pip --version`
- [ ] Can create venv: `python3 -m venv test_venv`

## Deployment

### Before Running Deploy Script

```bash
# Verify deploy.sh is executable
chmod +x deploy.sh

# Test deploy script syntax
bash -n deploy.sh

# Verify all source files exist
ls -la config.py monitor.py executor.py database.py healthcheck.py run_liquidation_bot.py
ls -la contracts/FlashLiquidator.sol
ls -la requirements.txt liquidation-bot.service
```

### Run Deployment

- [ ] Command: `./deploy.sh 142.93.143.178 root <PRIVATE_KEY> <LIQUIDATOR_CONTRACT>`
- [ ] All files copied successfully
- [ ] Virtual environment created
- [ ] Dependencies installed without errors
- [ ] Python syntax check passed
- [ ] Service file installed
- [ ] .env created with correct values

### After Deployment

```bash
# Verify files on VPS
ssh root@142.93.143.178 ls -la /opt/liquidation-bot/

# Verify service exists
ssh root@142.93.143.178 systemctl cat liquidation-bot.service

# Verify .env exists and has values
ssh root@142.93.143.178 grep -c "PRIVATE_KEY" /opt/liquidation-bot/.env
```

## Service Testing

### Initial Start

```bash
# Start the service
ssh root@142.93.143.178 sudo systemctl start liquidation-bot

# Check status (should show "active (running)")
ssh root@142.93.143.178 sudo systemctl status liquidation-bot
```

- [ ] Service started without errors
- [ ] Status shows "active (running)"
- [ ] No permission denied errors
- [ ] No module import errors

### Log Verification

```bash
# Check initial logs (first 50 lines)
ssh root@142.93.143.178 sudo journalctl -u liquidation-bot -n 50

# Wait 30 seconds, then check again
sleep 30
ssh root@142.93.143.178 sudo journalctl -u liquidation-bot -n 20
```

- [ ] Connected to Arbitrum successfully
- [ ] RPC chain ID matches (42161)
- [ ] Scanning for liquidations
- [ ] No repeated error messages
- [ ] Health check running

### Health Status Check

```bash
# View health status
ssh root@142.93.143.178 cat /opt/liquidation-bot/data/health_status.json | jq .
```

- [ ] Status file exists
- [ ] Uptime > 0
- [ ] Borrowers scanned > 0
- [ ] No critical errors

### Database Check

```bash
# Check database was created
ssh root@142.93.143.178 ls -la /opt/liquidation-bot/data/liquidations.db
```

- [ ] Database file exists
- [ ] File size > 1KB

## Safety Verification

### Private Key Security

- [ ] Private key NOT in git history: `git log --all --full-history -p -- .env`
- [ ] Private key NOT in code comments
- [ ] Private key NOT in log files: `grep -r "0x" /opt/liquidation-bot/data/`
- [ ] .gitignore contains `.env`

### Account Safety

- [ ] Account balance checked: `ssh root@142.93.143.178 cat /opt/liquidation-bot/data/health_status.json`
- [ ] Only used for gas (~0.1 ARB)
- [ ] No other valuable assets

### Contract Safety

- [ ] Contract verified on Arbiscan
- [ ] Code matches deployed contract
- [ ] Only owner can call `withdraw()`

## 24-Hour Monitoring

### First Day (Monitor Closely)

- [ ] Service runs for 24 hours without restart
- [ ] No error messages in logs
- [ ] Health status updates every 5 minutes
- [ ] Database grows (new entries)

### If Liquidation Opportunity Found

- [ ] Transaction submitted to blockchain
- [ ] Transaction visible on Arbiscan
- [ ] Transaction succeeded (status=1)
- [ ] Profit recorded in database

### If No Liquidations Found

This is **normal**! Liquidatable positions are rare. Continue monitoring.

Check:
- [ ] Bot is still running: `systemctl status liquidation-bot`
- [ ] No error messages: `journalctl -u liquidation-bot`
- [ ] RPC still responding

## Performance Optimization

After 24 hours of successful operation, optimize:

### Scan Efficiency

```bash
# Check scan duration
sqlite3 /opt/liquidation-bot/data/liquidations.db \
  "SELECT AVG(scan_duration_ms), MAX(scan_duration_ms) FROM scan_metrics;"
```

- [ ] Average scan < 10s (else: may need faster RPC)
- [ ] Max scan < 20s (else: consider paid RPC)

### Profit Analysis

```bash
# Check if MIN_PROFIT_USD should change
sqlite3 /opt/liquidation-bot/data/liquidations.db \
  "SELECT estimated_profit, status FROM liquidations LIMIT 20;"
```

- [ ] Adjust MIN_PROFIT_USD if too many/few opportunities

### Error Tracking

```bash
# Check for repeated errors
sqlite3 /opt/liquidation-bot/data/liquidations.db \
  "SELECT error_msg, COUNT(*) FROM liquidations WHERE error_msg IS NOT NULL GROUP BY error_msg;"
```

- [ ] If errors present, investigate and fix

## Ongoing Maintenance

### Daily

- [ ] Check service running: `systemctl status liquidation-bot`
- [ ] Review logs: `journalctl -u liquidation-bot -n 50`
- [ ] Check health status: `cat /opt/liquidation-bot/data/health_status.json`

### Weekly

- [ ] Backup database: `cp data/liquidations.db data/liquidations.db.$(date +%Y%m%d).bak`
- [ ] Review profit: `SELECT SUM(actual_profit) FROM liquidations WHERE status='success';`
- [ ] Check success rate

### Monthly

- [ ] Rotate private key to new account
- [ ] Update dependencies: `pip install --upgrade -r requirements.txt`
- [ ] Withdraw accumulated profits

## Disable Checklist (If Shutting Down)

- [ ] Stop service: `sudo systemctl stop liquidation-bot`
- [ ] Disable auto-start: `sudo systemctl disable liquidation-bot`
- [ ] Backup database: `cp data/liquidations.db backup.db`
- [ ] Withdraw all profits: `call contract.withdraw()`
- [ ] Keep .env secure for future reference

---

## Quick Reference Commands

### View Status
```bash
systemctl status liquidation-bot
```

### View Logs (Real-Time)
```bash
journalctl -u liquidation-bot -f
```

### View Logs (Last 100 Lines)
```bash
journalctl -u liquidation-bot -n 100
```

### Check Health
```bash
cat /opt/liquidation-bot/data/health_status.json | jq .
```

### Check Database
```bash
sqlite3 /opt/liquidation-bot/data/liquidations.db "SELECT * FROM liquidations ORDER BY id DESC LIMIT 5;"
```

### Stop Service
```bash
sudo systemctl stop liquidation-bot
```

### Start Service
```bash
sudo systemctl start liquidation-bot
```

### Restart Service
```bash
sudo systemctl restart liquidation-bot
```

### Enable Auto-Start
```bash
sudo systemctl enable liquidation-bot
```

### View Service File
```bash
systemctl cat liquidation-bot.service
```

---

## Troubleshooting During Deployment

### "Connection refused"
- [ ] Check RPC URL in .env
- [ ] Verify RPC is accessible: `curl https://arb1.arbitrum.io/rpc`
- [ ] Check firewall allows outbound HTTPS

### "Module not found"
- [ ] Verify pip install succeeded: `pip list | grep web3`
- [ ] Check venv is activated: `which python3`
- [ ] Re-run: `pip install -r requirements.txt`

### "Invalid private key"
- [ ] Check format: must start with `0x` and be 66 characters
- [ ] Verify no spaces or quotes in .env
- [ ] Test in Python: `from eth_account import Account; Account.from_key("<KEY>")`

### "Contract not found"
- [ ] Verify contract deployed: Go to Arbiscan with contract address
- [ ] Check contract address in .env matches deployment
- [ ] Ensure contract is on Arbitrum mainnet (chain ID 42161)

### "Watchdog timeout"
- [ ] Check bot logs: `journalctl -u liquidation-bot -n 100`
- [ ] Increase WatchdogSec in service file if needed
- [ ] May indicate RPC hanging (needs faster RPC provider)

---

## Sign-Off

Once all items checked:

**Date Deployed**: _______________
**Deployed By**: _______________
**Contract Address**: _______________
**First Liquidation Date**: _______________
**Total Profit (Month 1)**: _______________

---

**✅ Ready for Production**: All checklist items complete!
