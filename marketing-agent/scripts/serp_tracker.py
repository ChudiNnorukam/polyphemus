#!/usr/bin/env python3
"""SERP Tracker - Check Google rankings for target queries via DataForSEO.

Requires: pip install requests

Setup:
    Set DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD in .env
    (get free $1 credits at dataforseo.com)

Usage:
    python3 scripts/serp_tracker.py [--queries "query1,query2"] [--dry-run]
    python3 scripts/serp_tracker.py --report
"""

import argparse
import base64
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests


DB_PATH = os.environ.get(
    'LEADS_DB_PATH',
    os.path.join(os.path.dirname(__file__), '..', 'data', 'marketing_leads.db')
)

DEFAULT_QUERIES = (
    'ai citation rate,ai visibility audit,how to measure ai visibility,'
    'does my site appear in ai search,chatgpt site recommendations,'
    'aeo audit tool,answer engine optimization audit'
)

DEFAULT_OUR_DOMAINS = 'citability.dev,chudi.dev'


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


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS serp_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            our_domain TEXT,
            our_url TEXT,
            our_position INTEGER,
            top1_url TEXT,
            top1_domain TEXT,
            has_featured_snippet INTEGER DEFAULT 0,
            has_paa INTEGER DEFAULT 0,
            has_ai_overview INTEGER DEFAULT 0,
            result_count INTEGER DEFAULT 0,
            action TEXT,
            checked_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def extract_domain(url: str) -> str:
    """Extract bare domain from URL."""
    if not url:
        return ''
    try:
        parsed = urlparse(url)
        domain = parsed.netloc or parsed.path
        return domain.lstrip('www.')
    except Exception:
        return ''


def determine_action(our_position, has_featured_snippet: bool, we_own_snippet: bool) -> str:
    """Determine recommended action based on ranking position."""
    if our_position is None:
        action = 'WRITE'
    elif our_position <= 10:
        action = 'OPTIMIZE'
    elif our_position <= 30:
        action = 'EXPAND'
    else:
        action = 'REWRITE'

    if has_featured_snippet and not we_own_snippet:
        action += '+SCHEMA'

    return action


