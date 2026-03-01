#!/usr/bin/env python3
"""CEO Engine -- Strategic Analyst for OpenClaw.

Reads CMO, COO, and CTO decision tables. Produces weekly cross-functional brief.
Surfaces conflicts between engines. NEVER executes anything. Pure read-only.

Usage:
    python3 scripts/ceo_engine.py weekly            # Full 4-lens strategic brief
    python3 scripts/ceo_engine.py history            # Show last 10 decisions
    python3 scripts/ceo_engine.py --focus revenue    # Run single lens
"""

import argparse
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

SLACK_BOT_TOKEN = os.environ.get('SLACK_BOT_TOKEN', '')
SLACK_CHANNEL_ID = os.environ.get('SLACK_CHANNEL_ID', '')

VPS_HOST = os.environ.get('VPS_HOST', '82.24.19.114')
VPS_USER = os.environ.get('VPS_USER', 'root')

INSTANCES = {
    'emmanuel': '/opt/lagbot/instances/emmanuel/data',
    'polyphemus': '/opt/lagbot/instances/polyphemus/data',
}


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
        CREATE TABLE IF NOT EXISTS ceo_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            lens TEXT NOT NULL,
            assessment TEXT NOT NULL,
            severity TEXT NOT NULL,
            insight TEXT,
            recommendation TEXT,
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


