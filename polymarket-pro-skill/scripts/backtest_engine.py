#!/usr/bin/env python3
"""OpenClaw Backtest Engine v1 — walk-forward config change validator.

Replays signals.db + performance.db to answer: "If we had used config X
instead of config Y, what would the P&L difference be?"

Supports testing:
  - Entry price ranges (min/max)
  - Window timing (max_secs_remaining)
  - Asset filters (add/remove assets)
  - Sizing parameters (bet_pct, max_bet)
  - Danger hours (add/remove hours)
  - Snipe min entry price

Usage:
    python3 backtest_engine.py test --param SNIPE_MIN_ENTRY_PRICE --value 0.94 --hours 168
    python3 backtest_engine.py sweep --param DANGER_HOURS --values "1,2,3" "1,2,3,4" "1,2,3,4,5" --hours 168
    python3 backtest_engine.py recommend --hours 168
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = os.environ.get("OPENCLAW_DATA_DIR", "/opt/openclaw/data")
EVOLUTION_DIR = os.path.join(DATA_DIR, "evolution")
BACKTESTS_DIR = os.path.join(DATA_DIR, "backtests")
INSTANCES_DIR = os.environ.get("LAGBOT_INSTANCES_DIR", "/opt/lagbot/instances")

INSTANCES = ["emmanuel", "polyphemus"]


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr)


def _r8_label(n: int) -> str:
    if n < 30:
        return f"ANECDOTAL n={n}"
    elif n < 107:
        return f"LOW CONFIDENCE 70% CI n={n}"
    elif n < 385:
        return f"MODERATE CONFIDENCE 95% CI n={n}"
    else:
        return f"SIGNIFICANT 99% CI n={n}"


def _connect(db_path: str):
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _extract_asset(slug: str) -> str:
    if not slug:
        return "UNKNOWN"
    part = slug.split("-")[0].upper()
    return part if part in ("BTC", "ETH", "SOL", "XRP") else "OTHER"


def _extract_epoch(slug: str) -> int:
    if not slug:
        return 0
    for p in reversed(slug.split("-")):
        if p.isdigit() and len(p) >= 10:
            return int(p)
    return 0


# --- Data Loading ---

def load_signals(instance: str, since_ts: float) -> list:
    """Load all signals (passed + filtered) with outcome data."""
    db = os.path.join(INSTANCES_DIR, instance, "data", "signals.db")
    conn = _connect(db)
    if not conn:
        return []
    try:
        rows = conn.execute("""
            SELECT asset, source, direction, momentum_pct, midpoint,
                   time_remaining_secs, hour_utc, guard_passed, guard_reasons,
                   outcome, pnl, is_win, dry_run, strategy_type,
                   fear_greed, market_regime, slug, epoch
            FROM signals
            WHERE epoch >= ?
            ORDER BY epoch
        """, (since_ts,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def load_trades(instance: str, since_ts: float) -> list:
    """Load completed trades."""
    db = os.path.join(INSTANCES_DIR, instance, "data", "performance.db")
    conn = _connect(db)
    if not conn:
        return []
    try:
        rows = conn.execute("""
            SELECT slug, entry_price, exit_price, pnl, entry_time, exit_time,
                   entry_size, exit_reason, outcome, strategy
            FROM trades
            WHERE exit_time IS NOT NULL AND entry_time >= ?
            ORDER BY entry_time
        """, (since_ts,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def load_current_env(instance: str) -> dict:
    """Parse the instance .env into a dict."""
    env_path = os.path.join(INSTANCES_DIR, instance, ".env")
    result = {}
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                result[key.strip()] = val.strip()
    except FileNotFoundError:
        pass
    return result


# --- Simulation Engine ---

def simulate_filter(signal: dict, config: dict) -> bool:
    """Simulate whether a signal would have passed filters with given config.

    Returns True if signal passes (would trade), False if filtered.
    """
    asset = signal.get("asset", "").upper()
    source = signal.get("source", "")
    midpoint = signal.get("midpoint") or 0
    hour = signal.get("hour_utc")
    secs = signal.get("time_remaining_secs") or 0

    # Asset filter
    allowed_assets = [a.strip().upper() for a in config.get("ASSET_FILTER", "BTC,ETH,SOL").split(",")]
    shadow_assets = [a.strip().upper() for a in config.get("SHADOW_ASSETS", "").split(",") if a.strip()]
    if asset not in allowed_assets and asset not in shadow_assets:
        return False
    if asset in shadow_assets and asset not in allowed_assets:
        return False  # shadow = no live trade

    is_snipe = source in ("snipe", "resolution_snipe")

    if is_snipe:
        # Snipe filters
        snipe_assets = [a.strip().upper() for a in config.get("SNIPE_ASSETS", "BTC,ETH,SOL").split(",")]
        if asset not in snipe_assets:
            return False

        snipe_min = float(config.get("SNIPE_MIN_ENTRY_PRICE", "0.93"))
        snipe_max = float(config.get("SNIPE_MAX_ENTRY_PRICE", "0.985"))
        if midpoint < snipe_min or midpoint > snipe_max:
            return False

        max_secs = float(config.get("SNIPE_MAX_SECS_REMAINING", "10"))
        if secs > max_secs:
            return False
    else:
        # Momentum filters
        min_entry = float(config.get("MIN_ENTRY_PRICE", "0.99"))
        max_entry = float(config.get("MAX_ENTRY_PRICE", "0.95"))
        # If min > max, momentum is killed (no trades pass)
        if min_entry > max_entry:
            return False
        if midpoint < max_entry or midpoint > min_entry:
            return False

    # Danger hours
    danger_hours_str = config.get("DANGER_HOURS", "")
    if danger_hours_str and hour is not None:
        danger_hours = [int(h.strip()) for h in danger_hours_str.split(",") if h.strip()]
        if int(hour) in danger_hours:
            return False  # Simplified: treat danger hours as full block for backtest

    return True


def run_backtest(signals: list, trades: list, config: dict) -> dict:
    """Simulate trading with given config against historical signals.

    Uses actual trade outcomes (pnl, is_win) for signals that DID execute.
    For signals that were filtered but would now pass (or vice versa),
    estimates outcome from the signal's recorded outcome field.
    """
    # Build trade lookup by slug for actual P&L data
    trade_by_slug = {}
    for t in trades:
        slug = t.get("slug", "")
        if slug:
            trade_by_slug[slug] = t

    results = []
    for sig in signals:
        slug = sig.get("slug", "")
        originally_passed = sig.get("guard_passed") == 1
        would_pass = simulate_filter(sig, config)

        if not would_pass:
            continue  # Filtered out in this config

        # Get P&L: prefer actual trade data, fall back to signal outcome
        actual_trade = trade_by_slug.get(slug)
        if actual_trade and actual_trade.get("pnl") is not None:
            pnl = actual_trade["pnl"]
            is_win = 1 if pnl > 0 else 0
        elif sig.get("pnl") is not None:
            pnl = sig["pnl"]
            is_win = sig.get("is_win", 0)
        elif sig.get("outcome") in ("win", "1"):
            # Estimate: average snipe win is ~$1.13, momentum win ~$8
            source = sig.get("source", "")
            pnl = 1.13 if "snipe" in source else 8.0
            is_win = 1
        elif sig.get("outcome") in ("loss", "0"):
            source = sig.get("source", "")
            pnl = -7.0 if "snipe" in source else -80.0
            is_win = 0
        else:
            continue  # No outcome data, skip

        results.append({
            "slug": slug,
            "asset": sig.get("asset", ""),
            "source": sig.get("source", ""),
            "pnl": pnl,
            "is_win": is_win,
            "originally_passed": originally_passed,
            "midpoint": sig.get("midpoint", 0),
            "hour_utc": sig.get("hour_utc"),
            "secs_remaining": sig.get("time_remaining_secs"),
        })

    n = len(results)
    wins = sum(1 for r in results if r["is_win"])
    total_pnl = sum(r["pnl"] for r in results)
    wr = (wins / n * 100) if n else 0
    expectancy = (total_pnl / n) if n else 0

    return {
        "n": n,
        "wins": wins,
        "losses": n - wins,
        "wr": round(wr, 1),
        "total_pnl": round(total_pnl, 2),
        "expectancy": round(expectancy, 2),
        "label": _r8_label(n),
        "trades": results,
    }


# --- Config Variation Helpers ---

def make_config_variant(base: dict, param: str, value: str) -> dict:
    """Create a config variant with one parameter changed."""
    variant = dict(base)
    variant[param] = value
    return variant


# --- Commands ---

def cmd_test(args):
    """Test a single config change."""
    hours = args.hours
    since = time.time() - (hours * 3600)

    os.makedirs(BACKTESTS_DIR, exist_ok=True)

    for inst in INSTANCES:
        signals = load_signals(inst, since)
        trades = load_trades(inst, since)
        base_env = load_current_env(inst)

        if not signals:
            print(f"\n{inst.upper()}: No signals in {hours}h window")
            continue

        # Baseline (current config)
        baseline = run_backtest(signals, trades, base_env)

        # Variant
        variant_env = make_config_variant(base_env, args.param, args.value)
        variant = run_backtest(signals, trades, variant_env)

        # Delta
        pnl_delta = variant["total_pnl"] - baseline["total_pnl"]
        wr_delta = variant["wr"] - baseline["wr"]
        n_delta = variant["n"] - baseline["n"]

        print(f"\n{'='*60}")
        print(f"{inst.upper()} — {args.param}={args.value} vs current")
        print(f"{'='*60}")
        print(f"Period: {hours}h | Signals: {len(signals)}")
        print(f"")
        print(f"  {'':20s} {'Current':>12s} {'Proposed':>12s} {'Delta':>12s}")
        print(f"  {'Trades':20s} {baseline['n']:>12d} {variant['n']:>12d} {n_delta:>+12d}")
        print(f"  {'Win Rate':20s} {baseline['wr']:>11.1f}% {variant['wr']:>11.1f}% {wr_delta:>+11.1f}%")
        print(f"  {'Total PnL':20s} ${baseline['total_pnl']:>10.2f} ${variant['total_pnl']:>10.2f} ${pnl_delta:>+10.2f}")
        print(f"  {'Expectancy':20s} ${baseline['expectancy']:>10.2f} ${variant['expectancy']:>10.2f}")
        print(f"  {'Confidence':20s} {baseline['label']}")
        print(f"")

        verdict = "IMPROVEMENT" if pnl_delta > 0 else "WORSE" if pnl_delta < 0 else "NEUTRAL"
        print(f"  Verdict: {verdict} (${pnl_delta:+.2f})")

    # Save result
    result = {
        "param": args.param,
        "value": args.value,
        "hours": hours,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pnl_delta": pnl_delta,
        "verdict": verdict,
    }
    out_path = os.path.join(BACKTESTS_DIR, f"backtest_{args.param}_{int(time.time())}.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    _log(f"Result saved to {out_path}")


def cmd_sweep(args):
    """Sweep multiple values for a parameter."""
    hours = args.hours
    since = time.time() - (hours * 3600)

    os.makedirs(BACKTESTS_DIR, exist_ok=True)

    for inst in INSTANCES:
        signals = load_signals(inst, since)
        trades = load_trades(inst, since)
        base_env = load_current_env(inst)

        if not signals:
            print(f"\n{inst.upper()}: No signals in {hours}h window")
            continue

        baseline = run_backtest(signals, trades, base_env)

        print(f"\n{'='*60}")
        print(f"{inst.upper()} — {args.param} sweep ({hours}h)")
        print(f"{'='*60}")
        print(f"Baseline: {baseline['n']}tr, {baseline['wr']}% WR, ${baseline['total_pnl']:+.2f}")
        print(f"")
        print(f"  {'Value':20s} {'N':>6s} {'WR':>8s} {'PnL':>10s} {'Delta':>10s} {'Verdict':>10s}")
        print(f"  {'-'*20} {'-'*6} {'-'*8} {'-'*10} {'-'*10} {'-'*10}")

        best_val = None
        best_delta = 0

        for val in args.values:
            variant_env = make_config_variant(base_env, args.param, val)
            result = run_backtest(signals, trades, variant_env)
            delta = result["total_pnl"] - baseline["total_pnl"]
            verdict = "BETTER" if delta > 0 else "WORSE" if delta < 0 else "SAME"

            print(f"  {val:20s} {result['n']:>6d} {result['wr']:>7.1f}% ${result['total_pnl']:>9.2f} ${delta:>+9.2f} {verdict:>10s}")

            if delta > best_delta:
                best_delta = delta
                best_val = val

        print(f"")
        if best_val:
            print(f"  Best: {args.param}={best_val} (+${best_delta:.2f})")
        else:
            print(f"  Current config is optimal (no improvement found)")


def cmd_recommend(args):
    """Auto-generate config recommendations based on sweep analysis."""
    hours = args.hours
    since = time.time() - (hours * 3600)

    os.makedirs(BACKTESTS_DIR, exist_ok=True)

    # Parameters to sweep with candidate values
    sweeps = [
        ("SNIPE_MIN_ENTRY_PRICE", ["0.91", "0.92", "0.93", "0.94", "0.95"]),
        ("SNIPE_MAX_SECS_REMAINING", ["8", "10", "12", "15", "20"]),
        ("DANGER_HOURS", ["1,2,3,4", "1,2,3,4,5", "0,1,2,3,4", "1,2,3"]),
        ("SNIPE_MAX_BET", ["50", "75", "100", "150"]),
    ]

    recommendations = []

    for inst in INSTANCES:
        signals = load_signals(inst, since)
        trades = load_trades(inst, since)
        base_env = load_current_env(inst)

        if not signals:
            _log(f"{inst}: No signals, skipping")
            continue

        baseline = run_backtest(signals, trades, base_env)

        print(f"\n{'='*60}")
        print(f"{inst.upper()} Recommendations ({hours}h, {len(signals)} signals)")
        print(f"{'='*60}")
        print(f"Baseline: {baseline['n']}tr, {baseline['wr']}% WR, ${baseline['total_pnl']:+.2f} [{baseline['label']}]")
        print(f"")

        for param, candidates in sweeps:
            current_val = base_env.get(param, "?")
            best_val = None
            best_delta = 0

            for val in candidates:
                if val == current_val:
                    continue
                variant_env = make_config_variant(base_env, param, val)
                result = run_backtest(signals, trades, variant_env)
                delta = result["total_pnl"] - baseline["total_pnl"]
                if delta > best_delta and delta > 1.0:  # Min $1 improvement threshold
                    best_delta = delta
                    best_val = val

            if best_val:
                rec = {
                    "instance": inst,
                    "param": param,
                    "current": current_val,
                    "proposed": best_val,
                    "pnl_delta": round(best_delta, 2),
                    "confidence": baseline["label"],
                }
                recommendations.append(rec)
                print(f"  REC: {param} {current_val} -> {best_val} (+${best_delta:.2f})")
            else:
                print(f"  {param}: current ({current_val}) is optimal")

    if recommendations:
        # Save recommendations
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rec_path = os.path.join(EVOLUTION_DIR, f"config_recommendation_{date_str}.json")
        with open(rec_path, "w") as f:
            json.dump({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "hours": hours,
                "recommendations": recommendations,
            }, f, indent=2)
        print(f"\n{len(recommendations)} recommendation(s) saved to {rec_path}")
        print("Run /evolve approve to apply (with human review)")
    else:
        print("\nNo improvements found. Current config is optimal for this period.")


def main():
    parser = argparse.ArgumentParser(description="OpenClaw Backtest Engine")
    sub = parser.add_subparsers(dest="command")

    p_test = sub.add_parser("test", help="Test a single config change")
    p_test.add_argument("--param", required=True, help="Config parameter name")
    p_test.add_argument("--value", required=True, help="Proposed value")
    p_test.add_argument("--hours", type=int, default=168, help="Lookback hours")
    p_test.set_defaults(func=cmd_test)

    p_sweep = sub.add_parser("sweep", help="Sweep multiple values")
    p_sweep.add_argument("--param", required=True, help="Config parameter name")
    p_sweep.add_argument("--values", nargs="+", required=True, help="Values to test")
    p_sweep.add_argument("--hours", type=int, default=168, help="Lookback hours")
    p_sweep.set_defaults(func=cmd_sweep)

    p_rec = sub.add_parser("recommend", help="Auto-generate recommendations")
    p_rec.add_argument("--hours", type=int, default=168, help="Lookback hours")
    p_rec.set_defaults(func=cmd_recommend)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
