#!/usr/bin/env python3
"""COO Engine — Autonomous Chief Operating Officer for OpenClaw.

Monitors trading bots, VPS infrastructure, OAuth tokens, and cron jobs.
Produces daily ops digest. Alerts on anomalies. Escalates critical issues.

Usage:
    python3 scripts/coo_engine.py daily            # Full 4-lens ops assessment
    python3 scripts/coo_engine.py trading           # Trading lens only
    python3 scripts/coo_engine.py infra             # Infrastructure lens only
    python3 scripts/coo_engine.py --format slack    # Output as Slack message
"""

import argparse
import json
import os
import re
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

DRY_RUN = os.environ.get('DRY_RUN', 'true').lower() == 'true'

SLACK_BOT_TOKEN = os.environ.get('SLACK_BOT_TOKEN', '')
SLACK_CHANNEL_ID = os.environ.get('SLACK_CHANNEL_ID', '')

VPS_HOST = os.environ.get('VPS_HOST', '82.24.19.114')
VPS_USER = os.environ.get('VPS_USER', 'root')

# Trading DB paths (on VPS)
INSTANCES = {
    'emmanuel': {
        'service': 'lagbot@emmanuel',
        'data_dir': '/opt/lagbot/instances/emmanuel/data',
        'env_file': '/opt/lagbot/instances/emmanuel/.env',
        'expected_running': True,
    },
    'polyphemus': {
        'service': 'lagbot@polyphemus',
        'data_dir': '/opt/lagbot/instances/polyphemus/data',
        'env_file': '/opt/lagbot/instances/polyphemus/.env',
        'expected_running': False,
    },
}

OPENCLAW_SCRIPTS = {
    'weather_edge': {'log': '/opt/openclaw/logs/weather.log', 'interval_hours': 6},
    'sports_arb': {'log': '/opt/openclaw/logs/sports.log', 'interval_hours': 14},
    'resolution_sniper': {'log': '/opt/openclaw/logs/sniper.log', 'interval_hours': 8},
}

# Marketing-agent DB (local)
LEADS_DB_PATH = os.environ.get(
    'LEADS_DB_PATH',
    os.path.join(os.path.dirname(__file__), '..', 'data', 'marketing_leads.db')
)

# LinkedIn token env var
LINKEDIN_TOKEN_EXPIRES_AT = os.environ.get('LINKEDIN_TOKEN_EXPIRES_AT', '')
PINTEREST_ACCESS_TOKEN = os.environ.get('PINTEREST_ACCESS_TOKEN', '')


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
        return None
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
        CREATE TABLE IF NOT EXISTS coo_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            lens TEXT,
            assessment TEXT,
            severity TEXT,
            action TEXT,
            outcome TEXT,
            dry_run BOOLEAN DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def _get_lessons(agent='coo'):
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


def _llm_synthesize(findings):
    """Call Claude Haiku to synthesize ops findings into strategic insight."""
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

        prompt = (
            "You are the COO of a solo-founder tech startup running "
            "a Polymarket trading bot (lagbot) and marketing system (OpenClaw).\n"
            "Given these findings from today's automated ops assessment, provide "
            "2-3 sentences of operational insight. Focus on: what is the single "
            "most urgent operational issue, and what action to take. "
            "Be direct, no filler.\n\n"
        )
        lessons = _get_lessons('coo')
        if lessons:
            prompt += "LESSONS FROM YOUR PAST SELF-CRITIQUE:\n"
            prompt += '\n'.join(f"- {l}" for l in lessons) + "\n\n"
        prompt += f"FINDINGS:\n{findings_text}"

        response = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=500,
            messages=[{'role': 'user', 'content': prompt}]
        )
        return response.content[0].text
    except Exception:
        return ''


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
    """Run a command on VPS via SSH. Returns (success, output)."""
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


# ============================================================
# LENS 1: TRADING HEALTH
# ============================================================

