"""Phase 3 CLI — replay a single trade end-to-end in <60s.

Usage:
    python -m polyphemus.tools.debug_trade <trade_id>
    python -m polyphemus.tools.debug_trade <trade_id> --db path/to/performance.db

Prints:
  1. The ``trades`` row (all columns, pretty-printed).
  2. The ``signals`` row joined via ``signal_id`` (if present).
  3. The full ``trade_events`` timeline, oldest first.
  4. A one-line summary: fill_model, signal_source, entry→exit PnL,
     adverse-selection bps, hold duration.

Written to be readable at the terminal — no JSON output, no fancy
deps. If you want machine output, query the DB directly.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from typing import Optional

from polyphemus.trade_tracer import TradeTracer


DEFAULT_DB = os.environ.get(
    "POLYPHEMUS_PERF_DB",
    os.path.join(
        os.environ.get("LAGBOT_DATA_DIR", "polyphemus/data"),
        "performance.db",
    ),
)


def _fmt_ts(ts: Optional[float]) -> str:
    if ts is None:
        return "-"
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except Exception:
        return str(ts)


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def _print_kv(title: str, data: dict) -> None:
    print(f"\n=== {title} ===")
    if not data:
        print("(empty)")
        return
    width = max(len(k) for k in data.keys())
    for k in sorted(data.keys()):
        v = data[k]
        if isinstance(v, float) and k.endswith(("_time",)):
            v = f"{v}  ({_fmt_ts(v)})"
        print(f"  {k.ljust(width)}  {v}")


def _fetch_trade(db_path: str, trade_id: str) -> Optional[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM trades WHERE trade_id = ?", (trade_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def _fetch_signal(signals_db_path: str, signal_id: Optional[int]) -> Optional[dict]:
    if signal_id is None or signal_id < 0:
        return None
    if not os.path.exists(signals_db_path):
        return None
    conn = sqlite3.connect(signals_db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM signals WHERE id = ?", (signal_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def _print_timeline(tracer: TradeTracer, trade_id: str) -> int:
    events = tracer.timeline(trade_id)
    print(f"\n=== Timeline ({len(events)} events) ===")
    if not events:
        print("(no trade_events rows — tracer likely disabled when this trade fired)")
        return 0
    for e in events:
        payload = json.dumps(e.payload, sort_keys=True) if e.payload else ""
        print(f"  {_fmt_ts(e.ts)}  {e.event_type.ljust(24)}  {payload}")
    return len(events)


def _summary(trade: dict) -> None:
    entry = trade.get("entry_price")
    exit_ = trade.get("exit_price")
    pnl = trade.get("pnl")
    hold = None
    if trade.get("entry_time") and trade.get("exit_time"):
        hold = int(trade["exit_time"] - trade["entry_time"])
    print("\n=== Summary ===")
    print(f"  fill_model      {trade.get('fill_model')}")
    print(f"  fill_reason     {trade.get('fill_model_reason')}")
    print(f"  signal_source   {trade.get('signal_source')}")
    print(f"  entry_mode      {trade.get('entry_mode')}")
    print(f"  is_dry_run      {trade.get('is_dry_run')}")
    print(f"  entry -> exit   {entry} -> {exit_}")
    print(f"  pnl             {pnl}")
    print(f"  hold_seconds    {hold}")
    print(f"  adverse_fill    {trade.get('adverse_fill')} (bps: {trade.get('adverse_fill_bps')})")
    print(f"  fill_latency_ms {trade.get('fill_latency_ms')}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Replay a single trade end-to-end.")
    parser.add_argument("trade_id", help="trade_id from trades table")
    parser.add_argument(
        "--db",
        default=DEFAULT_DB,
        help=f"Path to performance.db (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--signals-db",
        default=None,
        help="Path to signals.db (default: alongside --db)",
    )
    args = parser.parse_args(argv)

    if not os.path.exists(args.db):
        print(f"error: db not found at {args.db}", file=sys.stderr)
        return 2

    trade = _fetch_trade(args.db, args.trade_id)
    if not trade:
        print(f"error: trade_id {args.trade_id!r} not found in {args.db}", file=sys.stderr)
        return 1

    _summary(trade)
    _print_kv("trades row", trade)

    signals_db = args.signals_db or os.path.join(os.path.dirname(args.db), "signals.db")
    signal = _fetch_signal(signals_db, trade.get("signal_id"))
    if signal:
        _print_kv(f"signals row (id={trade.get('signal_id')})", signal)
    else:
        print(f"\n=== signals row ===\n  (no matching row; signal_id={trade.get('signal_id')})")

    tracer = TradeTracer(db_path=args.db)
    _print_timeline(tracer, args.trade_id)

    return 0


if __name__ == "__main__":
    sys.exit(main())
