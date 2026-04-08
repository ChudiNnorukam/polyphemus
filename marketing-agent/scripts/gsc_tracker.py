#!/usr/bin/env python3
"""GSC Tracker - Pull Google Search Console query data weekly.

Requires:
    pip install google-auth google-auth-httplib2 google-api-python-client

Setup:
    1. Google Cloud Console -> Enable "Google Search Console API"
    2. Create service account -> download JSON key
    3. In GSC: Settings -> Users and permissions -> Add service account email as Owner
    4. Set GSC_SERVICE_ACCOUNT_JSON=/path/to/key.json in .env

Usage:
    python3 scripts/gsc_tracker.py [--days 28] [--dry-run]
    python3 scripts/gsc_tracker.py --report
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone


DB_PATH = os.environ.get(
    'LEADS_DB_PATH',
    os.path.join(os.path.dirname(__file__), '..', 'data', 'marketing_leads.db')
)


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
        CREATE TABLE IF NOT EXISTS gsc_queries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site TEXT NOT NULL,
            query TEXT NOT NULL,
            page TEXT NOT NULL,
            clicks INTEGER DEFAULT 0,
            impressions INTEGER DEFAULT 0,
            ctr REAL DEFAULT 0,
            position REAL DEFAULT 0,
            date_range TEXT NOT NULL,
            pulled_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gsc_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site TEXT NOT NULL,
            week_start TEXT NOT NULL,
            total_clicks INTEGER,
            total_impressions INTEGER,
            avg_position REAL,
            queries_with_impressions INTEGER,
            top_query TEXT,
            pulled_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def build_gsc_service(key_path: str):
    """Build authenticated GSC service using service account JSON."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        print("ERROR: Missing google-auth packages.")
        print("Install: pip install google-auth google-auth-httplib2 google-api-python-client")
        sys.exit(1)

    scopes = ['https://www.googleapis.com/auth/webmasters.readonly']
    creds = service_account.Credentials.from_service_account_file(key_path, scopes=scopes)
    service = build('searchconsole', 'v1', credentials=creds, cache_discovery=False)
    return service


def pull_site_data(service, site: str, days: int) -> list:
    """Pull query+page data for the last N days from GSC."""
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=days)

    body = {
        'startDate': start_date.isoformat(),
        'endDate': end_date.isoformat(),
        'dimensions': ['query', 'page'],
        'rowLimit': 5000,
    }

    try:
        response = service.searchanalytics().query(siteUrl=site, body=body).execute()
    except Exception as e:
        print(f"ERROR pulling data for {site}: {e}")
        return []

    rows = response.get('rows', [])
    date_range = f"{start_date.isoformat()} to {end_date.isoformat()}"
    results = []
    for row in rows:
        keys = row.get('keys', ['', ''])
        results.append({
            'site': site,
            'query': keys[0] if len(keys) > 0 else '',
            'page': keys[1] if len(keys) > 1 else '',
            'clicks': int(row.get('clicks', 0)),
            'impressions': int(row.get('impressions', 0)),
            'ctr': float(row.get('ctr', 0.0)),
            'position': float(row.get('position', 0.0)),
            'date_range': date_range,
        })
    return results


def write_queries(conn, rows: list):
    """Write query rows to gsc_queries table."""
    conn.executemany("""
        INSERT INTO gsc_queries (site, query, page, clicks, impressions, ctr, position, date_range)
        VALUES (:site, :query, :page, :clicks, :impressions, :ctr, :position, :date_range)
    """, rows)
    conn.commit()


