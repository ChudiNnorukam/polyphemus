# Polymarket Bot V1 vs V2 Comparison Framework

## Overview

This framework provides a comprehensive comparison between the V1 (Signal Following Bot) and V2 (Late-Entry 4coinsbot) Polymarket trading bots. It extracts trade data from both bots, normalizes them, computes detailed performance metrics, and generates allocation recommendations.

## Quick Start

```bash
# Basic usage (last 24 hours)
python3 compare_bots.py

# Compare last 48 hours
python3 compare_bots.py --hours 48

# Compare last week
python3 compare_bots.py --hours 168
```

## Features

### Data Extraction
- **V1**: Reads SQLite database from `/opt/polymarket-bot/data/performance.db`
- **V2**: Reads JSONL trade logs from `/opt/polymarket-bot-v2/4coinsbot/logs/trades.jsonl`
- Automatic coin extraction from market slugs (e.g., `btc-updown-15m-*` → BTC)
- Graceful error handling for missing or corrupted data

### Performance Metrics (Per Bot)
- **Trade Count**: Total number of completed trades
- **Win Rate**: Percentage of profitable trades
- **Total P&L**: Sum of all profit/loss amounts ($)
- **ROI**: Return on investment based on $446 starting capital
- **Average Win/Loss**: Mean profit on winning/losing trades
- **Profit Factor**: Total wins / total losses (>1.5 is good)
- **Max Drawdown**: Largest sequential decline from peak
- **Max Consecutive Losses**: Longest losing streak
- **Sharpe Ratio**: Risk-adjusted returns (annualized from 15-min markets)
- **Per-Coin Breakdown**: Metrics segmented by BTC/ETH/SOL/XRP

### Comparative Analysis
- **Overlapping Markets**: Number and percentage of markets traded by both bots
- **PnL Correlation**: How synchronized their profits/losses are (-1 to +1)
- **Diversification Benefit**: Estimated volatility reduction from 50/50 portfolio split

### Allocation Recommendation
- Composite scoring based on:
  - Win rate (0-30 points, requires ≥30% baseline)
  - Profit factor (0-25 points, capped at >2.0)
  - ROI (0-25 points, 10% = max)
  - Sharpe ratio (0-20 points)
- Confidence levels: LOW/MEDIUM/HIGH
- Sample size validation (requires ≥30 total trades)

## Report Output

The script generates a formatted ASCII report with:
- Header with timestamp and comparison window
- V1 metrics and per-coin breakdown
- V2 metrics and per-coin breakdown
- Head-to-head comparison (overlapping markets, correlation, diversification)
- Allocation recommendation with confidence level
- Footer separator

### Example Report

```
==================================================================
  POLYMARKET BOT COMPARISON REPORT
  Window: 24 hours
  Generated: 2026-02-05 14:30:45 UTC
==================================================================

--- V1: Signal Following Bot ---
  Trades: 45 | Win Rate: 62.2% | P&L: $198.50 | ROI: 44.5%
  Avg Win: $8.42 | Avg Loss: -$3.21 | Profit Factor: 2.84
  Max Drawdown: $45.32 | Max Consecutive Losses: 3
  Sharpe Ratio: 1.23
  Per Coin: BTC(12t, 75% WR, $89) | ETH(18t, 61% WR, $67) | SOL(15t, 53% WR, $42)

--- V2: Late-Entry 4coinsbot ---
  Trades: 38 | Win Rate: 58.5% | P&L: $145.30 | ROI: 32.6%
  Avg Win: $7.85 | Avg Loss: -$2.94 | Profit Factor: 2.41
  Max Drawdown: $32.50 | Max Consecutive Losses: 2
  Sharpe Ratio: 1.08
  Per Coin: BTC(11t, 72% WR, $75) | ETH(14t, 64% WR, $51) | SOL(13t, 46% WR, $19)

--- Head-to-Head ---
  Overlapping Markets: 18 (22.0% of total trades)
  PnL Correlation: 0.35
  Diversification Benefit: 12.4%

--- Recommendation ---
  V1: 58% / V2: 42%
  Confidence: HIGH

==================================================================
```

