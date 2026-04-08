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
            'coo': get_agent_status(conn, 'coo_decisions'),
        }
        pipeline = get_pipeline_metrics(conn)
    finally:
        conn.close()

    return jsonify({
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'agents': agents,
        'pipeline': pipeline,
    })


@app.route('/api/trends')
def api_trends():
    """Level 1: Trend arrows and recurring issues for each agent."""
    conn = get_db()
    try:
        result = {}
        for agent, table in [('cmo', 'cmo_decisions'), ('cto', 'cto_decisions'), ('ceo', 'ceo_decisions'), ('coo', 'coo_decisions')]:
            if not table_exists(conn, table):
                result[agent] = {'trend': 'no_data', 'this_week': 0, 'last_week': 0, 'recurring': [], 'heatmap': []}
                continue

            # Trend direction
            this_week = safe_count(conn,
                f"SELECT COUNT(*) FROM {table} WHERE created_at >= datetime('now', '-7 days') "
                f"AND lens != 'ai_insight'")
            last_week = safe_count(conn,
                f"SELECT COUNT(*) FROM {table} WHERE created_at >= datetime('now', '-14 days') "
                f"AND created_at < datetime('now', '-7 days') AND lens != 'ai_insight'")

            if this_week > last_week and last_week > 0:
                trend = 'up'
            elif this_week < last_week:
                trend = 'down'
            else:
                trend = 'flat'

            # Recurring findings (3+ times in 30 days)
            recurring_rows = conn.execute(
                f"SELECT assessment, COUNT(*) as cnt FROM {table} "
                f"WHERE lens != 'ai_insight' AND created_at >= datetime('now', '-30 days') "
                f"GROUP BY assessment HAVING COUNT(*) >= 3 ORDER BY cnt DESC LIMIT 5"
            ).fetchall()
            recurring = [{'text': r[0][:60], 'count': r[1]} for r in recurring_rows]

            # Check if table has severity column
            has_severity = True
            try:
                conn.execute(f"SELECT severity FROM {table} LIMIT 0")
            except Exception:
                has_severity = False

            # Daily heatmap (last 14 days)
            if has_severity:
                heatmap_rows = conn.execute(
                    f"SELECT date(created_at) as day, COUNT(*) as total, "
                    f"SUM(CASE WHEN severity='critical' THEN 1 ELSE 0 END) as crits, "
                    f"SUM(CASE WHEN severity IN ('warn','warning','high','medium') THEN 1 ELSE 0 END) as warns "
                    f"FROM {table} WHERE lens != 'ai_insight' "
                    f"AND created_at >= datetime('now', '-14 days') "
                    f"GROUP BY date(created_at) ORDER BY day"
                ).fetchall()
            else:
                heatmap_rows = conn.execute(
                    f"SELECT date(created_at) as day, COUNT(*) as total, "
                    f"0 as crits, COUNT(*) as warns "
                    f"FROM {table} WHERE lens != 'ai_insight' "
                    f"AND created_at >= datetime('now', '-14 days') "
                    f"GROUP BY date(created_at) ORDER BY day"
                ).fetchall()
            heatmap = []
            for h in heatmap_rows:
                if h[2] > 0:
                    level = 'red'
                elif h[3] > 0:
                    level = 'amber'
                else:
                    level = 'green'
                heatmap.append({'day': h[0], 'level': level, 'total': h[1]})

            result[agent] = {
                'trend': trend,
                'this_week': this_week,
                'last_week': last_week,
                'recurring': recurring,
                'heatmap': heatmap,
            }
    finally:
        conn.close()
    return jsonify(result)


@app.route('/api/reflections')
def api_reflections():
    """Level 2: Recent reflections and accuracy scores."""
    conn = get_db()
    try:
        if not table_exists(conn, 'agent_reflections'):
            return jsonify({'reflections': [], 'accuracy': {}})

        rows = conn.execute("""
            SELECT agent, run_id, reflection, accuracy_score, lesson, created_at
            FROM agent_reflections ORDER BY created_at DESC LIMIT 10
        """).fetchall()
        reflections = [dict(r) for r in rows]

        # Average accuracy per agent
        accuracy = {}
        for agent in ('cmo', 'cto', 'ceo', 'coo'):
            avg_row = conn.execute("""
                SELECT AVG(accuracy_score) as avg_score, COUNT(*) as cnt
                FROM agent_reflections WHERE agent=?
            """, (agent,)).fetchone()
            if avg_row and avg_row[0] is not None:
                accuracy[agent] = {'avg': round(avg_row[0], 2), 'count': avg_row[1]}
    finally:
        conn.close()
    return jsonify({'reflections': reflections, 'accuracy': accuracy})


