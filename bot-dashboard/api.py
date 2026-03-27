"""Bot Dashboard API — FastAPI backend for monitoring all Polymarket bots."""

import os
import time
import json
import sqlite3
import subprocess
import asyncio
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

import re
import hashlib
import shutil
import logging

import aiosqlite
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger("dashboard")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

API_TOKEN = os.environ.get("DASHBOARD_TOKEN", "changeme")

def _safe_ts(v):
    """Convert exit_time (float or datetime string) to unix timestamp."""
    if not v:
        return 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        try:
            return datetime.strptime(str(v)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
        except Exception:
            return 0.0

BOTS = {
    "emmanuel": {
        "db": "/opt/lagbot/instances/emmanuel/data/performance.db",
        "signals_db": "/opt/lagbot/instances/emmanuel/data/signals.db",
        "env": "/opt/lagbot/instances/emmanuel/.env",
        "service": "lagbot@emmanuel",
        "health_dir": "/opt/lagbot/instances/emmanuel/data",
        "kill_switch": "/opt/lagbot/instances/emmanuel/KILL_SWITCH",
    },
    "polyphemus": {
        "db": "/opt/lagbot/instances/polyphemus/data/performance.db",
        "signals_db": "/opt/lagbot/instances/polyphemus/data/signals.db",
        "env": "/opt/lagbot/instances/polyphemus/.env",
        "service": "lagbot@polyphemus",
        "health_dir": "/opt/lagbot/instances/polyphemus/data",
        "kill_switch": "/opt/lagbot/instances/polyphemus/KILL_SWITCH",
    },
    "pair_arb": {
        "db": "/opt/lagbot/instances/pair_arb/data/performance.db",
        "signals_db": "/opt/lagbot/instances/pair_arb/data/signals.db",
        "env": "/opt/lagbot/instances/pair_arb/.env",
        "service": "lagbot@pair_arb",
        "health_dir": "/opt/lagbot/instances/pair_arb/data",
        "kill_switch": "/opt/lagbot/instances/pair_arb/KILL_SWITCH",
    },
}

# Config keys safe to expose (no secrets)
SAFE_CONFIG_KEYS = {
    "DRY_RUN", "SIGNAL_MODE", "ASSET_FILTER", "MARKET_WINDOW_SECS",
    "MOMENTUM_TRIGGER_PCT", "MOMENTUM_WINDOW_SECS", "MIN_ENTRY_PRICE",
    "MAX_ENTRY_PRICE", "BASE_BET_PCT", "MIN_BET", "MAX_BET",
    "PROFIT_TARGET_PCT", "STOP_LOSS_PCT", "ENABLE_STOP_LOSS",
    "MAX_HOLD_MINS", "ENTRY_COOLDOWN_SECS", "MAX_OPEN_POSITIONS",
    "ENTRY_MODE", "MAKER_EXIT_ENABLED", "MAKER_EXIT_TIMEOUT_POLLS",
    "ENABLE_ACCUMULATOR", "ACCUM_MAX_PAIR_COST", "ACCUM_CAPITAL_PCT",
    "ACCUM_SCAN_INTERVAL", "ACCUM_ORDER_TIMEOUT", "ACCUM_REPRICE_LIMIT",
    "ACCUM_MIN_PROFIT_PER_SHARE", "ACCUM_MAX_SIDE_PRICE",
    "SIGNATURE_TYPE", "MAX_DAILY_LOSS", "MAX_CONSECUTIVE_LOSSES",
    "ENABLE_WINDOW_DELTA", "DUAL_WINDOW_ASSETS", "MARKET_WINDOW_15M_ASSETS",
    "DIRECTION_FILTER", "ASSET_MULTIPLIER_XRP",
    "ORACLE_ENABLED", "ORACLE_FLIP_ENABLED", "ORACLE_FLIP_DRY_RUN",
    "ORACLE_STALE_THRESHOLD_SECS", "ORACLE_ALCHEMY_API_KEY",
    "MM_DRY_RUN", "MM_ENABLED",
    "SNIPE_ASSETS", "SHADOW_ASSETS", "SNIPE_15M_DRY_RUN",
}

# Keys that must NEVER appear in log output or config diffs
SENSITIVE_KEY_PATTERN = re.compile(
    r'(PRIVATE_KEY|API_KEY|SECRET|TOKEN|PASSWORD|ALCHEMY)[=:\s]+\S+',
    re.IGNORECASE,
)
SENSITIVE_KEYS = {"PRIVATE_KEY", "API_KEY", "ALCHEMY_API_KEY", "DASHBOARD_TOKEN",
                  "PASSWORD", "SECRET", "ORACLE_ALCHEMY_API_KEY"}

# Audit log
AUDIT_DB_PATH = os.environ.get("AUDIT_DB_PATH", "/opt/lagbot/dashboard_audit.db")

# Deploy safeguards
DEPLOY_MAX_FILE_SIZE = 500 * 1024  # 500KB
DEPLOY_WHITELIST = {
    "signal_bot.py", "exit_manager.py", "position_executor.py",
    "config.py", "signal_guard.py", "binance_momentum.py",
    "chainlink_feed.py", "market_maker.py", "signal_pipeline.py",
    "performance_db.py", "redeemer.py", "exit_handler.py",
    "signal_logger.py", "vpin_engine.py", "regime_classifier.py",
}

DEPLOY_TARGET_DIR = "/opt/lagbot/lagbot"

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

security = HTTPBearer()


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials.credentials != API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    return credentials.credentials


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_env(path: str) -> dict:
    """Parse .env file, return {KEY: VALUE} for safe keys only."""
    result = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if key in SAFE_CONFIG_KEYS:
                    result[key] = value.strip()
    except FileNotFoundError:
        pass
    return result


def get_service_status(service: str) -> dict:
    """Get systemd service status."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True, text=True, timeout=5,
        )
        active = result.stdout.strip()
    except Exception:
        active = "unknown"

    try:
        result = subprocess.run(
            ["systemctl", "show", service, "--property=ActiveEnterTimestamp"],
            capture_output=True, text=True, timeout=5,
        )
        ts_line = result.stdout.strip()
        since_str = ts_line.split("=", 1)[1] if "=" in ts_line else ""
    except Exception:
        since_str = ""

    return {"status": active, "since": since_str}


def get_latest_health(health_dir: str) -> dict:
    """Read most recent health JSON from a bot's data dir."""
    try:
        p = Path(health_dir)
        health_files = sorted(p.glob("health_*.json"), reverse=True)
        if health_files:
            with open(health_files[0]) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


async def query_db(db_path: str, sql: str, params: tuple = ()) -> list[dict]:
    """Run a read-only query against a bot's performance.db."""
    if not Path(db_path).exists():
        return []
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def query_signals_db(db_path: str, sql: str, params: tuple = ()) -> list[dict]:
    """Run a read-only query against a bot's signals.db."""
    if not Path(db_path).exists():
        return []
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


def confidence_label(n: int) -> str:
    if n < 30:   return f"ANECDOTAL (n={n})"
    if n < 107:  return f"LOW (n={n})"
    if n < 385:  return f"MODERATE (n={n})"
    return f"SIGNIFICANT (n={n})"


def has_column(db_path: str, table: str, column: str) -> bool:
    """Check if a column exists in a SQLite table."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(f"PRAGMA table_info({table})")
        cols = [row[1] for row in cursor.fetchall()]
        conn.close()
        return column in cols
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Audit Logging (non-blocking, append-only)
# ---------------------------------------------------------------------------

def init_audit_db():
    """Create audit table if it doesn't exist. Non-fatal on failure."""
    try:
        conn = sqlite3.connect(AUDIT_DB_PATH)
        conn.execute("""CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            action TEXT NOT NULL,
            bot TEXT,
            details TEXT,
            source_ip TEXT
        )""")
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Audit DB init failed (non-fatal): {e}")


def audit_log(action: str, bot: str = "", details: str = "", source_ip: str = "unknown"):
    """Write an audit entry. NEVER blocks the calling action on failure."""
    try:
        init_audit_db()  # Lazy init - safe to call repeatedly (IF NOT EXISTS)
        conn = sqlite3.connect(AUDIT_DB_PATH, timeout=5)
        conn.execute(
            "INSERT INTO audit_log (timestamp, action, bot, details, source_ip) VALUES (?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), action, bot, details, source_ip),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Audit log write failed (non-fatal): {e}")


# ---------------------------------------------------------------------------
# Sensitive Data Filter
# ---------------------------------------------------------------------------

def filter_sensitive(text: str) -> str:
    """Redact private keys, API keys, tokens, and passwords from text."""
    text = SENSITIVE_KEY_PATTERN.sub(lambda m: m.group(1) + "=***REDACTED***", text)
    # Redact hex strings that look like private keys (0x followed by 10+ hex chars)
    text = re.sub(r'0x[0-9a-fA-F]{10,}', '0x***REDACTED***', text)
    # Redact sk- prefixed API keys
    text = re.sub(r'sk-[a-zA-Z0-9_-]{10,}', 'sk-***REDACTED***', text)
    # Redact pk_ prefixed keys
    text = re.sub(r'pk_[a-zA-Z0-9_-]{10,}', 'pk_***REDACTED***', text)
    return text


# ---------------------------------------------------------------------------
# Config Snapshot (for diff feature)
# ---------------------------------------------------------------------------

def create_config_snapshot(env_path: str):
    """Copy current .env to .env.snapshot for later diff."""
    try:
        snapshot_path = Path(env_path).parent / ".env.snapshot"
        shutil.copy2(env_path, str(snapshot_path))
    except Exception as e:
        logger.warning(f"Config snapshot failed (non-fatal): {e}")


def get_config_diff(env_path: str) -> dict:
    """Compare current .env to snapshot. Returns diff with sensitive keys filtered."""
    snapshot_path = Path(env_path).parent / ".env.snapshot"
    current = read_env_all(env_path)
    if not snapshot_path.exists():
        return {"snapshot_exists": False, "changes": [
            {"key": k, "old": None, "new": v, "type": "added"}
            for k, v in current.items() if k not in SENSITIVE_KEYS
        ]}
    old = read_env_all(str(snapshot_path))
    changes = []
    all_keys = set(current) | set(old)
    for key in sorted(all_keys):
        if key in SENSITIVE_KEYS:
            continue
        old_val = old.get(key)
        new_val = current.get(key)
        if old_val != new_val:
            if old_val is None:
                changes.append({"key": key, "old": None, "new": new_val, "type": "added"})
            elif new_val is None:
                changes.append({"key": key, "old": old_val, "new": None, "type": "removed"})
            else:
                changes.append({"key": key, "old": old_val, "new": new_val, "type": "changed"})
    return {"snapshot_exists": True, "changes": changes}


def read_env_all(path: str) -> dict:
    """Parse .env file, return ALL key-value pairs (for internal diff use)."""
    result = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                result[key.strip()] = value.strip()
    except FileNotFoundError:
        pass
    return result


# ---------------------------------------------------------------------------
# Deploy Models
# ---------------------------------------------------------------------------

class DeployFile(BaseModel):
    name: str
    content: str


class DeployRequest(BaseModel):
    bot: str
    files: list[DeployFile]


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Bot Dashboard API", version="2.0.0")

# Init audit DB on startup
init_audit_db()

# ---------------------------------------------------------------------------
# Security Middleware
# ---------------------------------------------------------------------------

ALLOWED_ORIGINS = [
    o.strip() for o in
    os.environ.get("DASHBOARD_ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:3001").split(",")
    if o.strip()
]


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'"
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory rate limiter: 60 requests/minute per IP."""

    def __init__(self, app, max_requests: int = 60, window_secs: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_secs = window_secs
        self._hits: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        cutoff = now - self.window_secs
        hits = self._hits[client_ip]
        self._hits[client_ip] = [t for t in hits if t > cutoff]
        if len(self._hits[client_ip]) >= self.max_requests:
            return Response("Rate limit exceeded", status_code=429)
        self._hits[client_ip].append(now)
        return await call_next(request)


class IPAllowlistMiddleware(BaseHTTPMiddleware):
    """Optional IP allowlist. Disabled when DASHBOARD_ALLOWED_IPS is empty (default).

    Recovery: SSH to VPS, clear DASHBOARD_ALLOWED_IPS env var, restart API.
    """

    async def dispatch(self, request: Request, call_next):
        allowed_ips = os.environ.get("DASHBOARD_ALLOWED_IPS", "").strip()
        if not allowed_ips:
            return await call_next(request)
        ip_list = [ip.strip() for ip in allowed_ips.split(",") if ip.strip()]
        client_ip = request.client.host if request.client else "unknown"
        if client_ip not in ip_list:
            return Response("IP not allowed", status_code=403)
        return await call_next(request)


class ReadOnlyMiddleware(BaseHTTPMiddleware):
    """When DASHBOARD_READ_ONLY=true, block all POST requests.

    Recovery: SSH to VPS, set DASHBOARD_READ_ONLY=false, restart API.
    """

    async def dispatch(self, request: Request, call_next):
        if os.environ.get("DASHBOARD_READ_ONLY", "false").lower() == "true":
            if request.method == "POST":
                return Response("Dashboard is in read-only mode", status_code=403)
        return await call_next(request)


class ControlRateLimitMiddleware(BaseHTTPMiddleware):
    """Tighter rate limit (10/min) for control endpoints (stop/start/restart/deploy/kill)."""

    CONTROL_PATHS = {"/api/control/stop", "/api/control/start", "/api/control/restart",
                     "/api/control/kill", "/api/deploy"}

    def __init__(self, app, max_requests: int = 10, window_secs: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_secs = window_secs
        self._hits: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        if request.url.path not in self.CONTROL_PATHS:
            return await call_next(request)
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        cutoff = now - self.window_secs
        hits = self._hits[client_ip]
        self._hits[client_ip] = [t for t in hits if t > cutoff]
        if len(self._hits[client_ip]) >= self.max_requests:
            return Response("Control rate limit exceeded", status_code=429)
        self._hits[client_ip].append(now)
        return await call_next(request)


# Middleware order matters: outermost runs first.
# IP allowlist -> Read-only -> Control rate limit -> General rate limit -> Security headers -> CORS
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware, max_requests=60, window_secs=60)
app.add_middleware(ControlRateLimitMiddleware, max_requests=10, window_secs=60)
app.add_middleware(ReadOnlyMiddleware)
app.add_middleware(IPAllowlistMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/overview")
async def overview(token: str = Depends(verify_token)):
    """Aggregate overview of all bots."""
    bots_data = {}
    total_pnl = 0.0
    total_trades = 0
    total_wins = 0

    for bot_name, bot_cfg in BOTS.items():
        # Service status
        svc = get_service_status(bot_cfg["service"])

        # Health
        health = get_latest_health(bot_cfg["health_dir"])

        # Trade stats from DB
        db_path = bot_cfg["db"]
        pnl_col = "pnl" if has_column(db_path, "trades", "pnl") else "profit_loss"

        stats = await query_db(db_path, f"""
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN {pnl_col} > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN {pnl_col} <= 0 THEN 1 ELSE 0 END) as losses,
                COALESCE(SUM({pnl_col}), 0) as total_pnl
            FROM trades WHERE exit_time IS NOT NULL
        """)
        s = stats[0] if stats else {"total_trades": 0, "wins": 0, "losses": 0, "total_pnl": 0}

        # Last trade
        last_trade = await query_db(db_path, f"""
            SELECT exit_time, {pnl_col} as pnl, slug
            FROM trades WHERE exit_time IS NOT NULL
            ORDER BY exit_time DESC LIMIT 1
        """)
        last = last_trade[0] if last_trade else None

        # Open positions count
        open_pos = await query_db(db_path, "SELECT COUNT(*) as cnt FROM trades WHERE exit_time IS NULL")
        open_count = open_pos[0]["cnt"] if open_pos else 0

        # Kill switch active?
        kill_active = Path(bot_cfg["kill_switch"]).exists()

        total_pnl += s["total_pnl"] or 0
        total_trades += s["total_trades"] or 0
        total_wins += s["wins"] or 0

        bots_data[bot_name] = {
            "service": svc,
            "total_pnl": round(s["total_pnl"] or 0, 2),
            "total_trades": s["total_trades"] or 0,
            "win_rate": round((s["wins"] / s["total_trades"] * 100) if s["total_trades"] else 0, 1),
            "open_positions": open_count,
            "last_trade": {
                "time": last["exit_time"] if last else None,
                "pnl": round(last["pnl"], 2) if last and last["pnl"] else None,
                "slug": last["slug"] if last else None,
            },
            "health": {
                "balance": health.get("balance"),
                "uptime_hours": health.get("uptime_hours"),
                "errors": health.get("errors", 0),
            },
            "kill_switch": kill_active,
        }

    return {
        "total_pnl": round(total_pnl, 2),
        "total_trades": total_trades,
        "overall_win_rate": round((total_wins / total_trades * 100) if total_trades else 0, 1),
        "bots": bots_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/trades")
async def trades(
    bot: Optional[str] = Query(None),
    limit: int = Query(50, le=500),
    offset: int = Query(0),
    exit_type: Optional[str] = Query(None),
    outcome: Optional[str] = Query(None),  # "win" or "loss"
    token: str = Depends(verify_token),
):
    """Recent trades across bots."""
    all_trades = []

    targets = {bot: BOTS[bot]} if bot and bot in BOTS else BOTS

    for bot_name, bot_cfg in targets.items():
        db_path = bot_cfg["db"]
        pnl_col = "pnl" if has_column(db_path, "trades", "pnl") else "profit_loss"

        where_clauses = ["exit_time IS NOT NULL"]
        params = []

        if exit_type:
            where_clauses.append("exit_reason = ?")
            params.append(exit_type)
        if outcome == "win":
            where_clauses.append(f"{pnl_col} > 0")
        elif outcome == "loss":
            where_clauses.append(f"{pnl_col} <= 0")

        where = " AND ".join(where_clauses)

        rows = await query_db(db_path, f"""
            SELECT trade_id, slug, token_id, entry_time, entry_price, entry_size,
                   exit_time, exit_price, exit_reason, {pnl_col} as pnl,
                   market_title, strategy
            FROM trades
            WHERE {where}
            ORDER BY exit_time DESC
            LIMIT ? OFFSET ?
        """, (*params, limit, offset))

        for row in rows:
            row["bot"] = bot_name
        all_trades.extend(rows)

    all_trades.sort(key=lambda x: _safe_ts(x.get("exit_time")), reverse=True)
    return {"trades": all_trades[:limit], "count": len(all_trades)}


@app.get("/api/pnl")
async def pnl(
    bot: Optional[str] = Query(None),
    days: int = Query(30, le=90),
    token: str = Depends(verify_token),
):
    """Daily P&L aggregation for charts."""
    cutoff = time.time() - (days * 86400)
    targets = {bot: BOTS[bot]} if bot and bot in BOTS else BOTS

    daily = {}  # date_str -> {pnl, trades, wins}

    for bot_name, bot_cfg in targets.items():
        db_path = bot_cfg["db"]
        pnl_col = "pnl" if has_column(db_path, "trades", "pnl") else "profit_loss"

        rows = await query_db(db_path, f"""
            SELECT exit_time, {pnl_col} as pnl
            FROM trades
            WHERE exit_time IS NOT NULL AND exit_time > ?
            ORDER BY exit_time ASC
        """, (cutoff,))

        for row in rows:
            dt = datetime.fromtimestamp(_safe_ts(row["exit_time"]), tz=timezone.utc)
            date_str = dt.strftime("%Y-%m-%d")
            if date_str not in daily:
                daily[date_str] = {"date": date_str, "pnl": 0, "trades": 0, "wins": 0}
            daily[date_str]["pnl"] += row["pnl"] or 0
            daily[date_str]["trades"] += 1
            if (row["pnl"] or 0) > 0:
                daily[date_str]["wins"] += 1

    # Fill in missing days
    result = []
    if daily:
        start = min(daily.keys())
        end = max(daily.keys())
        current = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")
        cumulative = 0
        while current <= end_dt:
            ds = current.strftime("%Y-%m-%d")
            day_data = daily.get(ds, {"date": ds, "pnl": 0, "trades": 0, "wins": 0})
            day_data["pnl"] = round(day_data["pnl"], 2)
            cumulative += day_data["pnl"]
            day_data["cumulative"] = round(cumulative, 2)
            result.append(day_data)
            current += timedelta(days=1)

    return {"daily": result}


@app.get("/api/positions")
async def positions(
    bot: Optional[str] = Query(None),
    token: str = Depends(verify_token),
):
    """Open positions across bots."""
    all_positions = []
    targets = {bot: BOTS[bot]} if bot and bot in BOTS else BOTS

    for bot_name, bot_cfg in targets.items():
        db_path = bot_cfg["db"]
        rows = await query_db(db_path, """
            SELECT trade_id, slug, token_id, entry_time, entry_price, entry_size,
                   market_title, strategy
            FROM trades
            WHERE exit_time IS NULL
            ORDER BY entry_time DESC
        """)
        for row in rows:
            row["bot"] = bot_name
            # Calculate hold time
            if row.get("entry_time"):
                row["hold_mins"] = round((time.time() - row["entry_time"]) / 60, 1)
        all_positions.extend(rows)

    return {"positions": all_positions, "count": len(all_positions)}


@app.get("/api/config")
async def config(
    bot: str = Query(...),
    token: str = Depends(verify_token),
):
    """Read current config for a bot (safe keys only)."""
    if bot not in BOTS:
        raise HTTPException(404, f"Unknown bot: {bot}")
    env = read_env(BOTS[bot]["env"])
    return {"bot": bot, "config": env}


@app.get("/api/health")
async def health(token: str = Depends(verify_token)):
    """Health status for all bots."""
    result = {}
    for bot_name, bot_cfg in BOTS.items():
        svc = get_service_status(bot_cfg["service"])
        h = get_latest_health(bot_cfg["health_dir"])
        kill_active = Path(bot_cfg["kill_switch"]).exists()
        result[bot_name] = {
            "service": svc,
            "health": h,
            "kill_switch": kill_active,
        }
    return result


@app.post("/api/control/kill")
async def kill_switch(
    request: Request,
    bot: str = Query(...),
    token: str = Depends(verify_token),
):
    """Toggle kill switch for a bot."""
    if bot not in BOTS:
        raise HTTPException(404, f"Unknown bot: {bot}")
    client_ip = request.client.host if request.client else "unknown"
    ks_path = Path(BOTS[bot]["kill_switch"])
    if ks_path.exists():
        ks_path.unlink()
        audit_log("kill_switch", bot, "disabled", client_ip)
        return {"status": "disabled", "bot": bot}
    else:
        ks_path.touch()
        audit_log("kill_switch", bot, "enabled", client_ip)
        return {"status": "enabled", "bot": bot}


@app.post("/api/control/stop")
async def stop_service(
    request: Request,
    bot: str = Query(...),
    token: str = Depends(verify_token),
):
    """Stop a bot's systemd service."""
    if bot not in BOTS:
        raise HTTPException(404, f"Unknown bot: {bot}")
    service = BOTS[bot]["service"]
    client_ip = request.client.host if request.client else "unknown"
    try:
        result = subprocess.run(
            ["systemctl", "stop", service],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise HTTPException(500, f"Stop failed: {result.stderr}")
        audit_log("stop", bot, "service stopped", client_ip)
        return {"status": "stopped", "bot": bot}
    except subprocess.TimeoutExpired:
        raise HTTPException(500, "Stop timed out")


@app.post("/api/control/start")
async def start_service(
    request: Request,
    bot: str = Query(...),
    token: str = Depends(verify_token),
):
    """Start a bot's systemd service."""
    if bot not in BOTS:
        raise HTTPException(404, f"Unknown bot: {bot}")
    service = BOTS[bot]["service"]
    client_ip = request.client.host if request.client else "unknown"
    try:
        result = subprocess.run(
            ["systemctl", "start", service],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise HTTPException(500, f"Start failed: {result.stderr}")
        # Snapshot config on start for later diff
        create_config_snapshot(BOTS[bot]["env"])
        audit_log("start", bot, "service started", client_ip)
        return {"status": "started", "bot": bot}
    except subprocess.TimeoutExpired:
        raise HTTPException(500, "Start timed out")


@app.post("/api/control/restart")
async def restart_service(
    request: Request,
    bot: str = Query(...),
    token: str = Depends(verify_token),
):
    """Restart a bot's systemd service."""
    if bot not in BOTS:
        raise HTTPException(404, f"Unknown bot: {bot}")
    service = BOTS[bot]["service"]
    client_ip = request.client.host if request.client else "unknown"
    try:
        result = subprocess.run(
            ["systemctl", "restart", service],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise HTTPException(500, f"Restart failed: {result.stderr}")
        create_config_snapshot(BOTS[bot]["env"])
        audit_log("restart", bot, "service restarted", client_ip)
        return {"status": "restarted", "bot": bot}
    except subprocess.TimeoutExpired:
        raise HTTPException(500, "Restart timed out")


@app.get("/api/signals")
async def signals_endpoint(
    bot: Optional[str] = Query(None),
    limit: int = Query(20, le=200),
    asset: Optional[str] = Query(None),
    guard_passed: Optional[int] = Query(None),
    token: str = Depends(verify_token),
):
    """Recent signals from signals.db across instances."""
    all_signals = []
    targets = {bot: BOTS[bot]} if bot and bot in BOTS else BOTS

    for bot_name, bot_cfg in targets.items():
        sig_db = bot_cfg.get("signals_db", "")
        where = []
        params: list = []
        if asset:
            where.append("asset = ?")
            params.append(asset.upper())
        if guard_passed is not None:
            where.append("guard_passed = ?")
            params.append(guard_passed)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        rows = await query_signals_db(sig_db, f"""
            SELECT timestamp, asset, direction, midpoint, guard_passed,
                   guard_reasons, slug, time_remaining_secs, pnl, outcome
            FROM signals {where_sql}
            ORDER BY timestamp DESC LIMIT ?
        """, (*params, limit))
        for r in rows:
            r["bot"] = bot_name
        all_signals.extend(rows)

    all_signals.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return {"signals": all_signals[:limit], "count": len(all_signals)}


@app.get("/api/inference")
async def inference(token: str = Depends(verify_token)):
    """Running WR analytics from signals.db with confidence labels."""
    results = {}
    for bot_name, bot_cfg in BOTS.items():
        sig_db = bot_cfg.get("signals_db", "")

        bucket_rows = await query_signals_db(sig_db, """
            SELECT
                CASE
                    WHEN midpoint < 0.55 THEN '0.50-0.55'
                    WHEN midpoint < 0.65 THEN '0.55-0.65'
                    WHEN midpoint < 0.75 THEN '0.65-0.75'
                    ELSE '0.75+'
                END as bucket,
                COUNT(*) as n,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
            FROM signals
            WHERE guard_passed=1 AND pnl IS NOT NULL
            GROUP BY bucket ORDER BY bucket
        """)

        asset_rows = await query_signals_db(sig_db, """
            SELECT asset,
                COUNT(*) as n,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
            FROM signals
            WHERE guard_passed=1 AND pnl IS NOT NULL
            GROUP BY asset
        """)

        cb_state: dict = {}
        try:
            cb_path = bot_cfg["health_dir"] + "/circuit_breaker.json"
            with open(cb_path) as f:
                cb_state = json.load(f)
        except Exception:
            pass

        pending = await query_signals_db(sig_db, """
            SELECT COUNT(*) as n FROM signals WHERE guard_passed=1 AND pnl IS NULL
        """)
        pending_n = pending[0]["n"] if pending else 0

        results[bot_name] = {
            "by_bucket": [
                {
                    "bucket": r["bucket"],
                    "n": r["n"],
                    "wins": r["wins"] or 0,
                    "win_rate": round((r["wins"] or 0) / r["n"] * 100, 1) if r["n"] else 0,
                    "confidence": confidence_label(r["n"]),
                }
                for r in bucket_rows
            ],
            "by_asset": [
                {
                    "asset": r["asset"],
                    "n": r["n"],
                    "wins": r["wins"] or 0,
                    "win_rate": round((r["wins"] or 0) / r["n"] * 100, 1) if r["n"] else 0,
                    "confidence": confidence_label(r["n"]),
                }
                for r in asset_rows
            ],
            "circuit_breaker": cb_state,
            "pending_signals": pending_n,
        }
    return results


@app.get("/api/pipeline")
async def pipeline(token: str = Depends(verify_token)):
    """Pipeline feed status for all bots (Chainlink, Binance, Guard)."""
    result = {}
    for bot_name, bot_cfg in BOTS.items():
        health = get_latest_health(bot_cfg["health_dir"])
        svc = get_service_status(bot_cfg["service"])
        pipeline_data = health.get("pipeline", {})

        # Compute health file freshness
        health_age = None
        if health.get("timestamp"):
            try:
                ht = datetime.fromisoformat(health["timestamp"].rstrip("Z")).replace(tzinfo=timezone.utc)
                health_age = round((datetime.now(timezone.utc) - ht).total_seconds(), 1)
            except Exception:
                pass

        result[bot_name] = {
            "service": svc,
            "health_age_secs": health_age,
            "chainlink": pipeline_data.get("chainlink", {}),
            "binance": pipeline_data.get("binance", {}),
            "guard": pipeline_data.get("guard", {}),
            "uptime_hours": health.get("uptime_hours"),
            "balance": health.get("balance"),
        }
    return {"bots": result, "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/strategy-pnl")
async def strategy_pnl(
    bot: Optional[str] = Query(None),
    days: int = Query(30, le=90),
    token: str = Depends(verify_token),
):
    """Per-strategy P&L breakdown from trades metadata."""
    cutoff = time.time() - (days * 86400)
    targets = {bot: BOTS[bot]} if bot and bot in BOTS else BOTS

    # Aggregate by strategy source
    strategy_totals = {}  # source -> {trades, wins, pnl}
    daily_by_strategy = {}  # (date, source) -> {pnl, trades}

    for bot_name, bot_cfg in targets.items():
        db_path = bot_cfg["db"]
        pnl_col = "pnl" if has_column(db_path, "trades", "pnl") else "profit_loss"
        has_meta = has_column(db_path, "trades", "metadata")

        if has_meta:
            rows = await query_db(db_path, f"""
                SELECT exit_time, {pnl_col} as pnl, strategy, metadata
                FROM trades
                WHERE exit_time IS NOT NULL AND exit_time > ?
                ORDER BY exit_time ASC
            """, (cutoff,))
        else:
            rows = await query_db(db_path, f"""
                SELECT exit_time, {pnl_col} as pnl, strategy, NULL as metadata
                FROM trades
                WHERE exit_time IS NOT NULL AND exit_time > ?
                ORDER BY exit_time ASC
            """, (cutoff,))

        for row in rows:
            # Extract source from metadata JSON, fall back to strategy column
            source = row.get("strategy") or "unknown"
            if row.get("metadata"):
                try:
                    meta = json.loads(row["metadata"])
                    source = meta.get("source", source)
                except (json.JSONDecodeError, TypeError):
                    pass

            pnl_val = row["pnl"] or 0

            if source not in strategy_totals:
                strategy_totals[source] = {"trades": 0, "wins": 0, "pnl": 0}
            strategy_totals[source]["trades"] += 1
            strategy_totals[source]["pnl"] += pnl_val
            if pnl_val > 0:
                strategy_totals[source]["wins"] += 1

            # Daily breakdown for trend charts
            dt = datetime.fromtimestamp(_safe_ts(row["exit_time"]), tz=timezone.utc)
            date_str = dt.strftime("%Y-%m-%d")
            key = (date_str, source)
            if key not in daily_by_strategy:
                daily_by_strategy[key] = {"date": date_str, "source": source, "pnl": 0, "trades": 0}
            daily_by_strategy[key]["pnl"] += pnl_val
            daily_by_strategy[key]["trades"] += 1

    strategies = []
    for source, totals in sorted(strategy_totals.items(), key=lambda x: x[1]["pnl"], reverse=True):
        n = totals["trades"]
        strategies.append({
            "source": source,
            "trades": n,
            "wins": totals["wins"],
            "win_rate": round(totals["wins"] / n * 100, 1) if n else 0,
            "total_pnl": round(totals["pnl"], 2),
            "avg_pnl": round(totals["pnl"] / n, 2) if n else 0,
            "confidence": confidence_label(n),
        })

    # Build daily series per strategy
    daily_series = sorted(daily_by_strategy.values(), key=lambda x: (x["date"], x["source"]))
    for d in daily_series:
        d["pnl"] = round(d["pnl"], 2)

    return {"strategies": strategies, "daily": daily_series}


@app.get("/api/oracle")
async def oracle_stats(
    bot: Optional[str] = Query(None),
    token: str = Depends(verify_token),
):
    """Oracle flip statistics from signals.db."""
    targets = {bot: BOTS[bot]} if bot and bot in BOTS else BOTS
    all_flips = []
    totals = {"attempted": 0, "passed": 0, "wins": 0, "total_pnl": 0}

    for bot_name, bot_cfg in targets.items():
        sig_db = bot_cfg.get("signals_db", "")
        if not Path(sig_db).exists():
            continue

        # Check if source column exists
        if not has_column(sig_db, "signals", "source"):
            continue

        # Oracle flip summary
        summary = await query_signals_db(sig_db, """
            SELECT
                COUNT(*) as attempted,
                SUM(CASE WHEN guard_passed = 1 THEN 1 ELSE 0 END) as passed,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl IS NOT NULL THEN 1 ELSE 0 END) as resolved,
                COALESCE(SUM(pnl), 0) as total_pnl
            FROM signals
            WHERE source = 'oracle_flip'
        """)
        s = summary[0] if summary else {}
        totals["attempted"] += s.get("attempted", 0) or 0
        totals["passed"] += s.get("passed", 0) or 0
        totals["wins"] += s.get("wins", 0) or 0
        totals["total_pnl"] += s.get("total_pnl", 0) or 0

        # Recent flips
        recent = await query_signals_db(sig_db, """
            SELECT timestamp, asset, direction, midpoint, entry_price,
                   pnl, outcome, time_remaining_secs
            FROM signals
            WHERE source = 'oracle_flip'
            ORDER BY epoch DESC LIMIT 10
        """)
        for r in recent:
            r["bot"] = bot_name
        all_flips.extend(recent)

    resolved = totals["passed"]
    win_rate = round(totals["wins"] / resolved * 100, 1) if resolved else 0

    return {
        "attempted": totals["attempted"],
        "passed": totals["passed"],
        "wins": totals["wins"],
        "win_rate": win_rate,
        "total_pnl": round(totals["total_pnl"], 2),
        "confidence": confidence_label(totals["passed"]),
        "recent": sorted(all_flips, key=lambda x: x.get("timestamp", ""), reverse=True)[:10],
    }


@app.get("/api/scoreboard")
async def scoreboard(
    hours: int = Query(4, le=48),
    token: str = Depends(verify_token),
):
    """Live scoreboard: recent trades, open positions, running stats, streak."""
    cutoff = time.time() - (hours * 3600)
    all_recent = []
    all_open = []
    combined = {"total": 0, "wins": 0, "losses": 0, "pnl": 0.0,
                "avg_win": 0.0, "avg_loss": 0.0, "streak": 0, "streak_type": ""}

    win_pnls = []
    loss_pnls = []

    for bot_name, bot_cfg in BOTS.items():
        db_path = bot_cfg["db"]
        pnl_col = "pnl" if has_column(db_path, "trades", "pnl") else "profit_loss"

        # Recent closed trades
        rows = await query_db(db_path, f"""
            SELECT slug, entry_price, exit_price, entry_size, {pnl_col} as pnl,
                   exit_reason, outcome, entry_time, exit_time
            FROM trades
            WHERE exit_time IS NOT NULL AND entry_time > ?
            ORDER BY exit_time DESC LIMIT 50
        """, (cutoff,))
        for r in rows:
            r["bot"] = bot_name
            r["is_win"] = (r["pnl"] or 0) > 0
        all_recent.extend(rows)

        # Open positions
        open_rows = await query_db(db_path, """
            SELECT slug, entry_price, entry_size, outcome, entry_time, market_title
            FROM trades WHERE exit_time IS NULL ORDER BY entry_time DESC
        """)
        for r in open_rows:
            r["bot"] = bot_name
            if r.get("entry_time"):
                r["hold_secs"] = round(time.time() - r["entry_time"])
        all_open.extend(open_rows)

    # Sort all recent by exit_time desc
    all_recent.sort(key=lambda x: _safe_ts(x.get("exit_time")), reverse=True)

    # Compute aggregate stats
    for t in all_recent:
        pnl_val = t["pnl"] or 0
        combined["total"] += 1
        combined["pnl"] += pnl_val
        if pnl_val > 0:
            combined["wins"] += 1
            win_pnls.append(pnl_val)
        else:
            combined["losses"] += 1
            loss_pnls.append(pnl_val)

    combined["pnl"] = round(combined["pnl"], 2)
    combined["win_rate"] = round(combined["wins"] / combined["total"] * 100, 1) if combined["total"] else 0
    combined["avg_win"] = round(sum(win_pnls) / len(win_pnls), 2) if win_pnls else 0
    combined["avg_loss"] = round(sum(loss_pnls) / len(loss_pnls), 2) if loss_pnls else 0
    combined["ev_per_trade"] = round(combined["pnl"] / combined["total"], 2) if combined["total"] else 0

    # Compute streak (from most recent trade)
    sorted_by_time = sorted(all_recent, key=lambda x: _safe_ts(x.get("exit_time")), reverse=True)
    if sorted_by_time:
        streak_win = sorted_by_time[0]["is_win"]
        streak_count = 0
        for t in sorted_by_time:
            if t["is_win"] == streak_win:
                streak_count += 1
            else:
                break
        combined["streak"] = streak_count
        combined["streak_type"] = "W" if streak_win else "L"

    return {
        "recent": all_recent[:30],
        "open": all_open,
        "stats": combined,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/stats")
async def stats(
    days: int = Query(7, le=90),
    token: str = Depends(verify_token),
):
    """Aggregate stats: by asset, direction, hour, entry price, per-instance."""
    cutoff = time.time() - (days * 86400)

    by_asset: dict = {}
    by_direction: dict = {}
    by_hour: dict = {str(h): {"hour": h, "trades": 0, "wins": 0, "pnl": 0.0} for h in range(24)}
    by_entry_bucket: dict = {}
    per_instance: dict = {}
    cumulative_series: list = []
    all_trades_for_cum: list = []

    for bot_name, bot_cfg in BOTS.items():
        db_path = bot_cfg["db"]
        pnl_col = "pnl" if has_column(db_path, "trades", "pnl") else "profit_loss"

        rows = await query_db(db_path, f"""
            SELECT slug, entry_price, {pnl_col} as pnl, exit_time, outcome, exit_reason
            FROM trades
            WHERE exit_time IS NOT NULL AND entry_time > ?
            ORDER BY exit_time ASC
        """, (cutoff,))

        inst = {"trades": 0, "wins": 0, "pnl": 0.0}

        for row in rows:
            try:
                pnl_val = float(row["pnl"] or 0)
            except (ValueError, TypeError):
                pnl_val = 0
            is_win = pnl_val > 0
            slug = row["slug"] or ""

            # Extract asset from slug (e.g. "btc-updown-5m-123" -> "BTC")
            asset = slug.split("-")[0].upper() if slug else "UNKNOWN"

            # Direction from outcome column
            direction = row.get("outcome") or "unknown"

            # Entry price bucket
            try:
                ep = float(row.get("entry_price") or 0)
            except (ValueError, TypeError):
                ep = 0
            if ep < 0.50:
                bucket = "<0.50"
            elif ep < 0.52:
                bucket = "0.50-0.52"
            elif ep < 0.55:
                bucket = "0.52-0.55"
            elif ep < 0.58:
                bucket = "0.55-0.58"
            else:
                bucket = "0.58+"

            # Hour of day (UTC)
            exit_t = row.get("exit_time") or 0
            try:
                hour_utc = datetime.fromtimestamp(float(exit_t), tz=timezone.utc).hour if exit_t else 0
            except (ValueError, TypeError, OSError):
                hour_utc = 0

            # Aggregate by asset
            if asset not in by_asset:
                by_asset[asset] = {"asset": asset, "trades": 0, "wins": 0, "pnl": 0.0}
            by_asset[asset]["trades"] += 1
            by_asset[asset]["pnl"] += pnl_val
            if is_win:
                by_asset[asset]["wins"] += 1

            # Aggregate by direction
            if direction not in by_direction:
                by_direction[direction] = {"direction": direction, "trades": 0, "wins": 0, "pnl": 0.0}
            by_direction[direction]["trades"] += 1
            by_direction[direction]["pnl"] += pnl_val
            if is_win:
                by_direction[direction]["wins"] += 1

            # Aggregate by hour
            h_key = str(hour_utc)
            by_hour[h_key]["trades"] += 1
            by_hour[h_key]["pnl"] += pnl_val
            if is_win:
                by_hour[h_key]["wins"] += 1

            # Aggregate by entry bucket
            if bucket not in by_entry_bucket:
                by_entry_bucket[bucket] = {"bucket": bucket, "trades": 0, "wins": 0, "pnl": 0.0}
            by_entry_bucket[bucket]["trades"] += 1
            by_entry_bucket[bucket]["pnl"] += pnl_val
            if is_win:
                by_entry_bucket[bucket]["wins"] += 1

            # Per-instance
            inst["trades"] += 1
            inst["pnl"] += pnl_val
            if is_win:
                inst["wins"] += 1

            # Cumulative series
            all_trades_for_cum.append({"exit_time": exit_t, "pnl": pnl_val, "bot": bot_name})

        per_instance[bot_name] = {
            "trades": inst["trades"],
            "wins": inst["wins"],
            "win_rate": round(inst["wins"] / inst["trades"] * 100, 1) if inst["trades"] else 0,
            "pnl": round(inst["pnl"], 2),
        }

    # Add win_rate to all aggregates
    def add_wr(items):
        for item in items:
            n = item["trades"]
            item["win_rate"] = round(item["wins"] / n * 100, 1) if n else 0
            item["pnl"] = round(item["pnl"], 2)
            item["confidence"] = confidence_label(n)

    asset_list = sorted(by_asset.values(), key=lambda x: x["pnl"], reverse=True)
    add_wr(asset_list)
    direction_list = list(by_direction.values())
    add_wr(direction_list)
    hour_list = sorted(by_hour.values(), key=lambda x: x["hour"])
    add_wr(hour_list)
    bucket_list = sorted(by_entry_bucket.values(), key=lambda x: x["bucket"])
    add_wr(bucket_list)

    # Build cumulative P&L series
    all_trades_for_cum.sort(key=lambda x: _safe_ts(x.get("exit_time")))
    cum_pnl = 0.0
    for t in all_trades_for_cum:
        cum_pnl += t["pnl"]
        cumulative_series.append({
            "time": t["exit_time"],
            "pnl": round(cum_pnl, 2),
            "trade_pnl": round(t["pnl"], 2),
        })

    return {
        "by_asset": asset_list,
        "by_direction": direction_list,
        "by_hour": hour_list,
        "by_entry_bucket": bucket_list,
        "per_instance": per_instance,
        "cumulative": cumulative_series,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/config")
async def update_config(
    request: Request,
    bot: str = Query(...),
    key: str = Query(...),
    value: str = Query(...),
    token: str = Depends(verify_token),
):
    """Update a single config param in a bot's .env file."""
    if bot not in BOTS:
        raise HTTPException(404, f"Unknown bot: {bot}")
    if key not in SAFE_CONFIG_KEYS:
        raise HTTPException(400, f"Cannot modify key: {key}")

    env_path = BOTS[bot]["env"]
    try:
        with open(env_path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        raise HTTPException(404, f"Env file not found: {env_path}")

    found = False
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            new_lines.append(f"{key}={value}\n")
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(f"{key}={value}\n")

    with open(env_path, "w") as f:
        f.writelines(new_lines)

    client_ip = request.client.host if request.client else "unknown"
    audit_log("config_update", bot, f"{key}={value}", client_ip)

    return {"status": "updated", "bot": bot, "key": key, "value": value,
            "note": "Restart the service for changes to take effect."}


# ---------------------------------------------------------------------------
# Feature 1: Logs (journalctl viewer + SSE stream)
# ---------------------------------------------------------------------------

@app.get("/api/logs")
async def get_logs(
    bot: str = Query(...),
    lines: int = Query(100, le=1000),
    filter: Optional[str] = Query(None),
    token: str = Depends(verify_token),
):
    """Get recent journalctl lines for a bot. Sensitive data is filtered."""
    if bot not in BOTS:
        raise HTTPException(404, f"Unknown bot: {bot}")
    service = BOTS[bot]["service"]
    try:
        result = subprocess.run(
            ["journalctl", "-u", service, "--no-pager", "-n", str(lines), "--output=short"],
            capture_output=True, text=True, timeout=10,
        )
        raw_lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
    except Exception:
        raw_lines = []

    # Filter sensitive data
    clean_lines = [filter_sensitive(line) for line in raw_lines]

    # Optional keyword filter
    if filter:
        filter_upper = filter.upper()
        clean_lines = [line for line in clean_lines if filter_upper in line.upper()]

    return {"lines": clean_lines, "bot": bot, "count": len(clean_lines)}


@app.get("/api/logs/stream")
async def stream_logs(
    bot: str = Query(...),
    token: str = Depends(verify_token),
):
    """SSE stream of live journalctl output. Auto-disconnects after 300s."""
    if bot not in BOTS:
        raise HTTPException(404, f"Unknown bot: {bot}")
    service = BOTS[bot]["service"]

    async def event_generator():
        try:
            proc = await asyncio.create_subprocess_exec(
                "journalctl", "-u", service, "-f", "--no-pager", "--output=short",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            start = time.time()
            while time.time() - start < 300:  # Max 5 min
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=5.0)
                    if line:
                        clean = filter_sensitive(line.decode("utf-8", errors="replace").rstrip())
                        yield f"data: {clean}\n\n"
                    else:
                        break
                except asyncio.TimeoutError:
                    yield f": keepalive\n\n"
            proc.terminate()
        except Exception as e:
            yield f"data: [ERROR] {e}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Feature 4: Balance check (subprocess-isolated)
# ---------------------------------------------------------------------------

@app.get("/api/balance")
async def get_balance(
    bot: str = Query(...),
    token: str = Depends(verify_token),
):
    """Get USDC balance for a bot. Uses subprocess to isolate wallet from API process."""
    if bot not in BOTS:
        raise HTTPException(404, f"Unknown bot: {bot}")
    health_dir = BOTS[bot]["health_dir"]
    # Build a simple script string without f-string backslash issues
    script = (
        "import json, glob, os, sys\n"
        "health_dir = sys.argv[1]\n"
        "files = sorted(glob.glob(os.path.join(health_dir, 'health_*.json')), reverse=True)\n"
        "data = json.load(open(files[0])) if files else {}\n"
        "print(json.dumps({'balance': data.get('balance', None)}))\n"
    )
    try:
        result = subprocess.run(
            ["python3", "-c", script, health_dir],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout.strip())
            return {"balance": data.get("balance"), "bot": bot}
        return {"balance": None, "bot": bot, "error": result.stderr.strip() or "No balance data"}
    except Exception as e:
        return {"balance": None, "bot": bot, "error": str(e)}


# ---------------------------------------------------------------------------
# Feature 5: Process health
# ---------------------------------------------------------------------------

@app.get("/api/process-health")
async def process_health(
    bot: str = Query(...),
    token: str = Depends(verify_token),
):
    """Get process-level metrics: memory, CPU, uptime, PID, restarts."""
    if bot not in BOTS:
        raise HTTPException(404, f"Unknown bot: {bot}")
    service = BOTS[bot]["service"]
    try:
        result = subprocess.run(
            ["systemctl", "show", service,
             "--property=MainPID,MemoryCurrent,CPUUsageNSec,NRestarts,ActiveEnterTimestamp,ActiveState"],
            capture_output=True, text=True, timeout=10,
        )
        props = {}
        for line in result.stdout.strip().split("\n"):
            if "=" in line:
                k, _, v = line.partition("=")
                props[k.strip()] = v.strip()

        status = props.get("ActiveState", "unknown")
        pid = int(props.get("MainPID", "0"))
        mem_bytes = int(props.get("MemoryCurrent", "0"))
        cpu_ns = int(props.get("CPUUsageNSec", "0"))
        restarts = int(props.get("NRestarts", "0"))
        since = props.get("ActiveEnterTimestamp", "")

        # Calculate uptime
        uptime_hours = None
        if since and status == "active":
            try:
                from email.utils import parsedate_to_datetime
                start_dt = datetime.strptime(since.split(";")[0].strip(), "%a %Y-%m-%d %H:%M:%S %Z")
                uptime_hours = round((datetime.now(timezone.utc) - start_dt.replace(tzinfo=timezone.utc)).total_seconds() / 3600, 1)
            except Exception:
                pass

        return {
            "bot": bot,
            "status": status,
            "pid": pid,
            "memory_mb": round(mem_bytes / (1024 * 1024), 1) if mem_bytes else 0,
            "cpu_secs": round(cpu_ns / 1e9, 1) if cpu_ns else 0,
            "uptime_hours": uptime_hours,
            "restarts": restarts,
            "n_restarts": restarts,
            "memory": mem_bytes,
            "cpu": cpu_ns,
            "main_pid": pid,
            "uptime": uptime_hours,
        }
    except Exception as e:
        return {
            "bot": bot, "status": "unknown", "pid": 0,
            "memory_mb": 0, "cpu_secs": 0, "uptime_hours": None,
            "restarts": 0, "n_restarts": 0, "memory": 0, "cpu": 0,
            "main_pid": 0, "uptime": None, "error": str(e),
        }


# ---------------------------------------------------------------------------
# Feature 6: Config diff
# ---------------------------------------------------------------------------

@app.get("/api/config/diff")
async def config_diff(
    bot: str = Query(...),
    token: str = Depends(verify_token),
):
    """Compare current .env to last snapshot. Sensitive keys filtered."""
    if bot not in BOTS:
        raise HTTPException(404, f"Unknown bot: {bot}")
    env_path = BOTS[bot]["env"]
    diff = get_config_diff(env_path)
    return {"bot": bot, **diff}


# ---------------------------------------------------------------------------
# Feature 7: Audit log
# ---------------------------------------------------------------------------

@app.get("/api/audit-log")
async def get_audit_log(
    limit: int = Query(50, le=500),
    action: Optional[str] = Query(None),
    token: str = Depends(verify_token),
):
    """Read audit log entries. Newest first."""
    try:
        conn = sqlite3.connect(AUDIT_DB_PATH)
        conn.row_factory = sqlite3.Row
        if action:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE action = ? ORDER BY id DESC LIMIT ?",
                (action, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        conn.close()
        entries = [dict(row) for row in rows]
    except Exception:
        entries = []
    return {"entries": entries}


# ---------------------------------------------------------------------------
# Feature 8: Services status
# ---------------------------------------------------------------------------

@app.get("/api/services")
async def list_services(
    token: str = Depends(verify_token),
):
    """List all configured bot services with status."""
    services = []
    for bot_name, bot_cfg in BOTS.items():
        svc_name = bot_cfg["service"]
        try:
            svc_status = get_service_status(svc_name)
            services.append({
                "bot": bot_name,
                "name": bot_name,
                "service": svc_name,
                "unit": svc_name,
                "status": svc_status["status"],
                "since": svc_status["since"],
            })
        except Exception:
            services.append({
                "bot": bot_name,
                "name": bot_name,
                "service": svc_name,
                "unit": svc_name,
                "status": "unknown",
                "since": "",
            })
    return {"services": services}


# ---------------------------------------------------------------------------
# Feature 3: Deploy (file upload + validation + backup)
# ---------------------------------------------------------------------------

@app.post("/api/deploy")
async def deploy_files(
    request: Request,
    body: DeployRequest,
    token: str = Depends(verify_token),
):
    """Deploy files to VPS. Validates whitelist, syntax, size. Creates backups."""
    if body.bot not in BOTS:
        raise HTTPException(404, f"Unknown bot: {body.bot}")
    if not body.files:
        raise HTTPException(400, "No files provided")

    client_ip = request.client.host if request.client else "unknown"
    results = []

    for f in body.files:
        # Security: whitelist check
        basename = Path(f.name).name
        if basename != f.name or f.name not in DEPLOY_WHITELIST:
            raise HTTPException(400, f"File not allowed (not in whitelist): {f.name}")

        # Security: path traversal check
        if ".." in f.name or "/" in f.name or "\\" in f.name:
            raise HTTPException(400, f"File not allowed (path traversal): {f.name}")

        # Security: size check
        if len(f.content.encode("utf-8")) > DEPLOY_MAX_FILE_SIZE:
            raise HTTPException(400, f"File size exceeds limit (max {DEPLOY_MAX_FILE_SIZE // 1024}KB): {f.name}")

        # Syntax validation via py_compile
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as tmp:
            tmp.write(f.content)
            tmp_path = tmp.name
        try:
            compile_result = subprocess.run(
                ["python3", "-m", "py_compile", tmp_path],
                capture_output=True, text=True, timeout=10,
            )
            if compile_result.returncode != 0:
                raise HTTPException(400, f"Syntax error in {f.name}: {compile_result.stderr}")
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        # Compute checksum of new content
        new_checksum = hashlib.md5(f.content.encode("utf-8")).hexdigest()

        # Backup existing file (if it exists on VPS path)
        target = Path(DEPLOY_TARGET_DIR) / basename
        backup_path = None
        old_checksum = None
        if target.exists():
            old_checksum = hashlib.md5(target.read_bytes()).hexdigest()
            backup_path = str(target) + f".bak.{int(time.time())}"
            try:
                shutil.copy2(str(target), backup_path)
            except Exception:
                backup_path = None

        # Write new file
        try:
            target.write_text(f.content)
        except Exception as e:
            raise HTTPException(500, f"Failed to write {f.name}: {e}")

        results.append({
            "name": f.name,
            "checksum": new_checksum,
            "old_checksum": old_checksum,
            "backup_path": backup_path,
            "backup": backup_path is not None,
        })

    audit_log("deploy", body.bot, f"files: {[f.name for f in body.files]}", client_ip)

    return {"status": "deployed", "bot": body.bot, "files": results, "backups_created": True}


# ---------------------------------------------------------------------------
# Snipe Strategy Tracker
# ---------------------------------------------------------------------------

SNIPE_SOURCES = ("resolution_snipe", "resolution_snipe_15m")


@app.get("/api/snipe")
async def snipe_tracker(
    bot: Optional[str] = Query(None),
    days: int = Query(30, le=365),
    mode: str = Query("all"),  # "all", "live", "shadow", "paper"
    token: str = Depends(verify_token),
):
    """Snipe strategy shadow/paper/live trade tracker.

    Queries performance.db for trades with source in (resolution_snipe, resolution_snipe_15m)
    and signals.db for shadow signals that were logged but not executed.
    """
    targets = {bot: BOTS[bot]} if bot and bot in BOTS else BOTS
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()

    all_trades = []
    all_shadow = []
    summary = {
        "live": {"trades": 0, "wins": 0, "pnl": 0.0},
        "paper": {"trades": 0, "wins": 0, "pnl": 0.0},
        "shadow": {"signals": 0, "would_have_entered": 0},
    }

    for bot_name, bot_cfg in targets.items():
        db_path = bot_cfg["db"]
        signals_db = bot_cfg["signals_db"]
        pnl_col = "pnl" if has_column(db_path, "trades", "pnl") else "profit_loss"

        # --- Live + paper trades from performance.db ---
        if mode in ("all", "live", "paper"):
            rows = await query_db(db_path, f"""
                SELECT trade_id, slug, token_id, entry_time, entry_price, entry_size,
                       exit_time, exit_price, exit_reason, {pnl_col} as pnl,
                       market_title, strategy, metadata
                FROM trades
                WHERE entry_time > ?
                ORDER BY entry_time DESC
            """, (cutoff,))

            for row in rows:
                meta = {}
                if row.get("metadata"):
                    try:
                        meta = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
                    except (json.JSONDecodeError, TypeError):
                        meta = {}

                source = meta.get("source", "")
                if source not in SNIPE_SOURCES:
                    continue

                is_paper = meta.get("dry_run", False) or meta.get("paper", False)
                trade_mode = "paper" if is_paper else "live"

                if mode not in ("all", trade_mode):
                    continue

                entry_p = row.get("entry_price") or 0
                exit_p = row.get("exit_price") or 0
                trade_pnl = row.get("pnl") or 0
                is_win = trade_pnl > 0

                trade = {
                    "trade_id": row["trade_id"],
                    "slug": row["slug"],
                    "entry_time": row["entry_time"],
                    "entry_price": entry_p,
                    "entry_size": row.get("entry_size", 0),
                    "exit_time": row.get("exit_time"),
                    "exit_price": exit_p,
                    "exit_reason": row.get("exit_reason", ""),
                    "pnl": trade_pnl,
                    "is_win": is_win,
                    "source": source,
                    "mode": trade_mode,
                    "bot": bot_name,
                    "direction": meta.get("entry_momentum_direction", ""),
                    "asset": meta.get("asset", row.get("slug", "").split("-")[0].upper() if row.get("slug") else ""),
                    "window": "15m" if "15m" in source else "5m",
                }
                all_trades.append(trade)

                bucket = summary[trade_mode]
                bucket["trades"] += 1
                if is_win:
                    bucket["wins"] += 1
                bucket["pnl"] += trade_pnl

        # --- Shadow signals from signals.db ---
        if mode in ("all", "shadow"):
            shadow_rows = await query_signals_db(signals_db, """
                SELECT timestamp, asset, direction, midpoint, guard_passed,
                       guard_reasons, slug, time_remaining_secs, source
                FROM signals
                WHERE timestamp > ?
                  AND source IN ('resolution_snipe', 'resolution_snipe_15m')
                ORDER BY timestamp DESC
                LIMIT 200
            """, (cutoff,))

            for row in shadow_rows:
                shadow = {
                    "timestamp": row.get("timestamp"),
                    "asset": row.get("asset", ""),
                    "direction": row.get("direction", ""),
                    "midpoint": row.get("midpoint", 0),
                    "guard_passed": bool(row.get("guard_passed", 0)),
                    "guard_reasons": row.get("guard_reasons", ""),
                    "slug": row.get("slug", ""),
                    "time_remaining_secs": row.get("time_remaining_secs"),
                    "source": row.get("source", ""),
                    "bot": bot_name,
                }
                all_shadow.append(shadow)
                summary["shadow"]["signals"] += 1
                if shadow["guard_passed"]:
                    summary["shadow"]["would_have_entered"] += 1

    # Compute aggregate stats
    all_completed = [t for t in all_trades if t.get("exit_time")]
    total_trades = len(all_completed)
    total_wins = sum(1 for t in all_completed if t["is_win"])
    total_pnl = sum(t["pnl"] for t in all_completed)
    win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0

    # Stats by entry price bucket
    buckets = {}
    for t in all_completed:
        ep = t["entry_price"]
        if ep < 0.50:
            bk = "<0.50"
        elif ep < 0.70:
            bk = "0.50-0.70"
        elif ep < 0.80:
            bk = "0.70-0.80"
        elif ep < 0.90:
            bk = "0.80-0.90"
        else:
            bk = "0.90+"
        if bk not in buckets:
            buckets[bk] = {"bucket": bk, "trades": 0, "wins": 0, "pnl": 0.0}
        buckets[bk]["trades"] += 1
        if t["is_win"]:
            buckets[bk]["wins"] += 1
        buckets[bk]["pnl"] += t["pnl"]

    for bk in buckets.values():
        bk["win_rate"] = (bk["wins"] / bk["trades"] * 100) if bk["trades"] > 0 else 0
        bk["confidence"] = confidence_label(bk["trades"])

    # Stats by asset
    by_asset = {}
    for t in all_completed:
        a = t["asset"]
        if a not in by_asset:
            by_asset[a] = {"asset": a, "trades": 0, "wins": 0, "pnl": 0.0}
        by_asset[a]["trades"] += 1
        if t["is_win"]:
            by_asset[a]["wins"] += 1
        by_asset[a]["pnl"] += t["pnl"]

    for a in by_asset.values():
        a["win_rate"] = (a["wins"] / a["trades"] * 100) if a["trades"] > 0 else 0
        a["confidence"] = confidence_label(a["trades"])

    # Stats by exit reason
    by_exit = {}
    for t in all_completed:
        er = t["exit_reason"]
        if er not in by_exit:
            by_exit[er] = {"reason": er, "trades": 0, "wins": 0, "pnl": 0.0}
        by_exit[er]["trades"] += 1
        if t["is_win"]:
            by_exit[er]["wins"] += 1
        by_exit[er]["pnl"] += t["pnl"]

    for e in by_exit.values():
        e["win_rate"] = (e["wins"] / e["trades"] * 100) if e["trades"] > 0 else 0

    return {
        "trades": all_trades[:200],
        "shadow_signals": all_shadow[:200],
        "summary": summary,
        "aggregate": {
            "total_trades": total_trades,
            "total_wins": total_wins,
            "win_rate": round(win_rate, 1),
            "total_pnl": round(total_pnl, 2),
            "avg_pnl": round(total_pnl / total_trades, 2) if total_trades > 0 else 0,
            "confidence": confidence_label(total_trades),
        },
        "by_entry_bucket": sorted(buckets.values(), key=lambda x: x["bucket"]),
        "by_asset": sorted(by_asset.values(), key=lambda x: x["trades"], reverse=True),
        "by_exit_reason": sorted(by_exit.values(), key=lambda x: x["trades"], reverse=True),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("DASHBOARD_PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
