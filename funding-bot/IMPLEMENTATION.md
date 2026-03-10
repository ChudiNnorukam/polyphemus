# Implementation Architecture

Deep dive into the funding rate arbitrage bot design and implementation details.

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     VPS (142.93.143.178)                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │              run_funding_bot.py (Main Process)            │ │
│  │  ┌─────────────────────────────────────────────────────┐  │ │
│  │  │ Main Loop (CHECK_INTERVAL seconds)                  │  │ │
│  │  │  1. _trading_cycle()                                │  │ │
│  │  │     - Scan rates for all pairs                     │  │ │
│  │  │     - Find opportunities (rate > threshold)        │  │ │
│  │  │     - Check existing positions                     │  │ │
│  │  │     - Enter new positions if rate good            │  │ │
│  │  │  2. _log_health_status() every 5 min              │  │ │
│  │  │  3. _send_watchdog_ping() every 60 sec            │  │ │
│  │  └─────────────────────────────────────────────────────┘  │ │
│  └───────────────────────────────────────────────────────────┘ │
│          ↓              ↓              ↓              ↓         │
│   ┌───────────┐  ┌─────────────┐  ┌──────────┐  ┌────────┐   │
│   │ Scanner   │  │  Positions  │  │ Database │  │ Config │   │
│   └───────────┘  └─────────────┘  └──────────┘  └────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                              ↓
            ┌─────────────────────────────────────┐
            │     Bybit API (pybit SDK)           │
            ├─────────────────────────────────────┤
            │ - get_tickers() - funding rates     │
            │ - place_order() - entry/exit        │
            │ - get_order_history() - fills       │
            │ - get_wallet_balance() - balance    │
            └─────────────────────────────────────┘
                              ↓
            ┌─────────────────────────────────────┐
            │   Bybit Unified Trading Account     │
            ├─────────────────────────────────────┤
            │ Spot: USDT, BTC, ETH, SOL, ...     │
            │ Perp: BTCUSDT, ETHUSDT, ...        │
            │ Margin: Shared between spot & perp │
            └─────────────────────────────────────┘
```

## Module Breakdown

### 1. config.py - Configuration Management

**Purpose**: Central configuration from environment variables

**Key Features**:
- All config loaded at startup from `.env`
- Type conversions (string → float, bool, int)
- Validation on import (catches config errors early)
- Easy override per environment

**Design Choices**:
- Single import point for all configuration
- No hardcoded values except defaults
- Fail-fast on invalid config

**Usage**:
```python
from config import MIN_FUNDING_RATE, PAIRS, DRY_RUN
# Values loaded from .env automatically
```

### 2. rate_scanner.py - Funding Rate Monitoring

**Purpose**: Monitor funding rates, detect profitable opportunities

**Key Classes**:
- `FundingRateData`: Immutable dataclass for rate snapshot
- `FundingRateScanner`: Main scanner class

**Rate Calculation**:
```
APY = funding_rate * (24 / 8) * 365
    = funding_rate * 1095
```
Example: 0.0002 per 8h = 0.219 APY (21.9%)

**Opportunity Criteria**:
```python
is_opportunity = (
    current_rate >= MIN_FUNDING_RATE and
    next_rate >= MIN_FUNDING_RATE and
    consecutive_positive >= RATE_STABILITY_PERIODS and
    avg_7d_rate > 0
)
```

**History Tracking**:
- Stores (timestamp, rate) tuples
- Auto-prunes to 7 days (2520 points at 5-min intervals)
- Calculates rolling consecutive positive count

**Caching**:
- Rates cached for `cache_ttl=30` seconds
- Reduces API calls (60 checks/hr → 2 API calls/hr)
- Trade-off: 30s stale data acceptable for this strategy

**Dry Run Mode**:
- No API session created if `dry_run=True`
- Returns mock data (0.02% rate, 5 consecutive positive)
- Allows testing without API credentials

### 3. position_manager.py - Position Lifecycle

**Purpose**: Enter, track, and exit delta-neutral positions

**Position State Machine**:
```
PENDING → OPEN → CLOSING → CLOSED
         (entry)  (exit)
```

**Entry Flow** (`enter_position`):
1. Calculate quantities: `qty = size_usdt / current_price`
2. Place spot BUY limit order
3. Place perp SELL limit order (1x leverage)
4. Wait for both fills (timeout: 60s)
5. Record to database and memory
6. Return Position object

**Key Implementation Details**:

Spot BUY + Perp SELL = Delta Neutral:
```
Spot side: Buy at price P
Perp side: Sell at price P
Delta = Qty_spot * 1 + Qty_perp * (-1) = 0
```

Leverage Control:
```python
# Always 1x (no leverage)
perp_order = session.place_order(
    ...
    leverage="1"  # Max safe leverage
)
```

**Exit Flow** (`exit_position`):
1. Place spot SELL market order (for speed)
2. Place perp BUY market order (to close short)
3. Wait for both fills (timeout: 30s)
4. Calculate P&L from execution prices
5. Update position status to CLOSED
6. Record exit info to database

**P&L Calculation**:
```python
# Spot P&L (long position)
spot_pnl = qty * (exit_price - entry_price)

