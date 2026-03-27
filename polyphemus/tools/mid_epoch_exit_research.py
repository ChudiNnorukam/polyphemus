"""Mid-Epoch Confidence Exit Research

Question: Can we cut losing positions EARLIER (T-120s, T-60s) based on midpoint
trajectory, while holding high-confidence winners to resolution?

Approach:
1. Join signals.db (price snapshots) with performance.db (trade outcomes)
2. For resolved trades: what was the midpoint at various checkpoints?
3. Simulate: if we exit when midpoint drops below threshold at checkpoint T,
   what's the P&L vs holding to resolution?
4. Walk-forward validate (70/30 time split)

Key insight: we KNOW the resolution outcome for market_resolved and redeemed_loss
trades. We can backtest any exit strategy against ground truth.

Usage:
    python3 tools/mid_epoch_exit_research.py \
        --perf-db /path/to/performance.db \
        --signals-db /path/to/signals.db
"""

import argparse
import sqlite3
import sys
from collections import defaultdict


def load_resolved_trades(perf_db):
    """Load trades where we know the resolution outcome."""
    conn = sqlite3.connect(perf_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT slug, entry_price, exit_price, pnl, exit_reason, entry_time,
               json_extract(metadata, '$.asset') as asset,
               json_extract(metadata, '$.direction') as direction,
               json_extract(metadata, '$.source') as source,
               CASE
                   WHEN exit_reason = 'redeemed_loss' THEN 0
                   WHEN exit_reason = 'market_resolved' AND pnl > 0 THEN 1
                   WHEN exit_reason = 'market_resolved' AND pnl <= 0 THEN 0
                   ELSE NULL
               END as resolved_to_win
        FROM trades
        WHERE exit_time IS NOT NULL
          AND exit_reason IN ('market_resolved', 'redeemed_loss')
          AND entry_price > 0
        ORDER BY entry_time
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def load_signals_for_slugs(signals_db, slugs):
    """Load all signals for given slugs, grouped by slug."""
    conn = sqlite3.connect(signals_db)
    conn.row_factory = sqlite3.Row
    placeholders = ",".join(["?"] * len(slugs))
    rows = conn.execute(f"""
        SELECT slug, midpoint, time_remaining_secs, direction
        FROM signals
        WHERE slug IN ({placeholders})
        ORDER BY slug, time_remaining_secs DESC
    """, list(slugs)).fetchall()
    conn.close()

    by_slug = defaultdict(list)
    for r in rows:
        by_slug[r["slug"]].append(dict(r))
    return by_slug


def infer_shares(trade):
    """Infer share count from P&L and prices."""
    if trade["resolved_to_win"]:
        price_diff = 1.00 - trade["entry_price"]
    else:
        price_diff = 0.00 - trade["entry_price"]
    if abs(price_diff) < 0.001:
        return 0
    return abs(trade["pnl"] / price_diff)


def get_midpoint_at_checkpoint(signals, checkpoint_secs, trade_direction):
    """Get the midpoint closest to the checkpoint time.

    Returns the midpoint for the TRADE's token (not necessarily the direction
    of the signal). If trade bought UP token, midpoint is the UP price.
    If trade bought DOWN token, midpoint = 1 - UP_midpoint.
    """
    if not signals:
        return None

    best = None
    best_dist = float("inf")
    for s in signals:
        dist = abs(s["time_remaining_secs"] - checkpoint_secs)
        if dist < best_dist:
            best_dist = dist
            best = s

    # Only use if within 30s of checkpoint
    if best_dist > 30:
        return None

    mid = best["midpoint"]
    # Signal midpoint is for the signal's direction token
    # If trade direction matches signal direction, use as-is
    # If trade direction differs, flip it
    if trade_direction and best["direction"]:
        if trade_direction.lower() != best["direction"].lower():
            mid = 1.0 - mid
    return mid


def simulate_strategies(trades, signals_by_slug):
    """Simulate various mid-epoch exit strategies."""

    checkpoints = [240, 180, 120, 60, 30]  # seconds remaining
    thresholds = [0.20, 0.30, 0.40, 0.50]  # midpoint below which we exit

    # Count trades with usable signal data
    trades_with_signals = 0
    for t in trades:
        if t["slug"] in signals_by_slug and len(signals_by_slug[t["slug"]]) >= 2:
            trades_with_signals += 1

    print(f"\nResolved trades: {len(trades)}")
    print(f"Trades with signal trajectory (>=2 signals): {trades_with_signals}")
    print(f"Coverage: {100*trades_with_signals/len(trades):.1f}%")

    if trades_with_signals < 20:
        print("\n** WARNING: Low coverage. Results may not be reliable. **")
        print("** Need more signal logging for robust backtest. **\n")

    # --- Strategy simulation ---
    print("\n" + "=" * 80)
    print("STRATEGY SIMULATION: Exit if midpoint < threshold at checkpoint")
    print("Baseline: hold all positions to resolution (current behavior for these trades)")
    print("=" * 80)

    baseline_pnl = sum(t["pnl"] for t in trades)
    baseline_wins = sum(1 for t in trades if t["pnl"] > 0)
    print(f"\nBaseline (hold to resolution): {len(trades)} trades, "
          f"{100*baseline_wins/len(trades):.1f}% WR, ${baseline_pnl:+.2f} P&L")

    print(f"\n{'Checkpoint':<12} {'Threshold':<11} {'Exits':<7} {'Holds':<7} "
          f"{'Exit P&L':<11} {'Hold P&L':<11} {'Total':<11} {'vs Base':<11} {'Note'}")
    print("-" * 95)

    best_strategy = None
    best_improvement = 0

    for cp in checkpoints:
        for thresh in thresholds:
            exit_pnl = 0.0
            hold_pnl = 0.0
            exit_n = 0
            hold_n = 0
            no_data_n = 0

            for t in trades:
                sigs = signals_by_slug.get(t["slug"], [])
                mid_at_cp = get_midpoint_at_checkpoint(sigs, cp, t.get("direction"))

                if mid_at_cp is None:
                    # No signal data at this checkpoint — hold (conservative)
                    hold_pnl += t["pnl"]
                    no_data_n += 1
                    hold_n += 1
                elif mid_at_cp < thresh:
                    # Below confidence threshold — EXIT at midpoint
                    shares = infer_shares(t)
                    sell_pnl = (mid_at_cp - t["entry_price"]) * shares
                    exit_pnl += sell_pnl
                    exit_n += 1
                else:
                    # Above threshold — HOLD to resolution
                    hold_pnl += t["pnl"]
                    hold_n += 1

            total = exit_pnl + hold_pnl
            vs_base = total - baseline_pnl
            note = ""
            if exit_n == 0:
                note = "no exits triggered"
            elif vs_base > best_improvement and exit_n >= 5:
                best_improvement = vs_base
                best_strategy = (cp, thresh, exit_n, total, vs_base)

            print(f"T-{cp:>3}s     < {thresh:<9.2f} {exit_n:<7} {hold_n:<7} "
                  f"${exit_pnl:<+9.2f} ${hold_pnl:<+9.2f} ${total:<+9.2f} ${vs_base:<+9.2f}  {note}")

    if best_strategy:
        cp, thresh, n, total, improvement = best_strategy
        print(f"\n>> BEST: Exit if midpoint < {thresh:.2f} at T-{cp}s "
              f"({n} exits, ${improvement:+.2f} improvement)")
    else:
        print("\n>> No strategy improves on baseline (hold to resolution)")

    return trades, best_strategy


def walk_forward_validate(trades, signals_by_slug, best_strategy):
    """Walk-forward validation: train on 70%, test on 30%."""
    if not best_strategy:
        print("\nNo strategy to validate.")
        return

    cp, thresh, _, _, _ = best_strategy
    split = int(len(trades) * 0.7)
    train = trades[:split]
    test = trades[split:]

    print(f"\n{'='*80}")
    print(f"WALK-FORWARD VALIDATION (train={len(train)}, test={len(test)})")
    print(f"Strategy: exit if midpoint < {thresh:.2f} at T-{cp}s")
    print(f"{'='*80}")

    for label, subset in [("TRAIN", train), ("TEST", test)]:
        baseline = sum(t["pnl"] for t in subset)
        strategy_pnl = 0
        exits = 0

        for t in subset:
            sigs = signals_by_slug.get(t["slug"], [])
            mid = get_midpoint_at_checkpoint(sigs, cp, t.get("direction"))

            if mid is not None and mid < thresh:
                shares = infer_shares(t)
                strategy_pnl += (mid - t["entry_price"]) * shares
                exits += 1
            else:
                strategy_pnl += t["pnl"]

        improvement = strategy_pnl - baseline
        print(f"\n{label}:")
        print(f"  Baseline (hold):  ${baseline:+.2f}")
        print(f"  Strategy:         ${strategy_pnl:+.2f} ({exits} early exits)")
        print(f"  Improvement:      ${improvement:+.2f}")

    return


def bucket_analysis(trades, signals_by_slug, best_strategy):
    """Break down strategy impact by entry price bucket."""
    if not best_strategy:
        return

    cp, thresh, _, _, _ = best_strategy

    print(f"\n{'='*80}")
    print(f"BUCKET ANALYSIS: exit if midpoint < {thresh:.2f} at T-{cp}s")
    print(f"{'='*80}")

    buckets = defaultdict(lambda: {"base_pnl": 0, "strat_pnl": 0, "n": 0, "exits": 0})

    for t in trades:
        ep = t["entry_price"]
        if ep < 0.60:
            b = "<0.60"
        elif ep < 0.70:
            b = "0.60-0.69"
        elif ep < 0.80:
            b = "0.70-0.79"
        elif ep < 0.90:
            b = "0.80-0.89"
        else:
            b = "0.90+"

        buckets[b]["n"] += 1
        buckets[b]["base_pnl"] += t["pnl"]

        sigs = signals_by_slug.get(t["slug"], [])
        mid = get_midpoint_at_checkpoint(sigs, cp, t.get("direction"))

        if mid is not None and mid < thresh:
            shares = infer_shares(t)
            buckets[b]["strat_pnl"] += (mid - t["entry_price"]) * shares
            buckets[b]["exits"] += 1
        else:
            buckets[b]["strat_pnl"] += t["pnl"]

    print(f"\n{'Bucket':<12} {'n':>5} {'Exits':>6} {'Base P&L':>10} {'Strat P&L':>11} {'Diff':>10}")
    print("-" * 58)
    for b in ["<0.60", "0.60-0.69", "0.70-0.79", "0.80-0.89", "0.90+"]:
        s = buckets[b]
        if s["n"] == 0:
            continue
        diff = s["strat_pnl"] - s["base_pnl"]
        print(f"{b:<12} {s['n']:>5} {s['exits']:>6} ${s['base_pnl']:>+9.2f} ${s['strat_pnl']:>+10.2f} ${diff:>+9.2f}")

    total_base = sum(s["base_pnl"] for s in buckets.values())
    total_strat = sum(s["strat_pnl"] for s in buckets.values())
    total_exits = sum(s["exits"] for s in buckets.values())
    total_n = sum(s["n"] for s in buckets.values())
    print(f"{'TOTAL':<12} {total_n:>5} {total_exits:>6} ${total_base:>+9.2f} ${total_strat:>+10.2f} ${total_strat-total_base:>+9.2f}")


def data_gap_analysis(trades, signals_by_slug):
    """Identify what data we're missing for a more robust backtest."""
    print(f"\n{'='*80}")
    print("DATA GAP ANALYSIS")
    print(f"{'='*80}")

    no_signals = sum(1 for t in trades if t["slug"] not in signals_by_slug)
    one_signal = sum(1 for t in trades if len(signals_by_slug.get(t["slug"], [])) == 1)
    multi_signal = sum(1 for t in trades if len(signals_by_slug.get(t["slug"], [])) >= 2)
    rich = sum(1 for t in trades if len(signals_by_slug.get(t["slug"], [])) >= 5)

    print(f"\n  No signal data:     {no_signals:>4} trades ({100*no_signals/len(trades):.0f}%)")
    print(f"  1 signal only:      {one_signal:>4} trades ({100*one_signal/len(trades):.0f}%)")
    print(f"  2-4 signals:        {multi_signal-rich:>4} trades ({100*(multi_signal-rich)/len(trades):.0f}%)")
    print(f"  5+ signals (rich):  {rich:>4} trades ({100*rich/len(trades):.0f}%)")

    print(f"\n  RECOMMENDATION: To improve backtest quality, add periodic midpoint")
    print(f"  snapshots to signals.db every 30s during active positions.")
    print(f"  This would increase coverage from {100*multi_signal/len(trades):.0f}% to ~100%.")

    # Check which checkpoints have best coverage
    checkpoints = [240, 180, 120, 60, 30]
    print(f"\n  Coverage by checkpoint (signals within 30s of target):")
    for cp in checkpoints:
        covered = 0
        for t in trades:
            sigs = signals_by_slug.get(t["slug"], [])
            mid = get_midpoint_at_checkpoint(sigs, cp, t.get("direction"))
            if mid is not None:
                covered += 1
        print(f"    T-{cp:>3}s: {covered:>4}/{len(trades)} ({100*covered/len(trades):.0f}%)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--perf-db", required=True)
    parser.add_argument("--signals-db", required=True)
    args = parser.parse_args()

    print("Loading data...")
    trades = load_resolved_trades(args.perf_db)
    slugs = set(t["slug"] for t in trades)
    signals_by_slug = load_signals_for_slugs(args.signals_db, slugs)

    trades_data, best = simulate_strategies(trades, signals_by_slug)
    walk_forward_validate(trades_data, signals_by_slug, best)
    bucket_analysis(trades_data, signals_by_slug, best)
    data_gap_analysis(trades_data, signals_by_slug)
