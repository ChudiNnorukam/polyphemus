import asyncio
import hashlib
import hmac
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import database as db
from . import redis_client
from . import slack_client
from .config import settings, setup_logger
from .drift import drift_loop
from .extractor import ExtractionWorker

logger = setup_logger("main")

# ── Startup / shutdown ────────────────────────────────────────────────────────

_worker: ExtractionWorker | None = None
_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _worker

    await db.create_pool(settings.database_url)
    await redis_client.create_client(settings.redis_url)
    slack_client.init_client(settings.slack_bot_token)

    _worker = ExtractionWorker(settings)
    _tasks.append(asyncio.create_task(_worker.run(), name="extractor"))
    _tasks.append(asyncio.create_task(drift_loop(settings), name="drift"))
    logger.info("All background tasks started")

    yield

    if _worker:
        _worker.stop()
    for t in _tasks:
        t.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)
    await db.close_pool()
    await redis_client.close_client()
    logger.info("Shutdown complete")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Execution Intelligence API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Auth ──────────────────────────────────────────────────────────────────────

security = HTTPBearer()


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials.credentials != settings.api_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return credentials.credentials


# ── Slack webhook ─────────────────────────────────────────────────────────────

def _verify_slack_signature(request_body: bytes, timestamp: str, signature: str) -> bool:
    if abs(time.time() - int(timestamp)) > 300:
        return False
    base = f"v0:{timestamp}:{request_body.decode()}"
    expected = "v0=" + hmac.new(
        settings.slack_signing_secret.encode(),
        base.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.post("/slack/events")
async def slack_events(request: Request):
    body_bytes = await request.body()
    body = await request.json()

    timestamp = request.headers.get("X-Slack-Request-Timestamp", "0")
    signature = request.headers.get("X-Slack-Signature", "")

    if settings.slack_signing_secret and not _verify_slack_signature(body_bytes, timestamp, signature):
        raise HTTPException(status_code=403, detail="Invalid Slack signature")

    # Slack URL verification challenge
    if body.get("type") == "url_verification":
        return {"challenge": body["challenge"]}

    event = body.get("event", {})
    event_type = event.get("type", "")

    # Only process messages (not edits, deletions, bot messages)
    if event_type == "message":
        subtype = event.get("subtype")
        if subtype in (None, "thread_broadcast"):  # new messages only
            channel_id = event.get("channel", "")
            thread_ts = event.get("thread_ts") or event.get("ts", "")
            channel_name = event.get("channel_name", "")

            if channel_id and thread_ts:
                queued = await redis_client.push_thread(channel_id, thread_ts, channel_name)
                if queued:
                    logger.debug(f"Queued {channel_id}/{thread_ts}")

    return {"ok": True}


# ── Dashboard API routes ──────────────────────────────────────────────────────

@app.get("/api/stats")
async def get_stats(token: str = Depends(verify_token)):
    decisions = await db.fetch_val("SELECT COUNT(*) FROM extractions WHERE type = 'decision'") or 0
    open_actions = await db.fetch_val("SELECT COUNT(*) FROM actions WHERE status = 'open'") or 0
    risks = await db.fetch_val("SELECT COUNT(*) FROM extractions WHERE type = 'risk'") or 0
    drifting = await db.fetch_val("SELECT COUNT(*) FROM actions WHERE status = 'drifted'") or 0
    return {
        "decisions": decisions,
        "open_actions": open_actions,
        "risks": risks,
        "drifting": drifting,
    }


@app.get("/api/decisions")
async def get_decisions(
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    channel: Optional[str] = Query(None),
    token: str = Depends(verify_token),
):
    sql = """
        SELECT
            e.id, e.text, e.attributed_to, e.confidence, e.extracted_at,
            c.name AS channel_name, c.slack_channel_id,
            (SELECT COUNT(*) FROM actions a
             JOIN extractions ae ON ae.id = a.extraction_id
             WHERE ae.thread_id = e.thread_id AND ae.type = 'action') AS linked_actions
        FROM extractions e
        JOIN threads t ON t.id = e.thread_id
        JOIN channels c ON c.id = t.channel_id
        WHERE e.type = 'decision'
    """
    params: list = []
    if channel:
        sql += " AND c.slack_channel_id = $1"
        params.append(channel)

    sql += f" ORDER BY e.extracted_at DESC LIMIT ${len(params)+1} OFFSET ${len(params)+2}"
    params.extend([limit, offset])

    rows = await db.fetch_all(sql, *params)
    return {"decisions": rows, "count": len(rows)}


@app.get("/api/actions")
async def get_actions(
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    status_filter: Optional[str] = Query(None, alias="status"),
    assignee: Optional[str] = Query(None),
    token: str = Depends(verify_token),
):
    sql = """
        SELECT
            a.id, a.assignee_id, a.due_date, a.status, a.created_at,
            e.text AS action_text, e.confidence,
            c.name AS channel_name
        FROM actions a
        JOIN extractions e ON e.id = a.extraction_id
        JOIN threads t ON t.id = e.thread_id
        JOIN channels c ON c.id = t.channel_id
        WHERE 1=1
    """
    params: list = []

    if status_filter:
        params.append(status_filter)
        sql += f" AND a.status = ${len(params)}"
    if assignee:
        params.append(assignee)
        sql += f" AND a.assignee_id = ${len(params)}"

    sql += f" ORDER BY a.created_at DESC LIMIT ${len(params)+1} OFFSET ${len(params)+2}"
    params.extend([limit, offset])

    rows = await db.fetch_all(sql, *params)
    return {"actions": rows, "count": len(rows)}


@app.get("/api/risks")
async def get_risks(
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    severity: Optional[str] = Query(None),
    token: str = Depends(verify_token),
):
    sql = """
        SELECT
            e.id, e.text, e.attributed_to AS raised_by, e.confidence, e.extracted_at,
            c.name AS channel_name
        FROM extractions e
        JOIN threads t ON t.id = e.thread_id
        JOIN channels c ON c.id = t.channel_id
        WHERE e.type = 'risk'
    """
    params: list = []

    sql += f" ORDER BY e.extracted_at DESC LIMIT ${len(params)+1} OFFSET ${len(params)+2}"
    params.extend([limit, offset])

    rows = await db.fetch_all(sql, *params)
    return {"risks": rows, "count": len(rows)}


@app.get("/api/drift")
async def get_drift(
    limit: int = Query(50, le=200),
    token: str = Depends(verify_token),
):
    sql = """
        SELECT
            d.id, d.detected_at, d.days_overdue,
            a.id AS action_id, a.assignee_id, a.created_at AS action_created_at,
            e.text AS action_text,
            c.name AS channel_name
        FROM drift_events d
        JOIN actions a ON a.id = d.action_id
        JOIN extractions e ON e.id = a.extraction_id
        JOIN threads t ON t.id = e.thread_id
        JOIN channels c ON c.id = t.channel_id
        ORDER BY d.days_overdue DESC
        LIMIT $1
    """
    rows = await db.fetch_all(sql, limit)
    return {"drift": rows, "count": len(rows)}


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)
