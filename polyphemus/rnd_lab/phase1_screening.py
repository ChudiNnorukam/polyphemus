"""
Phase 1 Feature Screening - Pre-Registered R&D Lab
Polymarket Polyphemus / Emmanuel signals

Protocol: PRE_REGISTRATION.md (locked 2026-04-11)
Test set is NOT touched in this phase.
"""

import sqlite3
import numpy as np
import pandas as pd
from scipy.stats import chi2_contingency
from sklearn.metrics import roc_auc_score
import warnings
warnings.filterwarnings('ignore')

DB_PATH = '/Users/chudinnorukam/Projects/business/polyphemus/rnd_lab/data/combined_signals.db'

# ── 1. Load data ──────────────────────────────────────────────────────────────
conn = sqlite3.connect(DB_PATH)
df = pd.read_sql('SELECT * FROM analysis ORDER BY epoch ASC', conn)
conn.close()

print(f"Total rows: {len(df):,}")
print(f"Columns: {list(df.columns)}")
print(f"is_win distribution: {df['is_win'].value_counts().to_dict()}")
print(f"Base WR (overall): {df['is_win'].mean():.4f}")
print()

# ── 2. Temporal Train / Val / Test Split ──────────────────────────────────────
n = len(df)
train_end = int(n * 0.70)
val_end   = int(n * 0.85)

df = df.reset_index(drop=True)
train = df.iloc[:train_end].copy()
val   = df.iloc[train_end:val_end].copy()
test  = df.iloc[val_end:].copy()

def epoch_to_dt(e):
    return pd.Timestamp(e, unit='s', tz='UTC')

print("=" * 60)
print("STEP 1 – TEMPORAL SPLIT")
print("=" * 60)
print(f"Train : n={len(train):,} | {epoch_to_dt(train['epoch'].min())} -> {epoch_to_dt(train['epoch'].max())}")
print(f"Val   : n={len(val):,}   | {epoch_to_dt(val['epoch'].min())} -> {epoch_to_dt(val['epoch'].max())}")
print(f"Test  : n={len(test):,}  | {epoch_to_dt(test['epoch'].min())} -> {epoch_to_dt(test['epoch'].max())}")
print(f"Train WR: {train['is_win'].mean():.4f} | Val WR: {val['is_win'].mean():.4f} | Test WR: {val['is_win'].mean():.4f} (test not revealed)")
print()

# ── Helpers ───────────────────────────────────────────────────────────────────
def auc(y_true, y_score):
    """Compute AUC; flip if < 0.5 (direction agnostic for single-feature test)."""
    if len(y_true) == 0 or y_true.nunique() < 2:
        return float('nan')
    a = roc_auc_score(y_true, y_score)
    return max(a, 1 - a)  # allow negative correlation to count

def chi2_p(obs):
    chi2, p, dof, _ = chi2_contingency(obs)
    return chi2, p, dof

# ── H1: Hour of Day ───────────────────────────────────────────────────────────
print("=" * 60)
print("STEP 2 – H1: Hour of day (chi-squared)")
print("=" * 60)

# WR by hour on train
wr_hour_train = train.groupby('hour_utc')['is_win'].agg(['mean','count']).rename(columns={'mean':'wr','count':'n'})
wr_hour_train = wr_hour_train.reindex(range(24), fill_value=np.nan)

print("\nHour  WR       n")
for h in range(24):
    row = wr_hour_train.loc[h]
    flag = " <-- WR>70% n>=50" if (row['wr'] > 0.70 and row['n'] >= 50) else ""
    print(f"  {h:02d}  {row['wr']:.3f}   {int(row['n']) if not np.isnan(row['n']) else 0}{flag}")

# Chi-squared test
ct = pd.crosstab(train['hour_utc'], train['is_win'])
chi2_h1, p_h1, dof_h1 = chi2_p(ct.values)
print(f"\nChi2={chi2_h1:.2f}, dof={dof_h1}, p={p_h1:.6f}")

hours_above_70_train = wr_hour_train[(wr_hour_train['wr'] > 0.70) & (wr_hour_train['n'] >= 50)].index.tolist()
print(f"Hours with WR>70% n>=50 (train): {hours_above_70_train}")

# Validation: same hours
print("\nValidation – same hours WR:")
if hours_above_70_train:
    for h in hours_above_70_train:
        sub = val[val['hour_utc'] == h]
        wr = sub['is_win'].mean() if len(sub) > 0 else float('nan')
        print(f"  Hour {h:02d}: WR={wr:.3f} n={len(sub)}")
