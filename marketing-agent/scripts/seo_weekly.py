#!/usr/bin/env python3
"""SEO Weekly - Run all SEO/AEO trackers and print a combined report.

Usage:
    python3 scripts/seo_weekly.py [--dry-run]

Cron (every Sunday 6am PT = 14:00 UTC):
    0 14 * * 0 cd /path/to/marketing-agent && python3 scripts/seo_weekly.py >> logs/seo_weekly.log 2>&1
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone


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


def table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row[0] > 0


def print_section(title: str):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print('=' * 60)


def run_top_opportunities(conn):
    """Print top actions for the week: expand opportunities + unowned answer boxes."""
    print_section("TOP ACTIONS THIS WEEK")

    actions = []

    # SERP: queries where we are in position 11-30 (expand opportunity)
    if table_exists(conn, 'serp_snapshots'):
        rows = conn.execute("""
            SELECT query, our_position, action
            FROM serp_snapshots
            WHERE checked_at = (SELECT MAX(checked_at) FROM serp_snapshots)
              AND our_position BETWEEN 11 AND 30
            ORDER BY our_position ASC
            LIMIT 10
        """).fetchall()
        for r in rows:
            actions.append({
                'priority': 1,
                'type': 'EXPAND',
                'query': r['query'],
                'detail': f"Pos {r['our_position']} - push to top 10",
            })

    # AEO: answer box exists but we do not own it
    if table_exists(conn, 'aeo_snapshots'):
        rows = conn.execute("""
            SELECT query, answer_box_type, answer_box_source_domain, our_organic_position
            FROM aeo_snapshots
            WHERE checked_at = (SELECT MAX(checked_at) FROM aeo_snapshots)
              AND has_answer_box = 1
              AND we_own_answer_box = 0
            ORDER BY our_organic_position ASC NULLS LAST
            LIMIT 10
        """).fetchall()
        for r in rows:
            org = f"org pos {r['our_organic_position']}" if r['our_organic_position'] else 'not ranking'
            actions.append({
                'priority': 2,
                'type': 'WIN-ANSWER-BOX',
                'query': r['query'],
                'detail': f"Owned by {r['answer_box_source_domain'] or 'unknown'}, {org}",
            })

    # SERP: WRITE actions (not ranking at all)
    if table_exists(conn, 'serp_snapshots'):
        rows = conn.execute("""
            SELECT query, action
            FROM serp_snapshots
            WHERE checked_at = (SELECT MAX(checked_at) FROM serp_snapshots)
              AND action LIKE 'WRITE%'
            ORDER BY query ASC
            LIMIT 5
        """).fetchall()
        for r in rows:
            actions.append({
                'priority': 3,
                'type': 'WRITE',
                'query': r['query'],
                'detail': 'Not ranking - create content',
            })

    if not actions:
        print("No data yet. Run gsc_tracker, serp_tracker, and aeo_monitor first.")
        return

    # Sort by priority then print
    actions.sort(key=lambda x: x['priority'])
    print(f"\n{'#':<4} {'TYPE':<20} {'QUERY':<42} DETAIL")
    print('-' * 100)
    for i, a in enumerate(actions, 1):
        print(f"{i:<4} {a['type']:<20} {a['query'][:41]:<42} {a['detail']}")


def run_gsc_summary(conn):
    """Print GSC snapshot summary."""
    print_section("GSC - Search Console Summary")
    if not table_exists(conn, 'gsc_snapshots'):
        print("No GSC data yet. Run: python3 scripts/gsc_tracker.py")
        return

    rows = conn.execute("""
        SELECT site, week_start, total_clicks, total_impressions, avg_position,
               queries_with_impressions, top_query
        FROM gsc_snapshots
        WHERE pulled_at = (SELECT MAX(pulled_at) FROM gsc_snapshots)
        ORDER BY site
    """).fetchall()

    if not rows:
        print("No snapshots found.")
        return

    for r in rows:
        print(f"\nSite: {r['site']}")
        print(f"  Week starting:          {r['week_start']}")
        print(f"  Total clicks:           {r['total_clicks']}")
        print(f"  Total impressions:      {r['total_impressions']}")
        print(f"  Avg position:           {r['avg_position']:.1f}")
        print(f"  Queries with impressions: {r['queries_with_impressions']}")
        print(f"  Top query:              {r['top_query']}")


def run_serp_summary(conn):
    """Print SERP ranking summary."""
    print_section("SERP - Ranking Summary")
    if not table_exists(conn, 'serp_snapshots'):
        print("No SERP data yet. Run: python3 scripts/serp_tracker.py")
        return

    rows = conn.execute("""
        SELECT query, our_position, has_featured_snippet, has_paa, action
        FROM serp_snapshots
        WHERE checked_at = (SELECT MAX(checked_at) FROM serp_snapshots)
        ORDER BY our_position ASC NULLS LAST
        LIMIT 15
    """).fetchall()

    if not rows:
        print("No SERP snapshots found.")
        return

    print(f"\n{'QUERY':<45} {'POS':>5} {'FEAT':>5} {'ACTION'}")
    print('-' * 80)
    for r in rows:
        pos = str(r['our_position']) if r['our_position'] else '-'
        feat = 'yes' if r['has_featured_snippet'] else 'no'
        print(f"{r['query'][:44]:<45} {pos:>5} {feat:>5} {r['action'] or ''}")


def run_aeo_summary(conn):
    """Print AEO presence summary."""
    print_section("AEO - Answer Engine Summary")
    if not table_exists(conn, 'aeo_snapshots'):
        print("No AEO data yet. Run: python3 scripts/aeo_monitor.py")
        return

    rows = conn.execute("""
        SELECT query, has_answer_box, answer_box_source_domain, we_own_answer_box,
               paa_count, our_organic_position
        FROM aeo_snapshots
        WHERE checked_at = (SELECT MAX(checked_at) FROM aeo_snapshots)
        ORDER BY has_answer_box DESC, our_organic_position ASC NULLS LAST
        LIMIT 15
    """).fetchall()

    if not rows:
        print("No AEO snapshots found.")
        return

    owned = sum(1 for r in rows if r['we_own_answer_box'])
    has_ab = sum(1 for r in rows if r['has_answer_box'])
    print(f"\nAnswer boxes: {has_ab} found, {owned} owned by us.")

    print(f"\n{'QUERY':<42} {'AB':>4} {'OWNED':>6} {'PAA':>4} {'ORG':>5}")
    print('-' * 70)
    for r in rows:
        ab = 'yes' if r['has_answer_box'] else 'no'
        owned_str = 'yes' if r['we_own_answer_box'] else 'no'
        org = str(r['our_organic_position']) if r['our_organic_position'] else '-'
        print(f"{r['query'][:41]:<42} {ab:>4} {owned_str:>6} {r['paa_count']:>4} {org:>5}")


def main():
    parser = argparse.ArgumentParser(description='Run all SEO/AEO trackers and print combined report.')
    parser.add_argument('--dry-run', action='store_true', help='Pass --dry-run to all sub-trackers')
    args = parser.parse_args()

    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    print(f"\nSEO Weekly Report - {now}")
    print("Running all trackers...")

    # Dynamically import to avoid circular issues and so each can be run standalone
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    run_errors = []

    # Run GSC tracker
    print("\n[1/3] Running gsc_tracker...")
    try:
        import gsc_tracker
        # Simulate argparse for main() by patching sys.argv
        old_argv = sys.argv
        sys.argv = ['gsc_tracker.py'] + (['--dry-run'] if args.dry_run else [])
        try:
            gsc_tracker.main()
        except SystemExit as e:
            if e.code != 0:
                run_errors.append(f"gsc_tracker exited with code {e.code}")
        finally:
            sys.argv = old_argv
    except Exception as e:
        run_errors.append(f"gsc_tracker error: {e}")
        print(f"  WARNING: gsc_tracker failed: {e}")

    # Run SERP tracker
    print("\n[2/3] Running serp_tracker...")
    try:
        import serp_tracker
        old_argv = sys.argv
        sys.argv = ['serp_tracker.py'] + (['--dry-run'] if args.dry_run else [])
        try:
            serp_tracker.main()
        except SystemExit as e:
            if e.code != 0:
                run_errors.append(f"serp_tracker exited with code {e.code}")
        finally:
            sys.argv = old_argv
    except Exception as e:
        run_errors.append(f"serp_tracker error: {e}")
        print(f"  WARNING: serp_tracker failed: {e}")

    # Run AEO monitor
    print("\n[3/3] Running aeo_monitor...")
    try:
        import aeo_monitor
        old_argv = sys.argv
        sys.argv = ['aeo_monitor.py'] + (['--dry-run'] if args.dry_run else [])
        try:
            aeo_monitor.main()
        except SystemExit as e:
            if e.code != 0:
                run_errors.append(f"aeo_monitor exited with code {e.code}")
        finally:
            sys.argv = old_argv
    except Exception as e:
        run_errors.append(f"aeo_monitor error: {e}")
        print(f"  WARNING: aeo_monitor failed: {e}")

    # Print combined summaries from DB
    conn = get_db()
    try:
        run_gsc_summary(conn)
        run_serp_summary(conn)
        run_aeo_summary(conn)
        run_top_opportunities(conn)
    finally:
        conn.close()

    print(f"\n{'=' * 60}")
    if run_errors:
        print(f"Completed with {len(run_errors)} warning(s):")
        for err in run_errors:
            print(f"  - {err}")
    else:
        print("All trackers completed successfully.")
    print(f"{'=' * 60}\n")


if __name__ == '__main__':
    main()
