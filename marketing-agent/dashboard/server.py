#!/usr/bin/env python3
"""OpenClaw Agent Dashboard - Flask API server.

Reads marketing_leads.db and serves a live system architecture diagram.
Usage: python3 dashboard/server.py [--port 8086]
"""

import os
import sys
import sqlite3
import argparse
from datetime import datetime, timezone

from flask import Flask, jsonify, send_from_directory, request

DB_PATH = os.environ.get(
    'LEADS_DB_PATH',
    os.path.join(os.path.dirname(__file__), '..', 'data', 'marketing_leads.db')
)

DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn, name):
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row[0] > 0


def safe_count(conn, query, default=0):
    try:
        return conn.execute(query).fetchone()[0] or default
    except Exception:
        return default


def get_agent_status(conn, table, insight_lens='ai_insight'):
    if not table_exists(conn, table):
        return {'status': 'no_data', 'note': f'{table} table missing'}

    row = conn.execute(
        f"SELECT MAX(created_at) as last_run FROM {table}"
    ).fetchone()

    if not row or not row['last_run']:
        return {'status': 'never', 'hours_ago': None, 'finding_count': 0,
                'critical_count': 0, 'warn_count': 0, 'ai_insight': None,
                'last_action': None, 'lenses_run': []}

    last_run = row['last_run']
    try:
        dt = datetime.fromisoformat(last_run)
    except ValueError:
        dt = datetime.strptime(last_run, '%Y-%m-%d %H:%M:%S')
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    hours_ago = (now - dt).total_seconds() / 3600

    if hours_ago < 24:
        status = 'healthy'
    elif hours_ago < 72:
        status = 'stale'
    else:
        status = 'critical'

    latest_run = conn.execute(
        f"SELECT run_id FROM {table} ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    run_id = latest_run['run_id'] if latest_run else None

    finding_count = 0
    critical_count = 0
    warn_count = 0
    last_action = None
    lenses_run = []

    if run_id:
        findings = conn.execute(
            f"SELECT * FROM {table} WHERE run_id=? AND lens != ?",
            (run_id, insight_lens)
        ).fetchall()
        finding_count = len(findings)

        for f in findings:
            sev = (dict(f).get('severity') or '').lower()
            if sev == 'critical':
                critical_count += 1
            elif sev in ('warn', 'warning', 'medium', 'high'):
                warn_count += 1

            lens = dict(f).get('lens', '')
            if lens and lens not in lenses_run:
                lenses_run.append(lens)

        if table == 'cmo_decisions':
            action_row = conn.execute(
                f"SELECT action FROM {table} WHERE run_id=? AND action IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 1", (run_id,)
            ).fetchone()
            if action_row:
                last_action = action_row['action']

    ai_row = conn.execute(
        f"SELECT assessment FROM {table} WHERE lens=? ORDER BY created_at DESC LIMIT 1",
        (insight_lens,)
    ).fetchone()
    ai_insight = ai_row['assessment'] if ai_row else None

    return {
        'status': status,
        'hours_ago': round(hours_ago, 1),
        'finding_count': finding_count,
        'critical_count': critical_count,
        'warn_count': warn_count,
        'ai_insight': ai_insight,
        'last_action': last_action,
        'lenses_run': lenses_run,
    }


def get_pipeline_metrics(conn):
    leads_total = safe_count(conn, "SELECT COUNT(*) FROM leads") if table_exists(conn, 'leads') else 0
    leads_enriched = safe_count(conn, "SELECT COUNT(*) FROM leads WHERE email IS NOT NULL") if table_exists(conn, 'leads') else 0
    social_pending = safe_count(conn, "SELECT COUNT(*) FROM social_posts WHERE status='pending'") if table_exists(conn, 'social_posts') else 0
    social_posted_7d = safe_count(conn, "SELECT COUNT(*) FROM social_posts WHERE status='posted' AND posted_at >= datetime('now','-7 days')") if table_exists(conn, 'social_posts') else 0
    purchases_7d = safe_count(conn, "SELECT COUNT(*) FROM funnel_contacts WHERE purchased_at >= datetime('now','-7 days')") if table_exists(conn, 'funnel_contacts') else 0

    return {
        'leads_total': leads_total,
        'leads_enriched': leads_enriched,
        'social_pending': social_pending,
        'social_posted_7d': social_posted_7d,
        'funnel_purchases_7d': purchases_7d,
    }


@app.route('/')
def index():
    return send_from_directory(DASHBOARD_DIR, 'index.html')


@app.route('/api/status')
def api_status():
    conn = get_db()
    try:
        agents = {
            'cmo': get_agent_status(conn, 'cmo_decisions'),
            'cto': get_agent_status(conn, 'cto_decisions'),
            'ceo': get_agent_status(conn, 'ceo_decisions'),
            'coo': {'status': 'no_data', 'note': 'COO writes to stdout/Slack only, no DB table'},
        }
        pipeline = get_pipeline_metrics(conn)
    finally:
        conn.close()

    return jsonify({
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'agents': agents,
        'pipeline': pipeline,
    })


@app.route('/api/decisions')
def api_decisions():
    agent = request.args.get('agent', 'cmo')
    limit = min(int(request.args.get('limit', '20')), 100)
    table = f'{agent}_decisions'

    conn = get_db()
    try:
        if not table_exists(conn, table):
            return jsonify({'error': f'Table {table} not found'}), 404
        rows = conn.execute(
            f"SELECT * FROM {table} ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        decisions = [dict(r) for r in rows]
    finally:
        conn.close()

    return jsonify({'agent': agent, 'decisions': decisions})


def main():
    parser = argparse.ArgumentParser(description='OpenClaw Agent Dashboard')
    parser.add_argument('--port', type=int, default=8086)
    parser.add_argument('--host', default='127.0.0.1')
    args = parser.parse_args()

    if not os.path.exists(DB_PATH):
        print(f"DB not found: {DB_PATH}")
        print("Run: ./run.sh init_db && ./run.sh funnel_db_init extend")
        sys.exit(1)

    print(f"DB: {DB_PATH}")
    print(f"Dashboard: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == '__main__':
    main()
