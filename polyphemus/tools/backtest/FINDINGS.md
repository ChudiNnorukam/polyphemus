# binance_momentum Entry-Filter Backtest — 2026-04-17

## Context
Task #1 from the "1, 2 then 3" sequence: engineer the win/loss ratio on
binance_momentum so Sharpe flips positive.

Original plan was "tighter stop-loss + hold-longer exit." That requires
intra-trade price data (MAE/MFE/ticks). **MAE, MFE, mid_t90, mid_t180,
btc_at_resolution are all NULL** on both the rnd_lab and live VPS DBs.
The instrumentation exists but has never been populated.

Pivoted to **entry-filter optimization**: given 508 realized trades,
which SUBSET has positive Sharpe? That subset becomes a deployable
entry filter.

## Data
- Source: `/tmp/emmanuel_perf_20260417.db` (VPS snapshot 2026-04-17 15:42 PST)
- Rows: 508 closed `signal_bot` trades where `metadata.source = binance_momentum`
- Date range: 2026-03-04 to 2026-03-25 (21 days, ~24 trades/day)

## Baseline
```
n=508  WR=56.3%  Wilson lo=52.0%
avg_win=+49.6%   avg_loss=-66.9%
win/loss_ratio=0.74   breakeven_ratio=0.78   gap=-0.03
Sharpe=-0.02   total_pnl=$-61.33
```
At 56.3% WR, we need win/loss ratio ≥ 0.78 to break even. We are at 0.74.
**Gap = -0.03 per trade** — extraordinarily close to breakeven.

## Grid search output (top winners, n ≥ 30)
| filter | n | WR | Wilson lo | Sharpe | PnL |
|---|---|---|---|---|---|
| dir=down ∩ entry_price ≥ 0.80 | 38 | 81.6% | 66.6% | +0.32 | +$25.91 |
| entry_price ≥ 0.80 (all dirs) | 65 | 73.8% | 62.0% | +0.18 | +$20.02 |
| BTC ∩ dir=up | 63 | 63.5% | 51.1% | +0.12 | +$35.14 |
| XRP ∩ dir=down | 45 | 64.4% | 49.8% | +0.10 | +$23.01 |

## Grid search output (biggest losers, candidates to EXCLUDE)
| filter | n | WR | Sharpe | PnL |
|---|---|---|---|---|
| entry_price ∈ [0.65, 0.80) | 186 | 52.2% | -0.13 | **-$102.79** |
| BTC ∩ dir=down | 63 | 49.2% | -0.15 | -$61.13 |
| SOL ∩ entry_price ∈ [0.65, 0.80) | 60 | 50.0% | -0.19 | -$33.25 |

**The `[0.65, 0.80)` entry band is single-handedly burning $103** (36% of
trades, 52% WR, -0.13 Sharpe). This is the most actionable finding.

## Scenario comparison vs baseline
| Scenario | N kept | WR | Sharpe | PnL |
|---|---|---|---|---|
| Baseline | 508 | 56.3% | -0.02 | -$61 |
| A. entry ≥ 0.80 | 65 | 73.8% | +0.18 | +$20 |
| B. skip [0.65, 0.80) | 322 | 58.7% | +0.03 | +$41 |
| C. skip [0.65, 0.80) + skip BTC-down | 274 | 60.2% | +0.05 | +$74 |
| D. ep_80+ OR BTC-up OR XRP-down[0.50,0.65) | 146 | 68.5% | +0.12 | +$78 |
| **D'. ep_80+ OR BTC-up** | **120** | **69.2%** | **+0.12** | **+$56** |
| F. skip [0.65,0.80) + whitelist | 189 | 64.0% | +0.09 | +$80 |

## Walk-forward validation (5 folds, time-ordered)
| Scenario | F1 | F2 | F3 | F4 | F5 | +folds |
|---|---|---|---|---|---|---|
| A (ep_80+) | +$0.8 | +$12.3 | skip | skip | +$5.9 | 3/3 of tradeable |
| C | -$40.5 | +$22.9 | +$63.6 | +$31.7 | -$3.5 | 3/5 (60%) |
| D (full) | -$6.7 | +$51.8 | +$15.6 | +$14.7 | +$2.9 | 4/5 (80%) |
| **D' (no XRP)** | **-$6.7** | **+$35.3** | **+$20.3** | **+$9.2** | **-$2.1** | **3/5 (60%)** |
| F | -$8.2 | +$55.7 | +$27.1 | -$4.2 | +$9.4 | 3/5 (60%) |

