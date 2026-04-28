#!/usr/bin/env python3
"""
Cycle-2 strategy variant simulator for sharp_move tiny-live.

Replays the live BTC sharp_move fills under alternative parameters
(entry-price floor, alternative Markov thresholds) and reports the
counterfactual WR + cumulative P&L. Output guides the cycle-2
experiment design.

GATED on n>=15. Counterfactual analysis on smaller samples is too
noisy to be defensible (one flipped trade can invert any verdict).

Usage:
  python3 tools/cycle2_simulator.py            # all variants vs current
  python3 tools/cycle2_simulator.py --n-floor 10  # override n>=15 gate (NOT recommended)
  python3 tools/cycle2_simulator.py --json     # machine-readable

Outputs go to docs/codex/cycle2-simulations/<timestamp>.json. The
sharp-move-cycle-2-design.md node consumes these as evidence for
proposed cycle-2 parameters.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

VPS = "82.24.19.114"
PERF_DB = "/opt/lagbot/instances/emmanuel/data/performance.db"
TINY_LIVE_START = "2026-04-26 15:49:00"
DEFAULT_N_FLOOR = 15
OUT_DIR = Path(__file__).resolve().parents[1] / "docs" / "codex" / "cycle2-simulations"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ssh_sqlite(query: str) -> str:
    cmd = ["ssh", "-o", "ConnectTimeout=15", f"root@{VPS}", f"sqlite3 {PERF_DB}"]
    r = subprocess.run(cmd, input=query, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"ssh+sqlite failed: {r.stderr}")
    return r.stdout


def fetch_fills() -> list[dict]:
    """Return all real BTC sharp_move fills since tiny-live activation."""
    q = (
        "SELECT entry_time, exit_time, slug, entry_price, pnl, "
        "       adverse_fill_bps, exit_reason "
        "FROM trades "
        f"WHERE entry_time > strftime('%s', '{TINY_LIVE_START}') "
        "  AND signal_source IN ('sharp_move', 'binance_momentum') "
        "  AND is_dry_run = 0 AND exit_time IS NOT NULL "
        "ORDER BY entry_time;"
    )
    fills = []
    for line in ssh_sqlite(q).strip().splitlines():
        cols = line.split("|")
        if len(cols) < 7:
            continue

        def _f(s: str, default=None):
            s = s.strip()
            if not s or s.lower() == "null":
                return default
            try:
                return float(s)
            except ValueError:
                return default

        fills.append({
            "entry_time": _f(cols[0]),
            "exit_time": _f(cols[1]),
            "slug": cols[2].strip(),
            "entry_price": _f(cols[3]),
            "pnl": _f(cols[4], 0.0),
            "adverse_fill_bps": _f(cols[5]),
            "exit_reason": cols[6].strip(),
        })
    return fills


def wilson_lb(wins: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - margin) / denom


def simulate_variant(fills: list[dict], filter_fn, label: str) -> dict:
    """Apply filter_fn to fills, compute WR + cum_pnl + Wilson LB."""
    kept = [f for f in fills if filter_fn(f)]
    n = len(kept)
    wins = sum(1 for f in kept if f["pnl"] > 0)
    losses = sum(1 for f in kept if f["pnl"] < 0)
    cum = round(sum(f["pnl"] for f in kept), 3)
    return {
        "variant": label,
        "n": n,
        "wins": wins,
        "losses": losses,
        "raw_wr": round(wins / n, 4) if n else None,
        "wilson_lb": round(wilson_lb(wins, n), 4) if n else None,
        "cum_pnl": cum,
        "delta_n_vs_baseline": None,  # filled in main
        "delta_pnl_vs_baseline": None,
    }


def simulate_markov(fills: list[dict], max_losses: int) -> dict:
    """Simulate Markov gate with alternative consecutive-loss threshold.
    Trades are accepted in order; once N consecutive losses hit, halt for
    rest of sample. (Live Markov has auto-probe, but baseline pass is
    sufficient for cycle-2 sensitivity check.)
    """
    consec_l = 0
    halted = False
    kept = []
    for f in fills:
        if halted:
            continue
        kept.append(f)
        if f["pnl"] < 0:
            consec_l += 1
            if consec_l >= max_losses:
                halted = True
        else:
            consec_l = 0
    n = len(kept)
    wins = sum(1 for f in kept if f["pnl"] > 0)
    cum = round(sum(f["pnl"] for f in kept), 3)
    return {
        "variant": f"markov_{max_losses}",
        "n": n,
        "wins": wins,
        "losses": sum(1 for f in kept if f["pnl"] < 0),
        "raw_wr": round(wins / n, 4) if n else None,
        "wilson_lb": round(wilson_lb(wins, n), 4) if n else None,
        "cum_pnl": cum,
        "halted_after": "n=" + str(n) + (" (gate fired)" if halted else " (no gate fire)"),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--n-floor", type=int, default=DEFAULT_N_FLOOR,
                   help=f"minimum sample size to run (default {DEFAULT_N_FLOOR})")
    p.add_argument("--json", action="store_true", help="emit JSON to stdout")
    p.add_argument("--no-write", action="store_true",
                   help="do not write outputs to docs/codex/cycle2-simulations/")
    args = p.parse_args()

    fills = fetch_fills()
    if len(fills) < args.n_floor:
        print(f"GATED: n={len(fills)} < n_floor={args.n_floor}", file=sys.stderr)
        print(f"Counterfactual analysis on n<{args.n_floor} is too noisy "
              f"(one flipped trade inverts the verdict). Re-run when n>=", file=sys.stderr)
        print(f"{args.n_floor}.", file=sys.stderr)
        return 2

    baseline = simulate_variant(fills, lambda f: True, "baseline (current)")

    variants = [
        baseline,
        simulate_variant(fills, lambda f: f["entry_price"] >= 0.85, "entry_floor_0.85"),
        simulate_variant(fills, lambda f: f["entry_price"] >= 0.75, "entry_floor_0.75"),
        simulate_variant(fills, lambda f: f["entry_price"] >= 0.65, "entry_floor_0.65"),
        simulate_markov(fills, 2),
        simulate_markov(fills, 3),
        simulate_markov(fills, 4),
        simulate_markov(fills, 5),
    ]
    # Compute deltas vs baseline
    base_n, base_pnl = baseline["n"], baseline["cum_pnl"]
    for v in variants:
        v["delta_n_vs_baseline"] = v["n"] - base_n
        v["delta_pnl_vs_baseline"] = round(v["cum_pnl"] - base_pnl, 3)

    out = {
        "ts": now_iso(),
        "n_fills": len(fills),
        "baseline": baseline,
        "variants": variants,
    }

    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print(f"=== cycle-2 simulator @ {out['ts']} (n={len(fills)} fills) ===\n")
        print(f"  {'variant':<22} {'n':>3} {'WR':>7} {'WilsonLB':>9} "
              f"{'cum_pnl':>9} {'dN':>5} {'dPnL':>8}")
        print(f"  {'-'*22} {'-'*3} {'-'*7} {'-'*9} {'-'*9} {'-'*5} {'-'*8}")
        for v in variants:
            wr = f"{v['raw_wr']:.3f}" if v.get("raw_wr") is not None else "—"
            wlb = f"{v['wilson_lb']:.3f}" if v.get("wilson_lb") is not None else "—"
            print(
                f"  {v['variant']:<22} {v['n']:>3} {wr:>7} {wlb:>9} "
                f"{v['cum_pnl']:>9.3f} {v['delta_n_vs_baseline']:>+5} "
                f"{v['delta_pnl_vs_baseline']:>+8.3f}"
            )
        print(f"\nresidual: counterfactual on n={len(fills)} is informative but not")
        print("  decisive; the cycle-2 design should ratify a variant only when its")
        print("  baseline-relative delta is robust to ±1 fill flip. See cycle-2 design")
        print("  node when authored.")

    if not args.no_write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        ts_safe = out["ts"].replace(":", "").replace("-", "")
        out_path = OUT_DIR / f"sim-{ts_safe}.json"
        out_path.write_text(json.dumps(out, indent=2, default=str))
        print(f"\n[wrote {out_path.relative_to(Path.cwd()) if out_path.is_relative_to(Path.cwd()) else out_path}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