def assess_trading():
    findings = []
    summaries = []

    for name, cfg in INSTANCES.items():
        # Check service status
        ok, status = ssh_cmd(f'systemctl is-active {cfg["service"]}')
        is_running = status.strip() == 'active'

        if cfg['expected_running'] and not is_running:
            findings.append({
                'issue': f'{name}: service {cfg["service"]} is DOWN (expected running)',
                'severity': 'critical',
                'action': f'systemctl restart {cfg["service"]}',
                'needs_approval': True,
            })
        elif not cfg['expected_running'] and is_running:
            findings.append({
                'issue': f'{name}: service {cfg["service"]} is RUNNING (expected stopped)',
                'severity': 'warn',
                'action': None,
                'needs_approval': False,
            })

        status_label = 'RUNNING' if is_running else 'STOPPED'
        intent = '(intended)' if not cfg['expected_running'] and not is_running else ''

        # Get recent trade count and PnL if running
        trade_info = ''
        if is_running:
            ok2, output = ssh_cmd(
                f'sqlite3 {cfg["data_dir"]}/performance.db '
                f'"SELECT COUNT(*), COALESCE(SUM(pnl),0) FROM trades WHERE date(exit_time) >= date(\'now\', \'-1 day\')"'
            )
            if ok2 and output:
                parts = output.split('|')
                if len(parts) == 2:
                    count, pnl = parts
                    trade_info = f', {count} trades today, PnL ${float(pnl):+.2f}'

        # Check for recent errors in logs
        if is_running:
            ok3, errors = ssh_cmd(
                f'journalctl -u {cfg["service"]} --since "1 hour ago" --no-pager 2>/dev/null | grep -ciE "error|traceback"'
            )
            if ok3 and errors.strip().isdigit():
                error_count = int(errors.strip())
                if error_count > 5:
                    findings.append({
                        'issue': f'{name}: {error_count} errors in last hour',
                        'severity': 'warn',
                        'action': None,
                        'needs_approval': False,
                    })

        summaries.append(f'{name}: {status_label} {intent}{trade_info}')

    # OpenClaw strategies
    for script, cfg in OPENCLAW_SCRIPTS.items():
        ok, output = ssh_cmd(f'stat -c %Y {cfg["log"]} 2>/dev/null || echo 0')
        if ok and output != '0':
            try:
                last_mod = int(output)
                now = int(datetime.now(timezone.utc).timestamp())
                hours_ago = (now - last_mod) / 3600
                if hours_ago > cfg['interval_hours'] * 2:
                    findings.append({
                        'issue': f'{script}: log not updated in {hours_ago:.0f}h (expected every {cfg["interval_hours"]}h)',
                        'severity': 'warn',
                        'action': None,
                        'needs_approval': False,
                    })
                summaries.append(f'{script}: last run {hours_ago:.1f}h ago')
            except ValueError:
                summaries.append(f'{script}: could not parse log timestamp')
        else:
            summaries.append(f'{script}: log not found')

    return {
        'lens': 'trading',
        'summary': '; '.join(summaries),
        'findings': findings,
    }


# ============================================================
# LENS 2: INFRASTRUCTURE
# ============================================================

def assess_infra():
    findings = []
    summaries = []

    # VPS connectivity
    ok, _ = ssh_cmd('echo ok')
    if not ok:
        findings.append({
            'issue': f'VPS {VPS_HOST} is unreachable via SSH',
            'severity': 'critical',
            'action': None,
            'needs_approval': False,
        })
        return {'lens': 'infra', 'summary': 'VPS UNREACHABLE', 'findings': findings}

    # Disk usage
    ok, disk_output = ssh_cmd("df -h / | tail -1 | awk '{print $5}'")
    if ok:
        disk_pct = disk_output.replace('%', '')
        try:
            if int(disk_pct) > 85:
                findings.append({
                    'issue': f'Disk usage at {disk_output} (above 85% threshold)',
                    'severity': 'warn',
                    'action': None,
                    'needs_approval': False,
                })
            summaries.append(f'disk {disk_output}')
        except ValueError:
            summaries.append(f'disk {disk_output}')

    # Memory usage
    ok, mem_output = ssh_cmd("free -m | awk '/Mem:/ {printf \"%.0f\", $3/$2*100}'")
    if ok:
        try:
            mem_pct = int(mem_output)
            if mem_pct > 90:
                findings.append({
                    'issue': f'Memory usage at {mem_pct}% (above 90% threshold)',
                    'severity': 'warn',
                    'action': None,
                    'needs_approval': False,
                })
            summaries.append(f'memory {mem_pct}%')
        except ValueError:
            summaries.append(f'memory {mem_output}')

    # Uptime
    ok, uptime = ssh_cmd("uptime -p")
    if ok:
        summaries.append(uptime)

    # Cron health - check if crontab has expected entries
    ok, crontab = ssh_cmd("crontab -l 2>/dev/null | grep -c 'python3'")
    if ok:
        try:
            cron_count = int(crontab.strip())
            summaries.append(f'{cron_count} Python cron jobs')
            if cron_count < 5:
                findings.append({
                    'issue': f'Only {cron_count} Python cron jobs found (expected 8+)',
                    'severity': 'warn',
                    'action': None,
                    'needs_approval': False,
                })
        except ValueError:
            pass

    return {
        'lens': 'infra',
        'summary': ', '.join(summaries),
        'findings': findings,
    }


