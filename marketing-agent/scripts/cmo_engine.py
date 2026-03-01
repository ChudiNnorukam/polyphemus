#!/usr/bin/env python3
"""CMO Engine — Autonomous Chief Marketing Officer for OpenClaw.

Reads all marketing data (leads, social, funnel), analyzes through 5 lenses,
produces a prioritized action list, executes via existing scripts, and logs decisions.

Usage:
    python3 scripts/cmo_engine.py daily           # Full 5-lens assessment + actions
    python3 scripts/cmo_engine.py history          # Show last 20 decisions
    python3 scripts/cmo_engine.py undo             # Reverse last reversible action
    python3 scripts/cmo_engine.py --focus content   # Run only content lens
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone

import requests

# --- Config ---

DB_PATH = os.environ.get(
    'LEADS_DB_PATH',
    os.path.join(os.path.dirname(__file__), '..', 'data', 'marketing_leads.db')
)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

DRY_RUN = os.environ.get('DRY_RUN', 'true').lower() == 'true'
CMO_LIVE = os.environ.get('CMO_LIVE', 'false').lower() == 'true'
MAX_ACTIONS = int(os.environ.get('CMO_MAX_ACTIONS', '5'))

SLACK_BOT_TOKEN = os.environ.get('SLACK_BOT_TOKEN', '')
SLACK_CHANNEL_ID = os.environ.get('SLACK_CHANNEL_ID', '')

BLOG_DIR = os.environ.get(
    'BLOG_DIR',
    os.path.expanduser('~/Projects/active/chudi-blog/content/posts')
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
        print(f"DB not found: {DB_PATH}")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn, name):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def ensure_decisions_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cmo_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            lens TEXT NOT NULL,
            assessment TEXT NOT NULL,
            action TEXT,
            script_invoked TEXT,
            params TEXT,
            outcome TEXT,
            undo_cmd TEXT,
            dry_run BOOLEAN DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def pct(num, denom):
    if not denom:
        return 'n/a'
    return f"{num / denom * 100:.1f}%"


def slack_post(text):
    if not SLACK_BOT_TOKEN or not SLACK_CHANNEL_ID:
        return
    try:
        requests.post(
            'https://slack.com/api/chat.postMessage',
            headers={'Authorization': f'Bearer {SLACK_BOT_TOKEN}'},
            json={'channel': SLACK_CHANNEL_ID, 'text': text},
            timeout=10,
        )
    except Exception:
        pass


def _llm_synthesize(findings):
    """Call Claude Haiku to synthesize findings into strategic marketing insight.

    Returns empty string if no API key or on failure (graceful degradation).
    """
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        return ''

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)

        findings_text = '\n'.join(
            f"- [{f.get('severity', 'info').upper()}] {f['issue']}"
            for f in findings if f.get('issue')
        )
        if not findings_text:
            return ''

        response = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=500,
            messages=[{
                'role': 'user',
                'content': (
                    "You are the CMO of a solo-founder tech startup running "
                    "a Polymarket trading bot and selling digital products.\n"
                    "Given these findings from today's automated marketing assessment, "
                    "provide 2-3 sentences of strategic insight. Focus on: what is the "
                    "single most important marketing action this week, and why. "
                    "Be direct, no filler.\n\n"
                    f"FINDINGS:\n{findings_text}"
                ),
            }]
        )
        return response.content[0].text
    except Exception:
        return ''


# ============================================================
# LENS 1: PIPELINE HEALTH
# ============================================================

def assess_pipeline(conn):
    c = conn.cursor()
    findings = []

    total = c.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    enriched = c.execute("SELECT COUNT(*) FROM leads WHERE email IS NOT NULL").fetchone()[0]
    seq_started = c.execute("SELECT COUNT(*) FROM leads WHERE email_seq_started_at IS NOT NULL").fetchone()[0]
    replied = c.execute("SELECT COUNT(*) FROM leads WHERE email_replied_at IS NOT NULL").fetchone()[0]
    converted = c.execute("SELECT COUNT(*) FROM leads WHERE converted_at IS NOT NULL").fetchone()[0]
    conn_sent = c.execute("SELECT COUNT(*) FROM leads WHERE connection_sent_at IS NOT NULL").fetchone()[0]
    connected = c.execute("SELECT COUNT(*) FROM leads WHERE connection_accepted_at IS NOT NULL").fetchone()[0]

    # Check pipeline thinness
    unenriched = total - enriched
    if unenriched > 0 and enriched < total * 0.5:
        findings.append({
            'issue': f'{unenriched} of {total} prospects have no email. Pipeline is {pct(enriched, total)} enriched.',
            'action': 'enrich_lead',
            'params': 'run',
            'severity': 'high',
            'undo': None,
        })

    # Connection acceptance rate
    if conn_sent > 10:
        acceptance_rate = connected / conn_sent
        if acceptance_rate < 0.15:
            findings.append({
                'issue': f'LinkedIn acceptance rate is {pct(connected, conn_sent)} ({connected}/{conn_sent}). Below 15% threshold.',
                'action': None,
                'params': None,
                'severity': 'high',
                'undo': None,
            })

    # Stale pipeline check
    days_since_load = c.execute(
        "SELECT julianday('now') - julianday(MAX(created_at)) FROM leads"
    ).fetchone()[0]
    if days_since_load and days_since_load > 14:
        findings.append({
            'issue': f'No new prospects loaded in {int(days_since_load)} days. Pipeline will dry up.',
            'action': None,
            'params': None,
            'severity': 'medium',
            'undo': None,
        })

    # Email reply rate
    if seq_started > 10:
        reply_rate = replied / seq_started
        if reply_rate < 0.02:
            findings.append({
                'issue': f'Email reply rate is {pct(replied, seq_started)} ({replied}/{seq_started}). Below 2% floor.',
                'action': None,
                'params': None,
                'severity': 'medium',
                'undo': None,
            })

    return {
        'lens': 'pipeline',
        'summary': f'{total} prospects, {enriched} enriched, {seq_started} emailed, {replied} replied, {converted} converted',
        'findings': findings,
    }


# ============================================================
# LENS 2: CONTENT VELOCITY
# ============================================================

def assess_content(conn):
    findings = []

    if not table_exists(conn, 'social_posts'):
        return {'lens': 'content', 'summary': 'social_posts table missing', 'findings': []}

    c = conn.cursor()

    pending = c.execute("SELECT COUNT(*) FROM social_posts WHERE status='pending'").fetchone()[0]
    posted_7d = c.execute(
        "SELECT COUNT(*) FROM social_posts WHERE status='posted' AND posted_at >= datetime('now', '-7 days')"
    ).fetchone()[0]

    # Posts with no social repurpose
    all_slugs = set()
    if os.path.isdir(BLOG_DIR):
        for f in os.listdir(BLOG_DIR):
            if f.endswith('.md'):
                all_slugs.add(f.replace('.md', ''))

    repurposed_slugs = set()
    rows = c.execute("SELECT DISTINCT source_slug FROM social_posts").fetchall()
    for r in rows:
        repurposed_slugs.add(r['source_slug'])

    missing = all_slugs - repurposed_slugs
    if len(missing) > 3:
        findings.append({
            'issue': f'{len(missing)} blog posts have no social content. Top: {", ".join(list(missing)[:3])}',
            'action': 'repurpose',
            'params': f'--dir {BLOG_DIR} --days 30',
            'severity': 'medium',
            'undo': None,
        })

    # Queue depth
    if pending == 0 and posted_7d == 0:
        findings.append({
            'issue': 'Social queue is empty and nothing posted in 7 days. Content velocity is zero.',
            'action': 'repurpose',
            'params': f'--dir {BLOG_DIR} --days 14',
            'severity': 'high',
            'undo': None,
        })

    return {
        'lens': 'content',
        'summary': f'{pending} pending, {posted_7d} posted this week, {len(missing)} unrepurposed posts',
        'findings': findings,
    }


# ============================================================
# LENS 3: ENGAGEMENT QUALITY
# ============================================================

def assess_engagement(conn):
    findings = []
    c = conn.cursor()

    # Email open rates by step
    if table_exists(conn, 'sequence_sends'):
        for step in range(1, 6):
            sent = c.execute(
                "SELECT COUNT(*) FROM sequence_sends ss JOIN sequence_enrollments se ON ss.enrollment_id=se.id "
                "WHERE se.sequence_id='post-purchase-v1' AND ss.step=? AND ss.status IN ('sent','opened','clicked')",
                (step,)
            ).fetchone()[0]
            opened = c.execute(
                "SELECT COUNT(*) FROM sequence_sends ss JOIN sequence_enrollments se ON ss.enrollment_id=se.id "
                "WHERE se.sequence_id='post-purchase-v1' AND ss.step=? AND ss.opened_at IS NOT NULL",
                (step,)
            ).fetchone()[0]
            if sent >= 5:
                rate = opened / sent
                if rate < 0.20:
                    findings.append({
                        'issue': f'Funnel step {step} open rate is {pct(opened, sent)} ({opened}/{sent}). Below 20% threshold.',
                        'action': None,
                        'params': None,
                        'severity': 'medium',
                        'undo': None,
                    })

    # Social engagement (Pinterest)
    if table_exists(conn, 'social_posts'):
        pt_posts = c.execute(
            "SELECT COUNT(*) FROM social_posts WHERE platform='pinterest' AND status='posted'"
        ).fetchone()[0]
        pt_clicks = c.execute(
            "SELECT SUM(clicks) FROM social_posts WHERE platform='pinterest'"
        ).fetchone()[0] or 0

        if pt_posts >= 5 and pt_clicks == 0:
            findings.append({
                'issue': f'{pt_posts} Pinterest posts with 0 total clicks. Content may not be resonating.',
                'action': 'social_analytics',
                'params': '--days 14',
                'severity': 'medium',
                'undo': None,
            })

    return {
        'lens': 'engagement',
        'summary': f'{len(findings)} engagement issues found',
        'findings': findings,
    }


# ============================================================
# LENS 4: FUNNEL CONVERSION
# ============================================================

def assess_funnel(conn):
    findings = []

    if not table_exists(conn, 'funnel_contacts'):
        return {'lens': 'funnel', 'summary': 'funnel tables not created yet', 'findings': []}

    c = conn.cursor()

    purchases_7d = c.execute(
        "SELECT COUNT(*) FROM funnel_contacts WHERE purchased_at >= datetime('now', '-7 days')"
    ).fetchone()[0]
    purchases_30d = c.execute(
        "SELECT COUNT(*) FROM funnel_contacts WHERE purchased_at >= datetime('now', '-30 days')"
    ).fetchone()[0]

    # Upsell gap
    eligible = c.execute("""
        SELECT COUNT(*) FROM sequence_enrollments
        WHERE sequence_id='post-purchase-v1' AND current_step >= 4 AND status='active'
    """).fetchone()[0]
    upselled = c.execute(
        "SELECT COUNT(*) FROM sequence_enrollments WHERE sequence_id='upsell-v1'"
    ).fetchone()[0]

    if eligible > 0 and upselled == 0:
        findings.append({
            'issue': f'{eligible} buyers at step 4+ but 0 upsell enrollments. upsell_trigger may not be running.',
            'action': 'upsell_trigger',
            'params': '--dry-run',
            'severity': 'high',
            'undo': None,
        })

    # Sequence completion rate
    total_enrollments = c.execute(
        "SELECT COUNT(*) FROM sequence_enrollments WHERE sequence_id='post-purchase-v1'"
    ).fetchone()[0]
    completed = c.execute(
        "SELECT COUNT(*) FROM sequence_enrollments WHERE sequence_id='post-purchase-v1' AND status='completed'"
    ).fetchone()[0]
    if total_enrollments >= 5:
        completion_rate = completed / total_enrollments
        if completion_rate < 0.30:
            findings.append({
                'issue': f'Post-purchase sequence completion rate is {pct(completed, total_enrollments)}. Below 30%.',
                'action': None,
                'params': None,
                'severity': 'medium',
                'undo': None,
            })

    return {
        'lens': 'funnel',
        'summary': f'{purchases_7d} purchases (7d), {purchases_30d} purchases (30d), {upselled} upsells',
        'findings': findings,
    }


# ============================================================
# LENS 5: CHANNEL MIX
# ============================================================

def assess_channel_mix(conn):
    findings = []
    c = conn.cursor()

    channels = {}

    # LinkedIn activity
    li_recent = c.execute(
        "SELECT COUNT(*) FROM leads WHERE connection_sent_at >= datetime('now', '-7 days')"
    ).fetchone()[0]
    channels['linkedin'] = li_recent

    # Email activity
    email_recent = c.execute(
        "SELECT COUNT(*) FROM leads WHERE email_seq_started_at >= datetime('now', '-7 days')"
    ).fetchone()[0]
    channels['email'] = email_recent

    # Social activity
    if table_exists(conn, 'social_posts'):
        social_recent = c.execute(
            "SELECT COUNT(*) FROM social_posts WHERE status='posted' AND posted_at >= datetime('now', '-7 days')"
        ).fetchone()[0]
        channels['social'] = social_recent
    else:
        channels['social'] = 0

    # Stale channel detection
    for ch, count in channels.items():
        if count == 0:
            findings.append({
                'issue': f'{ch} channel has 0 activity in the last 7 days.',
                'action': None,
                'params': None,
                'severity': 'low',
                'undo': None,
            })

    # Identify strongest channel
    if channels:
        best = max(channels, key=channels.get)
        best_count = channels[best]
        if best_count > 0:
            pass  # Informational only

    return {
        'lens': 'channel_mix',
        'summary': ', '.join(f'{ch}={cnt}' for ch, cnt in channels.items()),
        'findings': findings,
    }


# ============================================================
# ACTION EXECUTION
# ============================================================

ALLOWED_SCRIPTS = {
    'repurpose': 'repurpose.py',
    'publisher': 'publisher.py',
    'token_manager': 'token_manager.py',
    'social_analytics': 'social_analytics.py',
    'social_resolve': 'social_resolve.py',
    'enrich_lead': 'enrich_lead.py',
    'upsell_trigger': 'upsell_trigger.py',
    'funnel_resolve': 'funnel_resolve.py',
    'marketing_resolve': 'marketing_resolve.py',
}

AUTO_APPROVE = {'repurpose', 'publisher', 'token_manager', 'social_analytics', 'social_resolve',
                'marketing_resolve', 'funnel_resolve'}


def execute_action(action_name, params, dry_run=True):
    if action_name not in ALLOWED_SCRIPTS:
        return f'SKIPPED: {action_name} not in allowed scripts'

    if action_name not in AUTO_APPROVE:
        return f'NEEDS_APPROVAL: {action_name} requires user approval'

    script = os.path.join(SCRIPT_DIR, ALLOWED_SCRIPTS[action_name])
    if not os.path.exists(script):
        return f'MISSING: {script} not found'

    cmd = [sys.executable, script]
    if params:
        cmd.extend(params.split())

    if dry_run:
        return f'DRY_RUN: would run {" ".join(cmd)}'

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            return f'OK: {result.stdout[:200]}'
        return f'ERROR: exit {result.returncode}: {result.stderr[:200]}'
    except subprocess.TimeoutExpired:
        return 'TIMEOUT: script exceeded 120s'
    except Exception as e:
        return f'EXCEPTION: {e}'


# ============================================================
# DAILY COMMAND
# ============================================================

def cmd_daily(args):
    conn = get_db()
    ensure_decisions_table(conn)
    run_id = str(uuid.uuid4())[:8]

    is_dry = DRY_RUN or not CMO_LIVE
    mode_label = 'DRY RUN' if is_dry else 'LIVE'

    lenses_to_run = ['pipeline', 'content', 'engagement', 'funnel', 'channel_mix']
    if args.focus:
        lenses_to_run = [args.focus]

    assessors = {
        'pipeline': assess_pipeline,
        'content': assess_content,
        'engagement': assess_engagement,
        'funnel': assess_funnel,
        'channel_mix': assess_channel_mix,
    }

    all_findings = []
    digest_lines = [
        f'CMO DAILY DIGEST [{mode_label}]',
        '=' * 50,
        f'Run: {run_id} | {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}',
        '',
    ]

    for lens_name in lenses_to_run:
        if lens_name not in assessors:
            continue
        result = assessors[lens_name](conn)
        digest_lines.append(f'## {lens_name.upper()}')
        digest_lines.append(f'   {result["summary"]}')

        for f in result['findings']:
            all_findings.append(f)
            marker = f'[{f["severity"].upper()}]'
            digest_lines.append(f'   {marker} {f["issue"]}')
            if f['action']:
                digest_lines.append(f'   -> Action: {f["action"]} {f["params"] or ""}')

        if not result['findings']:
            digest_lines.append('   All clear.')
        digest_lines.append('')

    # Sort by severity and execute top N
    severity_order = {'high': 0, 'medium': 1, 'low': 2}
    actionable = [f for f in all_findings if f['action']]
    actionable.sort(key=lambda x: severity_order.get(x['severity'], 3))

    actions_taken = 0
    digest_lines.append('## ACTIONS')

    for finding in actionable[:MAX_ACTIONS]:
        outcome = execute_action(finding['action'], finding['params'], dry_run=is_dry)
        actions_taken += 1
        digest_lines.append(f'   {actions_taken}. {finding["action"]}: {outcome}')

        # Log decision
        conn.execute("""
            INSERT INTO cmo_decisions (run_id, lens, assessment, action, script_invoked, params, outcome, undo_cmd, dry_run)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            run_id,
            finding.get('lens', 'unknown'),
            finding['issue'],
            finding['action'],
            ALLOWED_SCRIPTS.get(finding['action']),
            finding['params'],
            outcome,
            finding.get('undo'),
            is_dry,
        ))

    if not actionable:
        digest_lines.append('   No actions needed. All systems nominal.')

    conn.commit()

    # Also log assessments with no action
    for finding in all_findings:
        if not finding['action']:
            conn.execute("""
                INSERT INTO cmo_decisions (run_id, lens, assessment, action, script_invoked, params, outcome, dry_run)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (run_id, 'alert', finding['issue'], None, None, None, 'ALERT_ONLY', is_dry))
    conn.commit()

    # LLM Synthesis
    if all_findings:
        ai_insight = _llm_synthesize(all_findings)
        if ai_insight:
            digest_lines.append('')
            digest_lines.append('## AI INSIGHT')
            digest_lines.append(f'   {ai_insight}')

            conn.execute("""
                INSERT INTO cmo_decisions (run_id, lens, assessment, action, script_invoked, params, outcome, dry_run)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (run_id, 'ai_insight', ai_insight, None, None, None, 'LLM_SYNTHESIS', is_dry))
            conn.commit()

    digest_lines.append('')
    digest_lines.append(f'Total findings: {len(all_findings)} | Actions: {actions_taken}/{MAX_ACTIONS} max')
    digest_lines.append('=' * 50)

    digest = '\n'.join(digest_lines)
    print(digest)

    # Post to Slack
    slack_post(digest)

    conn.close()