else:
    print("  No hours met the threshold on train.")

h1_pass = (p_h1 < 0.01) and (len(hours_above_70_train) > 0)
print(f"\nH1 threshold met? {'YES' if h1_pass else 'NO'} (need p<0.01 AND at least one hour WR>70% n>=50)")
print(f"  p={p_h1:.6f}, qualifying hours={hours_above_70_train}")

# ── H2: Time Remaining ────────────────────────────────────────────────────────
print()
print("=" * 60)
print("STEP 3 – H2: Time remaining")
print("=" * 60)

auc_h2_train = auc(train['is_win'], train['time_remaining_secs'])
auc_h2_val   = auc(val['is_win'],   val['time_remaining_secs'])
h2_pass = auc_h2_val > 0.55
print(f"Train AUC: {auc_h2_train:.4f}")
print(f"Val AUC:   {auc_h2_val:.4f}")
print(f"H2 threshold met? {'YES' if h2_pass else 'NO'} (need Val AUC > 0.55)")

# ── H3: Midpoint ──────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("STEP 4 – H3: Midpoint")
print("=" * 60)

auc_h3_train = auc(train['is_win'], train['midpoint'])
auc_h3_val   = auc(val['is_win'],   val['midpoint'])
h3_pass = auc_h3_val > 0.55

# WR by bucket
bins   = [0.0, 0.3, 0.5, 0.7, 0.9, 1.0]
labels = ['0.0-0.3','0.3-0.5','0.5-0.7','0.7-0.9','0.9-1.0']
train['mid_bucket'] = pd.cut(train['midpoint'], bins=bins, labels=labels, include_lowest=True)
val['mid_bucket']   = pd.cut(val['midpoint'],   bins=bins, labels=labels, include_lowest=True)

print("\nMidpoint bucket WR (train):")
gb = train.groupby('mid_bucket', observed=True)['is_win'].agg(['mean','count'])
for idx, row in gb.iterrows():
    print(f"  {idx}: WR={row['mean']:.3f} n={int(row['count'])}")

print("\nMidpoint bucket WR (val):")
gb_v = val.groupby('mid_bucket', observed=True)['is_win'].agg(['mean','count'])
for idx, row in gb_v.iterrows():
    print(f"  {idx}: WR={row['mean']:.3f} n={int(row['count'])}")

print(f"\nTrain AUC: {auc_h3_train:.4f}")
print(f"Val AUC:   {auc_h3_val:.4f}")
print(f"H3 threshold met? {'YES' if h3_pass else 'NO'} (need Val AUC > 0.55)")

# ── H4: Momentum magnitude ────────────────────────────────────────────────────
print()
print("=" * 60)
print("STEP 5 – H4: Momentum magnitude")
print("=" * 60)

tr_mom = train.dropna(subset=['momentum_pct']).copy()
va_mom = val.dropna(subset=['momentum_pct']).copy()
tr_mom['abs_mom'] = tr_mom['momentum_pct'].abs()
va_mom['abs_mom'] = va_mom['momentum_pct'].abs()

auc_h4_train = auc(tr_mom['is_win'], tr_mom['abs_mom'])
auc_h4_val   = auc(va_mom['is_win'], va_mom['abs_mom'])
h4_pass = auc_h4_val > 0.55
print(f"Train rows (non-null): {len(tr_mom):,} | Val rows: {len(va_mom):,}")
print(f"Train AUC: {auc_h4_train:.4f}")
print(f"Val AUC:   {auc_h4_val:.4f}")
print(f"H4 threshold met? {'YES' if h4_pass else 'NO'} (need Val AUC > 0.55)")

# ── H5: Volatility ────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("STEP 6 – H5: Volatility")
print("=" * 60)

tr_vol = train.dropna(subset=['volatility_1h']).copy()
va_vol = val.dropna(subset=['volatility_1h']).copy()

auc_h5_train = auc(tr_vol['is_win'], tr_vol['volatility_1h'])
auc_h5_val   = auc(va_vol['is_win'], va_vol['volatility_1h'])

# Regime chi-squared (train)
tr_reg = train.dropna(subset=['regime'])
if tr_reg['is_win'].nunique() > 1 and tr_reg['regime'].nunique() > 1:
    ct_reg = pd.crosstab(tr_reg['regime'], tr_reg['is_win'])
    chi2_reg, p_reg, dof_reg = chi2_p(ct_reg.values)
else:
    chi2_reg, p_reg, dof_reg = float('nan'), float('nan'), 0

