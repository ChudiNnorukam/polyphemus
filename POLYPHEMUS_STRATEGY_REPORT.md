# Polyphemus Strategy Report — Complete Analysis
**Updated**: February 11, 2026 16:30 UTC | **Version**: 2.0

---

## 1. Current Status: ETH-Only Dry Run LIVE

| Metric | Value |
|--------|-------|
| **Mode** | DRY_RUN=true (paper trading) |
| **Strategy** | Binance Momentum, ETH-only filter |
| **Entry mode** | Maker (post-only, 0% fees) |
| **Price range** | $0.65 — $0.70 ("golden zone") |
| **Uptime** | 17.4 hours continuous |
| **Balance** | $108.76 USDC |
| **Errors** | 0 |

---

## 2. Overnight Dry Run Results (Feb 10 23:06 — Feb 11 16:31 UTC)

### 2.1 Signal Pipeline

```
[Binance WS] 5,472 momentum detections
       ↓
[Asset Filter] ETH: 2,174 | SOL: 2,174 (blocked) | BTC: 1,124 (blocked)
       ↓
[Signal Generator] 19 ETH signals + 4 time-expired skips
       ↓
[Signal Guard] 3 PASSED golden zone | 16 rejected (price_out_of_range)
       ↓
[DRY RUN] 3 would-execute trades
```

### 2.2 All 19 ETH Signals Generated

| # | Time (UTC) | Market | Direction | Price | Result |
|---|-----------|--------|-----------|-------|--------|
| 1 | 04:23 | eth-...-1770783300 | DOWN | $0.825 | REJECTED (too high) |
| 2 | **04:32** | eth-...-1770784200 | **DOWN** | **$0.675** | **PASSED — DRY RUN** |
| 3 | 06:08 | eth-...-1770789600 | DOWN | $0.885 | REJECTED (too high) |
| 4 | 06:16 | eth-...-1770790500 | UP | $0.585 | REJECTED (too low) |
| 5 | 08:07 | eth-...-1770796800 | DOWN | $0.860 | REJECTED (too high) |
| 6 | 10:35 | eth-...-1770805800 | DOWN | $0.935 | REJECTED (too high) |
| 7 | 12:07 | eth-...-1770811200 | UP | $0.895 | REJECTED (too high) |
| 8 | 13:30 | eth-...-1770816600 | UP | $0.595 | REJECTED (too low) |
| 9 | 13:51 | eth-...-1770817500 | DOWN | $0.645 | REJECTED (too low) |
| 10 | 14:03 | eth-...-1770818400 | UP | $0.775 | REJECTED (too high) |
| 11 | 14:15 | eth-...-1770819300 | UP | $0.475 | REJECTED (too low) |
| 12 | 14:31 | eth-...-1770820200 | UP | $0.585 | REJECTED (too low) |
| 13 | 14:46 | eth-...-1770821100 | UP | $0.575 | REJECTED (too low) |
| 14 | **15:00** | eth-...-1770822000 | **DOWN** | **$0.675** | **PASSED — DRY RUN** |
| 15 | 15:15 | eth-...-1770822900 | UP | $0.580 | REJECTED (too low) |
| 16 | 15:31 | eth-...-1770823800 | UP | $0.575 | REJECTED (too low) |
| 17 | 15:46 | eth-...-1770824700 | DOWN | $0.645 | REJECTED (too low) |
| 18 | 16:04 | eth-...-1770825600 | UP | $0.745 | REJECTED (too high) |
| 19 | **16:15** | eth-...-1770826500 | **DOWN** | **$0.695** | **PASSED — DRY RUN** |

### 2.3 Key Observations

**Trade frequency**: 3 golden-zone signals in 17.4 hours = **4.1 trades/day** (projected)
- Higher than predicted 1.12/day (volatile day or larger sample effect)
- All 3 passed signals were ETH DOWN direction
- Prices: $0.675, $0.675, $0.695 — all within golden zone

**Rejection breakdown**:
- 8 signals too high (>$0.70): prices $0.745-$0.935
- 8 signals too low (<$0.65): prices $0.475-$0.645
- 3 signals in zone: prices $0.675-$0.695

**Asset filter effectiveness**: SOL (2,174 detections) and BTC (1,124 detections) fully blocked at signal generation level — zero Gamma API calls wasted.

**Time-expired skips**: 4 signals arrived too late (<360s remaining). The 6-minute buffer is working as designed.

**Infrastructure**: 0 errors, 0 disconnects, balance stable at $108.76. Maker rebates added ~$0.09 from previous session.

---

## 3. Strategy Evolution Timeline

