# Example Outputs: Polymarket Bot Comparison

This document shows realistic example outputs from the comparison framework.

## Example 1: V1 Outperforming (Strong Confidence)

### Command
```bash
python3 compare_bots.py --hours 24
```

### Output
```
==================================================================
  POLYMARKET BOT COMPARISON REPORT
  Window: 24 hours
  Generated: 2026-02-05 14:30:45 UTC
==================================================================

--- V1: Signal Following Bot ---
  Trades: 52 | Win Rate: 64.5% | P&L: $287.45 | ROI: 64.5%
  Avg Win: $12.30 | Avg Loss: $-6.80 | Profit Factor: 3.12
  Max Drawdown: $54.20 | Max Consecutive Losses: 4
  Sharpe Ratio: 1.87
  Per Coin: BTC(16t, 81% WR, $142) | ETH(18t, 67% WR, $98) | SOL(12t, 42% WR, $32) | XRP(6t, 67% WR, $15)

--- V2: Late-Entry 4coinsbot ---
  Trades: 38 | Win Rate: 57.8% | P&L: $145.30 | ROI: 32.6%
  Avg Win: $8.50 | Avg Loss: $-5.20 | Profit Factor: 2.15
  Max Drawdown: $42.10 | Max Consecutive Losses: 3
  Sharpe Ratio: 1.42
  Per Coin: BTC(14t, 79% WR, $95) | ETH(12t, 58% WR, $38) | SOL(8t, 38% WR, $8) | XRP(4t, 50% WR, $4)

--- Head-to-Head ---
  Overlapping Markets: 21 (32.3% of total trades)
  PnL Correlation: 0.52
  Diversification Benefit: 8.7%

--- Recommendation ---
  V1: 66% / V2: 34%
  Confidence: HIGH

==================================================================
```

### Analysis
- **V1 is clearly superior**: 64.5% vs 57.8% win rate, higher P&L, higher Sharpe
- **Reason**: V1's signal quality is better (0.65+ MIN_ENTRY_PRICE threshold works well)
- **Diversification still helps**: 8.7% volatility reduction despite high correlation
- **Action**: Allocate 2/3 to V1, 1/3 to V2 for diversification

---

## Example 2: Balanced Performance (Medium Confidence)

### Command
```bash
python3 compare_bots.py --hours 48
```

### Output
```
==================================================================
  POLYMARKET BOT COMPARISON REPORT
  Window: 48 hours
  Generated: 2026-02-05 16:15:30 UTC
==================================================================

--- V1: Signal Following Bot ---
  Trades: 78 | Win Rate: 59.0% | P&L: $312.50 | ROI: 70.1%
  Avg Win: $10.20 | Avg Loss: $-7.15 | Profit Factor: 2.31
  Max Drawdown: $87.30 | Max Consecutive Losses: 6
  Sharpe Ratio: 1.34
  Per Coin: BTC(24t, 75% WR, $165) | ETH(26t, 62% WR, $102) | SOL(18t, 44% WR, $28) | XRP(10t, 60% WR, $17)

--- V2: Late-Entry 4coinsbot ---
  Trades: 64 | Win Rate: 58.1% | P&L: $268.75 | ROI: 60.3%
  Avg Win: $9.80 | Avg Loss: $-6.90 | Profit Factor: 2.28
  Max Drawdown: $71.50 | Max Consecutive Losses: 5
  Sharpe Ratio: 1.29
  Per Coin: BTC(22t, 73% WR, $142) | ETH(20t, 60% WR, $86) | SOL(14t, 43% WR, $22) | XRP(8t, 63% WR, $18)

--- Head-to-Head ---
  Overlapping Markets: 34 (28.8% of total trades)
  PnL Correlation: 0.58
  Diversification Benefit: 7.2%

--- Recommendation ---
  V1: 51% / V2: 49%
  Confidence: MEDIUM

==================================================================
```

### Analysis
- **Performance is nearly identical**: 59.0% vs 58.1% win rate, similar P&L
- **Different coins drive performance**: BTC strongest (74% WR), SOL weakest (43% WR)
- **Confidence is MEDIUM**: High correlation (0.58) and similar scores make this 50/50
- **Action**: Both bots are equally viable. Use 50/50 split or choose one for simplicity

---

## Example 3: Insufficient Data (Low Confidence)

### Command
```bash
python3 compare_bots.py --hours 6
```

