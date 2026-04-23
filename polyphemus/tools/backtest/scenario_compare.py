"""Compare concrete deployable entry filters against baseline.

Builds on filter_search.py finding: ep_65_80 band is losing ($-103),
ep_80+ band is winning (+$20). This script evaluates scenarios as full
trade-by-trade filters so we can see what the deployed rule would have done.

Usage:
    python -m polyphemus.tools.backtest.scenario_compare --db /tmp/...
"""
from __future__ import annotations

import argparse
import sqlite3

from polyphemus.tools.backtest.filter_search import (
    compute_metrics,
    load_trades,
    _print_metrics,
)


def scenario(rows: list[dict], name: str, keep_fn) -> None:
    kept = [r for r in rows if keep_fn(r)]
    skipped = len(rows) - len(kept)
    print(f"\n--- Scenario: {name} ---")
    print(f"Kept {len(kept)}/{len(rows)} trades ({skipped} skipped)")
    m = compute_metrics(kept)
    _print_metrics(name, m)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--source", default="binance_momentum")
    args = ap.parse_args()

    rows = load_trades(args.db, args.source)
    print(f"=== BASELINE: {args.source} n={len(rows)} ===")
    _print_metrics("ALL", compute_metrics(rows))

    # Scenario A: only ep_80+ (deep favorite)
    scenario(rows, "A. entry_price >= 0.80",
             lambda r: r["entry_price"] >= 0.80)

    # Scenario B: skip ep_65_80 (the loss bucket)
    scenario(rows, "B. NOT (0.65 <= entry_price < 0.80)",
             lambda r: not (0.65 <= r["entry_price"] < 0.80))

    # Scenario C: skip ep_65_80 AND skip BTC-down
    scenario(rows, "C. skip ep_65_80 AND skip (BTC + down)",
             lambda r: not (0.65 <= r["entry_price"] < 0.80)
                       and not (r["asset"] == "BTC" and r["direction"] == "down"))

    # Scenario D: only buy in the two winning combos
    scenario(rows, "D. ONLY (ep_80+) OR (BTC+up) OR (XRP+down 0.50-0.65)",
             lambda r: (r["entry_price"] >= 0.80)
                       or (r["asset"] == "BTC" and r["direction"] == "up")
                       or (r["asset"] == "XRP" and r["direction"] == "down"
                           and 0.50 <= r["entry_price"] < 0.65))

    # Scenario E: raise floor to 0.50 (current config allows cheap_side all the way down)
    scenario(rows, "E. entry_price >= 0.50",
             lambda r: r["entry_price"] >= 0.50)

    # Scenario F: combination - skip ep_65_80 AND only if dir=up (BTC+up shines)
    scenario(rows, "F. skip ep_65_80 + only keep winning asset/dir combos",
             lambda r: not (0.65 <= r["entry_price"] < 0.80)
                       and (
                           r["entry_price"] >= 0.80
                           or (r["asset"] == "BTC" and r["direction"] == "up")
                           or (r["asset"] == "XRP" and r["direction"] == "down")
                           or (r["asset"] == "SOL" and 0.50 <= r["entry_price"] < 0.65)
                       ))


if __name__ == "__main__":
    main()
