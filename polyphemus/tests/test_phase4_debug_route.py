"""Phase 4 tests — webapp /debug/{trade_id} query helpers.

The route itself is thin: it opens the DB, calls a handful of query
helpers, and hands the dicts to a Jinja template. We test the helpers
directly (pure sqlite + defensive missing-object handling) rather than
spinning up a FastAPI TestClient — the .venv running the rest of the
suite doesn't carry fastapi and we don't want to wedge the dependency
set here. The template rendering layer is smoke-tested manually.

Contract these tests lock in:

  1. ``_resolve_performance_db_path`` honours POLYPHEMUS_PERFORMANCE_DB
     over LAGBOT_DATA_DIR over the local-dev fallback.
  2. ``_fetch_trade`` returns a dict or None — never raises on missing.
  3. ``_fetch_timeline`` returns [] when the trade_events table is
     absent (pre-Phase-3 DB) or when no events exist for this trade.
  4. ``_fetch_timeline`` parses JSON payload and survives corrupt rows.
  5. ``_fetch_attribution`` returns None when the view is missing,
     otherwise the row dict.
  6. ``_fetch_adverse_context`` returns None unless signal_source,
     entry_mode, AND fill_model are all populated on the trade.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from polyphemus.performance_db import PerformanceDB
from polyphemus.prediction_markets.webapp.routes import debug_queries as debug_route


def _unique_db(tmp_path, stem: str) -> str:
    return str(tmp_path / f"{stem}_{uuid.uuid4().hex[:8]}.db")


def _seed_trade(
    db: PerformanceDB,
    trade_id: str,
    *,
    entry_price: float = 0.60,
    exit_price: float = 0.65,
    signal_source: str = "binance_momentum",
    fill_model: str = "v2_probabilistic",
    entry_mode: str = "maker",
    adverse_fill: int | None = None,
    adverse_fill_bps: float | None = None,
) -> None:
    now = time.time()
    db.record_entry(
        trade_id=trade_id, token_id="0xtok", slug=trade_id,
        entry_time=now, entry_price=entry_price, entry_size=10.0,
        entry_tx_hash="0xhash", outcome="UP", market_title=f"m-{trade_id}",
        fill_model=fill_model, fill_model_reason="prob_hit",
        signal_source=signal_source, entry_mode=entry_mode,
        is_dry_run=True,
    )
    pnl = (exit_price - entry_price) * 10.0
    pnl_pct = (exit_price - entry_price) / entry_price
    db.record_exit(
        trade_id=trade_id, exit_time=now + 60.0, exit_price=exit_price,
        exit_size=10.0, exit_reason="profit_target",
        exit_tx_hash="0xexit", pnl=pnl, pnl_pct=pnl_pct,
    )
    if adverse_fill is not None:
        conn = sqlite3.connect(db.db_path)
        conn.execute(
            "UPDATE trades SET adverse_fill=?, adverse_fill_bps=? WHERE trade_id=?",
            (adverse_fill, adverse_fill_bps, trade_id),
        )
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# DB path resolution
# ---------------------------------------------------------------------------


class TestResolvePerformanceDbPath:
    def test_explicit_override_wins(self, tmp_path, monkeypatch):
        override = tmp_path / "override.db"
        monkeypatch.setenv("POLYPHEMUS_PERFORMANCE_DB", str(override))
        monkeypatch.setenv("LAGBOT_DATA_DIR", "/some/other/place")
        assert debug_route._resolve_performance_db_path() == override

    def test_lagbot_data_dir_fallback(self, tmp_path, monkeypatch):
        monkeypatch.delenv("POLYPHEMUS_PERFORMANCE_DB", raising=False)
        monkeypatch.setenv("LAGBOT_DATA_DIR", str(tmp_path))
        got = debug_route._resolve_performance_db_path()
        assert got == tmp_path / "performance.db"

    def test_local_dev_fallback(self, monkeypatch):
        monkeypatch.delenv("POLYPHEMUS_PERFORMANCE_DB", raising=False)
        monkeypatch.delenv("LAGBOT_DATA_DIR", raising=False)
        got = debug_route._resolve_performance_db_path()
        # The fallback lives inside the polyphemus package root.
        assert got.name == "performance.db"
        assert got.parent.name == "data"


# ---------------------------------------------------------------------------
# _fetch_trade
# ---------------------------------------------------------------------------


class TestFetchTrade:
    def test_returns_dict_when_found(self, tmp_path):
        db = PerformanceDB(db_path=_unique_db(tmp_path, "ft"))
        _seed_trade(db, "t-a")
        conn = sqlite3.connect(db.db_path)
        conn.row_factory = sqlite3.Row
        row = debug_route._fetch_trade(conn, "t-a")
        conn.close()
        assert row is not None
        assert row["trade_id"] == "t-a"
        assert row["signal_source"] == "binance_momentum"
        assert row["fill_model"] == "v2_probabilistic"

    def test_returns_none_when_missing(self, tmp_path):
        db = PerformanceDB(db_path=_unique_db(tmp_path, "ft"))
        conn = sqlite3.connect(db.db_path)
        conn.row_factory = sqlite3.Row
        assert debug_route._fetch_trade(conn, "nonexistent") is None
        conn.close()


# ---------------------------------------------------------------------------
# _fetch_timeline
# ---------------------------------------------------------------------------


class TestFetchTimeline:
    def test_empty_when_no_events_table(self, tmp_path):
        """Pre-Phase-3 DBs have no trade_events table. Must return []
        rather than raise OperationalError."""
        path = tmp_path / "pre_phase3.db"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE trades (trade_id TEXT PRIMARY KEY)")
        conn.commit()
        conn.row_factory = sqlite3.Row
        assert debug_route._fetch_timeline(conn, "any") == []
        conn.close()

    def test_events_in_chronological_order(self, tmp_path):
        db = PerformanceDB(db_path=_unique_db(tmp_path, "tl"))
        _seed_trade(db, "t-1")
        conn = sqlite3.connect(db.db_path)
        now = time.time()
        # Seed three events out of time order; the query must sort them.
        events = [
            (2, "order_filled", '{"price": 0.60}'),
            (1, "order_placed", '{"price": 0.60, "size": 10}'),
            (3, "exit_filled", '{"pnl": 0.50}'),
        ]
        for offset, ev_type, payload in events:
            conn.execute(
                "INSERT INTO trade_events (trade_id, ts, event_type, payload) "
                "VALUES (?, ?, ?, ?)",
                ("t-1", now + offset, ev_type, payload),
            )
        conn.commit()
        conn.row_factory = sqlite3.Row

        timeline = debug_route._fetch_timeline(conn, "t-1")
        conn.close()

        assert len(timeline) == 3
        assert [e["event_type"] for e in timeline] == [
            "order_placed", "order_filled", "exit_filled",
        ]
        # Payload JSON must be parsed, not passed as a raw string.
        assert timeline[0]["payload"] == {"price": 0.60, "size": 10}

    def test_corrupt_payload_surfaced_not_dropped(self, tmp_path):
        """Malformed JSON gets wrapped in {"_raw": ...} so the operator
        can see what went wrong instead of a silent drop."""
        db = PerformanceDB(db_path=_unique_db(tmp_path, "corrupt"))
        _seed_trade(db, "t-c")
        conn = sqlite3.connect(db.db_path)
        conn.execute(
            "INSERT INTO trade_events (trade_id, ts, event_type, payload) "
            "VALUES (?, ?, ?, ?)",
            ("t-c", time.time(), "weird_event", "{not json"),
        )
        conn.commit()
        conn.row_factory = sqlite3.Row

        timeline = debug_route._fetch_timeline(conn, "t-c")
        conn.close()
        assert len(timeline) == 1
        assert "_raw" in timeline[0]["payload"]
        assert timeline[0]["payload"]["_raw"] == "{not json"


# ---------------------------------------------------------------------------
# _fetch_attribution / _fetch_adverse_context
# ---------------------------------------------------------------------------


class TestFetchAttribution:
    def test_returns_row_when_view_exists(self, tmp_path):
        db = PerformanceDB(db_path=_unique_db(tmp_path, "attr"))
        _seed_trade(db, "t-a", entry_price=0.90, exit_price=1.00)
        conn = sqlite3.connect(db.db_path)
        conn.row_factory = sqlite3.Row
        row = debug_route._fetch_attribution(conn, "t-a")
        conn.close()
        assert row is not None
        # Matches the SQL CASE — should be 85-93 for price 0.90.
        assert row["entry_band"] == "85-93"
        assert row["is_win"] == 1

    def test_returns_none_when_view_missing(self, tmp_path):
        """A DB that wasn't opened via PerformanceDB has no views."""
        path = tmp_path / "raw.db"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE trades (trade_id TEXT)")
        conn.commit()
        conn.row_factory = sqlite3.Row
        assert debug_route._fetch_attribution(conn, "any") is None
        conn.close()


