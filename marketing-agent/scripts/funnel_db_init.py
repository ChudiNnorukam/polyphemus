#!/usr/bin/env python3
"""Funnel DB Init — Extend marketing_leads.db with Wing 3 + 4 tables.

Idempotent: safe to run multiple times (CREATE TABLE IF NOT EXISTS).
Also seeds sequence_definitions for post-purchase-v1 and upsell-v1.

Usage:
    python3 scripts/funnel_db_init.py extend    # Add Wing 3+4 tables
    python3 scripts/funnel_db_init.py status    # Show which tables exist
"""

import argparse
import os
import sqlite3
import sys

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

WING3_SCHEMA = """
CREATE TABLE IF NOT EXISTS social_posts (
    id INTEGER PRIMARY KEY,
    source_slug TEXT NOT NULL,
    platform TEXT NOT NULL,
    content TEXT NOT NULL,
    image_url TEXT,
    scheduled_at TEXT,
    status TEXT DEFAULT 'pending',
    platform_post_id TEXT,
    error TEXT,
    impressions INTEGER DEFAULT 0,
    clicks INTEGER DEFAULT 0,
    saves INTEGER DEFAULT 0,
    posted_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_social_posts_slug_platform
    ON social_posts (source_slug, platform);
"""

WING4_SCHEMA = """
CREATE TABLE IF NOT EXISTS funnel_contacts (
    id INTEGER PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    name TEXT,
    source TEXT,
    product_id TEXT,
    amount_cents INTEGER,
    sale_id TEXT UNIQUE,
    purchased_at TEXT,
    lead_id INTEGER REFERENCES leads(id),
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sequence_enrollments (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES funnel_contacts(id),
    sequence_id TEXT NOT NULL,
    current_step INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    enrolled_at TEXT DEFAULT (datetime('now')),
    exited_at TEXT,
    exit_reason TEXT
);

CREATE TABLE IF NOT EXISTS sequence_sends (
    id INTEGER PRIMARY KEY,
    enrollment_id INTEGER NOT NULL REFERENCES sequence_enrollments(id),
    step INTEGER NOT NULL,
    scheduled_for TEXT NOT NULL,
    sent_at TEXT,
    brevo_message_id TEXT,
    status TEXT DEFAULT 'pending',
    opened_at TEXT,
    clicked_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sequence_definitions (
    sequence_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    delay_hours INTEGER NOT NULL,
    subject TEXT NOT NULL,
    template_name TEXT NOT NULL,
    PRIMARY KEY (sequence_id, step)
);
"""

# Seed data for post-purchase-v1 (5 steps) and upsell-v1 (3 steps)
CMO_SCHEMA = """
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
);
"""

CTO_SCHEMA = """
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
);
"""

CEO_SCHEMA = """
CREATE TABLE IF NOT EXISTS ceo_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    lens TEXT NOT NULL,
    assessment TEXT NOT NULL,
    severity TEXT NOT NULL,
    insight TEXT,
    recommendation TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

# Level 2: Self-Reflection (Reflexion framework)
REFLECTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_reflections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    run_id TEXT NOT NULL,
    reflection TEXT NOT NULL,
    accuracy_score REAL,
    lesson TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

# Level 3: Inter-Agent Coordination
COORDINATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_agent TEXT NOT NULL,
    to_agent TEXT NOT NULL,
    message TEXT NOT NULL,
    priority TEXT DEFAULT 'normal',
    read_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

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
);
"""

SEQUENCE_SEEDS = [
    # post-purchase-v1
    ('post-purchase-v1', 1, 0,   'Your purchase is confirmed', 'post_purchase_welcome'),
    ('post-purchase-v1', 2, 24,  'Quick tip to get started', 'post_purchase_day1'),
    ('post-purchase-v1', 3, 72,  'How others are using this', 'post_purchase_day3'),
    ('post-purchase-v1', 4, 168, 'Are you getting value from it?', 'post_purchase_week1'),
    ('post-purchase-v1', 5, 336, 'One more thing before I go', 'post_purchase_week2'),
    # upsell-v1
    ('upsell-v1', 1, 0,  'Something I think you will love', 'upsell_intro'),
    ('upsell-v1', 2, 48, 'In case you missed this', 'upsell_followup'),
    ('upsell-v1', 3, 96, 'Last chance on this offer', 'upsell_final'),
]


def get_db():
    if not os.path.exists(DB_PATH):
        print(f"DB not found: {DB_PATH}. Run init_db.py first.")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def cmd_extend(args):
    conn = get_db()
    print(f"Extending DB: {DB_PATH}")

    conn.executescript(WING3_SCHEMA)
    print("  Created: social_posts (+ unique index on slug+platform)")

    conn.executescript(WING4_SCHEMA)
    print("  Created: funnel_contacts, sequence_enrollments, sequence_sends, sequence_definitions")

    conn.executescript(CMO_SCHEMA)
    print("  Created: cmo_decisions")

    conn.executescript(CTO_SCHEMA)
    print("  Created: cto_decisions")

    conn.executescript(CEO_SCHEMA)
    print("  Created: ceo_decisions")

    conn.executescript(REFLECTION_SCHEMA)
    print("  Created: agent_reflections")

    conn.executescript(COORDINATION_SCHEMA)
    print("  Created: agent_messages, task_queue")

    # Seed sequence_definitions (idempotent — INSERT OR IGNORE)
    seeded = 0
    for row in SEQUENCE_SEEDS:
        cursor = conn.execute("""
            INSERT OR IGNORE INTO sequence_definitions
                (sequence_id, step, delay_hours, subject, template_name)
            VALUES (?, ?, ?, ?, ?)
        """, row)
        seeded += cursor.rowcount
    conn.commit()
    conn.close()

    print(f"  Seeded {seeded} sequence definition rows (skipped existing)")
    print("\nDone. 4 tables ready for Wings 3 + 4.")


def cmd_status(args):
    conn = get_db()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()]
    conn.close()

    wing3 = ['social_posts']
    wing4 = ['funnel_contacts', 'sequence_enrollments', 'sequence_sends', 'sequence_definitions']
    agents = ['cmo_decisions', 'cto_decisions', 'ceo_decisions']
    evolution = ['agent_reflections', 'agent_messages', 'task_queue']
    original = ['leads', 'daily_caps', 'email_events']

    print("\nDB Tables:")
    for t in original + wing3 + wing4 + agents + evolution:
        status = 'OK' if t in tables else 'MISSING'
        print(f"  {t:<30} {status}")
    print()


COMMANDS = {
    'extend': cmd_extend,
    'status': cmd_status,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=list(COMMANDS.keys()))
    args = parser.parse_args()
    COMMANDS[args.command](args)


if __name__ == '__main__':
    main()
