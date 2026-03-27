#!/usr/bin/env python3
"""CTO Engine -- Autonomous Chief Technology Officer for OpenClaw.

Monitors code quality, deploy integrity, dependency health, and git hygiene.
Produces daily tech digest. Alerts on drift. NEVER deploys or modifies code.

Usage:
    python3 scripts/cto_engine.py daily            # Full 4-lens assessment
    python3 scripts/cto_engine.py history           # Show last 20 decisions
    python3 scripts/cto_engine.py --focus code      # Run single lens
"""

import argparse
import hashlib
import os
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone

import requests

try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'tools'))
    from api_telemetry import log_usage as _log_usage
except ImportError:
    _log_usage = None

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

# Paths
LOCAL_CODEBASE = os.environ.get(
    'LOCAL_CODEBASE',
    os.path.expanduser('~/Projects/business/polyphemus')
)
VPS_CODEBASE = '/opt/lagbot/lagbot'
VPS_VENV = '/opt/lagbot/venv/bin'


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
        CREATE TABLE IF NOT EXISTS cto_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            lens TEXT NOT NULL,
            assessment TEXT NOT NULL,
            severity TEXT NOT NULL,
            file_path TEXT,
            action TEXT,
            outcome TEXT,
            dry_run BOOLEAN DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


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


def _get_lessons(agent='cto'):
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


