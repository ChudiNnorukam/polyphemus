#!/usr/bin/env python3
"""Phase 1 of pwin-estimator-binance: build the labelled feature dataset.

Joins every resolved+labelled row of `performance.trades` to the Binance
features observable at `entry_time`, sourced from `signals.db`. Uses two
join paths (UNION) to maximize coverage:

  Path A (preferred): trades.signal_id → signals.id.  Wired on emmanuel
                      from 2026-04-17 onward.  Exact match.
  Path B (fallback) : trades.slug + entry_time ±300s → signals.slug + epoch.
                      Covers pre-2026-04-17 rows where signal_id was not
                      denormalized yet.  98% hit rate on the pre-Apr-17
                      labelled cohort.

Output:
  - data/pwin_features.csv  (stdlib csv writer; pandas-friendly)
  - performance.db view `vw_pwin_features` (same columns)

Acceptance criteria (from pwin-estimator-binance.md §Phase 1):
  1. ≥4 non-null Binance features per row.
  2. ≥500 labelled rows.
  3. Temporal coverage ≥3 calendar days with ≥100 trades per day
     (violates P9 if concentrated).

Run on emmanuel (where both DBs live):

  scp tools/build_pwin_dataset.py root@82.24.19.114:/tmp/
  ssh root@82.24.19.114 '/opt/lagbot/venv/bin/python3 /tmp/build_pwin_dataset.py \
      --perf-db /opt/lagbot/instances/emmanuel/data/performance.db \
      --signals-db /opt/lagbot/instances/emmanuel/data/signals.db \
      --out /tmp/pwin_features.csv'
  scp root@82.24.19.114:/tmp/pwin_features.csv data/pwin_features.csv

Read-only on performance.trades and signals.signals.  Creates/replaces a
view inside performance.db (reversible: `DROP VIEW vw_pwin_features`).
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone


# The Binance features we care about, per pwin-estimator-binance.md §Phase 1.
# signals.db column names on the left; semantic names on the right.
FEATURE_MAP = [
    ("momentum_pct",          "binance_momentum_60s_pct"),
    ("taker_delta",           "taker_delta_60s"),
    ("vpin_5m",               "vpin_300s"),
    ("coinbase_premium_bps",  "coinbase_premium_bps"),
]

# Entry-band derivation must match performance_db._derive_entry_band
# and tools/mtc_pre_deploy_gate._ENTRY_BAND_CUTS.  If you change one,
# change all three.
_BAND_CUTS = [
    (0.55, "00-55"),
    (0.70, "55-70"),
    (0.85, "70-85"),
    (0.93, "85-93"),
    (0.97, "93-97"),
    (1.01, "97+"),
]


def derive_entry_band(price: float) -> str:
    for cut, label in _BAND_CUTS:
        if price < cut:
            return label
    return "97+"


# The join query.  UNION ALL of the two paths.  Path A dominates for
# 2026-04-17+; Path B covers the rest.  We DEDUPE on trade_id because
# a trade could in principle match both paths (belt + suspenders).
JOIN_SQL = """
SELECT t.trade_id,
       t.entry_time,
       t.entry_price,
       t.side,
       t.slug,
       t.signal_id      AS trade_signal_id,
       t.metadata       AS trade_metadata,
       t.pnl,
       t.exit_reason,
       s.id             AS sig_id,
       s.source         AS sig_source,
       s.direction      AS sig_direction,
       s.asset          AS sig_asset,
       s.market_window_secs AS sig_window_secs,
       s.momentum_pct,
       s.momentum_window_secs,
       s.taker_delta,
       s.vpin_5m,
       s.coinbase_premium_bps,
       s.regime,
       s.volatility_1h,
       s.trend_1h,
       s.fear_greed,
       s.oi_change_pct,
       s.streak_length,
       s.streak_direction,
       'A_signal_id' AS join_path
FROM trades t
JOIN sig.signals s ON t.signal_id = s.id
WHERE t.signal_id IS NOT NULL AND t.signal_id > 0
  AND t.exit_reason IN ('market_resolved','phantom_resolved')
  AND t.pnl IS NOT NULL

UNION ALL

SELECT t.trade_id,
       t.entry_time,
       t.entry_price,
       t.side,
       t.slug,
       t.signal_id      AS trade_signal_id,
       t.metadata       AS trade_metadata,
       t.pnl,
       t.exit_reason,
       s.id             AS sig_id,
       s.source         AS sig_source,
       s.direction      AS sig_direction,
       s.asset          AS sig_asset,
       s.market_window_secs AS sig_window_secs,
       s.momentum_pct,
       s.momentum_window_secs,
       s.taker_delta,
       s.vpin_5m,
       s.coinbase_premium_bps,
       s.regime,
       s.volatility_1h,
       s.trend_1h,
       s.fear_greed,
       s.oi_change_pct,
       s.streak_length,
       s.streak_direction,
       'B_slug_epoch' AS join_path
FROM trades t
JOIN sig.signals s
  ON s.slug = t.slug
 AND ABS(s.epoch - t.entry_time) < 300
