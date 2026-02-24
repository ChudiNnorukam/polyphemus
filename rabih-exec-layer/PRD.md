# Execution Intelligence Layer — MVP PRD

**Version:** 0.1 (pre-call assumptions — validate flagged items on the call)
**Engineer:** Chudi Nnorukam
**Sprint:** 4-6 weeks

---

## Problem

Leadership in fast-moving orgs loses track of decisions made in Slack. Commitments are made,
risks are flagged, actions are assigned — all of it disappears into conversation history with
no structured visibility layer. Nobody knows what was decided, who owns what, or what has drifted.

---

## Solution

An AI-native pipeline that continuously monitors Slack, extracts structured intelligence
(decisions, actions, risks, drift), and surfaces it in a clean read-only dashboard for leadership.

---

## Users

| User | Need |
|------|------|
| Leadership / Founder | Executive visibility: what was decided, who owns it, what's drifting |
| Team Lead | Accountability: see your open actions, flag risks |

---

## MVP Scope

### In Scope

1. Slack Events API ingestion (public/private channels only — no DMs in v1)
2. Thread-level context extraction (not single messages)
3. LLM extraction: decisions, actions, risks
4. Drift detection: open actions with no follow-up in N days
5. Read-only dashboard: decision log, open actions, risks, drift alerts
6. Single Slack workspace (no multi-tenant in v1)
7. Hourly batch processing (not real-time — acceptable for v1)

### Out of Scope (v1)

- DM processing
- Multi-workspace / SaaS multi-tenancy
- Real-time streaming
- Write-back to Slack (notifications, status updates)
- Action completion tracking (manual close only)
- Mobile

---

## Architecture

```
[Slack workspace]
      |
      | Events API webhook (POST /slack/events)
      v
[FastAPI backend]
      |
      | put message + thread_id in queue
      v
[Redis queue]
      |
      | worker pulls
      v
[Extraction worker]
      |---- fetch full thread from Slack API
      |---- send to Claude (structured JSON schema output)
      |---- dedup + store
      v
[PostgreSQL (Supabase)]
      |
      | hourly cron: drift detection
      v
[Drift engine]
      |
[Next.js dashboard] reads PostgreSQL
```

---

## Data Model

```sql
-- Slack channels being monitored
channels (
  id              SERIAL PRIMARY KEY,
  slack_channel_id TEXT UNIQUE NOT NULL,
  name            TEXT,
  enabled         BOOLEAN DEFAULT true,
  added_at        TIMESTAMPTZ DEFAULT NOW()
)

-- Every thread that has been processed
threads (
  id              SERIAL PRIMARY KEY,
  channel_id      INT REFERENCES channels(id),
  slack_thread_ts TEXT NOT NULL,
  last_message_ts TEXT,
  processed_at    TIMESTAMPTZ,
  prompt_version  TEXT,
  UNIQUE(channel_id, slack_thread_ts)
)

-- Raw extraction output from LLM
extractions (
  id              SERIAL PRIMARY KEY,
  thread_id       INT REFERENCES threads(id),
  type            TEXT CHECK (type IN ('decision', 'action', 'risk')),
  text            TEXT NOT NULL,
  attributed_to   TEXT,                -- slack user ID
  confidence      FLOAT NOT NULL,
  extracted_at    TIMESTAMPTZ DEFAULT NOW(),
  prompt_version  TEXT NOT NULL
)

-- Richer model for actions (drift tracking)
actions (
  id              SERIAL PRIMARY KEY,
  extraction_id   INT REFERENCES extractions(id),
  assignee_id     TEXT,               -- slack user ID
  due_date        DATE,
  status          TEXT DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'drifted'))
)

-- Drift events (when an action has gone silent)
drift_events (
  id              SERIAL PRIMARY KEY,
  action_id       INT REFERENCES actions(id),
  detected_at     TIMESTAMPTZ DEFAULT NOW(),
  days_overdue    INT
)
```

---

## LLM Extraction Contract

**Input:** Full thread text with speaker labels + system prompt
**Output:** Strict JSON schema enforced via Claude structured output mode