### Phase 1: Copy-Trading (Feb 4-7) — FAILED
- Copied DB wallet trades on 15-min crypto markets
- **Paper**: +$1,274, 62.5% WR (518 trades) — looked great
- **Live**: -$84, 40.7% WR (86 trades) — catastrophic
- **Root cause**: DB is a market maker (buys BOTH sides). We were copying position-unwinding on expired markets, not fresh directional bets
- **Balance**: $162 → $69 (-57%)

### Phase 2: Bug Fix Sprint (Feb 7-9) — RECOVERED
- Fixed 12 critical bugs (#16-#30), including:
  - Bug #30: `market_end_time=now` caused instant exits on every trade ($25 lost)
  - Bug #21: CANCELLED orders treated as successful → phantom exits
  - Bug #28: DB recording errors blocked position cleanup → ghost positions
- **Balance**: $69 → $108 (recovery from maker rebates + deposit)

### Phase 3: Strategy Pivot Research (Feb 9-10)
- Analyzed 684 historical trades across 5 research stages
- **Key finding**: No positive Kelly zones exist on binary markets at our WR
- **Mathematical reality**: Breakeven WR = entry price. At $0.665 entry, need 66.5% WR
- **All-asset WR**: 59.6% — below 66.5% breakeven = guaranteed ruin (Monte Carlo: 100% ruin probability)

### Phase 4: ETH-Only Filter (Feb 10-11) — CURRENT
- **Discovery**: ETH has 71.6% WR (n=274) vs BTC 55%, SOL 58%
- **Statistical confidence**: 95% CI [66.1%, 76.8%] — even lower bound near breakeven
- **Deployed**: Asset filter + golden zone ($0.65-$0.70) + blackout hours (0-2 UTC)
- **Dry run status**: 3 would-execute trades in 17.4 hours, all in golden zone

---

## 4. Statistical Foundation

### 4.1 Win Rate Analysis

| Strategy | WR | vs Breakeven | Viable? |
|----------|------|-------------|---------|
| All assets, all prices | 40.7% | -25.8pp | NO |
| All assets, golden zone | 59.6% | -6.9pp | NO |
| **ETH-only, golden zone** | **71.6%** | **+5.1pp** | **YES** |
| BTC-only, golden zone | 55.0% | -11.5pp | NO |
| SOL-only, golden zone | 58.0% | -8.5pp | NO |

### 4.2 Monte Carlo Simulations (10,000 paths)

| Bankroll | WR | Ruin Probability | Max Drawdown | Verdict |
|----------|------|-----------------|-------------|---------|
| $100 | 59.6% | **100%** | -100% | GUARANTEED RUIN |
| $1,000 | 59.6% | **100%** | -100% | GUARANTEED RUIN |
| $100 | 67.0% | **<1%** | -35% | VIABLE |
| $100 | 71.6% | **<0.1%** | -20% | STRONG |

### 4.3 Expected Value (ETH-Only)

```
Entry price:     $0.665 (midpoint of golden zone)
Win payout:      $1.00 - $0.665 = +$0.335 per share
Loss payout:     -$0.665 per share
Win rate:        71.6%

Per-trade EV:    (0.716 × $0.335) + (0.284 × -$0.665) = +$0.051
At $5/trade:     +$0.26 per trade
At 3 trades/day: +$0.77/day = $23/month

Per-trade EV at breakeven (66.5%):
                 (0.665 × $0.335) + (0.335 × -$0.665) = $0.00
```

### 4.4 Confidence & Regression Risk

| Scenario | Probability | WR | Daily EV | Viable? |
|----------|------------|-----|----------|---------|
| Edge holds (71.6%) | 30-40% | 71.6% | +$0.77 | YES |
| Moderate regression | 40-50% | 67.5% | +$0.25 | YES |
| Significant regression | 10-20% | 62.5% | -$0.23 | NO |
| Complete reversion | 5-10% | 59.6% | -$0.77 | NO |

**Overall P(profitable) = 87.5%**

---

## 5. Infrastructure & Reliability

### 5.1 System Architecture
```
Binance WS (1s klines) → BinanceMomentumFeed → Asset Filter (ETH only)
                                                      ↓
                                               Signal Generator → Gamma API (market discovery)
                                                      ↓
                                               Signal Guard (7 filters + 4 validators)
                                                      ↓
                                               Position Executor (maker orders, 0% fees)
                                                      ↓
                                               Exit Manager (time/resolved/profit target)
```

### 5.2 Reliability Features
- systemd watchdog (120s heartbeat)
- Exponential backoff reconnection (5s → 60s cap)
- Balance guard ($10 minimum)
- Daily self-restart (>20h uptime, no positions)
- Session rotation (30min)
- Stale state pruning (15min)

### 5.3 Overnight Health
- **Uptime**: 17.4h continuous (one restart at 23:06 for deployment)
- **WebSocket**: 0 disconnects
- **Errors**: 0
- **Memory**: Stable (stale pruning active)

---

## 6. Go-Live Decision Framework

### Prerequisites (all met)
- [x] ETH-only filter deployed and verified
- [x] Golden zone pricing confirmed ($0.65-$0.70)
- [x] 3 would-execute signals validated in dry run
- [x] 0 errors in 17+ hours
- [x] Asset filter blocking SOL/BTC correctly
- [x] Time-expired guard working (4 late signals blocked)

### Go-Live Command
```bash
ssh root@142.93.143.178 "sed -i 's/DRY_RUN=true/DRY_RUN=false/' /opt/sigil/sigil/.env && systemctl restart sigil"
```

### Monitoring Thresholds (Post Go-Live)

| Checkpoint | Min WR | Action if Below |
|-----------|--------|-----------------|
| After 10 trades | 50% | WATCH closely |
| After 25 trades | 55% | REVIEW strategy |
| After 50 trades | 60% | CONSIDER reverting |
| After 100 trades | 64% | REVERT if below |

**Kill switch**: If WR < 55% in any rolling 50-trade window, revert to DRY_RUN=true immediately.

---

## 7. Financial Projections

### Conservative Scenario (67% WR after regression)
| Period | Trades | Est P&L | Cumulative | Balance |
|--------|--------|---------|------------|---------|
| Week 1 | 21 | +$1.75 | +$1.75 | $110.51 |
| Month 1 | 90 | +$7.50 | +$7.50 | $116.26 |
| Month 3 | 270 | +$22.50 | +$22.50 | $131.26 |
| Month 6 | 540 | +$45.00 | +$45.00 | $153.76 |
| Year 1 | 1,095 | +$91.25 | +$91.25 | $200.01 |

### Optimistic Scenario (71.6% WR holds)
| Period | Trades | Est P&L | Cumulative | Balance |
|--------|--------|---------|------------|---------|
| Week 1 | 21 | +$5.46 | +$5.46 | $114.22 |
| Month 1 | 90 | +$23.40 | +$23.40 | $132.16 |
| Month 3 | 270 | +$70.20 | +$70.20 | $178.96 |
| Month 6 | 540 | +$140.40 | +$140.40 | $249.16 |
| Year 1 | 1,095 | +$284.70 | +$284.70 | $393.46 |

*Assumes $5/trade flat sizing. Compound sizing would accelerate.*

---

## 8. Parallel Opportunity: SaaS Product

### Polyphemus-as-a-Service
While the bot trades with $108, the **software itself** has 270x-780x more revenue potential than the trading profits.

| Path | Year 1 Revenue | Effort |
|------|---------------|--------|
| Trading ($108 bankroll) | $91-$285 | Passive (bot runs itself) |
| SaaS (Pro @ $39/mo) | $27,000-$78,000 | Active (build + market) |

**SaaS positioning**: "Execution automation tool" — user brings strategy, we handle infrastructure. Non-custodial, no return promises.

**Target**: 55 Pro customers = break-even ($2,145 MRR vs ~$2,000 costs)

Full PRD: `dario_output/polyphemus_saas_prd.md`

---

## 9. Risk Register

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| ETH edge regresses to mean | 20% | High | Kill switch at 55% WR, monitor rolling windows |
| Sample size too small (274 trades) | Moderate | Medium | 9 months to 95% confidence; accept uncertainty |
| Polymarket fee changes | Low | High | Maker-only strategy; monitor announcements |
| Binance WS disruption | Low | Medium | Exponential backoff, auto-reconnect |
| Regulatory (CFTC) | Medium | High | SaaS = execution tool, consult attorney |
| VPS outage | Low | Low | systemd auto-restart, health monitoring |

---

## 10. Research Reports Index

| Report | File | Key Finding |
|--------|------|-------------|
| Strategy Pivot Analysis | `.omc/research/strategy-pivot-2026-02-09/report.md` | DB is market maker, copy-trading flawed |
| 65% WR Strategies | `dario_output/dario_65wr_strategy_20260210.md` | 65% directional WR impossible on binary markets |
| Event Market Making | `dario_output/dario_event_market_making_small_bankroll_20260210.md` | Not viable at $100-500 |
| Bot-as-Product | `dario_output/dario_bot_as_product_20260210.md` | TAM ~15,400 traders, $27K-78K Year 1 |
| Monte Carlo Analysis | `dario_output/monte_carlo_eth_strategy_analysis.md` | 59.6% WR = 100% ruin, 67%+ = viable |
| Strategy Improvement | `dario_output/strategy_improvement_67wr_analysis.md` | ETH-only = 71.6% WR, 87.5% success probability |
| SaaS PRD | `dario_output/polyphemus_saas_prd.md` | Full product spec, pricing, GTM |

---

*Report compiled: February 11, 2026 16:35 UTC*
*Data sources: 17.4h dry run logs, 684 historical trades, 10,000 Monte Carlo simulations*
