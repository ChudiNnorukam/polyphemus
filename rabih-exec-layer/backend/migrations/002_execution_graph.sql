-- Migration 002: Execution graph tables (programs, commitments, dependencies)
-- Run after 001_init.sql

BEGIN;

-- Programs / initiatives tracked by the execution graph
CREATE TABLE IF NOT EXISTS programs (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    status          TEXT DEFAULT 'on_track'
                      CHECK (status IN ('on_track', 'at_risk', 'failing')),
    score           JSONB DEFAULT '{}',
    last_scored_at  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Maps channels to programs (explicit mapping)
CREATE TABLE IF NOT EXISTS program_channels (
    id              SERIAL PRIMARY KEY,
    program_id      INT REFERENCES programs(id) ON DELETE CASCADE,
    channel_id      INT REFERENCES channels(id) ON DELETE CASCADE,
    UNIQUE(program_id, channel_id)
);

-- Commitments (richer than actions)
CREATE TABLE IF NOT EXISTS commitments (
    id              SERIAL PRIMARY KEY,
    program_id      INT REFERENCES programs(id),
    extraction_id   INT REFERENCES extractions(id),
    owner_id        TEXT,
    text            TEXT NOT NULL,
    due_date        DATE,
    status          TEXT DEFAULT 'open'
                      CHECK (status IN ('open', 'completed', 'overdue', 'drifted')),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Dependencies between programs
CREATE TABLE IF NOT EXISTS dependencies (
    id              SERIAL PRIMARY KEY,
    from_program_id INT REFERENCES programs(id),
    to_program_id   INT REFERENCES programs(id),
    extraction_id   INT REFERENCES extractions(id),
    description     TEXT,
    status          TEXT DEFAULT 'active'
                      CHECK (status IN ('active', 'blocked', 'resolved')),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Status change history for timeline view
CREATE TABLE IF NOT EXISTS status_history (
    id              SERIAL PRIMARY KEY,
    program_id      INT REFERENCES programs(id),
    old_status      TEXT,
    new_status      TEXT,
    trigger_text    TEXT,
    scored_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Evaluation set for precision/recall tracking
CREATE TABLE IF NOT EXISTS eval_labels (
    id              SERIAL PRIMARY KEY,
    thread_id       INT REFERENCES threads(id),
    expected_type   TEXT CHECK (expected_type IN ('decision', 'commitment', 'risk', 'dependency', 'none')),
    expected_text   TEXT,
    labeled_by      TEXT DEFAULT 'manual',
    labeled_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Add severity and ownership columns to extractions for richer risk modeling
ALTER TABLE extractions ADD COLUMN IF NOT EXISTS severity TEXT;
ALTER TABLE extractions ADD COLUMN IF NOT EXISTS has_owner BOOLEAN DEFAULT false;
ALTER TABLE extractions ADD COLUMN IF NOT EXISTS owner_id TEXT;

-- Indexes for scoring queries
CREATE INDEX IF NOT EXISTS idx_commitments_program_status ON commitments(program_id, status);
CREATE INDEX IF NOT EXISTS idx_dependencies_from_status ON dependencies(from_program_id, status);
CREATE INDEX IF NOT EXISTS idx_dependencies_to_status ON dependencies(to_program_id, status);
CREATE INDEX IF NOT EXISTS idx_status_history_program ON status_history(program_id, scored_at DESC);
CREATE INDEX IF NOT EXISTS idx_program_channels_program ON program_channels(program_id);
CREATE INDEX IF NOT EXISTS idx_program_channels_channel ON program_channels(channel_id);

COMMIT;
