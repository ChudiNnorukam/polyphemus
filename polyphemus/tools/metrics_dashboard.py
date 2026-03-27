"""Comprehensive metrics dashboard for Polyphemus trading bots.

Computes all 35 tracked metrics from performance.db and signals.db.
Run: python3 metrics_dashboard.py [--instance emmanuel] [--days 7] [--price-min 0.30] [--price-max 0.50]
"""

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path


def query(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def confidence_label(n):
    if n < 30: return f"ANECDOTAL (n={n})"
    if n < 107: return f"LOW (n={n})"
    if n < 385: return f"MODERATE (n={n})"
    return f"SIGNIFICANT (n={n})"


def compute_kelly(wr, avg_win, avg_loss):
    if avg_loss == 0: return 0
    b = avg_win / avg_loss
    return (wr * b - (1 - wr)) / b


def run_metrics(perf_db, signals_db, days=7, price_min=0.30, price_max=0.50, asset_filter=None):
    time_filter = f"entry_time > strftime('%s', 'now', '-{days} days')"
    price_filter = f"entry_price >= {price_min} AND entry_price <= {price_max}"
    slug_filter = "slug LIKE '%5m%'"
    asset_clause = ""
    if asset_filter:
        asset_clause = f"AND UPPER(SUBSTR(slug, 1, INSTR(slug,'-')-1)) = '{asset_filter.upper()}'"

    where = f"exit_time IS NOT NULL AND {time_filter} AND {price_filter} AND {slug_filter} {asset_clause}"

    print("=" * 70)
    print(f"POLYPHEMUS METRICS DASHBOARD")
    print(f"Range: last {days} days | Entry: ${price_min}-${price_max} | Asset: {asset_filter or 'ALL'}")
    print("=" * 70)

    # --- 1-5: Core metrics ---
    rows = query(perf_db, f"""
        SELECT COUNT(*) as n,
            SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
            SUM(pnl) as total_pnl,
            AVG(pnl) as ev,
            AVG(CASE WHEN pnl > 0 THEN pnl END) as avg_win,
            AVG(CASE WHEN pnl <= 0 THEN pnl END) as avg_loss,
            AVG(CASE WHEN pnl > 0 THEN pnl END) / NULLIF(ABS(AVG(CASE WHEN pnl <= 0 THEN pnl END)), 0) as payoff
        FROM trades WHERE {where}
    """)
    r = rows[0]
    n = r['n'] or 0
    if n == 0:
        print("\nNo trades found in this range. Adjust filters.")
        return

    wins = r['wins'] or 0
    wr = wins / n if n > 0 else 0
    avg_win = r['avg_win'] or 0
    avg_loss = abs(r['avg_loss'] or 0)
    payoff = r['payoff'] or 0
    kelly = compute_kelly(wr, avg_win, avg_loss)
    ev = r['ev'] or 0
    total_pnl = r['total_pnl'] or 0

    print(f"\n{'='*40}")
    print(f"  CORE METRICS ({confidence_label(n)})")
    print(f"{'='*40}")
    print(f"  1. Win Rate:        {wr*100:.1f}% ({wins}W / {n-wins}L)")
    print(f"  2. Total P&L:       ${total_pnl:.2f}")
    print(f"  3. EV per Trade:    ${ev:.4f}")
    print(f"  4. Avg Win:         ${avg_win:.2f}")
    print(f"     Avg Loss:        -${avg_loss:.2f}")
    print(f"  5. Payoff Ratio:    {payoff:.2f}x")
    print(f"  6. Kelly Criterion: {kelly:.4f} {'(EDGE)' if kelly > 0 else '(NO EDGE)'}")

    # --- 7: Profit Factor ---
    pf_rows = query(perf_db, f"""
        SELECT SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) as gross_w,
               ABS(SUM(CASE WHEN pnl <= 0 THEN pnl ELSE 0 END)) as gross_l
        FROM trades WHERE {where}
    """)
    gross_w = pf_rows[0]['gross_w'] or 0
    gross_l = pf_rows[0]['gross_l'] or 1
    pf = gross_w / gross_l
    print(f"  7. Profit Factor:   {pf:.3f} (${gross_w:.0f}W / ${gross_l:.0f}L)")

    # --- 8: Max Drawdown ---
    dd_rows = query(perf_db, f"""
        WITH cum AS (
            SELECT entry_time, SUM(pnl) OVER (ORDER BY entry_time) as cum_pnl
            FROM trades WHERE {where}
        ),
        peaks AS (
            SELECT cum_pnl, MAX(cum_pnl) OVER (ORDER BY entry_time) as peak FROM cum
        )
        SELECT MIN(cum_pnl - peak) as max_dd, MAX(cum_pnl) as peak_pnl FROM peaks
    """)
    max_dd = dd_rows[0]['max_dd'] or 0
    print(f"  8. Max Drawdown:    ${max_dd:.2f}")

    # --- 9: Risk of Ruin ---
    if kelly > 0:
        edge = (wr * payoff - (1 - wr)) / payoff
        ror = ((1 - edge) / (1 + edge)) ** 413  # assume $413 bankroll / $1 bet
        print(f"  9. Risk of Ruin:    {ror*100:.6f}%")
    else:
        print(f"  9. Risk of Ruin:    100% (negative Kelly)")

    # --- 10-11: Streak / Clustering ---
    streak_rows = query(perf_db, f"""
        WITH ordered AS (
            SELECT pnl, LAG(pnl) OVER (ORDER BY entry_time) as prev_pnl
            FROM trades WHERE {where}
        )
        SELECT CASE WHEN prev_pnl > 0 THEN 'after_win' ELSE 'after_loss' END as ctx,
            COUNT(*) as n,
            ROUND(100.0*SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) as wr
        FROM ordered WHERE prev_pnl IS NOT NULL GROUP BY ctx
    """)
    print(f"\n{'='*40}")
    print(f"  CLUSTERING")
    print(f"{'='*40}")
    for s in streak_rows:
        print(f"  10. {s['ctx']}: {s['wr']}% WR (n={s['n']})")

    # --- 12: Entry Price Buckets ---
    bucket_rows = query(perf_db, f"""
        SELECT CASE
            WHEN entry_price < 0.35 THEN '30-35c'
            WHEN entry_price < 0.40 THEN '35-40c'
            WHEN entry_price < 0.45 THEN '40-45c'
            ELSE '45-50c' END as bucket,
            COUNT(*) as n,
            SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) as wins,
            ROUND(100.0*SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) as wr,
            ROUND(SUM(pnl),2) as pnl
        FROM trades WHERE {where}
        GROUP BY bucket ORDER BY bucket
    """)
    print(f"\n{'='*40}")
    print(f"  ENTRY PRICE BUCKETS")
    print(f"{'='*40}")
    for b in bucket_rows:
        marker = "+" if (b['pnl'] or 0) > 0 else "-"
        print(f"  {b['bucket']}: {b['wr']}% WR, ${b['pnl']} P&L (n={b['n']}) [{marker}]")

    # --- 13: Direction ---
    dir_rows = query(perf_db, f"""
        SELECT COALESCE(LOWER(json_extract(metadata, '$.direction')), 'unknown') as dir,
            COUNT(*) as n,
            ROUND(100.0*SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) as wr,
            ROUND(SUM(pnl),2) as pnl
        FROM trades WHERE {where} GROUP BY dir
    """)
    print(f"\n{'='*40}")
    print(f"  DIRECTION")
    print(f"{'='*40}")
    for d in dir_rows:
        print(f"  {d['dir']}: {d['wr']}% WR, ${d['pnl']} (n={d['n']})")

    # --- 14: Asset breakdown ---
    asset_rows = query(perf_db, f"""
        SELECT UPPER(SUBSTR(slug, 1, INSTR(slug,'-')-1)) as asset,
            COUNT(*) as n,
            ROUND(100.0*SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) as wr,
            ROUND(SUM(pnl),2) as pnl
        FROM trades WHERE {where} GROUP BY asset ORDER BY pnl DESC
    """)
    print(f"\n{'='*40}")
    print(f"  BY ASSET")
    print(f"{'='*40}")
    for a in asset_rows:
        print(f"  {a['asset']}: {a['wr']}% WR, ${a['pnl']} (n={a['n']})")

    # --- 15: Hour of Day ---
    hour_rows = query(perf_db, f"""
        SELECT CAST(strftime('%H', entry_time, 'unixepoch') AS INT) as hr,
            COUNT(*) as n,
            ROUND(100.0*SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) as wr,
            ROUND(SUM(pnl),2) as pnl
        FROM trades WHERE {where} GROUP BY hr ORDER BY hr
    """)
    print(f"\n{'='*40}")
    print(f"  BY HOUR (UTC)")
    print(f"{'='*40}")
    for h in hour_rows:
        marker = "+" if (h['pnl'] or 0) > 0 else " "
        print(f"  {h['hr']:02d}:00  {h['wr']:5.1f}% WR  ${h['pnl']:>8.2f}  n={h['n']} {marker}")

    # --- 16: Exit Reason ---
    exit_rows = query(perf_db, f"""
        SELECT exit_reason, COUNT(*) as n,
            ROUND(100.0*SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) as wr,
            ROUND(SUM(pnl),2) as pnl
        FROM trades WHERE {where} GROUP BY exit_reason ORDER BY n DESC
    """)
    print(f"\n{'='*40}")
    print(f"  EXIT REASONS")
    print(f"{'='*40}")
    for e in exit_rows:
        print(f"  {e['exit_reason']:25s} {e['wr']:5.1f}% WR  ${e['pnl']:>8.2f}  n={e['n']}")

    # --- 17: Entry Timing ---
    timing_rows = query(perf_db, f"""
        SELECT CASE
            WHEN (entry_time - CAST(SUBSTR(slug,-10) AS INT)) < 90 THEN 'early(0-90s)'
            WHEN (entry_time - CAST(SUBSTR(slug,-10) AS INT)) < 180 THEN 'mid(90-180s)'
            ELSE 'late(180s+)' END as timing,
            COUNT(*) as n,
            ROUND(100.0*SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) as wr,
            ROUND(SUM(pnl),2) as pnl
        FROM trades WHERE {where} GROUP BY timing ORDER BY timing
    """)
    print(f"\n{'='*40}")
    print(f"  ENTRY TIMING")
    print(f"{'='*40}")
    for t in timing_rows:
        print(f"  {t['timing']:15s} {t['wr']:5.1f}% WR  ${t['pnl']:>8.2f}  n={t['n']}")

    # --- 18: Hold Duration ---
    hold_rows = query(perf_db, f"""
        SELECT CASE
            WHEN (exit_time - entry_time)/60 < 2 THEN '<2m'
            WHEN (exit_time - entry_time)/60 < 4 THEN '2-4m'
            ELSE '4m+' END as hold,
            COUNT(*) as n,
            ROUND(100.0*SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) as wr,
            ROUND(SUM(pnl),2) as pnl
        FROM trades WHERE {where} GROUP BY hold ORDER BY hold
    """)
    print(f"\n{'='*40}")
    print(f"  HOLD DURATION")
    print(f"{'='*40}")
    for h in hold_rows:
        print(f"  {h['hold']:10s} {h['wr']:5.1f}% WR  ${h['pnl']:>8.2f}  n={h['n']}")

    # --- 19: Signal Microstructure (from signals.db) ---
    if Path(signals_db).exists():
        sig_rows = query(signals_db, """
            SELECT
                COUNT(*) as total_signals,
                SUM(CASE WHEN guard_passed = 1 THEN 1 ELSE 0 END) as passed,
                ROUND(AVG(CASE WHEN guard_passed = 1 THEN fill_time_ms END), 0) as avg_fill_ms,
                ROUND(AVG(CASE WHEN guard_passed = 1 THEN spread END), 4) as avg_spread,
                ROUND(AVG(CASE WHEN guard_passed = 1 AND is_win = 1 THEN spread END), 4) as win_spread,
                ROUND(AVG(CASE WHEN guard_passed = 1 AND is_win = 0 THEN spread END), 4) as loss_spread
            FROM signals WHERE asset = 'BTC'
                AND midpoint >= ? AND midpoint <= ?
                AND timestamp > datetime('now', ?)
        """, (price_min, price_max, f'-{days} days'))
        if sig_rows and sig_rows[0]['total_signals']:
            s = sig_rows[0]
            fill_rate = (s['passed'] or 0) / s['total_signals'] * 100 if s['total_signals'] else 0
            print(f"\n{'='*40}")
            print(f"  SIGNAL MICROSTRUCTURE")
            print(f"{'='*40}")
            print(f"  Total signals:    {s['total_signals']}")
            print(f"  Guard pass rate:  {fill_rate:.1f}%")
            print(f"  Avg fill latency: {s['avg_fill_ms'] or 'N/A'}ms")
            print(f"  Avg spread:       {s['avg_spread'] or 'N/A'}")
            print(f"  Win spread:       {s['win_spread'] or 'N/A'}")
            print(f"  Loss spread:      {s['loss_spread'] or 'N/A'}")

    # --- 20: Daily P&L ---
    daily_rows = query(perf_db, f"""
        SELECT strftime('%Y-%m-%d', entry_time, 'unixepoch') as dt,
            COUNT(*) as n,
            ROUND(100.0*SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) as wr,
            ROUND(SUM(pnl),2) as pnl
        FROM trades WHERE {where} GROUP BY dt ORDER BY dt
    """)
    print(f"\n{'='*40}")
    print(f"  DAILY P&L")
    print(f"{'='*40}")
    cum = 0
    for d in daily_rows:
        cum += d['pnl'] or 0
        marker = "+" if (d['pnl'] or 0) > 0 else " "
        print(f"  {d['dt']}  {d['wr']:5.1f}% WR  ${d['pnl']:>8.2f}  cum=${cum:.2f}  n={d['n']} {marker}")

    print(f"\n{'='*70}")
    print(f"  END OF REPORT")
    print(f"{'='*70}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Polyphemus Metrics Dashboard")
    parser.add_argument("--instance", default="emmanuel", help="Bot instance name")
    parser.add_argument("--days", type=int, default=7, help="Lookback days")
    parser.add_argument("--price-min", type=float, default=0.30)
    parser.add_argument("--price-max", type=float, default=0.50)
    parser.add_argument("--asset", default=None, help="Filter to specific asset (BTC, SOL)")
    args = parser.parse_args()

    base = f"/opt/lagbot/instances/{args.instance}/data"
    perf_db = f"{base}/performance.db"
    signals_db = f"{base}/signals.db"

    run_metrics(perf_db, signals_db, args.days, args.price_min, args.price_max, args.asset)
