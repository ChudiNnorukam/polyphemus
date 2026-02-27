#!/usr/bin/env python3
"""Social Resolve — Print social media pipeline stats.

Usage:
    python3 scripts/social_resolve.py
"""

import os
import sqlite3
import sys

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
    if not os.path.exists(DB_PATH):
        print(f"DB not found: {DB_PATH}. Run init_db.py + funnel_db_init.py extend first.")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def main():
    conn = get_db()
    c = conn.cursor()

    if not table_exists(conn, 'social_posts'):
        print("social_posts table not found. Run: python3 scripts/funnel_db_init.py extend")
        conn.close()
        return

    # Queue status
    pending = c.execute("SELECT COUNT(*) FROM social_posts WHERE status='pending'").fetchone()[0]
    posted = c.execute("SELECT COUNT(*) FROM social_posts WHERE status='posted'").fetchone()[0]
    failed = c.execute("SELECT COUNT(*) FROM social_posts WHERE status='failed'").fetchone()[0]

    # Per-platform breakdown
    li_posted = c.execute(
        "SELECT COUNT(*) FROM social_posts WHERE platform='linkedin' AND status='posted'"
    ).fetchone()[0]
    tw_posted = c.execute(
        "SELECT COUNT(*) FROM social_posts WHERE platform='twitter' AND status='posted'"
    ).fetchone()[0]
    pt_posted = c.execute(
        "SELECT COUNT(*) FROM social_posts WHERE platform='pinterest' AND status='posted'"
    ).fetchone()[0]

    # This week
    this_week = c.execute(
        "SELECT COUNT(*) FROM social_posts WHERE status='posted' AND posted_at >= datetime('now', '-7 days')"
    ).fetchone()[0]

    # Top slug by post count
    top_slug_row = c.execute("""
        SELECT source_slug, COUNT(*) as cnt
        FROM social_posts
        GROUP BY source_slug
        ORDER BY cnt DESC
        LIMIT 1
    """).fetchone()
    top_slug = f"{top_slug_row['source_slug']} ({top_slug_row['cnt']} posts)" if top_slug_row else 'none'

    # Pinterest aggregate analytics
    pt_impressions = c.execute(
        "SELECT SUM(impressions) FROM social_posts WHERE platform='pinterest'"
    ).fetchone()[0] or 0
    pt_clicks = c.execute(
        "SELECT SUM(clicks) FROM social_posts WHERE platform='pinterest'"
    ).fetchone()[0] or 0

    # Recently posted
    recent = c.execute("""
        SELECT source_slug, platform, posted_at
        FROM social_posts
        WHERE status='posted'
        ORDER BY posted_at DESC
        LIMIT 5
    """).fetchall()

    conn.close()

    print()
    print("SOCIAL MEDIA - RESOLVE")
    print("━" * 42)
    print(f"  Queue:     pending {pending} / posted {posted} / failed {failed}")
    print()
    print("  Platforms:")
    print(f"    LinkedIn:  {li_posted} posted")
    print(f"    Twitter:   {tw_posted} posted")
    print(f"    Pinterest: {pt_posted} posted")
    print()
    print(f"  This week: {this_week} posts published")
    print(f"  Top slug:  {top_slug}")
    print()
    print("  Pinterest analytics (all-time):")
    print(f"    Impressions: {pt_impressions:,}")
    print(f"    Clicks:      {pt_clicks:,}")

    if recent:
        print()
        print("  Recently posted:")
        for r in recent:
            posted_at = r['posted_at'][:10] if r['posted_at'] else '?'
            print(f"    {posted_at}  [{r['platform']:<12}] {r['source_slug']}")

    print("━" * 42)
    print()


if __name__ == '__main__':
    main()
