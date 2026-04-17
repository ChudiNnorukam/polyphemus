"""Phase 2 tests for record_entry v4 observability pass-through.

The DB layer now accepts 9 new optional kwargs; these tests lock the
contract that callers can set them and a v4-migrated DB will persist
each one to its own column. Older callers that omit the kwargs must
continue to work unchanged.

What we're proving:

  1. Every v4 kwarg round-trips to its SQL column.
  2. Omitting a v4 kwarg stores NULL — no silent default that would
     mask missing instrumentation during rollout.
  3. ``signal_source`` falls back to ``metadata['source']`` in the
     async ``PerformanceTracker`` wrapper, so pair_arb / weather /
     accumulator entries auto-populate the attribution column without
     needing every record_entry callsite updated in lock-step.
"""

import asyncio
import sqlite3
import uuid

import pytest

from polyphemus.performance_db import PerformanceDB
from polyphemus.performance_tracker import PerformanceTracker


def _unique_db(tmp_path, stem: str) -> str:
    return str(tmp_path / f"{stem}_{uuid.uuid4().hex[:8]}.db")


def _record_minimal(db: PerformanceDB, trade_id: str, **extra) -> None:
    db.record_entry(
        trade_id=trade_id,
        token_id="0xtok",
        slug="test-slug",
        entry_time=1700000000.0,
        entry_price=0.55,
        entry_size=10.0,
        entry_tx_hash="0xhash",
        outcome="YES",
        market_title="test market",
        **extra,
    )


class TestV4FieldRoundTrip:
    def test_all_nine_fields_persisted(self, tmp_path):
        db = PerformanceDB(db_path=_unique_db(tmp_path, "v4_roundtrip"))
        _record_minimal(
            db, "t-1",
            fill_model="v2_probabilistic",
            fill_model_reason="prob_hit",
            signal_id=42,
            fill_latency_ms=180,
            book_spread_at_entry=0.02,
            book_depth_bid=1000.0,
            book_depth_ask=850.0,
            entry_mode="maker",
            signal_source="binance_momentum",
        )
        conn = sqlite3.connect(db.db_path)
        row = conn.execute(
            """SELECT fill_model, fill_model_reason, signal_id, fill_latency_ms,
                      book_spread_at_entry, book_depth_bid, book_depth_ask,
                      entry_mode, signal_source
               FROM trades WHERE trade_id='t-1'"""
        ).fetchone()
        conn.close()
        assert row == (
            "v2_probabilistic", "prob_hit", 42, 180,
            0.02, 1000.0, 850.0, "maker", "binance_momentum",
        )

    def test_missing_kwargs_persist_null(self, tmp_path):
        """No silent defaults — missing instrumentation must surface as NULL
        so the Phase 5 "fill_model non-NULL for 100% of new trades" gate
        catches callers that haven't been updated yet.
        """
        db = PerformanceDB(db_path=_unique_db(tmp_path, "v4_nulls"))
        _record_minimal(db, "t-bare")
        conn = sqlite3.connect(db.db_path)
        row = conn.execute(
            """SELECT fill_model, signal_id, book_spread_at_entry, signal_source
               FROM trades WHERE trade_id='t-bare'"""
        ).fetchone()
        conn.close()
        assert row == (None, None, None, None)


class TestSignalSourceFallback:
    def test_tracker_derives_signal_source_from_metadata(self, tmp_path):
        """Many existing callsites pass source via metadata JSON; the
        tracker should auto-lift it to the indexed column so we don't
        have to touch every callsite in Phase 2.
        """
        path = _unique_db(tmp_path, "v4_srcfallback")
        tracker = PerformanceTracker(db_path=path)

        asyncio.run(tracker.record_entry(
            trade_id="t-src",
            token_id="0xtok",
            slug="test",
            entry_price=0.55,
            entry_size=10.0,
            entry_tx_hash="0xhash",
            outcome="YES",
            market_title="m",
            entry_time=1700000000.0,
            metadata={"source": "pair_arb", "other": "field"},
        ))

        conn = sqlite3.connect(path)
        row = conn.execute(
            "SELECT signal_source FROM trades WHERE trade_id='t-src'"
        ).fetchone()
        conn.close()
        assert row[0] == "pair_arb"

    def test_explicit_signal_source_wins_over_metadata(self, tmp_path):
        path = _unique_db(tmp_path, "v4_srcwins")
        tracker = PerformanceTracker(db_path=path)

        asyncio.run(tracker.record_entry(
            trade_id="t-explicit",
            token_id="0xtok",
            slug="test",
            entry_price=0.55,
            entry_size=10.0,
            entry_tx_hash="0xhash",
            outcome="YES",
            market_title="m",
            entry_time=1700000000.0,
            metadata={"source": "pair_arb"},
            signal_source="binance_momentum",
        ))

        conn = sqlite3.connect(path)
        row = conn.execute(
            "SELECT signal_source FROM trades WHERE trade_id='t-explicit'"
        ).fetchone()
        conn.close()
        assert row[0] == "binance_momentum"


class TestLegacyCallersStillWork:
    def test_record_entry_without_v4_kwargs_still_persists(self, tmp_path):
        """Every existing record_entry callsite that hasn't been updated
        for Phase 2 must continue to produce a valid trade row.
        """
        db = PerformanceDB(db_path=_unique_db(tmp_path, "v4_legacy_call"))
        _record_minimal(db, "t-legacy")
        conn = sqlite3.connect(db.db_path)
        row = conn.execute(
            "SELECT trade_id, entry_price, entry_size FROM trades WHERE trade_id='t-legacy'"
        ).fetchone()
        conn.close()
        assert row == ("t-legacy", 0.55, 10.0)
