# Testing & Verification Guide

Complete testing strategy for the liquidation bot.

## Pre-Deployment Checklist

### 1. Environment Setup

```bash
# Verify Python version
python3 --version  # Must be 3.10+

# Verify dependencies
pip list | grep -E "web3|aiohttp|dotenv"

# Verify files exist
ls -la config.py monitor.py executor.py database.py healthcheck.py run_liquidation_bot.py

# Verify contract
ls -la contracts/FlashLiquidator.sol
```

### 2. Configuration Validation

```bash
# Create test .env
cp .env.example .env

# Edit with test values
# PRIVATE_KEY: Use account with 0.1+ ARB (testnet: use faucet)
# LIQUIDATOR_CONTRACT: Use deployed contract address
# MIN_PROFIT_USD: Set to 1.0 for testing

# Verify imports
python3 -c "
import config
import monitor
import executor
import database
import healthcheck
print('✅ All modules importable')
"

# Validate configuration
python3 -c "
from config import config
config.validate()
print('✅ Configuration valid')
print(f'  RPC: {config.arbitrum_rpc}')
print(f'  Chain ID: {config.chain_id}')
print(f'  Min Profit: ${config.min_profit_usd}')
"
```

### 3. RPC Connectivity

```bash
# Test RPC endpoint
curl https://arb1.arbitrum.io/rpc \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}'

# Expected response: {"jsonrpc":"2.0","result":"0x...", "id":1}

# Test in Python
python3 -c "
import asyncio
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

async def test_rpc():
    w3 = AsyncWeb3(AsyncHTTPProvider('https://arb1.arbitrum.io/rpc'))
    block = await w3.eth.block_number
    print(f'✅ Connected to Arbitrum, latest block: {block}')

asyncio.run(test_rpc())
"
```

### 4. Contract Deployment

#### Option A: Verify Pre-Deployed Contract

```bash
# Check contract exists on Arbitrum
# Go to https://arbiscan.io/address/<LIQUIDATOR_CONTRACT>

# Verify contract has:
# - liquidateWithFlashLoan() function
# - executeOperation() function
# - withdraw() function

# Test contract is callable
python3 -c "
import asyncio
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider
from config import config

async def test_contract():
    w3 = AsyncWeb3(AsyncHTTPProvider(config.arbitrum_rpc))

    # Simple contract existence check
    code = await w3.eth.get_code(config.liquidator_contract)
    if len(code) > 2:
        print(f'✅ Contract exists at {config.liquidator_contract}')
    else:
        print(f'❌ No contract code at {config.liquidator_contract}')

asyncio.run(test_contract())
"
```

#### Option B: Deploy New Contract

```bash
# Using Hardhat
npm install
npx hardhat compile

# Deploy to Arbitrum
npx hardhat run scripts/deploy.ts --network arbitrum

# Deploy to testnet first
npx hardhat run scripts/deploy.ts --network arbitrumGoerli
```

### 5. Monitor Module Testing

```bash
# Test monitor initialization
python3 << 'EOF'
import asyncio
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider
from monitor import AaveLiquidationMonitor
from config import config

AAVE_POOL_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "user", "type": "address"}],
        "name": "getUserAccountData",
        "outputs": [
            {"internalType": "uint256", "name": "totalCollateralBase", "type": "uint256"},
            {"internalType": "uint256", "name": "totalDebtBase", "type": "uint256"},
            {"internalType": "uint256", "name": "availableBorrowsBase", "type": "uint256"},
            {"internalType": "uint256", "name": "currentLiquidationThreshold", "type": "uint256"},
            {"internalType": "uint256", "name": "ltv", "type": "uint256"},
            {"internalType": "uint256", "name": "healthFactor", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

POOL_DATA_PROVIDER_ABI = []

async def test_monitor():
    w3 = AsyncWeb3(AsyncHTTPProvider(config.arbitrum_rpc))
    monitor = AaveLiquidationMonitor(
        w3,
        config.aave_pool,
        config.aave_pool_data_provider,
        AAVE_POOL_ABI,
        POOL_DATA_PROVIDER_ABI
    )

    # Test getting current block
    block = await monitor.get_current_block()
    print(f"✅ Monitor initialized, current block: {block}")

    # Test checking a known user (if available)
    # This would fail for random address (no debt) which is expected
    test_user = "0x" + "1" * 40
    result = await monitor.check_health_factor(test_user)
    print(f"✅ Health factor check works (returned: {result})")

asyncio.run(test_monitor())
EOF
```

### 6. Executor Module Testing

