-- Execution Intelligence Layer — Initial Schema
-- Run once against your Supabase (or any PostgreSQL) database

CREATE TABLE IF NOT EXISTS channels (
    id               SERIAL PRIMARY KEY,
    slack_channel_id TEXT UNIQUE NOT NULL,
    name             TEXT,
    enabled          BOOLEAN DEFAULT TRUE,
    added_at         TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS threads (
    id               SERIAL PRIMARY KEY,
    channel_id       INT REFERENCES channels(id) ON DELETE CASCADE,
    slack_thread_ts  TEXT NOT NULL,
    last_message_ts  TEXT,
    processed_at     TIMESTAMPTZ,
    prompt_version   TEXT,
    UNIQUE (channel_id, slack_thread_ts)
);

CREATE INDEX IF NOT EXISTS idx_threads_channel ON threads (channel_id);
CREATE INDEX IF NOT EXISTS idx_threads_processed ON threads (processed_at);

CREATE TABLE IF NOT EXISTS extractions (
    id             SERIAL PRIMARY KEY,
    thread_id      INT REFERENCES threads(id) ON DELETE CASCADE,
    type           TEXT NOT NULL CHECK (type IN ('decision', 'action', 'risk')),
    text           TEXT NOT NULL,
    attributed_to  TEXT,
    confidence     FLOAT NOT NULL,
    extracted_at   TIMESTAMPTZ DEFAULT NOW(),
    prompt_version TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_extractions_thread ON extractions (thread_id);
CREATE INDEX IF NOT EXISTS idx_extractions_type   ON extractions (type);

CREATE TABLE IF NOT EXISTS actions (
    id             SERIAL PRIMARY KEY,
    extraction_id  INT REFERENCES extractions(id) ON DELETE CASCADE,
    assignee_id    TEXT,
    due_date       DATE,
    status         TEXT DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'drifted')),
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_actions_status    ON actions (status);
CREATE INDEX IF NOT EXISTS idx_actions_assignee  ON actions (assignee_id);

CREATE TABLE IF NOT EXISTS drift_events (
    id           SERIAL PRIMARY KEY,
    action_id    INT REFERENCES actions(id) ON DELETE CASCADE,
    detected_at  TIMESTAMPTZ DEFAULT NOW(),
    days_overdue INT
);

CREATE INDEX IF NOT EXISTS idx_drift_action ON drift_events (action_id);