### Output
```
==================================================================
  POLYMARKET BOT COMPARISON REPORT
  Window: 6 hours
  Generated: 2026-02-05 18:45:12 UTC
==================================================================

--- V1: Signal Following Bot ---
  Trades: 12 | Win Rate: 66.7% | P&L: $48.30 | ROI: 10.8%
  Avg Win: $8.40 | Avg Loss: $-5.20 | Profit Factor: 2.20
  Max Drawdown: $12.50 | Max Consecutive Losses: 1
  Sharpe Ratio: 2.14
  Per Coin: BTC(4t, 75% WR, $24) | ETH(5t, 80% WR, $18) | SOL(3t, 33% WR, $6)

--- V2: Late-Entry 4coinsbot ---
  Trades: 8 | Win Rate: 62.5% | P&L: $32.40 | ROI: 7.3%
  Avg Win: $7.80 | Avg Loss: $-4.50 | Profit Factor: 2.10
  Max Drawdown: $8.20 | Max Consecutive Losses: 1
  Sharpe Ratio: 1.95
  Per Coin: BTC(3t, 67% WR, $16) | ETH(3t, 67% WR, $12) | SOL(2t, 50% WR, $4)

--- Head-to-Head ---
  Overlapping Markets: 2 (11.1% of total trades)
  PnL Correlation: 0.28
  Diversification Benefit: 18.2%

--- Recommendation ---
  INSUFFICIENT DATA
  Confidence: LOW

==================================================================
```

### Analysis
- **Sample is too small**: Only 20 total trades (need ≥30)
- **Metrics are unreliable**: High Sharpe ratios (2.14, 1.95) are suspicious with so few trades
- **Recommendation cannot be made**: Statistics aren't statistically significant
- **Action**: Re-run with `--hours 168` (1 week) for reliable comparison

---

## Example 4: V2 Outperforming (Weekly Comparison)

### Command
```bash
python3 compare_bots.py --hours 168
```

### Output
```
==================================================================
  POLYMARKET BOT COMPARISON REPORT
  Window: 168 hours (1 week)
  Generated: 2026-02-05 20:00:00 UTC
==================================================================

--- V1: Signal Following Bot ---
  Trades: 287 | Win Rate: 57.8% | P&L: $892.50 | ROI: 200.1%
  Avg Win: $9.80 | Avg Loss: $-6.40 | Profit Factor: 2.24
  Max Drawdown: $156.20 | Max Consecutive Losses: 8
  Sharpe Ratio: 1.17
  Per Coin: BTC(89t, 71% WR, $412) | ETH(94t, 58% WR, $268) | SOL(68t, 39% WR, $142) | XRP(36t, 53% WR, $70)

--- V2: Late-Entry 4coinsbot ---
  Trades: 312 | Win Rate: 59.3% | P&L: $945.80 | ROI: 212.1%
  Avg Win: $8.90 | Avg Loss: $-5.80 | Profit Factor: 2.36
  Max Drawdown: $128.50 | Max Consecutive Losses: 7
  Sharpe Ratio: 1.24
  Per Coin: BTC(98t, 70% WR, $448) | ETH(102t, 61% WR, $295) | SOL(76t, 41% WR, $142) | XRP(36t, 56% WR, $60)

--- Head-to-Head ---
  Overlapping Markets: 156 (31.0% of total trades)
  PnL Correlation: 0.61
  Diversification Benefit: 6.8%

--- Recommendation ---
  V1: 47% / V2: 53%
  Confidence: HIGH

==================================================================
```

### Analysis
- **V2 is slightly better this week**: 59.3% vs 57.8% win rate, higher profit factor
- **This is different from 24h comparison**: Weekly trends can shift
- **Trade count is large**: 599 total trades = highly reliable statistics
- **Action**: Shift allocation to favor V2 slightly (47/53 split)
- **Note**: Performance differences are small enough to warrant holding both for diversification

---

## Example 5: One Bot Struggling (Risk Alert)

### Command
```bash
python3 compare_bots.py --hours 24
```

### Output
```
==================================================================
  POLYMARKET BOT COMPARISON REPORT
  Window: 24 hours
  Generated: 2026-02-06 02:30:15 UTC
==================================================================

--- V1: Signal Following Bot ---
  Trades: 45 | Win Rate: 58.2% | P&L: $156.80 | ROI: 35.2%
  Avg Win: $11.40 | Avg Loss: $-6.90 | Profit Factor: 2.18
  Max Drawdown: $48.30 | Max Consecutive Losses: 4
  Sharpe Ratio: 1.31
  Per Coin: BTC(14t, 79% WR, $87) | ETH(16t, 63% WR, $52) | SOL(15t, 27% WR, $18)

--- V2: Late-Entry 4coinsbot ---
  Trades: 42 | Win Rate: 45.2% | P&L: $-23.50 | ROI: -5.3%
  Avg Win: $7.60 | Avg Loss: $-8.80 | Profit Factor: 0.86
  Max Drawdown: $102.10 | Max Consecutive Losses: 12
  Sharpe Ratio: 0.28
  Per Coin: BTC(13t, 62% WR, $45) | ETH(14t, 43% WR, $2) | SOL(15t, 27% WR, $-70)

--- Head-to-Head ---
  Overlapping Markets: 19 (23.1% of total trades)
  PnL Correlation: 0.34
  Diversification Benefit: 12.1%

--- Recommendation ---
  V1: 89% / V2: 11%
  Confidence: HIGH

==================================================================
```

