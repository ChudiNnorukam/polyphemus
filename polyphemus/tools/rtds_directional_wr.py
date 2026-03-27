#!/usr/bin/env python3
"""RTDS Directional Win Rate Analysis.

Analyzes flat_regime_rtds shadow trades by direction (Up vs Down) to validate
the directional bias finding from WF-CV regime analysis (Cohen's d=1.89).

Usage:
    python3 tools/rtds_directional_wr.py --db /path/to/performance.db
    python3 tools/rtds_directional_wr.py --db /path/to/performance.db --days 7
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def query_rtds_trades(db_path: str, days: int = 0) -> list[dict]:
    """Pull flat_regime_rtds trades from performance.db."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    query = """
        SELECT slug, entry_price, entry_time, exit_time, pnl, direction,
               asset, source, outcome
        FROM trades
        WHERE source = 'flat_regime_rtds'
    """
    params: list = []

    if days > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        query += " AND entry_time >= ?"
        params.append(cutoff)

    query += " ORDER BY entry_time"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def analyze_directional_wr(trades: list[dict]) -> dict:
    """Compute WR breakdown by direction."""
    up_trades = [t for t in trades if str(t.get("direction", "")).upper() == "UP"]
    down_trades = [t for t in trades if str(t.get("direction", "")).upper() == "DOWN"]
    unknown = [t for t in trades if str(t.get("direction", "")).upper() not in ("UP", "DOWN")]

    def wr_stats(subset: list[dict], label: str) -> dict:
        n = len(subset)
        if n == 0:
            return {"label": label, "n": 0, "wins": 0, "losses": 0, "wr": None, "pnl": 0.0}
        wins = sum(1 for t in subset if (t.get("pnl") or 0) > 0)
        losses = n - wins
        pnl = sum(t.get("pnl") or 0 for t in subset)
        return {
            "label": label,
            "n": n,
            "wins": wins,
            "losses": losses,
            "wr": round(wins / n * 100, 1) if n > 0 else None,
            "pnl": round(pnl, 2),
            "avg_entry": round(sum(t.get("entry_price", 0) or 0 for t in subset) / n, 4) if n else 0,
            "r8": "ANECDOTAL" if n < 15 else "PRELIMINARY" if n < 30 else "MODERATE" if n < 100 else "SUBSTANTIAL",
        }

    overall = wr_stats(trades, "Overall")
    up = wr_stats(up_trades, "Up")
    down = wr_stats(down_trades, "Down")

    # Per-asset breakdown for Down entries
    down_by_asset: dict[str, dict] = {}
    for t in down_trades:
        asset = t.get("asset", "UNKNOWN")
        down_by_asset.setdefault(asset, []).append(t)

    asset_breakdown = {
        asset: wr_stats(subset, f"Down-{asset}")
        for asset, subset in sorted(down_by_asset.items())
    }

    # Directional gap
    gap_pp = None
    if up["wr"] is not None and down["wr"] is not None:
        gap_pp = round(up["wr"] - down["wr"], 1)

    # Go-live gate check
    gate_status = {
        "kelly_positive": None,  # needs P&L data
        "overall_wr_above_65": overall["wr"] is not None and overall["wr"] >= 65.0,
        "down_wr_above_55": down["wr"] is not None and down["wr"] >= 55.0,
        "min_50_down_trades": down["n"] >= 50,
        "no_single_day_100_loss": None,  # needs daily breakdown
    }

    return {
        "overall": overall,
        "up": up,
        "down": down,
        "directional_gap_pp": gap_pp,
        "down_by_asset": asset_breakdown,
        "unknown_direction": len(unknown),
        "gate_status": gate_status,
    }


def print_report(result: dict, db_path: str, days: int) -> None:
    """Print formatted report."""
    print("=" * 60)
    print("RTDS Directional Win Rate Analysis")
    print(f"DB: {db_path}")
    if days > 0:
        print(f"Window: last {days} days")
    print(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    for section in ["overall", "up", "down"]:
        s = result[section]
        wr_str = f"{s['wr']:.1f}%" if s["wr"] is not None else "N/A"
        r8 = s.get("r8", "")
        print(f"\n{s['label']:>10}: {s['n']:>4} trades | WR={wr_str:>6} | "
              f"P&L=${s['pnl']:>8.2f} | avg_entry={s.get('avg_entry', 0):.4f} | {r8}")

    gap = result["directional_gap_pp"]
    if gap is not None:
        print(f"\n{'Gap':>10}: Up - Down = {gap:+.1f}pp")
        if gap > 10:
            print(f"           WARNING: Large directional gap. Down entries underperforming.")
        elif gap < -10:
            print(f"           NOTE: Down entries outperforming Up. Consider removing Down gate.")

    if result["down_by_asset"]:
        print(f"\n--- Down Entries by Asset ---")
        for asset, s in result["down_by_asset"].items():
            wr_str = f"{s['wr']:.1f}%" if s["wr"] is not None else "N/A"
            print(f"  {asset:>4}: n={s['n']:>3} | WR={wr_str:>6} | P&L=${s['pnl']:>7.2f} | {s.get('r8', '')}")

    if result["unknown_direction"] > 0:
        print(f"\n  {result['unknown_direction']} trades with unknown direction (excluded)")

    # Gate status
    print(f"\n--- Go-Live Gate Status ---")
    gs = result["gate_status"]
    for key, val in gs.items():
        icon = "PASS" if val is True else "FAIL" if val is False else "N/A"
        print(f"  [{icon:>4}] {key}")

    # Recommendation
    down_n = result["down"]["n"]
    down_wr = result["down"]["wr"]
    print(f"\n--- Recommendation ---")
    if down_n < 50:
        print(f"  COLLECT MORE DATA: {down_n}/50 Down trades. Need {50 - down_n} more.")
    elif down_wr is not None and down_wr < 55:
        print(f"  KEEP RTDS_DOWN_SIZING_MULT=0.50: Down WR={down_wr:.1f}% < 55% threshold.")
        print(f"  Consider implementing code-level directional gate (Gate 1+2).")
    elif down_wr is not None and down_wr >= 65:
        print(f"  REMOVE DOWN GATE: Down WR={down_wr:.1f}% >= 65%. Set RTDS_DOWN_SIZING_MULT=1.0.")
    else:
        print(f"  KEEP MONITORING: Down WR={down_wr:.1f}% is between 55-65%. Not conclusive yet.")

    print()


def main():
    parser = argparse.ArgumentParser(description="RTDS Directional WR Analysis")
    parser.add_argument("--db", required=True, help="Path to performance.db")
    parser.add_argument("--days", type=int, default=0, help="Only analyze last N days (0=all)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"ERROR: Database not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    trades = query_rtds_trades(args.db, args.days)
    if not trades:
        print(f"No flat_regime_rtds trades found in {args.db}")
        sys.exit(0)

    result = analyze_directional_wr(trades)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_report(result, args.db, args.days)


if __name__ == "__main__":
    main()