# Perp P&L (short position)
perp_pnl = qty * (entry_price - exit_price)

# Total (should be ~0 at exit, + funding collected)
total_pnl = spot_pnl + perp_pnl

# Minus fees (0.1% spot taker + 0.05% perp taker)
net_pnl = total_pnl - fees
```

**Dry Run Mode**:
- Simulates order fills instantly
- Tracks virtual "paper" balance
- No actual API calls

**Delta Check** (`check_delta`):
```python
# Calculate current delta (simplified)
spot_value = qty_spot * current_price
perp_notional = qty_perp * current_price
delta_drift = |spot_value - perp_notional| / spot_value

# Rebalance if > 5%
if delta_drift > REBALANCE_THRESHOLD:
    # Adjust perp qty to match spot
```

### 4. database.py - Position & Rate History

**Purpose**: Persistent storage for analysis and recovery

**Schema Design**:

**positions table**:
```sql
- id: unique position identifier
- symbol: trading pair (BTCUSDT)
- entry_price, spot_qty, perp_qty: position details
- entry_time, exit_time: timestamps
- funding_collected, fees_paid, pnl: financials
- status: OPEN|CLOSED
- exit_reason: why position exited
```

**funding_history table**:
```sql
- position_id: foreign key to positions
- symbol: trading pair
- funding_rate: rate at time of payment
- funding_payment: amount received (not implemented in v1)
- timestamp: when payment was received
```

**bot_status table**:
```sql
- timestamp: snapshot time
- balance, equity: account values
- open_positions: count
- total_pnl, total_funding_collected: cumulative
- uptime_seconds: how long bot has run
```

**funding_rates table**:
```sql
- symbol: trading pair
- current_rate, next_rate, avg_7d_rate: rate data
- apy: calculated APY
- consecutive_positive: stability metric
- timestamp: snapshot time
```

**Key Queries**:

Get stats (for daily reporting):
```python
stats = db.get_stats()
# Returns: {total_positions, open_positions, closed_positions, total_pnl, ...}
```

Calculate total P&L:
```python
total_pnl = db.get_total_pnl()
# SELECT SUM(pnl) FROM positions WHERE status='CLOSED'
```

### 5. run_funding_bot.py - Main Orchestrator

**Purpose**: Main loop, event coordination, health monitoring

**Startup Sequence**:
1. Create scanner, position_manager, database instances
2. Register signal handlers (SIGTERM, SIGINT)
3. Initialize bot state (start_time, peak_balance, etc.)
4. Call `initialize()` to get current balance
5. Enter main loop

**Main Loop** (`main_loop`):
```python
while running:
    await _trading_cycle()

    if time.time() - last_health_check >= HEALTH_CHECK_INTERVAL:
        await _log_health_status()

    if time.time() - last_watchdog >= WATCHDOG_INTERVAL:
        await _send_watchdog_ping()

    await asyncio.sleep(CHECK_INTERVAL)
```

**Trading Cycle** (`_trading_cycle`):
1. Scan funding rates for all PAIRS
2. Find opportunities (rate > threshold + stable)
3. For each opportunity:
   - If no position exists → enter
   - If position exists → check health
4. For each open position:
   - Check if rate flipped negative → exit
   - Check if rate too low → exit
   - Check delta drift → rebalance if needed

**Position Entry Logic**:
```python
async def _enter_position(symbol, current_price, funding_rate):
    balance = await pm.get_balance()
    size_usdt = balance * MAX_POSITION_PCT  # 40% of balance

    if size_usdt < 10:  # Skip if too small
        return False

    position = await pm.enter_position(
        symbol=symbol,
        size_usdt=size_usdt,
        current_price=current_price
    )

    if position:
        db.add_position(...)  # Record entry
        await _send_telegram(...)  # Alert
        return True
    return False
```

**Position Exit Logic**:
```python
async def _exit_position(position, reason):
    pnl = await pm.exit_position(position.symbol, reason)

    if pnl is not None:
        db.update_position_exit(...)  # Record exit
        stats["total_pnl"] += pnl
        await _send_telegram(...)  # Alert
        return True
    return False
```

**Exit Triggers**:
1. `rate_negative`: funding_rate < 0
2. `rate_dropped`: funding_rate < 0.01% (not profitable)
3. `basis_divergence`: spot/perp mispricing >2%
4. `delta_drift`: position hedge drifted >5%
5. `shutdown`: SIGTERM received + CLOSE_ON_SHUTDOWN=true

**Health Status** (`_log_health_status`):
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

Written to:
- `/opt/funding-bot/logs/health.json` (real-time)
- `bot_status` database table (historical)

**Watchdog Integration**:
```python
# Systemd expects WATCHDOG=1 every WatchdogSec/2 (120s/2=60s)
# If bot doesn't ping, systemd kills + restarts it
if "WATCHDOG_USEC" in os.environ:
    os.write(1, b"WATCHDOG=1\n")
