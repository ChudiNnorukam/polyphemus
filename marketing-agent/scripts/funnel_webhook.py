#!/usr/bin/env python3
"""Funnel Webhook — Flask receiver for Gumroad and Stripe purchase events.

Runs as a persistent service. Enrolls buyers in post-purchase email sequences.

Usage:
    python3 scripts/funnel_webhook.py
    DRY_RUN=true python3 scripts/funnel_webhook.py

Endpoints:
    POST /webhook/gumroad
    POST /webhook/stripe
    GET  /health
"""

import hashlib
import hmac
import json
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
GUMROAD_SECRET = os.environ.get('GUMROAD_SECRET', '')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
WEBHOOK_PORT = int(os.environ.get('WEBHOOK_PORT', '8085'))
SLACK_BOT_TOKEN = os.environ.get('SLACK_BOT_TOKEN', '')
SLACK_CHANNEL_ID = os.environ.get('SLACK_CHANNEL_ID', '')

try:
    from flask import Flask, request, jsonify
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    print("Flask not installed. Run: pip install flask")
    sys.exit(1)

try:
    import stripe
    STRIPE_AVAILABLE = True
except ImportError:
    STRIPE_AVAILABLE = False
    stripe = None

app = Flask(__name__)


def get_db():
    if not os.path.exists(DB_PATH):
        raise RuntimeError(f"DB not found: {DB_PATH}. Run init_db.py + funnel_db_init.py extend first.")
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