```bash
# Test executor initialization
python3 << 'EOF'
import asyncio
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider
from executor import LiquidationExecutor
from config import config

LIQUIDATOR_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "collateralAsset", "type": "address"},
            {"internalType": "address", "name": "debtAsset", "type": "address"},
            {"internalType": "address", "name": "user", "type": "address"},
            {"internalType": "uint256", "name": "debtToCover", "type": "uint256"},
        ],
        "name": "liquidateWithFlashLoan",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

POOL_ABI = []
ORACLE_ABI = []
QUOTER_ABI = []

async def test_executor():
    w3 = AsyncWeb3(AsyncHTTPProvider(config.arbitrum_rpc))
    executor = LiquidationExecutor(
        w3,
        config.liquidator_contract,
        config.private_key,
        config.aave_pool,
        config.aave_oracle,
        config.aave_pool_data_provider,
        config.uniswap_v3_quoter,
        LIQUIDATOR_ABI,
        POOL_ABI,
        ORACLE_ABI,
        QUOTER_ABI,
    )

    print(f"✅ Executor initialized")
    print(f"   Account: {executor.address}")

    # Test price fetch
    usdc_price = await executor.get_asset_price(config.usdc)
    print(f"✅ Got USDC price: {usdc_price} (should be ~1e8)")

    # Test reserve data
    reserve = await executor.get_reserve_data(config.usdc)
    print(f"✅ Got reserve data: decimals={reserve['decimals']}, bonus={reserve['liquidation_bonus']}")

asyncio.run(test_executor())
EOF
```

### 7. Database Testing

```bash
# Test database initialization
python3 << 'EOF'
import os
from database import LiquidationDatabase

# Use test database
db = LiquidationDatabase("test_liquidations.db")
print("✅ Database initialized")

# Test logging
liq_id = db.log_liquidation(
    user="0x" + "a" * 40,
    collateral_asset="0x" + "b" * 40,
    debt_asset="0x" + "c" * 40,
    debt_amount=100.0,
    estimated_profit=50.0,
    status="testing"
)
print(f"✅ Logged test liquidation: {liq_id}")

# Test stats
stats = db.get_liquidation_stats()
print(f"✅ Stats: {stats}")

# Cleanup
os.remove("test_liquidations.db")
EOF
```

### 8. Full Integration Test (Read-Only)

```bash
# Run bot in scan-only mode (no execution)
python3 << 'EOF'
import asyncio
import logging
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider
from monitor import AaveLiquidationMonitor
from config import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AAVE_POOL_ABI = []  # Use real ABI
POOL_DATA_PROVIDER_ABI = []

async def test_integration():
    logger.info("Starting integration test...")

    w3 = AsyncWeb3(AsyncHTTPProvider(config.arbitrum_rpc))
    monitor = AaveLiquidationMonitor(
        w3,
        config.aave_pool,
        config.aave_pool_data_provider,
        AAVE_POOL_ABI,
        POOL_DATA_PROVIDER_ABI
    )

    logger.info("Scanning for liquidations...")
    results = await monitor.scan_for_liquidations(batch_size=50)

    logger.info(f"✅ Found {len(results)} liquidatable positions")
    for pos in results[:5]:  # Show first 5
        logger.info(
            f"  User: {pos.user}, "
            f"Debt: ${pos.total_debt_base / 1e6:.2f}, "
            f"HF: {pos.health_factor_normalized:.4f}"
        )

asyncio.run(test_integration())
EOF
```

## Testnet Deployment

### Setup

```bash
# Get Arbitrum Goerli ETH from faucet
# https://faucet.arbitrum.io/

# Deploy contract to testnet
npx hardhat run scripts/deploy.ts --network arbitrumGoerli

# Update .env with testnet contract address
LIQUIDATOR_CONTRACT=0x...  # Testnet contract
ARBITRUM_RPC=https://goerli-rollup.arbitrum.io:8443

# Set very low profit threshold for testing
MIN_PROFIT_USD=0.10
```

### Run on Testnet

```bash
# Run bot in test mode (will try to execute if opportunity found)
LOG_LEVEL=DEBUG python3 run_liquidation_bot.py

# You'll see:
# ✅ Connected to Arbitrum Goerli
# ℹ️ Scanning...
# ℹ️ Found X liquidatable positions
# (No liquidations likely on testnet due to low liquidity)
```

## Mainnet Deployment

### Pre-Flight Checklist

- [ ] Contract deployed and verified on Arbiscan
- [ ] Private key secured (0.1-0.5 ARB balance)
- [ ] MIN_PROFIT_USD set conservatively (≥$5)
- [ ] CHECK_INTERVAL set to 12-30 seconds
- [ ] Telegram notifications configured (optional)
- [ ] Database backup script ready
- [ ] Monitoring plan documented

