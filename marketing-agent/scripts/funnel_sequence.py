#!/usr/bin/env python3
"""Funnel Sequence — Fire due post-purchase and upsell emails.

Run every 15 minutes via cron. Idempotent — only fires pending sends.

Usage:
    python3 scripts/funnel_sequence.py [--dry-run]
    python3 scripts/funnel_sequence.py status   # Show enrollment states
"""

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

import requests
from anthropic import Anthropic

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
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
BREVO_API_KEY = os.environ.get('BREVO_API_KEY', '')
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', '')
SENDER_NAME = os.environ.get('SENDER_NAME', 'Chudi')
SLACK_BOT_TOKEN = os.environ.get('SLACK_BOT_TOKEN', '')
SLACK_CHANNEL_ID = os.environ.get('SLACK_CHANNEL_ID', '')
BREVO_SEND_URL = 'https://api.brevo.com/v3/smtp/email'


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


def personalize_email(client: Anthropic, contact: dict, seq_def: dict) -> tuple[str, str]:
    """Return (subject, body) personalized for this contact."""
    first_name = (contact['name'] or 'there').split()[0]
    prompt = f"""Write a post-purchase email for a digital product customer.

Customer: {first_name}
Product ID: {contact['product_id'] or 'unknown'}
Email step: {seq_def['step']} of their sequence
Subject template: {seq_def['subject']}
Template name: {seq_def['template_name']}

Guidelines:
- Warm, direct, no fluff
- Under 120 words
- No em dashes, no "leverage", no "robust"
- First line addresses {first_name} by name
- Clear single CTA if applicable
- Return ONLY: first line = subject, remaining lines = body. No labels, no JSON."""

    response = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=300,
        messages=[{'role': 'user', 'content': prompt}]
    )
    text = response.content[0].text.strip()
    lines = text.split('\n', 1)
    subject = lines[0].strip()
    body = lines[1].strip() if len(lines) > 1 else seq_def['subject']
    return subject, body


def send_brevo_email(to_email: str, to_name: str, subject: str, body: str) -> str | None:
    """Send via Brevo. Returns message_id or None."""
    if DRY_RUN:
        print(f"    [DRY RUN] Would send to {to_email}: {subject[:60]}")
        return 'dry-run-id'

    headers = {
        'api-key': BREVO_API_KEY,
        'Content-Type': 'application/json',
    }
    payload = {
        'sender': {'email': SENDER_EMAIL, 'name': SENDER_NAME},
        'to': [{'email': to_email, 'name': to_name or ''}],
        'subject': subject,
        'textContent': body,
        'htmlContent': body.replace('\n', '<br>'),
    }
    try:
        r = requests.post(BREVO_SEND_URL, json=payload, headers=headers, timeout=15)
        r.raise_for_status()
        return r.json().get('messageId')
    except Exception as e:
        print(f"    Brevo send failed: {e}")
        return None