# ============================================================
# HISTORY COMMAND
# ============================================================

def cmd_history(args):
    conn = get_db()
    ensure_decisions_table(conn)

    rows = conn.execute("""
        SELECT run_id, lens, assessment, action, outcome, dry_run, created_at
        FROM cmo_decisions
        ORDER BY created_at DESC
        LIMIT 20
    """).fetchall()

    if not rows:
        print("No CMO decisions logged yet.")
        conn.close()
        return

    print()
    print("CMO DECISION HISTORY (last 20)")
    print("=" * 70)
    for r in rows:
        mode = 'DRY' if r['dry_run'] else 'LIVE'
        action = r['action'] or 'alert'
        print(f"  [{r['created_at'][:16]}] [{mode}] {r['run_id']} | {r['lens']}")
        print(f"    {r['assessment'][:80]}")
        if r['action']:
            print(f"    -> {action}: {r['outcome'][:60]}")
        print()
    print("=" * 70)
    conn.close()


# ============================================================
# UNDO COMMAND
# ============================================================

def cmd_undo(args):
    conn = get_db()
    ensure_decisions_table(conn)

    row = conn.execute("""
        SELECT id, action, undo_cmd, outcome FROM cmo_decisions
        WHERE undo_cmd IS NOT NULL AND dry_run=0
        ORDER BY created_at DESC
        LIMIT 1
    """).fetchone()

    if not row:
        print("No reversible live actions found.")
        conn.close()
        return

    print(f"Last reversible action: {row['action']}")
    print(f"Undo command: {row['undo_cmd']}")
    print("Undo execution not yet implemented (manual for safety).")
    conn.close()


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='CMO Engine')
    parser.add_argument('command', nargs='?', default='daily',
                        choices=['daily', 'history', 'undo'])
    parser.add_argument('--focus', choices=['pipeline', 'content', 'engagement', 'funnel', 'channel_mix'])
    args = parser.parse_args()

    if args.command == 'daily':
        cmd_daily(args)
    elif args.command == 'history':
        cmd_history(args)
    elif args.command == 'undo':
        cmd_undo(args)


if __name__ == '__main__':
    main()
