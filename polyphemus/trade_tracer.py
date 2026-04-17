"""Phase 3 — trade lifecycle event tracer.

Single emission point for every timestamped event we want a future
debugger to see when they reconstruct a trade. Writes to the
``trade_events`` table (created in PerformanceDB._init_trade_events)
and optionally mirrors each event to a JSONL sidecar for grep-ability.

Design guarantees:

  1. Emission never raises to the caller — a tracer failure must NOT
     block the trade path. We log and drop.
  2. Per-emit budget ~2ms (measured in tests). We use a per-call
     SQLite connection to match the rest of performance_db and
     avoid keeping a long-lived handle in async code.
  3. Gated by ``POLYPHEMUS_TRACER_ENABLED`` so Phase 3 can land with
     the tracer silent by default; Phase 5 flips the flag.
  4. Optional JSONL sidecar at ``logs/trade_events.jsonl`` (env:
     ``POLYPHEMUS_TRACER_JSONL=path``). One JSON object per line so
     grep/jq can triage without loading the DB.

Public surface:
  - ``EventType`` — canonical event-type strings used by emitters
  - ``TradeTracer`` — ``emit(trade_id, event_type, payload=None)`` and
    ``timeline(trade_id)``
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from .config import setup_logger


logger = setup_logger('polyphemus.trade_tracer')


class EventType:
    """Canonical event-type strings. Use these constants at emitters so
    downstream viewers/SQL don't have to guess at spellings.
    """
    SIGNAL_FIRED = 'signal_fired'
    SIZING_COMPUTED = 'sizing_computed'
    ORDER_PLACED = 'order_placed'
    ORDER_FILLED = 'order_filled'
    ADVERSE_CHECK_RUN = 'adverse_check_run'
    MIDPOINT_POLLED = 'midpoint_polled'
    EXIT_DECISION = 'exit_decision'
    EXIT_ORDER_PLACED = 'exit_order_placed'
    EXIT_FILLED = 'exit_filled'
    RESOLUTION_DETECTED = 'resolution_detected'
    REDEMPTION_CLAIMED = 'redemption_claimed'
    FORCE_CLOSED = 'force_closed'
    ERROR = 'error'


def _tracer_enabled() -> bool:
    """Tracer is opt-in through Phase 4 so callsites can land without
    surprising anyone. Phase 5 flips this on emmanuel.
    """
    return os.getenv('POLYPHEMUS_TRACER_ENABLED', 'false').lower() in ('1', 'true', 'yes')


@dataclass(frozen=True)
class TraceEvent:
    """Shape returned by ``TradeTracer.timeline()``."""
    event_id: int
    trade_id: str
    ts: float
    event_type: str
    payload: Optional[dict]


class TradeTracer:
    """Emit + replay lifecycle events for a trade_id.

    Constructed once per process and passed to anything that might
    want to emit. Stateless beyond the DB path + JSONL sidecar.
    """

    def __init__(self, db_path: str, jsonl_path: Optional[str] = None):
        self._db_path = db_path
        self._jsonl_path = jsonl_path or os.getenv('POLYPHEMUS_TRACER_JSONL') or None
        self._logger = logger

    def emit(
        self,
        trade_id: str,
        event_type: str,
        payload: Optional[dict] = None,
        ts: Optional[float] = None,
    ) -> None:
        """Write one event. Never raises.

        ``ts`` defaults to ``time.time()`` when omitted. Callers should
        pass explicit ts only when the event is being recorded after
        the fact (replay, backfill) so the timeline preserves causal
        order.
        """
        if not _tracer_enabled():
            return
        if ts is None:
            ts = time.time()
        payload_json = json.dumps(payload) if payload else None
        try:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute('PRAGMA journal_mode=WAL')
                conn.execute(
                    'INSERT INTO trade_events (trade_id, ts, event_type, payload) '
                    'VALUES (?, ?, ?, ?)',
                    (trade_id, ts, event_type, payload_json),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:  # pragma: no cover - exercised by failure-mode test
            # Drop the event but log it — we MUST NOT propagate out to the
            # trade path, otherwise a DB hiccup would block live orders.
            self._logger.warning(
                f'trade_tracer emit failed for {trade_id}/{event_type}: {e}'
            )

        if self._jsonl_path:
            try:
                with open(self._jsonl_path, 'a', encoding='utf-8') as f:
                    f.write(json.dumps({
                        'trade_id': trade_id,
                        'ts': ts,
                        'event_type': event_type,
                        'payload': payload,
                    }) + '\n')
            except Exception as e:  # pragma: no cover
                self._logger.warning(f'trade_tracer jsonl write failed: {e}')

    def timeline(self, trade_id: str) -> list[TraceEvent]:
        """Return all events for ``trade_id`` oldest-first.

        Read path does raise on failure — callers (debug_trade CLI,
        webapp route) should surface errors explicitly rather than
        silently show an empty timeline.
        """
        conn = sqlite3.connect(self._db_path)
        try:
            cur = conn.execute(
                'SELECT event_id, trade_id, ts, event_type, payload '
                'FROM trade_events WHERE trade_id = ? ORDER BY ts ASC, event_id ASC',
                (trade_id,),
            )
            events: list[TraceEvent] = []
            for row in cur.fetchall():
                payload = json.loads(row[4]) if row[4] else None
                events.append(TraceEvent(
                    event_id=row[0], trade_id=row[1], ts=row[2],
                    event_type=row[3], payload=payload,
                ))
            return events
        finally:
            conn.close()

    def event_types_seen(self, trade_id: str) -> set[str]:
        """Quick helper for assertions / dashboards: which event
        categories fired for this trade.
        """
        return {e.event_type for e in self.timeline(trade_id)}


# Module-level singleton so instrumented callsites can do
# ``from .trade_tracer import emit`` without threading a tracer object
# through every class. The DB path is resolved lazily from either an
# explicit env var or the LAGBOT_DATA_DIR convention used across the
# codebase.
_GLOBAL: Optional[TradeTracer] = None


def _resolve_db_path() -> Optional[str]:
    explicit = os.getenv('POLYPHEMUS_TRACER_DB_PATH')
    if explicit:
        return explicit
    data_dir = os.getenv('LAGBOT_DATA_DIR')
    if data_dir:
        return os.path.join(data_dir, 'performance.db')
    # Last-resort default matches polyphemus/ccd packaging convention.
    return None


def emit(trade_id: str, event_type: str, payload: Optional[dict] = None) -> None:
    """Module-level emit that reuses one TradeTracer instance.

    Short-circuits silently when the flag is off OR no DB path can
    be resolved. Callers should not have to know either detail.
    """
    if not _tracer_enabled():
        return
    global _GLOBAL
    if _GLOBAL is None:
        db_path = _resolve_db_path()
        if db_path is None:
            return
        _GLOBAL = TradeTracer(db_path=db_path)
    _GLOBAL.emit(trade_id, event_type, payload)


def reset_global_for_tests() -> None:
    """Tests that monkeypatch POLYPHEMUS_TRACER_DB_PATH call this to
    force re-resolution on the next emit().
    """
    global _GLOBAL
    _GLOBAL = None
