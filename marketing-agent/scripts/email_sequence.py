#!/usr/bin/env python3
"""Email Sequence — Fire PAS-framework 4-email cadence via Brevo API.

Only sends to leads where: status=connected, email IS NOT NULL, email_verified=1.
Safe to run daily — idempotent (checks DB state before sending).

Usage:
    python3 email_sequence.py scan    # Show leads ready for email + overdue follow-ups
    python3 email_sequence.py send    # Fire due emails (dry-run safe with DRY_RUN=true)
    python3 email_sequence.py status  # Full sequence progress per lead
    python3 email_sequence.py replies # Poll Brevo for new replies/opens
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from anthropic import Anthropic

# --- Config ---

DB_PATH = os.environ.get(
    'LEADS_DB_PATH',
    os.path.join(os.path.dirname(__file__), '..', 'data', 'marketing_leads.db')
)
TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), '..', 'templates', 'email_templates.json')
BREVO_API_KEY = os.environ.get('BREVO_API_KEY', '')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', '')
SENDER_NAME = os.environ.get('SENDER_NAME', 'Chudi')
DRY_RUN = os.environ.get('DRY_RUN', 'false').lower() == 'true'

BREVO_SEND_URL = 'https://api.brevo.com/v3/smtp/email'
BREVO_STATS_URL = 'https://api.brevo.com/v3/smtp/statistics/events'

# Days to wait before each follow-up
EMAIL_SPACING_DAYS = [0, 3, 7, 11]   # Email 1 on day 0, then +3, +7, +11
MAX_EMAILS = 4
DAILY_EMAIL_CAP = int(os.environ.get('DAILY_EMAIL_CAP', '50'))


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
BREVO_API_KEY = os.environ.get('BREVO_API_KEY', '')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', '')
SENDER_NAME = os.environ.get('SENDER_NAME', 'Chudi')
DRY_RUN = os.environ.get('DRY_RUN', 'false').lower() == 'true'

try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'tools'))
    from api_telemetry import log_usage as _log_usage
except ImportError:
    _log_usage = None


def get_db():
    if not os.path.exists(DB_PATH):
        print(f"DB not found: {DB_PATH}. Run init_db.py first.")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def load_templates() -> dict:
    if not os.path.exists(TEMPLATE_PATH):
        print(f"Templates not found: {TEMPLATE_PATH}")
        sys.exit(1)
    with open(TEMPLATE_PATH) as f:
        return json.load(f)


def today_email_count(conn) -> int:
    row = conn.execute(
        "SELECT emails_sent FROM daily_caps WHERE date=date('now')"
    ).fetchone()
    return row[0] if row else 0


def increment_email_cap(conn):
    conn.execute("""
        INSERT INTO daily_caps (date, emails_sent) VALUES (date('now'), 1)
        ON CONFLICT(date) DO UPDATE SET emails_sent = emails_sent + 1
    """)
    conn.commit()


def personalize_email(lead: dict, template: dict, ai_client) -> tuple[str, str]:
    """Return (subject, body) personalized via Claude haiku."""
    prompt = f"""You are writing a cold outreach email on behalf of {SENDER_NAME}.

Lead info:
- Name: {lead['name']}
- Title: {lead['title']}
- Company: {lead['company']}
- Notes: {lead['notes'] or 'none'}

Email #{lead['email_seq_num'] + 1} template:
Subject template: {template['subject_template']}
Body template: {template['body_template']}

