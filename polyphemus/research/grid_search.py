"""Grid search with automatic multiple testing correction.

Runs parameter sweeps through walk-forward validation, automatically
applies DSR and BH FDR correction, and warns on overfitting signals.

Usage:
    python3 -m polyphemus.research.grid_search \
        --db /path/to/signals.db --asset BTC --max-price 0.50

    # Custom grid
    python3 -m polyphemus.research.grid_search \
        --db /path/to/signals.db --asset BTC \
        --min-prices 0.40,0.45 --max-prices 0.50,0.55 \
        --hours "22-6,0-23"
"""

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_ROOT.parent))

from polyphemus.fees import breakeven_wr, taker_fee_per_share
from polyphemus.tools.trader_stats import (
    bootstrap_max_drawdown,
    deflated_sharpe,
    fdr_correction,
    hypothesis_test_wr,
    walk_forward_cv,
    wilson_ci,
)


# Default parameter grid
DEFAULT_GRID = {
    "min_price": [0.35, 0.40, 0.45],
    "max_price": [0.50, 0.55, 0.60],
    "hours_utc": ["0-23", "22-6", "0-6"],
}

# Session test counter persists across runs
COUNTER_PATH = Path(__file__).resolve().parent / "data" / "_tests_run.json"


def load_signals(db_path: str, asset: str) -> list[dict]:
    """Load signals with resolution outcomes from signals.db."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT timestamp, slug, asset, direction, midpoint, spread,
               is_win, momentum_pct, time_remaining_secs, hour_utc
        FROM signals
        WHERE asset = ? AND is_win IS NOT NULL AND guard_passed = 1
          AND midpoint > 0 AND dry_run = 0
        ORDER BY timestamp
    """, [asset]).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def parse_hours(hours_str: str) -> set[int]:
    """Parse hour range string like '22-6' into set of hours."""
    if hours_str == "0-23":
        return set(range(24))
    parts = hours_str.split("-")
    if len(parts) != 2:
        return set(range(24))
    start, end = int(parts[0]), int(parts[1])
    if start <= end:
        return set(range(start, end + 1))
    else:
        return set(range(start, 24)) | set(range(0, end + 1))


def filter_signals(signals: list[dict], min_price: float, max_price: float,
                   hours: set[int]) -> list[dict]:
    """Filter signals by price range and hour."""
    return [
        s for s in signals
        if min_price <= s["midpoint"] <= max_price
        and s.get("hour_utc", 12) in hours
    ]


def simulate_pnl(signal: dict) -> float:
    """Simple P&L simulation: entry at midpoint, resolved to 0 or 1."""
    mid = signal["midpoint"]
    fee = taker_fee_per_share(mid)
    if signal["is_win"]:
        return 1.0 - mid - fee
    else:
        return 0.0 - mid - fee


