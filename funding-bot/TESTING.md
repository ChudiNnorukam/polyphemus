# Testing Guide for Funding Rate Bot

Complete testing procedures before deploying to VPS with real capital.

## Phase 1: Local Dry Run (Paper Trading)

### Setup

```bash
# Install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure for dry run
cp .env.example .env
# Edit .env:
# - DRY_RUN=true
# - Leave BYBIT_API_KEY/SECRET empty or set to dummy values
```

### Test 1: Configuration Validation

```bash
python3 -c "import config; print('✅ Config loads correctly')"
```

Should output without errors.

### Test 2: Module Imports

```bash
python3 << 'EOF'
from config import *
from rate_scanner import FundingRateScanner
from position_manager import PositionManager
from database import FundingBotDatabase
print("✅ All modules import successfully")
EOF
```

### Test 3: Database Setup

```bash
python3 << 'EOF'
from database import FundingBotDatabase
db = FundingBotDatabase("test.db")
stats = db.get_stats()
print("✅ Database initialized:", stats)
EOF
```

Expected output:
```
✅ Database initialized: {'total_positions': 0, 'open_positions': 0, 'closed_positions': 0, 'total_pnl': 0.0, 'total_funding_collected': 0.0, 'avg_pnl': 0.0}
```

### Test 4: Rate Scanner (Paper Mode)

```bash
python3 << 'EOF'
import asyncio
from rate_scanner import FundingRateScanner

async def test_scanner():
    scanner = FundingRateScanner(dry_run=True)
    pairs = ["BTCUSDT", "ETHUSDT"]

    rates = await scanner.scan_rates(pairs)
    print(f"✅ Scanned {len(rates)} pairs")

    for symbol, rate_data in rates.items():
        print(f"  {symbol}: rate={rate_data.current_rate:.6f} apy={rate_data.annualized_apy:.2%}")

    scanner.close()

asyncio.run(test_scanner())
EOF
```

### Test 5: Position Manager (Paper Mode)

```bash
python3 << 'EOF'
import asyncio
from position_manager import PositionManager

async def test_positions():
    pm = PositionManager(dry_run=True)

    # Test entry
    position = await pm.enter_position(
        symbol="BTCUSDT",
        size_usdt=100.0,
        current_price=45000.0
    )

    if position:
        print(f"✅ Position entered: {position.position_id}")
        print(f"   Spot qty: {position.spot_qty:.6f}")
        print(f"   Perp qty: {position.perp_qty:.6f}")
        print(f"   Balance: ${pm.paper_balance:.2f}")

    # Test exit
    pnl = await pm.exit_position("BTCUSDT", "test")
    if pnl is not None:
        print(f"✅ Position exited with P&L: ${pnl:.2f}")

    pm.close()

asyncio.run(test_positions())
EOF
```

### Test 6: Main Bot Loop (5 minute dry run)

```bash
# Edit .env to set CHECK_INTERVAL=5 and HEALTH_CHECK_INTERVAL=10
# Then run:
timeout 30 python3 run_funding_bot.py
```

Expected behavior:
- Bot starts and logs initialization
- Scans funding rates
- May enter/exit positions based on rates
- Logs health status every 10 seconds
- Exits cleanly after timeout

Check outputs:
```bash
cat logs/health.json | jq
tail logs/funding_bot.log
sqlite3 data/funding.db "SELECT * FROM bot_status LIMIT 5;"
```

## Phase 2: Testnet with Real API

### Setup Bybit Testnet Account

1. Go to https://testnet.bybit.com
2. Create account
3. Generate API key (get testnet credentials)
4. Fund testnet account with demo USDT

### Configure Testnet

```bash
# Edit .env
DRY_RUN=false
BYBIT_API_KEY=<testnet-api-key>
BYBIT_API_SECRET=<testnet-api-secret>
```

### Test 7: Live API Calls (Testnet)