## Data Source Schemas

### V1: SQLite `trades` Table

```sql
CREATE TABLE trades (
    trade_id TEXT PRIMARY KEY,
    token_id TEXT,
    entry_time INTEGER,           -- Unix timestamp
    entry_price REAL,             -- Market price at entry
    entry_size REAL,              -- Number of shares
    side TEXT,                    -- "BUY" or "SELL"
    entry_amount REAL,            -- USD spent (size * price)
    exit_time INTEGER,            -- Unix timestamp
    exit_price REAL,              -- Market price at exit
    exit_amount REAL,             -- USD received
    exit_reason TEXT,             -- "market_resolved", "profit_target", etc.
    profit_loss REAL,             -- exit_amount - entry_amount
    profit_loss_pct REAL,         -- (profit_loss / entry_amount) * 100
    hold_seconds INTEGER,         -- Time held in seconds
    strategy TEXT,
    slug TEXT,                    -- Market identifier (e.g., "btc-updown-15m-xxx")
    outcome TEXT,                 -- "Up" or "Down"
    metadata TEXT                 -- JSON string with additional info
);
```

### V2: JSONL `trades.jsonl` Format

Each line is a JSON object:

```json
{
  "entry_time": "2026-02-05T10:30:00Z",
  "exit_time": "2026-02-05T10:45:00Z",
  "market_slug": "btc-updown-15m-1738755000",
  "entry_price": 0.65,
  "exit_price": 0.98,
  "entry_amount": 50.0,
  "exit_amount": 75.40,
  "profit_loss": 25.40,
  "exit_reason": "market_resolved",
  "token_id": "0x123abc...",
  "side": "BUY"
}
```

## Architecture

### Class Structure

#### `Trade`
Normalized trade representation used by both extractors.

**Attributes:**
- `bot`: "V1" or "V2"
- `coin`: BTC, ETH, SOL, XRP
- `entry_time`, `exit_time`: datetime with UTC timezone
- `entry_price`, `exit_price`: float
- `size_usd`: Entry amount in USD
- `profit_loss`: Net P&L in USD
- `exit_reason`: String describing exit type
- `market_slug`: Full market identifier
- `hold_seconds`: Seconds held (computed)

#### `V1Extractor`
Reads SQLite database and extracts trades within a time window.

**Key Methods:**
- `extract_trades(hours=24)`: Returns list of Trade objects

#### `V2Extractor`
Reads JSONL log file and extracts trades within a time window.

**Key Methods:**
- `extract_trades(hours=24)`: Returns list of Trade objects
- `_parse_datetime(dt_str)`: Handles ISO 8601 and Unix timestamp formats

#### `BotMetrics`
Computes performance metrics for a set of trades.

**Key Metrics:**
- `win_rate()`: Percentage of profitable trades
- `total_pnl()`: Sum of all profit/loss
- `roi()`: Return on investment
- `profit_factor()`: Wins / losses ratio
- `max_drawdown()`: Sequential peak-to-trough decline
- `sharpe_ratio()`: Risk-adjusted returns (annualized)
- `per_coin_stats()`: Breakdown by asset
- `max_consecutive_losses()`: Longest losing streak

#### `Analyzer`
Compares two sets of trades and generates recommendations.

**Key Methods:**
- `overlapping_markets()`: Markets traded by both bots
- `pnl_correlation()`: Correlation coefficient of overlapping market P&Ls
- `diversification_benefit()`: Volatility reduction from portfolio blend
- `recommend_allocation()`: Returns recommendation tuple with confidence

## Usage Examples

### 1. Daily Comparison Report
```bash
# Generate last 24 hours comparison
python3 compare_bots.py > today_comparison.txt

# Or save to the standard location
python3 compare_bots.py
# Automatically saved to: /opt/polymarket-bot-v2/comparison/COMPARISON_REPORT.txt
```

### 2. Weekly Performance Review
```bash
# Compare last 7 days (168 hours)
python3 compare_bots.py --hours 168 > weekly_report.txt
```