```json
{
  "decisions": [
    {
      "text": "We are moving the auth service to AWS Cognito",
      "decided_by": "U012ABC3DEF",
      "confidence": 0.92
    }
  ],
  "actions": [
    {
      "text": "Set up Cognito staging environment",
      "assigned_to": "U045XYZ6GHI",
      "due_date": "2026-03-01",
      "confidence": 0.88
    }
  ],
  "risks": [
    {
      "text": "Migration could break existing session tokens in prod",
      "raised_by": "U078JKL9MNO",
      "severity": "high",
      "confidence": 0.81
    }
  ]
}
```

**Confidence threshold:** Only persist extractions with `confidence >= 0.70` to the database.
**Prompt versioning:** Every extraction records `prompt_version` (e.g., `v1.0`, `v1.1`) so
schema and prompt changes don't contaminate historical data.

---

## Drift Detection Logic

Runs as a cron job every hour.

```
FOR each open action in database:
  1. Get action keywords (key nouns from action.text)
  2. Query messages table: any message in this channel mentioning these keywords in last N days?
  3. If NO match AND action.created_at > N days ago:
     → mark action.status = 'drifted'
     → insert drift_event
```

`N` is configurable via env var `DRIFT_THRESHOLD_DAYS` (default: 3).

---

## Dashboard Views

### 1. Decision Log
Table: date | decision | decided by | channel | confidence | linked actions

### 2. Open Actions
Table: action | assigned to | due date | status | source decision | age

### 3. Risk Register
Table: risk | raised by | severity | channel | date | still open?

### 4. Drift Alerts
Table: action | assignee | days silent | original due date | source thread link

---

## Tech Stack

| Layer | Choice | Reason |
|-------|--------|--------|
| Backend | Python 3.11 + FastAPI | Fast, async, Slack SDK native |
| Queue | Redis (list queue) | No Celery overhead needed at this scale |
| Database | PostgreSQL via Supabase | Instant hosted setup, built-in auth |
| LLM | Claude claude-sonnet-4-6 | Structured output, best JSON adherence |
| Dashboard | Next.js 14 + Tailwind + shadcn/ui | Fast to build, clean by default |
| Backend hosting | Railway | Push-to-deploy, easy env management |
| Frontend hosting | Vercel | Zero config Next.js |
| Slack SDK | `slack_bolt` (Python) | Official, handles signature verification |

---

## Non-Functional Requirements

- Idempotent processing: same thread processed twice = no duplicate extractions
- Privacy: bot only reads channels it is explicitly invited to
- Graceful failure: LLM extraction failure logs error, does not crash the worker
- Cost control: Claude claude-sonnet-4-6 input tokens ~ $0.003/1K. A 50-message thread is ~1,500 tokens = $0.0045/thread. Budget well within reason.

---

## Success Criteria (End of Sprint)

- [ ] Slack bot installed and reading messages from 3+ channels
- [ ] 100+ extractions in database
- [ ] False positive rate < 20% on manual spot check of 30 extractions
- [ ] Dashboard loads in < 2s and shows all 4 views
- [ ] Drift detection firing correctly on test data
- [ ] Prompt version recorded on all extractions

---

## Assumptions (validate on call with Rabih)

| # | Assumption | Risk if wrong |
|---|-----------|--------------|
| A1 | Single workspace for MVP | Low — easy to add multi later |
| A2 | Channels only, no DMs | Medium — DMs need different permissions + legal |
| A3 | Batch/hourly processing is acceptable | Low — can add real-time later |
| A4 | "Execution graph" = relationships (decision→actions→risks), not a literal graph DB | HIGH — changes data model significantly |
| A5 | Python/Next.js stack acceptable | Low — he said stack-agnostic |
| A6 | Supabase/Railway hosting acceptable | Low |

---

## Build Phases

| Phase | Work | Days |
|-------|------|------|
| 1 | Slack app setup, Events API webhook, FastAPI skeleton, Redis queue | 1-3 |
| 2 | Database schema, Supabase setup, storage layer | 3-5 |
| 3 | LLM extraction worker: prompt, JSON schema, dedup, confidence filter | 5-10 |
| 4 | Drift detection cron job | 10-12 |
| 5 | Next.js dashboard: 4 views, Supabase queries | 12-22 |
| 6 | Integration testing, extraction quality tuning, deployment | 22-28 |
