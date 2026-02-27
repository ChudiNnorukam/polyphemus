#!/usr/bin/env python3
"""Token Manager — Check OAuth token expiry and refresh where possible.

Checks LinkedIn (60-day token), Pinterest (30-day access / 365-day refresh).
Twitter OAuth 1.0a has no expiry. Alerts via Slack when tokens are near expiry.

Usage:
    python3 scripts/token_manager.py --check              # Check all token statuses
    python3 scripts/token_manager.py --refresh-pinterest  # Attempt Pinterest token refresh
"""

import argparse
import os
import re
import sys
from datetime import datetime, timezone

import requests

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
LINKEDIN_ACCESS_TOKEN = os.environ.get('LINKEDIN_ACCESS_TOKEN', '')
LINKEDIN_TOKEN_EXPIRES_AT = os.environ.get('LINKEDIN_TOKEN_EXPIRES_AT', '')
PINTEREST_ACCESS_TOKEN = os.environ.get('PINTEREST_ACCESS_TOKEN', '')
PINTEREST_REFRESH_TOKEN = os.environ.get('PINTEREST_REFRESH_TOKEN', '')
SLACK_BOT_TOKEN = os.environ.get('SLACK_BOT_TOKEN', '')
SLACK_CHANNEL_ID = os.environ.get('SLACK_CHANNEL_ID', '')


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


