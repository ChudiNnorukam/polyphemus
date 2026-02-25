#!/usr/bin/env python3
"""Backtest harness for lagbot signals.db.

Replays historical signals through configurable filter logic and outputs
WR/P&L breakdown by bucket with confidence labels (DARIO R8).

Usage:
    python scripts/backtest.py --db /path/to/signals.db [OPTIONS]

Examples:
    python scripts/backtest.py --db /tmp/signals.db
    python scripts/backtest.py --db /tmp/signals.db --min-entry-price 0.65 --asset BTC
    python scripts/backtest.py --db /tmp/signals.db --group-by hour
    python scripts/backtest.py --db /tmp/signals.db --min-book-imbalance 0.53
    python scripts/backtest.py --db /tmp/signals.db --hour-blackout 0,1,2,3,4,5,6,7
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Confidence labels (DARIO R8)
# ---------------------------------------------------------------------------

def confidence_label(n: int) -> str:
    if n < 30:
        return f"[ANECDOTAL — n={n}]"
    if n < 107:
        return f"[LOW — 70% CI, n={n}]"
    if n < 385:
        return f"[MODERATE — 95% CI, n={n}]"
    return f"[SIGNIFICANT — 99% CI, n={n}]"


# ---------------------------------------------------------------------------
# Filter logic (mirrors signal_guard checks, applied to stored feature columns)
# ---------------------------------------------------------------------------

def passes_filters(row: dict, args: argparse.Namespace) -> bool:
    midpoint = row.get("midpoint") or 0.0
    momentum_pct = row.get("momentum_pct")
    spread = row.get("spread")
    book_imbalance = row.get("book_imbalance")
    hour_utc = row.get("hour_utc")
    asset = (row.get("asset") or "").upper()
    direction = (row.get("direction") or "").lower()
    source = (row.get("source") or "")

    if args.min_entry_price is not None and midpoint < args.min_entry_price:
        return False
    if args.max_entry_price is not None and midpoint > args.max_entry_price:
        return False

    if args.min_momentum_pct is not None and momentum_pct is not None:
        if abs(momentum_pct) < args.min_momentum_pct:
            return False

    if args.max_spread is not None and spread is not None:
        if spread > args.max_spread:
            return False

    if args.min_book_imbalance is not None and book_imbalance is not None:
        thresh = args.min_book_imbalance
        if direction == "up" and book_imbalance < thresh:
            return False
        if direction == "down" and book_imbalance > (1.0 - thresh):
            return False
    # NULL book_imbalance: skip check (don't reject)

    if args.asset:
        allowed = [a.strip().upper() for a in args.asset.split(",")]
        if asset not in allowed:
            return False

    if args.direction and args.direction != "both":
        if direction != args.direction.lower():
            return False

    if args.hour_blackout and hour_utc is not None:
        blackout = [int(h.strip()) for h in args.hour_blackout.split(",") if h.strip()]
        if hour_utc in blackout:
            return False

    if args.source and source != args.source:
        return False

    return True


# ---------------------------------------------------------------------------
# Stats computation
# ---------------------------------------------------------------------------

def compute_stats(rows: list) -> dict:
    executed = [r for r in rows if r.get("outcome") == "executed" and r.get("pnl") is not None]
    n = len(executed)
    if n == 0:
        return {"n": 0, "wins": 0, "wr": 0.0, "total_pnl": 0.0, "avg_pnl": 0.0}
    wins = sum(1 for r in executed if (r.get("pnl") or 0) > 0)
    total_pnl = sum(r.get("pnl") or 0 for r in executed)
    return {
        "n": n,
        "wins": wins,
        "wr": wins / n * 100 if n else 0.0,
        "total_pnl": total_pnl,
        "avg_pnl": total_pnl / n if n else 0.0,
    }


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

BUCKET_EDGES = [0.50, 0.55, 0.65, 0.75, 0.85, 1.00]
BUCKET_LABELS = ["0.50-0.55", "0.55-0.65", "0.65-0.75", "0.75-0.85", "0.85+"]


def get_bucket(midpoint: float) -> str:
    for i, edge in enumerate(BUCKET_EDGES[1:]):
        if midpoint < edge:
            return BUCKET_LABELS[i]
    return BUCKET_LABELS[-1]


def group_rows(rows: list, group_by: str) -> dict:
    groups = {}
    for row in rows:
        if group_by == "bucket":
            key = get_bucket(row.get("midpoint") or 0.0)
        elif group_by == "hour":
            key = f"{row.get('hour_utc', '?'):02}" if row.get("hour_utc") is not None else "?"
        elif group_by == "asset":
            key = (row.get("asset") or "?").upper()
        elif group_by == "day_of_week":
            days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            dow = row.get("day_of_week")
            key = days[dow] if dow is not None and 0 <= dow < 7 else "?"
        else:
            key = "all"
        groups.setdefault(key, []).append(row)
    return groups


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_table(title: str, groups: dict, baseline_stats: dict, group_by: str):
    print(f"\n{title}")
    print("-" * 100)

    header = f"  {'Group':<14}  {'n_exec':>6}  {'Wins':>5}  {'WR%':>6}  {'Confidence':<32}  {'ΔWR vs baseline':>16}  {'Avg P&L':>8}"
    print(header)
    print("-" * 100)

    base_wr = baseline_stats["wr"]
    total_n = total_wins = 0
    total_pnl = 0.0

    sort_keys = sorted(groups.keys())
    for key in sort_keys:
        rows = groups[key]
        stats = compute_stats(rows)
        n = stats["n"]
        wins = stats["wins"]
        wr = stats["wr"]
        avg_pnl = stats["avg_pnl"]
        conf = confidence_label(n)
        delta = f"{wr - base_wr:+.1f}pp" if n > 0 and base_wr > 0 else "N/A"
        avg_str = f"${avg_pnl:+.2f}" if n > 0 else "N/A"

        print(f"  {key:<14}  {n:>6}  {wins:>5}  {wr:>5.1f}%  {conf:<32}  {delta:>16}  {avg_str:>8}")
        total_n += n
        total_wins += wins
        total_pnl += stats["total_pnl"]

    print("-" * 100)
    total_wr = total_wins / total_n * 100 if total_n else 0.0
    total_conf = confidence_label(total_n)
    delta_total = f"{total_wr - base_wr:+.1f}pp" if total_n > 0 and base_wr > 0 else "N/A"
    avg_total = f"${total_pnl/total_n:+.2f}" if total_n else "N/A"
    print(f"  {'TOTAL':<14}  {total_n:>6}  {total_wins:>5}  {total_wr:>5.1f}%  {total_conf:<32}  {delta_total:>16}  {avg_total:>8}")
    print(f"\n  Total P&L: ${total_pnl:+.2f}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Backtest lagbot signals.db with configurable filters",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", required=True, help="Path to signals.db")
    parser.add_argument("--min-entry-price", type=float, default=None, metavar="FLOAT")
    parser.add_argument("--max-entry-price", type=float, default=None, metavar="FLOAT")
    parser.add_argument("--min-momentum-pct", type=float, default=None, metavar="FLOAT",
                        help="Minimum |momentum_pct| (e.g. 0.003 = 0.3%%)")
    parser.add_argument("--max-spread", type=float, default=None, metavar="FLOAT")
    parser.add_argument("--min-book-imbalance", type=float, default=None, metavar="FLOAT",
                        help="Min book_imbalance alignment (0.53 = require 53%% bid-side for Up)")
    parser.add_argument("--asset", default=None, metavar="CSV",
                        help="Comma-separated assets: BTC,ETH,SOL")
    parser.add_argument("--direction", default=None, choices=["up", "down", "both"],
                        help="Filter by signal direction")
    parser.add_argument("--hour-blackout", default=None, metavar="CSV",
                        help="UTC hours to exclude: 0,1,2,3")
    parser.add_argument("--source", default=None, metavar="TEXT",
                        help="Filter by source: binance_momentum")
    parser.add_argument("--since", default=None, metavar="DATE",
                        help="ISO date lower bound: 2026-02-20")
    parser.add_argument("--group-by", default="bucket",
                        choices=["bucket", "hour", "asset", "day_of_week"],
                        help="Grouping dimension (default: bucket)")
    args = parser.parse_args()

    # Connect
    try:
        conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError as e:
        print(f"ERROR: Cannot open {args.db}: {e}", file=sys.stderr)
        sys.exit(1)

    # Build base query
    where_clauses = ["outcome IS NOT NULL", "outcome != 'shadow'"]
    params = []
    if args.since:
        where_clauses.append("timestamp >= ?")
        params.append(args.since)

    where_sql = "WHERE " + " AND ".join(where_clauses)
    query = f"""
        SELECT slug, asset, direction, midpoint, momentum_pct, spread,
               book_imbalance, hour_utc, day_of_week, time_remaining_secs,
               source, market_window_secs, guard_passed, guard_reasons,
               outcome, pnl, is_win, timestamp
        FROM signals
        {where_sql}
        ORDER BY epoch ASC
    """

    try:
        cursor = conn.execute(query, params)
        all_rows = [dict(r) for r in cursor.fetchall()]
    except sqlite3.OperationalError as e:
        print(f"ERROR querying signals: {e}", file=sys.stderr)
        sys.exit(1)

    conn.close()

    if not all_rows:
        print("No signals found in database.")
        sys.exit(0)

    # Date range
    timestamps = [r["timestamp"] for r in all_rows if r.get("timestamp")]
    date_range = f"{min(timestamps)[:10]} to {max(timestamps)[:10]}" if timestamps else "unknown"

    # Baseline: all signals (no filters)
    baseline_filtered = all_rows  # no extra filter for baseline
    baseline_executed = [r for r in baseline_filtered if r.get("outcome") == "executed" and r.get("pnl") is not None]
    baseline_stats = compute_stats(baseline_filtered)

    # Filtered set
    filtered_rows = [r for r in all_rows if passes_filters(r, args)]
    filtered_executed = [r for r in filtered_rows if r.get("outcome") == "executed" and r.get("pnl") is not None]

    # Header
    filter_parts = []
    if args.min_entry_price is not None:
        filter_parts.append(f"min_entry_price={args.min_entry_price}")
    if args.max_entry_price is not None:
        filter_parts.append(f"max_entry_price={args.max_entry_price}")
    if args.min_momentum_pct is not None:
        filter_parts.append(f"min_momentum_pct={args.min_momentum_pct}")
    if args.max_spread is not None:
        filter_parts.append(f"max_spread={args.max_spread}")
    if args.min_book_imbalance is not None:
        filter_parts.append(f"min_book_imbalance={args.min_book_imbalance}")
    if args.asset:
        filter_parts.append(f"asset={args.asset}")
    if args.direction:
        filter_parts.append(f"direction={args.direction}")
    if args.hour_blackout:
        filter_parts.append(f"hour_blackout={args.hour_blackout}")
    if args.source:
        filter_parts.append(f"source={args.source}")
    if args.since:
        filter_parts.append(f"since={args.since}")

    filter_str = ", ".join(filter_parts) if filter_parts else "none"

    print(f"\nBACKTEST — {args.db}")
    print(f"Date range : {date_range}")
    print(f"Filters    : {filter_str}")
    print(f"Scope      : {len(all_rows):,} total signals | {len(filtered_rows):,} passed filters | "
          f"{len(filtered_executed)} executed (outcome=executed, pnl known)")
    print(f"Baseline   : {len(all_rows):,} total signals | {len(baseline_executed)} executed (no filters)")

    if not filtered_executed:
        print("\nNo executed signals with known P&L match filters.")
        if filtered_rows:
            guard_passed = sum(1 for r in filtered_rows if r.get("guard_passed") == 1)
            print(f"({guard_passed} guard-passed signals exist but pnl not yet resolved)")
        sys.exit(0)

    # Group and print
    groups = group_rows(filtered_rows, args.group_by)
    title = f"By {args.group_by}:"
    print_table(title, groups, baseline_stats, args.group_by)

    # Guard pass rate summary
    total_guard_passed = sum(1 for r in filtered_rows if r.get("guard_passed") == 1)
    print(f"\n  Guard-passed signals (no pnl yet / pending): "
          f"{total_guard_passed - len(filtered_executed)}")

    # Top rejection reasons
    rejection_counts: dict = {}
    for r in filtered_rows:
        if not r.get("guard_passed") and r.get("guard_reasons"):
            for reason in str(r["guard_reasons"]).split(","):
                reason = reason.strip()
                if reason:
                    rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

    if rejection_counts:
        print(f"\n  Top rejection reasons (filtered set):")
        for reason, count in sorted(rejection_counts.items(), key=lambda x: -x[1])[:8]:
            print(f"    {reason:<35} {count:>5}")

    print()


if __name__ == "__main__":
    main()
