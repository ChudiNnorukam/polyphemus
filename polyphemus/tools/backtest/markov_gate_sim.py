"""Simulate the Markov gate retroactively on binance_momentum trades.

Gate rules (from config defaults):
  - markov_gate_max_losses = 1  → block after ANY single loss
  - markov_gate_min_wins   = 1  → unblock after ANY single win
  - markov_gate_timeout_secs = 1800  → auto-unblock after 30 min

Questions answered:
  1. How many trades would the gate have blocked?
  2. What was the PnL of the blocked set?  (If net negative → gate saved us.)
  3. Did the gate specifically block the late-March 0-for-N collapse?

Usage:
    python -m polyphemus.tools.backtest.markov_gate_sim --db /tmp/...
"""
from __future__ import annotations

import argparse
import math

from polyphemus.tools.backtest.filter_search import (
    load_trades,
    compute_metrics,
)


def simulate(rows: list[dict], max_losses: int, min_wins: int,
             timeout_secs: int) -> tuple[list[dict], list[dict]]:
    """Returns (taken, blocked) trade lists."""
    rows = sorted(rows, key=lambda r: r["entry_time"])
    blocked_state = False
    blocked_since = 0.0
    consec_w = 0
    consec_l = 0
    taken: list[dict] = []
    blocked: list[dict] = []

    for r in rows:
        ts = r["entry_time"]
        # Auto-unblock by timeout?
        if blocked_state and (ts - blocked_since) > timeout_secs:
            blocked_state = False
            blocked_since = 0.0

        if blocked_state:
            blocked.append(r)
            # Do NOT update streak state on blocked trades — we didn't take them
            continue

        taken.append(r)
        # Update streak using actual outcome
        is_win = r["pnl"] > 0
        if is_win:
            consec_w += 1
            consec_l = 0
            if blocked_state and consec_w >= min_wins:
                blocked_state = False
                blocked_since = 0.0
        else:
            consec_l += 1
            consec_w = 0
            if not blocked_state and consec_l >= max_losses:
                blocked_state = True
                blocked_since = ts

    return taken, blocked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--source", default="binance_momentum")
    ap.add_argument("--max-losses", type=int, default=1)
    ap.add_argument("--min-wins", type=int, default=1)
    ap.add_argument("--timeout-secs", type=int, default=1800)
    args = ap.parse_args()

    rows = load_trades(args.db, args.source)
    taken, blocked = simulate(rows, args.max_losses, args.min_wins, args.timeout_secs)

    print(f"=== Markov Gate Simulation ===")
    print(f"Config: max_losses={args.max_losses} min_wins={args.min_wins} "
          f"timeout={args.timeout_secs}s")
    print(f"Trades: {len(rows)} total | {len(taken)} taken | {len(blocked)} blocked "
          f"({len(blocked)/len(rows)*100:.0f}% filtered)")

    print(f"\n--- BASELINE (no gate) ---")
    m = compute_metrics(rows)
    print(f"n={m.n} WR={m.wr:.1%} Sharpe={m.sharpe:+.2f} PnL=${m.total_pnl:+.2f}")

    print(f"\n--- GATE ON (taken trades) ---")
    m = compute_metrics(taken)
    print(f"n={m.n} WR={m.wr:.1%} Sharpe={m.sharpe:+.2f} PnL=${m.total_pnl:+.2f}")

    print(f"\n--- BLOCKED trades (what the gate rejected) ---")
    if blocked:
        m = compute_metrics(blocked)
        print(f"n={m.n} WR={m.wr:.1%} Sharpe={m.sharpe:+.2f} PnL=${m.total_pnl:+.2f}")
    else:
        print("  (none)")

    # Sensitivity: sweep max_losses 1..5
    print(f"\n=== SENSITIVITY SWEEP (min_wins={args.min_wins}, timeout={args.timeout_secs}s) ===")
    print(f"{'max_L':>6} {'taken':>6} {'blocked':>7} {'take_WR':>7} "
          f"{'take_Sh':>7} {'take_PnL':>8} {'blocked_PnL':>11}")
    print("-" * 65)
    for ml in (1, 2, 3, 4, 5):
        t, b = simulate(rows, ml, args.min_wins, args.timeout_secs)
        mt, mb = compute_metrics(t), compute_metrics(b)
        print(f"{ml:>6} {mt.n:>6} {mb.n:>7} {mt.wr*100:>6.1f}% "
              f"{mt.sharpe:>+7.2f} {mt.total_pnl:>+8.2f} {mb.total_pnl:>+11.2f}")

    # Find biggest loss streaks
    print(f"\n=== BIGGEST LOSS STREAKS in actual trades ===")
    rows_s = sorted(rows, key=lambda r: r["entry_time"])
    longest = 0
    cur_run = 0
    run_start_ts = 0
    streaks = []
    for r in rows_s:
        if r["pnl"] <= 0:
            if cur_run == 0:
                run_start_ts = r["entry_time"]
            cur_run += 1
        else:
            if cur_run >= 3:
                streaks.append((cur_run, run_start_ts, r["entry_time"]))
            cur_run = 0
    if cur_run >= 3:
        streaks.append((cur_run, run_start_ts, rows_s[-1]["entry_time"]))
    streaks.sort(reverse=True)
    from datetime import datetime, timezone
    for L, start, end in streaks[:10]:
        s = datetime.fromtimestamp(start, tz=timezone.utc).strftime("%m-%d %H:%M")
        e = datetime.fromtimestamp(end, tz=timezone.utc).strftime("%m-%d %H:%M")
        dur_hrs = (end - start) / 3600
        print(f"  {L} losses over {dur_hrs:.1f}h  ({s} → {e})")


if __name__ == "__main__":
    main()
