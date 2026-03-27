#!/usr/bin/env python3
"""LinkedIn Comment Reply Automation — Autonomous engagement agent.

Monitors recent LinkedIn posts for new comments and replies within 2 hours using
Chudi's voice (grace, curiosity, warmth). Each reply restarts the post in the
commenter's network, creating a 2-3x impression multiplier.

Usage:
    python3 scripts/linkedin_engager.py [--dry-run] [--hours N]

Examples:
    # Dry-run mode (review generated replies before posting)
    python3 scripts/linkedin_engager.py --dry-run

    # Monitor posts from last 72 hours (default: 48)
    python3 scripts/linkedin_engager.py --hours 72

    # Post replies live (after reviewing dry-run output)
    python3 scripts/linkedin_engager.py
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

import anthropic
import requests

try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'tools'))
    from api_telemetry import log_usage as _log_usage
except ImportError:
    _log_usage = None

DB_PATH = os.environ.get(
    'LEADS_DB_PATH',
    os.path.join(os.path.dirname(__file__), '..', 'data', 'marketing_leads.db')
)
DRY_RUN = os.environ.get('DRY_RUN', 'false').lower() == 'true'

# Maximum replies per run to avoid rate limit issues
MAX_REPLIES_PER_RUN = 10


def _load_env():
    """Load environment variables from .env files in priority order."""
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
LINKEDIN_ENGAGER_ENABLED = os.environ.get('LINKEDIN_ENGAGER_ENABLED', 'true').lower() == 'true'
LINKEDIN_ACCESS_TOKEN = os.environ.get('LINKEDIN_ACCESS_TOKEN', '')
LINKEDIN_PERSON_URN = os.environ.get('LINKEDIN_PERSON_URN', '')
SLACK_BOT_TOKEN = os.environ.get('SLACK_BOT_TOKEN', '')
SLACK_CHANNEL_ID = os.environ.get('SLACK_CHANNEL_ID', '')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')


def _load_voice_config() -> dict:
    """Load LinkedIn voice config from canonical JSON (source: voice.ts)."""
    voice_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'voice-linkedin.json')
    if os.path.exists(voice_path):
        with open(voice_path) as f:
            return json.load(f)
    print("[WARN] voice-linkedin.json not found, using fallback voice rules")
    return {}


def _build_reply_prompt(voice: dict) -> str:
    """Build REPLY_SYSTEM_PROMPT from voice-linkedin.json."""
    identity = voice.get('identity', {})
    name = identity.get('name', 'Chudi')
    posture = voice.get('posture', 'Mid-to-senior practitioner thinking in public.')
    audience = voice.get('audience_model', 'Peers figuring things out in parallel.')
    em_dash = voice.get('em_dash_rule', 'No em dashes in AI-generated content.')

    framing_use = voice.get('framing_use', [])
    framing_never = voice.get('framing_never', [])
    forbidden = voice.get('forbidden_phrases', [])
    comment_rules = voice.get('comment_rules', {})
    patterns = comment_rules.get('patterns', {})
    max_sentences = comment_rules.get('max_sentences', 3)
    max_exchanges = comment_rules.get('max_exchanges_per_thread', 2)
    fingerprints = voice.get('fingerprints', {})

    never_list = '\n'.join(f'- "{p}"' for p in framing_never[:6])
    forbidden_list = ', '.join(f'"{p}"' for p in forbidden[:10])
    fingerprint_list = '\n'.join(f'- {k.replace("_", " ").title()}: {v}' for k, v in list(fingerprints.items())[:5])

    return f"""You are {name}, replying to a comment on your LinkedIn post.

VOICE IDENTITY:
- {posture}
- Audience: {audience}
- {em_dash}

VOICE RULES (mandatory):
- Grace over grind: meet comments with curiosity, not machismo
- Self-compassion visible: "I sat with it" not "I pushed through"
- Warmth toward the reader
- Specificity over polish: real moments over curated narratives
- Humor and lightness welcome
- Keep replies 1-{max_sentences} sentences. Natural, not performative.
- NEVER use em dashes in your reply.

