# Funding Rate Arbitrage Bot - File Index

**Project Location**: `/Users/chudinnorukam/Projects/business/funding-bot/`

**VPS Deployment**: `root@142.93.143.178:/opt/funding-bot/`

**Status**: ✅ PRODUCTION READY

---

## Quick Navigation

**New to this project?** → Start with `QUICKSTART.md` (5 minutes)

**Want to understand strategy?** → Read `README.md` (10 minutes)

**Ready to deploy?** → Follow `DEPLOYMENT_GUIDE.md` (20 minutes)

**Need technical details?** → See `IMPLEMENTATION.md` (30 minutes)

**Want to test first?** → Use `TESTING.md` (1 hour)

---

## Production Code Files

### Core Bot (`run_funding_bot.py`)
- **Lines**: 500
- **Purpose**: Main bot loop, orchestration, systemd integration
- **Key Classes**: `FundingRateBot`
- **Key Methods**:
  - `main_loop()` - Main trading cycle (every 60s)
  - `_trading_cycle()` - Scan rates, enter/exit positions
  - `_log_health_status()` - 5-min health snapshot
  - `_send_watchdog_ping()` - Systemd watchdog
  - `shutdown()` - Graceful exit

**Entry Point**:
```bash
python3 run_funding_bot.py
```

### Rate Scanner (`rate_scanner.py`)
- **Lines**: 250
- **Purpose**: Monitor funding rates, detect opportunities
- **Key Classes**: `FundingRateScanner`, `FundingRateData`
- **Key Methods**:
  - `scan_rates(pairs)` - Fetch current rates
  - `find_opportunities()` - Find profitable entries
  - `get_historical_rates()` - Analyze 7-day history
  - `get_current_rate()` - Single pair rate

**Usage**:
```python
scanner = FundingRateScanner(api_key, api_secret, dry_run=True)
opportunities = await scanner.find_opportunities(
    pairs=["BTCUSDT", "ETHUSDT"],
    min_rate=0.0002,
    stability_periods=3
)
```

### Position Manager (`position_manager.py`)
- **Lines**: 500
- **Purpose**: Enter/exit delta-neutral positions
- **Key Classes**: `PositionManager`, `Position`
- **Key Methods**:
  - `enter_position()` - Buy spot + short perp
  - `exit_position()` - Sell spot + close perp
  - `check_delta()` - Monitor hedge drift
  - `get_balance()` - Account balance
  - `get_open_positions()` - List active positions

**Usage**:
```python
pm = PositionManager(api_key, api_secret, dry_run=False)
position = await pm.enter_position(
    symbol="BTCUSDT",
    size_usdt=200.0,
    current_price=45000.0
)
```

### Database (`database.py`)
- **Lines**: 350
- **Purpose**: SQLite storage for positions, rates, status
- **Key Classes**: `FundingBotDatabase`
- **Tables**:
  - `positions` - Position entry/exit/P&L
  - `funding_history` - Funding payments
  - `bot_status` - Health snapshots (5-min)
  - `funding_rates` - Rate history for analysis
  - `errors` - Error log

**Usage**:
```python
db = FundingBotDatabase("data/funding.db")
db.add_position(position_id, symbol, entry_price, spot_qty, ...)
stats = db.get_stats()
```

### Configuration (`config.py`)
- **Lines**: 70
- **Purpose**: Load configuration from environment variables
- **Key Variables**:
  - `BYBIT_API_KEY`, `BYBIT_API_SECRET`
  - `PAIRS` - Trading pairs
  - `MIN_FUNDING_RATE` - Entry threshold
  - `MAX_POSITION_PCT` - Risk limit
  - `CHECK_INTERVAL` - Loop timing
  - All other parameters

**Usage**:
```python
from config import MIN_FUNDING_RATE, PAIRS, DRY_RUN
# All loaded from .env automatically
```

---

## Configuration Files

### `.env.example`
- **Purpose**: Configuration template
- **Action**: Copy to `.env` and edit
- **Key Settings**:
  - Bybit API credentials
  - Trading pairs
  - Risk parameters
  - Timing intervals
  - Telegram alerts (optional)

### `requirements.txt`
- **Purpose**: Python package dependencies
- **Packages**:
  - `pybit>=5.8.0` - Bybit SDK
  - `python-dotenv>=1.0.0` - .env loading
  - `aiohttp>=3.9.0` - Async HTTP