def enroll_contact(conn, email: str, name: str, source: str, product_id: str,
                   amount_cents: int, sale_id: str) -> bool:
    """Insert contact + enroll in post-purchase-v1. Idempotent on sale_id."""
    now = datetime.now(timezone.utc).isoformat()

    # Insert contact (idempotent on sale_id — UNIQUE constraint)
    cursor = conn.execute("""
        INSERT OR IGNORE INTO funnel_contacts
            (email, name, source, product_id, amount_cents, sale_id, purchased_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (email, name, source, product_id, amount_cents, sale_id, now))

    if cursor.rowcount == 0:
        print(f"  Duplicate sale_id {sale_id} — skipping enrollment.")
        return False

    contact_id = cursor.lastrowid

    # Enroll in post-purchase-v1
    cursor2 = conn.execute("""
        INSERT INTO sequence_enrollments
            (contact_id, sequence_id, current_step, status, enrolled_at)
        VALUES (?, 'post-purchase-v1', 0, 'active', ?)
    """, (contact_id, now))

    enrollment_id = cursor2.lastrowid

    # Schedule step 1 immediately
    conn.execute("""
        INSERT INTO sequence_sends
            (enrollment_id, step, scheduled_for, status)
        VALUES (?, 1, datetime('now'), 'pending')
    """, (enrollment_id,))

    conn.commit()
    return True


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})


@app.route('/webhook/gumroad', methods=['POST'])
def webhook_gumroad():
    raw_body = request.get_data()

    # Verify HMAC-SHA256 signature
    if GUMROAD_SECRET:
        expected = hmac.new(
            GUMROAD_SECRET.encode(),
            raw_body,
            hashlib.sha256
        ).hexdigest()
        received = request.headers.get('X-Gumroad-Signature', '')
        if not hmac.compare_digest(expected, received):
            print("Gumroad: signature mismatch")
            return jsonify({'error': 'Invalid signature'}), 401
    else:
        print("WARNING: GUMROAD_SECRET not set — skipping signature verification")

    # Gumroad sends form-encoded data
    data = request.form

    # Filter test pings and non-sale events
    if data.get('test') == 'true':
        return jsonify({'status': 'test_ignored'}), 200
    if data.get('resource_name') != 'sale':
        return jsonify({'status': 'ignored'}), 200

    email = data.get('email', '').strip().lower()
    name = data.get('full_name', '').strip()
    product_id = data.get('product_id', '')
    product_name = data.get('product_name', '')
    sale_id = data.get('sale_id', '')
    price_str = data.get('price', '0')

    if not email or not sale_id:
        return jsonify({'error': 'Missing email or sale_id'}), 400

    try:
        amount_cents = int(float(price_str) * 100)
    except (ValueError, TypeError):
        amount_cents = 0

    print(f"Gumroad purchase: {email} — {product_name} (${amount_cents/100:.2f}) sale_id={sale_id}")

    if DRY_RUN:
        print(f"  [DRY RUN] Would enroll {email} in post-purchase-v1")
        send_slack(f":shopping_trolley: [DRY RUN] New purchase: {email} bought \"{product_name}\" (${amount_cents/100:.2f})")
        return jsonify({'status': 'dry_run'}), 200

    try:
        conn = get_db()
        enrolled = enroll_contact(conn, email, name, 'gumroad', product_id, amount_cents, sale_id)
        conn.close()
        if enrolled:
            send_slack(f":shopping_trolley: New purchase: {email} bought \"{product_name}\" (${amount_cents/100:.2f})")
    except Exception as e:
        print(f"  DB error: {e}")
        return jsonify({'error': 'DB error'}), 500

    return jsonify({'status': 'enrolled'}), 200


@app.route('/webhook/stripe', methods=['POST'])
def webhook_stripe():
    if not STRIPE_AVAILABLE or stripe is None:
        print("stripe package not installed")
        return jsonify({'error': 'stripe not available'}), 500

    payload = request.data  # raw bytes — never parse first

    if STRIPE_WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(
                payload,
                request.headers.get('Stripe-Signature', ''),
                STRIPE_WEBHOOK_SECRET
            )
        except stripe.error.SignatureVerificationError:
            print("Stripe: signature verification failed")
            return jsonify({'error': 'Invalid signature'}), 401
        except Exception as e:
            print(f"Stripe construct_event error: {e}")
            return jsonify({'error': str(e)}), 400
    else:
        print("WARNING: STRIPE_WEBHOOK_SECRET not set — skipping signature verification")
        try:
            event = json.loads(payload)
        except Exception:
            return jsonify({'error': 'Invalid JSON'}), 400

    if event.get('type') != 'checkout.session.completed':
        return jsonify({'status': 'ignored'}), 200

    session = event.get('data', {}).get('object', {})
    customer_details = session.get('customer_details', {})
    email = (customer_details.get('email') or '').strip().lower()
    name = customer_details.get('name', '')
    product_id = session.get('metadata', {}).get('product_id', '')
    sale_id = session.get('id', '')
    amount_cents = session.get('amount_total', 0)

    if not email or not sale_id:
        return jsonify({'error': 'Missing email or session id'}), 400

    print(f"Stripe purchase: {email} — product={product_id} (${amount_cents/100:.2f}) session={sale_id}")

    if DRY_RUN:
        print(f"  [DRY RUN] Would enroll {email} in post-purchase-v1")
        send_slack(f":shopping_trolley: [DRY RUN] New Stripe purchase: {email} (${amount_cents/100:.2f})")
        return jsonify({'status': 'dry_run'}), 200

    try:
        conn = get_db()
        enrolled = enroll_contact(conn, email, name, 'stripe', product_id, amount_cents, sale_id)
        conn.close()
        if enrolled:
            send_slack(f":shopping_trolley: New Stripe purchase: {email} (${amount_cents/100:.2f})")
    except Exception as e:
        print(f"  DB error: {e}")
        return jsonify({'error': 'DB error'}), 500

    return jsonify({'status': 'enrolled'}), 200


if __name__ == '__main__':
    port = WEBHOOK_PORT
    mode = ' [DRY RUN]' if DRY_RUN else ''
    print(f"Funnel webhook{mode} starting on port {port}")
    print(f"  POST /webhook/gumroad")
    print(f"  POST /webhook/stripe")
    print(f"  GET  /health")
    app.run(host='0.0.0.0', port=port, debug=False)
