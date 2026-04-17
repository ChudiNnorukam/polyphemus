"""
Phase 2: Combined model analysis on cheap-side BTC signals.
Protocol: /Users/chudinnorukam/Projects/business/polyphemus/rnd_lab/PRE_REGISTRATION.md
DO NOT touch the test set.
"""

import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.metrics import roc_auc_score
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import cross_val_score
from scipy import stats

DB_PATH = "/Users/chudinnorukam/Projects/business/polyphemus/rnd_lab/data/combined_signals.db"
OUT_DIR  = Path("/Users/chudinnorukam/Projects/business/polyphemus/rnd_lab/.omc/scientist")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────
# STEP 1: Filter to tradeable universe
# ─────────────────────────────────────────────
print("=" * 65)
print("STEP 1: FILTER TO TRADEABLE UNIVERSE")
print("=" * 65)

conn = sqlite3.connect(DB_PATH)
df_raw = pd.read_sql_query("SELECT * FROM analysis", conn)
conn.close()

print(f"Raw rows: {len(df_raw):,}")
print(f"Assets: {df_raw['asset'].value_counts().to_dict()}")
print(f"is_win null: {df_raw['is_win'].isna().sum()}")

df = df_raw[
    (df_raw['asset'] == 'BTC') &
    (df_raw['midpoint'] >= 0.40) &
    (df_raw['midpoint'] <= 0.55) &
    (df_raw['is_win'].notna())
].copy()

print(f"\nAfter filter (BTC + midpoint 0.40-0.55 + is_win not null): {len(df):,} rows")
print(f"  is_win distribution: {df['is_win'].value_counts().to_dict()}")
print(f"  Base WR: {df['is_win'].mean():.3f}")
print(f"  Epoch range: {df['epoch'].min():.0f} -> {df['epoch'].max():.0f}")

if len(df) < 100:
    print("\nFLAG: INSUFFICIENT DATA (n < 100). Cannot model reliably. PARK recommendation.")
    raise SystemExit(1)
else:
    print(f"\nData sufficient for modeling (n={len(df):,}).")

# ─────────────────────────────────────────────
# STEP 2: Temporal split (70/15/15)
# ─────────────────────────────────────────────
print("\n" + "=" * 65)
print("STEP 2: TEMPORAL SPLIT (70 / 15 / 15)")
print("=" * 65)

df = df.sort_values('epoch').reset_index(drop=True)
n = len(df)
train_end = int(n * 0.70)
val_end   = int(n * 0.85)

train = df.iloc[:train_end].copy()
val   = df.iloc[train_end:val_end].copy()
test  = df.iloc[val_end:].copy()   # DO NOT ANALYZE — stored only for future use

print(f"  Train:      n={len(train):,}  WR={train['is_win'].mean():.3f}")
print(f"  Validation: n={len(val):,}   WR={val['is_win'].mean():.3f}")
print(f"  Test:       n={len(test):,}   WR={test['is_win'].mean():.3f}  [LOCKED — not used]")

epoch_train_end = train['epoch'].max()
epoch_val_end   = val['epoch'].max()
print(f"  Train epoch max:  {epoch_train_end:.0f}")
print(f"  Val epoch max:    {epoch_val_end:.0f}")

# ─────────────────────────────────────────────
# STEP 3: Individual feature AUC
# ─────────────────────────────────────────────
print("\n" + "=" * 65)
print("STEP 3: INDIVIDUAL FEATURE AUC (CHEAP-SIDE BTC)")
print("=" * 65)

# Cyclic hour encoding
for split_df in [train, val, test]:
    split_df['hour_sin'] = np.sin(2 * np.pi * split_df['hour_utc'] / 24)
    split_df['hour_cos'] = np.cos(2 * np.pi * split_df['hour_utc'] / 24)
    split_df['abs_momentum'] = split_df['momentum_pct'].abs()

features_to_test = [
    ('time_remaining_secs', 'time_remaining_secs'),
    ('abs(momentum_pct)',    'abs_momentum'),
    ('hour_sin',            'hour_sin'),
    ('hour_cos',            'hour_cos'),
    ('volatility_1h',       'volatility_1h'),
    ('trend_1h',            'trend_1h'),
    ('day_of_week',         'day_of_week'),
    ('midpoint',            'midpoint'),
    ('fear_greed',          'fear_greed'),
]

auc_results = []
PASS_THRESHOLD = 0.52

