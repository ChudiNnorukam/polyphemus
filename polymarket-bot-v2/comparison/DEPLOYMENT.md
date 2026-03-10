# Deployment Guide: Polymarket Bot Comparison Framework

## Installation

### 1. Directory Structure

The framework is located at:
```
/Users/chudinnorukam/Projects/business/polymarket-bot-v2/comparison/
├── compare_bots.py           # Main comparison script
├── test_compare.py           # Test suite
├── README.md                 # Full documentation
├── DEPLOYMENT.md             # This file
└── COMPARISON_REPORT.txt     # Generated report (created on first run)
```

### 2. VPS Deployment

For VPS execution (`142.93.143.178`), copy the script:

```bash
# From local machine
scp /Users/chudinnorukam/Projects/business/polymarket-bot-v2/comparison/compare_bots.py \
  root@142.93.143.178:/opt/polymarket-bot-v2/comparison/

# Verify on VPS
ssh root@142.93.143.178 "ls -la /opt/polymarket-bot-v2/comparison/"
```

## Basic Usage

### Run the comparison (last 24 hours)
```bash
cd /Users/chudinnorukam/Projects/business/polymarket-bot-v2/comparison
python3 compare_bots.py
```

### Run with custom window (e.g., 48 hours)
```bash
python3 compare_bots.py --hours 48
```

### Run tests
```bash
python3 test_compare.py
```

## Output Files

### Console Output
The script prints the report to stdout. Example:
```
==================================================================
  POLYMARKET BOT COMPARISON REPORT
  Window: 24 hours
  Generated: 2026-02-05 14:30:45 UTC
==================================================================

--- V1: Signal Following Bot ---
  Trades: 45 | Win Rate: 62.2% | P&L: $219.00 | ROI: 49.1%
  ...
```

### Report File
Automatically saved to:
```
/opt/polymarket-bot-v2/comparison/COMPARISON_REPORT.txt
```

### Extraction Logs
Status messages printed to stderr:
```
[*] Extracting V1 trades from /opt/polymarket-bot/data/performance.db...
[+] V1: 45 trades extracted
[*] Extracting V2 trades from /opt/polymarket-bot-v2/4coinsbot/logs/trades.jsonl...
[+] V2: 38 trades extracted
[*] Computing metrics...
[+] Report saved to /opt/polymarket-bot-v2/comparison/COMPARISON_REPORT.txt
```

## Data Source Requirements

### V1: SQLite Database
**Location**: `/opt/polymarket-bot/data/performance.db`

**Required Table**: `trades` with columns:
- `trade_id`, `token_id`, `entry_time`, `entry_price`, `entry_size`
- `side`, `entry_amount`, `exit_time`, `exit_price`, `exit_amount`
- `exit_reason`, `profit_loss`, `slug`, `outcome`, `metadata`

**Verify with:**
```bash
sqlite3 /opt/polymarket-bot/data/performance.db "SELECT COUNT(*) FROM trades;"
```

### V2: JSONL Trades Log
**Location**: `/opt/polymarket-bot-v2/4coinsbot/logs/trades.jsonl`

**Format**: One JSON object per line
```json
{"entry_time": "2026-02-05T10:30:00Z", "exit_time": "2026-02-05T10:45:00Z", ...}
```

**Verify with:**
```bash
wc -l /opt/polymarket-bot-v2/4coinsbot/logs/trades.jsonl
```

## Integration Patterns

### 1. Daily Cron Job
Add to crontab to run daily comparison:

```bash
# Run daily at 8 AM UTC, log results
0 8 * * * cd /opt/polymarket-bot-v2/comparison && \
  python3 compare_bots.py >> /var/log/polymarket-comparison.log 2>&1

# Keep last 30 days of logs
0 9 * * * find /var/log -name "polymarket-comparison.log*" -mtime +30 -delete
```

### 2. Weekly Performance Review
```bash
# Every Sunday at 10 AM UTC, compare last 7 days
0 10 * * 0 cd /opt/polymarket-bot-v2/comparison && \
  python3 compare_bots.py --hours 168 > /tmp/weekly_comparison.txt && \
  mail -s "Weekly Bot Comparison" admin@example.com < /tmp/weekly_comparison.txt
```

### 3. Real-time Monitoring (Systemd Timer)
Create `/etc/systemd/system/polymarket-comparison.service`:

```ini
[Unit]
Description=Polymarket Bot Comparison Report
After=polymarket-bot.service polymarket-bot-v2.service

[Service]
Type=oneshot
User=root
WorkingDirectory=/opt/polymarket-bot-v2/comparison
ExecStart=/usr/bin/python3 compare_bots.py

[Install]
WantedBy=multi-user.target
```

Create `/etc/systemd/system/polymarket-comparison.timer`:

```ini
[Unit]
Description=Run Polymarket Bot Comparison Hourly
Requires=polymarket-comparison.service

[Timer]
OnBootSec=5min
OnUnitActiveSec=1h
Persistent=true

[Install]
WantedBy=timers.target
```

