#!/usr/bin/env python3
"""Publisher — Post queued social content to LinkedIn, Twitter, and Pinterest.

Run every 30 minutes via cron. Pulls pending posts whose scheduled_at has passed.

Usage:
    python3 scripts/publisher.py [--dry-run]
"""

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

import requests

try:
    import tweepy
    TWEEPY_AVAILABLE = True
except ImportError:
    TWEEPY_AVAILABLE = False

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
LINKEDIN_ACCESS_TOKEN = os.environ.get('LINKEDIN_ACCESS_TOKEN', '')
LINKEDIN_PERSON_URN = os.environ.get('LINKEDIN_PERSON_URN', '')
TWITTER_API_KEY = os.environ.get('TWITTER_API_KEY', '')
TWITTER_API_SECRET = os.environ.get('TWITTER_API_SECRET', '')
TWITTER_ACCESS_TOKEN = os.environ.get('TWITTER_ACCESS_TOKEN', '')
TWITTER_ACCESS_SECRET = os.environ.get('TWITTER_ACCESS_SECRET', '')
PINTEREST_ACCESS_TOKEN = os.environ.get('PINTEREST_ACCESS_TOKEN', '')
PINTEREST_BOARD_ID = os.environ.get('PINTEREST_BOARD_ID', '')
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


def build_utm_url(slug: str, platform: str = 'linkedin') -> str:
    """Build a blog URL with UTM tracking parameters."""
    # Extract the blog slug from the source_slug (e.g., 'w1-mon-i-deployed-4-000' -> use as campaign)
    params = {
        'utm_source': platform,
        'utm_medium': 'social',
        'utm_campaign': slug,
        'utm_content': 'post',
    }
    query = '&'.join(f'{k}={v}' for k, v in params.items())
    return f'https://chudi.dev/blog/{slug}?{query}'


def comment_linkedin(post_urn: str, comment_text: str) -> str | None:
    """Post a comment on a LinkedIn post. Returns comment URN or None."""
    if not LINKEDIN_ACCESS_TOKEN or not LINKEDIN_PERSON_URN:
        return None

    payload = {
        'actor': LINKEDIN_PERSON_URN,
        'message': {
            'text': comment_text,
        },
    }

    r = requests.post(
        f'https://api.linkedin.com/rest/socialActions/{post_urn}/comments',
        json=payload,
        headers={
            'Authorization': f'Bearer {LINKEDIN_ACCESS_TOKEN}',
            'LinkedIn-Version': '202602',
            'Content-Type': 'application/json',
        },
        timeout=15
    )
    if r.ok:
        return r.headers.get('X-RestLi-Id', 'commented')
    else:
        print(f"    Comment failed: {r.status_code} {r.text[:200]}")
        return None


def post_linkedin(content: str, image_url: str) -> str:
    """Post to LinkedIn. Returns platform_post_id."""
    if not LINKEDIN_ACCESS_TOKEN or not LINKEDIN_PERSON_URN:
        raise ValueError("LINKEDIN_ACCESS_TOKEN or LINKEDIN_PERSON_URN not set")

    payload = {
        'author': LINKEDIN_PERSON_URN,
        'commentary': content,
        'visibility': 'PUBLIC',
        'lifecycleState': 'PUBLISHED',
        'distribution': {
            'feedDistribution': 'MAIN_FEED',
            'targetEntities': [],
            'thirdPartyDistributionChannels': [],
        },
    }

    r = requests.post(
        'https://api.linkedin.com/rest/posts',
        json=payload,
        headers={
            'Authorization': f'Bearer {LINKEDIN_ACCESS_TOKEN}',
            'LinkedIn-Version': '202602',
            'Content-Type': 'application/json',
        },
        timeout=15
    )
    r.raise_for_status()
    # LinkedIn returns the post URN in the X-RestLi-Id header
    post_id = r.headers.get('X-RestLi-Id', r.headers.get('x-restli-id', 'unknown'))
    return post_id


def post_twitter(content: str) -> str:
    """Post tweet or thread. Returns tweet ID of first tweet."""
    if not TWEEPY_AVAILABLE:
        raise ImportError("tweepy not installed. Run: pip install tweepy")
    if not all([TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET]):
        raise ValueError("Twitter credentials not fully set")

    client = tweepy.Client(
        consumer_key=TWITTER_API_KEY,
        consumer_secret=TWITTER_API_SECRET,
        access_token=TWITTER_ACCESS_TOKEN,
        access_token_secret=TWITTER_ACCESS_SECRET,
    )

    # Check if content is a JSON array (thread)
    try:
        tweets = json.loads(content)
        if not isinstance(tweets, list):
            tweets = [content]
    except (json.JSONDecodeError, TypeError):
        tweets = [content]

    first_id = None
    reply_to = None
    for tweet_text in tweets:
        kwargs = {'text': tweet_text[:280]}
        if reply_to:
            kwargs['in_reply_to_tweet_id'] = reply_to
        response = client.create_tweet(**kwargs)
        tweet_id = response.data['id']
        if first_id is None:
            first_id = tweet_id
        reply_to = tweet_id
        time.sleep(1)  # Rate limit buffer

    return str(first_id)


