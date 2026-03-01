#!/usr/bin/env python3
"""Reflection Engine (Level 2) -- Self-Critique for OpenClaw Agents.

Based on the Reflexion framework (NeurIPS 2023, Shinn et al.).
Each engine gains a post-run self-critique step:
1. Query own decision history
2. Compare current vs historical findings
3. Self-assess accuracy and extract lessons
4. Store reflections for future runs

Usage:
    python3 scripts/reflection_engine.py reflect cmo   # Reflect on CMO's last run
    python3 scripts/reflection_engine.py reflect cto   # Reflect on CTO's last run
    python3 scripts/reflection_engine.py reflect ceo   # Reflect on CEO's last run
    python3 scripts/reflection_engine.py history        # Show recent reflections
    python3 scripts/reflection_engine.py accuracy       # Show accuracy scores
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

AGENT_TABLES = {
    'cmo': 'cmo_decisions',
    'cto': 'cto_decisions',
    'ceo': 'ceo_decisions',
    'coo': 'coo_decisions',
}


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


def ensure_reflections_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_reflections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent TEXT NOT NULL,
            run_id TEXT NOT NULL,
            reflection TEXT NOT NULL,
            accuracy_score REAL,
            lesson TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def _llm_reflect(agent, current_findings, historical_context, prior_reflections=None):
    """Call Claude Haiku to produce a self-reflection on the agent's performance.

    Returns (reflection, accuracy_score, lesson) or empty tuple on failure.
    """
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        return None

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)

        current_text = '\n'.join(
            f"- [{f.get('severity', 'info').upper()}] {f['assessment'][:100]}"
            for f in current_findings
        )
        history_text = '\n'.join(
            f"- [{h.get('severity', 'info').upper()}] {h['assessment'][:80]} (run {h['run_id']})"
            for h in historical_context[:10]
        )

        lessons_text = ''
        if prior_reflections:
            lessons_text = '\n'.join(
                f"- {pr['lesson']}" for pr in prior_reflections if pr.get('lesson')
            )

        prompt = (
            f"You are the {agent.upper()} of a solo-founder tech startup. "
            f"You just completed an assessment run. Now do a SELF-CRITIQUE.\n\n"
        )
        if lessons_text:
            prompt += (
                f"YOUR PAST SELF-CRITIQUE LESSONS (apply these):\n{lessons_text}\n\n"
            )
        prompt += (
            f"YOUR CURRENT FINDINGS:\n{current_text}\n\n"
            f"YOUR PAST FINDINGS (last 10):\n{history_text or 'No history yet.'}\n\n"
            f"Evaluate yourself honestly:\n"
            f"1. ACCURACY: Are you flagging the same things repeatedly? "
            f"Are those things actually problems or false positives?\n"
            f"2. RELEVANCE: Are your findings actionable or just noise?\n"
            f"3. LESSON: What one thing should you do differently next run?\n\n"
            f"Respond in this EXACT format:\n"
            f"REFLECTION: [2-3 sentences of self-critique]\n"
            f"ACCURACY: [0.0 to 1.0, where 1.0 = all findings were actionable]\n"
            f"LESSON: [One concrete lesson for next time]"
        )

        response = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=400,
            messages=[{'role': 'user', 'content': prompt}]
        )
        text = response.content[0].text

        reflection = ''
        accuracy = 0.5
        lesson = ''

        for line in text.split('\n'):
            line = line.strip()
            if line.startswith('REFLECTION:'):
                reflection = line[len('REFLECTION:'):].strip()
            elif line.startswith('ACCURACY:'):
                try:
                    accuracy = float(line[len('ACCURACY:'):].strip())
                    accuracy = max(0.0, min(1.0, accuracy))
                except ValueError:
                    accuracy = 0.5
            elif line.startswith('LESSON:'):
                lesson = line[len('LESSON:'):].strip()

        if not reflection:
            reflection = text[:200]

        return reflection, accuracy, lesson
    except Exception:
        return None


def reflect_on_agent(conn, agent):
    """Run the Reflexion loop for a specific agent."""
    table = AGENT_TABLES.get(agent)
    if not table or not table_exists(conn, table):
        print(f"No decision table for {agent}")
        return

    ensure_reflections_table(conn)

    # Get latest run_id
    latest = conn.execute(
        f"SELECT run_id FROM {table} ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not latest:
        print(f"No decisions found for {agent}")
        return

    run_id = latest['run_id']

    # Current findings (this run)
    current = conn.execute(
        f"SELECT * FROM {table} WHERE run_id=? AND lens != 'ai_insight'",
        (run_id,)
    ).fetchall()
    current_findings = [dict(r) for r in current]

    if not current_findings:
        print(f"No findings in run {run_id} for {agent}")
        return

    # Historical context (previous runs, different run_ids)
    historical = conn.execute(
        f"SELECT * FROM {table} WHERE run_id != ? AND lens != 'ai_insight' "
        f"ORDER BY created_at DESC LIMIT 20",
        (run_id,)
    ).fetchall()
    historical_context = [dict(r) for r in historical]

    # Check for prior reflections to inject as context
    prior_reflections = conn.execute(
        "SELECT reflection, lesson FROM agent_reflections "
        "WHERE agent=? ORDER BY created_at DESC LIMIT 3",
        (agent,)
    ).fetchall()

    print(f'REFLECTION: {agent.upper()} (run {run_id})')
    print('-' * 50)
    print(f'Current findings: {len(current_findings)}')
    print(f'Historical context: {len(historical_context)} past findings')
    if prior_reflections:
        print(f'Prior reflections: {len(prior_reflections)}')
        for pr in prior_reflections:
            print(f'  Previous lesson: {pr["lesson"][:80]}')
    print()

    # LLM self-critique (with prior lessons injected)
    result = _llm_reflect(agent, current_findings, historical_context,
                          prior_reflections=[dict(r) for r in prior_reflections])
    if result:
        reflection, accuracy, lesson = result
        print(f'Reflection: {reflection}')
        print(f'Accuracy: {accuracy:.2f}')
        print(f'Lesson: {lesson}')

        conn.execute("""
            INSERT INTO agent_reflections (agent, run_id, reflection, accuracy_score, lesson)
            VALUES (?, ?, ?, ?, ?)
        """, (agent, run_id, reflection, accuracy, lesson))
        conn.commit()
        print('\nSaved to agent_reflections.')
    else:
        # Fallback: deterministic reflection (no API key)
        repeated = {}
        for f in current_findings:
            a = f['assessment'][:60]
            count = sum(1 for h in historical_context if h['assessment'][:60] == a)
            if count >= 2:
                repeated[a] = count

        if repeated:
            reflection = f"Flagged {len(repeated)} recurring issues that have appeared 2+ times without resolution. May be over-reporting."
            accuracy = max(0.3, 1.0 - len(repeated) * 0.1)
            lesson = f"Top repeated finding: '{list(repeated.keys())[0]}' ({list(repeated.values())[0]}x). Consider raising threshold or acknowledging as accepted risk."
        else:
            reflection = f"Run {run_id}: {len(current_findings)} findings, all appear fresh (not repeated from history)."
            accuracy = 0.7
            lesson = "Findings are novel. Continue monitoring for patterns."

        print(f'Reflection (deterministic): {reflection}')
        print(f'Accuracy: {accuracy:.2f}')
        print(f'Lesson: {lesson}')

        conn.execute("""
            INSERT INTO agent_reflections (agent, run_id, reflection, accuracy_score, lesson)
            VALUES (?, ?, ?, ?, ?)
        """, (agent, run_id, reflection, accuracy, lesson))
        conn.commit()
        print('\nSaved to agent_reflections.')

    print('-' * 50)


def cmd_reflect(args):
    conn = get_db()
    agents_to_reflect = [args.agent] if args.agent else list(AGENT_TABLES.keys())
    for agent in agents_to_reflect:
        reflect_on_agent(conn, agent)
        print()
    conn.close()


def cmd_history(args):
    conn = get_db()
    ensure_reflections_table(conn)

    rows = conn.execute("""
        SELECT agent, run_id, reflection, accuracy_score, lesson, created_at
        FROM agent_reflections
        ORDER BY created_at DESC
        LIMIT 15
    """).fetchall()

    if not rows:
        print("No reflections logged yet.")
        conn.close()
        return

    print()
    print('REFLECTION HISTORY (last 15)')
    print('=' * 60)
    for r in rows:
        print(f'  [{r["created_at"][:16]}] {r["agent"].upper()} (run {r["run_id"]})')
        print(f'    {r["reflection"][:80]}')
        print(f'    accuracy: {r["accuracy_score"]:.2f} | lesson: {(r["lesson"] or "")[:60]}')
        print()
    print('=' * 60)
    conn.close()


def cmd_accuracy(args):
    conn = get_db()
    ensure_reflections_table(conn)

    print()
    print('ACCURACY SCORES (last 10 runs per agent)')
    print('-' * 40)
    for agent in AGENT_TABLES:
        rows = conn.execute("""
            SELECT accuracy_score FROM agent_reflections
            WHERE agent=? ORDER BY created_at DESC LIMIT 10
        """, (agent,)).fetchall()
        if rows:
            scores = [r['accuracy_score'] for r in rows if r['accuracy_score'] is not None]
            if scores:
                avg = sum(scores) / len(scores)
                print(f'  {agent.upper()}: avg {avg:.2f} over {len(scores)} reflections')
                bar = ''.join('#' if s >= 0.7 else '.' for s in scores)
                print(f'         [{bar}] (# >= 0.7)')
            else:
                print(f'  {agent.upper()}: no scored reflections')
        else:
            print(f'  {agent.upper()}: no reflections yet')
    print()
    conn.close()


COMMANDS = {
    'reflect': cmd_reflect,
    'history': cmd_history,
    'accuracy': cmd_accuracy,
}


def main():
    parser = argparse.ArgumentParser(description='Reflection Engine (Level 2)')
    parser.add_argument('command', nargs='?', default='reflect',
                        choices=list(COMMANDS.keys()))
    parser.add_argument('agent', nargs='?', default=None,
                        choices=list(AGENT_TABLES.keys()) + [None])
    args = parser.parse_args()
    COMMANDS[args.command](args)


if __name__ == '__main__':
    main()
