#!/usr/bin/env python3
"""Social Review — Interactively approve, edit, or reject queued social posts.

Posts must be approved here before publisher.py will send them.

Usage:
    python3 scripts/social_review.py               # Review all pending posts
    python3 scripts/social_review.py --platform linkedin
    python3 scripts/social_review.py --slug adhd-engineer-productivity-system
    python3 scripts/social_review.py --list        # Show queue summary only
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
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
    if not os.path.exists(DB_PATH):
        print(f"DB not found: {DB_PATH}. Run init_db.py + funnel_db_init.py extend first.")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


PLATFORM_CHARS = {
    'linkedin': 1200,
    'twitter': 280,    # per tweet
    'pinterest': 300,
}

PLATFORM_ICONS = {
    'linkedin': 'in',
    'twitter': 'tw',
    'pinterest': 'pt',
}


def format_content(platform: str, content: str) -> str:
    """Format content for display. Twitter JSON array shown as numbered tweets."""
    if platform == 'twitter':
        try:
            tweets = json.loads(content)
            if isinstance(tweets, list):
                lines = []
                for i, tweet in enumerate(tweets, 1):
                    char_count = len(tweet)
                    flag = ' !!OVER!!' if char_count > 280 else ''
                    lines.append(f"  [{i:02d}] ({char_count} chars){flag}\n       {tweet}")
                return '\n'.join(lines)
        except (json.JSONDecodeError, TypeError):
            pass
    return content


def char_count_display(platform: str, content: str) -> str:
    """Return character count info for the platform."""
    if platform == 'twitter':
        try:
            tweets = json.loads(content)
            if isinstance(tweets, list):
                over = [i+1 for i, t in enumerate(tweets) if len(t) > 280]
                if over:
                    return f"{len(tweets)} tweets — OVER 280 chars: tweets {over}"
                return f"{len(tweets)} tweets, all within 280 chars"
        except (json.JSONDecodeError, TypeError):
            pass
        return f"{len(content)} chars"

    limit = PLATFORM_CHARS.get(platform, 9999)
    count = len(content)
    flag = ' !! OVER LIMIT !!' if count > limit else ''
    return f"{count}/{limit} chars{flag}"


def print_post(post: dict, index: int, total: int):
    icon = PLATFORM_ICONS.get(post['platform'], post['platform'])
    scheduled = post['scheduled_at'] or 'unscheduled'
    print()
    print(f"{'━' * 60}")
    print(f"  [{index}/{total}]  [{icon.upper()}]  {post['source_slug']}")
    print(f"  Scheduled: {scheduled}  |  {char_count_display(post['platform'], post['content'])}")
    print(f"{'━' * 60}")
    print()
    print(format_content(post['platform'], post['content']))
    print()


def open_in_editor(content: str, platform: str) -> str | None:
    """Open content in $EDITOR. Returns edited content or None if unchanged/aborted."""
    editor = os.environ.get('EDITOR', 'nano')

    if platform == 'twitter':
        # Show tweets as numbered lines for editing, re-serialize after
        try:
            tweets = json.loads(content)
            if isinstance(tweets, list):
                edit_content = '\n\n'.join(
                    f"# Tweet {i+1} ({len(t)} chars)\n{t}" for i, t in enumerate(tweets)
                )
                edit_content += "\n\n# Each tweet is the text after the '# Tweet N' header line.\n# Blank lines between tweets are fine. Don't edit the header lines."
            else:
                edit_content = content
        except (json.JSONDecodeError, TypeError):
            edit_content = content
    else:
        edit_content = content

    with tempfile.NamedTemporaryFile(
        mode='w', suffix=f'.{platform}.txt', delete=False, prefix='social_review_'
    ) as f:
        f.write(edit_content)
        tmp_path = f.name

    try:
        subprocess.call([editor, tmp_path])
        with open(tmp_path) as f:
            edited = f.read()
    finally:
        os.unlink(tmp_path)

    if platform == 'twitter':
        # Re-parse edited tweets from numbered sections
        try:
            original_tweets = json.loads(content) if isinstance(json.loads(content), list) else None
        except (json.JSONDecodeError, TypeError):
            original_tweets = None

        if original_tweets is not None:
            tweets_out = []
            for line in edited.split('\n'):
                line_stripped = line.strip()
                if line_stripped.startswith('# Tweet ') or line_stripped.startswith('# Each tweet'):
                    continue
                tweets_out.append(line_stripped)

            # Reconstruct: collapse blank lines between tweets, group by blanks
            raw = '\n'.join(tweets_out).strip()
            # Split on double newlines (paragraph = one tweet)
            paragraphs = [p.strip() for p in raw.split('\n\n') if p.strip()]
            if paragraphs:
                return json.dumps(paragraphs)

    if edited.strip() == edit_content.strip():
        return None  # No change
    return edited.strip()


def cmd_list(args):
    conn = get_db()
    rows = conn.execute("""
        SELECT platform, source_slug, status, scheduled_at,
               length(content) as content_len
        FROM social_posts
        ORDER BY scheduled_at ASC
    """).fetchall()
    conn.close()

    if not rows:
        print("No posts in queue.")
        return

    pending   = [r for r in rows if r['status'] == 'pending']
    approved  = [r for r in rows if r['status'] == 'approved']
    rejected  = [r for r in rows if r['status'] == 'rejected']
    posted    = [r for r in rows if r['status'] == 'posted']
    failed    = [r for r in rows if r['status'] == 'failed']

    print()
    print("SOCIAL QUEUE")
    print("━" * 55)
    print(f"  pending {len(pending)} / approved {len(approved)} / rejected {len(rejected)} / posted {len(posted)} / failed {len(failed)}")
    print()

    if pending:
        print(f"  PENDING (needs review):")
        for r in pending:
            icon = PLATFORM_ICONS.get(r['platform'], r['platform'])
            sched = (r['scheduled_at'] or '?')[:16]
            print(f"    [{icon}] {sched}  {r['source_slug']}")

    if approved:
        print()
        print(f"  APPROVED (will post on schedule):")
        for r in approved:
            icon = PLATFORM_ICONS.get(r['platform'], r['platform'])
            sched = (r['scheduled_at'] or '?')[:16]
            print(f"    [{icon}] {sched}  {r['source_slug']}")

    print("━" * 55)
    print()


def cmd_review(args):
    conn = get_db()

    query = "SELECT * FROM social_posts WHERE status='pending'"
    params = []
    if args.platform:
        query += " AND platform=?"
        params.append(args.platform)
    if args.slug:
        query += " AND source_slug=?"
        params.append(args.slug)
    query += " ORDER BY scheduled_at ASC"

    posts = conn.execute(query, params).fetchall()

    if not posts:
        print("No pending posts to review.")
        if args.platform or args.slug:
            print("(Try without --platform/--slug to see all pending)")
        conn.close()
        return

    print(f"\n{len(posts)} pending post(s) to review.")
    print("Actions: [a]pprove  [e]dit  [r]eject  [s]kip  [q]uit\n")

    approved_count = 0
    rejected_count = 0
    skipped_count = 0

    for i, post in enumerate(posts, 1):
        post = dict(post)
        print_post(post, i, len(posts))

        while True:
            try:
                choice = input("  Action [a/e/r/s/q]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nInterrupted. Exiting.")
                break

            if choice in ('a', ''):
                conn.execute(
                    "UPDATE social_posts SET status='approved' WHERE id=?", (post['id'],)
                )
                conn.commit()
                approved_count += 1
                print(f"  Approved. Will post on {(post['scheduled_at'] or '?')[:16]}.")
                break

            elif choice == 'e':
                print("  Opening editor...")
                edited = open_in_editor(post['content'], post['platform'])
                if edited is None:
                    print("  No changes detected.")
                else:
                    post['content'] = edited
                    conn.execute(
                        "UPDATE social_posts SET content=? WHERE id=?",
                        (edited, post['id'])
                    )
                    conn.commit()
                    print()
                    print("  Updated content:")
                    print()
                    print(format_content(post['platform'], edited))
                    print()
                # Show the post again with updated content and re-prompt
                print(f"  {char_count_display(post['platform'], post['content'])}")
                print("  Actions: [a]pprove  [e]dit again  [r]eject  [s]kip")
                continue

            elif choice == 'r':
                conn.execute(
                    "UPDATE social_posts SET status='rejected' WHERE id=?", (post['id'],)
                )
                conn.commit()
                rejected_count += 1
                print("  Rejected.")
                break

            elif choice == 's':
                skipped_count += 1
                print("  Skipped (still pending).")
                break

            elif choice == 'q':
                print(f"\nExiting. Approved: {approved_count}, Rejected: {rejected_count}, Skipped: {skipped_count + (len(posts) - i)}")
                conn.close()
                return

            else:
                print("  Enter a, e, r, s, or q.")

    conn.close()
    print(f"\nDone. Approved: {approved_count} | Rejected: {rejected_count} | Skipped: {skipped_count}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--platform', choices=['linkedin', 'twitter', 'pinterest'],
                        help='Filter by platform')
    parser.add_argument('--slug', help='Filter by source slug')
    parser.add_argument('--list', action='store_true', help='Show queue summary only')
    args = parser.parse_args()

    if args.list:
        cmd_list(args)
    else:
        cmd_review(args)


if __name__ == '__main__':
    main()
