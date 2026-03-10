#!/usr/bin/env python3
"""Marketing Agent Resolve — Print outreach funnel stats from leads.db.

Usage:
    python3 marketing_resolve.py            # Full stats
    python3 marketing_resolve.py --csv      # Export leads to CSV
    python3 marketing_resolve.py --recent   # Only leads touched in last 30 days
"""

import argparse
import csv
import os
import sqlite3
import sys
from datetime import datetime, timezone

DB_PATH = os.environ.get(
    'LEADS_DB_PATH',
    os.path.join(os.path.dirname(__file__), '..', 'data', 'marketing_leads.db')
)

def get_db():
    if not os.path.exists(DB_PATH):
        print(f"DB not found: {DB_PATH}")
        print("Run: python3 init_db.py first")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def pct(num, denom):
    if denom == 0:
        return "n/a"
    return f"{num/denom*100:.1f}%"

def cmd_stats(args):
    conn = get_db()
    c = conn.cursor()

    total = c.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    viewed = c.execute("SELECT COUNT(*) FROM leads WHERE profile_viewed_at IS NOT NULL").fetchone()[0]
    conn_sent = c.execute("SELECT COUNT(*) FROM leads WHERE connection_sent_at IS NOT NULL").fetchone()[0]
    conn_pending = c.execute("SELECT COUNT(*) FROM leads WHERE status='pending'").fetchone()[0]
    connected = c.execute("SELECT COUNT(*) FROM leads WHERE connection_accepted_at IS NOT NULL").fetchone()[0]
    messaged = c.execute("SELECT COUNT(*) FROM leads WHERE message_sent_at IS NOT NULL").fetchone()[0]
    msg_replied = c.execute("SELECT COUNT(*) FROM leads WHERE message_replied_at IS NOT NULL").fetchone()[0]

    enriched = c.execute("SELECT COUNT(*) FROM leads WHERE email IS NOT NULL").fetchone()[0]
    verified = c.execute("SELECT COUNT(*) FROM leads WHERE email_verified=1").fetchone()[0]
    seq_started = c.execute("SELECT COUNT(*) FROM leads WHERE email_seq_started_at IS NOT NULL").fetchone()[0]
    opened = c.execute("SELECT COUNT(*) FROM leads WHERE email_opened=1").fetchone()[0]
    email_replied = c.execute("SELECT COUNT(*) FROM leads WHERE email_replied_at IS NOT NULL").fetchone()[0]
    converted = c.execute("SELECT COUNT(*) FROM leads WHERE converted_at IS NOT NULL").fetchone()[0]
    churned = c.execute("SELECT COUNT(*) FROM leads WHERE status='churned'").fetchone()[0]

    apollo_used = c.execute(
        "SELECT COUNT(*) FROM leads WHERE email_found_at > date('now', '-30 days')"
    ).fetchone()[0]

    today_conns = c.execute(
        "SELECT COUNT(*) FROM leads WHERE date(connection_sent_at)=date('now')"
    ).fetchone()[0]
    today_emails = c.execute(
        "SELECT COUNT(*) FROM email_events WHERE date(occurred_at)=date('now') AND event_type='sent'"
    ).fetchone()[0]

    print()
    print("MARKETING AGENT — RESOLVE")
    print("━" * 42)
    print(f"  Prospects loaded:           {total}")
    print(f"  Profile views:              {viewed} ({pct(viewed, total)})")
    print()
    print("  LinkedIn channel:")
    print(f"    Connections sent:         {conn_sent}")
    print(f"    Connections accepted:     {connected} ({pct(connected, conn_sent)} of sent)")
    print(f"    Still pending:            {conn_pending}")
    print(f"    DM sent:                  {messaged}")
    print(f"    DM replied:               {msg_replied} ({pct(msg_replied, messaged)})")
    print()
    print("  Email channel:")
    print(f"    Emails enriched:          {enriched}")
    print(f"    Emails verified:          {verified} ({pct(verified, enriched)})")
    print(f"    Sequences started:        {seq_started}")
    print(f"    Opened:                   {opened} ({pct(opened, seq_started)})")
    print(f"    Replied:                  {email_replied} ({pct(email_replied, seq_started)})")
    print(f"    Churned:                  {churned}")
    print()
    print(f"  Converted:                  {converted}")
    print()
    print("  Today:")
    print(f"    Connections sent:         {today_conns}/20 cap")
    print(f"    Emails sent:              {today_emails}/300 cap")
    print(f"    Apollo credits used (30d):{apollo_used}/50 monthly")
    print("━" * 42)

    # Recent activity
    recent = c.execute("""
        SELECT name, company, title, status,
               COALESCE(connection_accepted_at, connection_sent_at, profile_viewed_at) as last_touch
        FROM leads
        WHERE last_touch IS NOT NULL
        ORDER BY last_touch DESC
        LIMIT 5
    """).fetchall()
    if recent:
        print()
        print("  Recent activity:")
        for r in recent:
            print(f"    {r['name']:<20} {r['company']:<20} [{r['status']}]")
    print()
    conn.close()

def cmd_csv(args):
    conn = get_db()
    rows = conn.execute("SELECT * FROM leads ORDER BY created_at DESC").fetchall()
    writer = csv.DictWriter(sys.stdout, fieldnames=rows[0].keys() if rows else [])
    writer.writeheader()
    writer.writerows([dict(r) for r in rows])
    conn.close()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--csv', action='store_true')
    parser.add_argument('--recent', action='store_true')
    args = parser.parse_args()

    if args.csv:
        cmd_csv(args)
    else:
        cmd_stats(args)

if __name__ == '__main__':
    main()
