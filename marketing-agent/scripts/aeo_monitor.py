#!/usr/bin/env python3
"""AEO Monitor - Check AI Overview, featured snippet, and PAA presence via Serper.dev.

Requires: pip install requests

Setup:
    Set SERPER_API_KEY in .env (get 2500 free queries at serper.dev)

Usage:
    python3 scripts/aeo_monitor.py [--dry-run]
    python3 scripts/aeo_monitor.py --report
"""

import argparse
import json
import os
import sqlite3
import sys
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
        CREATE TABLE IF NOT EXISTS aeo_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            has_answer_box INTEGER DEFAULT 0,
            answer_box_type TEXT,
            answer_box_source_url TEXT,
            answer_box_source_domain TEXT,
            we_own_answer_box INTEGER DEFAULT 0,
            has_knowledge_graph INTEGER DEFAULT 0,
            paa_count INTEGER DEFAULT 0,
            paa_questions TEXT,
            our_organic_position INTEGER,
            our_domain TEXT,
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


def query_serper(api_key: str, query: str) -> dict:
    """Call Serper.dev search API for a single query."""
    headers = {
        'X-API-KEY': api_key,
        'Content-Type': 'application/json',
    }
    payload = {
        'q': query,
        'gl': 'us',
        'hl': 'en',
        'num': 10,
    }

    try:
        r = requests.post(
            'https://google.serper.dev/search',
            json=payload,
            headers=headers,
            timeout=20,
        )
        r.raise_for_status()
        return r.json()
    except requests.exceptions.Timeout:
        print(f"  ERROR: Request timed out for query: {query}")
        return {}
    except requests.exceptions.HTTPError as e:
        print(f"  ERROR: HTTP {e.response.status_code} for query: {query}")
        if e.response.status_code == 401:
            print("  Check your SERPER_API_KEY.")
        return {}
    except Exception as e:
        print(f"  ERROR: {e} for query: {query}")
        return {}


def parse_serper_result(data: dict, query: str, our_domains: list) -> dict:
    """Parse Serper.dev response into a flat AEO record."""
    record = {
        'query': query,
        'has_answer_box': 0,
        'answer_box_type': None,
        'answer_box_source_url': None,
        'answer_box_source_domain': None,
        'we_own_answer_box': 0,
        'has_knowledge_graph': 0,
        'paa_count': 0,
        'paa_questions': None,
        'our_organic_position': None,
        'our_domain': None,
    }

    if not data:
        return record

    # Answer box (featured snippet / direct answer)
    answer_box = data.get('answerBox', {})
    if answer_box:
        record['has_answer_box'] = 1
        record['answer_box_type'] = answer_box.get('type', 'answer')
        source_url = answer_box.get('link') or answer_box.get('sitelink') or ''
        record['answer_box_source_url'] = source_url
        source_domain = extract_domain(source_url)
        record['answer_box_source_domain'] = source_domain
        if any(od in source_domain for od in our_domains):
            record['we_own_answer_box'] = 1

    # Knowledge graph
    kg = data.get('knowledgeGraph', {})
    if kg:
        record['has_knowledge_graph'] = 1

    # People Also Ask
    paa = data.get('peopleAlsoAsk', [])
    if paa:
        record['paa_count'] = len(paa)
        first_three = [item.get('question', '') for item in paa[:3] if item.get('question')]
        if first_three:
            record['paa_questions'] = json.dumps(first_three)

    # Organic results - check if our domain is in top 10
    organic = data.get('organic', [])
    for item in organic:
        link = item.get('link', '')
        domain = extract_domain(link)
        position = item.get('position', 0)
        if any(od in domain for od in our_domains):
            if record['our_organic_position'] is None or position < record['our_organic_position']:
                record['our_organic_position'] = position
                record['our_domain'] = domain

    return record


def write_results(conn, records: list):
    """Write AEO records to aeo_snapshots table."""
    conn.executemany("""
        INSERT INTO aeo_snapshots
            (query, has_answer_box, answer_box_type, answer_box_source_url,
             answer_box_source_domain, we_own_answer_box, has_knowledge_graph,
             paa_count, paa_questions, our_organic_position, our_domain)
        VALUES
            (:query, :has_answer_box, :answer_box_type, :answer_box_source_url,
             :answer_box_source_domain, :we_own_answer_box, :has_knowledge_graph,
             :paa_count, :paa_questions, :our_organic_position, :our_domain)
    """, records)
    conn.commit()


