#!/usr/bin/env python3
"""Social Analytics — Pull Pinterest pin performance data and store in social_posts.

Run weekly (Monday). Fetches impressions, outbound clicks, and saves per pin.

Usage:
    python3 scripts/social_analytics.py [--days 7]
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

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
PINTEREST_ACCESS_TOKEN = os.environ.get('PINTEREST_ACCESS_TOKEN', '')
SLACK_BOT_TOKEN = os.environ.get('SLACK_BOT_TOKEN', '')
SLACK_CHANNEL_ID = os.environ.get('SLACK_CHANNEL_ID', '')


def get_db():
    if not os.path.exists(DB_PATH):
        print(f"DB not found: {DB_PATH}. Run init_db.py + funnel_db_init.py extend first.")
        sys.exit(1)
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


def fetch_pin_analytics(pin_id: str, start_date: str, end_date: str) -> dict:
    """Fetch analytics for one Pinterest pin. Returns impressions/clicks/saves."""
    if not PINTEREST_ACCESS_TOKEN:
        raise ValueError("PINTEREST_ACCESS_TOKEN not set")

    r = requests.get(
        f'https://api.pinterest.com/v5/pins/{pin_id}/analytics',
        params={
            'start_date': start_date,
            'end_date': end_date,
            'metric_types': 'IMPRESSION,OUTBOUND_CLICK,SAVE',
        },
        headers={
            'Authorization': f'Bearer {PINTEREST_ACCESS_TOKEN}',
        },
        timeout=15
    )
    r.raise_for_status()
    data = r.json()

    # Pinterest analytics structure: {"all": {"daily_metrics": [...], "summary_metrics": {...}}}
    summary = data.get('all', {}).get('summary_metrics', {})
    return {
        'impressions': summary.get('IMPRESSION', 0),
        'clicks': summary.get('OUTBOUND_CLICK', 0),
        'saves': summary.get('SAVE', 0),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=7, help='Analytics window in days')
    args = parser.parse_args()

    conn = get_db()

    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=args.days)
    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')

    # Find recently posted Pinterest pins
    pins = conn.execute("""
        SELECT id, source_slug, platform_post_id, posted_at
        FROM social_posts
        WHERE status = 'posted'
          AND platform = 'pinterest'
          AND platform_post_id IS NOT NULL
          AND posted_at >= datetime('now', ? || ' days')
        ORDER BY posted_at DESC
    """, (f'-{args.days}',)).fetchall()

    if not pins:
        print(f"No Pinterest pins posted in the last {args.days} days.")
        conn.close()
        return

    print(f"\nFetching Pinterest analytics ({start_str} to {end_str})")
    print(f"Pins to check: {len(pins)}")
    print()

    if not PINTEREST_ACCESS_TOKEN:
        print("PINTEREST_ACCESS_TOKEN not set. Cannot fetch analytics.")
        conn.close()
        return

    total_impressions = 0
    total_clicks = 0
    total_saves = 0
    updated = 0

    print(f"{'Slug':<30} {'Impressions':>12} {'Clicks':>8} {'Saves':>8}")
    print("-" * 62)

    for pin in pins:
        pin_id = pin['platform_post_id']
        slug = pin['source_slug']

        if DRY_RUN:
            print(f"  [DRY RUN] Would fetch analytics for pin {pin_id} ({slug})")
            continue

        try:
            stats = fetch_pin_analytics(pin_id, start_str, end_str)
            imps = stats['impressions']
            clicks = stats['clicks']
            saves = stats['saves']

            conn.execute("""
                UPDATE social_posts
                SET impressions=?, clicks=?, saves=?
                WHERE id=?
            """, (imps, clicks, saves, pin['id']))

            total_impressions += imps
            total_clicks += clicks
            total_saves += saves
            updated += 1

            print(f"  {slug:<28} {imps:>12,} {clicks:>8,} {saves:>8,}")

        except Exception as e:
            print(f"  {slug:<28} ERROR: {e}")

    if not DRY_RUN:
        conn.commit()

    conn.close()

    print("-" * 62)
    print(f"  {'TOTAL':<28} {total_impressions:>12,} {total_clicks:>8,} {total_saves:>8,}")
    print()

    if updated:
        send_slack(
            f":pushpin: Pinterest ({args.days}d): {total_impressions:,} impressions, "
            f"{total_clicks:,} clicks across {updated} pins"
        )
        print(f"Updated analytics for {updated} pin(s).")

    mode = ' [DRY RUN]' if DRY_RUN else ''
    print(f"Done{mode}.")


if __name__ == '__main__':
    main()