# ============================================================
# LENS 3: TOKEN + CREDENTIAL HEALTH
# ============================================================

def assess_tokens():
    findings = []
    summaries = []

    # LinkedIn OAuth (60-day token)
    if LINKEDIN_TOKEN_EXPIRES_AT:
        try:
            expires = datetime.fromisoformat(LINKEDIN_TOKEN_EXPIRES_AT.replace('Z', '+00:00'))
            days_left = (expires - datetime.now(timezone.utc)).days
            if days_left <= 0:
                findings.append({
                    'issue': f'LinkedIn OAuth token EXPIRED ({days_left} days ago)',
                    'severity': 'critical',
                    'action': None,
                    'needs_approval': False,
                })
            elif days_left <= 10:
                findings.append({
                    'issue': f'LinkedIn OAuth token expires in {days_left} days. Refresh at developers.linkedin.com',
                    'severity': 'warn',
                    'action': None,
                    'needs_approval': False,
                })
            summaries.append(f'LinkedIn: {days_left}d remaining')
        except (ValueError, TypeError):
            summaries.append('LinkedIn: could not parse expiry')
    else:
        summaries.append('LinkedIn: no expiry date set')

    # Pinterest
    if PINTEREST_ACCESS_TOKEN:
        summaries.append('Pinterest: token configured')
    else:
        summaries.append('Pinterest: no token')

    # Twitter (OAuth 1.0a, no expiry)
    summaries.append('Twitter: no expiry (OAuth 1.0a)')

    # Odds API quota
    ok, output = ssh_cmd("grep -c 'ODDS_API_KEY' /opt/openclaw/.env 2>/dev/null")
    if ok and output.strip() != '0':
        summaries.append('Odds API: key present')

    return {
        'lens': 'tokens',
        'summary': ', '.join(summaries),
        'findings': findings,
    }


# ============================================================
# LENS 4: FINANCIAL SUMMARY
# ============================================================

def assess_financial():
    findings = []
    summaries = []

    # Get wallet balances for running instances
    for name, cfg in INSTANCES.items():
        ok, output = ssh_cmd(
            f'sqlite3 {cfg["data_dir"]}/performance.db '
            f'"SELECT COALESCE(SUM(pnl),0) FROM trades WHERE exit_time >= datetime(\'now\', \'-7 days\')" 2>/dev/null'
        )
        if ok and output:
            try:
                pnl_7d = float(output)
                summaries.append(f'{name}: ${pnl_7d:+.2f} (7d)')
            except ValueError:
                summaries.append(f'{name}: no data')
        else:
            summaries.append(f'{name}: DB unavailable')

    # Marketing spend (if tracked)
    if os.path.exists(LEADS_DB_PATH):
        conn = sqlite3.connect(LEADS_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            if conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='funnel_contacts'"
            ).fetchone():
                revenue = conn.execute(
                    "SELECT COALESCE(SUM(amount_cents),0) FROM funnel_contacts"
                ).fetchone()[0]
                summaries.append(f'product revenue: ${revenue / 100:,.2f}')
        except Exception:
            pass
        conn.close()

    return {
        'lens': 'financial',
        'summary': ', '.join(summaries) if summaries else 'no financial data available',
        'findings': findings,
    }


# ============================================================
# DAILY COMMAND
# ============================================================

