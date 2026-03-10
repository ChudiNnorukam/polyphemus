# Architecture & Design

Complete technical architecture of the Aave V3 Liquidation Bot.

## System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      Liquidation Bot                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────────┐         ┌──────────────────┐         │
│  │  Monitor         │         │  Executor        │         │
│  │  ────────────    │         │  ────────────    │         │
│  │ • Fetch events   │         │ • Estimate profit│         │
│  │ • Check HF       │         │ • Execute TX     │         │
│  │ • Batch check    │         │ • Swap collateral│         │
│  └────────┬─────────┘         └────────┬─────────┘         │
│           │                           │                    │
│  ┌────────▼───────────────────────────▼──────┐             │
│  │         run_liquidation_bot.py            │             │
│  │  • Main loop (12s intervals)              │             │
│  │  • Profitability check                    │             │
│  │  • Error handling & retries               │             │
│  └────────┬───────────────────────────┬──────┘             │
│           │                           │                    │
│  ┌────────▼──────────────┐  ┌────────▼─────────────────┐  │
│  │  database.py          │  │  healthcheck.py         │  │
│  │  • SQLite tracking    │  │  • Systemd watchdog     │  │
│  │  • Liquidation logs   │  │  • Health JSON          │  │
│  │  • Performance stats  │  │  • Telegram alerts      │  │
│  └───────────────────────┘  └─────────────────────────┘  │
│                                                              │
└─────────────────────────────────────────────────────────────┘
                           │
                ┌──────────┴──────────┐
                │                     │
         ┌──────▼──────┐      ┌──────▼────────┐
         │ Arbitrum    │      │  Flash Loan   │
         │ RPC         │      │  Smart        │
         │             │      │  Contract     │
         │ • Aave Pool │      │               │
         │ • Events    │      │ FlashLiquidato│
         │ • Oracle    │      │ r.sol         │
         └─────────────┘      └───────────────┘
                │
         ┌──────▴────────┐
         │ Uniswap V3    │
         │ • Quoter      │
         │ • Router      │
         └───────────────┘
```

## Data Flow

### Liquidation Detection Flow

```
1. Monitor.scan_for_liquidations()
   │
   ├─ fetch_borrowers_from_events()
   │  └─ Query Aave Pool Borrow events (last 1000 blocks)
   │     └─ Cache addresses in OrderedDict (max 5000)
   │
   └─ check_multiple_users()
      ├─ AsyncIO gather 100 concurrent health checks
      │  │
      │  └─ pool.getUserAccountData(user)
      │     Returns: (totalCollateral, totalDebt, healthFactor, ...)
      │
      └─ Filter: health_factor < 1e18
         └─ Return: List[LiquidatablePosition]
```

### Execution Flow

```
1. run_liquidation_bot.py
   │
   ├─ For each liquidatable position:
   │  │
   │  ├─ executor.estimate_profit()
   │  │  │
   │  │  ├─ Get prices: oracle.getAssetPrice(asset)
   │  │  ├─ Get reserve config: pool.getReserveData(asset)
   │  │  ├─ Estimate Uniswap output: quoter.quoteExactInputSingle()
   │  │  │
   │  │  └─ Calculate:
   │  │     profit = swap_output - debt - flash_fee - gas
   │  │
   │  └─ If profit > MIN_PROFIT_USD:
   │     │
   │     └─ executor.execute_liquidation()
   │        │
   │        └─ liquidator.liquidateWithFlashLoan()
   │           │
   │           └─ Aave Pool.flashLoanSimple()
   │              │
   │              └─ executeOperation() callback:
   │                 ├─ Approve debt tokens
   │                 ├─ pool.liquidationCall() ← Seize collateral
   │                 ├─ uniswap.exactInputSingle() ← Swap
   │                 └─ Repay flash loan + fee
   │
   ├─ Log results to database
   ├─ Send Telegram notification
   └─ Update health status