Enable:
```bash
systemctl daemon-reload
systemctl enable --now polymarket-comparison.timer
systemctl status polymarket-comparison.timer
```

### 4. Dashboard Integration
Generate HTML report from text output:

```bash
# Generate comparison
python3 compare_bots.py > /tmp/comparison.txt

# Convert to HTML (using simple shell script)
cat > /tmp/compare_to_html.sh << 'EOF'
#!/bin/bash
echo "<pre>"
cat /tmp/comparison.txt | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g'
echo "</pre>"
EOF

bash /tmp/compare_to_html.sh > /var/www/html/polymarket-comparison.html
```

## Troubleshooting

### No Data Extracted

**Problem**: "V1: 0 trades extracted" or "V2: 0 trades extracted"

**Solutions**:
1. Verify database/log file exists:
   ```bash
   ls -la /opt/polymarket-bot/data/performance.db
   ls -la /opt/polymarket-bot-v2/4coinsbot/logs/trades.jsonl
   ```

2. Check database integrity:
   ```bash
   sqlite3 /opt/polymarket-bot/data/performance.db ".tables"
   sqlite3 /opt/polymarket-bot/data/performance.db ".schema trades"
   ```

3. Check JSONL format:
   ```bash
   head -1 /opt/polymarket-bot-v2/4coinsbot/logs/trades.jsonl | python3 -m json.tool
   ```

4. Verify bots are running:
   ```bash
   systemctl status polymarket-bot
   systemctl status polymarket-bot-v2
   ```

### "INSUFFICIENT DATA" Recommendation

**Problem**: Allocation recommendation shows "INSUFFICIENT DATA"

**Cause**: Fewer than 30 total trades across both bots

**Solution**: Increase comparison window:
```bash
python3 compare_bots.py --hours 168  # 1 week instead of 24 hours
```

### Sharpe Ratio is 0

**Problem**: Sharpe Ratio shows 0.00

**Cause**: Too few trades (<2) or all returns are identical

**Solution**:
- Wait for more trades to accumulate
- Use longer comparison window: `--hours 168`

### Permission Denied

**Problem**: "Permission denied: '/opt/polymarket-bot/data/performance.db'"

**Solution**:
```bash
# On VPS
sudo chmod 644 /opt/polymarket-bot/data/performance.db
sudo chmod 755 /opt/polymarket-bot/data/
```

## Performance Characteristics

| Metric | Value |
|--------|-------|
| Execution time | <1 second |
| Memory usage | <50MB |
| CPU usage | <2% |
| Database I/O | Minimal (streaming) |
| Network I/O | None |
| Disk I/O | One read per bot |

Safe for:
- Running every hour in production
- Parallel execution (no file locks)
- High-frequency dashboards
- Mobile/low-bandwidth clients

## Script Maintenance

### Adding Metrics
Edit `BotMetrics` class in `compare_bots.py`:

1. Add method:
```python
def new_metric(self):
    """Compute new metric."""
    # ...
    return value
```

2. Add to report in `format_report()`:
```python
lines.append(f"  New Metric: {metrics.new_metric():.2f}")
```

3. Update test:
```python
def test_metrics():
    # ...
    assert metrics.new_metric() > 0, "New metric should be positive"
```

### Supporting New Data Sources
1. Create `NewExtractor` class extending extractor pattern
2. Implement `extract_trades(hours=24)` method
3. Return list of `Trade` objects
4. Update `main()` to use new extractor

## Monitoring & Alerts

### Alert Rules (Example)
If running in monitoring system (Prometheus, Datadog, etc.):

1. **No data extracted**: Alert if V1 or V2 trades = 0 for >24h
2. **Win rate drops**: Alert if win_rate < 45% (threshold from training)
3. **Drawdown spike**: Alert if max_drawdown > $100
4. **Report missing**: Alert if report file older than 2 hours

### Health Check
```bash
#!/bin/bash
REPORT="/opt/polymarket-bot-v2/comparison/COMPARISON_REPORT.txt"
AGE=$(($(date +%s) - $(stat -f %m "$REPORT")))

if [ $AGE -gt 7200 ]; then
  echo "ALERT: Comparison report older than 2 hours"
  exit 1
fi

if ! grep -q "Recommendation" "$REPORT"; then
  echo "ALERT: Comparison report incomplete"
  exit 1
fi

echo "OK: Comparison report healthy"
exit 0
```

## Version History

### v1.0 (2026-02-05)
- Initial release
- Core metrics: win rate, P&L, ROI, profit factor, drawdown, Sharpe
- Per-coin breakdown
- Overlapping market analysis
- PnL correlation
- Diversification benefit estimation
- Allocation recommendation engine

## Support

For issues or improvements:
1. Run tests: `python3 test_compare.py`
2. Check logs: Check stderr output for extraction errors
3. Verify data sources: Ensure V1 DB and V2 JSONL have recent trades
4. Review metrics: Compare against expected ranges in README.md

## License

Internal use only. Part of Polymarket Bot trading infrastructure.
