"""Pre-Resolution Exit Strategy Simulation

Answers: what is the optimal pre_resolution_exit strategy across entry price buckets?

Approach (no overfitting):
1. For pre_res exits: exit_price at T-8s ≈ true probability of resolving to $1.00
   - Simulate "hold" EV = P(win) * (1.00 - entry) + P(loss) * (0.00 - entry)
   - Compare to actual sell P&L = (exit_price - entry_price) * shares

2. For market_resolved/redeemed_loss: we know actual outcome
   - market_resolved with pnl > 0: resolved to $1.00
   - redeemed_loss: resolved to $0.00
   - Use these as ground truth for base WR at each entry bucket

3. Monte Carlo: for pre_res exits, simulate 1000 resolution outcomes using
   exit_price as Bernoulli probability. Avoids treating expected value as certainty.

4. Walk-forward: split data by time (first 70% train, last 30% test).
   Find optimal strategy on train, validate on test.

Usage:
    python3 tools/pre_res_simulation.py --db /path/to/performance.db
    python3 tools/pre_res_simulation.py --db /path/to/performance.db --verbose
"""

import argparse
import sqlite3
import random
import sys
from collections import defaultdict

random.seed(42)  # reproducible

def load_trades(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT entry_price, exit_price, pnl, exit_reason, entry_time, exit_time,
               json_extract(metadata, '$.asset') as asset,
               json_extract(metadata, '$.direction') as direction,
               json_extract(metadata, '$.source') as source,
               slug
        FROM trades
        WHERE exit_time IS NOT NULL
          AND entry_price > 0 AND exit_price >= 0
        ORDER BY entry_time
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def bucket(entry_price):
    if entry_price < 0.60:
        return "<0.60"
    elif entry_price < 0.70:
        return "0.60-0.69"
    elif entry_price < 0.80:
        return "0.70-0.79"
    elif entry_price < 0.90:
        return "0.80-0.89"
    else:
        return "0.90+"


def infer_share_count(trade):
    """Infer share count from pnl and prices."""
    price_diff = trade["exit_price"] - trade["entry_price"]
    if abs(price_diff) < 0.001:
        return 0
    return abs(trade["pnl"] / price_diff)


def simulate_hold(trade, n_sims=1000):
    """For a pre_res exit, simulate what holding to resolution would yield.

    exit_price at T-8s ≈ probability of resolving to $1.00.
    Returns: (mean_pnl, win_rate, pnl_samples)
    """
    p_win = trade["exit_price"]  # market probability at exit time
    entry = trade["entry_price"]
    shares = infer_share_count(trade)
    if shares <= 0:
        return trade["pnl"], 0.5, [trade["pnl"]]

    wins = 0
    pnl_samples = []
    for _ in range(n_sims):
        if random.random() < p_win:
            # resolves to $1.00
            pnl = (1.00 - entry) * shares
            wins += 1
        else:
            # resolves to $0.00
            pnl = (0.00 - entry) * shares
        pnl_samples.append(pnl)

    return sum(pnl_samples) / n_sims, wins / n_sims, pnl_samples


def simulate_pre_res_exit(trade, exit_threshold_price=None):
    """For a resolved trade, simulate what pre_res exit would yield.

    We don't have the T-8s price for resolved trades, so we can only
    analyze this for pre_res exits where we have the actual exit price.
    """
    # Can't simulate: we don't know the T-8s price for resolved trades
    return None


def run_simulation(trades, verbose=False):
    print("=" * 70)
    print("PRE-RESOLUTION EXIT STRATEGY SIMULATION")
    print("=" * 70)
    print(f"\nTotal trades: {len(trades)}")

    # --- Section 1: Ground truth from resolved trades ---
    resolved_wins = [t for t in trades if t["exit_reason"] == "market_resolved" and t["pnl"] > 0]
    resolved_losses = [t for t in trades if t["exit_reason"] == "redeemed_loss"]
    pre_res = [t for t in trades if t["exit_reason"] == "pre_resolution_exit"]

    print(f"\nResolved wins (market_resolved, pnl>0): {len(resolved_wins)}")
    print(f"Resolved losses (redeemed_loss):        {len(resolved_losses)}")
    print(f"Pre-resolution exits:                   {len(pre_res)}")

    # Base WR by bucket (from resolved trades only — no pre_res contamination)
    print("\n--- BASE WIN RATE BY BUCKET (resolved trades only) ---")
    bucket_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "win_pnl": 0, "loss_pnl": 0})
    for t in resolved_wins:
        b = bucket(t["entry_price"])
        bucket_stats[b]["wins"] += 1
        bucket_stats[b]["win_pnl"] += t["pnl"]
    for t in resolved_losses:
        b = bucket(t["entry_price"])
        bucket_stats[b]["losses"] += 1
        bucket_stats[b]["loss_pnl"] += t["pnl"]

    print(f"{'Bucket':<12} {'Wins':>5} {'Losses':>7} {'WR%':>6} {'Avg Win':>9} {'Avg Loss':>10} {'Net P&L':>9}")
    print("-" * 65)
    for b in ["<0.60", "0.60-0.69", "0.70-0.79", "0.80-0.89", "0.90+"]:
        s = bucket_stats[b]
        total = s["wins"] + s["losses"]
        if total == 0:
            continue
        wr = 100.0 * s["wins"] / total
        avg_w = s["win_pnl"] / s["wins"] if s["wins"] > 0 else 0
        avg_l = s["loss_pnl"] / s["losses"] if s["losses"] > 0 else 0
        net = s["win_pnl"] + s["loss_pnl"]
        print(f"{b:<12} {s['wins']:>5} {s['losses']:>7} {wr:>5.1f}% {avg_w:>+8.2f} {avg_l:>+9.2f} {net:>+8.2f}")

    # --- Section 2: Pre-res exit analysis ---
    print("\n--- PRE-RESOLUTION EXIT: ACTUAL vs SIMULATED HOLD ---")
    print("For each pre_res exit, Monte Carlo (n=1000) simulates holding to resolution.")
    print(f"exit_price at T-8s used as probability of resolving to $1.00.\n")

    bucket_comparison = defaultdict(lambda: {
        "n": 0, "actual_pnl": 0, "hold_pnl": 0, "actual_wins": 0, "hold_wins": 0
    })

    for t in pre_res:
        b = bucket(t["entry_price"])
        mean_hold_pnl, hold_wr, _ = simulate_hold(t)

        bc = bucket_comparison[b]
        bc["n"] += 1
        bc["actual_pnl"] += t["pnl"]
        bc["hold_pnl"] += mean_hold_pnl
        bc["actual_wins"] += 1 if t["pnl"] > 0 else 0
        bc["hold_wins"] += hold_wr

    print(f"{'Bucket':<12} {'n':>4} {'Sell P&L':>10} {'Hold EV':>10} {'Diff':>10} {'Sell WR':>8} {'Hold WR':>8} {'Verdict':>12}")
    print("-" * 78)
    total_sell = 0
    total_hold = 0
    for b in ["<0.60", "0.60-0.69", "0.70-0.79", "0.80-0.89", "0.90+"]:
        bc = bucket_comparison[b]
        if bc["n"] == 0:
            continue
        diff = bc["hold_pnl"] - bc["actual_pnl"]
        sell_wr = 100.0 * bc["actual_wins"] / bc["n"]
        hold_wr = 100.0 * bc["hold_wins"] / bc["n"]
        verdict = "HOLD BETTER" if diff > 0 else "SELL BETTER"
        total_sell += bc["actual_pnl"]
        total_hold += bc["hold_pnl"]
        print(f"{b:<12} {bc['n']:>4} {bc['actual_pnl']:>+9.2f} {bc['hold_pnl']:>+9.2f} {diff:>+9.2f} {sell_wr:>7.1f}% {hold_wr:>7.1f}% {verdict:>12}")

    total_diff = total_hold - total_sell
    print(f"{'TOTAL':<12} {sum(bc['n'] for bc in bucket_comparison.values()):>4} {total_sell:>+9.2f} {total_hold:>+9.2f} {total_diff:>+9.2f}")

    # --- Section 3: Optimal strategy by exit_price threshold ---
    print("\n--- STRATEGY SWEEP: exit only if price below threshold ---")
    print("Instead of exiting ALL losing positions, only exit if exit_price < threshold.")
    print("Positions above threshold hold to resolution (simulated).\n")

    thresholds = [0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50, 1.00]

    print(f"{'Threshold':<12} {'Exit n':>7} {'Hold n':>7} {'Exit P&L':>10} {'Hold EV':>10} {'Total':>10} {'vs Current':>11}")
    print("-" * 70)

    current_total = sum(t["pnl"] for t in pre_res)

    for thresh in thresholds:
        exit_pnl = 0
        hold_pnl = 0
        exit_n = 0
        hold_n = 0
        for t in pre_res:
            if t["pnl"] >= 0:
                # Winning pre_res exits: these were correct regardless
                exit_pnl += t["pnl"]
                exit_n += 1
            elif t["exit_price"] < thresh:
                # Losing, below threshold: still exit
                exit_pnl += t["pnl"]
                exit_n += 1
            else:
                # Losing, above threshold: hold to resolution (simulate)
                mean_pnl, _, _ = simulate_hold(t)
                hold_pnl += mean_pnl
                hold_n += 1

        total = exit_pnl + hold_pnl
        vs_current = total - current_total
        label = " <-- current" if thresh == 1.00 else ""
        print(f"  < {thresh:<8.2f} {exit_n:>7} {hold_n:>7} {exit_pnl:>+9.2f} {hold_pnl:>+9.2f} {total:>+9.2f} {vs_current:>+10.2f}{label}")

    # --- Section 4: Walk-forward validation ---
    print("\n--- WALK-FORWARD VALIDATION (70/30 time split) ---")

    pre_res_sorted = sorted(pre_res, key=lambda t: t["entry_time"])
    split_idx = int(len(pre_res_sorted) * 0.7)
    train = pre_res_sorted[:split_idx]
    test = pre_res_sorted[split_idx:]

    print(f"Train: {len(train)} trades | Test: {len(test)} trades\n")

    # Find best threshold on train
    best_thresh = 1.00
    best_total = sum(t["pnl"] for t in train)  # current strategy baseline

    for thresh in thresholds:
        total = 0
        for t in train:
            if t["pnl"] >= 0 or t["exit_price"] < thresh:
                total += t["pnl"]
            else:
                mean_pnl, _, _ = simulate_hold(t)
                total += mean_pnl
        if total > best_total:
            best_total = total
            best_thresh = thresh

    print(f"Best threshold on TRAIN: < {best_thresh:.2f} (P&L: {best_total:+.2f})")

    # Apply best threshold to test
    test_current = sum(t["pnl"] for t in test)
    test_optimized = 0
    for t in test:
        if t["pnl"] >= 0 or t["exit_price"] < best_thresh:
            test_optimized += t["pnl"]
        else:
            mean_pnl, _, _ = simulate_hold(t)
            test_optimized += mean_pnl

    print(f"TEST current strategy:   {test_current:+.2f}")
    print(f"TEST optimized (< {best_thresh:.2f}):  {test_optimized:+.2f}")
    improvement = test_optimized - test_current
    print(f"TEST improvement:        {improvement:+.2f}")

    if improvement > 0:
        print(f"\n>> VALIDATED: threshold < {best_thresh:.2f} improves P&L out-of-sample by ${improvement:.2f}")
    else:
        print(f"\n>> NOT VALIDATED: optimized strategy does not improve on test set")

    # --- Section 5: Recommendation ---
    print("\n" + "=" * 70)
    print("RECOMMENDATION")
    print("=" * 70)

    if improvement > 0 and best_thresh < 1.00:
        print(f"""
Strategy: Only pre_res exit if exit_price < {best_thresh:.2f}
Meaning:  At T-8s, if the position is losing BUT the market still gives it
          >{best_thresh:.0%} chance, HOLD to resolution instead of panic-selling.

Rationale: Positions with exit_price > {best_thresh:.2f} have enough probability
           of winning that holding has higher expected value than selling.
           Positions with exit_price < {best_thresh:.2f} are essentially dead
           and should be sold to recover the small remaining value.

Walk-forward validated: +${improvement:.2f} on {len(test)} out-of-sample trades.
""")
    else:
        print(f"""
Current strategy (exit all losing at T-8s) is optimal or near-optimal.
No threshold change improves out-of-sample performance.

Pre-resolution exit total P&L: ${current_total:+.2f} on {len(pre_res)} trades.
""")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pre-resolution exit simulation")
    parser.add_argument("--db", required=True, help="Path to performance.db")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    trades = load_trades(args.db)
    run_simulation(trades, verbose=args.verbose)
