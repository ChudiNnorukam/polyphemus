#!/usr/bin/env python3
"""LinkedIn Schedule Audit — verify upcoming LinkedIn cadence and schedule health.

Usage:
    python3 scripts/linkedin_schedule_audit.py
    python3 scripts/linkedin_schedule_audit.py --limit 12
"""

import argparse
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DB_PATH = os.environ.get(
    'LEADS_DB_PATH',
    os.path.join(os.path.dirname(__file__), '..', 'data', 'marketing_leads.db')
)
LOCAL_TZ = ZoneInfo('America/Los_Angeles')
ALLOWED_WEEKDAYS = {0, 1, 2, 3}  # Mon-Thu


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
    if not os.path.exists(DB_PATH):
        print(f"DB not found: {DB_PATH}. Run init_db.py + funnel_db_init.py extend first.")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def parse_db_utc(value: str):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def local_dt(value: str):
    return parse_db_utc(value).astimezone(LOCAL_TZ)


def weekday_label(dt: datetime) -> str:
    return dt.strftime('%a')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=8, help='Number of upcoming posts to show')
    args = parser.parse_args()

    conn = get_db()
    rows = conn.execute("""
        SELECT id, source_slug, scheduled_at, status
        FROM social_posts
        WHERE platform='linkedin'
          AND status IN ('approved', 'posted')
        ORDER BY scheduled_at ASC, id ASC
    """).fetchall()
    conn.close()

    if not rows:
        print("No LinkedIn posts found.")
        return

    now_utc = datetime.now(timezone.utc)
    local_days = [local_dt(r['scheduled_at']).date().isoformat() for r in rows]
    day_counts = Counter(local_days)

    violations = []
    due_now = []
    upcoming = []

    for row in rows:
        dt_local = local_dt(row['scheduled_at'])
        issues = []
        if dt_local.weekday() not in ALLOWED_WEEKDAYS:
            issues.append('non-Mon-Thu')
        if day_counts[dt_local.date().isoformat()] > 1:
            issues.append('duplicate-day')

        if row['status'] == 'approved' and parse_db_utc(row['scheduled_at']) <= now_utc:
            due_now.append((row, dt_local, issues))
        if row['status'] == 'approved' and parse_db_utc(row['scheduled_at']) >= now_utc:
            upcoming.append((row, dt_local, issues))
        if issues:
            violations.append((row, dt_local, issues))

    print()
    print("LINKEDIN SCHEDULE AUDIT")
    print("━" * 52)
    print(f"  Local timezone: {LOCAL_TZ.key}")
    print(f"  Allowed days:   Mon Tue Wed Thu")
    print(f"  Queue:          {sum(r['status'] == 'approved' for r in rows)} approved / {sum(r['status'] == 'posted' for r in rows)} posted")
    print(f"  Compliance:     {'OK' if not violations else 'FAIL'}")

    if due_now:
        print()
        print(f"  Due now: {len(due_now)} approved post(s)")
        for row, dt_local, issues in due_now:
            suffix = f"  [{', '.join(issues)}]" if issues else ''
            print(f"    {dt_local.strftime('%Y-%m-%d %a %I:%M%p')}  {row['source_slug']}{suffix}")

    print()
    print(f"  Next {min(args.limit, len(upcoming))} upcoming:")
    for row, dt_local, issues in upcoming[:args.limit]:
        suffix = f"  [{', '.join(issues)}]" if issues else ''
        print(f"    {dt_local.strftime('%Y-%m-%d %a %I:%M%p')}  {row['source_slug']}{suffix}")

    if violations:
        print()
        print(f"  Violations: {len(violations)}")
        for row, dt_local, issues in violations:
            print(f"    ID {row['id']}  {dt_local.strftime('%Y-%m-%d %a %I:%M%p')}  {row['source_slug']}  -> {', '.join(issues)}")

    duplicate_days = [(day, count) for day, count in sorted(day_counts.items()) if count > 1]
    if duplicate_days:
        print()
        print("  Duplicate local days:")
        for day, count in duplicate_days:
            print(f"    {day}  {count} posts")

    print("━" * 52)
    print()

    if violations:
        sys.exit(2)


if __name__ == '__main__':
    main()