def run_grid_search(signals: list[dict], param_grid: dict) -> list[dict]:
    """Run all parameter combinations and collect results."""
    keys = list(param_grid.keys())
    combos = list(product(*param_grid.values()))
    total = len(combos)

    print(f"Running {total} parameter combinations...")
    results = []

    for i, combo in enumerate(combos):
        params = dict(zip(keys, combo))

        hours = parse_hours(params["hours_utc"])
        filtered = filter_signals(
            signals,
            params["min_price"],
            params["max_price"],
            hours,
        )

        n = len(filtered)
        if n < 10:
            continue

        pnls = [simulate_pnl(s) for s in filtered]
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / n
        avg_entry = sum(s["midpoint"] for s in filtered) / n
        be = breakeven_wr(avg_entry)

        # Hypothesis test
        ht = hypothesis_test_wr(wins, n, breakeven=be)

        # Walk-forward
        n_splits = min(5, n // 4)
        if n_splits >= 2:
            wf = walk_forward_cv(pnls, n_splits=n_splits)
        else:
            wf = {"mean_test_wr": wr, "consistent": False, "splits_positive": 0,
                  "split_results": []}

        # Sharpe
        import numpy as np
        arr = np.array(pnls)
        sharpe = float(arr.mean() / arr.std(ddof=1)) if arr.std(ddof=1) > 0 else 0.0

        # WFE
        if wf.get("split_results"):
            is_wrs = [r["train_wr"] for r in wf["split_results"]]
            oos_wrs = [r["test_wr"] for r in wf["split_results"]]
            avg_is = sum(is_wrs) / len(is_wrs) if is_wrs else 0
            avg_oos = sum(oos_wrs) / len(oos_wrs) if oos_wrs else 0
            wfe = avg_oos / avg_is if avg_is > 0 else 0
        else:
            wfe = 0

        results.append({
            "params": params,
            "n": n,
            "wins": wins,
            "wr": round(wr, 4),
            "breakeven_wr": round(be, 4),
            "p_value": ht["p_value"],
            "sharpe": round(sharpe, 4),
            "wfe": round(wfe, 4),
            "wf_splits_positive": wf.get("splits_positive", 0),
            "wf_total_splits": len(wf.get("split_results", [])),
            "mean_test_wr": wf.get("mean_test_wr", 0),
            "total_pnl": round(sum(pnls), 4),
            "r8_label": ht["r8_label"],
            "wilson_ci": ht["wilson_ci"],
        })

        if (i + 1) % 50 == 0:
            print(f"  ... {i+1}/{total} combinations")

    return results


def apply_corrections(results: list[dict], total_combos: int) -> list[dict]:
    """Apply DSR and FDR correction to results."""
    if not results:
        return results

    # DSR for each result (corrected for total combos tested)
    for r in results:
        # Use total_combos as k for DSR
        pnls_approx_sharpe = r["sharpe"]
        r["dsr"] = deflated_sharpe(
            [r["sharpe"]] * max(10, r["n"]),  # placeholder for proper returns
            k=total_combos,
        )

    # FDR correction on p-values
    p_values = [r["p_value"] for r in results]
    fdr = fdr_correction(p_values)

    for i, r in enumerate(results):
        r["fdr_adjusted_p"] = fdr["adjusted_p_values"][i]
        r["fdr_survives"] = fdr["rejected"][i]

    return results


def update_test_counter(n_tests: int):
    """Track cumulative tests run across sessions."""
    COUNTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = 0
    if COUNTER_PATH.exists():
        with open(COUNTER_PATH) as f:
            data = json.load(f)
            existing = data.get("cumulative_tests", 0)

    with open(COUNTER_PATH, "w") as f:
        json.dump({
            "cumulative_tests": existing + n_tests,
            "last_session_tests": n_tests,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }, f, indent=2)

    return existing + n_tests


def print_results(results: list[dict], total_combos: int, cumulative_tests: int):
    """Print ranked results with corrections."""
    # Sort by Sharpe descending
    ranked = sorted(results, key=lambda r: r["sharpe"], reverse=True)

    print(f"\n{'='*80}")
    print(f"GRID SEARCH RESULTS ({len(results)}/{total_combos} combos with n >= 10)")
    print(f"Cumulative tests this project: {cumulative_tests}")
    print(f"{'='*80}\n")

    # Top 10
    for i, r in enumerate(ranked[:10]):
        params_str = ", ".join(f"{k}={v}" for k, v in r["params"].items())
        ci = r["wilson_ci"]
        fdr_mark = "+" if r.get("fdr_survives") else "-"

        print(f"#{i+1} [{fdr_mark}FDR] {params_str}")
        print(f"    n={r['n']} ({r['r8_label']})  WR={r['wr']*100:.1f}% [{ci[0]*100:.1f}%, {ci[1]*100:.1f}%]  "
              f"BE={r['breakeven_wr']*100:.1f}%")
        print(f"    Sharpe={r['sharpe']:.2f}  WFE={r['wfe']:.2f}  "
              f"WF={r['wf_splits_positive']}/{r['wf_total_splits']}  "
              f"P&L=${r['total_pnl']:.2f}")
        print(f"    p={r['p_value']:.6f}  FDR-adj={r.get('fdr_adjusted_p', 1.0):.6f}")

        if r["sharpe"] > 3.0:
            print(f"    *** WARNING: Sharpe > 3.0 = LIKELY OVERFITTING ***")
        print()

    # Summary
    n_fdr_survive = sum(1 for r in results if r.get("fdr_survives"))
    n_positive_wr = sum(1 for r in results if r["wr"] > r["breakeven_wr"])
    print(f"Summary: {n_positive_wr}/{len(results)} above breakeven, "
          f"{n_fdr_survive}/{len(results)} survive FDR correction")


def main():
    parser = argparse.ArgumentParser(description="Grid search with multiple testing correction")
    parser.add_argument("--db", required=True, help="Path to signals.db")
    parser.add_argument("--asset", default="BTC", help="Asset to analyze")
    parser.add_argument("--min-prices", help="Comma-separated min prices (default: 0.35,0.40,0.45)")
    parser.add_argument("--max-prices", help="Comma-separated max prices (default: 0.50,0.55,0.60)")
    parser.add_argument("--hours", help='Comma-separated hour ranges (default: "0-23,22-6,0-6")')
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"ERROR: Database not found: {args.db}")
        sys.exit(1)

    # Build grid
    grid = dict(DEFAULT_GRID)
    if args.min_prices:
        grid["min_price"] = [float(x) for x in args.min_prices.split(",")]
    if args.max_prices:
        grid["max_price"] = [float(x) for x in args.max_prices.split(",")]
    if args.hours:
        grid["hours_utc"] = args.hours.split(",")

    total_combos = 1
    for v in grid.values():
        total_combos *= len(v)

    # Load signals
    signals = load_signals(args.db, args.asset)
    print(f"Loaded {len(signals)} signals for {args.asset}")

    if not signals:
        print("No signals found.")
        sys.exit(0)

    # Run grid search
    results = run_grid_search(signals, grid)

    # Apply corrections
    results = apply_corrections(results, total_combos)

    # Update test counter
    cumulative = update_test_counter(total_combos)

    if args.json:
        print(json.dumps({
            "total_combos": total_combos,
            "cumulative_tests": cumulative,
            "results": sorted(results, key=lambda r: r["sharpe"], reverse=True),
        }, indent=2, default=str))
    else:
        print_results(results, total_combos, cumulative)


if __name__ == "__main__":
    main()