```

## Module Breakdown

### config.py (60 lines)

**Purpose**: Centralized configuration management.

**Key Classes**:
- `Config`: Dataclass with all settings
  - RPC endpoints, contract addresses
  - Bot parameters (MIN_PROFIT_USD, CHECK_INTERVAL)
  - Timeouts, retry logic
  - Logging configuration

**Key Methods**:
- `validate()`: Ensures PRIVATE_KEY and LIQUIDATOR_CONTRACT are set

**Dependencies**: dotenv

---

### monitor.py (250 lines)

**Purpose**: Identify liquidation opportunities on Aave V3.

**Key Classes**:
- `LiquidatablePosition`: Dataclass representing a borrower
  - Fields: user, collateral, debt, health_factor, etc.
  - Property: `is_liquidatable` (HF < 1.0)

- `AaveLiquidationMonitor`: Main monitoring logic
  - Event-based borrower discovery (Borrow events)
  - Health factor batch checking
  - Memory cache with FIFO eviction (max 5000)

**Key Methods**:
- `fetch_borrowers_from_events()`: Scans Borrow events, updates cache
- `check_health_factor()`: Gets user account data from pool
- `check_multiple_users()`: Concurrent checks via asyncio.gather()
- `scan_for_liquidations()`: Main entry point

**Concurrency**: AsyncIO with AsyncWeb3

**Performance**:
- Event scanning: ~1000-2000 blocks per scan
- Health checks: 100 users per RPC call
- Memory: ~100-500 KB for borrower cache

---

### executor.py (300 lines)

**Purpose**: Estimate profitability and execute liquidations.

**Key Classes**:
- `LiquidationOpportunity`: Opportunity with profit estimate

- `LiquidationExecutor`: Execution engine
  - Tracks account state (nonce, balance)
  - Manages contract interactions

**Key Methods**:
- `estimate_profit()`: Complex profit calculation
  - Fetches prices from Aave oracle (8 decimal format)
  - Gets reserve config (liquidation bonus, decimals)
  - Quotes Uniswap V3 swap output
  - Accounts for: flash fee, gas cost, slippage
  - Returns: (profit_usd, details_dict)

- `execute_liquidation()`: Send transaction
  - Builds TX via liquidator contract
  - Signs with private key
  - Waits for receipt with timeout
  - Returns: tx_hash or None

- `estimate_tx_gas()`: Estimate gas requirement

**Oracle Integration**:
- Aave PriceOracle returns prices in 8 decimals
- All calculations use wei arithmetic for precision

**Uniswap Integration**:
- Uses V3 Quoter for accurate swap output
- Fallback: 0.5% slippage estimate if quoter fails

---

### database.py (200 lines)

**Purpose**: Track all liquidations and performance metrics.

**Key Classes**:
- `LiquidationDatabase`: SQLite wrapper

**Schema**:
```sql
-- Core liquidation tracking
liquidations
  ├─ id, timestamp
  ├─ user, collateral_asset, debt_asset, debt_amount
  ├─ tx_hash (unique)
  ├─ status (pending/success/failed)
  ├─ estimated_profit, actual_profit, gas_cost
  └─ error_msg

-- Scanned opportunities (for analysis)
opportunities
  ├─ user, collateral_usd, debt_usd, health_factor
  └─ liquidatable (0/1)

-- Health checks (5-min intervals)
health_checks
  ├─ timestamp
  ├─ uptime_seconds, borrowers_scanned, liquidatable_found
  ├─ liquidations_executed, total_profit, balance_usdc
  └─ error_count

-- Performance metrics
scan_metrics
  ├─ scan_duration_ms, borrowers_checked
  └─ liquidatable_found, batch_size
