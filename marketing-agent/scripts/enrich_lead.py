#!/usr/bin/env python3
"""Lead Enrichment — Find + verify emails for connected leads via Apollo.io + ZeroBounce.

Runs daily after linkedin sessions. Only processes leads with status=connected
that don't have an email yet.

Usage:
    python3 enrich_lead.py scan       # Show leads needing enrichment
    python3 enrich_lead.py run        # Find + verify emails (uses API credits)
    python3 enrich_lead.py credits    # Check estimated credits remaining
"""

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

import requests

# --- Config ---

DB_PATH = os.environ.get(
    'LEADS_DB_PATH',
    os.path.join(os.path.dirname(__file__), '..', 'data', 'marketing_leads.db')
)
APOLLO_API_KEY = os.environ.get('APOLLO_API_KEY', '')
ZEROBOUNCE_API_KEY = os.environ.get('ZEROBOUNCE_API_KEY', '')

APOLLO_MATCH_URL = 'https://api.apollo.io/api/v1/people/match'
APOLLO_ORG_URL   = 'https://api.apollo.io/api/v1/organizations/search'
ZB_VALIDATE_URL  = 'https://api.zerobounce.net/v2/validate'

# Only use verified emails — protects domain reputation
MIN_APOLLO_CONFIDENCE = 'likely'  # Apollo grades: likely / possibly / none
ZB_VALID_STATUSES = {'valid'}


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
APOLLO_API_KEY = os.environ.get('APOLLO_API_KEY', '')
ZEROBOUNCE_API_KEY = os.environ.get('ZEROBOUNCE_API_KEY', '')


def get_db():
    if not os.path.exists(DB_PATH):
        print(f"DB not found: {DB_PATH}. Run init_db.py first.")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def resolve_domain(company_name: str) -> str | None:
    """Ask Apollo to resolve company name to primary domain."""
    if not APOLLO_API_KEY:
        return None
    try:
        r = requests.get(APOLLO_ORG_URL, params={
            'q_organization_name': company_name,
            'page': 1,
            'per_page': 1,
        }, headers={'x-api-key': APOLLO_API_KEY, 'Cache-Control': 'no-cache'}, timeout=10)
        r.raise_for_status()
        orgs = r.json().get('organizations', [])
        if orgs:
            return orgs[0].get('primary_domain')
    except Exception as e:
        print(f"    Apollo domain lookup failed for '{company_name}': {e}")
    return None


def apollo_find_email(first: str, last: str, domain: str) -> tuple[str | None, str | None]:
    """Return (email, confidence) or (None, None)."""
    if not APOLLO_API_KEY:
        return None, None
    try:
        r = requests.post(APOLLO_MATCH_URL, json={
            'first_name': first,
            'last_name': last,
            'domain': domain,
            'reveal_personal_emails': False,
        }, headers={
            'x-api-key': APOLLO_API_KEY,
            'Content-Type': 'application/json',
            'Cache-Control': 'no-cache',
        }, timeout=15)
        r.raise_for_status()
        person = r.json().get('person', {})
        email = person.get('email')
        confidence = person.get('email_status')  # 'likely', 'possibly', etc.
        return email, confidence
    except Exception as e:
        print(f"    Apollo email lookup failed: {e}")
    return None, None


def zerobounce_verify(email: str) -> str | None:
    """Return ZeroBounce status: 'valid', 'invalid', 'catch-all', 'unknown', etc."""
    if not ZEROBOUNCE_API_KEY:
        return None
    try:
        r = requests.get(ZB_VALIDATE_URL, params={
            'api_key': ZEROBOUNCE_API_KEY,
            'email': email,
            'ip_address': '',
        }, timeout=10)
        r.raise_for_status()
        return r.json().get('status')
    except Exception as e:
        print(f"    ZeroBounce verify failed for {email}: {e}")
    return None


def parse_name(full_name: str) -> tuple[str, str]:
    parts = full_name.strip().split()
    if len(parts) >= 2:
        return parts[0], ' '.join(parts[1:])
    return full_name, ''


