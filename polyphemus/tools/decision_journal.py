"""Decision Journal - Closed-loop learning for trading bot decisions.

Logs every significant decision with expected outcome. On next session startup,
checks actuals against expectations and extracts learnings.

Born from Mar 21 2026: 10 decisions in one session, none systematically verified.

Usage:
    # Log a decision
    python3 tools/decision_journal.py log \
        --decision "Disable MID_PRICE_STOP_ENABLED" \
        --expected "WR improves from 48% to 75%+, P&L turns positive" \
        --checkpoint-query "SELECT exit_reason, COUNT(*), AVG(pnl) FROM trades WHERE entry_time > 1774090000 GROUP BY exit_reason" \
        --checkpoint-n 100

    # Check pending decisions (run at session start)
    python3 tools/decision_journal.py check --db /path/to/performance.db

    # List all decisions
    python3 tools/decision_journal.py list
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "decision_journal.db"


def get_db(path=None):
    db_path = path or DB_PATH
    os.makedirs(db_path.parent, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision TEXT NOT NULL,
            expected TEXT NOT NULL,
            checkpoint_query TEXT,
            checkpoint_n INTEGER DEFAULT 50,
            status TEXT DEFAULT 'pending',
            actual TEXT,
            learning TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            verified_at TEXT,
            domain TEXT DEFAULT 'trading',
            session_date TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS failure_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern TEXT NOT NULL,
            symptom TEXT NOT NULL,
            root_cause TEXT,
            prevention TEXT NOT NULL,
            domain TEXT DEFAULT 'python',
            cost TEXT,
            discovered_at TEXT DEFAULT (datetime('now')),
            source_file TEXT,
            source_line INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS heuristics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            specific TEXT NOT NULL,
            general TEXT NOT NULL,
            applies_to TEXT,
            discovered_from TEXT,
            discovered_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn


def log_decision(args):
    conn = get_db()
    conn.execute(
        "INSERT INTO decisions (decision, expected, checkpoint_query, checkpoint_n, session_date, domain) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (args.decision, args.expected, args.checkpoint_query,
         args.checkpoint_n, datetime.now(timezone.utc).strftime("%Y-%m-%d"),
         args.domain or "trading"),
    )
    conn.commit()
    print(f"Logged: {args.decision}")
    conn.close()


def check_decisions(args):
    conn = get_db()
    perf_db = args.db
    pending = conn.execute(
        "SELECT * FROM decisions WHERE status = 'pending' ORDER BY created_at"
    ).fetchall()

    if not pending:
        print("No pending decisions to verify.")
        return

    perf_conn = None
    if perf_db and os.path.exists(perf_db):
        perf_conn = sqlite3.connect(perf_db)
        perf_conn.row_factory = sqlite3.Row

    for d in pending:
        print(f"\n--- Decision #{d['id']}: {d['decision']} ---")
        print(f"  Expected: {d['expected']}")
        print(f"  Logged: {d['created_at']}")

        if d['checkpoint_query'] and perf_conn:
            try:
                # Check if we have enough data
                results = perf_conn.execute(d['checkpoint_query']).fetchall()
                total_rows = sum(r[1] if len(r) > 1 else 1 for r in results)
                if total_rows < (d['checkpoint_n'] or 50):
                    print(f"  Status: WAITING (n={total_rows} < {d['checkpoint_n']})")
                    continue
                print(f"  Data (n={total_rows}):")
                for r in results:
                    print(f"    {dict(r)}")
                print(f"  Status: READY FOR REVIEW (n={total_rows} >= {d['checkpoint_n']})")
            except Exception as e:
                print(f"  Query error: {e}")
        else:
            print("  No checkpoint query or performance DB not found.")

    if perf_conn:
        perf_conn.close()
    conn.close()


def verify_decision(args):
    conn = get_db()
    conn.execute(
        "UPDATE decisions SET status = ?, actual = ?, learning = ?, verified_at = datetime('now') "
        "WHERE id = ?",
        (args.status, args.actual, args.learning, args.id),
    )
    conn.commit()
    print(f"Decision #{args.id} verified as {args.status}")
    conn.close()


def log_failure(args):
    conn = get_db()
    conn.execute(
        "INSERT INTO failure_patterns (pattern, symptom, root_cause, prevention, domain, cost, source_file, source_line) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (args.pattern, args.symptom, args.root_cause, args.prevention,
         args.domain or "python", args.cost, args.source_file, args.source_line),
    )
    conn.commit()
    print(f"Failure pattern logged: {args.pattern}")
    conn.close()


def check_failures(args):
    """Query failure patterns before making a change."""
    conn = get_db()
    query = args.query or ""
    patterns = conn.execute(
        "SELECT * FROM failure_patterns WHERE "
        "pattern LIKE ? OR symptom LIKE ? OR prevention LIKE ? "
        "ORDER BY discovered_at DESC",
        (f"%{query}%", f"%{query}%", f"%{query}%"),
    ).fetchall()
    if not patterns:
        print(f"No known failure patterns matching '{query}'")
    else:
        print(f"Found {len(patterns)} matching failure patterns:")
        for p in patterns:
            print(f"\n  Pattern: {p['pattern']}")
            print(f"  Symptom: {p['symptom']}")
            print(f"  Prevention: {p['prevention']}")
            print(f"  Cost: {p['cost'] or 'unknown'}")
    conn.close()


def log_heuristic(args):
    conn = get_db()
    conn.execute(
        "INSERT INTO heuristics (specific, general, applies_to, discovered_from) "
        "VALUES (?, ?, ?, ?)",
        (args.specific, args.general, args.applies_to, args.discovered_from),
    )
    conn.commit()
    print(f"Heuristic logged: {args.general}")
    conn.close()


def list_all(args):
    conn = get_db()
    table = args.table or "decisions"
    rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 20").fetchall()
    for r in rows:
        print(dict(r))
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Decision Journal + Failure Patterns + Heuristics")
    sub = parser.add_subparsers(dest="command")

    # Log decision
    p = sub.add_parser("log", help="Log a new decision")
    p.add_argument("--decision", required=True)
    p.add_argument("--expected", required=True)
    p.add_argument("--checkpoint-query")
    p.add_argument("--checkpoint-n", type=int, default=50)
    p.add_argument("--domain", default="trading")

    # Check pending
    p = sub.add_parser("check", help="Check pending decisions against data")
    p.add_argument("--db", help="Path to performance.db")

    # Verify
    p = sub.add_parser("verify", help="Mark decision as verified")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--status", choices=["confirmed", "wrong", "partial"], required=True)
    p.add_argument("--actual", required=True)
    p.add_argument("--learning")

    # Log failure pattern
    p = sub.add_parser("failure", help="Log a failure pattern")
    p.add_argument("--pattern", required=True)
    p.add_argument("--symptom", required=True)
    p.add_argument("--root-cause")
    p.add_argument("--prevention", required=True)
    p.add_argument("--domain", default="python")
    p.add_argument("--cost")
    p.add_argument("--source-file")
    p.add_argument("--source-line", type=int)

    # Check failures
    p = sub.add_parser("check-failures", help="Query failure patterns")
    p.add_argument("--query", help="Search term")

    # Log heuristic
    p = sub.add_parser("heuristic", help="Log a transferable heuristic")
    p.add_argument("--specific", required=True)
    p.add_argument("--general", required=True)
    p.add_argument("--applies-to")
    p.add_argument("--discovered-from")

    # List
    p = sub.add_parser("list", help="List entries")
    p.add_argument("--table", choices=["decisions", "failure_patterns", "heuristics"], default="decisions")

    args = parser.parse_args()
    if args.command == "log":
        log_decision(args)
    elif args.command == "check":
        check_decisions(args)
    elif args.command == "verify":
        verify_decision(args)
    elif args.command == "failure":
        log_failure(args)
    elif args.command == "check-failures":
        check_failures(args)
    elif args.command == "heuristic":
        log_heuristic(args)
    elif args.command == "list":
        list_all(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
