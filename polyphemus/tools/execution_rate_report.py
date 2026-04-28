#!/usr/bin/env python3
"""
Execution-rate funnel report for sharp_move (or any signal source).

Surfaces the full signal-to-fill pipeline so the operator can decompose
"WR on filled trades" from "execution rate" — closing the selection-bias
hole identified during the 2026-04-27 tiny-live triage.

Pipeline stages (per signal_logger.update_signal calls in signal_bot.py):
  shadow             — signal logged but never executed (non-BTC asset, etc.)
  guard / filtered   — rejected at signal_guard (price_out_of_range, ...)
  execution / executed       — order placed AND filled
  execution / execution_failed — order placed, FOK didn't fill or rejected

Usage:
  python3 tools/execution_rate_report.py                          # since tiny-live
  python3 tools/execution_rate_report.py --since '2026-04-26 15:49'
  python3 tools/execution_rate_report.py --source binance_momentum
  python3 tools/execution_rate_report.py --vps  # query VPS instead of local
  python3 tools/execution_rate_report.py --json # machine-readable
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LOCAL_DB = Path(__file__).resolve().parents[1] / "data" / "signals.db"
DEFAULT_VPS_DB = "/opt/lagbot/instances/emmanuel/data/signals.db"
VPS_HOST = "82.24.19.114"
TINY_LIVE_START = "2026-04-26 15:49:00"


def fetch_via_ssh(query: str) -> str:
    # Pipe the SQL via stdin so quoting/percent signs survive the shell.
    cmd = ["ssh", "-o", "ConnectTimeout=15", f"root@{VPS_HOST}",
           f"sqlite3 {DEFAULT_VPS_DB}"]
    r = subprocess.run(cmd, input=query, capture_output=True,
                       text=True, timeout=30)
    if r.returncode != 0:
        print(f"SSH/sqlite failed: {r.stderr}", file=sys.stderr)
        sys.exit(2)
    return r.stdout


def fetch_local(db: Path, query: str) -> str:
    if not db.exists():
        print(f"Local DB missing: {db}", file=sys.stderr)
        sys.exit(2)
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(query).fetchall()
        return "\n".join("|".join(str(x) for x in r) for r in rows)
    finally:
        conn.close()


def run_query(query: str, vps: bool, db: Path) -> list[list[str]]:
    raw = fetch_via_ssh(query) if vps else fetch_local(db, query)
    return [line.split("|") for line in raw.strip().split("\n") if line.strip()]


def report(args) -> dict:
    src_filter = f"source = '{args.source}'"
    time_filter = f"epoch > strftime('%s', '{args.since}')"
    where = f"WHERE {src_filter} AND {time_filter}"

    # 1. Total + outcome breakdown
    rows = run_query(
        f"SELECT outcome, pipeline_stage, COUNT(*) "
        f"FROM signals {where} GROUP BY outcome, pipeline_stage",
        args.vps, args.db,
    )
    funnel: dict[str, int] = {}
    total = 0
    for r in rows:
        if len(r) < 3:
            continue
        outcome, stage, n = r[0], r[1], int(r[2])
        funnel[f"{stage}/{outcome}"] = n
        total += n

    # 2. Per-asset funnel
    rows_asset = run_query(
        f"SELECT asset, outcome, COUNT(*) "
        f"FROM signals {where} GROUP BY asset, outcome",
        args.vps, args.db,
    )
    by_asset: dict[str, dict[str, int]] = {}
    for r in rows_asset:
        if len(r) < 3:
            continue
        asset, outcome, n = r[0], r[1], int(r[2])
        by_asset.setdefault(asset, {})[outcome] = n

    # 3. Daily fill counts
    rows_daily = run_query(
        f"SELECT date(epoch,'unixepoch') AS d, "
        f"SUM(CASE WHEN outcome='executed' THEN 1 ELSE 0 END), "
        f"SUM(CASE WHEN outcome='execution_failed' THEN 1 ELSE 0 END), "
        f"SUM(CASE WHEN outcome='filtered' THEN 1 ELSE 0 END), "
        f"SUM(CASE WHEN outcome='shadow' THEN 1 ELSE 0 END), "
        f"COUNT(*) "
        f"FROM signals {where} GROUP BY d ORDER BY d",
        args.vps, args.db,
    )
    daily = []
    for r in rows_daily:
        if len(r) < 6:
            continue
        daily.append({
            "date": r[0],
            "executed": int(r[1]),
            "execution_failed": int(r[2]),
            "filtered": int(r[3]),
            "shadow": int(r[4]),
            "total": int(r[5]),
        })

    # Compute execution rates
    btc = by_asset.get("BTC", {})
    btc_total = sum(btc.values())
    btc_filtered = btc.get("filtered", 0)
    btc_failed = btc.get("execution_failed", 0)
    btc_executed = btc.get("executed", 0)
    btc_order_attempts = btc_failed + btc_executed
    return {
        "since": args.since,
        "source": args.source,
        "total_signals": total,
        "funnel": funnel,
        "by_asset": by_asset,
        "daily": daily,
        "btc_summary": {
            "total": btc_total,
            "filtered_at_guard": btc_filtered,
            "reached_order_layer": btc_order_attempts,
            "fok_timeout": btc_failed,
            "executed": btc_executed,
            "execution_rate_among_order_attempts": (
                round(btc_executed / btc_order_attempts, 3)
                if btc_order_attempts else None
            ),
            "execution_rate_among_all_btc_signals": (
                round(btc_executed / btc_total, 3)
                if btc_total else None
            ),
        },
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def print_human(rep: dict) -> None:
    btc = rep["btc_summary"]
    print(f"=== execution-rate report ===")
    print(f"source: {rep['source']} | since: {rep['since']} | run: {rep['ts']}")
    print()
    print(f"BTC sharp_move funnel:")
    print(f"  total signals          : {btc['total']}")
    print(f"  filtered at guard      : {btc['filtered_at_guard']}  "
          f"({pct(btc['filtered_at_guard'], btc['total'])})")
    print(f"  reached order layer    : {btc['reached_order_layer']}  "
          f"({pct(btc['reached_order_layer'], btc['total'])})")
    print(f"    FOK timeout/rejected : {btc['fok_timeout']}")
    print(f"    executed             : {btc['executed']}")
    print()
    er_oa = btc["execution_rate_among_order_attempts"]
    er_all = btc["execution_rate_among_all_btc_signals"]
    print(f"execution-rate (executed / order-attempts) : {er_oa}")
    print(f"execution-rate (executed / all BTC signals): {er_all}")
    print()
    print("daily fills (executed only):")
    for d in rep["daily"]:
        print(f"  {d['date']} : executed={d['executed']}  "
              f"failed={d['execution_failed']}  filtered={d['filtered']}  "
              f"shadow={d['shadow']}  total={d['total']}")
    print()
    print("residual: this report is bug-class only — it shows where the funnel")
    print("loses signals, not whether the strategy has true alpha. Pair with /forecast")
    print("for P&L view; pair with /invariant-audit for codebase reliability.")


def pct(n: int, d: int) -> str:
    return f"{(n/d*100):.1f}%" if d else "—"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", default="sharp_move")
    p.add_argument("--since", default=TINY_LIVE_START)
    p.add_argument("--vps", action="store_true",
                   help="Query VPS signals.db over SSH (default: local)")
    p.add_argument("--db", type=Path, default=DEFAULT_LOCAL_DB)
    p.add_argument("--json", action="store_true",
                   help="Emit JSON instead of human-readable")
    args = p.parse_args()

    rep = report(args)
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        print_human(rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