def print_report():
    """Print AEO presence table from DB."""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT query, has_answer_box, answer_box_type, answer_box_source_domain,
                   we_own_answer_box, paa_count, our_organic_position, checked_at
            FROM aeo_snapshots
            WHERE checked_at = (SELECT MAX(checked_at) FROM aeo_snapshots)
            ORDER BY has_answer_box DESC, our_organic_position ASC NULLS LAST
            LIMIT 20
        """).fetchall()
    finally:
        conn.close()

    if not rows:
        print("No AEO data found. Run without --report first.")
        return

    print(
        f"\n{'QUERY':<42} {'ANSW':>5} {'TYPE':<15} {'SOURCE':<22} "
        f"{'OURS':>5} {'PAA':>4} {'ORG POS':>8}"
    )
    print('-' * 110)
    for r in rows:
        query = r['query'][:41]
        ab = 'yes' if r['has_answer_box'] else 'no'
        ab_type = (r['answer_box_type'] or '')[:14]
        source = (r['answer_box_source_domain'] or '')[:21]
        ours = 'yes' if r['we_own_answer_box'] else 'no'
        paa = str(r['paa_count'])
        org_pos = str(r['our_organic_position']) if r['our_organic_position'] else '-'
        print(f"{query:<42} {ab:>5} {ab_type:<15} {source:<22} {ours:>5} {paa:>4} {org_pos:>8}")


def main():
    parser = argparse.ArgumentParser(description='Check AEO presence via Serper.dev.')
    parser.add_argument('--dry-run', action='store_true', help='Print results, do not write to DB')
    parser.add_argument('--report', action='store_true', help='Print AEO table from DB and exit')
    args = parser.parse_args()

    if args.report:
        print_report()
        return

    api_key = os.environ.get('SERPER_API_KEY', '')
    if not api_key:
        print("ERROR: SERPER_API_KEY not set in environment.")
        print("Get 2500 free queries at serper.dev and set SERPER_API_KEY in .env.")
        sys.exit(1)

    queries_raw = os.environ.get('SERP_TARGET_QUERIES', DEFAULT_QUERIES)
    queries = [q.strip() for q in queries_raw.split(',') if q.strip()]

    domains_raw = os.environ.get('SERP_OUR_DOMAINS', DEFAULT_OUR_DOMAINS)
    our_domains = [d.strip() for d in domains_raw.split(',') if d.strip()]

    print(f"AEO Monitor - checking {len(queries)} queries")
    print(f"Our domains: {', '.join(our_domains)}")
    if args.dry_run:
        print("[DRY RUN] No data will be written to DB.")

    records = []
    for query in queries:
        print(f"\nChecking: {query}")
        data = query_serper(api_key, query)
        record = parse_serper_result(data, query, our_domains)
        ab_str = f"YES ({record['answer_box_type']})" if record['has_answer_box'] else 'no'
        ours_str = 'WE OWN IT' if record['we_own_answer_box'] else 'no'
        org_str = str(record['our_organic_position']) if record['our_organic_position'] else 'not found'
        print(f"  Answer box: {ab_str} | Ours: {ours_str} | Organic pos: {org_str} | PAA: {record['paa_count']}")
        records.append(record)

    if records and not args.dry_run:
        conn = get_db()
        try:
            ensure_tables(conn)
            write_results(conn, records)
            print(f"\nWrote {len(records)} records to aeo_snapshots.")
        finally:
            conn.close()
    elif records and args.dry_run:
        print("\n[DRY RUN] Sample record:")
        print(json.dumps(records[0], indent=2, default=str))

    owned = sum(1 for r in records if r['we_own_answer_box'])
    has_ab = sum(1 for r in records if r['has_answer_box'])
    print(f"\nSummary: {has_ab}/{len(records)} queries have answer box, we own {owned}.")
    print("Done.")


if __name__ == '__main__':
    main()