```

**Key Methods**:
- `log_liquidation()`: Record attempt
- `update_liquidation_result()`: Record outcome
- `get_liquidation_stats()`: Aggregate metrics
- `get_24h_stats()`: Last 24h performance

---

### healthcheck.py (150 lines)

**Purpose**: Health monitoring, systemd integration, notifications.

**Key Classes**:
- `HealthStatus`: Tracks bot state
  - Uptime, scans, liquidations, profit
  - Writes JSON every health check interval

- `TelegramNotifier`: Optional notifications
  - Startup, liquidation, error, shutdown messages

**Key Functions**:
- `notify_ready()`: Systemd READY=1 signal
- `notify_watchdog()`: Systemd WATCHDOG=1 signal (120s timeout)

**Systemd Integration**:
- Type=notify required
- WatchdogSec=120 (must ping every 60s or systemd kills process)
- Automatic restart on failure

---

### run_liquidation_bot.py (200 lines)

**Purpose**: Main bot orchestration and loop.

**Key Classes**:
- `LiquidationBot`: Main bot logic
  - Initializes monitor, executor, database, health tracking
  - Manages signal handlers (SIGINT, SIGTERM)
  - Runs main loop + health check loop

**Key Methods**:
- `check_network()`: Verify RPC connectivity
- `scan_and_execute()`: Main scanning logic
  1. Call monitor.scan_for_liquidations()
  2. For each opportunity, estimate profit
  3. If profitable, execute liquidation
  4. Log results to DB
  5. Send Telegram notification

- `health_check_loop()`: Periodic (5-min) status updates
- `main_loop()`: Infinite loop with retry logic

**Error Handling**:
- RPC failures: exponential backoff (5s → 60s)
- Network errors: max 5 consecutive before exit
- TX execution: 300s timeout per transaction

**Graceful Shutdown**:
- SIGTERM/SIGINT triggers self.running = False
- Finalizes health logs
- Cancels background tasks

---

### FlashLiquidator.sol (100 lines)

**Purpose**: Smart contract for atomic flash loan liquidations.

**Key Functions**:

1. `liquidateWithFlashLoan(collateral, debt, user, amount)`
   - Initiates flash loan from Aave pool
   - Passes params to executeOperation callback

2. `executeOperation(asset, amount, premium, initiator, params)` [IFlashLoanSimpleReceiver]
   - Aave pool callback after sending flash loan
   - Steps:
     a. Approve pool for debt tokens
     b. Call pool.liquidationCall() → seize collateral
     c. Approve Uniswap for collateral
     d. Swap collateral → debt asset
     e. Repay flash loan + premium
   - Profit stays in contract

3. `withdraw()` / `withdrawToken(token)`
   - Owner-only profit withdrawal

**Security**:
- `require(msg.sender == address(pool))` - only Aave can call callback
- `require(initiator == address(this))` - prevents reentrancy
- SafeERC20 for all transfers
- Onlyowner for withdrawals

---

## Key Design Decisions

### 1. Async/Await with AsyncWeb3

**Why**:
- Monitor needs to check 100+ users concurrently
- RPC calls are I/O bound (network latency)
- Asyncio allows checking HF for 100 users in time of 1 sequential check

**Trade-offs**:
- More complex code than sync version
- Requires understanding of asyncio patterns
- Better scalability (could check 1000+ users)

### 2. Event-Based Borrower Discovery

**Why**:
- Iterating all Aave users is impossible (millions)
- Filtering Borrow events is efficient (hundreds per block)
- New borrowers appear in real-time

**Trade-offs**:
- Misses historical borrowers not in recent events
- Relies on event indexing accuracy
- Requires block range management

**Solution**: Keep cache of 5000 most recent borrowers, refresh every scan

### 3. Batch Health Checks (100 users per batch)

**Why**:
- Reduces RPC call count from N to N/100
- Avoids rate limiting on free RPC
- Keeps response times reasonable

**Trade-offs**:
- Stale data (checks happen over ~5 seconds)
- But: health factor very stable (usually)

### 4. Profit Estimation Before Execution

**Why**:
- Verify profitability before spending gas
- Accounts for: prices, swap slippage, gas cost, fees
- Skips sub-MIN_PROFIT_USD opportunities

**Trade-offs**:
- Estimates may diverge from actual (prices move, slippage)
- But: Conservative (1% slippage, 30% gas buffer)

### 5. Single Flash Loan Per Liquidation

**Why**:
- Atomic: borrow → liquidate → swap → repay in 1 TX
- Cannot fail midway (all-or-nothing)
- Minimal MEV exposure

**Trade-offs**:
- Cannot liquidate multiple borrowers in 1 TX (but rare anyway)
- Smart contract overhead (~100k gas)

### 6. SQLite for Tracking

**Why**:
- Single-file database (easy to backup/transport)
- No external DB dependency
- Sufficient for tracking (not real-time analytics)
- Matches polymarket-bot pattern

**Trade-offs**:
- Sequential write-lock (but batch writes mitigate)
- No distributed querying (but single bot instance)

---

## Scalability & Bottlenecks

### Current Limits (Single Bot)

| Metric | Current | Limit | Solution |
|--------|---------|-------|----------|
| Borrowers cached | 5,000 | Memory | Increase max_users_cache |
| Health checks/scan | 100 concurrent | RPC rate | Use paid RPC (Alchemy) |
| Scan interval | 12 seconds | Network latency | Decrease if RPC <100ms |
| Transactions/min | 1-5 | Gas prices | Batch on Arbitrum |

### Bottleneck Analysis

1. **RPC Latency** (Dominant)
   - Event scanning: ~2-3s
   - Health checks: ~1-2s
   - Price fetches: ~0.5s
   - Total scan: ~5-6s (leaves 6s for execution)

2. **Smart Contract Gas** (Not limiting)
   - Flash loan: requires ~350-400k gas
   - Arbitrum gas cheap (~$0.05)
   - Could execute 10+ liquidations/min if found

3. **Network Bandwidth** (Not limiting)
   - Single async connection
   - Could handle 1000+ concurrent RPC calls

### To Scale Beyond Single Bot

- Run multiple bot instances (different accounts)
- Use faster RPC (Alchemy, Quicknode)
- Implement event subscription (vs. polling)
- Add mempool monitoring for early detection

---

## Testing Strategy

### Unit Tests (Manual)

```python
# Test price oracle
await executor.get_asset_price(config.usdc)