Instructions:
- Replace [NAME] with their first name only
- Replace [COMPANY] with their company
- Replace [TITLE] with their title
- Replace [PROBLEM] with a specific pain point for their role/industry (1 sentence, no fluff)
- Replace [AGITATION] with the consequence of that problem (1 sentence)
- Replace [SOLUTION] with what {SENDER_NAME} offers — keep to 1 sentence
- Keep total email under 100 words
- Sound like a real human, not a marketer
- Return ONLY: first line = subject, remaining lines = body. No labels, no JSON."""

    response = ai_client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=300,
        messages=[{'role': 'user', 'content': prompt}]
    )
    if _log_usage:
        _log_usage("email_sequence", response)
    text = response.content[0].text.strip()
    lines = text.split('\n', 1)
    subject = lines[0].strip()
    body = lines[1].strip() if len(lines) > 1 else ''
    return subject, body


def send_brevo_email(to_email: str, to_name: str, subject: str, body: str) -> str | None:
    """Send via Brevo API. Returns message_id or None on failure."""
    if DRY_RUN:
        print(f"    [DRY RUN] Would send to {to_email}: {subject[:50]}")
        return 'dry-run-id'

    headers = {
        'api-key': BREVO_API_KEY,
        'Content-Type': 'application/json',
    }
    payload = {
        'sender': {'email': SENDER_EMAIL, 'name': SENDER_NAME},
        'to': [{'email': to_email, 'name': to_name}],
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


def get_ready_leads(conn) -> list:
    """Leads eligible for Email 1 (newly connected, verified email, not started)."""
    return conn.execute("""
        SELECT * FROM leads
        WHERE connection_accepted_at IS NOT NULL
          AND email IS NOT NULL
          AND email_verified = 1
          AND email_seq_started_at IS NULL
          AND email_replied_at IS NULL
          AND status NOT IN ('churned', 'converted')
        ORDER BY icp_score DESC, connection_accepted_at ASC
    """).fetchall()


def get_followup_leads(conn) -> list:
    """Leads due for follow-up emails 2-4."""
    now = datetime.now(timezone.utc)
    leads = conn.execute("""
        SELECT * FROM leads
        WHERE email_seq_started_at IS NOT NULL
          AND email_seq_num < ?
          AND email_replied_at IS NULL
          AND status NOT IN ('churned', 'converted')
          AND last_email_sent_at IS NOT NULL
        ORDER BY last_email_sent_at ASC
    """, (MAX_EMAILS,)).fetchall()

    due = []
    for lead in leads:
        seq_num = lead['email_seq_num']
        if seq_num >= len(EMAIL_SPACING_DAYS):
            continue
        last_sent = datetime.fromisoformat(lead['last_email_sent_at'])
        if last_sent.tzinfo is None:
            last_sent = last_sent.replace(tzinfo=timezone.utc)
        spacing = EMAIL_SPACING_DAYS[seq_num] if seq_num > 0 else 0
        due_at = last_sent + timedelta(days=spacing)
        if now >= due_at:
            due.append(lead)
    return due


def churn_expired_leads(conn):
    """Mark leads as churned if they've received all 4 emails with no reply."""
    now = datetime.now(timezone.utc)
    leads = conn.execute("""
        SELECT * FROM leads
        WHERE email_seq_num = ?
          AND email_replied_at IS NULL
          AND last_email_sent_at IS NOT NULL
          AND status NOT IN ('churned', 'converted')
    """, (MAX_EMAILS,)).fetchall()

    for lead in leads:
        last_sent = datetime.fromisoformat(lead['last_email_sent_at'])
        if last_sent.tzinfo is None:
            last_sent = last_sent.replace(tzinfo=timezone.utc)
        if now >= last_sent + timedelta(days=EMAIL_SPACING_DAYS[-1] + 3):
            conn.execute(
                "UPDATE leads SET status='churned' WHERE id=?", (lead['id'],)
            )
    conn.commit()


def cmd_scan(args):
    conn = get_db()
    ready = get_ready_leads(conn)
    followup = get_followup_leads(conn)
    conn.close()

    print(f"\nReady for Email 1: {len(ready)}")
    for r in ready[:5]:
        print(f"  {r['name']:<22} {r['company']:<24} [{r['email']}]")
    if len(ready) > 5:
        print(f"  ... and {len(ready)-5} more")

    print(f"\nDue for follow-up: {len(followup)}")
    for r in followup[:5]:
        print(f"  {r['name']:<22} Email #{r['email_seq_num']+1}  last: {r['last_email_sent_at'][:10]}")
    if len(followup) > 5:
        print(f"  ... and {len(followup)-5} more")
    print()


