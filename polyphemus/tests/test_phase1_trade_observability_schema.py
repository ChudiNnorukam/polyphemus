"""Phase 1 schema tests for the Trade Observability Overhaul.

The migration lives in :meth:`PerformanceDB._migrate_v4_trade_observability`.
It is the foundation for attribution (every trade debuggable in isolation);
later phases populate the new columns at write time and build viewers on top.

Coverage here focuses on what the migration itself must guarantee:

  1. All nine v4 columns exist after the migration runs.
  2. Historical rows get sensible backfills (fill_model, signal_source) rather
     than silent NULL buckets.
  3. NULL ``pnl`` on open trades is cleared (kills the SUM(pnl) blind spot)
     without masking anomalies on closed trades.
  4. The four attribution indexes are present.
  5. Re-running is idempotent (safe on every startup).
"""

import json
import sqlite3
import time
import uuid

from polyphemus.performance_db import PerformanceDB


V4_COLUMNS = (
    "fill_model",
    "fill_model_reason",
    "signal_id",
    "fill_latency_ms",
    "book_spread_at_entry",
    "book_depth_bid",
    "book_depth_ask",
    "entry_mode",
    "signal_source",
)

V4_INDEXES = (
    "idx_trades_signal_id",
    "idx_trades_fill_model",
    "idx_trades_signal_source",
    "idx_trades_is_dry_run_strategy",
)


def _unique_db(tmp_path, stem: str) -> str:
    return str(tmp_path / f"{stem}_{uuid.uuid4().hex[:8]}.db")


def _seed_legacy_trades(db_path: str, rows: list[dict]) -> None:
    """Create a pre-v4 trades table and insert rows with arbitrary metadata.

    Mirrors the V1 schema shape used in :mod:`test_phase0_is_dry_run_migration`
    so migration logic is exercised against realistic historical data.
    """
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            trade_id TEXT PRIMARY KEY,
            token_id TEXT,
            slug TEXT,
            entry_time REAL,
            entry_price REAL,
            entry_size REAL,
            exit_time REAL,
            exit_price REAL,
            exit_reason TEXT,
            pnl REAL,
            metadata TEXT
        )
        """
    )
    for row in rows:
        conn.execute(
            """INSERT INTO trades (trade_id, token_id, slug, entry_time,
                entry_price, entry_size, exit_time, exit_price, exit_reason,
                pnl, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["trade_id"], row.get("token_id", "0xtok"),
                row.get("slug", row["trade_id"]),
                row.get("entry_time", time.time() - 120),
                row.get("entry_price", 0.5),
                row.get("entry_size", 10.0),
                row.get("exit_time"),
                row.get("exit_price"),
                row.get("exit_reason"),
                row.get("pnl"),
                row.get("metadata"),
            ),
        )
    conn.commit()
    conn.close()


class TestV4ColumnsPresent:
    def test_fresh_db_has_all_v4_columns(self, tmp_path):
        db = PerformanceDB(db_path=_unique_db(tmp_path, "v4_fresh"))
        conn = sqlite3.connect(db.db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
        conn.close()
        missing = set(V4_COLUMNS) - cols
        assert not missing, f"v4 columns missing from fresh DB: {missing}"

    def test_legacy_db_has_all_v4_columns_after_migration(self, tmp_path):
        path = _unique_db(tmp_path, "v4_legacy")
        _seed_legacy_trades(path, [{"trade_id": "legacy-1"}])

        PerformanceDB(db_path=path)

        conn = sqlite3.connect(path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
        conn.close()
        missing = set(V4_COLUMNS) - cols
        assert not missing, f"v4 columns missing after legacy migration: {missing}"


class TestBackfills:
    def test_signal_source_backfilled_from_metadata_json(self, tmp_path):
        path = _unique_db(tmp_path, "v4_sigsrc")
        _seed_legacy_trades(path, [
            {"trade_id": "t-arb", "metadata": json.dumps({"source": "pair_arb"})},
            {"trade_id": "t-snipe", "metadata": json.dumps({"source": "resolution_snipe"})},
            {"trade_id": "t-null-meta", "metadata": None},
            {"trade_id": "t-no-source", "metadata": json.dumps({"other": "field"})},
        ])

        PerformanceDB(db_path=path)

        conn = sqlite3.connect(path)
        rows = dict(conn.execute(
            "SELECT trade_id, signal_source FROM trades"
        ).fetchall())
        conn.close()
        assert rows["t-arb"] == "pair_arb"
        assert rows["t-snipe"] == "resolution_snipe"
        assert rows["t-null-meta"] is None
        assert rows["t-no-source"] is None

    def test_fill_model_backfilled_to_unknown_legacy(self, tmp_path):
        path = _unique_db(tmp_path, "v4_fillmodel")
        _seed_legacy_trades(path, [
            {"trade_id": "legacy-a"},
            {"trade_id": "legacy-b"},
        ])

        PerformanceDB(db_path=path)

        conn = sqlite3.connect(path)
        values = [r[0] for r in conn.execute(
            "SELECT fill_model FROM trades ORDER BY trade_id"
        ).fetchall()]
        conn.close()
        assert values == ["unknown_legacy", "unknown_legacy"]

    def test_pnl_backfilled_only_for_open_trades(self, tmp_path):
        """Open NULL pnl -> 0.0; closed NULL pnl preserved for manual triage."""
        path = _unique_db(tmp_path, "v4_pnl")
        now = time.time()
        _seed_legacy_trades(path, [
            {"trade_id": "open-null", "exit_time": None, "pnl": None},
            {"trade_id": "closed-null", "exit_time": now, "exit_price": 0.4,
             "exit_reason": "market_resolved", "pnl": None},
            {"trade_id": "closed-real", "exit_time": now, "exit_price": 0.6,
             "exit_reason": "market_resolved", "pnl": 1.25},
        ])

        PerformanceDB(db_path=path)

        conn = sqlite3.connect(path)
        rows = dict(conn.execute("SELECT trade_id, pnl FROM trades").fetchall())
        conn.close()
        assert rows["open-null"] == 0.0, "open trade must backfill to 0.0"
        assert rows["closed-null"] is None, (
            "closed trade with NULL pnl must stay NULL (anomaly, not 0.0)"
        )
        assert rows["closed-real"] == 1.25, "real pnl must be preserved"


class TestIndexes:
    def test_all_v4_indexes_created(self, tmp_path):
        db = PerformanceDB(db_path=_unique_db(tmp_path, "v4_idx"))
        conn = sqlite3.connect(db.db_path)
        indexes = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='trades'"
        ).fetchall()}
        conn.close()
        missing = set(V4_INDEXES) - indexes
        assert not missing, f"v4 indexes missing: {missing}"


class TestIdempotent:
    def test_double_migration_safe(self, tmp_path):
        path = _unique_db(tmp_path, "v4_idem")
        _seed_legacy_trades(path, [
            {"trade_id": "t-1", "metadata": json.dumps({"source": "pair_arb"})},
        ])

        PerformanceDB(db_path=path)
        PerformanceDB(db_path=path)
        PerformanceDB(db_path=path)

        conn = sqlite3.connect(path)
        row = conn.execute(
            "SELECT signal_source, fill_model FROM trades WHERE trade_id='t-1'"
        ).fetchone()
        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='trades'"
        ).fetchall()}
        conn.close()
        assert row == ("pair_arb", "unknown_legacy")
        assert set(V4_INDEXES).issubset(indexes)
