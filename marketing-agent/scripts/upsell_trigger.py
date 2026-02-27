#!/usr/bin/env python3
"""Upsell Trigger — Find engaged post-purchase buyers and enroll in upsell sequence.

Run daily at 10am. Targets buyers who opened 2+ emails in post-purchase-v1.

Usage:
    python3 scripts/upsell_trigger.py [--dry-run]
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

import requests

DB_PATH = os.environ.get(
    'LEADS_DB_PATH',
    os.path.join(os.path.dirname(__file__), '..', 'data', 'marketing_leads.db')
)
DRY_RUN = os.environ.get('DRY_RUN', 'false').lower() == 'true'


def _load_env():
    for path in [
        os.path.join(os.path.dirname(__file__), '..', '.env'),
        '/opt/openclaw/.env',
        '/opt/lagbot/lagbot/.env',
    ]:
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        k, _, v = line.partition('=')
                        os.environ.setdefault(k.strip(), v.strip())


_load_env()
DRY_RUN = os.environ.get('DRY_RUN', 'false').lower() == 'true'
SLACK_BOT_TOKEN = os.environ.get('SLACK_BOT_TOKEN', '')
SLACK_CHANNEL_ID = os.environ.get('SLACK_CHANNEL_ID', '')


def get_db():
    if not os.path.exists(DB_PATH):
        print(f"DB not found: {DB_PATH}. Run init_db.py + funnel_db_init.py extend first.")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def send_slack(msg: str):
    if not SLACK_BOT_TOKEN or not SLACK_CHANNEL_ID:
        print(f"[SLACK] {msg}")
        return
    if DRY_RUN:
        print(f"[SLACK DRY RUN] {msg}")
        return
    try:
        requests.post(
            'https://slack.com/api/chat.postMessage',
            json={'channel': SLACK_CHANNEL_ID, 'text': msg},
            headers={'Authorization': f'Bearer {SLACK_BOT_TOKEN}'},
            timeout=10
        )
    except Exception as e:
        print(f"Slack alert failed: {e}")


def find_upsell_candidates(conn) -> list:
    """Find buyers eligible for upsell: step >= 4, 2+ engaged sends, not already in upsell."""
    return conn.execute("""
        SELECT
            se.id as enrollment_id,
            se.contact_id,
            fc.email,
            fc.product_id,
            COUNT(ss.id) as engaged_sends
        FROM sequence_enrollments se
        JOIN funnel_contacts fc ON se.contact_id = fc.id
        JOIN sequence_sends ss ON ss.enrollment_id = se.id
        WHERE se.sequence_id = 'post-purchase-v1'
          AND se.current_step >= 4
          AND se.status = 'active'
          AND ss.status IN ('opened', 'clicked')
          AND NOT EXISTS (
              SELECT 1 FROM sequence_enrollments upsell
              WHERE upsell.contact_id = se.contact_id
                AND upsell.sequence_id = 'upsell-v1'
          )
        GROUP BY se.id, se.contact_id, fc.email, fc.product_id
        HAVING COUNT(ss.id) >= 2
    """).fetchall()


def enroll_upsell(conn, contact_id: int, email: str) -> bool:
    """Enroll contact in upsell-v1. Returns True if enrolled."""
    now = datetime.now(timezone.utc).isoformat()

    cursor = conn.execute("""
        INSERT INTO sequence_enrollments
            (contact_id, sequence_id, current_step, status, enrolled_at)
        VALUES (?, 'upsell-v1', 0, 'active', ?)
    """, (contact_id, now))

    enrollment_id = cursor.lastrowid

    # Schedule step 1 immediately
    conn.execute("""
        INSERT INTO sequence_sends
            (enrollment_id, step, scheduled_for, status)
        VALUES (?, 1, datetime('now'), 'pending')
    """, (enrollment_id,))

    conn.commit()
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    if args.dry_run:
        os.environ['DRY_RUN'] = 'true'
        global DRY_RUN
        DRY_RUN = True

    conn = get_db()
    candidates = find_upsell_candidates(conn)

    if not candidates:
        print("No upsell candidates found.")
        conn.close()
        return

    print(f"Found {len(candidates)} upsell candidate(s):")
    for c in candidates:
        print(f"  {c['email']} ({c['product_id']}) — {c['engaged_sends']} engaged sends")

    if DRY_RUN:
        print(f"\n[DRY RUN] Would enroll {len(candidates)} buyer(s) in upsell-v1")
        send_slack(f":zap: [DRY RUN] Upsell would trigger for {len(candidates)} buyers (opened 2+ emails)")
        conn.close()
        return

    enrolled_count = 0
    for candidate in candidates:
        try:
            enrolled = enroll_upsell(conn, candidate['contact_id'], candidate['email'])
            if enrolled:
                enrolled_count += 1
                print(f"  Enrolled: {candidate['email']}")
        except Exception as e:
            print(f"  Error enrolling {candidate['email']}: {e}")

    conn.close()

    if enrolled_count:
        send_slack(f":zap: Upsell triggered for {enrolled_count} buyer(s) (opened 2+ emails)")

    print(f"\nDone. Enrolled {enrolled_count} buyer(s) in upsell-v1.")


if __name__ == '__main__':
    main()