def write_snapshot(conn, site: str, rows: list):
    """Compute and write a weekly snapshot for a site."""
    if not rows:
        return

    week_start = datetime.now(timezone.utc).date() - timedelta(days=7)
    total_clicks = sum(r['clicks'] for r in rows)
    total_impressions = sum(r['impressions'] for r in rows)
    queries_with_impressions = sum(1 for r in rows if r['impressions'] > 0)

    if queries_with_impressions > 0:
        avg_position = sum(r['position'] for r in rows if r['impressions'] > 0) / queries_with_impressions
    else:
        avg_position = 0.0

    top_query = max(rows, key=lambda r: r['impressions'], default={}).get('query', '')

    conn.execute("""
        INSERT INTO gsc_snapshots
            (site, week_start, total_clicks, total_impressions, avg_position,
             queries_with_impressions, top_query)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (site, week_start.isoformat(), total_clicks, total_impressions,
          round(avg_position, 2), queries_with_impressions, top_query))
    conn.commit()


def format_slack_alert(site_results: dict) -> str:
    """Build Slack alert text. Print only - does not send."""
    lines = ['*GSC Weekly Update*']
    for site, rows in site_results.items():
        total_clicks = sum(r['clicks'] for r in rows)
        total_impressions = sum(r['impressions'] for r in rows)
        new_queries = len([r for r in rows if r['impressions'] > 0])
        lines.append(
            f"*{site}*: {total_clicks} clicks, {total_impressions} impressions, "
            f"{new_queries} queries with impressions"
        )
        if rows:
            top = max(rows, key=lambda r: r['impressions'])
            lines.append(f"  Top query: \"{top['query']}\" ({top['impressions']} imp)")
    return '\n'.join(lines)


def print_report():
    """Print top 20 queries sorted by impressions from DB."""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT site, query, page, impressions, clicks, position, date_range
            FROM gsc_queries
            WHERE pulled_at = (SELECT MAX(pulled_at) FROM gsc_queries)
            ORDER BY impressions DESC
            LIMIT 20
        """).fetchall()
    finally:
        conn.close()

    if not rows:
        print("No GSC query data found. Run without --report first to pull data.")
        return

    print(f"\n{'QUERY':<40} {'PAGE':<45} {'IMP':>6} {'CLICKS':>6} {'POS':>6}")
    print('-' * 110)
    for r in rows:
        query = r['query'][:39]
        page = r['page'][:44]
        print(f"{query:<40} {page:<45} {r['impressions']:>6} {r['clicks']:>6} {r['position']:>6.1f}")


def main():
    parser = argparse.ArgumentParser(description='Pull GSC query data weekly.')
    parser.add_argument('--days', type=int, default=28, help='Number of days to pull (default: 28)')
    parser.add_argument('--dry-run', action='store_true', help='Print data, do not write to DB')
    parser.add_argument('--report', action='store_true', help='Print top queries from DB and exit')
    args = parser.parse_args()

    if args.report:
        print_report()
        return

    key_path = os.environ.get('GSC_SERVICE_ACCOUNT_JSON', '')
    if not key_path:
        print("ERROR: GSC_SERVICE_ACCOUNT_JSON not set in environment.")
        print("Set it to the path of your Google service account JSON key file.")
        sys.exit(1)

    if not os.path.exists(key_path):
        print(f"ERROR: Service account JSON not found at: {key_path}")
        sys.exit(1)

    sites_raw = os.environ.get('GSC_SITES', 'https://citability.dev/,https://chudi.dev/')
    sites = [s.strip() for s in sites_raw.split(',') if s.strip()]

    print(f"GSC Tracker - pulling last {args.days} days for {len(sites)} site(s)")
    if args.dry_run:
        print("[DRY RUN] No data will be written to DB.")

    service = build_gsc_service(key_path)
    site_results = {}

    for site in sites:
        print(f"\nPulling: {site}")
        rows = pull_site_data(service, site, args.days)
        print(f"  Got {len(rows)} query rows")
        site_results[site] = rows

        if rows and not args.dry_run:
            conn = get_db()
            try:
                ensure_tables(conn)
                write_queries(conn, rows)
                write_snapshot(conn, site, rows)
                print(f"  Wrote {len(rows)} rows + snapshot to DB")
            finally:
                conn.close()
        elif rows and args.dry_run:
            print(f"  [DRY RUN] Sample row: {rows[0]}")

    slack_text = format_slack_alert(site_results)
    print("\n--- Slack Alert Preview ---")
    print(slack_text)
    print("--- End Slack Alert ---")
    print("\nDone.")


if __name__ == '__main__':
    main()
