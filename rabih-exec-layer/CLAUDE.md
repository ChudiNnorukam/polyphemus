# Lucider AI: Slack Intelligence Platform

## Project Context

- **Codebase**: `/Users/chudinnorukam/Projects/business/rabih-exec-layer/`
- **PRD**: `PRD.md` (v2.0, comprehensive, source of truth for all requirements)
- **Client**: Rabih Masri (rabih@lucider.ai), Tech Entrepreneur
- **Sprint**: 4 weeks, fixed-price, scope freeze after Week 1
- **Goal**: Investor-ready demo that extracts execution intelligence from Slack

## Architecture Overview

```
Slack (Socket Mode) -> FastAPI -> Redis Queue -> Extraction Worker (Claude) -> PostgreSQL
                                                                                    |
                                                                              Scoring Engine
                                                                                    |
                                                                           React Dashboard + Slack Bot
```

## Directory Structure

```
rabih-exec-layer/
  backend/
    app/
      main.py          # FastAPI app, Slack webhook, dashboard API routes
      extractor.py     # Claude extraction worker (tool_use)
      database.py      # asyncpg pool, all SQL operations
      slack_client.py  # Slack SDK, thread fetching, formatting
      drift.py         # Drift detection cron
      config.py        # Pydantic settings
      redis_client.py  # Redis queue
      prompts/         # LLM prompt templates
    migrations/        # SQL migrations
    requirements.txt
    .env.example
  dashboard/           # Static HTML dashboard (to be replaced with React)
  frontend/            # Landing page / marketing (not the product dashboard)
  landing/             # Rabih landing page
  PRD.md               # Product Requirements Document v2.0
```

## Tech Stack

- **Backend**: Python 3.12, FastAPI, asyncpg, redis, slack-bolt, anthropic SDK
- **LLM**: Claude claude-sonnet-4-6 via tool_use (structured JSON extraction)
- **Database**: PostgreSQL (Supabase), pgvector for semantic search (stretch)
- **Queue**: Redis list queue (no Celery overhead)
- **Frontend**: React + Tailwind + Tremor (Week 3 build)
- **Deployment**: Docker Compose (demo), AWS ECS (production)

## Key Commands

```bash
# Backend
cd rabih-exec-layer/backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Database migrations
psql $DATABASE_URL < migrations/001_init.sql

# Run tests (when created)
python -m pytest tests/ -v
```

## Build Rules

- **PRD is source of truth**: All features, scope, and priorities come from PRD.md
- **Scope freeze**: No new features after Week 1. New ideas go to v2 backlog in PRD.
- **Scoring config is YAML**: Classification logic must be configurable without code changes. File: `config/scoring.yaml`
- **Evidence linking**: Every status classification MUST trace back to specific Slack messages. This is a core design principle.
- **Extraction schema v2**: Decisions, commitments (not just actions), risks (with ownership), dependencies. See PRD for full schema.
- **Eval set**: 50-100 labeled messages. Precision >= 80% before moving past Week 1.

## Data Model Summary

### Existing (v0.1)
- `channels`, `threads`, `extractions`, `actions`, `drift_events`

### New (Week 1-2, per PRD)
- `programs` - tracked initiatives with tri-state health (on_track/at_risk/failing)
- `program_channels` - maps channels to programs (explicit admin config)
- `commitments` - richer than actions: owner, deadline, overdue tracking
- `dependencies` - cross-team blocked/active links
- `status_history` - timeline of program status transitions
- `eval_labels` - ground truth for precision/recall measurement

## Extraction Contract

Claude extracts via tool_use into 4 categories:
1. **Decisions** - what was decided, by whom, conflicts
2. **Commitments** - who promised what, by when
3. **Risks** - severity (low/medium/high), ownership status
4. **Dependencies** - cross-team blocks

Confidence threshold: 0.70 minimum. Prompt version tracked on every extraction.

## Execution Graph Scoring

Programs scored every 6 hours using configurable weights:

| Signal | Weight |
|--------|--------|
| Unowned high risk | 10 |
| Owned high risk | 3 |
| Medium risk | 1 |
| Overdue commitment | 5 |
| Blocked dependency | 7 |
| Conflicting decision | 8 |

Score >= 10 = AT RISK, >= 25 = FAILING. All configurable in `config/scoring.yaml`.

## Safety Rules

- **NEVER hardcode scoring thresholds** in Python. Always read from config.
- **NEVER skip confidence filtering**. Extractions below threshold pollute the graph.
- **NEVER process DMs** in v1. Channels only.
- **ALWAYS record prompt_version** on extractions for regression safety.
- **ALWAYS link evidence** - every risk/commitment/decision must reference its source thread.

## API Endpoints

### Existing
- `POST /slack/events` - Slack webhook
- `GET /api/stats` - Dashboard summary counts
- `GET /api/decisions` - Decision log
- `GET /api/actions` - Action list
- `GET /api/risks` - Risk register
- `GET /api/drift` - Drift alerts
- `GET /health` - Health check

### Planned (Week 2)
- `GET /api/programs` - Program list with health status
- `GET /api/programs/:id` - Program detail (risks, commitments, dependencies, timeline)
- `POST /api/programs` - Create/configure program
- `POST /api/programs/:id/channels` - Map channels to program
- `GET /api/programs/:id/timeline` - Status change history
- `GET /api/search` - Semantic search across extractions

### Planned (Week 4)
- `POST /api/notifications/digest` - Trigger daily digest
- `GET /api/eval` - Precision/recall metrics
