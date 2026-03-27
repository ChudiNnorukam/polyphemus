#!/usr/bin/env python3
"""Post-Trade Attribution - Generate one-line trade explanations.

Standalone script. Can be called after trade resolution or run as batch.
No LLM needed - uses feature importance from signal data.

Usage:
    python3 trade_attribution.py --instance emmanuel --last 10
    python3 trade_attribution.py --instance emmanuel --today

Cron (every 30 min, attributes unattributed trades):
    */30 * * * * cd /opt/lagbot && /opt/lagbot/venv/bin/python3 lagbot/tools/trade_attribution.py --instance emmanuel --unattributed
"""

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timezone


def get_env(instance: str) -> dict:
    env_path = f"/opt/lagbot/instances/{instance}/.env"
    env = {}
    if os.path.exists(env_path):
        for line in open(env_path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def attribute_trade(trade: dict, signal: dict = None) -> str:
    """Generate a one-line human-readable attribution for a trade."""
    pnl = trade.get("pnl", 0) or 0
    ep = trade.get("entry_price", 0) or 0
    slug = trade.get("slug", "") or ""
    exit_reason = trade.get("exit_reason", "") or ""
    outcome = "Won" if pnl > 0 else "Lost"

    # Parse asset and window from slug
    asset = "BTC"
    for a in ["eth", "sol", "xrp"]:
        if a in slug.lower():
            asset = a.upper()
            break
    window = "5m" if "-5m-" in slug else ("15m" if "-15m-" in slug else "?")

    # Direction from slug
    direction = "UP" if "-up" in slug.lower() else "DOWN"

    # Build explanation from signal features
    parts = [f"{outcome}"]

    if signal:
        mom = signal.get("momentum_pct")
        if mom:
            parts.append(f"{asset} moved {abs(mom):.2%} in 60s")

        mid = signal.get("midpoint")
        if mid:
            parts.append(f"entered {direction} at {mid:.2f}")

        time_rem = signal.get("time_remaining_secs")
        if time_rem:
            parts.append(f"{time_rem}s remaining")

        source = signal.get("source", "")
        if source:
            parts.append(f"via {source}")

        score = signal.get("signal_score")
        if score:
            parts.append(f"score={score:.0f}")
    else:
        parts.append(f"{asset} {window} {direction} at {ep:.2f}")

    parts.append(f"exit={exit_reason}")
    parts.append(f"P&L=${pnl:+.2f}")

    return " | ".join(parts)


def run_attribution(instance: str, mode: str, count: int = 10):
    data_dir = f"/opt/lagbot/instances/{instance}/data"
    perf_db = os.path.join(data_dir, "performance.db")
    sig_db = os.path.join(data_dir, "signals.db")
    journal_path = os.path.join(data_dir, "trade_journal.jsonl")

    conn = sqlite3.connect(perf_db)
    conn.row_factory = sqlite3.Row

    if mode == "last":
        trades = conn.execute(
            "SELECT * FROM trades WHERE exit_time IS NOT NULL ORDER BY entry_time DESC LIMIT ?",
            (count,),
        ).fetchall()
    elif mode == "today":
        cutoff = time.time() - 86400
        trades = conn.execute(
            "SELECT * FROM trades WHERE exit_time IS NOT NULL AND entry_time > ? ORDER BY entry_time",
            (cutoff,),
        ).fetchall()
    elif mode == "unattributed":
        # Check journal for already-attributed trade_ids
        attributed = set()
        if os.path.exists(journal_path):
            for line in open(journal_path):
                try:
                    entry = json.loads(line)
                    attributed.add(entry.get("trade_id"))
                except (json.JSONDecodeError, KeyError):
                    pass
        trades = conn.execute(
            "SELECT * FROM trades WHERE exit_time IS NOT NULL ORDER BY entry_time DESC LIMIT 50"
        ).fetchall()
        trades = [t for t in trades if t["trade_id"] not in attributed]
    else:
        trades = []

    conn.close()

    if not trades:
        print(f"No trades to attribute for {instance} ({mode}).")
        return

    # Try to match trades with signals
    sig_conn = sqlite3.connect(sig_db)
    sig_conn.row_factory = sqlite3.Row

    results = []
    for trade in trades:
        # Match signal by slug and approximate entry time
        entry_time = trade["entry_time"] or 0
        slug = trade["slug"] or ""
        signal = sig_conn.execute(
            """SELECT * FROM signals
               WHERE slug = ? AND ABS(epoch - ?) < 300
               AND outcome = 'executed'
               ORDER BY ABS(epoch - ?) LIMIT 1""",
            (slug, entry_time, entry_time),
        ).fetchone()

        attribution = attribute_trade(dict(trade), dict(signal) if signal else None)
        entry_dt = datetime.fromtimestamp(entry_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

        results.append({
            "trade_id": trade["trade_id"],
            "timestamp": entry_dt,
            "attribution": attribution,
        })

        print(f"[{entry_dt}] {attribution}")

    sig_conn.close()

    # Append to journal
    with open(journal_path, "a") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")

    print(f"\n{len(results)} attributions written to {journal_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Post-trade attribution")
    parser.add_argument("--instance", required=True)
    parser.add_argument("--last", type=int, default=0, help="Attribute last N trades")
    parser.add_argument("--today", action="store_true", help="Attribute today's trades")
    parser.add_argument("--unattributed", action="store_true", help="Attribute new trades only")
    args = parser.parse_args()

    if args.last > 0:
        run_attribution(args.instance, "last", args.last)
    elif args.today:
        run_attribution(args.instance, "today")
    elif args.unattributed:
        run_attribution(args.instance, "unattributed")
    else:
        run_attribution(args.instance, "last", 10)