```

**Signal Handling**:
```python
def _handle_shutdown(signum, frame):
    logger.info(f"Signal {signum} received")
    self.running = False
    # Main loop exits gracefully
    # shutdown() called in finally block
```

## Key Design Decisions

### 1. Why Delta Neutral?

Avoids directional bet:
- Spot gain = Perp loss when price moves
- Net exposure ≈ 0
- Only profit source is funding

### 2. Why Limit Orders for Entry?

- Lower fees (maker vs taker)
- Avoid slippage
- Trade-off: may not fill immediately

Alternative: Market orders
- Guaranteed fill but ~0.1% slippage
- Better for volatile times

### 3. Why Market Orders for Exit?

- Speed (no wait for fills)
- Guaranteed exit
- Slippage acceptable on exit

### 4. Why 1x Leverage?

- No liquidation risk
- Bybit UTA shares margin
- Interest costs minimal
- Safety paramount

### 5. Why Cache Rates 30 Seconds?

- Rate limit: 600 req/5s = 120 req/s
- Current: 1 req/60s (scanner) + 1 req per position check
- 30s cache → 2 API calls/min = 120/hr (safe margin)
- 30s stale data acceptable (rates move slowly)

### 6. Why Position Limit per Pair?

- Risk control: MAX_POSITION_PCT = 0.4 (40% of balance)
- Example: $500 balance → max $200 per position
- Allows 2-3 simultaneous positions
- Diversification without over-leverage

### 7. Why Consecutive Positive Check?

- Single positive period can be noise
- RATE_STABILITY_PERIODS=3 requires 3+ consecutive
- Better predictor of sustained positive rates
- Avoids flash-dump trades

### 8. Why Exit on Negative Rate?

- No profit if funding is negative
- Actually lose money (pay instead of receive)
- Exit immediately to preserve capital

### 9. Why Systemd Watchdog?

- Detects hung processes
- Auto-restart if stalled
- Important for 24/7 uptime
- Works with type=notify service

## Performance Considerations

### Memory Usage

Position tracking in memory:
```python
self.positions: Dict[str, Position] = {}
# Each Position: ~500 bytes
# 10 positions: ~5 KB
# Rate history: 2520 points/pair * 50 bytes = 126 KB/pair
# 3 pairs: ~378 KB
```

Total: <10 MB typical

### API Call Efficiency

```
Per cycle (60 seconds):
- get_tickers(3 pairs): 3 calls
- get_order_history(filled orders): 2-4 calls
- get_wallet_balance: 1 call
- Total: ~10 calls/minute = 600/hour (acceptable)

With 30s cache:
- get_tickers: 2 calls/min = 120/hour
- Total: ~140/hour (excellent)
```

### Database Queries

```python
# Per position lifecycle:
- INSERT positions: 1
- UPDATE positions: 1
- SELECT bot_status: 0.2/min = 12/hour
- Total: efficient, <100ms per query
```

### CPU Usage

```
Loop timing:
- get_tickers(3): ~100ms (network)
- Position check: ~10ms
- DB write: ~20ms
- Per cycle: ~150ms
- Sleep: 59.85s
- Average CPU: <1%
```

## Error Handling Strategy

### Recoverable Errors

```python
# Network errors → retry with backoff
try:
    rates = await scanner.scan_rates(pairs)
except aiohttp.ClientError:
    logger.warning("API timeout, will retry next cycle")
    continue  # Don't exit, just skip this cycle
```

### Critical Errors

```python
# API credential failure → log and exit
if not BYBIT_API_KEY:
    if not DRY_RUN:
        raise ValueError("Credentials required")
```

### Non-Fatal Errors

```python
# Individual position failure → log, continue
try:
    await pm.enter_position(...)
except Exception as e:
    logger.error(f"Position entry failed: {e}")
    self.stats["errors"] += 1
    continue  # Try next pair
```

## Testing Strategy

### Unit Tests (via TESTING.md)

1. Config validation
2. Module imports
3. Database schema
4. Rate calculations
5. Position entry/exit

### Integration Tests

1. Dry run (paper trading)
2. Testnet with real API
3. Small mainnet position ($10-50)
4. Full mainnet operation

## Future Improvements

1. **Funding rate prediction**: ML model for next rate
2. **Multi-leg positions**: BTC+ETH+SOL simultaneous
3. **Adaptive sizing**: Scale based on volatility
4. **Basis prediction**: Entry timing optimization
5. **Web dashboard**: Real-time monitoring UI
6. **Backtesting engine**: Historical rate analysis
7. **Risk metrics**: Sharpe ratio, max drawdown alerts
8. **Advanced exits**: Trailing take-profit, time-based