### Gradual Rollout

**Phase 1: Monitor Mode (1 day)**
```bash
# Run in monitor-only (no execution)
# Kill liquidation execution in run_liquidation_bot.py
# Just log opportunities to database

# Check: Do we find opportunities?
sqlite3 data/liquidations.db "SELECT COUNT(*) FROM opportunities WHERE liquidatable=1;"
```

**Phase 2: Small Executions (1 day)**
```bash
# Set MIN_PROFIT_USD=50 (skip small opportunities)
# Enable execution
# Let bot run for 24h

# Check: Do we execute? What's success rate?
sqlite3 data/liquidations.db "SELECT COUNT(*), SUM(actual_profit) FROM liquidations WHERE status='success';"
```

**Phase 3: Full Mode (Ongoing)**
```bash
# Set MIN_PROFIT_USD=5
# Monitor continuously
# Adjust parameters based on performance
```

## Performance Testing

### Scan Performance

```bash
# Measure scan speed with different batch sizes
python3 << 'EOF'
import asyncio
import time
from monitor import AaveLiquidationMonitor

async def test_scan_perf():
    monitor = AaveLiquidationMonitor(...)

    for batch_size in [50, 100, 200]:
        start = time.time()
        results = await monitor.scan_for_liquidations(batch_size)
        elapsed = time.time() - start

        print(f"Batch {batch_size}: {elapsed:.2f}s, {len(results)} found")

asyncio.run(test_scan_perf())
EOF
```

### Gas Estimation Accuracy

```bash
# Compare estimated vs actual gas
python3 << 'EOF'
# Get last 10 successful liquidations
SELECT
  user,
  estimated_profit - actual_profit as profit_diff,
  gas_cost
FROM liquidations
WHERE status='success'
ORDER BY created_at DESC
LIMIT 10;

# If profit_diff is large, adjust gas_buffer_multiplier or slippage_tolerance
EOF
```

## Continuous Monitoring

### Daily Checks

```bash
# Check bot is running
systemctl status liquidation-bot

# Check recent activity
journalctl -u liquidation-bot -n 50

# View health status
cat data/health_status.json | jq .

# Check profit
sqlite3 data/liquidations.db "SELECT SUM(actual_profit) FROM liquidations WHERE created_at > datetime('now', '-1 day');"
```

### Weekly Checks

```bash
# Backup database
cp data/liquidations.db data/liquidations.db.$(date +%Y%m%d).backup

# Check success rate
sqlite3 data/liquidations.db "SELECT COUNT(*) as attempts, SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as successful FROM liquidations;"

# Analyze profit by asset
sqlite3 data/liquidations.db "SELECT collateral_asset, debt_asset, COUNT(*), SUM(actual_profit) FROM liquidations WHERE status='success' GROUP BY collateral_asset, debt_asset;"

# Check for error patterns
sqlite3 data/liquidations.db "SELECT error_msg, COUNT(*) FROM liquidations WHERE error_msg IS NOT NULL GROUP BY error_msg;"
```

### Monthly Checks

```bash
# Full performance review
SELECT
  date(created_at) as day,
  COUNT(*) as attempts,
  SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as successful,
  SUM(actual_profit) as daily_profit
FROM liquidations
WHERE created_at > datetime('now', '-30 days')
GROUP BY date(created_at)
ORDER BY day DESC;

# Update bot parameters if needed
# - Increase MIN_PROFIT if too many small ones
# - Decrease if missing opportunities
```

## Debugging Failed Liquidations

### Common Issues

```bash
# 1. Transaction reverted
# Check Arbiscan for revert reason
# Likely: insufficient collateral or slippage too tight

# 2. Flash loan fee miscalculation
# Verify fee in executor.py is 0.05% (0.0005)
# Check Aave documentation for latest fee

# 3. Uniswap swap failed
# Test swap manually via swap.defillama.com
# Check liquidity for collateral/debt pair

# 4. Out of gas
# Increase gas_buffer_multiplier in config
# Or set higher MIN_PROFIT_USD to skip edge cases
```

### Debug Mode

```bash
# Set LOG_LEVEL=DEBUG
LOG_LEVEL=DEBUG python3 run_liquidation_bot.py

# This outputs:
# - All RPC calls
# - Price fetches
# - Gas estimates
# - Profit calculations (per-component breakdown)
# - Transaction details
```

---

**Testing Complete?** You're ready for production!