### 3. Integration with Monitoring
```bash
# Hourly comparison (run in cron)
*/60 * * * * cd /opt/polymarket-bot-v2/comparison && \
  python3 compare_bots.py --hours 1 >> logs/hourly_comparison.log
```

## Interpretation Guide

### Win Rate
- **Target**: >55%
- **Excellent**: >65%
- **Poor**: <50% (unlikely to be profitable long-term)

### Profit Factor
- **Excellent**: >2.0 ($2 profit per $1 loss)
- **Good**: >1.5
- **Acceptable**: >1.0
- **Concerning**: <1.0 (losing more than winning)

### Sharpe Ratio
- **Excellent**: >1.5 (strong risk-adjusted returns)
- **Good**: 1.0-1.5
- **Acceptable**: 0.5-1.0
- **Poor**: <0.5

### PnL Correlation (Overlapping Markets)
- **>0.7**: Highly correlated (similar trading signals, less diversification benefit)
- **0.3-0.7**: Moderately correlated (reasonable diversification)
- **<0.3**: Low correlation (strong diversification, complementary strategies)
- **Negative**: Opposite signals (excellent diversification but risky if one bot fails)

### Allocation Confidence
- **HIGH**: ≥50 trades per bot, clear performance difference, low score variance
- **MEDIUM**: 30-50 trades, moderate performance difference
- **LOW**: <30 trades, statistically insufficient data

## Error Handling

The script handles:
- Missing database/log files gracefully (zero trades for missing source)
- Corrupted JSON lines (skipped silently)
- Invalid datetime formats (fallback to ISO 8601 or Unix timestamp)
- Empty trade sets (shows 0 trades, "INSUFFICIENT DATA" recommendation)
- Division by zero (0 profit factor if no losses, 0 Sharpe if insufficient data)

## Output Locations

- **Console**: Full report printed to stdout (for piping/logging)
- **File**: Saved to `/opt/polymarket-bot-v2/comparison/COMPARISON_REPORT.txt`
- **Stderr**: Status messages (extraction progress, error logs)

## Requirements

- Python 3.7+
- Standard library only (no external dependencies):
  - `sqlite3`: Database access
  - `json`: JSONL parsing
  - `datetime`: Time handling
  - `pathlib`: File operations
  - `statistics`: Metrics computation
  - `math`: Sharpe ratio calculation

## Performance

- Typical execution time: <1 second (for 100+ trades)
- Memory usage: <50MB (independent of dataset size due to streaming extraction)
- Can be run continuously in production without resource concerns

## Troubleshooting

### No trades extracted
- **V1**: Check if `/opt/polymarket-bot/data/performance.db` exists and has `trades` table
- **V2**: Check if `/opt/polymarket-bot-v2/4coinsbot/logs/trades.jsonl` exists
- Verify bots have been running long enough to generate trades

### "INSUFFICIENT DATA" recommendation
- Need ≥30 total trades for meaningful comparison
- Try `--hours 168` (1 week) to get more sample data

### Correlation appears wrong
- Requires ≥2 overlapping markets for meaningful correlation
- With <5 overlapping markets, sample size is too small for reliable statistics

### Sharpe ratio is 0
- Need ≥2 trades with non-zero returns
- Formula assumes 96 15-minute markets per day × 365 days = 35,040 periods/year

## Development Notes

### Adding New Metrics
1. Add method to `BotMetrics` class
2. Compute from `self.trades` (list of Trade objects)
3. Add to report template in `format_report()` function

### Supporting New Data Sources
1. Create new `Extractor` class (e.g., `V3Extractor`)
2. Implement `extract_trades(hours=24)` method
3. Return list of `Trade` objects
4. Instantiate in `main()` and pass to `Analyzer`

### Modifying Allocation Logic
- Update weights in `_compute_score()` method
- Confidence thresholds in `_compute_confidence()` method
- Add/remove metrics as needed

## Future Enhancements

- Real-time streaming comparison (WebSocket updates)
- Statistical significance testing (p-values for performance differences)
- Machine learning ensemble recommendations (weighted by estimated signal quality)
- Risk parity allocation (volatility-weighted splits)
- Walk-forward validation (out-of-sample performance estimation)