class TestFetchAdverseContext:
    def test_requires_all_three_columns_populated(self, tmp_path):
        db = PerformanceDB(db_path=_unique_db(tmp_path, "adv-null"))
        _seed_trade(db, "t-null", signal_source="", fill_model="")
        conn = sqlite3.connect(db.db_path)
        conn.row_factory = sqlite3.Row
        trade = debug_route._fetch_trade(conn, "t-null")
        assert debug_route._fetch_adverse_context(conn, trade) is None
        conn.close()

    def test_returns_rollup_when_populated(self, tmp_path):
        db = PerformanceDB(db_path=_unique_db(tmp_path, "adv-full"))
        # Seed two rows with matching peer-group columns; only one has
        # adverse_fill populated so the view aggregates it.
        _seed_trade(
            db, "t-peer",
            signal_source="binance_momentum", fill_model="v2_probabilistic",
            entry_mode="maker", adverse_fill=1, adverse_fill_bps=15.0,
        )
        _seed_trade(
            db, "t-main",
            signal_source="binance_momentum", fill_model="v2_probabilistic",
            entry_mode="maker", adverse_fill=0, adverse_fill_bps=-2.0,
        )
        conn = sqlite3.connect(db.db_path)
        conn.row_factory = sqlite3.Row
        trade = debug_route._fetch_trade(conn, "t-main")
        rollup = debug_route._fetch_adverse_context(conn, trade)
        conn.close()
        assert rollup is not None
        assert rollup["n"] == 2
        assert rollup["adv_rate"] == 0.5