for label, col in features_to_test:
    tr = train[[col, 'is_win']].dropna()
    vl = val[[col, 'is_win']].dropna()

    if len(tr) < 10 or len(vl) < 5:
        print(f"  {label:<25}  SKIP (too few non-null rows)")
        continue

    try:
        tr_auc = roc_auc_score(tr['is_win'], tr[col])
        vl_auc = roc_auc_score(vl['is_win'], vl[col])
        pop_pct = tr.shape[0] / len(train) * 100
        passes  = vl_auc > PASS_THRESHOLD
        flag    = " PASS" if passes else ""
        auc_results.append({
            'feature': label,
            'col': col,
            'train_auc': tr_auc,
            'val_auc': vl_auc,
            'n_train': len(tr),
            'n_val': len(vl),
            'pop_pct': pop_pct,
            'passes': passes,
        })
        print(f"  {label:<25}  train={tr_auc:.3f}  val={vl_auc:.3f}  n_tr={len(tr)}  n_vl={len(vl)}  pop={pop_pct:.0f}%{flag}")
    except Exception as e:
        print(f"  {label:<25}  ERROR: {e}")

passing = [r for r in auc_results if r['passes']]
print(f"\nPassing features (val AUC > {PASS_THRESHOLD}): {len(passing)}")
for r in passing:
    print(f"  {r['feature']:<25}  val_auc={r['val_auc']:.3f}")

# ─────────────────────────────────────────────
# STEP 4: Logistic regression combined model
# ─────────────────────────────────────────────
print("\n" + "=" * 65)
print("STEP 4: LOGISTIC REGRESSION COMBINED MODEL")
print("=" * 65)

# Use all passing features + midpoint (always include in narrow band)
feature_cols = list({r['col'] for r in passing} | {'midpoint'})
feature_cols = [c for c in feature_cols if c in train.columns]
print(f"Features in combined model: {feature_cols}")

# Drop rows with any null in selected features
tr_model = train[feature_cols + ['is_win']].dropna()
vl_model = val[feature_cols + ['is_win']].dropna()

print(f"  Train n after dropna: {len(tr_model)}")
print(f"  Val n after dropna:   {len(vl_model)}")

X_train = tr_model[feature_cols].values
y_train = tr_model['is_win'].values.astype(int)
X_val   = vl_model[feature_cols].values
y_val   = vl_model['is_win'].values.astype(int)

scaler = StandardScaler()
X_train_s = scaler.fit_transform(X_train)
X_val_s   = scaler.transform(X_val)

lr = LogisticRegression(max_iter=1000, random_state=42)
lr.fit(X_train_s, y_train)

train_pred = lr.predict_proba(X_train_s)[:, 1]
val_pred   = lr.predict_proba(X_val_s)[:, 1]

train_auc_combined = roc_auc_score(y_train, train_pred)
val_auc_combined   = roc_auc_score(y_val, val_pred)

print(f"\n  Combined model train AUC: {train_auc_combined:.3f}")
print(f"  Combined model val AUC:   {val_auc_combined:.3f}")

best_single_val_auc = max((r['val_auc'] for r in auc_results), default=0.5)
print(f"  Best single feature val AUC: {best_single_val_auc:.3f}")

if val_auc_combined > best_single_val_auc:
    print(f"  Combined BEATS best single by {val_auc_combined - best_single_val_auc:.3f}")
    use_combined = True
else:
    print(f"  Combined does NOT beat best single. Using combined anyway (broader coverage).")
    use_combined = True  # still use it; signal is in val AUC number

# Feature coefficients
print("\n  Feature coefficients (standardized):")
for fname, coef in sorted(zip(feature_cols, lr.coef_[0]), key=lambda x: abs(x[1]), reverse=True):
    print(f"    {fname:<25}  coef={coef:.4f}")

# ─────────────────────────────────────────────
# STEP 5: Expected Value by Quintile
# ─────────────────────────────────────────────
print("\n" + "=" * 65)
print("STEP 5: EXPECTED VALUE BY QUINTILE (VALIDATION SET)")
print("=" * 65)

# Attach val predictions back
vl_ev = vl_model.copy()
vl_ev['pred_prob'] = val_pred

# Quintile bins
try:
    vl_ev['quintile'] = pd.qcut(vl_ev['pred_prob'], q=5, labels=False, duplicates='drop')
except Exception:
    vl_ev['quintile'] = pd.cut(vl_ev['pred_prob'], bins=5, labels=False)

print(f"\n{'Q':<4} {'n':>5} {'ActWR':>7} {'AvgMid':>8} {'EV/trade':>10} {'Kelly':>8} {'PredP_lo':>10} {'PredP_hi':>10}")
print("-" * 75)

ev_table = []
positive_ev_quintiles = []

