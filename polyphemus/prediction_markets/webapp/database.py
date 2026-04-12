"""Database layer for the trading dashboard."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import AppSettings

DEFAULT_DB = Path(__file__).resolve().parent.parent / "weather" / "data" / "paper_trades.db"

_EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_cache (
    id TEXT PRIMARY KEY,
    scanner_type TEXT NOT NULL,
    data_json TEXT NOT NULL,
    scanned_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scan_cache_type ON scan_cache(scanner_type);
"""


def get_db(db_path: Path | None = None) -> sqlite3.Connection:
    """Get DB connection, creating tables if needed."""
    from ..weather.paper_tracker import get_db as _weather_db
    conn = _weather_db(db_path or DEFAULT_DB)
    conn.executescript(_EXTRA_SCHEMA)
    return conn


def get_open_trades(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM paper_trades WHERE resolved = 0 ORDER BY market_date"
    ).fetchall()
    return [dict(r) for r in rows]


def get_resolved_trades(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM paper_trades WHERE resolved = 1 ORDER BY resolved_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_portfolio_summary(conn: sqlite3.Connection) -> dict:
    from ..weather.paper_tracker import summary
    return summary(conn)


def get_pnl_timeseries(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT resolved_at, pnl FROM paper_trades
        WHERE resolved = 1 AND pnl IS NOT NULL
        ORDER BY resolved_at"""
    ).fetchall()
    cumulative = 0.0
    series = []
    for r in rows:
        cumulative += r["pnl"]
        series.append({"date": r["resolved_at"][:10], "pnl": round(r["pnl"], 2), "cumulative": round(cumulative, 2)})
    return series


def save_settings(conn: sqlite3.Connection, settings: AppSettings) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for key, value in settings.model_dump().items():
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, json.dumps(value), now),
        )
    conn.commit()


def load_settings(conn: sqlite3.Connection) -> AppSettings:
    rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    if not rows:
        return AppSettings()
    data = {r["key"]: json.loads(r["value"]) for r in rows}
    return AppSettings(**{k: v for k, v in data.items() if k in AppSettings.model_fields})


def cache_scan_results(conn: sqlite3.Connection, scanner_type: str, opportunities: list[dict]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("DELETE FROM scan_cache WHERE scanner_type = ?", (scanner_type,))
    for opp in opportunities:
        opp_id = opp.get("id", f"{scanner_type}:{hash(json.dumps(opp, sort_keys=True, default=str))}")
        conn.execute(
            "INSERT OR REPLACE INTO scan_cache (id, scanner_type, data_json, scanned_at) VALUES (?, ?, ?, ?)",
            (opp_id, scanner_type, json.dumps(opp, default=str), now),
        )
    conn.commit()


def load_cached_scan(conn: sqlite3.Connection, scanner_type: str) -> list[dict]:
    rows = conn.execute(
        "SELECT data_json FROM scan_cache WHERE scanner_type = ? ORDER BY scanned_at DESC",
        (scanner_type,),
    ).fetchall()
    return [json.loads(r["data_json"]) for r in rows]
