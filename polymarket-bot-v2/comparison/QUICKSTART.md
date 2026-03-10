# Quick Start Guide: Polymarket Bot Comparison

## 30-Second Setup

```bash
cd /Users/chudinnorukam/Projects/business/polymarket-bot-v2/comparison
python3 compare_bots.py
```

Done. Report prints to screen and saves to `COMPARISON_REPORT.txt`.

## Common Commands

### Last 24 hours (default)
```bash
python3 compare_bots.py
```

### Last 7 days
```bash
python3 compare_bots.py --hours 168
```

### Last 48 hours
```bash
python3 compare_bots.py --hours 48
```

### View saved report
```bash
cat /opt/polymarket-bot-v2/comparison/COMPARISON_REPORT.txt
```

### Run tests
```bash
python3 test_compare.py
```

## Understanding the Report

### Section 1: V1 Bot Metrics
```
--- V1: Signal Following Bot ---
  Trades: 45 | Win Rate: 62.2% | P&L: $219.00 | ROI: 49.1%
  Avg Win: $13.14 | Avg Loss: $-8.76 | Profit Factor: 2.47
  Max Drawdown: $63.00 | Max Consecutive Losses: 7
  Sharpe Ratio: 1.23
  Per Coin: BTC(12t, 75% WR) ETH(18t, 61% WR) SOL(15t, 53% WR)
```

**What it means:**
- **Trades: 45** → 45 completed trades in the window
- **Win Rate: 62.2%** → 62% of trades were profitable
- **P&L: $219** → Total profit of $219
- **ROI: 49.1%** → 49% return on $446 capital
- **Profit Factor: 2.47** → Earns $2.47 for every $1 lost
- **Per Coin** → Performance breakdown by asset

### Section 2: V2 Bot Metrics
Same format as V1. Compare the numbers.

### Section 3: Head-to-Head
```
--- Head-to-Head ---
  Overlapping Markets: 18 (22.0% of total trades)
  PnL Correlation: 0.35
  Diversification Benefit: 12.4%
```

**What it means:**
- **Overlapping Markets: 18** → Both bots traded the same 18 markets
- **PnL Correlation: 0.35** → Moderately correlated (0.35 = 35% move together)
- **Diversification Benefit: 12.4%** → Portfolio is 12.4% less volatile when combined

### Section 4: Recommendation
```
--- Recommendation ---
  V1: 58% / V2: 42%
  Confidence: HIGH
```

**What it means:**
- Allocate 58% capital to V1, 42% to V2
- HIGH confidence = data is reliable (>50 trades per bot)

## Decision Tree

### "Which bot should I use?"
**Check confidence level first:**

- **HIGH**: Use the recommendation (e.g., "V1: 65% / V2: 35%")
- **MEDIUM**: Consider both but trust V1 more if P&L gap is large
- **LOW**: Need more data (wait for more trades or use `--hours 168`)

### "What does the score mean?"
Each bot gets a score (0-100) based on:
- Win rate (how often it makes money)
- Profit factor (wins vs losses ratio)
- ROI (overall profitability)
- Sharpe ratio (consistent returns)

**Higher score = better bot** (for this time window)

### "Should I use both bots?"
**Check PnL correlation:**

- **> 0.7**: Don't combine (same signals, redundant)
- **0.3-0.7**: Good (complementary, diversification works)
- **< 0.3**: Excellent (different strategies, strong diversification)

## Real Examples

### Example 1: V1 Clearly Better
```
V1: Trades: 50 | Win Rate: 65% | P&L: $250
V2: Trades: 45 | Win Rate: 55% | P&L: $120

Recommendation: V1: 72% / V2: 28%
Confidence: HIGH
```
→ V1 is winning more often and making more money. Use mostly V1.

### Example 2: Similar Performance
```
V1: Trades: 48 | Win Rate: 60% | P&L: $180
V2: Trades: 46 | Win Rate: 59% | P&L: $175

Recommendation: V1: 51% / V2: 49%
Confidence: MEDIUM
```
→ Both bots are similar. Could use either. Low correlation means good diversification.

### Example 3: Insufficient Data
```
V1: Trades: 8
V2: Trades: 12
Total: 20 trades

Recommendation: INSUFFICIENT DATA
Confidence: LOW
```
→ Need more data. Wait longer or use `--hours 168` for weekly comparison.

## Metric Interpretation Cheat Sheet

| Metric | Excellent | Good | Acceptable | Concerning |
|--------|-----------|------|-----------|-----------|
| Win Rate | >65% | 55-65% | 50-55% | <50% |
| Profit Factor | >2.5 | 1.5-2.5 | 1.0-1.5 | <1.0 |
| ROI | >50% | 20-50% | 5-20% | <5% |
| Sharpe | >1.5 | 1.0-1.5 | 0.5-1.0 | <0.5 |
| Max Drawdown | <$30 | $30-60 | $60-100 | >$100 |

## Troubleshooting

### "INSUFFICIENT DATA"
**Problem**: Not enough trades to compare

**Fix**:
```bash
python3 compare_bots.py --hours 168  # Try 1 week instead of 24 hours
```

### "No trades extracted"
**Problem**: Can't find or read V1 or V2 data

**Check**:
```bash
# V1 database exists?
ls -la /opt/polymarket-bot/data/performance.db

# V2 logs exist?
ls -la /opt/polymarket-bot-v2/4coinsbot/logs/trades.jsonl

# V1 has trades?
sqlite3 /opt/polymarket-bot/data/performance.db "SELECT COUNT(*) FROM trades;"
```

### "All metrics are 0"
**Problem**: Bots haven't run yet or no completed trades

**Fix**: Wait for bots to trade more, then try again

## Next Steps

### Daily Monitoring
Set up a daily cron job:
```bash
# Add to crontab (crontab -e)
0 8 * * * cd /Users/chudinnorukam/Projects/business/polymarket-bot-v2/comparison && python3 compare_bots.py >> /tmp/comparison.log 2>&1
```

### Weekly Reporting
```bash
# Compare full week every Sunday
0 10 * * 0 cd /Users/chudinnorukam/Projects/business/polymarket-bot-v2/comparison && python3 compare_bots.py --hours 168 > /tmp/weekly.txt
```

### Production Deployment
See `DEPLOYMENT.md` for:
- VPS setup
- Systemd integration
- Alert configuration
- Dashboard integration

## Advanced: Custom Analysis

### Run tests
```bash
python3 test_compare.py
```

### View detailed metrics (in Python)
```python
from compare_bots import V1Extractor, BotMetrics

extractor = V1Extractor("/opt/polymarket-bot/data/performance.db")
trades = extractor.extract_trades(hours=24)
metrics = BotMetrics(trades)

print(f"Win Rate: {metrics.win_rate():.1f}%")
print(f"Sharpe: {metrics.sharpe_ratio():.2f}")
print(f"Per-coin stats: {metrics.per_coin_stats()}")
```

## Help & Support

### Full documentation
```bash
cat README.md
```

### Deployment guide
```bash
cat DEPLOYMENT.md
```

### Test coverage
```bash
python3 test_compare.py
```

---

**That's it!** Run `python3 compare_bots.py` and you'll have a bot comparison report in seconds.
