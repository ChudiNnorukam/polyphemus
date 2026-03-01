#!/usr/bin/env python3
"""Memory Engine (Level 1) -- Pattern Detection for OpenClaw.

Analyzes decision history across all engines. Detects recurring issues,
staleness patterns, and week-over-week trends. Pure SQL, no LLM needed.

Usage:
    python3 scripts/memory_engine.py scan       # Full pattern scan
    python3 scripts/memory_engine.py trends     # Week-over-week trend arrows
    python3 scripts/memory_engine.py recurring  # Recurring unresolved findings
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

DB_PATH = os.environ.get(
    'LEADS_DB_PATH',
    os.path.join(os.path.dirname(__file__), '..', 'data', 'marketing_leads.db')
)


def _load_env():
    for path in [
        os.path.join(os.path.dirname(__file__), '..', '.env'),
        '/opt/openclaw/.env',
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


def get_recurring_findings(conn, table, days=30, min_occurrences=3):
    """Find assessments that appear N+ times in the last M days."""
    if not table_exists(conn, table):
        return []
    rows = conn.execute("""
        SELECT assessment, COUNT(*) as occurrences,
               MIN(created_at) as first_seen, MAX(created_at) as last_seen
        FROM {table}
        WHERE lens != 'ai_insight'
          AND created_at >= datetime('now', '-{days} days')
        GROUP BY assessment
        HAVING COUNT(*) >= ?
        ORDER BY occurrences DESC
        LIMIT 10
    """.format(table=table, days=days), (min_occurrences,)).fetchall()
    return [dict(r) for r in rows]


def _has_column(conn, table, column):
    """Check if a table has a specific column."""
    try:
        conn.execute(f"SELECT {column} FROM {table} LIMIT 0")
        return True
    except sqlite3.OperationalError:
        return False


def get_trend(conn, table):
    """Compare this week vs last week finding counts. Returns 'up', 'down', or 'flat'."""
    if not table_exists(conn, table):
        return 'no_data', 0, 0

    has_severity = _has_column(conn, table, 'severity')
    if has_severity:
        sev_clause = "AND severity IN ('critical', 'warn', 'warning', 'high', 'medium') "
    else:
        sev_clause = ""

    this_week = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE created_at >= datetime('now', '-7 days') "
        f"{sev_clause}AND lens != 'ai_insight'"
    ).fetchone()[0]
    last_week = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE created_at >= datetime('now', '-14 days') "
        f"AND created_at < datetime('now', '-7 days') "
        f"{sev_clause}AND lens != 'ai_insight'"
    ).fetchone()[0]

    if this_week > last_week and last_week > 0:
        return 'up', this_week, last_week
    elif this_week < last_week:
        return 'down', this_week, last_week
    return 'flat', this_week, last_week


def get_daily_severity_map(conn, table, days=30):
    """Get daily severity counts for heatmap display."""
    if not table_exists(conn, table):
        return []

    has_severity = _has_column(conn, table, 'severity')
    if has_severity:
        query = """
            SELECT date(created_at) as day,
                   SUM(CASE WHEN severity = 'critical' THEN 1 ELSE 0 END) as criticals,
                   SUM(CASE WHEN severity IN ('warn', 'warning', 'high', 'medium') THEN 1 ELSE 0 END) as warns,
                   COUNT(*) as total
            FROM {table}
            WHERE lens != 'ai_insight'
              AND created_at >= datetime('now', '-{days} days')
            GROUP BY date(created_at)
            ORDER BY day
        """.format(table=table, days=days)
    else:
        query = """
            SELECT date(created_at) as day,
                   0 as criticals,
                   COUNT(*) as warns,
                   COUNT(*) as total
            FROM {table}
            WHERE lens != 'ai_insight'
              AND created_at >= datetime('now', '-{days} days')
            GROUP BY date(created_at)
            ORDER BY day
        """.format(table=table, days=days)

    rows = conn.execute(query).fetchall()
    return [dict(r) for r in rows]


def cmd_scan(args):
    conn = get_db()
    agents = [
        ('CMO', 'cmo_decisions'),
        ('CTO', 'cto_decisions'),
        ('CEO', 'ceo_decisions'),
    ]

    print('MEMORY ENGINE SCAN')
    print('=' * 50)
    print(f'{datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}')
    print()

    for label, table in agents:
        if not table_exists(conn, table):
            print(f'## {label}: table missing')
            print()
            continue

        # Trend
        direction, this_w, last_w = get_trend(conn, table)
        arrow = {'up': 'UP', 'down': 'DOWN', 'flat': 'FLAT', 'no_data': 'N/A'}[direction]
        print(f'## {label} -- trend: {arrow} ({this_w} this week, {last_w} last week)')

        # Recurring findings
        recurring = get_recurring_findings(conn, table)
        if recurring:
            print(f'   Recurring issues ({len(recurring)}):')
            for r in recurring[:5]:
                print(f'   (x{r["occurrences"]}) {r["assessment"][:70]}')
                print(f'         first: {r["first_seen"][:16]}, last: {r["last_seen"][:16]}')
        else:
            print('   No recurring issues.')

        # Daily heatmap summary
        daily = get_daily_severity_map(conn, table, days=7)
        if daily:
            strip = []
            for d in daily:
                if d['criticals'] > 0:
                    strip.append('R')
                elif d['warns'] > 0:
                    strip.append('A')
                else:
                    strip.append('G')
            print(f'   7-day heatmap: [{"".join(strip)}] (R=red A=amber G=green)')

        print()

    conn.close()
    print('=' * 50)


def cmd_trends(args):
    conn = get_db()
    print('TREND ARROWS')
    print('-' * 40)
    for label, table in [('CMO', 'cmo_decisions'), ('CTO', 'cto_decisions'), ('CEO', 'ceo_decisions')]:
        direction, this_w, last_w = get_trend(conn, table)
        arrow = {'up': '^', 'down': 'v', 'flat': '-', 'no_data': '?'}[direction]
        print(f'  {label}: [{arrow}] {this_w} this week / {last_w} last week')
    conn.close()


def cmd_recurring(args):
    conn = get_db()
    print('RECURRING UNRESOLVED FINDINGS (30 days)')
    print('-' * 50)
    for label, table in [('CMO', 'cmo_decisions'), ('CTO', 'cto_decisions'), ('CEO', 'ceo_decisions')]:
        recurring = get_recurring_findings(conn, table)
        if recurring:
            print(f'\n  {label}:')
            for r in recurring:
                print(f'    (x{r["occurrences"]}) {r["assessment"][:80]}')
    conn.close()


COMMANDS = {
    'scan': cmd_scan,
    'trends': cmd_trends,
    'recurring': cmd_recurring,
}


def main():
    parser = argparse.ArgumentParser(description='Memory Engine (Level 1)')
    parser.add_argument('command', nargs='?', default='scan',
                        choices=list(COMMANDS.keys()))
    args = parser.parse_args()
    COMMANDS[args.command](args)


if __name__ == '__main__':
    main()