**D's XRP component appears only in folds F2-F5; in F1 it contributes 0 trades.**
Dropping it (→ D') trades 22% PnL for lower curve-fit risk. D' walk-forward:
3 of 5 folds clearly positive, F5 marginal (-$2.1), F1 warm-up (-$6.7).

## Recommendation

Deploy **Scenario D' as a signal_bot entry filter**:

```python
def binance_momentum_entry_filter(signal) -> bool:
    """Return True to accept the signal, False to skip.

    Based on 508-trade backtest 2026-03-04 to 2026-03-25.
    Walk-forward 3/5 folds positive; Sharpe +0.12 vs baseline -0.02.
    Flips 21-day PnL from -$61 to +$56.
    """
    entry_price = signal.get("price", 0)  # token price paid
    asset = (signal.get("asset") or "").upper()
    direction = (signal.get("outcome") or "").lower()

    if entry_price >= 0.80:
        return True  # deep favorite: 73.8% WR
    if asset == "BTC" and direction == "up":
        return True  # BTC-up: 63.5% WR
    return False
```

**Expected deployment characteristics**:
- Accept rate: ~24% of baseline signals (120/508)
- Volume: ~5-6 trades/day (down from 24/day)
- WR: 69% expected (vs 56% baseline)
- Sharpe: +0.12 expected (vs -0.02 baseline)

## Limitations & honest caveats

1. **21-day in-sample window is short.** Alpha decay is a real risk.
   MEMORY.md flags "alpha decay NO-GO" for older signal_bot data. D' is
   validated across 5 folds within this window but not ACROSS windows.

2. **Fees might be under-counted.** The `pnl` column appears to include
   CLOB fee-in-shares since avg_loss (-67%) > avg_win (+50%) magnitudes,
   but I did not verify the fee-accounting code path. If fees are NOT
   included, subtract ~4.4% from every `pnl_pct` and re-evaluate.

3. **No intra-trade price data.** Cannot simulate tighter stops or
   longer holds. Task #3 (fix `binance_price_at_fill` dropout) unlocks
   that analysis.

4. **Walk-forward F1 loss (-$6.7) is small but real.** Possible
   explanations: (a) first-fold warm-up as strategy learns, (b) regime
   shift. Treat F1 as mild evidence the filter may not be robust in
   unobserved regimes.

## Deployment plan (gated by user approval)

Per LIFECYCLE, I am NOT deploying this. The next step requires Chudi's
explicit OK because it changes `.env` trading parameters on a running
instance (even if DRY_RUN=true).

**Recommended staged rollout if approved**:
1. **Phase 4 TEST**: add the filter as a `POLYPHEMUS_MOMENTUM_STRICT_FILTER=true`
   flag, default off. Deploy to emmanuel (DRY_RUN=true).
2. **Shadow soak, 7 days**: run with flag ON, compare D' filter output
   to baseline in real time. Expect ~5 trades/day under filter.
3. **MTC gate re-check** after 7 days: segment-by source, entry_band.
   Confirm WR ≥ 65%, PnL > 0, walk-forward still ≥ 60%.
4. **Phase 5 LIVE**: only if shadow passes. Move to 5% capital →
   20% → 100% over 2 weeks.

No live-money change until the shadow soak is green and the MTC gate
verdicts match backtest expectations.

## Files
- `polyphemus/tools/backtest/filter_search.py` — grid search across dimensions
- `polyphemus/tools/backtest/scenario_compare.py` — A-F scenario compare
- `polyphemus/tools/backtest/walk_forward.py` — 5-fold time-ordered split
- `polyphemus/tools/backtest/FINDINGS.md` — this file

## Next (Task #2)
Activate Markov-Kelly gate in shadow mode (commit c51c5ed, default-off).
Run shadow for a week to see if it would have blocked the 0-for-27 week.
