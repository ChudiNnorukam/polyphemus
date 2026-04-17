# Pre-Registration Protocol: Polymarket Strategy R&D Lab

> **Date**: 2026-04-11
> **Time-box**: 20 hours (hard stop Apr 25)
> **Kill criteria**: No feature with AUC > 0.55 at Phase 2 = PARK permanently
> **Authority**: This document is BINDING. Hypotheses, metrics, and split methodology are locked before any analysis runs. Post-hoc additions are flagged as exploratory and cannot drive go-live decisions.

---

## 1. Dataset Description

| Source | Rows | Type | Features (>80%) |
|--------|------|------|-----------------|
| Emmanuel signals | 9,990 | 8,212 counterfactual + 1,778 traded | 15 |
| Polyphemus signals | 1,309 | 1,307 traded + 2 other | 32 |
| Combined | 11,299 | Mixed | 15 (cross-instance) |

**Label**: `is_win` (INTEGER 0/1)
- On non-traded signals: "signal direction was correct at market resolution" (counterfactual)
- On traded signals: "trade was profitable" (includes entry price impact)

**Critical distinction**: `is_win=1` does NOT mean a trade at that signal would have been profitable. A correct direction prediction at midpoint 0.90 pays only $0.10 on $0.90 risked. Profitability requires modeling both P(correct) AND payoff ratio.

**Base rates**:
- Emmanuel: 62.3% direction accuracy (inflated by non-BTC and non-cheap-side signals)
- Polyphemus: 55.8% direction accuracy
- Combined BTC only: 63.9%

---

## 2. Train/Test Split (LOCKED)

**Method**: Temporal split. NOT random. Markets have temporal autocorrelation.

- **Train**: First 70% of rows by epoch (earliest signals)
- **Validation**: Next 15% by epoch
- **Test (holdout)**: Final 15% by epoch (most recent signals)

The test set is NEVER touched until final evaluation. All feature selection, threshold tuning, and model selection happen on train + validation only.

**No leakage rules**:
- Features computed from future data (e.g., exit_price, pnl, hold_secs) are EXCLUDED from pre-trade prediction models
- Features from the same epoch as the target signal are allowed (they represent current market state)
- No target encoding (encoding is_win statistics into features)

---

## 3. Pre-Registered Hypotheses

Each hypothesis has: a specific prediction, a feature set, a metric, and a threshold. They are tested in order. Each must pass its threshold to proceed to the next.

### H1: Time-of-day predicts direction accuracy
**Prediction**: Some UTC hours have significantly higher WR than others.
**Features**: hour_utc
**Metric**: Chi-squared test for independence between hour_utc bins and is_win
**Threshold**: p < 0.01 AND at least one hour bin with WR > 70% at n >= 50
**If fails**: Time-of-day is noise. Remove from feature set.

### H2: Time-remaining predicts direction accuracy
**Prediction**: Signals with more time remaining have different WR than late signals.
**Features**: time_remaining_secs
**Metric**: AUC of time_remaining_secs as single-feature predictor of is_win
**Threshold**: AUC > 0.55 (above chance) on validation set
**If fails**: Time remaining is not predictive. Remove from feature set.

### H3: Midpoint (market probability) predicts our WR
**Prediction**: Lower midpoints (e.g., 0.30) have different direction accuracy than higher midpoints (0.80).
**Features**: midpoint
**Metric**: AUC of midpoint as single-feature predictor of is_win
**Threshold**: AUC > 0.55
**Note**: Even if direction accuracy is LOWER at low midpoints, the payoff ratio may make them +EV. This hypothesis tests prediction, not profitability.
**If fails**: Midpoint is not predictive of direction accuracy.

### H4: Momentum magnitude predicts direction accuracy
**Prediction**: Larger absolute momentum_pct = stronger signal.
**Features**: abs(momentum_pct)
**Metric**: AUC > 0.55
**If fails**: Momentum magnitude is noise.

### H5: Volatility regime predicts direction accuracy
**Prediction**: Low-volatility regimes have different WR than high-volatility.
**Features**: volatility_1h, regime
**Metric**: AUC > 0.55 for volatility_1h; chi-squared p < 0.01 for regime categories
**If fails**: Regime is not predictive.

