#!/usr/bin/env python3
"""Repurpose — Convert chudi-blog .md posts into platform-specific social content.

Reads a blog post, calls Claude Haiku 3x (one per platform), and queues
rows in social_posts. Deduplicates by slug+platform (INSERT OR IGNORE).

Usage:
    python3 scripts/repurpose.py --file /path/to/post.md [--dry-run]
    python3 scripts/repurpose.py --dir /path/to/posts/ --days 7
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

from anthropic import Anthropic

DB_PATH = os.environ.get(
    'LEADS_DB_PATH',
    os.path.join(os.path.dirname(__file__), '..', 'data', 'marketing_leads.db')
)
DRY_RUN = os.environ.get('DRY_RUN', 'false').lower() == 'true'

FORBIDDEN_PHRASES = [
    '\u2014',           # em dash
    'Additionally',
    'Furthermore',
    'leverage',
    'robust',
    'scalable',
    'game-changer',
    'cutting-edge',
    'In conclusion',
    'Moreover',
]

LINKEDIN_CHAR_LIMIT = 1200
LINKEDIN_TARGET_CHARS = 1150
LINKEDIN_MAX_HASHTAGS = 3
LINKEDIN_COMMENT_PATTERNS = [
    r'\bin the comments\b',
    r'\bin comments\b',
    r'\bfirst comment\b',
    r'\bdropping it in the comments\b',
]
LINKEDIN_VOICE_BRIEF = """Voice goals:
- Sound like a mid-to-senior practitioner thinking in public, not a guru or marketer
- Warm, exploratory, and peer-to-peer
- Use concrete mechanisms, tradeoffs, failure modes, and architecture layers when relevant
- Applied LLM / harness engineering / systems design framing is welcome when it fits the topic
- Prefer \"I keep noticing\", \"my current model is\", or similarly curious framing over absolute claims
- Name what changed your thinking, not just the conclusion
- Avoid hype, certainty theater, hustle language, and sweeping statements about what \"everyone\" should do
- No em dashes
"""


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
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
DRY_RUN = os.environ.get('DRY_RUN', 'false').lower() == 'true'


def get_db():
    if not os.path.exists(DB_PATH):
        print(f"DB not found: {DB_PATH}. Run init_db.py + funnel_db_init.py extend first.")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def parse_frontmatter(text: str) -> dict:
    """Extract YAML frontmatter fields using regex (no PyYAML dependency)."""
    meta = {}
    match = re.match(r'^---\s*\n(.*?)\n---', text, re.DOTALL)
    if not match:
        return meta
    for line in match.group(1).split('\n'):
        if ':' in line:
            k, _, v = line.partition(':')
            meta[k.strip()] = v.strip().strip('"\'')
    return meta


def strip_to_plain_text(text: str) -> str:
    """Remove frontmatter, Svelte components, and markdown formatting."""
    # Remove frontmatter
    text = re.sub(r'^---\s*\n.*?\n---\s*\n', '', text, flags=re.DOTALL)
    # Remove script/style tags
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    # Remove self-closing and component tags like <AuthorBio /> or <Component>
    text = re.sub(r'<[A-Z][^>]*/>', '', text)
    text = re.sub(r'<[A-Z][^>]*>.*?</[A-Z][^>]*>', '', text, flags=re.DOTALL)
    # Remove remaining HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Remove markdown links but keep text: [text](url) -> text
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # Remove markdown image syntax
    text = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', text)
    # Remove code blocks
    text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
    text = re.sub(r'`[^`]+`', '', text)
    # Remove heading markers
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Remove bold/italic
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
    # Collapse whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def check_and_fix_voice(client: Anthropic, text: str, platform: str) -> str:
    """Scan for forbidden phrases; re-call LLM to rewrite each one found."""
    found = [p for p in FORBIDDEN_PHRASES if p in text]
    for phrase in found:
        label = 'em dash' if phrase == '\u2014' else f'"{phrase}"'
        prompt = f"Rewrite this without {label}. Return only the rewritten text, nothing else:\n\n{text}"
        response = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=800,
            messages=[{'role': 'user', 'content': prompt}]
        )
        text = response.content[0].text.strip()
    return text


def extract_hashtags(text: str) -> list[str]:
    return re.findall(r'(?<!\w)#[A-Za-z0-9_]+', text)


def linkedin_issues(text: str) -> list[str]:
    issues = []
    if len(text) > LINKEDIN_CHAR_LIMIT:
        issues.append(f'length {len(text)} exceeds {LINKEDIN_CHAR_LIMIT}')
    if len(extract_hashtags(text)) > LINKEDIN_MAX_HASHTAGS:
        issues.append(f'more than {LINKEDIN_MAX_HASHTAGS} hashtags')
    if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in LINKEDIN_COMMENT_PATTERNS):
        issues.append('mentions comments or first comment')
    if '\u2014' in text:
        issues.append('contains em dash')
    if '---' in text:
        issues.append('contains divider line')
    return issues


def fallback_linkedin_cleanup(text: str) -> str:
    cleaned = text.replace('\r\n', '\n').strip()
    cleaned = re.sub(r'\n?---\n?', '\n\n', cleaned)

    kept_lines = []
    hashtag_lines = []
    for raw_line in cleaned.split('\n'):
        line = raw_line.strip()
        if not line:
            continue
        if any(re.search(pattern, line, flags=re.IGNORECASE) for pattern in LINKEDIN_COMMENT_PATTERNS):
            continue
        if line.startswith('#'):
            hashtag_lines.extend(extract_hashtags(line))
            continue
        kept_lines.append(line)

    hashtags = []
    for tag in hashtag_lines:
        if tag not in hashtags:
            hashtags.append(tag)
        if len(hashtags) >= LINKEDIN_MAX_HASHTAGS:
            break

    result = '\n\n'.join(kept_lines).strip()
    if hashtags:
        result = f"{result}\n\n{' '.join(hashtags[:LINKEDIN_MAX_HASHTAGS])}"

    if len(result) <= LINKEDIN_CHAR_LIMIT:
        return result

    tag_block = ''
    if hashtags:
        tag_block = f"\n\n{' '.join(hashtags[:LINKEDIN_MAX_HASHTAGS])}"
    budget = LINKEDIN_TARGET_CHARS - len(tag_block)
    budget = max(200, budget)
    trimmed = result[:budget].rstrip()
    last_break = max(trimmed.rfind('\n\n'), trimmed.rfind('. '))
    if last_break > 200:
        trimmed = trimmed[:last_break].rstrip()
    return f"{trimmed}{tag_block}".strip()


def normalize_linkedin_post(client: Anthropic | None, text: str, title: str = '') -> str:
    normalized = text.strip()
    for _ in range(3):
        issues = linkedin_issues(normalized)
        if not issues:
            return normalized
        if client is None:
            break
        prompt = f"""Rewrite this LinkedIn post so it meets every constraint.