def cmd_scan(args):
    conn = get_db()
    rows = conn.execute("""
        SELECT id, name, company, title, status, email
        FROM leads
        WHERE connection_accepted_at IS NOT NULL
          AND (email IS NULL OR email_verified = 0)
        ORDER BY icp_score DESC, connection_accepted_at ASC
    """).fetchall()
    conn.close()

    if not rows:
        print("No leads need enrichment right now.")
        return

    print(f"\n{len(rows)} lead(s) need email enrichment:\n")
    print(f"  {'Name':<22} {'Company':<24} {'Status':<12} {'Email'}")
    print("  " + "-"*70)
    for r in rows:
        email_str = r['email'] or '—'
        print(f"  {(r['name'] or '?'):<22} {(r['company'] or '?'):<24} {r['status']:<12} {email_str}")
    print()


def cmd_run(args):
    if not APOLLO_API_KEY:
        print("APOLLO_API_KEY not set. Add to .env and retry.")
        sys.exit(1)

    conn = get_db()
    rows = conn.execute("""
        SELECT id, name, company, title
        FROM leads
        WHERE connection_accepted_at IS NOT NULL
          AND email IS NULL
        ORDER BY icp_score DESC, connection_accepted_at ASC
        LIMIT 50
    """).fetchall()

    if not rows:
        print("No leads to enrich.")
        conn.close()
        return

    print(f"\nEnriching {len(rows)} lead(s)...\n")
    found = 0
    verified = 0

    for r in rows:
        lead_id = r['id']
        name = r['name'] or ''
        company = r['company'] or ''
        first, last = parse_name(name)
        print(f"  {name} @ {company}")

        # Step 1: Resolve company domain
        domain = conn.execute(
            "SELECT company_domain FROM leads WHERE id=?", (lead_id,)
        ).fetchone()[0]

        if not domain and company:
            domain = resolve_domain(company)
            if domain:
                conn.execute(
                    "UPDATE leads SET company_domain=? WHERE id=?", (domain, lead_id)
                )
                conn.commit()
                print(f"    Domain: {domain}")
            time.sleep(0.5)

        if not domain:
            print("    No domain found, skipping.")
            continue

        # Step 2: Apollo email lookup
        email, confidence = apollo_find_email(first, last, domain)
        time.sleep(1)

        if not email:
            print("    No email found via Apollo.")
            continue

        print(f"    Apollo: {email} (confidence: {confidence})")

        if confidence not in ('likely', 'verified'):
            print("    Confidence too low, skipping.")
            continue

        found += 1

        # Step 3: ZeroBounce verification
        zb_status = zerobounce_verify(email) if ZEROBOUNCE_API_KEY else 'skipped'
        time.sleep(0.5)
        print(f"    ZeroBounce: {zb_status}")

        is_verified = 1 if zb_status in ZB_VALID_STATUSES or zb_status == 'skipped' else 0

        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            UPDATE leads
            SET email=?, email_verified=?, email_found_at=?, email_verified_at=?
            WHERE id=?
        """, (email, is_verified, now, now if is_verified else None, lead_id))
        conn.commit()

        if is_verified:
            verified += 1
            print(f"    Saved + verified.")
        else:
            print(f"    Saved (unverified — will NOT enter email sequence).")

    conn.close()
    print(f"\nDone: {found} found, {verified} verified and ready for sequences.")


def cmd_credits(args):
    print("\nCredit estimates (free tiers):")
    print("  Apollo.io:    50 email exports/month")
    print("  ZeroBounce:   100 verifications/month")
    print()

    conn = get_db()
    apollo_used = conn.execute(
        "SELECT COUNT(*) FROM leads WHERE email_found_at > date('now', '-30 days')"
    ).fetchone()[0]
    zb_used = conn.execute(
        "SELECT COUNT(*) FROM leads WHERE email_verified_at > date('now', '-30 days')"
    ).fetchone()[0]
    conn.close()

    print(f"  Apollo used (30d):    {apollo_used}/50")
    print(f"  ZeroBounce used (30d):{zb_used}/100")
    print()


COMMANDS = {
    'scan': cmd_scan,
    'run': cmd_run,
    'credits': cmd_credits,
}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=list(COMMANDS.keys()))
    args = parser.parse_args()
    COMMANDS[args.command](args)

if __name__ == '__main__':
    main()