def cmd_daily(args):
    mode_label = 'DRY RUN' if DRY_RUN else 'LIVE'
    run_id = str(uuid.uuid4())[:8]

    lenses_to_run = ['trading', 'infra', 'tokens', 'financial']
    if args.focus:
        lenses_to_run = [args.focus]

    assessors = {
        'trading': assess_trading,
        'infra': assess_infra,
        'tokens': assess_tokens,
        'financial': assess_financial,
    }

    # DB setup
    conn = get_db()
    if conn:
        ensure_decisions_table(conn)

    # Check for incoming messages (Level 3)
    if conn:
        try:
            if table_exists(conn, 'agent_messages'):
                unread = conn.execute(
                    "SELECT from_agent, message, priority FROM agent_messages "
                    "WHERE to_agent='coo' AND read_at IS NULL ORDER BY created_at DESC"
                ).fetchall()
                if unread:
                    print(f'INBOX: {len(unread)} unread messages')
                    for msg in unread:
                        print(f'  [{msg["priority"]}] from {msg["from_agent"]}: {msg["message"][:70]}')
                    conn.execute(
                        "UPDATE agent_messages SET read_at=datetime('now') "
                        "WHERE to_agent='coo' AND read_at IS NULL"
                    )
                    conn.commit()
                    print()
        except Exception:
            pass

    all_findings = []
    digest_lines = [
        f'COO OPS DIGEST [{mode_label}]',
        '=' * 50,
        f'Run: {run_id} | {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}',
        '',
    ]

    for lens_name in lenses_to_run:
        if lens_name not in assessors:
            continue
        result = assessors[lens_name]()
        digest_lines.append(f'## {lens_name.upper()}')
        digest_lines.append(f'   {result["summary"]}')

        for f in result['findings']:
            all_findings.append(f)
            sev = f['severity'].upper()
            digest_lines.append(f'   [{sev}] {f["issue"]}')
            if f.get('action'):
                approval = ' (NEEDS APPROVAL)' if f.get('needs_approval') else ''
                digest_lines.append(f'   -> Recommended: {f["action"]}{approval}')

            # Log to DB
            if conn:
                conn.execute("""
                    INSERT INTO coo_decisions (run_id, lens, assessment, severity, action, outcome, dry_run)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (run_id, lens_name, f['issue'], f['severity'], f.get('action'),
                      'REPORTED', DRY_RUN))

        if not result['findings']:
            digest_lines.append('   All clear.')
        digest_lines.append('')

    if conn:
        conn.commit()

    # Summary
    criticals = sum(1 for f in all_findings if f['severity'] == 'critical')
    warns = sum(1 for f in all_findings if f['severity'] == 'warn')

    # LLM Synthesis
    if all_findings:
        ai_insight = _llm_synthesize(all_findings)
        if ai_insight:
            digest_lines.append('## AI INSIGHT')
            digest_lines.append(f'   {ai_insight}')
            digest_lines.append('')

            if conn:
                conn.execute("""
                    INSERT INTO coo_decisions (run_id, lens, assessment, severity, action, outcome, dry_run)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (run_id, 'ai_insight', ai_insight, 'info', None, 'LLM_SYNTHESIS', DRY_RUN))
                conn.commit()

    digest_lines.append('## VERDICT')
    if criticals > 0:
        digest_lines.append(f'   ATTENTION NEEDED: {criticals} critical, {warns} warnings')
    elif warns > 0:
        digest_lines.append(f'   MONITORING: {warns} warnings, no criticals')
    else:
        digest_lines.append('   ALL SYSTEMS NOMINAL')
    digest_lines.append('=' * 50)

    digest = '\n'.join(digest_lines)
    print(digest)

    # Slack alert for criticals
    if criticals > 0:
        critical_msgs = [f['issue'] for f in all_findings if f['severity'] == 'critical']
        slack_post(f'COO ALERT: {criticals} CRITICAL issues\n' + '\n'.join(f'- {m}' for m in critical_msgs))
    elif args.format == 'slack':
        slack_post(digest)

    # Auto-reflect (Level 2)
    if conn:
        try:
            from reflection_engine import reflect_on_agent, ensure_reflections_table
            ensure_reflections_table(conn)
            reflect_on_agent(conn, 'coo')
        except Exception as e:
            print(f'\nAuto-reflect skipped: {e}')
        conn.close()


# ============================================================
# TRADING COMMAND
# ============================================================

def cmd_trading(args):
    args.focus = 'trading'
    cmd_daily(args)


# ============================================================
# INFRA COMMAND
# ============================================================

def cmd_infra(args):
    args.focus = 'infra'
    cmd_daily(args)


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='COO Engine')
    parser.add_argument('command', nargs='?', default='daily',
                        choices=['daily', 'trading', 'infra'])
    parser.add_argument('--focus', choices=['trading', 'infra', 'tokens', 'financial'])
    parser.add_argument('--format', default='text', choices=['text', 'slack'])
    args = parser.parse_args()

    if args.command == 'daily':
        cmd_daily(args)
    elif args.command == 'trading':
        cmd_trading(args)
    elif args.command == 'infra':
        cmd_infra(args)


if __name__ == '__main__':
    main()