@app.route('/api/messages')
def api_messages():
    """Level 3: Recent inter-agent messages and task queue."""
    conn = get_db()
    try:
        messages = []
        if table_exists(conn, 'agent_messages'):
            rows = conn.execute("""
                SELECT from_agent, to_agent, message, priority, read_at, created_at
                FROM agent_messages ORDER BY created_at DESC LIMIT 20
            """).fetchall()
            messages = [dict(r) for r in rows]

        tasks = []
        if table_exists(conn, 'task_queue'):
            rows = conn.execute("""
                SELECT assigned_to, task, priority, status, created_by, result, created_at, completed_at
                FROM task_queue ORDER BY created_at DESC LIMIT 10
            """).fetchall()
            tasks = [dict(r) for r in rows]
    finally:
        conn.close()
    return jsonify({'messages': messages, 'tasks': tasks})


@app.route('/api/briefing')
def api_briefing():
    """Executive briefing: plain-English summary for layman dashboard."""
    conn = get_db()
    try:
        agents = {
            'cmo': get_agent_status(conn, 'cmo_decisions'),
            'cto': get_agent_status(conn, 'cto_decisions'),
            'ceo': get_agent_status(conn, 'ceo_decisions'),
            'coo': get_agent_status(conn, 'coo_decisions'),
        }

        statuses = [a['status'] for a in agents.values()]
        healthy = statuses.count('healthy')
        stale = statuses.count('stale')
        critical = statuses.count('critical')

        if healthy == 4:
            overall = 'ALL HEALTHY'
        elif critical > 0:
            overall = f'{critical} AGENT{"S" if critical > 1 else ""} CRITICAL'
        elif stale > 0:
            overall = f'{stale} AGENT{"S" if stale > 1 else ""} STALE'
        else:
            overall = 'NO DATA'

        hours_list = [a['hours_ago'] for a in agents.values() if a.get('hours_ago')]
        if hours_list:
            min_h = min(hours_list)
            if min_h < 1:
                last_run = f'{int(min_h * 60)}m ago'
            elif min_h < 24:
                last_run = f'{min_h:.1f}h ago'
            else:
                last_run = f'{int(min_h / 24)}d ago'
        else:
            last_run = 'never'

        labels = {'cmo': 'Marketing', 'cto': 'Tech', 'ceo': 'Strategy', 'coo': 'Operations'}
        briefs = []
        for aid, a in agents.items():
            briefs.append({
                'agent': aid,
                'label': labels[aid],
                'insight': a.get('ai_insight') or 'No assessment yet.',
                'action': a.get('last_action'),
            })

        top_action = agents['ceo'].get('last_action') or agents['cmo'].get('last_action')

    finally:
        conn.close()

    return jsonify({
        'overall_status': overall,
        'last_run_ago': last_run,
        'briefs': briefs,
        'top_action': top_action,
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


@app.route('/api/seo')
def api_seo():
    """SEO/AEO dashboard data."""
    conn = get_db()
    try:
        # GSC snapshot (latest per site)
        gsc_data = {}
        if table_exists(conn, 'gsc_snapshots'):
            for site in ('https://citability.dev/', 'https://chudi.dev/'):
                row = conn.execute(
                    "SELECT * FROM gsc_snapshots WHERE site=? ORDER BY pulled_at DESC LIMIT 1",
                    (site,)
                ).fetchone()
                if row:
                    key = 'citability' if 'citability' in site else 'chudi'
                    gsc_data[key] = dict(row)

        # Top queries (latest pull, both sites, sorted by impressions)
        top_queries = []
        if table_exists(conn, 'gsc_queries'):
            rows = conn.execute("""
                SELECT site, query, page, impressions, clicks, position, date_range
                FROM gsc_queries
                WHERE pulled_at = (SELECT MAX(pulled_at) FROM gsc_queries)
                ORDER BY impressions DESC
                LIMIT 25
            """).fetchall()
            top_queries = [dict(r) for r in rows]

        # SERP actions (latest check)
        serp_actions = []
        if table_exists(conn, 'serp_snapshots'):
            rows = conn.execute("""
                SELECT query, our_position, has_featured_snippet, has_paa, action, checked_at
                FROM serp_snapshots
                WHERE checked_at = (SELECT MAX(checked_at) FROM serp_snapshots)
                ORDER BY our_position ASC NULLS LAST
                LIMIT 20
            """).fetchall()
            serp_actions = [dict(r) for r in rows]

        # AEO opportunities
        aeo_data = []
        if table_exists(conn, 'aeo_snapshots'):
            rows = conn.execute("""
                SELECT query, has_answer_box, answer_box_type, answer_box_source_domain,
                       we_own_answer_box, paa_count, our_organic_position, checked_at
                FROM aeo_snapshots
                WHERE checked_at = (SELECT MAX(checked_at) FROM aeo_snapshots)
                ORDER BY has_answer_box DESC, our_organic_position ASC NULLS LAST
                LIMIT 20
            """).fetchall()
            aeo_data = [dict(r) for r in rows]

    finally:
        conn.close()

    return jsonify({
        'gsc': gsc_data,
        'top_queries': top_queries,
        'serp_actions': serp_actions,
        'aeo': aeo_data,
    })


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