Title: {title or 'LinkedIn post'}

{LINKEDIN_VOICE_BRIEF}

Constraints:
- Keep the same core hook, insight, and exploratory tone
- Maximum {LINKEDIN_TARGET_CHARS} characters total
- Maximum {LINKEDIN_MAX_HASHTAGS} hashtags
- No mention of comments, first comment, or "dropping it in the comments"
- No em dashes
- No divider line like ---
- End with either one practical takeaway or one narrow discussion question
- If the post makes a technical point, ground it in mechanism or architecture instead of broad advice
- Return only the rewritten post text

Issues to fix: {', '.join(issues)}

Current post:
{normalized}
"""
        response = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=500,
            messages=[{'role': 'user', 'content': prompt}]
        )
        normalized = response.content[0].text.strip()

    return fallback_linkedin_cleanup(normalized)


def generate_linkedin(client: Anthropic, title: str, body: str, tags: str) -> str:
    prompt = f"""Write a LinkedIn post (800-1200 characters) based on this blog post.

Title: {title}
Tags: {tags}
Content:
{body[:2000]}

{LINKEDIN_VOICE_BRIEF}

Rules:
- Open with either a sharp observed pattern, a concrete failure, or a specific story beat
- Share a specific insight or story, not a summary of the article
- Sound like someone building real systems and revising their thinking in public
- Senior signal should come from tradeoffs, mechanisms, and judgment, not jargon or certainty
- If the topic touches AI, software, or productivity, talk about the control surface around the work: context, routing, verification, memory, handoffs, constraints, or observability when relevant
- End with one practical takeaway or one narrow discussion question
- No hashtags in the text (save for end, max 3)
- No em dashes, no "leverage", no "robust", no "game-changer"
- No mention of comments, first comment, or "dropping it in the comments"
- Avoid sweeping declarations like "this is why everyone should" or "99% of people"
- Return only the post text."""

    response = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=600,
        messages=[{'role': 'user', 'content': prompt}]
    )
    return response.content[0].text.strip()


def generate_twitter(client: Anthropic, title: str, body: str, tags: str) -> str:
    prompt = f"""Write a Twitter thread of 8-10 tweets based on this blog post.