def _llm_synthesize(findings, engine='CTO'):
    """Call Claude Haiku to synthesize findings into strategic insight.

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

        prompt = (
            f"You are the {engine} of a solo-founder tech startup running "
            f"a Polymarket trading bot and marketing system.\n"
            f"Given these findings from today's automated assessment, provide "
            f"2-3 sentences of strategic insight. Focus on: what is the single "
            f"most important thing to act on, and why. Be direct, no filler.\n\n"
        )
        lessons = _get_lessons(engine.lower())
        if lessons:
            prompt += "LESSONS FROM YOUR PAST SELF-CRITIQUE:\n"
            prompt += '\n'.join(f"- {l}" for l in lessons) + "\n\n"
        prompt += f"FINDINGS:\n{findings_text}"

        response = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=500,
            messages=[{'role': 'user', 'content': prompt}]
        )
        if _log_usage:
            _log_usage("cto_engine", response)
        return response.content[0].text
    except Exception:
        return ''


# ============================================================
# LENS 1: CODE QUALITY
# ============================================================

def assess_code_quality():
    findings = []
    summaries = []

    # py_compile all .py files on VPS
    ok, output = ssh_cmd(
        f'cd /tmp && for f in {VPS_CODEBASE}/*.py; do '
        f'python3 -m py_compile "$f" 2>&1 || echo "FAIL:$f"; done',
        timeout=30,
    )
    if ok:
        fail_count = output.count('FAIL:')
        if fail_count > 0:
            failed_files = [
                line.split('FAIL:')[1].strip()
                for line in output.split('\n') if 'FAIL:' in line
            ]
            findings.append({
                'issue': f'{fail_count} files fail py_compile: {", ".join(f.split("/")[-1] for f in failed_files[:3])}',
                'severity': 'critical',
                'file_path': failed_files[0] if failed_files else None,
            })
        summaries.append(f'py_compile: {fail_count} failures')
    else:
        summaries.append(f'py_compile: SSH failed ({output[:50]})')

    # Count Python errors in recent logs
    ok, error_output = ssh_cmd(
        'journalctl -u "lagbot@*" --since "1 hour ago" --no-pager 2>/dev/null | '
        'grep -ciE "ImportError|SyntaxError|AttributeError|NameError|TypeError" || echo 0'
    )
    if ok:
        try:
            error_count = int(error_output.strip())
            if error_count > 5:
                findings.append({
                    'issue': f'{error_count} code errors in last hour (ImportError/SyntaxError/AttributeError/NameError/TypeError)',
                    'severity': 'warn',
                    'file_path': None,
                })
            summaries.append(f'code errors (1h): {error_count}')
        except ValueError:
            summaries.append('code errors: parse failed')

    # TODO/FIXME density (local codebase)
    if os.path.isdir(LOCAL_CODEBASE):
        todo_count = 0
        py_files = 0
        for f in os.listdir(LOCAL_CODEBASE):
            if f.endswith('.py'):
                py_files += 1
                try:
                    with open(os.path.join(LOCAL_CODEBASE, f)) as fh:
                        content = fh.read()
                        todo_count += content.upper().count('TODO')
                        todo_count += content.upper().count('FIXME')
                        todo_count += content.upper().count('HACK')
                except Exception:
                    pass
        if todo_count > 20:
            findings.append({
                'issue': f'{todo_count} TODO/FIXME/HACK markers across {py_files} files',
                'severity': 'info',
                'file_path': None,
            })
        summaries.append(f'TODO/FIXME: {todo_count}')

    return {
        'lens': 'code',
        'summary': ', '.join(summaries),
        'findings': findings,
    }


# ============================================================
# LENS 2: DEPLOY INTEGRITY
# ============================================================

def assess_deploy_integrity():
    findings = []
    summaries = []

    if not os.path.isdir(LOCAL_CODEBASE):
        return {'lens': 'deploy', 'summary': f'local codebase not found: {LOCAL_CODEBASE}', 'findings': []}

    # Checksum comparison: local vs VPS
    local_files = {}
    for f in os.listdir(LOCAL_CODEBASE):
        if f.endswith('.py') and not f.startswith('test_'):
            fpath = os.path.join(LOCAL_CODEBASE, f)
            try:
                with open(fpath, 'rb') as fh:
                    local_files[f] = hashlib.md5(fh.read()).hexdigest()
            except Exception:
                pass

    drift_files = []
    if local_files:
        file_list = ' '.join(f'{VPS_CODEBASE}/{f}' for f in local_files)
        ok, md5_output = ssh_cmd(f'md5sum {file_list} 2>/dev/null', timeout=20)
        if ok:
            for line in md5_output.split('\n'):
                if not line.strip():
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    vps_hash = parts[0]
                    vps_file = parts[1].split('/')[-1]
                    if vps_file in local_files and local_files[vps_file] != vps_hash:
                        drift_files.append(vps_file)

    if drift_files:
        findings.append({
            'issue': f'{len(drift_files)} files differ local vs VPS: {", ".join(drift_files[:5])}',
            'severity': 'warn',
            'file_path': drift_files[0],
        })
    summaries.append(f'checksum drift: {len(drift_files)} files')

    # __pycache__ check
    ok, cache_output = ssh_cmd(
        f'find {VPS_CODEBASE}/__pycache__ -name "*.pyc" -newer {VPS_CODEBASE}/signal_bot.py 2>/dev/null | wc -l'
    )
    if ok:
        try:
            stale = int(cache_output.strip())
            if stale == 0:
                ok2, cache_exists = ssh_cmd(f'ls {VPS_CODEBASE}/__pycache__ 2>/dev/null | wc -l')
                if ok2 and int(cache_exists.strip()) > 0:
                    findings.append({
                        'issue': '__pycache__ is older than source files. May be running stale bytecode.',
                        'severity': 'warn',
                        'file_path': f'{VPS_CODEBASE}/__pycache__',
                    })
        except ValueError:
            pass

    # Last deploy timestamp (most recent .py mtime on VPS)
    ok, mtime_output = ssh_cmd(
        f'stat -c %Y {VPS_CODEBASE}/signal_bot.py 2>/dev/null'
    )
    if ok:
        try:
            last_deploy = int(mtime_output.strip())
            now = int(datetime.now(timezone.utc).timestamp())
            days_ago = (now - last_deploy) / 86400
            summaries.append(f'last deploy: {days_ago:.1f}d ago')
            if days_ago > 14:
                findings.append({
                    'issue': f'No deploy in {days_ago:.0f} days. Code may be stale.',
                    'severity': 'info',
                    'file_path': None,
                })
        except ValueError:
            pass

    return {
        'lens': 'deploy',
        'summary': ', '.join(summaries),
        'findings': findings,
    }


# ============================================================
# LENS 3: DEPENDENCY HEALTH
# ============================================================

def assess_dependencies():
    findings = []
    summaries = []

    # pip list --outdated
    ok, outdated_output = ssh_cmd(
        f'{VPS_VENV}/pip list --outdated --format=columns 2>/dev/null | tail -n +3 | wc -l',
        timeout=30,
    )
    if ok:
        try:
            outdated_count = int(outdated_output.strip())
            if outdated_count > 10:
                findings.append({
                    'issue': f'{outdated_count} outdated packages on VPS',
                    'severity': 'warn',
                    'file_path': None,
                })
            summaries.append(f'outdated packages: {outdated_count}')
        except ValueError:
            summaries.append('outdated: parse failed')

    # Python version
    ok, py_version = ssh_cmd('python3 --version 2>&1')
    if ok:
        summaries.append(py_version.strip())
        if '3.10' in py_version or '3.9' in py_version or '3.8' in py_version:
            findings.append({
                'issue': f'VPS Python version {py_version.strip()} is below 3.11',
                'severity': 'warn',
                'file_path': None,
            })

    # Check key package versions
    ok, pkg_output = ssh_cmd(
        f'{VPS_VENV}/pip show py-clob-client 2>/dev/null | grep Version',
        timeout=15,
    )
    if ok and pkg_output:
        summaries.append(f'py-clob-client: {pkg_output.split(":")[-1].strip()}')

    return {
        'lens': 'deps',
        'summary': ', '.join(summaries),
        'findings': findings,
    }


# ============================================================
# LENS 4: GIT HYGIENE
# ============================================================

def assess_git_hygiene():
    findings = []
    summaries = []

    # VPS uncommitted changes
    ok, git_status = ssh_cmd(f'cd {VPS_CODEBASE} && git status --porcelain 2>/dev/null')
    if ok:
        if git_status:
            changed = len([l for l in git_status.split('\n') if l.strip()])
            findings.append({
                'issue': f'{changed} uncommitted changes on VPS. Someone may have hotfixed without committing.',
                'severity': 'warn',
                'file_path': VPS_CODEBASE,
            })
            summaries.append(f'VPS uncommitted: {changed}')
        else:
            summaries.append('VPS: clean')

    # Local uncommitted changes
    if os.path.isdir(LOCAL_CODEBASE):
        try:
            result = subprocess.run(
                ['git', 'status', '--porcelain'],
                capture_output=True, text=True, cwd=LOCAL_CODEBASE, timeout=10,
            )
            if result.stdout.strip():
                local_changed = len([l for l in result.stdout.split('\n') if l.strip()])
                summaries.append(f'local uncommitted: {local_changed}')
            else:
                summaries.append('local: clean')
        except Exception:
            summaries.append('local git: unavailable')

    # VPS HEAD vs local HEAD
    ok, vps_head = ssh_cmd(f'cd {VPS_CODEBASE} && git rev-parse --short HEAD 2>/dev/null')
    if ok and os.path.isdir(LOCAL_CODEBASE):
        try:
            result = subprocess.run(
                ['git', 'rev-parse', '--short', 'HEAD'],
                capture_output=True, text=True, cwd=LOCAL_CODEBASE, timeout=10,
            )
            local_head = result.stdout.strip()
            if vps_head and local_head and vps_head != local_head:
                summaries.append(f'HEAD drift: local={local_head} VPS={vps_head}')
            elif vps_head and local_head:
                summaries.append(f'HEAD sync: {local_head}')
        except Exception:
            pass

    return {
        'lens': 'git',
        'summary': ', '.join(summaries),
        'findings': findings,
    }


# ============================================================
# DAILY COMMAND
# ============================================================

def _check_messages(conn):
    """Check for unread messages from CEO or other agents."""
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_messages'"
        ).fetchone()
        if not row:
            return []
        rows = conn.execute("""
            SELECT * FROM agent_messages
            WHERE to_agent='cto' AND read_at IS NULL
            ORDER BY created_at DESC
        """).fetchall()
        if rows:
            conn.execute("""
                UPDATE agent_messages SET read_at=datetime('now')
                WHERE to_agent='cto' AND read_at IS NULL
            """)
            conn.commit()
        return [dict(r) for r in rows]
    except Exception:
        return []


def cmd_daily(args):
    conn = get_db()
    ensure_decisions_table(conn)
    run_id = str(uuid.uuid4())[:8]

    mode_label = 'DRY RUN' if DRY_RUN else 'LIVE'

    # Level 3: Check incoming messages
    messages = _check_messages(conn)

    lenses_to_run = ['code', 'deploy', 'deps', 'git']
    if args.focus:
        lenses_to_run = [args.focus]

    assessors = {
        'code': assess_code_quality,
        'deploy': assess_deploy_integrity,
        'deps': assess_dependencies,
        'git': assess_git_hygiene,
    }

    all_findings = []
    digest_lines = [
        f'CTO TECH DIGEST [{mode_label}]',
        '=' * 50,
        f'Run: {run_id} | {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}',
        '',
    ]

    # Display incoming messages
    if messages:
        digest_lines.append('## INCOMING MESSAGES')
        for m in messages:
            digest_lines.append(f'   [{m["priority"].upper()}] from {m["from_agent"].upper()}: {m["message"][:80]}')
        digest_lines.append('')

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

        if not result['findings']:
            digest_lines.append('   All clear.')
        digest_lines.append('')

    # Log all findings
    for f in all_findings:
        conn.execute("""
            INSERT INTO cto_decisions (run_id, lens, assessment, severity, file_path, action, outcome, dry_run)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            run_id,
            f.get('lens', 'unknown'),
            f['issue'],
            f['severity'],
            f.get('file_path'),
            None,
            'REPORTED',
            DRY_RUN,
        ))
    conn.commit()

    # Summary
    criticals = sum(1 for f in all_findings if f['severity'] == 'critical')
    warns = sum(1 for f in all_findings if f['severity'] == 'warn')

    digest_lines.append('## VERDICT')
    if criticals > 0:
        digest_lines.append(f'   ATTENTION NEEDED: {criticals} critical, {warns} warnings')
    elif warns > 0:
        digest_lines.append(f'   MONITORING: {warns} warnings, no criticals')
    else:
        digest_lines.append('   CODE HEALTH NOMINAL')

    # LLM Synthesis
    if all_findings:
        ai_insight = _llm_synthesize(all_findings, engine='CTO')
        if ai_insight:
            digest_lines.append('')
            digest_lines.append('## AI INSIGHT')
            digest_lines.append(f'   {ai_insight}')

            conn.execute("""
                INSERT INTO cto_decisions (run_id, lens, assessment, severity, action, outcome, dry_run)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (run_id, 'ai_insight', ai_insight, 'info', None, 'LLM_SYNTHESIS', DRY_RUN))
            conn.commit()

    digest_lines.append('')
    digest_lines.append(f'Total findings: {len(all_findings)} ({criticals} critical, {warns} warn)')
    digest_lines.append('=' * 50)

    digest = '\n'.join(digest_lines)
    print(digest)

    if criticals > 0:
        critical_msgs = [f['issue'] for f in all_findings if f['severity'] == 'critical']
        slack_post(f'CTO ALERT: {criticals} CRITICAL issues\n' + '\n'.join(f'- {m}' for m in critical_msgs))

    # Auto-reflect (Level 2)
    try:
        from reflection_engine import reflect_on_agent, ensure_reflections_table
        ensure_reflections_table(conn)
        reflect_on_agent(conn, 'cto')
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
        SELECT run_id, lens, assessment, severity, file_path, dry_run, created_at
        FROM cto_decisions
        ORDER BY created_at DESC
        LIMIT 20
    """).fetchall()

    if not rows:
        print("No CTO decisions logged yet.")
        conn.close()
        return

    print()
    print("CTO DECISION HISTORY (last 20)")
    print("=" * 70)
    for r in rows:
        mode = 'DRY' if r['dry_run'] else 'LIVE'
        sev = r['severity'].upper()
        print(f"  [{r['created_at'][:16]}] [{mode}] [{sev}] {r['run_id']} | {r['lens']}")
        print(f"    {r['assessment'][:80]}")
        if r['file_path']:
            print(f"    file: {r['file_path']}")
        print()
    print("=" * 70)
    conn.close()


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='CTO Engine')
    parser.add_argument('command', nargs='?', default='daily',
                        choices=['daily', 'history'])
    parser.add_argument('--focus', choices=['code', 'deploy', 'deps', 'git'])
    args = parser.parse_args()

    if args.command == 'daily':
        cmd_daily(args)
    elif args.command == 'history':
        cmd_history(args)


if __name__ == '__main__':
    main()