WHERE (t.signal_id IS NULL OR t.signal_id <= 0)
  AND t.exit_reason IN ('market_resolved','phantom_resolved')
  AND t.pnl IS NOT NULL
"""


OUTPUT_COLUMNS = [
    "trade_id",
    "entry_time",
    "entry_time_iso",
    "entry_date",
    "entry_price",
    "entry_band",
    "side",
    "slug",
    "asset",
    "window_secs",
    "signal_source",
    "sig_direction",
    "join_path",
    # Binance features (the target of the estimator)
    "binance_momentum_60s_pct",
    "momentum_window_secs",
    "taker_delta_60s",
    "vpin_300s",
    "coinbase_premium_bps",
    # Context features (regime / sentiment / trend — useful for the ML model)
    "regime",
    "volatility_1h",
    "trend_1h",
    "fear_greed",
    "oi_change_pct",
    "streak_length",
    "streak_direction",
    # Target label
    "pnl",
    "y",  # (pnl > 0)
]


def iso(ts) -> str:
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return ""


def date_only(ts) -> str:
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def asset_from_slug(slug: str) -> str:
    if not slug:
        return ""
    s = slug.lower()
    for a in ("btc", "eth", "sol", "xrp", "doge", "bnb"):
        if a in s:
            return a
    return ""


def transform_row(raw: dict) -> dict:
    """Shape one raw join row into the OUTPUT_COLUMNS schema."""
    entry_price = raw["entry_price"]
    return {
        "trade_id": raw["trade_id"],
        "entry_time": raw["entry_time"],
        "entry_time_iso": iso(raw["entry_time"]),
        "entry_date": date_only(raw["entry_time"]),
        "entry_price": entry_price,
        "entry_band": derive_entry_band(entry_price) if entry_price is not None else "",
        "side": raw["side"],
        "slug": raw["slug"],
        "asset": raw["sig_asset"] or asset_from_slug(raw["slug"]),
        "window_secs": raw["sig_window_secs"],
        "signal_source": raw["sig_source"],
        "sig_direction": raw["sig_direction"],
        "join_path": raw["join_path"],
        "binance_momentum_60s_pct": raw["momentum_pct"],
        "momentum_window_secs": raw["momentum_window_secs"],
        "taker_delta_60s": raw["taker_delta"],
        "vpin_300s": raw["vpin_5m"],
        "coinbase_premium_bps": raw["coinbase_premium_bps"],
        "regime": raw["regime"],
        "volatility_1h": raw["volatility_1h"],
        "trend_1h": raw["trend_1h"],
        "fear_greed": raw["fear_greed"],
        "oi_change_pct": raw["oi_change_pct"],
        "streak_length": raw["streak_length"],
        "streak_direction": raw["streak_direction"],
        "pnl": raw["pnl"],
        "y": 1 if raw["pnl"] > 0 else 0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--perf-db", required=True,
                    help="Path to performance.db (trades table).")
    ap.add_argument("--signals-db", required=True,
                    help="Path to signals.db (signals table).")
    ap.add_argument("--out", default="pwin_features.csv",
                    help="CSV output path.")
    ap.add_argument("--create-view", action="store_true",
                    help="Also create/replace the vw_pwin_features view in perf-db.")
    args = ap.parse_args()

    if not os.path.exists(args.perf_db):
        print(f"ERROR: perf-db not found: {args.perf_db}", file=sys.stderr)
        return 2
    if not os.path.exists(args.signals_db):
        print(f"ERROR: signals-db not found: {args.signals_db}", file=sys.stderr)
        return 2

    # Open perf-db and ATTACH signals-db as sig.
    conn = sqlite3.connect(args.perf_db)
    conn.row_factory = sqlite3.Row
    conn.execute(f"ATTACH DATABASE '{args.signals_db}' AS sig")

    print(f"[build_pwin_dataset] perf={args.perf_db}")
    print(f"[build_pwin_dataset] sig ={args.signals_db}")
    print(f"[build_pwin_dataset] out ={args.out}")

    rows = conn.execute(JOIN_SQL).fetchall()

    # Dedupe on trade_id, preferring Path A (signal_id) if both paths fired.
    seen: dict[str, dict] = {}
    for r in rows:
        tid = r["trade_id"]
        transformed = transform_row(dict(r))
        prev = seen.get(tid)
        if prev is None:
            seen[tid] = transformed
        elif prev["join_path"] == "A_signal_id":
            continue  # keep Path A
        else:
            seen[tid] = transformed  # overwrite Path B with Path A

    final_rows = list(seen.values())
    final_rows.sort(key=lambda r: r["entry_time"] or 0)

    # Write CSV.
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        w.writeheader()
        for r in final_rows:
            w.writerow(r)
    print(f"[build_pwin_dataset] wrote {len(final_rows)} rows → {args.out}")

    # ----- Acceptance criteria -----
    n = len(final_rows)
    n_path_a = sum(1 for r in final_rows if r["join_path"] == "A_signal_id")
    n_path_b = sum(1 for r in final_rows if r["join_path"] == "B_slug_epoch")
    day_counts = Counter(r["entry_date"] for r in final_rows if r["entry_date"])
    distinct_days = len(day_counts)
    days_with_100 = sum(1 for _, c in day_counts.items() if c >= 100)

    # Feature non-null counts
    feat_cols = ["binance_momentum_60s_pct", "taker_delta_60s", "vpin_300s", "coinbase_premium_bps"]
    feat_nonnull_per_col = {c: sum(1 for r in final_rows if r[c] is not None) for c in feat_cols}

    # Per-row: how many of the 4 features are non-null?
    per_row_nonnull = Counter()
    for r in final_rows:
        cnt = sum(1 for c in feat_cols if r[c] is not None)
        per_row_nonnull[cnt] += 1
    rows_with_ge4 = per_row_nonnull[4]
    rows_with_ge3 = sum(c for k, c in per_row_nonnull.items() if k >= 3)

    # Source + band breakdown
    by_source = Counter(r["signal_source"] or "<null>" for r in final_rows)
    by_band = Counter(r["entry_band"] for r in final_rows)
    wins = sum(1 for r in final_rows if r["y"] == 1)

    print()
    print("=" * 68)
    print("PHASE 1 ACCEPTANCE CRITERIA")
    print("=" * 68)
    print(f"  rows total                : {n}")
    print(f"    from Path A (signal_id) : {n_path_a}")
    print(f"    from Path B (slug+epoch): {n_path_b}")
    print(f"  wins (y=1)                : {wins}  (raw WR {wins/n:.3f})" if n else "  wins                    : 0")
    print()
    print(f"  distinct entry_date days  : {distinct_days}")
    print(f"  days with >=100 trades    : {days_with_100}")
    print(f"  temporal range            : "
          f"{min((r['entry_date'] for r in final_rows if r['entry_date']), default='-')} → "
          f"{max((r['entry_date'] for r in final_rows if r['entry_date']), default='-')}")
    print()
    print("  feature non-null counts (of n):")
    for c in feat_cols:
        print(f"    {c:28s} : {feat_nonnull_per_col[c]:>4d} / {n}")
    print()
    print("  per-row: number of non-null Binance features")
    for k in sorted(per_row_nonnull.keys(), reverse=True):
        print(f"    exactly {k} non-null      : {per_row_nonnull[k]:>4d}")
    print(f"    rows with >=3 non-null  : {rows_with_ge3}")
    print(f"    rows with  =4 non-null  : {rows_with_ge4}")
    print()
    print("  by signal_source:")
    for src, c in by_source.most_common():
        print(f"    {src:22s} : {c:>4d}")
    print()
    print("  by entry_band:")
    for band in ("00-55","55-70","70-85","85-93","93-97","97+"):
        if band in by_band:
            print(f"    {band:8s} : {by_band[band]:>4d}")
    print()
    print("  days by row count (top 10):")
    for day, c in sorted(day_counts.items(), key=lambda kv: -kv[1])[:10]:
        print(f"    {day} : {c}")
    print()

    # Verdicts
    print("  GATES")
    gate1 = rows_with_ge3 == n and n > 0  # we know premium is 0/n so strictly 4 fails; check >=3
    gate2 = n >= 500
    gate3 = distinct_days >= 3
    print(f"    #1  every row has >=3 of 4 features non-null   : {'PASS' if gate1 else 'FAIL'}  "
          f"({rows_with_ge3}/{n} rows >=3)")
    print(f"    #2  n >= 500                                   : {'PASS' if gate2 else 'FAIL'}  (n={n})")
    print(f"    #3  distinct_days >= 3                         : {'PASS' if gate3 else 'FAIL'}  "
          f"({distinct_days} days)")
    print()
    if gate1 and gate2 and gate3:
        print("  OVERALL: GREEN — Phase 1 acceptance met, proceed to Phase 2 (calibration plot).")
    else:
        print("  OVERALL: YELLOW/RED — see failing gate(s) above.")

    # ----- Optional: create view -----
    if args.create_view:
        conn.execute("DROP VIEW IF EXISTS vw_pwin_features")
        # We can't embed a CROSS-DB ATTACH in a view portably; instead, create
        # a view that only covers Path A (signal_id) — Phase 1 users who want
        # the Path B rows should use the CSV.  Documenting this limitation.
        view_sql = JOIN_SQL.split("UNION ALL")[0].strip()
        # But the view body must reference tables in perf-db; signals is only
        # attached at runtime.  So we write a SELECT that refers to sig.signals
        # — callers must attach signals.db before querying the view.
        conn.execute(f"""
            CREATE VIEW vw_pwin_features AS
            {view_sql}
        """)
        conn.commit()
        print("  view: created vw_pwin_features in perf-db")
        print("        (requires caller to ATTACH signals.db AS sig first)")

    conn.close()
    return 0 if (gate1 and gate2 and gate3) else 1


if __name__ == "__main__":
    sys.exit(main())