def cmd_send(args):
    if not BREVO_API_KEY and not DRY_RUN:
        print("BREVO_API_KEY not set.")
        sys.exit(1)

    client = Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()

    # Find all due sends
    due_sends = conn.execute("""
        SELECT
            ss.id as send_id,
            ss.enrollment_id,
            ss.step,
            se.sequence_id,
            se.contact_id,
            fc.email,
            fc.name,
            fc.product_id
        FROM sequence_sends ss
        JOIN sequence_enrollments se ON ss.enrollment_id = se.id
        JOIN funnel_contacts fc ON se.contact_id = fc.id
        WHERE ss.status = 'pending'
          AND ss.scheduled_for <= datetime('now')
          AND se.status = 'active'
        ORDER BY ss.scheduled_for ASC
    """).fetchall()

    if not due_sends:
        print("No funnel emails due.")
        conn.close()
        return

    print(f"{len(due_sends)} funnel email(s) due.")
    sent_count = 0
    first_step_notified = set()

    for send in due_sends:
        seq_def = conn.execute("""
            SELECT * FROM sequence_definitions
            WHERE sequence_id = ? AND step = ?
        """, (send['sequence_id'], send['step'])).fetchone()

        if not seq_def:
            print(f"  No definition for {send['sequence_id']} step {send['step']} — skipping")
            conn.execute("UPDATE sequence_sends SET status='skipped' WHERE id=?", (send['send_id'],))
            conn.commit()
            continue

        print(f"  Sending {send['sequence_id']} step {send['step']} to {send['email']}")

        if client:
            try:
                subject, body = personalize_email(client, dict(send), dict(seq_def))
            except Exception as e:
                print(f"    Personalization failed: {e}. Using template subject.")
                subject = seq_def['subject']
                body = f"Hi {send['name'] or 'there'},\n\n{seq_def['subject']}\n\nBest,\n{SENDER_NAME}"
        else:
            subject = seq_def['subject']
            body = f"Hi {send['name'] or 'there'},\n\n{seq_def['subject']}\n\nBest,\n{SENDER_NAME}"

        msg_id = send_brevo_email(send['email'], send['name'] or '', subject, body)

        if msg_id:
            # Mark send complete
            conn.execute("""
                UPDATE sequence_sends
                SET status='sent', sent_at=?, brevo_message_id=?
                WHERE id=?
            """, (now, msg_id, send['send_id']))

            # Advance enrollment step
            conn.execute("""
                UPDATE sequence_enrollments SET current_step=? WHERE id=?
            """, (send['step'], send['enrollment_id']))

            # Check for next step
            next_def = conn.execute("""
                SELECT * FROM sequence_definitions
                WHERE sequence_id = ? AND step = ?
            """, (send['sequence_id'], send['step'] + 1)).fetchone()

            if next_def:
                delay_hours = next_def['delay_hours']
                conn.execute("""
                    INSERT INTO sequence_sends
                        (enrollment_id, step, scheduled_for, status)
                    VALUES (?, ?, datetime('now', ? || ' hours'), 'pending')
                """, (send['enrollment_id'], send['step'] + 1, str(delay_hours)))
            else:
                # Sequence complete
                conn.execute("""
                    UPDATE sequence_enrollments
                    SET status='completed', exited_at=?, exit_reason='completed'
                    WHERE id=?
                """, (now, send['enrollment_id']))
                print(f"    Sequence {send['sequence_id']} completed for {send['email']}")

            conn.commit()
            sent_count += 1

            # Slack alert on step 1 (new funnel started)
            if send['step'] == 1 and send['email'] not in first_step_notified:
                send_slack(f":envelope: Funnel started: {send['email']} for {send['sequence_id']}")
                first_step_notified.add(send['email'])
        else:
            print(f"    Send failed. Will retry next run.")

        time.sleep(1)

    conn.close()
    mode = ' [DRY RUN]' if DRY_RUN else ''
    print(f"\nDone{mode}. Sent {sent_count} email(s).")


def cmd_status(args):
    conn = get_db()
    rows = conn.execute("""
        SELECT
            fc.email,
            fc.product_id,
            se.sequence_id,
            se.current_step,
            se.status,
            se.enrolled_at
        FROM sequence_enrollments se
        JOIN funnel_contacts fc ON se.contact_id = fc.id
        ORDER BY se.enrolled_at DESC
        LIMIT 30
    """).fetchall()
    conn.close()

    if not rows:
        print("No enrollments yet.")
        return

    print(f"\n{'Email':<28} {'Product':<15} {'Sequence':<20} {'Step':<6} {'Status'}")
    print("-" * 85)
    for r in rows:
        print(f"  {(r['email'] or '?'):<26} {(r['product_id'] or '?'):<15} "
              f"{r['sequence_id']:<20} {r['current_step']:<6} {r['status']}")
    print()


COMMANDS = {
    'send': cmd_send,
    'status': cmd_status,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', nargs='?', default='send',
                        choices=list(COMMANDS.keys()))
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    if args.dry_run:
        os.environ['DRY_RUN'] = 'true'
        global DRY_RUN
        DRY_RUN = True

    COMMANDS[args.command](args)


if __name__ == '__main__':
    main()
