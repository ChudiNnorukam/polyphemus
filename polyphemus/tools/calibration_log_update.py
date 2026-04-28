#!/usr/bin/env python3
"""
Continuous calibration loop for the sharp_move tiny-live experiment.

Queries VPS live data, computes predicted-vs-actual deltas on every
pre-committed metric, and appends a row to
~/.claude/sharp-move-calibration.jsonl.

Predicted bands (from sharp-move-tiny-live-experiment.md + my forecast):
  raw_wr               : 0.88 - 0.94  (haircut from backtest's 0.994)
  wilson_lb_wr         : 0.72 - 0.83  (n=30 projection)
  mean_adverse_fill_bps: 30 - 55      (haircut from backtest's 24)
  execution_rate       : 0.30 - 0.50  (above 0.50 unlocks PROMOTE)

Each row in the log is the substrate for cycle-2 simulator + retrospective
calibration audit. Append-only by design.

Usage:
  python3 tools/calibration_log_update.py            # query, compute, append
  python3 tools/calibration_log_update.py --view     # show last 10 rows
  python3 tools/calibration_log_update.py --view-all # show all rows
  python3 tools/calibration_log_update.py --json     # machine-readable
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG = Path.home() / ".claude" / "sharp-move-calibration.jsonl"
VPS = "82.24.19.114"
PERF_DB = "/opt/lagbot/instances/emmanuel/data/performance.db"
SIG_DB = "/opt/lagbot/instances/emmanuel/data/signals.db"
TINY_LIVE_START = "2026-04-26 15:49:00"

# Pre-committed prediction bands (from sharp-move-tiny-live-experiment.md
# + my Day-0 forecast). Edits to these MUST be ratified — they're the
# falsifiable claim being tested.
PREDICTIONS = {
    "raw_wr": {"low": 0.88, "high": 0.94, "kill_below": 0.50, "promote_above": 0.55},
    "wilson_lb_wr": {"low": 0.72, "high": 0.83, "kill_below": 0.50},
    "mean_adverse_fill_bps": {"low": 30, "high": 55, "kill_above": 30, "promote_below": 15},
    "execution_rate": {"low": 0.30, "high": 0.50, "promote_above": 0.50},
    "n_fills_target": {"by_2026_05_10": 30},
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ssh_sqlite(db: str, query: str) -> str:
    cmd = ["ssh", "-o", "ConnectTimeout=15", f"root@{VPS}", f"sqlite3 {db}"]
    r = subprocess.run(cmd, input=query, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"ssh+sqlite failed: {r.stderr}")
    return r.stdout


def wilson_lb(wins: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - margin) / denom


def query_actuals() -> dict:
    """Compute current actuals against the live VPS."""
    # Trades since tiny-live activation
    trades_q = (
        "SELECT COUNT(*) || '|' || "
        "  SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) || '|' || "
        "  SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) || '|' || "
        "  ROUND(SUM(pnl), 4) || '|' || "
        "  ROUND(AVG(adverse_fill_bps), 4) "
        f"FROM trades WHERE entry_time > strftime('%s', '{TINY_LIVE_START}') "
        "  AND signal_source IN ('sharp_move', 'binance_momentum') "
        "  AND is_dry_run = 0 AND exit_time IS NOT NULL;"
    )
    raw = ssh_sqlite(PERF_DB, trades_q).strip()
    parts = (raw.split("|") + [""] * 5)[:5]

    def _num(x: str, cast=float, default=0):
        x = x.strip()
        if not x or x.lower() == "null":
            return default
        try:
            return cast(x)
        except (ValueError, TypeError):
            return default

    n = _num(parts[0], int, 0)
    wins = _num(parts[1], int, 0)
    losses = _num(parts[2], int, 0)
    cum_pnl = _num(parts[3], float, 0.0)
    mean_adverse = _num(parts[4], float, None)

    # Distinct ISO weeks (P9 disjoint-window check)
    weeks_q = (
        "SELECT COUNT(DISTINCT strftime('%Y-W%W', datetime(entry_time, 'unixepoch'))) "
        f"FROM trades WHERE entry_time > strftime('%s', '{TINY_LIVE_START}') "
        "  AND signal_source IN ('sharp_move', 'binance_momentum') "
        "  AND is_dry_run = 0 AND exit_time IS NOT NULL;"
    )
    distinct_weeks = _num(ssh_sqlite(PERF_DB, weeks_q).strip(), int, 0)

    # Execution-rate funnel from signals.db
    funnel_q = (
        "SELECT outcome || ',' || COUNT(*) FROM signals "
        "WHERE source = 'sharp_move' "
        f"  AND epoch > strftime('%s', '{TINY_LIVE_START}') "
        "  AND (slug LIKE 'btc%') "
        "GROUP BY outcome;"
    )
    funnel_raw = ssh_sqlite(SIG_DB, funnel_q).strip().splitlines()
    funnel = {}
    for line in funnel_raw:
        if "," in line:
            o, c = line.split(",")
            funnel[o.strip()] = int(c.strip())
    btc_executed = funnel.get("executed", 0)
    btc_failed = funnel.get("execution_failed", 0)
    btc_filtered = funnel.get("filtered", 0)
    btc_order_attempts = btc_executed + btc_failed
    execution_rate = btc_executed / btc_order_attempts if btc_order_attempts else None

    raw_wr = wins / n if n else None
    wlb = wilson_lb(wins, n) if n else None
    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "raw_wr": round(raw_wr, 4) if raw_wr is not None else None,
        "wilson_lb_wr": round(wlb, 4) if wlb is not None else None,
        "cum_pnl": cum_pnl,
        "mean_adverse_fill_bps": mean_adverse,
        "distinct_weeks": distinct_weeks,
        "btc_funnel": {
            "executed": btc_executed,
            "execution_failed": btc_failed,
            "filtered_at_guard": btc_filtered,
            "order_attempts": btc_order_attempts,
        },
        "execution_rate": round(execution_rate, 4) if execution_rate is not None else None,
    }


MIN_N_FOR_VERDICT_PRESSURE = 30  # pre-committed threshold; below this, no kill/promote signals


def compute_deltas(actuals: dict) -> dict:
    """For each predicted metric: in-band? above/below? distance to kill/promote.

    Verdict pressure is suppressed below MIN_N_FOR_VERDICT_PRESSURE (30) per
    falsifiable-prediction-discipline: the pre-committed verdict thresholds
    (Wilson LB >= 0.50, adverse < 30, execution_rate >= 0.50) only bind at
    n>=30. Pre-n=30 they are observational, not actionable.
    """
    n_fills = actuals.get("n", 0) or 0
    n_gate_open = n_fills >= MIN_N_FOR_VERDICT_PRESSURE

    deltas = {}
    for metric, bands in PREDICTIONS.items():
        if metric == "n_fills_target":
            target = bands.get("by_2026_05_10")
            deltas[metric] = {
                "actual": n_fills, "target": target,
                "pct_complete": round(n_fills / target, 4) if target else None,
                "on_pace": n_fills >= target * (
                    (datetime.now(timezone.utc) -
                     datetime(2026, 4, 26, 15, 49, 0, tzinfo=timezone.utc)).total_seconds()
                    / (14 * 86400)
                ),
            }
            continue
        actual = actuals.get(metric)
        if actual is None:
            deltas[metric] = {"actual": None, "in_band": None,
                              "verdict_pressure": ["unknown"]}
            continue
        low, high = bands.get("low"), bands.get("high")
        in_band = (low is None or actual >= low) and (high is None or actual <= high)
        verdict_pressure: list[str] = []
        if not n_gate_open:
            verdict_pressure.append(f"PRE_N30 (n={n_fills}, gate at {MIN_N_FOR_VERDICT_PRESSURE})")
        else:
            if "kill_below" in bands and actual < bands["kill_below"]:
                verdict_pressure.append("KILL")
            if "kill_above" in bands and actual > bands["kill_above"]:
                verdict_pressure.append("KILL")
            if "promote_above" in bands and actual >= bands["promote_above"]:
                verdict_pressure.append("PROMOTE_ELIGIBLE")
            if "promote_below" in bands and actual <= bands["promote_below"]:
                verdict_pressure.append("PROMOTE_ELIGIBLE")
            if not verdict_pressure:
                verdict_pressure.append("NEUTRAL")
        deltas[metric] = {
            "actual": actual,
            "predicted_band": [low, high],
            "in_band": in_band,
            "verdict_pressure": verdict_pressure,
        }
    return deltas


def append_row(row: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(row, default=str) + "\n")


def view_log(limit: int | None = 10) -> None:
    if not LOG.exists():
        print(f"no calibration log yet at {LOG}")
        return
    rows = [json.loads(l) for l in LOG.read_text().splitlines() if l.strip()]
    if limit:
        rows = rows[-limit:]
    print(f"=== calibration log ({len(rows)} rows shown, total {sum(1 for _ in LOG.open())}) ===")
    for r in rows:
        n = r.get("actuals", {}).get("n", "?")
        wr = r.get("actuals", {}).get("raw_wr", "?")
        wlb = r.get("actuals", {}).get("wilson_lb_wr", "?")
        er = r.get("actuals", {}).get("execution_rate", "?")
        adv = r.get("actuals", {}).get("mean_adverse_fill_bps", "?")
        print(
            f"  {r.get('ts', '?')}  n={n:>3}  WR={wr}  WilsonLB={wlb}  "
            f"exec_rate={er}  adv_bps={adv}"
        )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--view", action="store_true", help="show last 10 rows")
    p.add_argument("--view-all", action="store_true", help="show all rows")
    p.add_argument("--json", action="store_true", help="emit row as JSON to stdout")
    p.add_argument("--no-append", action="store_true",
                   help="compute but don't append (dry-run)")
    args = p.parse_args()

    if args.view or args.view_all:
        view_log(limit=None if args.view_all else 10)
        return 0

    actuals = query_actuals()
    deltas = compute_deltas(actuals)
    row = {
        "ts": now_iso(),
        "experiment_decision_id": "dc-20260426T155020Z-sharp-move-tiny-live-activated",
        "actuals": actuals,
        "predictions": PREDICTIONS,
        "deltas": deltas,
    }

    if args.json:
        print(json.dumps(row, indent=2, default=str))
    else:
        n = actuals["n"]
        print(f"=== calibration row @ {row['ts']} (n={n}) ===")
        for metric, d in deltas.items():
            if metric == "n_fills_target":
                pct = d.get("pct_complete")
                pace = "ON_PACE" if d.get("on_pace") else "OFF_PACE"
                print(f"  {metric:<28} actual={d.get('actual')}  "
                      f"target={d.get('target')}  ({(pct or 0) * 100:.0f}% complete, {pace})")
                continue
            actual = d.get("actual")
            in_band = d.get("in_band")
            pressure = ",".join(d.get("verdict_pressure", []))
            print(f"  {metric:<28} actual={actual}  in_band={in_band}  pressure={pressure}")

    if not args.no_append:
        append_row(row)
        print(f"\n[appended row to {LOG}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
