"""Bot Dashboard API — FastAPI backend for monitoring all Polymarket bots."""

import os
import time
import json
import sqlite3
import subprocess
import asyncio
from pathlib import Path
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

import aiosqlite

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

API_TOKEN = os.environ.get("DASHBOARD_TOKEN", "changeme")

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
}

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
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Bot Dashboard API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

    all_trades.sort(key=lambda x: x.get("exit_time") or 0, reverse=True)
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
            dt = datetime.fromtimestamp(row["exit_time"], tz=timezone.utc)
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
    bot: str = Query(...),
    token: str = Depends(verify_token),
):
    """Toggle kill switch for a bot."""
    if bot not in BOTS:
        raise HTTPException(404, f"Unknown bot: {bot}")
    ks_path = Path(BOTS[bot]["kill_switch"])
    if ks_path.exists():
        ks_path.unlink()
        return {"status": "disabled", "bot": bot}
    else:
        ks_path.touch()
        return {"status": "enabled", "bot": bot}


@app.post("/api/control/restart")
async def restart_service(
    bot: str = Query(...),
    token: str = Depends(verify_token),
):
    """Restart a bot's systemd service."""
    if bot not in BOTS:
        raise HTTPException(404, f"Unknown bot: {bot}")
    service = BOTS[bot]["service"]
    try:
        result = subprocess.run(
            ["systemctl", "restart", service],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise HTTPException(500, f"Restart failed: {result.stderr}")
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


@app.post("/api/config")
async def update_config(
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

    return {"status": "updated", "bot": bot, "key": key, "value": value,
            "note": "Restart the service for changes to take effect."}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("DASHBOARD_PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
