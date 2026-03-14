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
    "ORACLE_ENABLED", "ORACLE_FLIP_ENABLED", "ORACLE_FLIP_DRY_RUN",
    "ORACLE_STALE_THRESHOLD_SECS", "ORACLE_ALCHEMY_API_KEY",
    "MM_DRY_RUN", "MM_ENABLED",
    "SNIPE_ASSETS", "SHADOW_ASSETS", "SNIPE_15M_DRY_RUN",
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
            dt = datetime.fromtimestamp(row["exit_time"], tz=timezone.utc)
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
    all_recent.sort(key=lambda x: x.get("exit_time") or 0, reverse=True)

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
    sorted_by_time = sorted(all_recent, key=lambda x: x.get("exit_time") or 0, reverse=True)
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
            pnl_val = row["pnl"] or 0
            is_win = pnl_val > 0
            slug = row["slug"] or ""

            # Extract asset from slug (e.g. "btc-updown-5m-123" -> "BTC")
            asset = slug.split("-")[0].upper() if slug else "UNKNOWN"

            # Direction from outcome column
            direction = row.get("outcome") or "unknown"

            # Entry price bucket
            ep = row.get("entry_price") or 0
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
            hour_utc = datetime.fromtimestamp(exit_t, tz=timezone.utc).hour if exit_t else 0

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
    all_trades_for_cum.sort(key=lambda x: x["exit_time"])
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