def query_dataforseo(login: str, password: str, query: str) -> dict:
    """Call DataForSEO SERP API for a single query."""
    creds = base64.b64encode(f"{login}:{password}".encode()).decode()
    headers = {
        'Authorization': f'Basic {creds}',
        'Content-Type': 'application/json',
    }
    payload = [{
        'keyword': query,
        'location_code': 2840,
        'language_code': 'en',
        'depth': 10,
    }]

    try:
        r = requests.post(
            'https://api.dataforseo.com/v3/serp/google/organic/live/regular',
            json=payload,
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()
    except requests.exceptions.Timeout:
        print(f"  ERROR: Request timed out for query: {query}")
        return {}
    except requests.exceptions.HTTPError as e:
        print(f"  ERROR: HTTP {e.response.status_code} for query: {query}")
        return {}
    except Exception as e:
        print(f"  ERROR: {e} for query: {query}")
        return {}


def parse_serp_result(data: dict, query: str, our_domains: list) -> dict:
    """Parse DataForSEO response into a flat record."""
    record = {
        'query': query,
        'our_domain': None,
        'our_url': None,
        'our_position': None,
        'top1_url': None,
        'top1_domain': None,
        'has_featured_snippet': 0,
        'has_paa': 0,
        'has_ai_overview': 0,
        'result_count': 0,
        'action': 'WRITE',
    }

    tasks = data.get('tasks', [])
    if not tasks:
        return record

    task = tasks[0]
    task_result = task.get('result', [])
    if not task_result:
        return record

    result = task_result[0]
    items = result.get('items', [])
    record['result_count'] = result.get('se_results_count', 0) or 0

    has_featured_snippet = False
    we_own_snippet = False

    for item in items:
        item_type = item.get('type', '')

        if item_type == 'featured_snippet':
            has_featured_snippet = True
            snippet_domain = extract_domain(item.get('url', ''))
            if any(od in snippet_domain for od in our_domains):
                we_own_snippet = True

        elif item_type == 'people_also_ask':
            record['has_paa'] = 1

        elif item_type == 'ai_overview':
            record['has_ai_overview'] = 1

        elif item_type == 'organic':
            position = item.get('rank_absolute', 0)
            url = item.get('url', '')
            domain = extract_domain(url)

            if position == 1 and not record['top1_url']:
                record['top1_url'] = url
                record['top1_domain'] = domain

            if any(od in domain for od in our_domains):
                if record['our_position'] is None or position < record['our_position']:
                    record['our_position'] = position
                    record['our_url'] = url
                    record['our_domain'] = domain

    record['has_featured_snippet'] = 1 if has_featured_snippet else 0
    record['action'] = determine_action(
        record['our_position'], has_featured_snippet, we_own_snippet
    )

    return record


def write_results(conn, records: list):
    """Write SERP records to serp_snapshots table."""
    conn.executemany("""
        INSERT INTO serp_snapshots
            (query, our_domain, our_url, our_position, top1_url, top1_domain,
             has_featured_snippet, has_paa, has_ai_overview, result_count, action)
        VALUES
            (:query, :our_domain, :our_url, :our_position, :top1_url, :top1_domain,
             :has_featured_snippet, :has_paa, :has_ai_overview, :result_count, :action)
    """, records)
    conn.commit()


def print_report():
    """Print action table from DB."""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT query, our_position, has_featured_snippet, has_paa, action, checked_at
            FROM serp_snapshots
            WHERE checked_at = (SELECT MAX(checked_at) FROM serp_snapshots)
            ORDER BY our_position ASC NULLS LAST
            LIMIT 20
        """).fetchall()
    finally:
        conn.close()

    if not rows:
        print("No SERP data found. Run without --report first.")
        return

    print(f"\n{'QUERY':<45} {'POS':>5} {'FEAT':>5} {'PAA':>5} {'ACTION':<20}")
    print('-' * 90)
    for r in rows:
        pos = str(r['our_position']) if r['our_position'] else '-'
        feat = 'yes' if r['has_featured_snippet'] else 'no'
        paa = 'yes' if r['has_paa'] else 'no'
        action = r['action'] or ''
        query = r['query'][:44]
        print(f"{query:<45} {pos:>5} {feat:>5} {paa:>5} {action:<20}")


def main():
    parser = argparse.ArgumentParser(description='Check Google rankings via DataForSEO.')
    parser.add_argument('--queries', type=str, help='Comma-separated queries to check (overrides env)')
    parser.add_argument('--dry-run', action='store_true', help='Print results, do not write to DB')
    parser.add_argument('--report', action='store_true', help='Print action table from DB and exit')
    args = parser.parse_args()

    if args.report:
        print_report()
        return

    login = os.environ.get('DATAFORSEO_LOGIN', '')
    password = os.environ.get('DATAFORSEO_PASSWORD', '')
    missing = []
    if not login:
        missing.append('DATAFORSEO_LOGIN')
    if not password:
        missing.append('DATAFORSEO_PASSWORD')
    if missing:
        print(f"ERROR: Missing required env vars: {', '.join(missing)}")
        print("Set them in .env or export them before running.")
        sys.exit(1)

    if args.queries:
        queries_raw = args.queries
    else:
        queries_raw = os.environ.get('SERP_TARGET_QUERIES', DEFAULT_QUERIES)
    queries = [q.strip() for q in queries_raw.split(',') if q.strip()]

    domains_raw = os.environ.get('SERP_OUR_DOMAINS', DEFAULT_OUR_DOMAINS)
    our_domains = [d.strip() for d in domains_raw.split(',') if d.strip()]

    print(f"SERP Tracker - checking {len(queries)} queries")
    print(f"Our domains: {', '.join(our_domains)}")
    if args.dry_run:
        print("[DRY RUN] No data will be written to DB.")

    records = []
    for query in queries:
        print(f"\nChecking: {query}")
        data = query_dataforseo(login, password, query)
        record = parse_serp_result(data, query, our_domains)
        pos_str = str(record['our_position']) if record['our_position'] else 'not found'
        print(f"  Position: {pos_str} | Action: {record['action']}")
        records.append(record)

    if records and not args.dry_run:
        conn = get_db()
        try:
            ensure_tables(conn)
            write_results(conn, records)
            print(f"\nWrote {len(records)} records to serp_snapshots.")
        finally:
            conn.close()
    elif records and args.dry_run:
        print("\n[DRY RUN] Sample record:")
        print(json.dumps(records[0], indent=2, default=str))

    print("\nSummary:")
    write_count = sum(1 for r in records if r['action'] and r['action'].startswith('WRITE'))
    expand_count = sum(1 for r in records if r['action'] and 'EXPAND' in r['action'])
    optimize_count = sum(1 for r in records if r['action'] and 'OPTIMIZE' in r['action'])
    print(f"  WRITE (not ranking): {write_count}")
    print(f"  EXPAND (pos 11-30):  {expand_count}")
    print(f"  OPTIMIZE (top 10):   {optimize_count}")
    print("Done.")


if __name__ == '__main__':
    main()
