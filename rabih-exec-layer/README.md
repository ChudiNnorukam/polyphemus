# Execution Intelligence Layer

AI-native Slack → LLM extraction → PostgreSQL → dashboard.
Turns Slack conversations into structured execution visibility for leadership.

## Architecture

```
Slack Events API → FastAPI webhook → Redis queue → Extraction worker (Claude)
→ PostgreSQL → Next.js dashboard
```

## Stack

- **Backend**: Python 3.11 + FastAPI + asyncpg + redis + slack-bolt + anthropic
- **Frontend**: Next.js 14 + Tailwind
- **DB**: PostgreSQL (Supabase)
- **LLM**: Claude claude-sonnet-4-6 (structured output via tools API)
- **Deploy**: Railway (backend) + Vercel (frontend)

## Setup

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# fill in SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET, DATABASE_URL, ANTHROPIC_API_KEY, API_TOKEN

# Run schema migration (Supabase SQL editor or psql)
psql $DATABASE_URL < migrations/001_initial.sql

# Start (DRY_RUN=true by default — no LLM calls)
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local
# set NEXT_PUBLIC_API_URL and NEXT_PUBLIC_API_TOKEN

npm run dev
```

## Slack App Config

1. Create app at api.slack.com/apps
2. **Event Subscriptions**: enable, set Request URL to `https://<your-backend>/slack/events`
3. Subscribe to bot events: `message.channels`, `message.groups`
4. **OAuth Scopes**: `channels:history`, `groups:history`, `channels:read`, `groups:read`
5. Install to workspace, copy Bot Token + Signing Secret to `.env`

## Environment Variables

See `backend/.env.example` for all required variables.

Key ones:
- `DRY_RUN=true` — set to `false` to enable live LLM extraction
- `EXTRACTION_CONFIDENCE_MIN=0.70` — only store extractions above this confidence
- `DRIFT_THRESHOLD_DAYS=3` — actions silent for N days are flagged as drifted

## Deployment

**Backend → Railway**
- Connect GitHub repo, set root to `backend/`
- Set all env vars in Railway dashboard
- Railway auto-detects `Procfile`

**Frontend → Vercel**
- Connect GitHub repo, set root to `frontend/`
- Set `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_API_TOKEN`