def cmd_send(args):
    if not BREVO_API_KEY and not DRY_RUN:
        print("BREVO_API_KEY not set.")
        sys.exit(1)
    if not SENDER_EMAIL and not DRY_RUN:
        print("SENDER_EMAIL not set.")
        sys.exit(1)

    templates = load_templates()
    ai_client = Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

    conn = get_db()
    churn_expired_leads(conn)

    daily_sent = today_email_count(conn)
    sent_this_run = 0

    all_due = get_ready_leads(conn) + get_followup_leads(conn)
    all_due = sorted(all_due, key=lambda r: r['icp_score'] or 0, reverse=True)

    if not all_due:
        print("No emails due today.")
        conn.close()
        return

    print(f"\n{len(all_due)} email(s) due. Daily cap: {daily_sent}/{DAILY_EMAIL_CAP}\n")

    for lead in all_due:
        if daily_sent + sent_this_run >= DAILY_EMAIL_CAP:
            print(f"Daily cap reached ({DAILY_EMAIL_CAP}). Stopping.")
            break

        seq_num = lead['email_seq_num']
        email_key = f"email_{seq_num + 1}"
        template = templates.get(email_key)
        if not template:
            print(f"  No template for {email_key}, skipping {lead['name']}")
            continue

        print(f"  Sending Email #{seq_num + 1} to {lead['name']} <{lead['email']}>")

        if ai_client:
            try:
                subject, body = personalize_email(dict(lead), template, ai_client)
            except Exception as e:
                print(f"    Claude personalization failed: {e}. Using template defaults.")
                subject = template['subject_template']
                body = template['body_template']
        else:
            subject = template['subject_template']
            body = template['body_template']

        msg_id = send_brevo_email(lead['email'], lead['name'] or '', subject, body)

        if msg_id:
            now = datetime.now(timezone.utc).isoformat()
            is_first = seq_num == 0
            conn.execute("""
                UPDATE leads SET
                    email_seq_num = email_seq_num + 1,
                    last_email_sent_at = ?,
                    email_seq_started_at = COALESCE(email_seq_started_at, ?),
                    status = CASE WHEN status != 'email_replied' THEN 'email_seq' ELSE status END
                WHERE id = ?
            """, (now, now if is_first else lead['email_seq_started_at'], lead['id']))
            conn.execute("""
                INSERT INTO email_events (lead_id, event_type, email_num, brevo_message_id, occurred_at)
                VALUES (?, 'sent', ?, ?, ?)
            """, (lead['id'], seq_num + 1, msg_id, now))
            conn.commit()
            increment_email_cap(conn)
            sent_this_run += 1
            print(f"    Sent. Message ID: {msg_id}")
        else:
            print(f"    Failed to send. Will retry tomorrow.")

        time.sleep(2)

    conn.close()
    mode = " [DRY RUN]" if DRY_RUN else ""
    print(f"\nDone{mode}. Sent {sent_this_run} email(s) this run.")


def cmd_status(args):
    conn = get_db()
    rows = conn.execute("""
        SELECT name, company, email, email_seq_num, email_seq_started_at,
               last_email_sent_at, email_replied_at, status
        FROM leads
        WHERE email_seq_started_at IS NOT NULL
        ORDER BY email_seq_started_at DESC
    """).fetchall()
    conn.close()

    if not rows:
        print("No sequences started yet.")
        return

    print(f"\n{'Name':<22} {'Company':<20} {'Seq':<6} {'Last Sent':<12} {'Status'}")
    print("-"*75)
    for r in rows:
        last = r['last_email_sent_at'][:10] if r['last_email_sent_at'] else '—'
        seq = f"{r['email_seq_num']}/{MAX_EMAILS}"
        print(f"  {(r['name'] or '?'):<20} {(r['company'] or '?'):<20} {seq:<6} {last:<12} {r['status']}")
    print()


def cmd_replies(args):
    """Minimal reply check — poll Brevo events for email_replied status."""
    if not BREVO_API_KEY:
        print("BREVO_API_KEY not set.")
        return

    print("Checking Brevo for reply events...")
    headers = {'api-key': BREVO_API_KEY}
    try:
        r = requests.get(BREVO_STATS_URL, params={
            'event': 'reply',
            'limit': 50,
            'offset': 0,
        }, headers=headers, timeout=10)
        r.raise_for_status()
        events = r.json().get('events', [])
        print(f"Found {len(events)} reply event(s) from Brevo.")
        for ev in events:
            email = ev.get('email', '')
            conn = get_db()
            row = conn.execute("SELECT id, name, email_replied_at FROM leads WHERE email=?", (email,)).fetchone()
            if row and not row['email_replied_at']:
                now = datetime.now(timezone.utc).isoformat()
                conn.execute("""
                    UPDATE leads SET email_replied_at=?, status='email_replied' WHERE id=?
                """, (now, row['id']))
                conn.commit()
                print(f"  Marked replied: {email}")
            conn.close()
    except Exception as e:
        print(f"Brevo poll failed: {e}")
        print("Check Brevo dashboard manually for now.")


COMMANDS = {
    'scan': cmd_scan,
    'send': cmd_send,
    'status': cmd_status,
    'replies': cmd_replies,
}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=list(COMMANDS.keys()))
    args = parser.parse_args()
    COMMANDS[args.command](args)

if __name__ == '__main__':
    main()