# Test profit estimation
await executor.estimate_profit(user, collateral, debt, amount)

# Test batch checking
positions = await monitor.check_multiple_users([user1, user2, ...])
```

### Integration Tests

```python
# Scan for liquidations (doesn't execute)
results = await monitor.scan_for_liquidations()

# Estimate profit (uses real prices)
profit, details = await executor.estimate_profit(...)

# Verify database logging
db.log_liquidation(...)
stats = db.get_liquidation_stats()
```

### Live Testing (Before Production)

1. Run on testnet (Arbitrum Goerli)
2. Test with small MIN_PROFIT_USD ($1)
3. Monitor logs for 24 hours
4. Check database for accuracy
5. Deploy to mainnet with monitoring

---

## Monitoring & Observability

### Metrics Tracked

1. **Scan Metrics**
   - Duration (ms), borrowers checked, liquidatable found
   - Logged every scan to DB

2. **Liquidation Metrics**
   - Attempt count, success count, profit distribution
   - Per asset pair (WETH/USDC, etc.)

3. **Health Metrics**
   - Uptime, error count, last scan time
   - Current balance, profit/day

4. **Performance**
   - Average gas cost, success rate
   - Profit per scan, hourly/daily aggregates

### Alerting Strategy

1. **Systemd Watchdog**: Auto-restart if no ping for 120s
2. **Telegram**: Liquidation alerts, error notifications
3. **JSON Health Log**: Human-readable status every 5 min
4. **Database**: Full audit trail of all operations

---

## References

- [Aave V3 Architecture](https://docs.aave.com/developers/architecture/)
- [Flash Loan Internals](https://docs.aave.com/developers/guides/flash-loans)
- [Uniswap V3 Swaps](https://docs.uniswap.org/protocol/concepts/V3-overview/swaps)
- [Arbitrum Performance](https://docs.arbitrum.io/inside-arbitrum-nitro/)
- [AsyncWeb3.py](https://web3py.readthedocs.io/)
