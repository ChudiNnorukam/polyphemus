#!/usr/bin/env python3
"""Check Accepted — Review + update pending LinkedIn connection status.

Prints all pending connections so you can update their status during
or after a LinkedIn session. Can also mark accepted/rejected interactively.

Usage:
    python3 check_accepted.py           # List all pending connections
    python3 check_accepted.py mark      # Interactive: mark accepted/rejected by ID
    python3 check_accepted.py accept ID # Mark single lead as accepted
    python3 check_accepted.py reject ID # Mark single lead as rejected (keep as pending)
    python3 check_accepted.py stale     # Show requests older than 14 days with no response
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

DB_PATH = os.environ.get(
    'LEADS_DB_PATH',
    os.path.join(os.path.dirname(__file__), '..', 'data', 'marketing_leads.db')
)


def _load_env():
    for path in [
        os.path.join(os.path.dirname(__file__), '..', '.env'),
        '/opt/openclaw/.env',
    ]:
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        k, _, v = line.partition('=')
                        os.environ.setdefault(k.strip(), v.strip())

_load_env()


def get_db():
    if not os.path.exists(DB_PATH):
        print(f"DB not found: {DB_PATH}. Run init_db.py first.")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def mark_accepted(conn, lead_id: int):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        UPDATE leads
        SET connection_accepted_at=?, connection_checked_at=?, status='connected'
        WHERE id=? AND status='pending'
    """, (now, now, lead_id))
    conn.commit()
    row = conn.execute("SELECT name, company FROM leads WHERE id=?", (lead_id,)).fetchone()
    if row:
        print(f"  Accepted: {row['name']} @ {row['company']}")
    else:
        print(f"  Lead {lead_id} not found or not in pending status.")


def mark_checked(conn, lead_id: int):
    """Mark as checked but not yet accepted — update checked_at."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE leads SET connection_checked_at=? WHERE id=?", (now, lead_id)
    )
    conn.commit()


def cmd_list(args):
    conn = get_db()
    rows = conn.execute("""
        SELECT id, name, company, title, connection_sent_at,
               connection_checked_at
        FROM leads
        WHERE status='pending'
        ORDER BY connection_sent_at ASC
    """).fetchall()
    conn.close()

    if not rows:
        print("\nNo pending connections.")
        return

    print(f"\n{len(rows)} pending connection(s):\n")
    print(f"  {'ID':<5} {'Name':<22} {'Company':<22} {'Sent':<12} {'Last Checked'}")
    print("  " + "-"*75)
    for r in rows:
        sent = r['connection_sent_at'][:10] if r['connection_sent_at'] else '—'
        checked = r['connection_checked_at'][:10] if r['connection_checked_at'] else 'never'
        print(f"  {r['id']:<5} {(r['name'] or '?'):<22} {(r['company'] or '?'):<22} {sent:<12} {checked}")
    print()
    print("To accept: python3 check_accepted.py accept <ID>")
    print("To batch mark: python3 check_accepted.py mark")
    print()


def cmd_mark(args):
    conn = get_db()
    rows = conn.execute("""
        SELECT id, name, company FROM leads WHERE status='pending'
        ORDER BY connection_sent_at ASC
    """).fetchall()

    if not rows:
        print("No pending connections.")
        conn.close()
        return

    print(f"\nMark pending connections (y=accepted, n=still pending, q=quit):\n")
    for r in rows:
        answer = input(f"  [{r['id']}] {r['name']} @ {r['company']} accepted? (y/n/q): ").strip().lower()
        if answer == 'q':
            break
        elif answer == 'y':
            mark_accepted(conn, r['id'])
        else:
            mark_checked(conn, r['id'])

    conn.close()
    print("\nDone.")


def cmd_accept(args):
    conn = get_db()
    mark_accepted(conn, args.id)
    conn.close()


def cmd_stale(args):
    conn = get_db()
    rows = conn.execute("""
        SELECT id, name, company, connection_sent_at
        FROM leads
        WHERE status='pending'
          AND connection_sent_at < date('now', '-14 days')
        ORDER BY connection_sent_at ASC
    """).fetchall()
    conn.close()

    if not rows:
        print("\nNo stale pending connections (all < 14 days old).")
        return

    print(f"\n{len(rows)} stale connection(s) (sent >14 days ago, no response):\n")
    for r in rows:
        sent = r['connection_sent_at'][:10] if r['connection_sent_at'] else '—'
        print(f"  [{r['id']}] {r['name']} @ {r['company']} — sent {sent}")
    print("\nConsider withdrawing and re-attempting in 3 months.")
    print()


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('mark')
    sub.add_parser('stale')
    accept_p = sub.add_parser('accept')
    accept_p.add_argument('id', type=int)
    reject_p = sub.add_parser('reject')
    reject_p.add_argument('id', type=int)

    args = parser.parse_args()

    if args.command == 'mark':
        cmd_mark(args)
    elif args.command == 'accept':
        cmd_accept(args)
    elif args.command == 'stale':
        cmd_stale(args)
    else:
        cmd_list(args)

if __name__ == '__main__':
    main()