def check_linkedin() -> tuple[str, str]:
    """Returns (status_label, days_remaining_str)."""
    if not LINKEDIN_TOKEN_EXPIRES_AT:
        return 'UNKNOWN', 'LINKEDIN_TOKEN_EXPIRES_AT not set'

    try:
        # Accept ISO8601 date or datetime
        expires_str = LINKEDIN_TOKEN_EXPIRES_AT.split('T')[0]
        expires = datetime.strptime(expires_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    except ValueError:
        return 'PARSE_ERROR', f'Cannot parse: {LINKEDIN_TOKEN_EXPIRES_AT}'

    now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    days_remaining = (expires - now).days

    if days_remaining <= 0:
        send_slack(
            f":rotating_light: CRITICAL: LinkedIn OAuth token EXPIRED. Posting is broken. "
            f"Refresh at developers.linkedin.com"
        )
        return 'EXPIRED', str(days_remaining)
    elif days_remaining <= 10:
        send_slack(
            f":warning: URGENT: LinkedIn OAuth token expires in {days_remaining} days. "
            f"Refresh at developers.linkedin.com"
        )
        return 'EXPIRING_SOON', str(days_remaining)

    return 'OK', str(days_remaining)


def check_pinterest() -> tuple[str, str]:
    """Test Pinterest access token. Returns (status, detail)."""
    if not PINTEREST_ACCESS_TOKEN:
        return 'NOT_SET', 'PINTEREST_ACCESS_TOKEN not set'

    try:
        r = requests.get(
            'https://api.pinterest.com/v5/user_account',
            headers={'Authorization': f'Bearer {PINTEREST_ACCESS_TOKEN}'},
            timeout=10
        )
        if r.status_code == 200:
            username = r.json().get('username', 'unknown')
            return 'OK', f'Authenticated as @{username}'
        elif r.status_code == 401:
            return 'EXPIRED', '401 Unauthorized'
        else:
            return 'ERROR', f'HTTP {r.status_code}'
    except Exception as e:
        return 'ERROR', str(e)


def refresh_pinterest_token() -> bool:
    """Attempt to refresh Pinterest access token using refresh token."""
    if not PINTEREST_REFRESH_TOKEN:
        print("PINTEREST_REFRESH_TOKEN not set.")
        return False

    # Pinterest token refresh requires client_id + client_secret (app credentials)
    # which are not stored in .env by default. Show instructions instead.
    print("Pinterest token refresh requires client_id + client_secret.")
    print("Steps:")
    print("  1. Go to developers.pinterest.com > Your Apps")
    print("  2. POST https://api.pinterest.com/v5/oauth/token")
    print("     grant_type=refresh_token&refresh_token=PINTEREST_REFRESH_TOKEN")
    print("     with Basic auth: client_id:client_secret")
    print("  3. Update PINTEREST_ACCESS_TOKEN in .env with new token")
    print("  4. Update PINTEREST_REFRESH_TOKEN if a new one is returned")

    # If refresh token exchange succeeds here (manual invocation with creds):
    client_id = os.environ.get('PINTEREST_CLIENT_ID', '')
    client_secret = os.environ.get('PINTEREST_CLIENT_SECRET', '')
    if not client_id or not client_secret:
        print("\nTo auto-refresh, set PINTEREST_CLIENT_ID and PINTEREST_CLIENT_SECRET in .env.")
        return False

    try:
        r = requests.post(
            'https://api.pinterest.com/v5/oauth/token',
            data={
                'grant_type': 'refresh_token',
                'refresh_token': PINTEREST_REFRESH_TOKEN,
            },
            auth=(client_id, client_secret),
            timeout=15
        )
        r.raise_for_status()
        data = r.json()
        new_access = data.get('access_token')
        new_refresh = data.get('refresh_token', PINTEREST_REFRESH_TOKEN)

        if not new_access:
            print(f"Refresh failed: {data}")
            return False

        # Write new tokens back to .env file
        env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
        if os.path.exists(env_path):
            with open(env_path) as f:
                content = f.read()
            content = re.sub(r'PINTEREST_ACCESS_TOKEN=.*', f'PINTEREST_ACCESS_TOKEN={new_access}', content)
            content = re.sub(r'PINTEREST_REFRESH_TOKEN=.*', f'PINTEREST_REFRESH_TOKEN={new_refresh}', content)
            with open(env_path, 'w') as f:
                f.write(content)
            print(f"Updated .env with new Pinterest tokens.")
            send_slack(":white_check_mark: Pinterest access token refreshed successfully.")
            return True

    except Exception as e:
        print(f"Pinterest refresh failed: {e}")
        return False

    return False


def check_twitter() -> tuple[str, str]:
    """Twitter OAuth 1.0a — no expiry."""
    has_creds = all([
        os.environ.get('TWITTER_API_KEY'),
        os.environ.get('TWITTER_API_SECRET'),
        os.environ.get('TWITTER_ACCESS_TOKEN'),
        os.environ.get('TWITTER_ACCESS_SECRET'),
    ])
    if has_creds:
        return 'OK', 'OAuth 1.0a (no expiry)'
    return 'NOT_SET', 'One or more Twitter credentials missing'


def cmd_check(args):
    print("\nOAuth Token Status")
    print("━" * 48)

    li_status, li_detail = check_linkedin()
    pi_status, pi_detail = check_pinterest()
    tw_status, tw_detail = check_twitter()

    rows = [
        ('LinkedIn',  li_status, li_detail, '60-day expiry'),
        ('Twitter',   tw_status, tw_detail, 'No expiry'),
        ('Pinterest', pi_status, pi_detail, '30-day access token'),
    ]

    for name, status, detail, note in rows:
        indicator = ':white_check_mark:' if status == 'OK' else ':warning:' if 'EXPIR' in status else ':x:'
        print(f"  {name:<12} [{status:<14}]  {detail}  ({note})")

    print("━" * 48)
    print()

    if pi_status == 'EXPIRED':
        print("Pinterest token expired. Run: python3 scripts/token_manager.py --refresh-pinterest")


def cmd_refresh_pinterest(args):
    print("Attempting Pinterest token refresh...")
    success = refresh_pinterest_token()
    if not success:
        print("Refresh incomplete. See instructions above.")
        sys.exit(1)


COMMANDS = {
    'check': cmd_check,
    'refresh_pinterest': cmd_refresh_pinterest,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='Check all token statuses')
    parser.add_argument('--refresh-pinterest', action='store_true', help='Attempt Pinterest token refresh')
    args = parser.parse_args()

    if args.refresh_pinterest:
        cmd_refresh_pinterest(args)
    else:
        cmd_check(args)


if __name__ == '__main__':
    main()
