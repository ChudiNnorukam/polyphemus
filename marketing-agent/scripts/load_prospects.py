#!/usr/bin/env python3
"""Load Prospects — Import ICP list from CSV into leads.db.

CSV format: linkedin_url,name,company,title,icp_score
            (header required, icp_score 1-10)

Usage:
    python3 load_prospects.py prospects/icp_list.csv
    python3 load_prospects.py prospects/icp_list.csv --dry-run
"""

import argparse
import csv
import os
import sqlite3
import sys

DB_PATH = os.environ.get(
    'LEADS_DB_PATH',
    os.path.join(os.path.dirname(__file__), '..', 'data', 'marketing_leads.db')
)

REQUIRED_COLS = {'linkedin_url', 'name', 'company', 'title'}

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('csv_file')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    if not os.path.exists(args.csv_file):
        print(f"File not found: {args.csv_file}")
        sys.exit(1)

    with open(args.csv_file, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        print("CSV is empty.")
        sys.exit(1)

    missing = REQUIRED_COLS - set(rows[0].keys())
    if missing:
        print(f"Missing columns: {missing}")
        sys.exit(1)

    inserted = 0
    skipped = 0

    conn = get_db()
    for row in rows:
        url = row['linkedin_url'].strip()
        if not url:
            continue

        if args.dry_run:
            print(f"  WOULD INSERT: {row['name']} @ {row['company']}")
            inserted += 1
            continue

        try:
            conn.execute("""
                INSERT OR IGNORE INTO leads
                    (linkedin_url, name, company, title, icp_score)
                VALUES (?, ?, ?, ?, ?)
            """, (
                url,
                row['name'].strip(),
                row['company'].strip(),
                row['title'].strip(),
                int(row.get('icp_score', 5))
            ))
            if conn.execute("SELECT changes()").fetchone()[0]:
                inserted += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"  ERROR on {url}: {e}")

    if not args.dry_run:
        conn.commit()
    conn.close()

    mode = "[DRY RUN] " if args.dry_run else ""
    print(f"{mode}Loaded: {inserted} new | Skipped (duplicate): {skipped}")

if __name__ == '__main__':
    main()
