#!/usr/bin/env python3
"""Init DB — Create marketing_leads.db schema on VPS or locally.

Usage:
    python3 init_db.py              # Create DB at default path
    python3 init_db.py --path /opt/openclaw/data/marketing_leads.db
"""

import argparse
import os
import sqlite3

DEFAULT_DB = os.path.join(os.path.dirname(__file__), '..', 'data', 'marketing_leads.db')

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY,
    linkedin_url TEXT UNIQUE NOT NULL,
    name TEXT,
    company TEXT,
    company_domain TEXT,
    title TEXT,
    email TEXT,
    email_verified INTEGER DEFAULT 0,
    icp_score INTEGER DEFAULT 5,
    status TEXT DEFAULT 'prospect',
    profile_viewed_at TEXT,
    post_liked_at TEXT,
    connection_sent_at TEXT,
    connection_checked_at TEXT,
    connection_accepted_at TEXT,
    message_sent_at TEXT,
    message_replied_at TEXT,
    email_found_at TEXT,
    email_verified_at TEXT,
    email_seq_started_at TEXT,
    email_seq_num INTEGER DEFAULT 0,
    last_email_sent_at TEXT,
    email_opened INTEGER DEFAULT 0,
    email_clicked INTEGER DEFAULT 0,
    email_replied_at TEXT,
    converted_at TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS daily_caps (
    date TEXT PRIMARY KEY,
    connections_sent INTEGER DEFAULT 0,
    profiles_viewed INTEGER DEFAULT 0,
    emails_sent INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS email_events (
    id INTEGER PRIMARY KEY,
    lead_id INTEGER REFERENCES leads(id),
    event_type TEXT,
    email_num INTEGER,
    brevo_message_id TEXT,
    occurred_at TEXT DEFAULT (datetime('now'))
);
"""

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--path', default=DEFAULT_DB)
    args = parser.parse_args()

    db_path = os.path.abspath(args.path)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()

    print(f"DB initialized: {db_path}")
    print("Tables: leads, daily_caps, email_events")

if __name__ == '__main__':
    main()