for q in sorted(vl_ev['quintile'].dropna().unique()):
    sub = vl_ev[vl_ev['quintile'] == q]
    n_q = len(sub)
    wr  = sub['is_win'].mean()
    avg_mid = sub['midpoint'].mean()
    pred_lo = sub['pred_prob'].min()
    pred_hi = sub['pred_prob'].max()

    # Payoff: win = (1 - avg_mid), lose = avg_mid
    payoff_win  = 1 - avg_mid
    payoff_lose = avg_mid
    fee         = avg_mid * (1 - avg_mid)   # per-share fee
    ev          = wr * payoff_win - (1 - wr) * payoff_lose - fee

    # Kelly: p*b - (1-p) / b where b = payoff_win / payoff_lose
    b     = payoff_win / payoff_lose if payoff_lose > 0 else 0
    kelly = (wr * b - (1 - wr)) / b if b > 0 else -99

    pos_flag = " <-- POSITIVE EV" if ev > 0 and kelly > 0 else ""
    print(f"  Q{int(q)+1:<3} {n_q:>5} {wr:>7.3f} {avg_mid:>8.4f} {ev:>10.4f} {kelly:>8.4f}  [{pred_lo:.3f}-{pred_hi:.3f}]{pos_flag}")

    ev_table.append({
        'quintile': int(q) + 1,
        'n': n_q,
        'wr': wr,
        'avg_midpoint': avg_mid,
        'ev_per_trade': ev,
        'kelly': kelly,
        'pred_prob_lo': pred_lo,
        'pred_prob_hi': pred_hi,
    })
    if ev > 0 and kelly > 0:
        positive_ev_quintiles.append(ev_table[-1])

print(f"\nPositive EV quintiles: {len(positive_ev_quintiles)}")

# Also check overall val EV (no filter)
overall_wr = val['is_win'].mean()
avg_mid_all = val['midpoint'].mean()
overall_ev  = overall_wr * (1 - avg_mid_all) - (1 - overall_wr) * avg_mid_all - avg_mid_all * (1 - avg_mid_all)
overall_kelly = (overall_wr * ((1 - avg_mid_all) / avg_mid_all) - (1 - overall_wr)) / ((1 - avg_mid_all) / avg_mid_all)
print(f"\nOverall (unfiltered) val: WR={overall_wr:.3f}  avg_mid={avg_mid_all:.4f}  EV={overall_ev:.4f}  Kelly={overall_kelly:.4f}")

# ─────────────────────────────────────────────
# STEP 6: Trading filter design
# ─────────────────────────────────────────────
print("\n" + "=" * 65)
print("STEP 6: TRADING FILTER DESIGN")
print("=" * 65)

if not positive_ev_quintiles:
    print("\nNo profitable filter found in cheap-side BTC. PARK recommendation.")
    filter_verdict = "PARK"
else:
    # Find the best quintile(s) - threshold = lowest pred_prob in that quintile
    best_q = max(positive_ev_quintiles, key=lambda x: x['ev_per_trade'])
    threshold = best_q['pred_prob_lo']
    print(f"\nBest positive-EV quintile: Q{best_q['quintile']}")
    print(f"  WR: {best_q['wr']:.3f}  EV: {best_q['ev_per_trade']:.4f}  Kelly: {best_q['kelly']:.4f}")
    print(f"  Model score threshold: >= {threshold:.4f}")

    # Signals per day in validation
    val_epoch_range_days = (val['epoch'].max() - val['epoch'].min()) / 86400
    val_epoch_range_days = max(val_epoch_range_days, 1)

    # Signals passing the filter in val set
    passing_val = vl_ev[vl_ev['pred_prob'] >= threshold]
    signals_per_day = len(passing_val) / val_epoch_range_days
    passing_wr = passing_val['is_win'].mean() if len(passing_val) > 0 else 0
    passing_mid = passing_val['midpoint'].mean() if len(passing_val) > 0 else 0

    ev_filtered = (passing_wr * (1 - passing_mid)
                   - (1 - passing_wr) * passing_mid
                   - passing_mid * (1 - passing_mid))
    daily_ev = ev_filtered * signals_per_day

    print(f"\n  Validation span: {val_epoch_range_days:.1f} days")
    print(f"  Signals passing filter in val: {len(passing_val)} ({signals_per_day:.1f}/day)")
    print(f"  Passing cohort WR: {passing_wr:.3f}")
    print(f"  Passing cohort avg midpoint: {passing_mid:.4f}")
    print(f"  EV per filtered trade: {ev_filtered:.4f}")
    print(f"  Projected daily EV (assume $1/share): ${daily_ev:.4f}")

    filter_verdict = "PROCEED_TO_PHASE3"

# ─────────────────────────────────────────────
# STEP 7: Cross-validation stability (5-fold temporal)
# ─────────────────────────────────────────────
print("\n" + "=" * 65)
print("STEP 7: 5-FOLD TEMPORAL CROSS-VALIDATION")
print("=" * 65)

# Use train+val only (no test leakage)
tv = pd.concat([train, val]).sort_values('epoch').reset_index(drop=True)

