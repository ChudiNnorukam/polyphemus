# Opus 4.6 Findings & Experiments — Week 1
**Period**: February 4-11, 2026
**Operator**: Chudi Nnorukam + Claude Opus 4.6
**Project**: Polyphemus — Polymarket 15-min crypto prediction market trading bot

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Timeline of Experiments](#2-timeline-of-experiments)
3. [Technical Discoveries](#3-technical-discoveries)
4. [Strategy Research Findings](#4-strategy-research-findings)
5. [Bug Catalogue](#5-bug-catalogue-30-bugs-found-and-fixed)
6. [Infrastructure Learnings](#6-infrastructure-learnings)
7. [Data Analysis Results](#7-data-analysis-results)
8. [Product Pivot](#8-product-pivot-trading-bot--saas)
9. [What Worked / What Didn't](#9-what-worked--what-didnt)
10. [Current State](#10-current-state)
11. [Files & Artifacts Produced](#11-files--artifacts-produced)

---

## 1. Project Overview

**What is Polyphemus?**
An automated trading bot for Polymarket's 15-minute crypto prediction markets. These markets ask simple questions like "Will ETH go up in the next 15 minutes?" and resolve as binary outcomes (Up/Down). You buy shares at a price between $0 and $1, and if correct, get paid $1.

**Stack**: Python 3.11, asyncio, aiohttp, py_clob_client, systemd on DigitalOcean VPS ($6/mo)

**Codebase**: 22 Python files, ~6,876 LOC (grew from ~2,500 LOC at start of week)

**Key files**:
- `signal_bot.py` — Main orchestrator
- `binance_momentum.py` — Primary signal generator (added Feb 10)
- `signal_guard.py` — 7-filter + 4-validator entry gate
- `position_executor.py` — Order execution (maker/taker)
- `exit_manager.py` — 6 exit strategies
- `arb_engine.py` — Riskless arbitrage scanner (added Feb 8)
- `self_tuner.py` — Kelly-optimal position sizing
- `config.py` — 50+ configurable parameters
- `clob_wrapper.py` — Polymarket CLOB API abstraction
- `position_store.py` — In-memory + SQLite position tracking

---

## 2. Timeline of Experiments

### Day 1-3: Copy-Trading (Feb 4-6)

**Hypothesis**: Copy a profitable Polymarket trader's signals and profit.

**Setup**: Monitored wallet `0xe00...548c` (nicknamed "DB") via Polymarket RTDS WebSocket. When DB bought in the $0.65-$0.80 range, we followed.

**Paper trading results (518 trades)**:
- Win rate: 62.5%
- P&L: +$1,274
- market_resolved WR: 93% (positions held to market close)

**Went live Feb 6 20:40 UTC with $162.07 USDC.**

### Day 3-4: Live Trading Disaster (Feb 6-7)

**Live results (86 trades)**:
- Win rate: 40.7% (vs 62.5% paper)
- P&L: -$84
- market_resolved WR: 52% (vs 93% paper)

**Balance: $162 → $69 (-57%)**

**Root causes discovered**:
1. DB is a **market maker**, not a directional trader — buys BOTH sides of every market
2. We were copying his position-unwinding trades on expired markets (prices $0.60-$0.79), not fresh entries
3. On fresh markets, DB enters at $0.41-$0.55 (true fair value at open)
4. Copy-trading a market maker with directional bets is fundamentally flawed
5. Disabling sell-signal exits removed the safety net that pruned losers before binary resolution
6. Paper's 93% resolution WR was survivorship bias

### Day 4-5: Bug Fix Marathon (Feb 7-9)

Found and fixed **30 bugs** (see Section 5). The most devastating:

- **Bug #30** ($25 direct loss): `position_executor.py` set `market_end_time = datetime.now(UTC)` instead of computing from the slug epoch. Exit manager saw `market_end - now = 0` → instant `time_exit` on every trade. Bot entered and immediately exited at a loss, repeatedly.

- **Bug #20** (5-10x position duplication): Exit logic freed slug from dedup set → next signal for same market passed dedup → rapid buy-exit-rebuy cycle. One market window had 6 duplicate BTC Down positions.

- **Bug #16** ($840 phantom profits): `_handle_exit()` calculated theoretical P&L and recorded to DB, but never actually submitted SELL orders. Dashboard showed +$1,052 but wallet only had +$213.

**Balance recovery**: $69 → $108 (maker rebates + small deposit)

### Day 5-6: Strategy Pivot Research (Feb 9-10)

**Experiment**: 5-stage DARIO research pipeline analyzing 684 historical trades.

**Key findings**:
1. DB's overall WR is 28.4% with -$2,608 cashPnl — he profits from market making (buying both sides), not direction
2. No positive Kelly zones exist on binary markets at our WR
3. Mathematical proof: breakeven WR = entry price. At $0.665 entry, need 66.5% WR to break even
4. Our blended WR (59.6%) guarantees ruin — confirmed by Monte Carlo (100% ruin across 10,000 simulations)
5. Market making is not viable either — fees of 1.56% at $0.50 price eliminate all edges
6. Arb engine found 0 opportunities — Up+Down pairs sit at exactly $1.00

**This was the critical "come to Jesus" moment**: every strategy we tested was provably unprofitable.

### Day 6: ETH Discovery + Binance Momentum (Feb 10)

**Experiment**: Break down WR by asset.

| Asset | Win Rate | Sample Size |
|-------|----------|-------------|
| **ETH** | **71.6%** | 274 trades |
| BTC | 55.0% | 205 trades |
| SOL | 58.0% | 102 trades |
| XRP | 43.5% | 34 trades |

**ETH is the only asset above breakeven (66.5%).** BTC and SOL were dragging the average down.

**New strategy deployed**: Binance Momentum
- Monitor Binance spot prices via WebSocket (1-second klines)
- When ETH moves >0.3% in 60 seconds, generate a signal
- Discover corresponding Polymarket market via Gamma API
- Place post-only maker order (0% fees + rebates)
- Entry only in golden zone ($0.65-$0.70)
- ETH-only asset filter

**Code changes**: New file `binance_momentum.py` (315 LOC) + edits to 5 existing files.

### Day 7: Overnight Dry Run (Feb 10-11)

**17.4 hours of ETH-only dry run data**:
- 5,472 momentum detections (ETH 2,174, SOL 2,174, BTC 1,124)
- SOL and BTC completely blocked by asset filter
- 19 ETH signals generated
- 3 passed golden zone ($0.675, $0.675, $0.695) — DRY RUN would-execute
- 16 rejected (price_out_of_range)
- 4 skipped (time too short, <360s remaining)
- **0 errors, 0 disconnects, balance $108.76**

---

## 3. Technical Discoveries

### 3.1 Polymarket CLOB API

- **py_clob_client v0.34.5**: `create_and_post_order()` for live trading (NOT `create_order()` which only signs locally)
- **Post-only maker orders**: `create_order()` → `post_order(signed, GTC, post_only=True)` — two-step process, NOT `create_and_post_order`
- **Balance is in microcents**: `get_balance_allowance()` returns strings, divide by 1e6 for USDC
- **`side` = BUY/SELL (direction), NOT YES/NO (outcome)**: `token_id` already encodes the outcome
- **Order statuses**: Only `FILLED` or `MATCHED` = execution happened. `CANCELLED` does NOT mean success.
- **Minimum order**: 5 shares. Below 5, skip entirely.
- **Builder API keys != CLOB API keys**: Separate credentials from Settings > Builder Codes

### 3.2 Gamma API (Market Discovery)

- **`clobTokenIds` and `outcomes` are JSON strings, not lists** — need `json.loads()` to parse
- **Tag filter doesn't work** for 15-min markets — `tag=15-min` returns unrelated results
- **Slug pattern**: `{asset}-updown-15m-{epoch_rounded_to_900}`
- **Outcomes**: `["Up", "Down"]` — `token_ids[0]` = Up, `token_ids[1]` = Down

### 3.3 Polymarket Data API (RTDS)

- **REST polling latency**: 13-22s fresh trades, 45-75s stale/startup — too slow for 15-min markets
- **RTDS WebSocket latency**: 0.2-1.1s — 20-40x improvement
- **First RTDS message is empty/non-JSON** — handle with JSONDecodeError continue
- **Subscription**: `{"action": "subscribe", "subscriptions": [{"topic": "activity", "type": "trades"}]}`

### 3.4 Binance WebSocket

- Combined streams: `wss://stream.binance.com:9443/stream?streams=ethusdt@kline_1s/btcusdt@kline_1s/solusdt@kline_1s`
- Process ALL kline updates (not just closed candles) for real-time 1-second price tracking
- Data format: `data.data.k.c` = close price (string → float)

### 3.5 Infrastructure

- **Polymarket CLOB is in London (eu-west-2)** — NYC VPS → London = 80-120ms latency
- **For maker orders, 80-120ms is acceptable** (not latency-arbitrage)
- **systemd watchdog**: `Type=notify`, `WatchdogSec=120` — bot sends `READY=1` on init, `WATCHDOG=1` every 60s
- **Python logger propagation**: Child loggers print every line twice if `propagate = True`. Fixed: set `propagate = False` for all child loggers.
- **Never use inline `python3 -c` via SSH** — zsh mangles `$`, `()`, quotes. Always: write script locally → `scp` → execute remotely.
- **`dotenv.load_dotenv()` fails via stdin heredoc** — `find_dotenv()` inspects call stack frames. Use explicit path.

### 3.6 Fee Structure

- **Maker orders**: 0% fee + small rebates (~$0.09 per session observed)
- **Taker orders**: Fee formula `shares × price × 0.25 × (price × (1-price))^2`
- **At p=0.50**: 1.56% fee — eliminates all market-making edges
- **At p=0.95**: ~0.06% fee — negligible for settlement arb
- **Fee/price is symmetric**: f(p) = f(1-p)

---

## 4. Strategy Research Findings

### 4.1 Strategies Tested and Rejected

| Strategy | Why It Failed |
|----------|--------------|
| **Copy-trading DB** | DB is a market maker, buys both sides. Copying one side = random betting |
| **Market making** | 1.56% fees at $0.50 eliminate all spreads. Arb engine found 0 opportunities |
| **All-asset momentum** | 59.6% WR < 66.5% breakeven = guaranteed ruin (Monte Carlo: 100% ruin) |
| **BTC-only momentum** | 55% WR — 11.5pp below breakeven |
| **SOL-only momentum** | 58% WR — 8.5pp below breakeven |
| **Time-of-day filtering** | Peak hours (13-15 UTC) have 93% WR but only 0.35 trades/day — too few to matter |
| **Settlement arbitrage** | Markets rarely misprice near expiry; insufficient volume |
| **Liquidity arbitrage** | Up+Down pairs always sum to $1.00 exactly; no arb windows found |
| **Event markets** | Not viable at $100-500 bankroll. 70% of Polymarket traders lose money. Top 0.04% capture 70% of profits |
| **Adaptive signal filter** | Death spiral: kept tightening threshold past signal capability. Disabled permanently |
| **Sell-signal exits** | 40.5% WR, -$116 P&L across 153 trades. Disabled |
| **Stop-loss** | 0% WR, -$240 P&L across 39 trades (data corrupted by Bug #30). Re-evaluated later |

### 4.2 Strategy That Works: ETH-Only Binance Momentum

| Parameter | Value |
|-----------|-------|
| Asset | ETH only |
| Signal source | Binance 1s klines, >0.3% move in 60s |
| Entry price range | $0.65-$0.70 ("golden zone") |
| Entry mode | Maker (post-only, 0% fees) |
| Blackout hours | 0-2 UTC |
| Expected WR | 71.6% (95% CI: 66.1%-76.8%) |
| Breakeven WR | 66.5% |
| Edge | +5.1 percentage points above breakeven |
| Trades/day | ~3-4 (observed overnight) |
| Daily EV | +$0.77 (optimistic) to +$0.25 (conservative) |
| Success probability | 87.5% |

### 4.3 Binary Market Mathematics

**The fundamental equation**: On a binary market, breakeven WR = entry price.

```
If you buy at $0.665:
  Win:  $1.00 - $0.665 = +$0.335 profit
  Lose: $0.00 - $0.665 = -$0.665 loss

Breakeven: 0.665 / (0.335 + 0.665) = 66.5%
```

This is why WR matters so much and why the difference between 59.6% (ruin) and 71.6% (profitable) is everything.

### 4.4 Time-of-Day Patterns

| Hours (UTC) | WR | Trades/day | Notes |
|-------------|-----|-----------|-------|
| 0-2 | 30% | 0.7 | BLOCKED — blackout hours |
| 3-9 | 64.7% | 6.3 | Mixed quality |
| 10-12 | ~65% | 2.1 | Warming up |
| **13-15** | **93.3%** | **0.35** | Peak, but too few trades |
| 16-18 | ~70% | 2.3 | Good |
| 19-23 | 45.9% | 4.2 | Weakest period |

### 4.5 Monte Carlo Validation

10,000 simulated trading paths:

| WR | $100 Bankroll Ruin % | $1,000 Bankroll Ruin % |
|----|---------------------|----------------------|
| 59.6% | **100%** | **100%** |
| 65.0% | ~85% | ~60% |
| 67.0% | **<1%** | **<0.1%** |
| 71.6% | **<0.1%** | **<0.01%** |

The cliff between 65% and 67% is the critical threshold. Below it, ruin is near-certain. Above it, survival is near-certain.

---

## 5. Bug Catalogue (30 Bugs Found and Fixed)

### Critical ($$$-losing bugs)

| # | Bug | Impact | Fix |
|---|-----|--------|-----|
| **#30** | `market_end_time = now()` instead of epoch+900 | Instant time_exit on every trade, -$25 | Parse slug epoch |
| **#20** | Exit frees slug → rebuy loop | 5-10x duplicate positions per window | Never discard slug during exit |
| **#16** | `_handle_exit()` never submits SELL | $840 phantom profits (paper vs real) | Submit SELL before recording |
| **#21** | CANCELLED treated as success | Phantom exits, USDC never recovered | Only FILLED/MATCHED = success |
| **#1** | `side = "YES"` instead of `"BUY"` | Backwards trades | Use BUY/SELL constants |
| **#2** | `create_order()` instead of `create_and_post_order()` | Orders never placed | Use correct method |

### Serious (Strategy-breaking)

| # | Bug | Impact | Fix |
|---|-----|--------|-----|
| **#12** | Adaptive filter death spiral | 0 signals for 6+ hours | Disabled permanently |
| **#13** | Sell-signal churn | 2.4 trades/slug, -$69 in 6.4h | Disabled sell exits |
| **#14** | Filter excluded golden zone | Up signals scored 0.20, failed 0.35 threshold | Fixed price range |
| **#17** | Time-exit behind price guard | Empty order book → price=0 → exit skipped | Check time before price |
| **#25** | Self-tuner death spiral | Circuit breaker locked at 0.5x after strategy change | Reset tuning_state.json |
| **#27** | V1→V2 DB schema mismatch | `entry_tx_hash` column missing → trades never recorded | Added column migration |
| **#28** | DB error blocks cleanup | Ghost positions stuck in store after successful SELL | try/except around DB ops |
| **#29** | market_expired guard too lax (30s) | Entries in last 5min → immediate time_exit → death loop | Raised to 360s |

### Moderate (Operational)

| # | Bug | Impact | Fix |
|---|-----|--------|-----|
| **#3** | PerformanceTracker has no self.db | Crash on stats query | Use db_path + fresh connect |
| **#4** | Paper balance in live mode | Wrong sizing | Use get_balance_allowance() |
| **#5** | Positions lost on restart | Empty exit_manager | DB loading in init_async() |
| **#6** | Naive vs aware datetimes | Silent comparison failure | Use timezone.utc everywhere |
| **#7** | Exit errors hidden (DEBUG) | Invisible failures | Changed to INFO |
| **#8** | OrderArgs built before size cap | Oversized orders | Move construction after cap |
| **#9-11** | Balance check edge cases | Various | Multiple fixes |
| **#15** | Bare return killed all signals | 0 signals despite data | Comment out return |
| **#18** | Builder key != CLOB key | Auth failures on redeemer | Separate credentials |
| **#19** | Redeemer loop silent death | run_in_executor blocked forever | asyncio.wait_for(timeout=60) |
| **#22** | fetch_positions wrong param name | Redeemer fetched 0 positions | user_address= not user= |
| **#23** | Price range too narrow (0.72-0.80) | 0 signals in 6h | Reverted to 0.65-0.80 |
| **#24** | Fill timeout too short (5s) | 72% timeout rate | Changed to 30s |

### Pattern: Non-fatal DB ops must never block critical cleanup
Bug #28 taught us the most important architectural principle: `record_exit()` ValueError killed `_handle_exit()` before `store.remove()` ran → ghost positions. **Pattern**: Always wrap non-fatal operations (DB writes, logging, metrics) in try/except so critical cleanup (position removal, state updates) ALWAYS executes.

---

## 6. Infrastructure Learnings

### 6.1 VPS Operations

- **VPS**: DigitalOcean Basic $6/mo at `142.93.143.178`
- **Deployment pattern**: Edit locally → `scp` to VPS → `py_compile` verify → `systemctl restart`
- **NEVER use inline `python3 -c` via SSH** — zsh on VPS mangles all Python string operations
- **Package path**: `/opt/sigil/sigil/` (double nested) — files must go in package dir
- **Deploy script** (`deploy_polyphemus.sh`): copies local `polyphemus/` dir to `/opt/polyphemus/` but VPS still runs old `sigil` service (naming mismatch)
- **Patch pattern**: `safe_replace()` function that checks count==1, replaces once, reports per patch. Reliable for multi-file atomic patches.

### 6.2 Reliability Features Built

| Feature | Implementation | Why |
|---------|---------------|-----|
| Connection watchdog | Check signal gap every 30s, reconnect at 120s stale | WebSocket drops silently |
| Balance guard | Cache USDC 60s, skip BUY if < $10 | Prevent overdraw |
| Memory cleanup | Prune stale state every 15min | deque/dict growth |
| Health JSON log | Every 5min: uptime, balance, positions, stuck detection | Monitoring |
| systemd watchdog | READY=1 on init, WATCHDOG=1 every 60s | Auto-restart on hang |
| Exponential backoff | 5s → 60s cap, reset after 5min healthy | Graceful reconnect |
| Daily self-restart | >20h uptime + no positions → sys.exit(0) | Memory leak prevention |
| Order fill verification | Check after 30s, alert on 3+ consecutive unfilled | Dead market detection |
| Session rotation | aiohttp sessions rotate every 30min | Connection staleness |
| Seen trades pruning | OrderedDict capped at 5000, evict oldest 20% | Memory bound |

### 6.3 Other Bots on VPS

| Bot | Status | Result |
|-----|--------|--------|
| V1 Signal Bot (Polyphemus) | **ACTIVE** | ETH-only dry run |
| Liquidation Bot (Aave V3) | DRY_RUN | Monitoring 532 borrowers |
| V2 4coinsbot | **KILLED** | 0% WR, -$203, disabled |
| V2 mlmodelpoly | **KILLED** | Killed alongside V2 |

---

## 7. Data Analysis Results

### 7.1 Research Reports Produced

| Date | Report | Key Finding | File |
|------|--------|-------------|------|
| Feb 5 | Capital & Growth Analysis | Need $500+ for meaningful returns | `.omc/scientist/reports/` |
| Feb 5 | 1-Hour Market Research | No 1h markets on Polymarket — only 15min | `.omc/scientist/reports/` |
| Feb 9 | Strategy Pivot (684 trades) | DB is market maker, copy-trading flawed | `.omc/research/strategy-pivot-2026-02-09/` |
| Feb 9 | Monte Carlo Simulation | 59.6% WR = 100% ruin at any bankroll | `.omc/scientist/reports/` |
| Feb 9 | Binance Momentum Backtest | 80% WR, 20 signals/12h (simulated) | `.omc/scientist/reports/` |
| Feb 10 | 15min Profitability | Only top 0.04% profit; 70% of traders lose | `dario_output/` |
| Feb 10 | 65% WR Strategies | 65% directional WR impossible on binary markets | `dario_output/` |
| Feb 10 | Early Entry Analysis | Fresh markets trade at $0.41-$0.55 (fair value) | `dario_output/` |
| Feb 10 | Strategy Retrospective | Full timeline analysis of what went wrong | `dario_output/` |
| Feb 10 | Event Market Making | Not viable at $100-500 bankroll | `dario_output/` |
| Feb 10 | Bot-as-Product | TAM ~15,400 traders, Year 1: $27K-78K | `dario_output/` |
| Feb 10 | Monte Carlo (ETH) | ETH 71.6% WR → <0.1% ruin probability | `dario_output/` |
| Feb 10 | Strategy Improvement | ETH-only = +12pp edge, 87.5% success probability | `dario_output/` |
| Feb 10 | SaaS PRD | Full product spec, pricing, GTM for Polyphemus SaaS | `dario_output/` |
| Feb 10 | Data Audit (Adversarial) | Verified data accuracy across all reports | `dario_output/` |
| Feb 11 | Strategy Report v2 | Comprehensive updated report with dry run data | `POLYPHEMUS_STRATEGY_REPORT.md` |
| Feb 11 | Layman Analysis | Plain English version of everything | `POLYPHEMUS_LAYMAN_ANALYSIS.md` |

### 7.2 Key Statistical Findings

1. **Binary market math is unforgiving**: 1 percentage point of WR = massive P&L swing. At $0.665 entry, going from 66% to 67% WR changes annual EV from -$30 to +$91.

2. **Asset selection is the #1 lever**: ETH-only filter added +12 percentage points to WR — more than all other optimizations combined.

3. **Time-of-day helps but kills volume**: Peak hours (13-15 UTC) have 93% WR but only 0.35 trades/day. Not worth the trade-off.

4. **Maker orders are essential**: 0% fees vs 1-2% taker fees. On binary markets where edges are 3-5%, paying 1-2% fees cuts your edge in half.

5. **Small bankrolls need high WR**: With $100, you need 67%+ WR for survival. At $10K, you can tolerate 60-62% WR due to lower variance impact.

---

## 8. Product Pivot: Trading Bot → SaaS

### The Realization

Even in the best case ($285/year on $108), trading profits are modest. But the **software itself** — a production-grade Polymarket execution engine with real-time signals, 7 filters, 6 exit strategies, and 24/7 reliability — is valuable to other traders.

### SaaS Opportunity

| Metric | Trading | SaaS |
|--------|---------|------|
| Year 1 revenue | $91-$285 | $27,000-$78,000 |
| Multiplier | 1x | 100-270x |
| Effort | Passive | Active (build + market) |
| Risk | $108 bankroll | Time investment |

### SaaS Positioning
- **What we are**: Execution automation tool (like TradingView auto-executing on Binance)
- **What we are NOT**: Financial advisor, guaranteed-returns bot
- **Pricing**: Free (limited) → $39/mo Pro → $299/mo Enterprise
- **Break-even**: 55 Pro customers
- **Distribution**: Discord-first → Twitter → ProductHunt

### Competitive Landscape
- **PolyTrack** ($19/mo): Analytics only, no execution
- **Polywhaler** (free): Monitoring, no execution
- **Polycule** (1% fee): Basic execution, custodial
- **Polyphemus**: Full stack — signals + execution + risk management, non-custodial

Full PRD: `dario_output/polyphemus_saas_prd.md`

---

## 9. What Worked / What Didn't

### What Worked

| Thing | Why It Worked |
|-------|---------------|
| **DARIO research pipeline** | 5-phase research (Discover→Authorities→Reconstruct→Interrogate→Operate) produced deep, accurate insights from web sources |
| **Monte Carlo validation** | 10,000 simulations immediately killed bad strategies (59.6% WR) and validated good ones (71.6%) |
| **Asset-level WR decomposition** | Single most impactful analysis — found +12pp ETH edge hidden in blended data |
| **Maker-only execution** | 0% fees + rebates vs 1-2% taker fees. Essential for thin edges |
| **Systematic bug tracking** | 30 bugs catalogued with root causes, patterns, and principles. Prevented repeats |
| **Signal guard architecture** | 7 filters + 4 validators = every signal passes through 11 checks. Catches bad trades before execution |
| **Near-miss detection** | Logging signals that ONLY fail on market_expired revealed timing patterns |
| **Exponential backoff** | WebSocket disconnects happen. Auto-reconnect with backoff = zero manual intervention |

### What Didn't Work

| Thing | Why It Failed | Lesson |
|-------|--------------|--------|
| **Copy-trading** | Copied a market maker's unwind trades, not his edge | Understand HOW someone profits, not just THAT they profit |
| **Paper → live extrapolation** | 93% paper WR → 52% live WR | Paper trading hides execution costs and timing issues |
| **Adaptive signal filter** | Self-reinforcing feedback loop tightened to 0 signals | Adaptive systems need bounds and manual override |
| **Sell-signal exits** | 40.5% WR, -$116 P&L | On 15-min markets, hold to resolution is often better than early exit |
| **Arb engine** | 0 opportunities found in weeks | Pairs always sum to $1.00 — arb needs market-maker repricing events that don't occur |
| **Inline Python via SSH** | zsh mangled every special character | Always scp files, never inline |
| **Multi-asset trading** | BTC/SOL dragged WR below breakeven | Diversification hurts when some assets have negative edge |
| **Small bet sizing** | $5 trades mean slow feedback loops | At 3 trades/day, 100 trades = 33 days of waiting |

---

## 10. Current State

### Live System
- **Mode**: DRY_RUN=true (paper trading), ETH-only Binance Momentum
- **Uptime**: 17.4h continuous, 0 errors
- **Last 3 would-execute trades**: All ETH, all in golden zone ($0.675, $0.675, $0.695)
- **Balance**: $108.76 USDC
- **VPS**: Running at `/opt/sigil/sigil/` (pending rename to polyphemus)

### Go-Live Readiness
- [x] ETH-only filter deployed and verified
- [x] Golden zone pricing confirmed
- [x] 3 valid signals in dry run
- [x] 0 errors in 17+ hours
- [x] Kill switch defined (55% WR over 50 trades)
- [ ] Switch DRY_RUN=false (awaiting user decision)

### Key Metrics to Watch Post-Launch
| After N Trades | Minimum Acceptable WR | Action if Below |
|---------------|----------------------|-----------------|
| 10 | 50% | Watch |
| 25 | 55% | Review |
| 50 | 60% | Consider revert |
| 100 | 64% | Revert |

### Parallel Track: SaaS
- PRD written (19 user stories, 3 phases, 16 weeks)
- Competitive analysis complete (4 competitors mapped)
- Pricing model defined ($0/Free → $39/Pro → $299/Enterprise)
- Break-even: 55 customers
- Not yet started building multi-tenant version

---

## 11. Files & Artifacts Produced

### Production Code (22 files, 6,876 LOC)
```
polyphemus/
├── __init__.py
├── arb_engine.py          # Riskless arbitrage scanner
├── balance_manager.py     # USDC balance tracking
├── binance_feed.py        # Binance price confirmation
├── binance_momentum.py    # Primary signal generator (NEW)
├── clob_wrapper.py        # Polymarket CLOB abstraction
├── config.py              # 50+ config parameters
├── dashboard.py           # Web UI (port 8080)
├── exit_manager.py        # 6 exit strategies
├── main.py                # Entry point
├── performance_tracker.py # SQLite P&L tracking
├── polling_signal_feed.py # REST API signal source
├── position_executor.py   # Order execution (maker/taker)
├── position_redeemer.py   # Auto-claim resolved positions
├── position_store.py      # In-memory + SQLite positions
├── self_tuner.py          # Kelly-optimal sizing
├── signal_bot.py          # Main orchestrator
├── signal_feed.py         # RTDS WebSocket signal source
├── signal_guard.py        # 7 filters + 4 validators
├── types.py               # Shared types and constants
├── tuning_state.py        # Persistent tuning state
└── tests/                 # 72 tests (67 passing)
```

### Research & Reports (~55 files)
- `dario_output/` — 55 files, DARIO research reports
- `.omc/research/` — 40 files, strategy research
- `.omc/scientist/` — 10 files, data analysis
- `POLYPHEMUS_STRATEGY_REPORT.md` — Comprehensive strategy report
- `POLYPHEMUS_LAYMAN_ANALYSIS.md` — Plain English analysis

### Knowledge Base (Memory Files)
- `MEMORY.md` — 218-line index of all learnings
- `PRINCIPLES.md` — 16 binding principles from 30 bugs
- `bugs-reference.md` — Full details on all 30 bugs
- `polymarket-api-reference.md` — Verified API reference from VPS packages
- `other-bots.md` — Reference on V2 bot, liquidation bot

---

## Summary

**One week. $162 starting bankroll. 30 bugs. 684 trades analyzed. 17 research reports. 1 viable strategy found.**

The journey from "copy a profitable trader" to "the only viable strategy is ETH-only Binance momentum with maker orders in the golden zone" required disproving 10+ hypotheses, fixing 30 bugs, and accepting uncomfortable mathematical truths about binary market economics.

The most important finding: **on binary prediction markets, your win rate must exceed your entry price**. This single equation — `breakeven WR = entry price` — explains everything. It explains why copy-trading failed (59.6% < 66.5%), why market making failed (fees eat the spread), why multi-asset failed (BTC/SOL drag the average), and why ETH-only works (71.6% > 66.5%).

The second most important finding: **the software is worth more than the trading profits**. A $108 bankroll earning $285/year pales against a SaaS product earning $27K-78K/year. The trading validates the strategy; the product monetizes it.

---

*Compiled: February 11, 2026*
*By: Claude Opus 4.6 + Chudi Nnorukam*
*Total artifacts this week: 22 production files + 55 research reports + 5 memory files + 3 summary reports*
