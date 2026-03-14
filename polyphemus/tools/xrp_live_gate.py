#!/usr/bin/env python3
"""
XRP Live Gate — checks whether XRP shadow performance justifies reinstatement.

Gate criteria:
- n >= 20 XRP shadow trades (resolved markets only)
- WR >= 50%
- No single loss > $50

Usage:
    python3 tools/xrp_live_gate.py
    python3 tools/xrp_live_gate.py --db /path/to/signals.db
"""

import argparse
import sqlite3
import sys
from pathlib import Path


MIN_N = 20
MIN_WR = 0.50
MAX_SINGLE_LOSS = -50.0


def run_gate(db_path: str) -> int:
    if not Path(db_path).exists():
        print(f"ERROR: signals DB not found: {db_path}")
        return 2

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT pnl, outcome
        FROM signals
        WHERE asset = 'XRP'
          AND shadow = 1
          AND outcome IS NOT NULL
        ORDER BY timestamp DESC
    """).fetchall()
    conn.close()

    n = len(rows)
    wins = sum(1 for r in rows if (r["pnl"] is not None and r["pnl"] > 0)
               or r["outcome"] in ("win", "1", "YES"))
    wr = wins / n if n > 0 else 0.0
    losses = [r["pnl"] for r in rows if r["pnl"] is not None and r["pnl"] < 0]
    worst_loss = min(losses) if losses else 0.0

    print(f"XRP Live Gate")
    print(f"  Shadow trades (resolved): {n} / {MIN_N} required")
    print(f"  Win Rate: {wr:.1%} / {MIN_WR:.0%} required")
    print(f"  Worst single loss: ${worst_loss:.2f} (limit ${MAX_SINGLE_LOSS:.0f})")
    print()

    gate_n = n >= MIN_N
    gate_wr = wr >= MIN_WR
    gate_loss = worst_loss >= MAX_SINGLE_LOSS

    if gate_n and gate_wr and gate_loss:
        print("GATE: PASS — XRP ready for reinstatement. Add XRP to ASSET_FILTER.")
        return 0
    else:
        reasons = []
        if not gate_n:
            reasons.append(f"need {MIN_N - n} more shadow trades")
        if not gate_wr:
            reasons.append(f"WR {wr:.1%} below {MIN_WR:.0%} threshold")
        if not gate_loss:
            reasons.append(f"single loss ${worst_loss:.2f} exceeds ${MAX_SINGLE_LOSS:.0f} limit")
        print(f"GATE: NO-GO — {'; '.join(reasons)}")
        return 1


def main():
    parser = argparse.ArgumentParser(description="XRP reinstatement gate check")
    parser.add_argument(
        "--db",
        default="/opt/lagbot/instances/emmanuel/data/signals.db",
        help="Path to signals.db",
    )
    args = parser.parse_args()
    sys.exit(run_gate(args.db))


if __name__ == "__main__":
    main()
