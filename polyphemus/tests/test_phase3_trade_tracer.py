"""Phase 3 tests for TradeTracer and trade_events table.

The tracer is the replay backbone for trade debugging — every event
written here must survive untouched into ``debug_trade`` and the
webapp ``/debug/{trade_id}`` page. These tests lock:

  1. The schema exists with expected columns.
  2. emit() → timeline() round-trips trade_id, event_type, payload, ts.
  3. Tracer is OFF by default (POLYPHEMUS_TRACER_ENABLED unset) so
     Phase 3 can land without flipping emission on surprise.
  4. A DB failure inside emit() does NOT raise to the caller — the
     trade path keeps moving even if the events table is locked.
  5. Timeline returns events oldest-first regardless of insert order.
  6. event_types_seen() returns just the distinct categories.
"""

import json
import sqlite3
import uuid

import pytest

from polyphemus.performance_db import PerformanceDB
from polyphemus.trade_tracer import TradeTracer, EventType


def _db(tmp_path, stem: str) -> str:
    path = str(tmp_path / f"{stem}_{uuid.uuid4().hex[:8]}.db")
    PerformanceDB(db_path=path)  # ensure schema exists
    return path


@pytest.fixture
def tracer_on(monkeypatch):
    monkeypatch.setenv("POLYPHEMUS_TRACER_ENABLED", "true")


class TestSchema:
    def test_trade_events_table_created(self, tmp_path):
        path = _db(tmp_path, "events_schema")
        conn = sqlite3.connect(path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(trade_events)").fetchall()]
        conn.close()
        assert set(cols) >= {"event_id", "trade_id", "ts", "event_type", "payload"}

    def test_trade_events_index_exists(self, tmp_path):
        path = _db(tmp_path, "events_idx")
        conn = sqlite3.connect(path)
        idx_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='trade_events'"
        ).fetchall()
        conn.close()
        names = {r[0] for r in idx_rows}
        assert "idx_trade_events_trade_id_ts" in names


class TestEmitRoundTrip:
    def test_emit_then_timeline(self, tmp_path, tracer_on):
        path = _db(tmp_path, "events_rt")
        tr = TradeTracer(db_path=path)
        tr.emit("t-1", EventType.SIGNAL_FIRED, {"momentum_pct": 0.42})
        tr.emit("t-1", EventType.ORDER_PLACED, {"price": 0.55, "qty": 10})
        tr.emit("t-1", EventType.ORDER_FILLED, {"fill_price": 0.55})

        timeline = tr.timeline("t-1")
        assert len(timeline) == 3
        assert [e.event_type for e in timeline] == [
            EventType.SIGNAL_FIRED, EventType.ORDER_PLACED, EventType.ORDER_FILLED,
        ]
        assert timeline[0].payload == {"momentum_pct": 0.42}
        assert timeline[1].payload == {"price": 0.55, "qty": 10}

    def test_other_trade_ids_isolated(self, tmp_path, tracer_on):
        path = _db(tmp_path, "events_iso")
        tr = TradeTracer(db_path=path)
        tr.emit("t-A", EventType.SIGNAL_FIRED, {"asset": "BTC"})
        tr.emit("t-B", EventType.SIGNAL_FIRED, {"asset": "ETH"})

        a = tr.timeline("t-A")
        assert len(a) == 1
        assert a[0].payload == {"asset": "BTC"}

    def test_event_types_seen(self, tmp_path, tracer_on):
        path = _db(tmp_path, "events_seen")
        tr = TradeTracer(db_path=path)
        tr.emit("t-2", EventType.SIGNAL_FIRED)
        tr.emit("t-2", EventType.ORDER_PLACED)
        tr.emit("t-2", EventType.ORDER_PLACED)  # duplicate
        assert tr.event_types_seen("t-2") == {
            EventType.SIGNAL_FIRED, EventType.ORDER_PLACED,
        }


class TestDefaultOff:
    def test_emit_is_silent_when_flag_unset(self, tmp_path, monkeypatch):
        """Tracer stays off through Phase 4 so callsites can land
        without switching emission on. Emitting should be a no-op.
        """
        monkeypatch.delenv("POLYPHEMUS_TRACER_ENABLED", raising=False)
        path = _db(tmp_path, "events_off")
        tr = TradeTracer(db_path=path)
        tr.emit("t-off", EventType.SIGNAL_FIRED, {"x": 1})

        # Still open the table directly (not via tracer) — if emit was
        # silenced, no row exists.
        conn = sqlite3.connect(path)
        count = conn.execute("SELECT COUNT(*) FROM trade_events WHERE trade_id='t-off'").fetchone()[0]
        conn.close()
        assert count == 0


class TestFailureMode:
    def test_emit_never_raises(self, tmp_path, tracer_on, monkeypatch):
        """A DB write failure inside emit MUST NOT propagate — a
        tracer hiccup can't be allowed to block the trade path.
        """
        path = str(tmp_path / "does_not_exist_dir" / "nope.db")
        tr = TradeTracer(db_path=path)
        # Should log-and-drop rather than raise FileNotFoundError
        tr.emit("t-fail", EventType.SIGNAL_FIRED, {"x": 1})


class TestOrdering:
    def test_timeline_ordered_by_ts(self, tmp_path, tracer_on):
        path = _db(tmp_path, "events_order")
        tr = TradeTracer(db_path=path)
        # Insert out-of-order explicit ts
        tr.emit("t-ord", EventType.EXIT_FILLED, {"step": 3}, ts=300.0)
        tr.emit("t-ord", EventType.SIGNAL_FIRED, {"step": 1}, ts=100.0)
        tr.emit("t-ord", EventType.ORDER_PLACED, {"step": 2}, ts=200.0)
        timeline = tr.timeline("t-ord")
        assert [e.payload["step"] for e in timeline] == [1, 2, 3]


class TestJsonlSidecar:
    def test_jsonl_written_when_env_set(self, tmp_path, tracer_on, monkeypatch):
        sidecar = tmp_path / "trade_events.jsonl"
        monkeypatch.setenv("POLYPHEMUS_TRACER_JSONL", str(sidecar))
        path = _db(tmp_path, "events_jsonl")
        tr = TradeTracer(db_path=path)
        tr.emit("t-js", EventType.SIGNAL_FIRED, {"asset": "BTC"})
        assert sidecar.exists()
        line = sidecar.read_text().strip()
        record = json.loads(line)
        assert record["trade_id"] == "t-js"
        assert record["event_type"] == EventType.SIGNAL_FIRED
        assert record["payload"] == {"asset": "BTC"}