```bash
python3 << 'EOF'
import asyncio
from rate_scanner import FundingRateScanner
from position_manager import PositionManager

async def test_live():
    from config import BYBIT_API_KEY, BYBIT_API_SECRET

    # Test scanner on testnet
    scanner = FundingRateScanner(
        api_key=BYBIT_API_KEY,
        api_secret=BYBIT_API_SECRET,
        dry_run=True  # Still use testnet flag
    )

    rates = await scanner.scan_rates(["BTCUSDT"])
    print("✅ Live API rates retrieved:")
    for sym, data in rates.items():
        print(f"  {sym}: {data.current_rate:.6f}")

    # Test position manager balance check
    pm = PositionManager(
        api_key=BYBIT_API_KEY,
        api_secret=BYBIT_API_SECRET,
        dry_run=False
    )

    balance = await pm.get_balance()
    print(f"✅ Testnet balance: ${balance:.2f}")

    scanner.close()
    pm.close()

asyncio.run(test_live())
EOF
```

### Test 8: Testnet Position Entry/Exit

```bash
python3 << 'EOF'
import asyncio
from rate_scanner import FundingRateScanner
from position_manager import PositionManager
from config import BYBIT_API_KEY, BYBIT_API_SECRET

async def test_positions():
    scanner = FundingRateScanner(
        api_key=BYBIT_API_KEY,
        api_secret=BYBIT_API_SECRET,
        dry_run=True
    )
    pm = PositionManager(
        api_key=BYBIT_API_KEY,
        api_secret=BYBIT_API_SECRET,
        dry_run=False
    )

    # Get current price
    rates = await scanner.scan_rates(["BTCUSDT"])
    price = 45000.0  # Fallback
    for sym, data in rates.items():
        print(f"Rate for {sym}: {data.current_rate:.6f}")

    # Enter position
    position = await pm.enter_position(
        symbol="BTCUSDT",
        size_usdt=50.0,  # Small testnet position
        current_price=price
    )

    if position:
        print(f"✅ Position entered: {position.position_id}")

        # Wait for fills
        await asyncio.sleep(10)

        # Exit position
        pnl = await pm.exit_position("BTCUSDT", "test")
        if pnl is not None:
            print(f"✅ Position exited with P&L: ${pnl:.2f}")
        else:
            print("❌ Position exit failed")
    else:
        print("❌ Position entry failed")

    scanner.close()
    pm.close()

asyncio.run(test_positions())
EOF
```

## Phase 3: Mainnet with Real Capital (Small)

### Final Safety Checks

Before going live with real money:

1. **Verify API credentials work** (test rate fetch)
2. **Test small position** ($10-50)
3. **Monitor for 24 hours** before scaling
4. **Have exit plan ready** (know how to stop bot)
5. **Verify Telegram alerts** if enabled

### Deployment Checklist

- [ ] `.env` configured correctly for mainnet
- [ ] BYBIT_API_KEY and BYBIT_API_SECRET set
- [ ] DRY_RUN=false
- [ ] MIN_FUNDING_RATE set appropriately
- [ ] MAX_POSITION_PCT limited (0.4 = 40%)
- [ ] PAIRS configured (start with 1-2)
- [ ] Database path correct
- [ ] Logs directory writable
- [ ] deploy.sh reviewed
- [ ] systemd service paths correct

### Deployment

```bash
chmod +x deploy.sh
./deploy.sh

# Verify deployment
ssh root@142.93.143.178 'systemctl status funding-bot'
ssh root@142.93.143.178 'tail -20 /opt/funding-bot/logs/funding_bot.log'
```

### Monitoring First 24 Hours

```bash
# Every hour, check:
ssh root@142.93.143.178 'cat /opt/funding-bot/logs/health.json | jq'

# Monitor for errors:
ssh root@142.93.143.178 'grep ERROR /opt/funding-bot/logs/funding_bot.log | tail -5'

# Check positions:
ssh root@142.93.143.178 'sqlite3 /opt/funding-bot/data/funding.db "SELECT symbol, status, entry_price, pnl FROM positions ORDER BY entry_time DESC LIMIT 10;"'
```

## Performance Benchmarks

Expected performance on dry run with mock data:

| Metric | Expected |
|--------|----------|
| Startup time | <5 seconds |
| First rate scan | <2 seconds |
| Position entry | <10 seconds (simulated) |
| Position exit | <10 seconds (simulated) |
| Health check | <1 second |
| Memory usage | <100 MB |
| CPU usage | <5% (idle) |

