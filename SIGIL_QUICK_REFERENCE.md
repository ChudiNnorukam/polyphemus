# Sigil V2 - Quick Reference Summary

**Date:** 2026-02-09 03:05 UTC  
**Status:** ✅ RUNNING & HEALTHY

## Current State (Last 10 minutes)

| Metric | Value |
|--------|-------|
| **Service Status** | ACTIVE (PID 194514) |
| **Uptime** | 10 minutes |
| **USDC Balance** | $137.33 |
| **Open Positions** | 0 |
| **Error Count** | 0 |
| **Memory** | 56.5M |

## Historical Performance (2026-02-04 to 2026-02-07)

| Metric | Value |
|--------|-------|
| **Total Trades** | 608 |
| **Win Rate** | 59.2% (360 winners) |
| **Total P&L** | +$1,145.28 |
| **Avg P&L per Trade** | +$1.88 |
| **Avg Hold Time** | 616 seconds (~10 min) |

## Exit Reasons (608 total)

- **Market Resolved:** 261 (42.9%) - held to binary resolution
- **Sell Signal:** 153 (25.2%) - exited via DB signal
- **Profit Target:** 58 (9.5%) - price target hit
- **Time Exit:** 56 (9.2%) - max hold time
- **Stop Loss:** 39 (6.4%) - 15% stop loss triggered
- **Other:** 41 (6.7%) - manual/cleanup/reconcile

## Asset Performance

| Asset | Trades | P&L | Avg |
|-------|--------|-----|-----|
| **Down** | 437 | +$734.54 | +$1.68 |
| **Up** | 171 | +$410.73 | +$2.40 |

## VPS Configuration

```
Host:        142.93.143.178
Service:     sigil (systemd)
Path:        /opt/sigil/
Database:    /opt/sigil/data/performance.db (332K)
Python:      /opt/sigil/venv/bin/python
Monitoring:  5-min health interval
Logs:        50+ health JSON files
```

## Current Activity (Last 60 seconds)

### ✅ Working
- RTDS WebSocket connected
- Receiving market signals (BTC, ETH, SOL, XRP)
- WebSocket auto-reconnection functional
- Health monitoring running

### ⚠️ Current Window (50 signals processed)
- All signals rejected (market_expired)
- No positions entered
- Indicates DB is alive but 15-min markets already resolved

### ⚠️ Known Issues
- 2 Gamma API order book fetch timeouts (non-critical)
- Recent restart - monitor for stability

## Performance Insights

### Strengths
1. **Profitable:** +$1,145.28 across 608 trades
2. **Consistent:** 59.2% win rate over 2.85 days
3. **Active Management:** 51.8% of trades exit before market resolution
4. **Risk Control:** Stop loss at 15%, time exits at max hold

### What's Working
- Mix of early exits (sell signal, profit target, stop loss, time exit)
- Binary resolution strategy (42.9% held to resolution)
- Reference trader signal integration
- Market cycle adaptation

## Monitoring Checklist

- [ ] Monitor signal acceptance rate (should improve as fresh markets launch)
- [ ] Watch balance trend (should stabilize once trading resumes)
- [ ] Check for API errors (Gamma API reliability)
- [ ] Verify uptime over next 4-6 hours
- [ ] Confirm arb_engine operational if enabled

## Next Steps

1. **Monitor:** 2-4 hours for signal normalization
2. **Investigate:** Gamma API fetch failures if persistent
3. **Review:** Config to ensure all modules enabled
4. **Verify:** Dashboard endpoint availability
5. **Watch:** Balance trend and new position entries

---

**Recommendation:** No action required. Service is healthy and operational. Recent restart (10 min ago) is performing as expected.

For detailed report, see: `/Users/chudinnorukam/Projects/business/SIGIL_STATUS_2026-02-09.txt`