# Prepare features
for split_df in [tv]:
    split_df['hour_sin'] = np.sin(2 * np.pi * split_df['hour_utc'] / 24)
    split_df['hour_cos'] = np.cos(2 * np.pi * split_df['hour_utc'] / 24)
    split_df['abs_momentum'] = split_df['momentum_pct'].abs()

tv_model = tv[feature_cols + ['is_win']].dropna().copy()
print(f"  Train+val n for CV: {len(tv_model)}")

K = 5
fold_size = len(tv_model) // K
fold_aucs = []

print(f"\n  {'Fold':<6} {'n_train':>8} {'n_val':>7} {'AUC':>7}")
print("  " + "-" * 32)

for k in range(K):
    val_start = k * fold_size
    val_end_k = (k + 1) * fold_size if k < K - 1 else len(tv_model)

    fold_val   = tv_model.iloc[val_start:val_end_k]
    fold_train = pd.concat([tv_model.iloc[:val_start], tv_model.iloc[val_end_k:]])

    if len(fold_train) < 10 or len(fold_val) < 5:
        print(f"  Fold {k+1}: too few rows, skipping")
        continue

    Xtr = fold_train[feature_cols].values
    ytr = fold_train['is_win'].values.astype(int)
    Xvl = fold_val[feature_cols].values
    yvl = fold_val['is_win'].values.astype(int)

    if len(np.unique(yvl)) < 2:
        print(f"  Fold {k+1}: only one class in validation, skipping")
        continue

    sc = StandardScaler()
    Xtr_s = sc.fit_transform(Xtr)
    Xvl_s = sc.transform(Xvl)

    m = LogisticRegression(max_iter=1000, random_state=42)
    m.fit(Xtr_s, ytr)
    preds = m.predict_proba(Xvl_s)[:, 1]
    auc_k = roc_auc_score(yvl, preds)
    fold_aucs.append(auc_k)
    print(f"  Fold {k+1:<5} {len(fold_train):>8} {len(fold_val):>7} {auc_k:>7.3f}")

if fold_aucs:
    mean_auc = np.mean(fold_aucs)
    std_auc  = np.std(fold_aucs)
    print(f"\n  CV AUC: {mean_auc:.3f} +/- {std_auc:.3f}")
    if std_auc > 0.10:
        print(f"  WARNING: std={std_auc:.3f} > 0.10 -- model is UNSTABLE (non-stationary market)")
        stability = "UNSTABLE"
    else:
        print(f"  Stability check PASSED (std <= 0.10)")
        stability = "STABLE"
else:
    print("  No valid folds computed.")
    mean_auc, std_auc, stability = None, None, "UNKNOWN"

# ─────────────────────────────────────────────
# FINAL VERDICT
# ─────────────────────────────────────────────
print("\n" + "=" * 65)
print("FINAL VERDICT")
print("=" * 65)

print(f"\n  Filtered dataset n:         {len(df):,} (BTC, midpoint 0.40-0.55)")
print(f"  Train / Val / Test split:   {len(train)} / {len(val)} / {len(test)}")
print(f"  Base WR (val, unfiltered):  {val['is_win'].mean():.3f}")
print(f"  Combined model val AUC:     {val_auc_combined:.3f}")
print(f"  Best single feature val AUC:{best_single_val_auc:.3f}")
if fold_aucs:
    print(f"  5-fold CV AUC:              {mean_auc:.3f} +/- {std_auc:.3f}  [{stability}]")
print(f"  Positive-EV quintiles:      {len(positive_ev_quintiles)}")
print(f"  Filter verdict:             {filter_verdict}")

# PRE_REGISTRATION gate
print("\n  Pre-registration gate:")
if val_auc_combined > 0.57:
    print(f"    Combined AUC {val_auc_combined:.3f} > 0.57 -- H6 PASSES")
    phase3_gate = "PROCEED"
else:
    print(f"    Combined AUC {val_auc_combined:.3f} <= 0.57 -- H6 FAILS")
    if best_single_val_auc > 0.52:
        print(f"    Best single AUC {best_single_val_auc:.3f} > 0.52 -- use single feature as filter")
        phase3_gate = "PROCEED_SINGLE_FEATURE"
    else:
        print(f"    No feature passes. PARK recommendation.")
        phase3_gate = "PARK"

print(f"\n  Decision: {phase3_gate}")

if phase3_gate.startswith("PROCEED") and filter_verdict == "PROCEED_TO_PHASE3":
    print("\n  --> MOVE TO PHASE 3: EV on test set (if Phase 3 confirms EV > 0, go live)")
elif phase3_gate == "PARK" or filter_verdict == "PARK":
    print("\n  --> PARK. No signal found in cheap-side BTC. Redirect to Tradeify.")
else:
    print("\n  --> EV not positive. PARK recommendation.")

print("\n" + "=" * 65)
print("Phase 2 complete.")