## Stress Testing

### High Rate Update Frequency

```bash
# Change in .env
CHECK_INTERVAL=5  # Check every 5 seconds
HEALTH_CHECK_INTERVAL=10  # Health every 10 seconds

# Run for 5 minutes
timeout 300 python3 run_funding_bot.py

# Check for memory leaks
watch -n 1 'ps aux | grep python'
```

### Many Positions

Manually insert test positions:
```bash
sqlite3 data/funding.db << 'EOF'
INSERT INTO positions (id, symbol, entry_price, spot_qty, perp_qty, entry_time, status, created_at)
VALUES
  ('pos1', 'BTCUSDT', 45000, 0.001, 0.001, datetime('now'), 'OPEN', datetime('now')),
  ('pos2', 'ETHUSDT', 2500, 0.1, 0.1, datetime('now'), 'OPEN', datetime('now')),
  ('pos3', 'SOLUSDT', 150, 1.0, 1.0, datetime('now'), 'OPEN', datetime('now'));
EOF
```

Then run bot and verify it handles multiple positions correctly.

## Debugging Tips

### Enable Debug Logging

```bash
# Edit .env
LOG_LEVEL=DEBUG

# Run bot
python3 run_funding_bot.py 2>&1 | grep -E "DEBUG|ERROR|CLOSED"
```

### Database Inspection

```bash
# List all tables
sqlite3 data/funding.db ".tables"

# View positions
sqlite3 data/funding.db ".schema positions"
sqlite3 data/funding.db "SELECT * FROM positions LIMIT 3;"

# View errors
sqlite3 data/funding.db "SELECT error_type, message FROM errors LIMIT 5;"

# View rates history
sqlite3 data/funding.db "SELECT symbol, current_rate, timestamp FROM funding_rates WHERE symbol='BTCUSDT' ORDER BY timestamp DESC LIMIT 10;"
```

### Network Debugging

```bash
# Monitor API calls with tcpdump
sudo tcpdump -i any -n 'port 443' | grep api.bybit

# Or use strace to see system calls
strace -e trace=network python3 run_funding_bot.py 2>&1 | head -50
```

## Rollback Plan

If bot goes wrong on mainnet:

```bash
# Stop bot immediately
ssh root@142.93.143.178 'systemctl stop funding-bot'

# Check open positions
ssh root@142.93.143.178 'sqlite3 /opt/funding-bot/data/funding.db "SELECT * FROM positions WHERE status='\''OPEN'\'';"'

# Manually close positions via Bybit web UI or API
# Then verify closed
ssh root@142.93.143.178 'sqlite3 /opt/funding-bot/data/funding.db "SELECT COUNT(*) FROM positions WHERE status='\''OPEN'\'';"'

# If zero, safe to restart or delete .env and redeploy
```

## Post-Mortem Analysis

After bot runs, analyze performance:

```bash
# Calculate P&L by symbol
sqlite3 data/funding.db << 'EOF'
SELECT
  symbol,
  COUNT(*) as num_trades,
  ROUND(SUM(pnl), 2) as total_pnl,
  ROUND(AVG(pnl), 2) as avg_pnl,
  ROUND(MAX(pnl), 2) as max_win,
  ROUND(MIN(pnl), 2) as max_loss,
  ROUND(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) as win_rate
FROM positions
WHERE status = 'CLOSED'
GROUP BY symbol;
EOF
```

## Common Issues and Fixes

| Issue | Cause | Fix |
|-------|-------|-----|
| `ModuleNotFoundError: No module named 'pybit'` | Dependencies not installed | `pip install -r requirements.txt` |
| `ValueError: BYBIT_API_KEY required` | Missing credentials | Set in `.env` or use `DRY_RUN=true` |
| `sqlite3.OperationalError: database is locked` | DB file access issue | Stop bot, check file permissions |
| `OSError: [Errno 13] Permission denied` | Log file not writable | `mkdir -p logs data && chmod 755 logs data` |
| `ConnectionError: Failed to establish a new connection` | API unreachable | Check network, Bybit status page |
| `No positions entered` | Rates below threshold | Lower `MIN_FUNDING_RATE` or wait for higher rates |