### Analysis
- **V2 is underperforming significantly**: 45.2% win rate (below 50%), negative P&L
- **Profit factor < 1.0**: Losing more than winning (0.86)
- **Max consecutive losses: 12**: Very long losing streak (concerning)
- **SOL trades are killing V2**: -$70 on SOL, only 27% WR
- **Action**: Don't use V2 right now (allocation 89/11 means avoid V2 almost entirely)
- **Investigation**: Check if V2 is broken or if market conditions changed

---

## Example 6: High Diversification Benefit (Opposite Signals)

### Command
```bash
python3 compare_bots.py --hours 72
```

### Output
```
==================================================================
  POLYMARKET BOT COMPARISON REPORT
  Window: 72 hours
  Generated: 2026-02-06 06:15:45 UTC
==================================================================

--- V1: Signal Following Bot ---
  Trades: 96 | Win Rate: 60.4% | P&L: $425.20 | ROI: 95.3%
  Avg Win: $11.80 | Avg Loss: $-7.20 | Profit Factor: 2.45
  Max Drawdown: $92.40 | Max Consecutive Losses: 5
  Sharpe Ratio: 1.42
  Per Coin: BTC(28t, 75% WR, $198) | ETH(32t, 63% WR, $142) | SOL(24t, 42% WR, $62) | XRP(12t, 58% WR, $23)

--- V2: Late-Entry 4coinsbot ---
  Trades: 84 | Win Rate: 59.5% | P&L: $356.90 | ROI: 80.0%
  Avg Win: $10.40 | Avg Loss: $-6.80 | Profit Factor: 2.38
  Max Drawdown: $78.20 | Max Consecutive Losses: 4
  Sharpe Ratio: 1.38
  Per Coin: BTC(26t, 77% WR, $168) | ETH(28t, 64% WR, $118) | SOL(22t, 41% WR, $45) | XRP(8t, 63% WR, $25)

--- Head-to-Head ---
  Overlapping Markets: 8 (5.0% of total trades)
  PnL Correlation: -0.08
  Diversification Benefit: 24.6%

===Recommendation ---
  V1: 50% / V2: 50%
  Confidence: MEDIUM

==================================================================
```

### Analysis
- **Very low overlap**: Only 5% of trades are in the same markets
- **Negative correlation**: -0.08 (almost opposite signals)
- **High diversification benefit**: 24.6% reduction in portfolio volatility
- **Action**: Use 50/50 split despite similar performance. Low correlation makes this ideal for risk management
- **Note**: This is the "sweet spot" for portfolio diversification

---

## Interpretation Patterns

### When to Be Confident
```
✓ Total trades > 50 per bot
✓ Win rate differs by >5%
✓ Profit factor differs by >0.3
✓ Confidence is HIGH
→ Clear recommendation is reliable
```

### When to Be Cautious
```
⚠ Total trades < 40
⚠ Win rates within 3% of each other
⚠ Confidence is MEDIUM
⚠ PnL correlation > 0.7
→ Results may change with more data
```

### When to Hold Both
```
✓ Correlation is low (< 0.5)
✓ Diversification benefit > 10%
✓ Both are profitable (win rate > 55%)
→ Portfolio benefits from diversification
```

### When to Use One Bot Only
```
✓ One bot's P&L >> other bot's P&L
✓ One bot's win rate < 50%
✓ Correlation > 0.8
✓ Confidence is HIGH
→ Focus allocation on better bot
```

---

## Performance Patterns Over Time

### V1 Weekly Performance Trend
```
Week 1: V1: 65% WR / V2: 58% WR → Allocate V1 more
Week 2: V1: 62% WR / V2: 57% WR → Consistent, V1 still better
Week 3: V1: 59% WR / V2: 59% WR → Performance converged
Week 4: V1: 56% WR / V2: 61% WR → V2 catching up
```
**Observation**: V1's advantage declining. Monitor for regime change.

### Seasonal Patterns
```
Hours 0-6 UTC:   V1: 58% / V2: 54% (low volume)
Hours 6-12 UTC:  V1: 62% / V2: 59% (London open)
Hours 12-18 UTC: V1: 64% / V2: 61% (peak volume)
Hours 18-24 UTC: V1: 59% / V2: 57% (NY close)
```
**Observation**: Both bots perform better during London/NY trading hours.

---

## Common Metrics by Bot Type

### Signal-Following Bot (V1)
- **Typical win rate**: 55-65%
- **Typical profit factor**: 2.0-3.0
- **Typical Sharpe**: 0.8-1.5
- **Typical ROI/day**: 10-20%

### Late-Entry Bot (V2)
- **Typical win rate**: 55-62% (slightly lower due to paper trading entry friction)
- **Typical profit factor**: 1.8-2.5
- **Typical Sharpe**: 0.7-1.3
- **Typical ROI/day**: 8-15% (slightly lower due to later entry)

**Note**: V2 doesn't have slippage in backtesting, but live trading will reduce these metrics.