NEVER USE THESE PHRASES:
{never_list}

ALSO BANNED (AI detection triggers):
{forbidden_list}

VOICE FINGERPRINTS (use when natural):
{fingerprint_list}

PREFERRED FRAMINGS:
{chr(10).join(f'- "{p}"' for p in framing_use[:5])}

REPLY PATTERNS:
- Shared experience: {patterns.get('shared_experience', 'Acknowledge specifically, add perspective.')}
- Question: {patterns.get('question', 'Answer directly, ask one back.')}
- Agreement: {patterns.get('agreement', 'Thank warmly, add new insight.')}
- Disagreement: {patterns.get('disagreement', 'Fair point + genuine curiosity.')}
- Cheerleader: {patterns.get('cheerleader', 'Brief thanks, point to what is next.')}
- Max {max_exchanges} exchanges per thread.

POST CONTEXT:
{{post_content}}

COMMENTER:
Name: {{commenter_name}}
Comment: {{comment_text}}

Write a reply. No quotation marks around it. Just the text."""


_VOICE_CONFIG = _load_voice_config()
REPLY_SYSTEM_PROMPT = _build_reply_prompt(_VOICE_CONFIG)


def get_db():
    """Open database connection with row factory."""
    if not os.path.exists(DB_PATH):
        print(f"DB not found: {DB_PATH}. Run init_db.py + funnel_db_init.py extend first.")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def send_slack(msg: str):
    """Send message to Slack channel."""
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


def ensure_replies_table(conn):
    """Create linkedin_replies table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS linkedin_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_urn TEXT NOT NULL,
            comment_urn TEXT NOT NULL UNIQUE,
            commenter_name TEXT,
            comment_text TEXT,
            reply_text TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now')),
            posted_at TEXT,
            error TEXT
        )
    """)
    conn.commit()


def get_recent_posts(conn, hours: int = 48) -> list[str]:
    """Get post URNs from social_posts table posted in last N hours."""
    rows = conn.execute("""
        SELECT platform_post_id FROM social_posts
        WHERE platform = 'linkedin'
          AND status = 'posted'
          AND posted_at >= datetime('now', ?)
        ORDER BY posted_at DESC
    """, (f'-{hours} hours',)).fetchall()
    return [r['platform_post_id'] for r in rows if r['platform_post_id']]


def get_post_content(conn, post_urn: str) -> str:
    """Get original post content for context."""
    row = conn.execute("""
        SELECT content FROM social_posts
        WHERE platform_post_id = ? AND platform = 'linkedin'
        LIMIT 1
    """, (post_urn,)).fetchone()
    return row['content'] if row else "[Post content unavailable]"


def get_post_comments(post_urn: str) -> list[dict]:
    """Fetch comments on a LinkedIn post via REST API."""
    if not LINKEDIN_ACCESS_TOKEN:
        return []

    try:
        r = requests.get(
            f'https://api.linkedin.com/rest/socialActions/{post_urn}/comments',
            headers={
                'Authorization': f'Bearer {LINKEDIN_ACCESS_TOKEN}',
                'LinkedIn-Version': '202501',
            },
            params={'count': 50, 'start': 0},
            timeout=15
        )
        if r.ok:
            return r.json().get('elements', [])
        else:
            print(f"    API error: {r.status_code} {r.text[:200]}")
            return []
    except Exception as e:
        print(f"    Failed to fetch comments: {e}")
        return []


def generate_reply(post_content: str, commenter_name: str, comment_text: str) -> str:
    """Generate a voice-matched reply using Claude Haiku."""
    if not ANTHROPIC_API_KEY:
        return "[Reply generation requires ANTHROPIC_API_KEY]"

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=[{
                "type": "text",
                "text": REPLY_SYSTEM_PROMPT.format(
                    post_content=post_content,
                    commenter_name=commenter_name,
                    comment_text=comment_text
                ),
                "cache_control": {"type": "ephemeral"}
            }],
            messages=[{"role": "user", "content": "Write the reply."}]
        )
        if _log_usage:
            _log_usage("linkedin_engager", response)
        return response.content[0].text.strip()
    except Exception as e:
        print(f"    Reply generation failed: {e}")
        return f"[Error: {e}]"


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

    try:
        r = requests.post(
            f'https://api.linkedin.com/rest/socialActions/{post_urn}/comments',
            json=payload,
            headers={
                'Authorization': f'Bearer {LINKEDIN_ACCESS_TOKEN}',
                'LinkedIn-Version': '202501',
                'Content-Type': 'application/json',
            },
            timeout=15
        )
        if r.ok:
            return r.headers.get('X-RestLi-Id', 'commented')
        else:
            print(f"    Comment failed: {r.status_code} {r.text[:200]}")
            return None
    except Exception as e:
        print(f"    Comment request failed: {e}")
        return None


def main():
    """Poll recent posts for new comments, generate and post replies."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='Review replies without posting')
    parser.add_argument('--hours', type=int, default=48, help='Lookback window in hours (default: 48)')
    args = parser.parse_args()

    if args.dry_run:
        os.environ['DRY_RUN'] = 'true'
        global DRY_RUN
        DRY_RUN = True

    if not LINKEDIN_ENGAGER_ENABLED:
        print("LINKEDIN_ENGAGER_ENABLED is false. Exiting.")
        return

    if not LINKEDIN_ACCESS_TOKEN or not LINKEDIN_PERSON_URN:
        print("Missing LINKEDIN_ACCESS_TOKEN or LINKEDIN_PERSON_URN. Cannot proceed.")
        return

    conn = get_db()
    ensure_replies_table(conn)

    post_urns = get_recent_posts(conn, hours=args.hours)
    if not post_urns:
        print(f"No recent posts to monitor in last {args.hours} hours.")
        conn.close()
        return

    print(f"Monitoring {len(post_urns)} recent post(s) from last {args.hours} hours.")

    new_replies = 0
    for post_urn in post_urns:
        if new_replies >= MAX_REPLIES_PER_RUN:
            print(f"Reached max replies per run ({MAX_REPLIES_PER_RUN}). Stopping.")
            break

        comments = get_post_comments(post_urn)
        if not comments:
            continue

        for comment in comments:
            if new_replies >= MAX_REPLIES_PER_RUN:
                break

            try:
                actor_urn = comment.get('actor', '')
                comment_urn = comment.get('$URN', comment.get('id', ''))

                # Skip own comments
                if actor_urn == LINKEDIN_PERSON_URN:
                    continue

                # Skip already-processed comments (dedup)
                existing = conn.execute(
                    "SELECT id FROM linkedin_replies WHERE comment_urn = ?",
                    (comment_urn,)
                ).fetchone()
                if existing:
                    continue

                # Extract commenter info
                commenter_name = comment.get('actor~', {}).get('localizedFirstName', 'Someone')
                comment_text = comment.get('message', {}).get('text', '')

                if not comment_text:
                    continue

                # Get original post content for context
                post_content = get_post_content(conn, post_urn)

                # Generate reply
                reply_text = generate_reply(post_content, commenter_name, comment_text)

                if DRY_RUN:
                    print(f"  [DRY RUN] Would reply to {commenter_name}: {reply_text[:80]}...")
                    status = 'dry_run'
                else:
                    # Post the reply
                    result = comment_linkedin(post_urn, reply_text)
                    status = 'posted' if result else 'failed'
                    if result:
                        print(f"  Replied to {commenter_name}. Comment URN: {result}")
                        send_slack(f":linkedin: Replied to {commenter_name} on LinkedIn post")

                # Record the reply
                conn.execute("""
                    INSERT INTO linkedin_replies (post_urn, comment_urn, commenter_name,
                        comment_text, reply_text, status, posted_at)
                    VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                """, (post_urn, comment_urn, commenter_name, comment_text, reply_text, status))

                new_replies += 1

            except Exception as e:
                print(f"  Error processing comment: {e}")
                continue

    conn.commit()
    conn.close()

    mode = ' [DRY RUN]' if DRY_RUN else ''
    print(f"\nDone{mode}. Processed {new_replies} new comment(s).")


if __name__ == '__main__':
    main()