def ssh_cmd(cmd, timeout=15):
    """Run a read-only command on VPS via SSH. Returns (success, output)."""
    try:
        result = subprocess.run(
            ['ssh', '-o', 'ConnectTimeout=5', '-o', 'StrictHostKeyChecking=no',
             f'{VPS_USER}@{VPS_HOST}', cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode == 0, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, 'SSH_TIMEOUT'
    except Exception as e:
        return False, str(e)


def _get_lessons(agent='ceo'):
    """Fetch recent self-critique lessons from agent_reflections."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT lesson FROM agent_reflections WHERE agent=? "
            "ORDER BY created_at DESC LIMIT 3", (agent,)
        ).fetchall()
        conn.close()
        return [r['lesson'] for r in rows if r['lesson']]
    except Exception:
        return []


def _llm_synthesize(findings, context=''):
    """Call Claude Haiku to synthesize cross-functional findings into strategic insight.

    Returns empty string if no API key or on failure (graceful degradation).
    """
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        return ''

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)

        findings_text = '\n'.join(
            f"- [{f.get('severity', 'info').upper()}] [{f.get('lens', '?')}] {f['issue']}"
            for f in findings if f.get('issue')
        )
        if not findings_text:
            return ''

        prompt = (
            "You are the CEO of a solo-founder tech startup that runs a Polymarket "
            "trading bot (lagbot) and a marketing/product system (OpenClaw).\n"
            "You are reviewing the weekly cross-functional report from your CMO, COO, and CTO.\n"
            "Given these findings, provide 3-4 sentences of strategic insight.\n"
            "Focus on: (1) what is the single highest-leverage action this week, "
            "(2) any conflicts between departments, (3) resource allocation.\n"
            "Be direct, no filler.\n"
        )
        lessons = _get_lessons('ceo')
        if lessons:
            prompt += "\nLESSONS FROM YOUR PAST SELF-CRITIQUE:\n"
            prompt += '\n'.join(f"- {l}" for l in lessons) + "\n"
        if context:
            prompt += f"\nCONTEXT:\n{context}\n"
        prompt += f"\nFINDINGS:\n{findings_text}"

        response = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=600,
            messages=[{'role': 'user', 'content': prompt}]
        )
        return response.content[0].text
    except Exception:
        return ''


def _engine_freshness(conn, table_name, label, max_age_hours=48):
    """Check if an engine has run recently. Returns (is_stale, summary)."""
    if not table_exists(conn, table_name):
        return True, f'{label}: table missing'
    row = conn.execute(
        f"SELECT MAX(created_at) FROM {table_name}"
    ).fetchone()
    if not row or not row[0]:
        return True, f'{label}: no data'
    try:
        last_run = datetime.fromisoformat(row[0])
        hours_ago = (datetime.now() - last_run).total_seconds() / 3600
        if hours_ago > max_age_hours:
            return True, f'{label}: last run {hours_ago:.0f}h ago (STALE)'
        return False, f'{label}: last run {hours_ago:.0f}h ago'
    except (ValueError, TypeError):
        return True, f'{label}: could not parse timestamp'


# ============================================================
# LENS 1: REVENUE TRAJECTORY
# ============================================================

def assess_revenue(conn):
    findings = []
    summaries = []

    # Product revenue from funnel_contacts
    if table_exists(conn, 'funnel_contacts'):
        total_revenue = conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM funnel_contacts"
        ).fetchone()[0]
        purchases_7d = conn.execute(
            "SELECT COUNT(*) FROM funnel_contacts WHERE purchased_at >= datetime('now', '-7 days')"
        ).fetchone()[0]
        purchases_30d = conn.execute(
            "SELECT COUNT(*) FROM funnel_contacts WHERE purchased_at >= datetime('now', '-30 days')"
        ).fetchone()[0]
        summaries.append(f'product: ${total_revenue / 100:,.2f} total, {purchases_7d} (7d), {purchases_30d} (30d)')

        if purchases_30d == 0:
            findings.append({
                'issue': 'Zero product purchases in 30 days. Product revenue channel is inactive.',
                'severity': 'warn',
                'lens': 'revenue',
            })

        # Deceleration check
        purchases_prev_7d = conn.execute(
            "SELECT COUNT(*) FROM funnel_contacts WHERE purchased_at >= datetime('now', '-14 days') "
            "AND purchased_at < datetime('now', '-7 days')"
        ).fetchone()[0]
        if purchases_7d < purchases_prev_7d and purchases_prev_7d > 0:
            findings.append({
                'issue': f'Product purchases decelerating: {purchases_7d} this week vs {purchases_prev_7d} last week.',
                'severity': 'info',
                'lens': 'revenue',
            })
    else:
        summaries.append('product: funnel_contacts table missing')

    # Conversion rate from leads
    if table_exists(conn, 'leads'):
        total_leads = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        converted = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE converted_at IS NOT NULL"
        ).fetchone()[0]
        summaries.append(f'conversion: {pct(converted, total_leads)} ({converted}/{total_leads})')

    # Trading PnL from VPS
    for name, data_dir in INSTANCES.items():
        ok, output = ssh_cmd(
            f'sqlite3 {data_dir}/performance.db '
            f'"SELECT COALESCE(SUM(pnl),0) FROM trades WHERE exit_time >= datetime(\'now\', \'-7 days\')" '
            f'2>/dev/null'
        )
        if ok and output:
            try:
                pnl_7d = float(output)
                summaries.append(f'{name}: ${pnl_7d:+.2f} (7d)')
                if pnl_7d < -50:
                    findings.append({
                        'issue': f'{name} trading PnL is ${pnl_7d:+.2f} this week. Review with COO.',
                        'severity': 'info',
                        'lens': 'revenue',
                    })
            except ValueError:
                pass

    return {
        'lens': 'revenue',
        'summary': ', '.join(summaries) if summaries else 'no revenue data',
        'findings': findings,
    }


# ============================================================
# LENS 2: OPERATIONAL HEALTH SCORE
# ============================================================

def assess_ops_health(conn):
    findings = []
    summaries = []

    # Read COO decisions if available (no coo_decisions table yet -- COO prints to stdout)
    # Instead, check engine freshness and derive health from CTO findings
    stale_engines = []
    for table, label, max_h in [
        ('cmo_decisions', 'CMO', 48),
        ('cto_decisions', 'CTO', 48),
    ]:
        is_stale, summary = _engine_freshness(conn, table, label, max_h)
        summaries.append(summary)
        if is_stale:
            stale_engines.append(label)

    if stale_engines:
        findings.append({
            'issue': f'Stale data from: {", ".join(stale_engines)}. Findings may be outdated.',
            'severity': 'warn',
            'lens': 'ops_health',
        })

    # Derive health score from CTO findings (last 7 days)
    score = 100
    if table_exists(conn, 'cto_decisions'):
        criticals = conn.execute(
            "SELECT COUNT(*) FROM cto_decisions WHERE severity='critical' "
            "AND created_at >= datetime('now', '-7 days')"
        ).fetchone()[0]
        warns = conn.execute(
            "SELECT COUNT(*) FROM cto_decisions WHERE severity='warn' "
            "AND created_at >= datetime('now', '-7 days')"
        ).fetchone()[0]
        score -= criticals * 20
        score -= warns * 5
        score = max(0, score)

        summaries.append(f'health score: {score}/100 ({criticals}C, {warns}W)')

        if score < 50:
            findings.append({
                'issue': f'Ops health score is {score}/100. Multiple critical issues unresolved.',
                'severity': 'critical',
                'lens': 'ops_health',
            })
        elif score < 75:
            findings.append({
                'issue': f'Ops health score is {score}/100. Some warnings accumulating.',
                'severity': 'warn',
                'lens': 'ops_health',
            })
    else:
        summaries.append('health score: no CTO data')

    return {
        'lens': 'ops_health',
        'summary': ', '.join(summaries),
        'findings': findings,
    }


# ============================================================
# LENS 3: TECHNICAL DEBT TREND
# ============================================================

def assess_tech_debt(conn):
    findings = []
    summaries = []

    if not table_exists(conn, 'cto_decisions'):
        return {'lens': 'tech_debt', 'summary': 'cto_decisions table missing', 'findings': []}

    # This week vs last week CTO findings
    this_week = conn.execute(
        "SELECT COUNT(*) FROM cto_decisions WHERE created_at >= datetime('now', '-7 days') "
        "AND severity IN ('critical', 'warn') AND lens != 'ai_insight'"
    ).fetchone()[0]
    last_week = conn.execute(
        "SELECT COUNT(*) FROM cto_decisions WHERE created_at >= datetime('now', '-14 days') "
        "AND created_at < datetime('now', '-7 days') AND severity IN ('critical', 'warn') "
        "AND lens != 'ai_insight'"
    ).fetchone()[0]

    if this_week > last_week and last_week > 0:
        direction = 'accumulating'
        findings.append({
            'issue': f'Tech debt accumulating: {this_week} issues this week vs {last_week} last week.',
            'severity': 'warn',
            'lens': 'tech_debt',
        })
    elif this_week < last_week:
        direction = 'paying down'
    else:
        direction = 'stable'

    summaries.append(f'debt direction: {direction} ({this_week} this week, {last_week} last week)')

    # Top recurring findings
    top_findings = conn.execute(
        "SELECT assessment, COUNT(*) as cnt FROM cto_decisions "
        "WHERE created_at >= datetime('now', '-30 days') AND severity IN ('critical', 'warn') "
        "AND lens != 'ai_insight' "
        "GROUP BY assessment ORDER BY cnt DESC LIMIT 3"
    ).fetchall()
    if top_findings:
        summaries.append('top issues: ' + ', '.join(
            f'{r["assessment"][:40]}({r["cnt"]}x)' for r in top_findings
        ))

    return {
        'lens': 'tech_debt',
        'summary': ', '.join(summaries),
        'findings': findings,
    }


# ============================================================
# LENS 4: MARKETING PIPELINE VELOCITY
# ============================================================

def assess_pipeline_velocity(conn):
    findings = []
    summaries = []

    # Read from leads table
    if table_exists(conn, 'leads'):
        total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        enriched = conn.execute("SELECT COUNT(*) FROM leads WHERE email IS NOT NULL").fetchone()[0]
        emailed = conn.execute("SELECT COUNT(*) FROM leads WHERE email_seq_started_at IS NOT NULL").fetchone()[0]
        replied = conn.execute("SELECT COUNT(*) FROM leads WHERE email_replied_at IS NOT NULL").fetchone()[0]

        summaries.append(f'funnel: {total} leads -> {enriched} enriched -> {emailed} emailed -> {replied} replied')

        # Stale pipeline
        days_since_load = conn.execute(
            "SELECT julianday('now') - julianday(MAX(created_at)) FROM leads"
        ).fetchone()[0]
        if days_since_load and days_since_load > 14:
            findings.append({
                'issue': f'No new prospects loaded in {int(days_since_load)} days. Pipeline is stalling.',
                'severity': 'warn',
                'lens': 'pipeline',
            })

        # Reply rate
        if emailed > 10:
            reply_rate = replied / emailed
            if reply_rate < 0.02:
                findings.append({
                    'issue': f'Email reply rate is {pct(replied, emailed)}. Below 2% floor.',
                    'severity': 'info',
                    'lens': 'pipeline',
                })

    # Content velocity from social_posts
    if table_exists(conn, 'social_posts'):
        posted_7d = conn.execute(
            "SELECT COUNT(*) FROM social_posts WHERE status='posted' "
            "AND posted_at >= datetime('now', '-7 days')"
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM social_posts WHERE status='pending'"
        ).fetchone()[0]
        summaries.append(f'social: {posted_7d} posted (7d), {pending} queued')

        if posted_7d == 0 and pending == 0:
            findings.append({
                'issue': 'Zero social posts published or queued. Content velocity is zero.',
                'severity': 'warn',
                'lens': 'pipeline',
            })

    # CMO activity
    if table_exists(conn, 'cmo_decisions'):
        cmo_actions_7d = conn.execute(
            "SELECT COUNT(*) FROM cmo_decisions WHERE action IS NOT NULL "
            "AND created_at >= datetime('now', '-7 days')"
        ).fetchone()[0]
        summaries.append(f'CMO actions (7d): {cmo_actions_7d}')

    return {
        'lens': 'pipeline',
        'summary': ', '.join(summaries),
        'findings': findings,
    }


# ============================================================
# CROSS-FUNCTIONAL INSIGHTS
# ============================================================

def ensure_coordination_tables(conn):
    """Create Level 3 coordination tables if they don't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_agent TEXT NOT NULL,
            to_agent TEXT NOT NULL,
            message TEXT NOT NULL,
            priority TEXT DEFAULT 'normal',
            read_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assigned_to TEXT NOT NULL,
            task TEXT NOT NULL,
            priority INTEGER DEFAULT 5,
            status TEXT DEFAULT 'pending',
            created_by TEXT NOT NULL,
            result TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT
        )
    """)
    conn.commit()


def send_message(conn, to_agent, message, priority='normal'):
    """CEO sends a message to another agent."""
    conn.execute("""
        INSERT INTO agent_messages (from_agent, to_agent, message, priority)
        VALUES ('ceo', ?, ?, ?)
    """, (to_agent, message, priority))
    conn.commit()


def create_task(conn, assigned_to, task, priority=5):
    """CEO creates a task for another agent."""
    conn.execute("""
        INSERT INTO task_queue (assigned_to, task, priority, created_by)
        VALUES (?, ?, ?, 'ceo')
    """, (assigned_to, task, priority))
    conn.commit()


def get_unread_messages(conn, agent='ceo'):
    """Get unread messages for an agent."""
    if not table_exists(conn, 'agent_messages'):
        return []
    rows = conn.execute("""
        SELECT * FROM agent_messages
        WHERE to_agent=? AND read_at IS NULL
        ORDER BY created_at DESC
    """, (agent,)).fetchall()
    return [dict(r) for r in rows]


def mark_messages_read(conn, agent='ceo'):
    """Mark all messages to an agent as read."""
    if not table_exists(conn, 'agent_messages'):
        return
    conn.execute("""
        UPDATE agent_messages SET read_at=datetime('now')
        WHERE to_agent=? AND read_at IS NULL
    """, (agent,))
    conn.commit()


def generate_cross_functional(all_findings, lens_results):
    """Generate cross-functional insights by detecting conflicts between lenses."""
    insights = []

    # Extract lens summaries for pattern matching
    lens_data = {r['lens']: r for r in lens_results}

    # Check for conflicts
    has_tech_issues = any(
        f['severity'] in ('critical', 'warn') and f.get('lens') in ('tech_debt', 'ops_health')
        for f in all_findings
    )
    has_pipeline_stall = any(
        'stalling' in f.get('issue', '').lower() or 'zero' in f.get('issue', '').lower()
        for f in all_findings if f.get('lens') == 'pipeline'
    )
    has_revenue_decline = any(
        'decelerat' in f.get('issue', '').lower() or 'inactive' in f.get('issue', '').lower()
        for f in all_findings if f.get('lens') == 'revenue'
    )
    has_trading_loss = any(
        'PnL' in f.get('issue', '') and '-' in f.get('issue', '')
        for f in all_findings if f.get('lens') == 'revenue'
    )

    # Cross-functional conflict detection
    if has_tech_issues and has_pipeline_stall:
        insights.append(
            'Tech issues AND pipeline stalled. Fix technical debt first -- '
            'marketing push on a broken system wastes effort.'
        )

    if has_revenue_decline and has_pipeline_stall:
        insights.append(
            'Revenue declining AND pipeline stalled. Both growth engines are cold. '
            'Priority: load new prospects to restart the funnel.'
        )

    if has_trading_loss and not has_revenue_decline:
        insights.append(
            'Trading drawdown but product/marketing stable. '
            'Trading losses are cyclical. Do not redirect marketing resources to fix trading.'
        )

    if has_tech_issues and not has_pipeline_stall and not has_revenue_decline:
        insights.append(
            'Tech debt accumulating but business metrics stable. '
            'Schedule a tech debt sprint before it compounds into production failures.'
        )

    if not all_findings:
        insights.append('All systems nominal across all departments. Maintain current trajectory.')

    return insights


def orchestrate_messages(conn, all_findings, insights):
    """CEO orchestrator: send messages to agents based on cross-functional analysis.

    Returns list of messages sent for display in digest.
    """
    messages_sent = []

    has_tech_criticals = any(
        f['severity'] == 'critical' and f.get('lens') in ('tech_debt', 'ops_health')
        for f in all_findings
    )
    has_pipeline_stall = any(
        'stalling' in f.get('issue', '').lower() or 'zero' in f.get('issue', '').lower()
        for f in all_findings if f.get('lens') == 'pipeline'
    )
    has_revenue_zero = any(
        'inactive' in f.get('issue', '').lower() or 'zero' in f.get('issue', '').lower()
        for f in all_findings if f.get('lens') == 'revenue'
    )

    # Tech critical: pause CMO marketing push, alert CTO
    if has_tech_criticals:
        msg = 'CRITICAL tech issues detected. Pause non-essential marketing actions until resolved.'
        send_message(conn, 'cmo', msg, priority='urgent')
        messages_sent.append(('cmo', msg, 'urgent'))

        msg = 'CRITICAL findings from cross-functional review. Prioritize fix before next deploy.'
        send_message(conn, 'cto', msg, priority='urgent')
        messages_sent.append(('cto', msg, 'urgent'))

    # Pipeline stalled: task CMO to load prospects
    if has_pipeline_stall and not has_tech_criticals:
        task = 'Load new prospects to restart pipeline. Pipeline velocity is zero.'
        create_task(conn, 'cmo', task, priority=2)
        messages_sent.append(('cmo', f'[TASK] {task}', 'normal'))

    # Revenue zero: coordinate CMO + COO
    if has_revenue_zero:
        msg = 'Product revenue channel inactive for 30+ days. Review product pricing and funnel.'
        send_message(conn, 'cmo', msg, priority='normal')
        messages_sent.append(('cmo', msg, 'normal'))

        msg = 'Revenue at zero. Check VPS health and trading bot performance.'
        send_message(conn, 'coo', msg, priority='normal')
        messages_sent.append(('coo', msg, 'normal'))

    return messages_sent


# ============================================================
# WEEKLY COMMAND
# ============================================================

def cmd_weekly(args):
    conn = get_db()
    ensure_decisions_table(conn)
    ensure_coordination_tables(conn)
    run_id = str(uuid.uuid4())[:8]

    # Check for incoming messages to CEO
    incoming = get_unread_messages(conn, 'ceo')
    if incoming:
        print(f'CEO has {len(incoming)} unread messages:')
        for m in incoming:
            print(f'  [{m["priority"].upper()}] from {m["from_agent"].upper()}: {m["message"][:80]}')
        print()
        mark_messages_read(conn, 'ceo')

    lenses_to_run = ['revenue', 'ops_health', 'tech_debt', 'pipeline']
    if args.focus:
        lenses_to_run = [args.focus]

    assessors = {
        'revenue': assess_revenue,
        'ops_health': assess_ops_health,
        'tech_debt': assess_tech_debt,
        'pipeline': assess_pipeline_velocity,
    }

    all_findings = []
    lens_results = []
    digest_lines = [
        'CEO WEEKLY BRIEF',
        '=' * 50,
        f'Run: {run_id} | {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}',
        '',
    ]

    for lens_name in lenses_to_run:
        if lens_name not in assessors:
            continue
        result = assessors[lens_name](conn)
        lens_results.append(result)
        digest_lines.append(f'## {lens_name.upper()}')
        digest_lines.append(f'   {result["summary"]}')

        for f in result['findings']:
            all_findings.append(f)
            sev = f['severity'].upper()
            digest_lines.append(f'   [{sev}] {f["issue"]}')

        if not result['findings']:
            digest_lines.append('   All clear.')
        digest_lines.append('')

    # Cross-functional insights
    insights = generate_cross_functional(all_findings, lens_results)
    if insights:
        digest_lines.append('## CROSS-FUNCTIONAL INSIGHTS')
        for i, insight in enumerate(insights, 1):
            digest_lines.append(f'   {i}. {insight}')
        digest_lines.append('')

    # Memory trends (Level 1 pattern detection)
    memory_context = ''
    try:
        from memory_engine import get_trend, get_recurring_findings, get_db as mem_get_db
        mem_conn = mem_get_db()
        trend_lines = []
        for agent_label, table in [('CMO', 'cmo_decisions'), ('CTO', 'cto_decisions'),
                                    ('COO', 'coo_decisions')]:
            direction, this_w, last_w = get_trend(mem_conn, table)
            if direction == 'up':
                trend_lines.append(f'{agent_label} findings INCREASING ({this_w} this week vs {last_w} last)')
                all_findings.append({
                    'issue': f'{agent_label} findings trending up: {this_w} this week vs {last_w} last week',
                    'severity': 'info',
                    'lens': 'memory_trends',
                })
            recurring = get_recurring_findings(mem_conn, table)
            for r in recurring[:2]:
                trend_lines.append(f'{agent_label} recurring (x{r["occurrences"]}): {r["assessment"][:60]}')

        if trend_lines:
            digest_lines.append('## MEMORY PATTERNS')
            for line in trend_lines:
                digest_lines.append(f'   {line}')
            digest_lines.append('')
            memory_context = '\n'.join(trend_lines)

        mem_conn.close()
    except Exception:
        pass

    # Level 3: Orchestrator message dispatch
    messages_sent = orchestrate_messages(conn, all_findings, insights)
    if messages_sent:
        digest_lines.append('## ORCHESTRATOR MESSAGES')
        for to_agent, msg, priority in messages_sent:
            digest_lines.append(f'   -> {to_agent.upper()} [{priority}]: {msg[:70]}')
        digest_lines.append('')

    # LLM Synthesis
    if all_findings:
        # Build context from lens summaries + memory patterns
        context = '\n'.join(f'{r["lens"]}: {r["summary"]}' for r in lens_results)
        if memory_context:
            context += f'\n\nRECURRING PATTERNS:\n{memory_context}'
        ai_insight = _llm_synthesize(all_findings, context=context)
        if ai_insight:
            digest_lines.append('## AI STRATEGIC INSIGHT')
            digest_lines.append(f'   {ai_insight}')
            digest_lines.append('')

            conn.execute("""
                INSERT INTO ceo_decisions (run_id, lens, assessment, severity, insight, recommendation)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (run_id, 'ai_insight', ai_insight, 'info', ai_insight, None))

    # Log all findings
    for f in all_findings:
        conn.execute("""
            INSERT INTO ceo_decisions (run_id, lens, assessment, severity, insight, recommendation)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            run_id,
            f.get('lens', 'unknown'),
            f['issue'],
            f['severity'],
            None,
            None,
        ))
    conn.commit()

    # Summary
    criticals = sum(1 for f in all_findings if f['severity'] == 'critical')
    warns = sum(1 for f in all_findings if f['severity'] == 'warn')

    digest_lines.append('## VERDICT')
    if criticals > 0:
        digest_lines.append(f'   EXECUTIVE ACTION REQUIRED: {criticals} critical, {warns} warnings')
    elif warns > 0:
        digest_lines.append(f'   MONITORING: {warns} items need attention')
    else:
        digest_lines.append('   ALL SYSTEMS NOMINAL')
    digest_lines.append('=' * 50)

    digest = '\n'.join(digest_lines)
    print(digest)

    slack_post(digest)

    # Auto-reflect (Level 2)
    try:
        from reflection_engine import reflect_on_agent, ensure_reflections_table
        ensure_reflections_table(conn)
        reflect_on_agent(conn, 'ceo')
    except Exception as e:
        print(f'\nAuto-reflect skipped: {e}')

    conn.close()


# ============================================================
# HISTORY COMMAND
# ============================================================

def cmd_history(args):
    conn = get_db()
    ensure_decisions_table(conn)

    rows = conn.execute("""
        SELECT run_id, lens, assessment, severity, insight, created_at
        FROM ceo_decisions
        ORDER BY created_at DESC
        LIMIT 10
    """).fetchall()

    if not rows:
        print("No CEO decisions logged yet.")
        conn.close()
        return

    print()
    print("CEO DECISION HISTORY (last 10)")
    print("=" * 70)
    for r in rows:
        sev = r['severity'].upper()
        print(f"  [{r['created_at'][:16]}] [{sev}] {r['run_id']} | {r['lens']}")
        print(f"    {r['assessment'][:80]}")
        if r['insight']:
            print(f"    insight: {r['insight'][:60]}")
        print()
    print("=" * 70)
    conn.close()


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='CEO Engine')
    parser.add_argument('command', nargs='?', default='weekly',
                        choices=['weekly', 'history'])
    parser.add_argument('--focus', choices=['revenue', 'ops_health', 'tech_debt', 'pipeline'])
    args = parser.parse_args()

    if args.command == 'weekly':
        cmd_weekly(args)
    elif args.command == 'history':
        cmd_history(args)


if __name__ == '__main__':
    main()
