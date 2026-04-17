"""Phase 4 — /debug/{trade_id} timeline + attribution view.

A single trade_id has to be enough to reconstruct what happened. The
route pulls four blocks from the lagbot performance.db:

    * trade row       (all attribution columns — fill_model, signal_source,
                       book_spread_at_entry, etc.)
    * timeline        (trade_events joined chronologically)
    * attribution     (bucket / band / fill-model context)
    * adverse summary (if the trade has adverse_fill populated)

No dashboard state, no scanner hooks — this is a read-only inspection
surface. The DB connection is opened per-request and closed in a
try/finally so a slow query can't leak into the shared app.state.db
connection used by the rest of the dashboard.

Query logic lives in :mod:`debug_queries` so it can be unit-tested
without FastAPI installed. This module is a thin router shim.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .debug_queries import (
    _fetch_adverse_context,
    _fetch_attribution,
    _fetch_attribution_rollup,
    _fetch_timeline,
    _fetch_trade,
    _open_db,
    _resolve_performance_db_path,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/debug/{trade_id}", response_class=HTMLResponse)
async def debug_trade(trade_id: str, request: Request):
    """Render the debug page for one trade_id.

    404 behavior: if the DB is missing, render a message. If the DB is
    present but trade_id is unknown, render a "not found" message with
    the DB path so the caller can spot instance-mismatches.
    """
    templates = request.app.state.templates
    db_path = _resolve_performance_db_path()
    conn = _open_db()
    if conn is None:
        return templates.TemplateResponse(
            "debug.html",
            {
                "request": request,
                "active_page": "debug",
                "trade_id": trade_id,
                "db_missing": True,
                "db_path": str(db_path),
                "trade": None, "attribution": None,
                "timeline": [], "adverse_context": None,
            },
            status_code=404,
        )
    try:
        trade = _fetch_trade(conn, trade_id)
        if not trade:
            return templates.TemplateResponse(
                "debug.html",
                {
                    "request": request,
                    "active_page": "debug",
                    "trade_id": trade_id,
                    "db_missing": False,
                    "db_path": str(db_path),
                    "trade": None, "attribution": None,
                    "timeline": [], "adverse_context": None,
                },
                status_code=404,
            )
        return templates.TemplateResponse(
            "debug.html",
            {
                "request": request,
                "active_page": "debug",
                "trade_id": trade_id,
                "db_missing": False,
                "db_path": str(db_path),
                "trade": trade,
                "attribution": _fetch_attribution(conn, trade_id),
                "timeline": _fetch_timeline(conn, trade_id),
                "adverse_context": _fetch_adverse_context(conn, trade),
            },
        )
    finally:
        conn.close()


@router.get("/api/attribution", response_class=JSONResponse)
async def attribution_api(request: Request):
    """JSON rollup from vw_strategy_perf.

    Query params:
        source       optional; filter to one signal_source
        fill_model   optional; filter to one fill_model
        is_dry_run   optional (0/1); default: both

    Returns: {"rows": [...], "count": N, "generated_at": <epoch>}
    """
    params = request.query_params
    sig = params.get("source") or ""
    fm = params.get("fill_model") or ""
    dry_raw = params.get("is_dry_run")
    dry_flag = None
    if dry_raw is not None and dry_raw != "":
        try:
            dry_flag = int(dry_raw)
        except ValueError:
            dry_flag = None

    conn = _open_db()
    if conn is None:
        return JSONResponse(
            {"error": "performance.db not found",
             "db_path": str(_resolve_performance_db_path())},
            status_code=503,
        )
    try:
        rows = _fetch_attribution_rollup(
            conn,
            signal_source=sig,
            fill_model=fm,
            is_dry_run=dry_flag,
        )
    finally:
        conn.close()
    return JSONResponse({
        "rows": rows,
        "count": len(rows),
        "generated_at": time.time(),
    })