**Install**:
```bash
pip install -r requirements.txt
```

### `funding-bot.service`
- **Purpose**: Systemd service definition
- **Features**:
  - Type=notify (watchdog)
  - Auto-restart on crash
  - Graceful shutdown (30s timeout)
  - User isolation
- **Location**: `/etc/systemd/system/funding-bot.service`

---

## Deployment Scripts

### `deploy.sh`
- **Lines**: 150
- **Purpose**: Automated VPS deployment
- **Steps**:
  1. Validate .env
  2. Package code
  3. SCP to VPS
  4. Setup venv
  5. Install dependencies
  6. Validate syntax
  7. Create user
  8. Install systemd service
  9. Start service
  10. Verify running

**Usage**:
```bash
chmod +x deploy.sh
./deploy.sh
```

**Target**: `root@142.93.143.178:/opt/funding-bot/`

---

## Documentation Files

### `QUICKSTART.md` (Quick Start)
- **Length**: 150 lines
- **Time**: 5 minutes
- **Contains**:
  - 5-min local setup
  - Key configuration
  - How it works (30 seconds)
  - First week checklist
  - Monitoring commands
  - Economics/returns

**Best for**: Getting started immediately

### `README.md` (Full Documentation)
- **Length**: 250 lines
- **Time**: 10-15 minutes
- **Contains**:
  - Strategy explanation
  - Architecture diagram
  - Feature overview
  - Configuration guide
  - Risk parameters
  - Monitoring guide
  - Troubleshooting
  - API rate limits
  - Safety features

**Best for**: Understanding everything about the bot

### `DEPLOYMENT_GUIDE.md` (VPS Deployment)
- **Length**: 400 lines
- **Time**: 20 minutes (read), 10 minutes (execute)
- **Contains**:
  - Pre-deployment checklist
  - Phase-by-phase deployment
  - VPS setup steps
  - Verification procedures
  - First 24-hour monitoring
  - Configuration profiles (conservative/balanced/aggressive)
  - Operational commands
  - Troubleshooting guide
  - Emergency procedures
  - Rollback plan

**Best for**: Deploying to production VPS

### `TESTING.md` (Test Procedures)
- **Length**: 300 lines
- **Time**: 1+ hours (execution)
- **Contains**:
  - Phase 1: Local dry run tests
  - Phase 2: Testnet validation
  - Phase 3: Mainnet deployment
  - 8 detailed test cases
  - Stress testing procedures
  - Database inspection
  - Network debugging
  - Post-mortem analysis
  - Common issues & fixes

**Best for**: Thorough testing before production

### `IMPLEMENTATION.md` (Technical Deep Dive)
- **Length**: 500 lines
- **Time**: 30 minutes
- **Contains**:
  - System architecture diagram
  - Module breakdown (each file)
  - Rate calculation details
  - Position entry/exit flow
  - P&L calculation logic
  - Delta hedge mechanics
  - Database schema design
  - Main loop orchestration
  - Exit trigger logic
  - Key design decisions (9 rationales)
  - Performance considerations
  - Error handling strategy
  - Testing strategy
  - Future improvements

**Best for**: Understanding "why" and "how"

### `PROJECT_SUMMARY.md` (Project Overview)
- **Length**: 400 lines
- **Contains**:
  - Project completion status
  - What was built
  - Architecture overview
  - File structure
  - Feature breakdown
  - Deployment summary
  - Performance expectations
  - Configuration reference
  - Exit triggers
  - Systemd integration
  - Monitoring setup
  - Known limitations
  - Checklist

**Best for**: Overall project assessment

### `INDEX.md` (This File)
- **Purpose**: Navigate all files
- **Contains**: File descriptions, usage, key classes/methods

---

## Recommended Reading Order

### For Immediate Deployment (1 hour)

1. `QUICKSTART.md` (5 min) - Get running locally
2. `DEPLOYMENT_GUIDE.md` (15 min) - Read pre-deployment checklist
3. `.env.example` (2 min) - Understand configuration
4. `deploy.sh` (2 min) - Review script
5. Execute `deploy.sh` (10 min) - Deploy to VPS
6. Monitor via SSH commands (20 min) - Watch bot run

### For Deep Understanding (3 hours)

1. `README.md` (15 min) - Strategy overview
2. `IMPLEMENTATION.md` (45 min) - Technical architecture
3. `PROJECT_SUMMARY.md` (30 min) - Features & design
4. Code files (`run_funding_bot.py`, `position_manager.py`, etc.) (90 min) - Read source
5. `TESTING.md` (15 min) - Testing procedures

