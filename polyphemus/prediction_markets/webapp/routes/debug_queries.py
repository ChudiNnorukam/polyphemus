"""Pure-sqlite helpers for /debug/{trade_id}.

Split from ``debug.py`` so the query logic can be imported and tested
without pulling in FastAPI. The route module is a thin shim on top of
these functions — it opens a read-only connection, calls each helper,
and hands the dicts to Jinja.

DB path resolution order:
    1. POLYPHEMUS_PERFORMANCE_DB env var (explicit override)
    2. LAGBOT_DATA_DIR env var -> $LAGBOT_DATA_DIR/performance.db
    3. polyphemus/data/performance.db (local dev)

Each fetcher defends against missing tables/views: older DBs
(pre-Phase-3, pre-Phase-4) still render a best-effort debug page
instead of 500ing.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# polyphemus/prediction_markets/webapp/routes -> polyphemus/
_POLYPHEMUS_ROOT = Path(__file__).resolve().parents[3]


def _resolve_performance_db_path() -> Path:
    """Return the best guess for the lagbot performance.db location.

    Never raises — if nothing matches, returns the local-dev path so the
    route can render a "DB not found" message instead of 500ing.
    """
    override = os.environ.get("POLYPHEMUS_PERFORMANCE_DB")
    if override:
        return Path(override)
    data_dir = os.environ.get("LAGBOT_DATA_DIR")
    if data_dir:
        return Path(data_dir) / "performance.db"
    return _POLYPHEMUS_ROOT / "data" / "performance.db"


def _open_db() -> Optional[sqlite3.Connection]:
    """Open the performance DB read-only. Returns None if missing so the
    route can render a friendly message rather than crashing."""
    path = _resolve_performance_db_path()
    if not path.exists():
        logger.warning("performance.db not found at %s", path)
        return None
    # ``mode=ro`` prevents writes; the debug page is inspection-only and
    # must not accidentally mutate a production DB.
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_trade(conn: sqlite3.Connection, trade_id: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM trades WHERE trade_id = ?", (trade_id,)
    ).fetchone()
    return dict(row) if row else None


def _fetch_timeline(conn: sqlite3.Connection, trade_id: str) -> list[dict]:
    """Read trade_events for this trade in chronological order.

    Trade_events may not exist on older databases (pre-Phase 3 migration)
    — handle that as an empty timeline rather than a 500.
    """
    try:
        rows = conn.execute(
            "SELECT event_id, ts, event_type, payload FROM trade_events "
            "WHERE trade_id = ? ORDER BY ts ASC, event_id ASC",
            (trade_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for r in rows:
        payload_raw = r["payload"]
        try:
            payload = json.loads(payload_raw) if payload_raw else {}
        except (TypeError, json.JSONDecodeError):
            # Surface the raw string rather than drop it; the page is
            # for debugging, so corrupt-but-present is better than gone.
            payload = {"_raw": payload_raw}
        out.append({
            "event_id": r["event_id"],
            "ts": r["ts"],
            "event_type": r["event_type"],
            "payload": payload,
        })
    return out


def _fetch_attribution(conn: sqlite3.Connection, trade_id: str) -> Optional[dict]:
    """Read vw_trade_attribution, if the view exists. Supplies entry_band
    + is_win without forcing the template to duplicate the CASE."""
    try:
        row = conn.execute(
            "SELECT * FROM vw_trade_attribution WHERE trade_id = ?",
            (trade_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(row) if row else None


def _fetch_adverse_context(conn: sqlite3.Connection, trade: dict) -> Optional[dict]:
    """Roll up adverse-selection metrics for this trade's (signal_source,
    entry_mode, fill_model) peer group. Gives an at-a-glance 'is this
    trade's adverse fill typical or anomalous?' without another page."""
    sig = trade.get("signal_source")
    mode = trade.get("entry_mode")
    fm = trade.get("fill_model")
    if not (sig and mode and fm):
        return None
    try:
        row = conn.execute(
            "SELECT * FROM vw_adverse_selection "
            "WHERE signal_source = ? AND entry_mode = ? AND fill_model = ?",
            (sig, mode, fm),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(row) if row else None


def _fetch_attribution_rollup(
    conn: sqlite3.Connection,
    signal_source: str = "",
    fill_model: str = "",
    is_dry_run: Optional[int] = None,
) -> list[dict]:
    """Return rows from vw_strategy_perf with Python-side filters.

    Filtering in Python rather than with SQL params keeps the view
    stable (it groups by every column; the caller just trims) and
    lets the route cache the full rollup if we ever want to.
    """
    try:
        rows = conn.execute("SELECT * FROM vw_strategy_perf").fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for r in rows:
        d = dict(r)
        if signal_source and d.get("signal_source") != signal_source:
            continue
        if fill_model and d.get("fill_model") != fill_model:
            continue
        if is_dry_run is not None:
            try:
                if int(d.get("is_dry_run") or 0) != int(is_dry_run):
                    continue
            except (TypeError, ValueError):
                continue
        out.append(d)
    return out