Return a JSON array of strings. Each string is one tweet (max 280 chars).
Tweet 1 is the hook. Last tweet is a CTA or takeaway.

Title: {title}
Tags: {tags}
Content:
{body[:2000]}

Rules:
- No em dashes, no "leverage", no "cutting-edge"
- Conversational, not corporate
- Each tweet must stand alone if read out of context
- Return ONLY the JSON array, no other text."""

    response = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=800,
        messages=[{'role': 'user', 'content': prompt}]
    )
    raw = response.content[0].text.strip()
    # Strip markdown code fences if LLM wrapped the JSON
    raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.IGNORECASE)
    raw = re.sub(r'\s*```$', '', raw).strip()
    # Validate it parses as JSON array; return as raw string for storage
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise ValueError("Not a list")
        return json.dumps(parsed)
    except Exception:
        # Fallback: return raw text in a single-item JSON array
        return json.dumps([raw[:280]])


def generate_pinterest(client: Anthropic, title: str, body: str, tags: str) -> str:
    prompt = f"""Write a Pinterest pin description (150-300 characters) and a short title (max 100 chars).

Title: {title}
Tags: {tags}
Content:
{body[:1000]}

Rules:
- Description should include the key benefit and a soft CTA
- No em dashes, no marketing buzzwords
- Return as two lines: first line = pin title, second line = pin description. Nothing else."""

    response = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=200,
        messages=[{'role': 'user', 'content': prompt}]
    )
    return response.content[0].text.strip()


def next_weekday_at(weekday: int, hour: int, minute: int = 0) -> str:
    """Return next occurrence of given weekday (0=Mon) at given PST hour as UTC ISO string.
    PST = UTC-8."""
    now_utc = datetime.now(timezone.utc)
    days_ahead = (weekday - now_utc.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7  # Always next occurrence, not today
    target_date = (now_utc + timedelta(days=days_ahead)).date()
    # Convert PST hour to UTC (PST = UTC-8)
    target_utc = datetime(target_date.year, target_date.month, target_date.day,
                          hour + 8, minute, 0, tzinfo=timezone.utc)
    return target_utc.strftime('%Y-%m-%d %H:%M:%S')


def repurpose_file(filepath: str, client: Anthropic, conn) -> int:
    """Repurpose one .md file. Returns number of rows inserted."""
    with open(filepath) as f:
        text = f.read()

    meta = parse_frontmatter(text)
    title = meta.get('title', os.path.basename(filepath).replace('.md', ''))
    slug = meta.get('slug', os.path.basename(filepath).replace('.md', ''))
    tags = meta.get('tags', '')
    image_url = meta.get('image', meta.get('coverImage', ''))

    body = strip_to_plain_text(text)
    if len(body) < 100:
        print(f"  Skipping {slug}: body too short after stripping ({len(body)} chars)")
        return 0

    print(f"  Processing: {slug}")

    if client is None:
        # No API key — use stub content for dry-run
        linkedin_content = f"[DRY RUN placeholder] LinkedIn post for: {title}"
        twitter_content = json.dumps([f"[DRY RUN placeholder] Tweet 1 for: {title}", "Tweet 2..."])
        pinterest_content = f"{title}\n[DRY RUN placeholder] Pinterest description for: {title}"
    else:
        # Generate content for all 3 platforms
        linkedin_content = generate_linkedin(client, title, body, tags)
        twitter_content = generate_twitter(client, title, body, tags)
        pinterest_content = generate_pinterest(client, title, body, tags)

        # Voice DNA reflexion pass
        linkedin_content = check_and_fix_voice(client, linkedin_content, 'linkedin')
        pinterest_content = check_and_fix_voice(client, pinterest_content, 'pinterest')
        # Twitter is JSON array — check the raw string
        twitter_content = check_and_fix_voice(client, twitter_content, 'twitter')

    linkedin_content = normalize_linkedin_post(client, linkedin_content, title)

    # Scheduled times
    linkedin_at = next_weekday_at(0, 10)   # Monday 10am PST
    twitter_at = next_weekday_at(1, 9)     # Tuesday 9am PST
    pinterest_at = next_weekday_at(2, 11)  # Wednesday 11am PST

    posts = [
        ('linkedin', linkedin_content, image_url, linkedin_at),
        ('twitter', twitter_content, image_url, twitter_at),
        ('pinterest', pinterest_content, image_url, pinterest_at),
    ]

    inserted = 0
    for platform, content, img, scheduled_at in posts:
        if DRY_RUN:
            print(f"    [DRY RUN] Would queue {platform} post for {slug} at {scheduled_at}")
            print(f"      Preview: {content[:80]}...")
            inserted += 1
            continue

        cursor = conn.execute("""
            INSERT OR IGNORE INTO social_posts
                (source_slug, platform, content, image_url, scheduled_at, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
        """, (slug, platform, content, img or None, scheduled_at))
        if cursor.rowcount:
            inserted += 1

    if not DRY_RUN:
        conn.commit()

    ln_time = datetime.fromisoformat(linkedin_at).strftime('%a %b %d %I%p')
    tw_time = datetime.fromisoformat(twitter_at).strftime('%a %b %d %I%p')
    pt_time = datetime.fromisoformat(pinterest_at).strftime('%a %b %d %I%p')
    print(f"  Queued {inserted} posts for {slug}: LinkedIn {ln_time} / Twitter {tw_time} / Pinterest {pt_time}")
    return inserted


def collect_files(args) -> list:
    files = []
    if args.file:
        if not os.path.exists(args.file):
            print(f"File not found: {args.file}")
            sys.exit(1)
        files = [args.file]
    elif args.dir:
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
        for fname in os.listdir(args.dir):
            if not fname.endswith('.md'):
                continue
            fpath = os.path.join(args.dir, fname)
            mtime = datetime.fromtimestamp(os.path.getmtime(fpath), tz=timezone.utc)
            if mtime >= cutoff:
                files.append(fpath)
        if not files:
            print(f"No .md files modified in last {args.days} days in {args.dir}")
    else:
        print("Provide --file or --dir")
        sys.exit(1)
    return files


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--file', help='Single .md file to repurpose')
    parser.add_argument('--dir', help='Directory of .md files')
    parser.add_argument('--days', type=int, default=7, help='Only files modified in last N days (with --dir)')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    if args.dry_run:
        os.environ['DRY_RUN'] = 'true'
        global DRY_RUN
        DRY_RUN = True

    if not ANTHROPIC_API_KEY and not DRY_RUN:
        print("ANTHROPIC_API_KEY not set.")
        sys.exit(1)

    files = collect_files(args)
    if not files:
        return

    client = Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None
    conn = get_db()

    total_inserted = 0
    for filepath in files:
        try:
            n = repurpose_file(filepath, client, conn)
            total_inserted += n
        except Exception as e:
            print(f"  ERROR processing {filepath}: {e}")

    conn.close()
    mode = ' [DRY RUN]' if DRY_RUN else ''
    print(f"\nDone{mode}. Queued {total_inserted} posts across {len(files)} file(s).")


if __name__ == '__main__':
    main()
