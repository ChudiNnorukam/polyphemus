#!/usr/bin/env python3
"""
IGOC Live Gate — checks whether the IGOC signal source has earned live status.

Gate criteria:
- n >= 50 guard_passed=1 signals where source contains 'igoc'
- WR >= 55% on those signals (resolved markets only)

Usage:
    python3 tools/igoc_live_gate.py
    python3 tools/igoc_live_gate.py --db /path/to/signals.db
"""

import argparse
import sqlite3
import sys
from pathlib import Path


MIN_N = 50
MIN_WR = 0.55


def run_gate(db_path: str) -> int:
    if not Path(db_path).exists():
        print(f"ERROR: signals DB not found: {db_path}")
        return 2

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Fetch guard_passed=1 IGOC signals with resolved outcomes
    rows = conn.execute("""
        SELECT outcome, pnl
        FROM signals
        WHERE guard_passed = 1
          AND (source LIKE '%igoc%' OR signal_type LIKE '%igoc%')
          AND outcome IS NOT NULL
        ORDER BY timestamp DESC
    """).fetchall()
    conn.close()

    n = len(rows)
    wins = sum(1 for r in rows if (r["pnl"] is not None and r["pnl"] > 0)
               or r["outcome"] in ("win", "1", "YES"))
    wr = wins / n if n > 0 else 0.0

    print(f"IGOC Live Gate")
    print(f"  Signals (guard_passed=1, resolved): {n} / {MIN_N} required")
    print(f"  Win Rate: {wr:.1%} / {MIN_WR:.0%} required")
    print()

    gate_n = n >= MIN_N
    gate_wr = wr >= MIN_WR

    if gate_n and gate_wr:
        print("GATE: PASS — IGOC ready for live. Set IGOC_SHADOW=false.")
        return 0
    else:
        reasons = []
        if not gate_n:
            reasons.append(f"need {MIN_N - n} more guard_passed signals")
        if not gate_wr:
            reasons.append(f"WR {wr:.1%} below {MIN_WR:.0%} threshold")
        print(f"GATE: NO-GO — {'; '.join(reasons)}")
        return 1


def main():
    parser = argparse.ArgumentParser(description="IGOC live gate check")
    parser.add_argument(
        "--db",
        default="/opt/lagbot/instances/emmanuel/data/signals.db",
        help="Path to signals.db",
    )
    args = parser.parse_args()
    sys.exit(run_gate(args.db))


if __name__ == "__main__":
    main()