### H6: Combined model outperforms best single feature
**Prediction**: A logistic regression on all passing features outperforms the best individual feature.
**Features**: All features that passed H1-H5
**Metric**: AUC on validation set > best single-feature AUC + 0.02
**Threshold**: Combined AUC > 0.57 minimum
**If fails**: No interaction effects. Use the single best feature as a filter.

### H7 (Polyphemus only): Signal score predicts outcomes
**Prediction**: The existing signal_score feature (98% populated in Polyphemus) predicts is_win.
**Features**: signal_score
**Metric**: AUC > 0.55 on Polyphemus validation set
**If fails**: Signal scoring pipeline is not adding value.

---

## 4. Expected Value Model (if H1-H6 produce a viable filter)

If any hypothesis produces a usable filter (AUC > 0.55), compute:

```
EV_per_trade = P(win|filter) * avg_win - P(loss|filter) * avg_loss - fee
```

Where:
- P(win|filter) = WR in the filtered cohort (validation set)
- avg_win = (1 - entry_price) * shares (payout on correct prediction)
- avg_loss = entry_price * shares (lost on incorrect prediction)
- fee = entry_price * (1 - entry_price) per share (Polymarket fee formula)

**Profitability gate**: EV_per_trade > 0 on validation set, confirmed on test set.

**Kelly criterion**: If EV > 0, compute:
```
kelly_fraction = (p * b - q) / b
where p = WR, q = 1-WR, b = avg_win / avg_loss
```

Kelly must be > 0 on the test set for any go-live recommendation.

---

## 5. Anti-Overfitting Safeguards

1. **No more than 7 hypotheses.** Each additional test inflates false discovery rate. If all 7 hypotheses are tested, apply Bonferroni correction: divide alpha by 7 (p < 0.0014 for individual tests).

2. **No threshold tuning on test set.** All thresholds (e.g., "trade only when hour_utc in [0,1,2]") are selected on validation data. The test set only confirms/denies.

3. **Report ALL results.** Negative findings are as important as positive ones. Do not cherry-pick the one feature that "worked."

4. **Walk-forward validation.** After temporal train/val/test, also run a rolling 7-day walk-forward: train on days 1-7, predict day 8, roll forward. This catches non-stationarity.

5. **Effect size requirement.** Statistical significance alone is insufficient. The filter must produce a WR improvement of at least 5 percentage points over the base rate (unfiltered WR) to be practically meaningful.

6. **Replication across instances.** Any finding on Emmanuel must replicate on Polyphemus (or vice versa) at p < 0.05 to be considered robust. Instance-specific findings are flagged as fragile.

---

## 6. Decision Framework

| Phase | Action | Gate |
|-------|--------|------|
| Phase 1 | Individual feature AUC screening | Any feature AUC > 0.55? If no: PARK. |
| Phase 2 | Combined model on validation set | Combined AUC > 0.57? If no: use best single feature. |
| Phase 3 | EV computation on validation set | EV > 0 per trade? If no: PARK. |
| Phase 4 | Test set confirmation | Kelly > 0 on holdout? If no: PARK. |
| Phase 5 | Walk-forward stability | WR stable across rolling windows? If no: PARK. |
| Phase 6 | LIFECYCLE Phase 4B+ | Dry-run deployment per LIFECYCLE.md |

**PARK = stop R&D, redirect to Tradeify. No more Polymarket capital.**

---

## 7. Exploratory Analysis (NOT pre-registered)

The following analyses may be run for insight but CANNOT drive go-live decisions:

- Cluster analysis of signal features
- Interaction plots between features
- Analysis of mm_observations.db (20GB, untapped)
- Sentiment/order-book features (too sparse for modeling but may inspire future data collection)

Any finding from exploratory analysis that looks promising must be registered as a NEW hypothesis and tested on FRESH data (future signals collected in dry-run mode, minimum n=200).

---

## 8. Time Budget

| Activity | Hours | Cumulative |
|----------|-------|------------|
| P0-P4 prerequisites (this session) | 3h | 3h |
| Phase 1: Feature screening | 4h | 7h |
| Phase 2: Combined model | 3h | 10h |
| Phase 3-4: EV + test set | 2h | 12h |
| Phase 5: Walk-forward | 3h | 15h |
| Buffer / exploratory | 5h | 20h |

**Hard stop at 20h regardless of progress.**