def post_pinterest(content: str, image_url: str, slug: str) -> str:
    """Post to Pinterest. Returns pin ID."""
    if not PINTEREST_ACCESS_TOKEN or not PINTEREST_BOARD_ID:
        raise ValueError("PINTEREST_ACCESS_TOKEN or PINTEREST_BOARD_ID not set")

    # content format from repurpose.py: line 1 = title, line 2 = description
    lines = content.split('\n', 1)
    pin_title = lines[0].strip()[:100]
    pin_description = lines[1].strip() if len(lines) > 1 else pin_title

    payload = {
        'board_id': PINTEREST_BOARD_ID,
        'title': pin_title,
        'description': pin_description,
        'link': f'https://chudi.dev/blog/{slug}',
    }
    if image_url:
        payload['media_source'] = {
            'source_type': 'image_url',
            'url': image_url,
        }

    r = requests.post(
        'https://api.pinterest.com/v5/pins',
        json=payload,
        headers={
            'Authorization': f'Bearer {PINTEREST_ACCESS_TOKEN}',
            'Content-Type': 'application/json',
        },
        timeout=15
    )
    r.raise_for_status()
    return r.json().get('id', 'unknown')


def dispatch_post(post: dict) -> tuple[str | None, str | None]:
    """Dispatch a single post. Returns (platform_post_id, error_msg)."""
    platform = post['platform']
    content = post['content']
    image_url = post['image_url'] or ''
    slug = post['source_slug']

    if DRY_RUN:
        print(f"  [DRY RUN] Would post {platform}: {slug} — {content[:60]}...")
        return 'dry-run-id', None

    try:
        if platform == 'linkedin':
            pid = post_linkedin(content, image_url)
        elif platform == 'twitter':
            pid = post_twitter(content)
        elif platform == 'pinterest':
            pid = post_pinterest(content, image_url, slug)
        else:
            return None, f"Unknown platform: {platform}"
        return pid, None
    except Exception as e:
        return None, str(e)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    if args.dry_run:
        os.environ['DRY_RUN'] = 'true'
        global DRY_RUN
        DRY_RUN = True

    conn = get_db()

    due_posts = conn.execute("""
        SELECT * FROM social_posts
        WHERE status = 'approved'
          AND scheduled_at <= datetime('now')
        ORDER BY scheduled_at ASC
        LIMIT 10
    """).fetchall()

    if not due_posts:
        print("No posts due.")
        conn.close()
        return

    print(f"{len(due_posts)} post(s) due to publish.")

    published_slugs = []
    failed = 0
    now = datetime.now(timezone.utc).isoformat()

    for post in due_posts:
        print(f"  Publishing [{post['platform']}] {post['source_slug']}")
        platform_post_id, error = dispatch_post(dict(post))

        if platform_post_id and not error:
            conn.execute("""
                UPDATE social_posts
                SET status='posted', platform_post_id=?, posted_at=?
                WHERE id=?
            """, (platform_post_id, now, post['id']))
            published_slugs.append(f"{post['platform']}:{post['source_slug']}")
            print(f"    Posted. ID: {platform_post_id}")

            # Auto-comment with UTM-tagged blog link for LinkedIn posts
            # that mention "in the comments" or are Thursday posts (link-in-comment day)
            if post['platform'] == 'linkedin' and not DRY_RUN:
                content_lower = post['content'].lower()
                has_comment_cta = 'in the comments' in content_lower or 'in comments' in content_lower
                if has_comment_cta:
                    # Extract a real blog slug from the source_slug
                    # Source slugs like 'w1-thu-one-file-replaced-3-produc' -> find matching blog post
                    blog_url = build_utm_url(post['source_slug'], 'linkedin')
                    comment_text = f"Full breakdown here: {blog_url}"
                    time.sleep(3)  # Brief delay before commenting
                    cid = comment_linkedin(platform_post_id, comment_text)
                    if cid:
                        print(f"    Commented with link: {blog_url}")
                    else:
                        print(f"    Comment failed (non-blocking)")
        else:
            conn.execute("""
                UPDATE social_posts
                SET status='failed', error=?
                WHERE id=?
            """, (error, post['id']))
            failed += 1
            print(f"    FAILED: {error}")
            send_slack(f":x: Social post failed [{post['platform']}] {post['source_slug']}: {error}")

    conn.commit()
    conn.close()

    if published_slugs:
        summary = ', '.join(published_slugs[:5])
        if len(published_slugs) > 5:
            summary += f' +{len(published_slugs)-5} more'
        send_slack(f":iphone: Posted: {len(published_slugs)} social posts - {summary}")

    mode = ' [DRY RUN]' if DRY_RUN else ''
    print(f"\nDone{mode}. Published: {len(published_slugs)}, Failed: {failed}")


if __name__ == '__main__':
    main()
