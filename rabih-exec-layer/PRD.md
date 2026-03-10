# Lucider AI: Slack Intelligence Platform - PRD v2.0

**Version:** 2.0 (post-call, post-proposal, post-clarification)
**Engineer:** Chudi Nnorukam
**Client:** Rabih Masri (rabih@lucider.ai)
**Sprint:** 4 weeks (fixed-price $6,000)
**Structure:** Project-based consulting, open to full-time if demo lands
**Date:** March 5, 2026

---

## Problem

Leadership in fast-scaling orgs (AI data centers, cloud infra, SaaS) gets status reports too late to course-correct. Teams report best-case scenarios in weekly standups while reality drifts. By the time a VP learns a program is failing, it's already failed. Decisions are buried in Slack threads. Commitments go untracked. Risks surface only after damage.

**Rabih's framing (from call):** "Instead of basically someone telling you what happened, you're seeing reality. And you see it as it's happening."

**Target buyer:** VP of Engineering, Chief of Staff, CEO at 50-500 person orgs competing in fast-moving categories (AI infra, cloud, fintech).

**Acquisition positioning:** Slack-native intelligence layer. Evolution path: human-to-human > human-to-AI-agent > AI-to-AI-agent monitoring. Positioned for acquisition by OpenAI/Slack/Salesforce ($500M-$6B range per Rabih's investor conversations).

---

## Solution

AI pipeline that continuously monitors a Slack workspace, extracts structured intelligence (decisions, commitments, risks, dependencies), builds an **execution graph** classifying program health as ON TRACK / AT RISK / FAILING, and surfaces it in an executive dashboard with evidence links back to source conversations.

**Core principle:** Inference, not reporting. No one files a status update. The AI reads the room.

---

## Users

| User | Need |
|------|------|
| CEO / Founder | "Is my company on track?" in one glance |
| VP Engineering | Which programs are drifting before weekly standup |
| Chief of Staff | Cross-team dependency visibility, unowned risks |
| Investor (demo audience) | See the system work live on a real/synthetic workspace |

---

## MVP Scope (4 weeks)

### In Scope (scope freeze after Week 1)

1. **Slack ingestion** - Socket Mode listener, real-time message events, thread context builder
2. **LLM extraction** - Decisions, commitments, risks, dependencies as structured JSON via Claude
3. **Program detection** - Channel-to-program mapping (explicit config, implicit clustering as stretch)
4. **Execution graph** - Tri-state health classification (ON TRACK / AT RISK / FAILING) per program
5. **Commitment tracker** - Who promised what, by when, current status
6. **Dependency mapper** - Cross-team links (engineering blocked on design)
7. **Executive dashboard** - Program health grid, drill-down to evidence, timeline view, search
8. **Slack bot notifications** - Daily digest, threshold alerts ("Project X moved to FAILING")
9. **30-day backfill** - Ingest historical messages for demo richness
10. **Configurable scoring** - JSON/YAML config for classification weights and thresholds
11. **Evaluation set** - 50-100 labeled messages for precision/recall measurement

### Out of Scope (v2 backlog)

- DM processing
- Multi-workspace / OAuth / SaaS multi-tenancy
- GDPR/SOC2 compliance
- Mobile
- Write-back actions (resolve from dashboard)
- AI-to-AI agent monitoring layer

---

## Architecture

```
[Slack Workspace]
      |
      | Socket Mode (no public URL needed for demo)
      v
[FastAPI Backend] -----> [Redis Queue]
      |                       |
      |                       v
      |              [Extraction Worker]
      |                  |---- fetch full thread from Slack API
      |                  |---- Claude API (structured output)
      |                  |---- dedup + confidence filter + store
      |                       |
      v                       v
[PostgreSQL + pgvector] <-----+
      |
      | 6-hour cron: re-score all programs
      v
[Execution Graph Builder]
      |
      |---- classify: ON TRACK / AT RISK / FAILING
      |---- dependency map across programs
      |---- timeline tracking (status transitions)
      v
[React Dashboard] <--- WebSocket (live updates)
      |
[Slack Bot] --- daily digest, threshold alerts, weekly summary
```

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Socket Mode vs Events API | Socket Mode | No public URL needed for demo. Simpler setup. |
| Scoring engine | JSON config file | Rabih asked: "Can we iterate on classification logic without major refactoring?" Answer: yes, weights/thresholds in config, not code. |
| pgvector | Supporting search layer | NOT core to status inference. Enables semantic search ("show me similar conversations"). First to defer if cutting scope. |
| Program detection | Explicit mapping first | If implicit channel clustering is noisy in Week 2, fallback to admin config screen (tag channels to programs). Demo credibility preserved. |
| Evaluation | Labeled eval set in Week 1 | 50-100 messages manually tagged. Measure precision/recall. Target 80%+ before moving to inference. |

---

## Data Model

### Existing tables (already in code)

```sql
channels (id, slack_channel_id, name, enabled, added_at)
threads (id, channel_id, slack_thread_ts, last_message_ts, processed_at, prompt_version)
extractions (id, thread_id, type, text, attributed_to, confidence, extracted_at, prompt_version)
actions (id, extraction_id, assignee_id, due_date, status, created_at)
drift_events (id, action_id, detected_at, days_overdue)
```

### New tables (Week 2)

```sql
-- Programs / initiatives tracked by the execution graph
programs (
  id              SERIAL PRIMARY KEY,
  name            TEXT NOT NULL,
  description     TEXT,
  status          TEXT DEFAULT 'on_track'
                    CHECK (status IN ('on_track', 'at_risk', 'failing')),
  score           JSONB,           -- breakdown: {risks: 2, overdue: 1, blocked: 0, ...}
  last_scored_at  TIMESTAMPTZ,
  created_at      TIMESTAMPTZ DEFAULT NOW()
)

-- Maps channels to programs (explicit mapping, admin-configured)
program_channels (
  id              SERIAL PRIMARY KEY,
  program_id      INT REFERENCES programs(id) ON DELETE CASCADE,
  channel_id      INT REFERENCES channels(id),
  UNIQUE(program_id, channel_id)
)

-- Commitments (richer than actions, tracks owner + deadline + evidence)
commitments (
  id              SERIAL PRIMARY KEY,
  program_id      INT REFERENCES programs(id),
  extraction_id   INT REFERENCES extractions(id),
  owner_id        TEXT,            -- slack user ID
  text            TEXT NOT NULL,
  due_date        DATE,
  status          TEXT DEFAULT 'open'
                    CHECK (status IN ('open', 'completed', 'overdue', 'drifted')),
  created_at      TIMESTAMPTZ DEFAULT NOW()
)

-- Dependencies between programs
dependencies (
  id              SERIAL PRIMARY KEY,
  from_program_id INT REFERENCES programs(id),
  to_program_id   INT REFERENCES programs(id),
  extraction_id   INT REFERENCES extractions(id),
  description     TEXT,
  status          TEXT DEFAULT 'active'
                    CHECK (status IN ('active', 'blocked', 'resolved')),
  created_at      TIMESTAMPTZ DEFAULT NOW()
)

-- Status change history for timeline view
status_history (
  id              SERIAL PRIMARY KEY,
  program_id      INT REFERENCES programs(id),
  old_status      TEXT,
  new_status      TEXT,
  trigger_text    TEXT,            -- what caused the transition
  scored_at       TIMESTAMPTZ DEFAULT NOW()
)

-- Evaluation set for precision/recall tracking
eval_labels (
  id              SERIAL PRIMARY KEY,
  thread_id       INT REFERENCES threads(id),
  expected_type   TEXT CHECK (expected_type IN ('decision', 'action', 'risk', 'dependency', 'none')),
  expected_text   TEXT,
  labeled_by      TEXT DEFAULT 'manual',
  labeled_at      TIMESTAMPTZ DEFAULT NOW()
)
```

### Scoring Config (YAML)

```yaml
# config/scoring.yaml - Execution graph classification rules
# Editable without code changes. Re-scored every 6 hours.

classification:
  on_track:
    max_high_risks: 0
    max_overdue_commitments: 0
    max_blocked_dependencies: 0

  at_risk:
    # Any of these conditions triggers AT RISK
    has_high_risks_with_owner: true
    max_overdue_commitments: 2
    max_blocked_dependencies: 1

  failing:
    # Any of these conditions triggers FAILING
    has_unowned_risks: true
    has_conflicting_decisions: true
    min_overdue_commitments: 3
    min_blocked_dependencies: 2

weights:
  unowned_risk: 10
  high_risk_owned: 3
  medium_risk: 1
  overdue_commitment: 5
  blocked_dependency: 7
  conflicting_decision: 8
  stale_thread_days: 1     # per day of silence

thresholds:
  at_risk_score: 10
  failing_score: 25

extraction:
  confidence_min: 0.70
  prompt_version: "v2.0"
```

---

## LLM Extraction Contract

**Input:** Full thread text with speaker labels + system prompt
**Output:** Structured JSON via Claude tool_use

### Enhanced extraction schema (v2)

```json
{
  "decisions": [
    {
      "text": "We are moving auth to AWS Cognito",
      "decided_by": "U012ABC3DEF",
      "confidence": 0.92,
      "conflicts_with": null
    }
  ],
  "commitments": [
    {
      "text": "Set up Cognito staging environment",
      "owner": "U045XYZ6GHI",
      "due_date": "2026-03-15",
      "confidence": 0.88
    }
  ],
  "risks": [
    {
      "text": "Migration could break existing session tokens",
      "raised_by": "U078JKL9MNO",
      "severity": "high",
      "has_owner": true,
      "owner": "U012ABC3DEF",
      "confidence": 0.81
    }
  ],
  "dependencies": [
    {
      "text": "Auth migration blocked on design team's API spec review",
      "from_team": "engineering",
      "to_team": "design",
      "status": "blocked",
      "confidence": 0.75
    }
  ]
}
```

**Changes from v1:** Added `commitments` (separate from actions), `dependencies`, risk `has_owner`/`owner` fields, decision `conflicts_with`.

---

## Execution Graph Logic

### Scoring algorithm (runs every 6 hours per program)

```python
def score_program(program_id: int, config: ScoringConfig) -> ProgramScore:
    risks = get_risks_for_program(program_id)
    commitments = get_commitments_for_program(program_id)
    dependencies = get_dependencies_for_program(program_id)

    score = 0
    breakdown = {}

    # Risk scoring
    unowned_high = [r for r in risks if r.severity == 'high' and not r.has_owner]
    owned_high = [r for r in risks if r.severity == 'high' and r.has_owner]
    medium = [r for r in risks if r.severity == 'medium']

    score += len(unowned_high) * config.weights.unowned_risk
    score += len(owned_high) * config.weights.high_risk_owned
    score += len(medium) * config.weights.medium_risk

    # Commitment scoring
    overdue = [c for c in commitments if c.status == 'overdue']
    score += len(overdue) * config.weights.overdue_commitment

    # Dependency scoring
    blocked = [d for d in dependencies if d.status == 'blocked']
    score += len(blocked) * config.weights.blocked_dependency

    # Classify
    if score >= config.thresholds.failing_score:
        status = 'failing'
    elif score >= config.thresholds.at_risk_score:
        status = 'at_risk'
    else:
        status = 'on_track'

    return ProgramScore(status=status, score=score, breakdown=breakdown)
```

### Classification rules (from Rabih's spec)

| Status | Conditions |
|--------|-----------|
| ON TRACK | No high-severity risks, no overdue commitments, no blocked dependencies |
| AT RISK | High-severity risks but owned, some overdue commitments, partially blocked |
| FAILING | Unowned risks, conflicting decisions, multiple overdue commitments, critical blocks |

---

## Dashboard Views

### 1. Program Health Overview (landing page)
- Grid of program cards: green (ON TRACK) / amber (AT RISK) / red (FAILING)
- Each card shows: program name, risk count, overdue commitments, last status change
- Click to drill down

### 2. Program Drill-Down
- Status badge + score breakdown
- Risks tab: severity, owner, source conversation link
- Commitments tab: who, what, deadline, status
- Dependencies tab: cross-team links, blocked/active
- Timeline: when did status change? What triggered it?

### 3. Team Health Heatmap
- Which teams generate the most risks / overdue items
- Cross-team dependency density

### 4. Search
- "Show me all unowned risks across engineering"
- Semantic search via pgvector (stretch goal)

### 5. Slack Bot Notifications
- Daily digest to #executive-updates
- Threshold alert: "Project X moved from AT RISK to FAILING" (immediate)
- Weekly AI-generated narrative summary

---

## Tech Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Backend | Python 3.12 + FastAPI | Already scaffolded. Async-native. |
| Slack SDK | slack-bolt + Socket Mode | No public URL for demo. Official SDK. |
| LLM | Claude API (claude-sonnet-4-6) | Best structured output. Tool use for JSON. |
| Database | PostgreSQL (Supabase) + pgvector | Relational for execution graph. Vector for search. |
| Queue | Redis (list queue) | Already scaffolded. Lightweight. |
| Frontend | React + Tailwind + Tremor | Dashboard components out of the box. |
| Deployment | Docker Compose (demo), AWS ECS (prod) | Docker for demo simplicity. |
| Notifications | Slack Bot (same workspace) | Insights surface where people work. |

---

## Development Milestones

### Week 1: Data Foundation (scope freeze at end)
**Goal:** Slack connected, messages flowing, extraction working, eval set built.

- [x] FastAPI skeleton, Redis queue, PostgreSQL schema (DONE - exists)
- [x] Slack webhook + signature verification (DONE - exists)
- [x] Extraction worker with Claude tool_use (DONE - exists)
- [x] Drift detection cron (DONE - exists)
- [ ] Switch to Socket Mode (replace webhook with persistent connection)
- [ ] Enhance extraction schema: add commitments, dependencies, risk ownership
- [ ] Update prompt to v2.0 with enhanced extraction targets
- [ ] Build evaluation set: label 50-100 messages from test workspace
- [ ] Measure precision/recall, tune prompts to 80%+ accuracy
- [ ] 30-day backfill pipeline (paginated history fetch)
- [ ] Database migration: add programs, commitments, dependencies tables

**Deliverable:** Messages flowing into DB. Can query "show me all commitments from #engineering this week" via API. Eval set shows 80%+ precision.

### Week 2: Execution Graph + Program Intelligence
**Goal:** System classifies program health automatically.

- [ ] Program detection: explicit channel-to-program mapping (admin config)
- [ ] If noisy: fallback to simple config UI (tag channels to programs)
- [ ] Commitment tracker: owner, deadline, status, overdue detection
- [ ] Risk classifier: severity, ownership tracking
- [ ] Dependency mapper: cross-team blocked/active links
- [ ] Scoring engine: configurable YAML weights/thresholds
- [ ] Tri-state execution graph: ON TRACK / AT RISK / FAILING per program
- [ ] 6-hour re-scoring cron
- [ ] Status history tracking (timeline of transitions)
- [ ] API: GET /programs (status, score breakdown, evidence links)

**Deliverable:** API returns program health with evidence. Scoring is configurable without code changes.

### Week 3: Executive Dashboard
**Goal:** Visual interface an investor can understand in 30 seconds.

- [ ] Program health grid: colored cards (green/amber/red)
- [ ] Drill-down: click program > risks, commitments, dependencies, source links
- [ ] Timeline view: status transitions with trigger explanations
- [ ] Team health heatmap: risk/overdue density by team
- [ ] Search: filter by program, team, risk severity
- [ ] Real-time updates via WebSocket
- [ ] Evidence linking: click through from AT RISK to exact Slack conversation

**Deliverable:** Working dashboard Rabih can demo to investors. Click from overview > program > evidence.

### Week 4: Polish, Notifications, Demo Prep
**Goal:** Demo-ready product.

- [ ] Slack bot: daily digest to #executive-updates
- [ ] Threshold alerts: immediate push on status transitions
- [ ] Weekly AI-generated executive summary narrative
- [ ] Edge case hardening: edited messages, deleted messages, private channels
- [ ] Performance: dashboard loads < 2s with 30 days of data
- [ ] Demo script: seed data, walkthrough flow, investor talking points
- [ ] Documentation: setup guide, architecture diagram, API reference
- [ ] pgvector semantic search (stretch - defer if behind schedule)

**Deliverable:** Complete demo-ready system. Live Slack workspace with real-time inference.

---

## Evaluation Framework

### Precision/Recall Protocol (Week 1)

1. Select 50-100 diverse Slack threads (mix of channels, topics, lengths)
2. Manually label each: what decisions/commitments/risks/dependencies exist?
3. Run extraction pipeline on same threads
4. Compare: true positives, false positives, false negatives
5. Compute precision (are extractions real?) and recall (did we miss any?)
6. Target: precision >= 80%, recall >= 70% before moving to Week 2
7. Store labels in `eval_labels` table for regression testing

### Quality Gates

| Gate | Metric | Threshold | When |
|------|--------|-----------|------|
| Extraction accuracy | Precision on eval set | >= 80% | End of Week 1 |
| Extraction coverage | Recall on eval set | >= 70% | End of Week 1 |
| Dashboard performance | Page load time | < 2s | End of Week 3 |
| Classification stability | Same input = same output | 100% | End of Week 2 |
| False alarm rate | AT RISK/FAILING on healthy programs | < 10% | End of Week 4 |

---

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| LLM extraction quality (messy Slack data) | Medium | High | Structured output via tool_use. Eval set + prompt tuning loop. 80%+ target. |
| Program detection ambiguity | Medium | Medium | Start explicit (admin maps channels). Implicit clustering is stretch. |
| Slack API rate limits on backfill | Low | Low | Backoff + batch. Run overnight. |
| Scope creep | Medium | High | Freeze after Week 1. v2 backlog. |
| Demo data quality | Medium | Medium | Two tracks: real workspace if available, high-quality synthetic 50-person org. |
| Scoring too aggressive (false FAILING) | Medium | High | Configurable thresholds. Conservative defaults. Manual override. |

---

## Assumptions

| # | Assumption | Status |
|---|-----------|--------|
| A1 | Single workspace for MVP | CONFIRMED |
| A2 | Channels only, no DMs | CONFIRMED |
| A3 | "Execution graph" = tri-state program health, not literal graph DB | CONFIRMED on call |
| A4 | Python + React stack | CONFIRMED in proposal |
| A5 | Supabase PostgreSQL | CONFIRMED |
| A6 | Socket Mode acceptable (no public URL) | CONFIRMED in proposal |
| A7 | Scope freeze after Week 1 | CONFIRMED by Rabih |
| A8 | 25-30 hrs/week commitment | CONFIRMED in clarification |
| A9 | Rabih provides test Slack workspace or we build synthetic | PENDING - need workspace access |

---

## What's Already Built (v0.1 scaffold)

| Component | Status | Files |
|-----------|--------|-------|
| FastAPI app + lifespan | DONE | backend/app/main.py |
| Slack webhook + signature verification | DONE | backend/app/main.py |
| Extraction worker (Claude tool_use) | DONE | backend/app/extractor.py |
| Database layer (asyncpg pool) | DONE | backend/app/database.py |
| Redis queue (push/pop threads) | DONE | backend/app/redis_client.py |
| Drift detection (hourly cron) | DONE | backend/app/drift.py |
| Config (pydantic-settings) | DONE | backend/app/config.py |
| Slack thread fetch + formatting | DONE | backend/app/slack_client.py |
| Dashboard API (stats, decisions, actions, risks, drift) | DONE | backend/app/main.py |
| Basic dashboard HTML | DONE | dashboard/index.html |
| Landing page | DONE | landing/ |

### What needs building

| Component | Priority | Week |
|-----------|----------|------|
| Socket Mode migration | P0 | 1 |
| Enhanced extraction (commitments, dependencies) | P0 | 1 |
| Programs + execution graph tables | P0 | 1-2 |
| Scoring engine (configurable YAML) | P0 | 2 |
| Program health API | P0 | 2 |
| React dashboard (replace static HTML) | P0 | 3 |
| Slack bot notifications | P1 | 4 |
| pgvector semantic search | P2 | 4 (stretch) |
| Eval set + precision/recall tooling | P1 | 1 |
| 30-day backfill pipeline | P1 | 1 |

---

## Success Criteria (End of Sprint)

- [ ] Slack bot reading messages from 5+ channels in real-time
- [ ] 200+ extractions in database with 80%+ precision
- [ ] 3+ programs classified with ON TRACK / AT RISK / FAILING status
- [ ] Dashboard loads in < 2s, shows program grid + drill-down + evidence links
- [ ] Scoring config editable without code changes
- [ ] Daily Slack digest firing correctly
- [ ] Rabih can walk investors through live demo in < 10 minutes
- [ ] Documentation: setup guide, architecture diagram, API reference

---

## Next Steps

1. Rabih reviews this PRD v2.0
2. Sign NDA, receive detailed spec if any additions
3. Rabih provides or we set up test Slack workspace
4. Day 1: Socket Mode migration + enhanced extraction + eval set
5. Weekly check-ins (Monday) to demo progress and adjust priorities