h5_pass = (auc_h5_val > 0.55) or (p_reg < 0.01)
print(f"Train rows (non-null vol): {len(tr_vol):,} | Val rows: {len(va_vol):,}")
print(f"Train AUC (vol): {auc_h5_train:.4f}")
print(f"Val AUC (vol):   {auc_h5_val:.4f}")
print(f"\nRegime chi2={chi2_reg:.2f}, p={p_reg:.6f} (train)")
print("\nRegime WR (train):")
rg = tr_reg.groupby('regime')['is_win'].agg(['mean','count'])
for idx, row in rg.iterrows():
    print(f"  {idx}: WR={row['mean']:.3f} n={int(row['count'])}")
print(f"\nH5 threshold met? {'YES' if h5_pass else 'NO'} (need vol AUC>0.55 OR regime p<0.01)")

# ── H7: Signal score (Polyphemus only) ────────────────────────────────────────
print()
print("=" * 60)
print("STEP 7 – H7: Signal score (Polyphemus only)")
print("=" * 60)

poly_df = df[df['instance'] == 'polyphemus'].dropna(subset=['signal_score']).copy().reset_index(drop=True)
n_p = len(poly_df)
p_train_end = int(n_p * 0.70)
p_val_end   = int(n_p * 0.85)
p_train = poly_df.iloc[:p_train_end]
p_val   = poly_df.iloc[p_train_end:p_val_end]

print(f"Polyphemus signal_score rows: {n_p:,}")
print(f"  p_train: n={len(p_train):,} | p_val: n={len(p_val):,}")

if len(p_train) > 0 and p_train['is_win'].nunique() > 1:
    auc_h7_train = auc(p_train['is_win'], p_train['signal_score'])
else:
    auc_h7_train = float('nan')

if len(p_val) > 0 and p_val['is_win'].nunique() > 1:
    auc_h7_val = auc(p_val['is_win'], p_val['signal_score'])
else:
    auc_h7_val = float('nan')

h7_pass = (not np.isnan(auc_h7_val)) and (auc_h7_val > 0.55)
print(f"Train AUC: {auc_h7_train:.4f}" if not np.isnan(auc_h7_train) else "Train AUC: N/A")
print(f"Val AUC:   {auc_h7_val:.4f}" if not np.isnan(auc_h7_val) else "Val AUC: N/A (insufficient data)")
print(f"H7 threshold met? {'YES' if h7_pass else 'NO'} (need Val AUC > 0.55)")

# ── Summary ───────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("SUMMARY TABLE")
print("=" * 60)
print(f"{'Hypothesis':<12} {'Feature':<22} {'Train AUC/Chi2':<18} {'Val AUC/p':<18} {'Pass?'}")
print("-" * 85)

rows = [
    ("H1",  "hour_utc",        f"chi2={chi2_h1:.2f}",      f"p={p_h1:.5f}",       "YES" if h1_pass else "NO"),
    ("H2",  "time_remaining",  f"{auc_h2_train:.4f}",       f"{auc_h2_val:.4f}",   "YES" if h2_pass else "NO"),
    ("H3",  "midpoint",        f"{auc_h3_train:.4f}",       f"{auc_h3_val:.4f}",   "YES" if h3_pass else "NO"),
    ("H4",  "abs(momentum)",   f"{auc_h4_train:.4f}",       f"{auc_h4_val:.4f}",   "YES" if h4_pass else "NO"),
    ("H5",  "volatility_1h",   f"{auc_h5_train:.4f}",       f"{auc_h5_val:.4f}",   "YES" if h5_pass else "NO"),
    ("H7",  "signal_score",    f"{auc_h7_train:.4f}" if not np.isnan(auc_h7_train) else "N/A",
                               f"{auc_h7_val:.4f}" if not np.isnan(auc_h7_val) else "N/A",
                               "YES" if h7_pass else "NO"),
]

passing = []
for r in rows:
    print(f"{r[0]:<12} {r[1]:<22} {r[2]:<18} {r[3]:<18} {r[4]}")
    if r[4] == "YES":
        passing.append(r[0])

print()
print(f"Passing hypotheses: {passing if passing else 'NONE'}")
print()

if passing:
    print("PHASE 1 VERDICT: At least one feature passed AUC > 0.55.")
    print("Proceed to Phase 2 (combined model) with features:", passing)
else:
    print("PHASE 1 VERDICT: NO feature passed AUC > 0.55 gate.")
    print("Per pre-registration protocol: PARK Polymarket R&D. Redirect to Tradeify.")

print()
print("NOTE: Test set was NOT examined in this analysis.")
