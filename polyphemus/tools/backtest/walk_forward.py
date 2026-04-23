"""Walk-forward test for the Scenario D entry filter.

In-sample fit will ALWAYS look good on 508 historical trades. To
determine whether the filter captures real edge vs curve-fit noise,
split by entry_time into k folds and verify each test fold is profitable.

Usage:
    python -m polyphemus.tools.backtest.walk_forward --db /tmp/... --folds 5
"""
from __future__ import annotations

import argparse

from polyphemus.tools.backtest.filter_search import (
    compute_metrics,
    load_trades,
)


FILTER_CANDIDATES = {
    "A_ep_80_plus": lambda r: r["entry_price"] >= 0.80,
    "B_skip_65_80": lambda r: not (0.65 <= r["entry_price"] < 0.80),
    "C_skip_65_80_and_btc_down":
        lambda r: not (0.65 <= r["entry_price"] < 0.80)
                  and not (r["asset"] == "BTC" and r["direction"] == "down"),
    "D_whitelist":
        lambda r: (r["entry_price"] >= 0.80)
                  or (r["asset"] == "BTC" and r["direction"] == "up")
                  or (r["asset"] == "XRP" and r["direction"] == "down"
                      and 0.50 <= r["entry_price"] < 0.65),
    "F_skip_65_80_plus_whitelist":
        lambda r: not (0.65 <= r["entry_price"] < 0.80)
                  and (
                      r["entry_price"] >= 0.80
                      or (r["asset"] == "BTC" and r["direction"] == "up")
                      or (r["asset"] == "XRP" and r["direction"] == "down")
                      or (r["asset"] == "SOL" and 0.50 <= r["entry_price"] < 0.65)
                  ),
}


def walk_forward(rows: list[dict], folds: int):
    rows = sorted(rows, key=lambda r: r["entry_time"])
    n = len(rows)
    fold_size = n // folds
    print(f"\n=== WALK-FORWARD on n={n} trades in {folds} folds ===")
    header = f"{'filter':<32}"
    for i in range(folds):
        header += f"  F{i+1}:WR/PnL/Sh"
    print(header)
    print("-" * (32 + folds * 24))

    for fname, fn in FILTER_CANDIDATES.items():
        line = f"{fname:<32}"
        positive_folds = 0
        fold_pnls = []
        for i in range(folds):
            start = i * fold_size
            end = start + fold_size if i < folds - 1 else n
            fold_rows = [r for r in rows[start:end] if fn(r)]
            if len(fold_rows) < 5:
                line += f"  {'skip':>22}"
                continue
            m = compute_metrics(fold_rows)
            fold_pnls.append(m.total_pnl)
            if m.total_pnl > 0:
                positive_folds += 1
            line += f"  {m.wr*100:>3.0f}/{m.total_pnl:>+6.1f}/{m.sharpe:>+4.2f}"
        pos_pct = positive_folds / folds * 100
        total = sum(fold_pnls)
        line += f"   +folds={positive_folds}/{folds} ({pos_pct:.0f}%)  sum=${total:+.2f}"
        print(line)

    print("\nLegend: WR (%), total PnL ($), Sharpe per fold; +folds = folds with PnL>0")
    print("MTC R3 passes if >=60% of folds are positive.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--source", default="binance_momentum")
    ap.add_argument("--folds", type=int, default=5)
    args = ap.parse_args()
    rows = load_trades(args.db, args.source)
    walk_forward(rows, args.folds)


if __name__ == "__main__":
    main()