### For Production Operations (30 min)

1. `DEPLOYMENT_GUIDE.md` (15 min) - Focus on "Operational Commands" section
2. `QUICKSTART.md` (15 min) - Focus on "Monitor Bot" section

---

## Directory Structure (After First Run)

```
/Users/chudinnorukam/Projects/business/funding-bot/
├── data/
│   ├── funding.db              (SQLite database, created on first run)
│   └── adaptive_state.json     (Optional: tuning state)
├── logs/
│   ├── funding_bot.log         (Main log file)
│   └── health.json             (Health status, updated every 5 min)
├── venv/                       (Python virtual environment)
│   └── lib/python3.x/site-packages/
│       ├── pybit/
│       ├── aiohttp/
│       └── dotenv/
├── [Python source files]       (See above)
├── [Configuration files]       (See above)
├── [Documentation files]       (See above)
└── [Deployment files]         (See above)
```

---

## Key Metrics & Monitoring

### Health JSON (Updated Every 5 Minutes)

Location: `logs/health.json`

```json
{
  "timestamp": "2026-02-05T15:30:00Z",
  "balance": 475.32,
  "peak_balance": 500.0,
  "drawdown": 4.94,
  "open_positions": 2,
  "symbols_with_positions": ["BTCUSDT", "ETHUSDT"],
  "total_pnl": 125.45,
  "total_funding_collected": 98.20,
  "uptime_hours": 24.5,
  "positions_entered": 12,
  "positions_exited": 10,
  "errors": 0,
  "mode": "LIVE"
}
```

### Database Queries

```bash
# Total P&L
sqlite3 data/funding.db "SELECT SUM(pnl) FROM positions WHERE status='CLOSED';"

# Win rate
sqlite3 data/funding.db "SELECT SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)*100/COUNT(*) FROM positions WHERE status='CLOSED';"

# By symbol
sqlite3 data/funding.db "SELECT symbol, COUNT(*), SUM(pnl) FROM positions WHERE status='CLOSED' GROUP BY symbol;"

# Monthly stats
sqlite3 data/funding.db "SELECT DATE(exit_time), COUNT(*), SUM(pnl) FROM positions WHERE status='CLOSED' AND exit_time >= datetime('now', '-30 days') GROUP BY DATE(exit_time);"
```

---

## Common Commands

### Local Testing

```bash
# Setup
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# Run (dry run)
python3 run_funding_bot.py

# Check logs
tail -f logs/funding_bot.log

# Check health
cat logs/health.json | jq
```

### VPS Operations

```bash
# Status
ssh root@142.93.143.178 'systemctl status funding-bot'

# Logs
ssh root@142.93.143.178 'tail -f /opt/funding-bot/logs/funding_bot.log'

# Health
ssh root@142.93.143.178 'cat /opt/funding-bot/logs/health.json | jq'

# Restart
ssh root@142.93.143.178 'systemctl restart funding-bot'

# Stop
ssh root@142.93.143.178 'systemctl stop funding-bot'

# Start
ssh root@142.93.143.178 'systemctl start funding-bot'
```

---

## Support Resources

| Need | Resource | Time |
|------|----------|------|
| Get started quickly | `QUICKSTART.md` | 5 min |
| Understand strategy | `README.md` | 15 min |
| Deploy to VPS | `DEPLOYMENT_GUIDE.md` | 20 min |
| Learn technical details | `IMPLEMENTATION.md` | 30 min |
| Run tests | `TESTING.md` | 1+ hour |
| View all code | Python files | 1 hour |
| Project overview | `PROJECT_SUMMARY.md` | 15 min |

---

## Quick Facts

- **Total Code**: 1,670 lines (production)
- **Total Docs**: 1,600+ lines (guides)
- **Setup Time**: 10 minutes
- **Test Time**: 30 minutes (dry run)
- **Deploy Time**: 5 minutes (VPS)
- **Time to P&L**: 8 hours (first funding settlement)
- **Expected Return**: 15-30% annually
- **Capital Required**: $350-500 minimum
- **Risk Level**: Low (delta-neutral, 1x leverage)
- **Maintenance**: 5 min/day (monitoring)

---

**Last Updated**: 2026-02-05
**Status**: ✅ Production Ready
**Next Step**: Read `QUICKSTART.md`

