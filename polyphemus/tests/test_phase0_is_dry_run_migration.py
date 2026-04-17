"""Phase 0 migration smoke tests for is_dry_run column.

Covers accum_metrics cycles table and performance.db trades table. Each
migration must:
  - add column when missing
  - backfill pre-existing rows to 1 (historical data is dry-run)
  - be idempotent (rerunning does not corrupt data)
  - default new rows to 0 when caller omits the flag (fail-closed)

Apr 10 2026 precedent: accum_metrics.db excluded sellbacks and reported +$39
while live P&L was -$85. Silent column-less aggregation is the failure mode
this phase blocks.
"""

import sqlite3
import time
import uuid

import pytest

from polyphemus.accumulator_metrics import AccumulatorMetrics, CycleRecord
from polyphemus.performance_db import PerformanceDB


def _unique_db(tmp_path, stem):
    return str(tmp_path / f"{stem}_{uuid.uuid4().hex[:8]}.db")


def _seed_legacy_cycles_table(db_path: str, row_count: int) -> None:
    """Create cycles table WITHOUT is_dry_run, insert legacy rows, return."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cycles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL,
            started_at REAL NOT NULL,
            ended_at REAL NOT NULL,
            up_qty REAL,
            down_qty REAL,
            up_avg_price REAL,
            down_avg_price REAL,
            pair_cost REAL,
            pnl REAL,
            exit_reason TEXT,
            reprices_used INTEGER,
            fill_time_secs REAL,
            hedge_time_secs REAL,
            spread_at_entry REAL
        )
        """
    )
    now = time.time()
    for i in range(row_count):
        conn.execute(
            """INSERT INTO cycles (slug, started_at, ended_at, up_qty, down_qty,
                up_avg_price, down_avg_price, pair_cost, pnl, exit_reason,
                reprices_used, fill_time_secs, hedge_time_secs, spread_at_entry)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"legacy-{i}", now - 60, now, 10.0, 10.0, 0.5, 0.5, 5.0, 0.0,
             "hedged_settlement", 0, 1.0, 1.0, 0.0),
        )
    conn.commit()
    conn.close()


def _seed_legacy_trades_table(db_path: str, row_count: int) -> None:
    """Create trades table WITHOUT is_dry_run using the V1 schema."""
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
            pnl REAL
        )
        """
    )
    now = time.time()
    for i in range(row_count):
        conn.execute(
            """INSERT INTO trades (trade_id, token_id, slug, entry_time, exit_time,
                entry_price, exit_price, entry_size, pnl, exit_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"tid-{i}", f"0xt{i}", f"legacy-{i}", now - 120, now, 0.50, 0.55,
             10.0, 0.50, "market_resolved"),
        )
    conn.commit()
    conn.close()


class TestAccumMetricsMigration:
    def test_fresh_db_has_column_and_defaults_to_zero(self, tmp_path):
        path = _unique_db(tmp_path, "accum_fresh")
        AccumulatorMetrics(db_path=path)
        conn = sqlite3.connect(path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(cycles)").fetchall()}
        conn.close()
        assert "is_dry_run" in cols

    def test_legacy_rows_backfilled_to_one(self, tmp_path):
        path = _unique_db(tmp_path, "accum_legacy")
        _seed_legacy_cycles_table(path, row_count=5)

        AccumulatorMetrics(db_path=path)

        conn = sqlite3.connect(path)
        values = [r[0] for r in conn.execute("SELECT is_dry_run FROM cycles").fetchall()]
        conn.close()
        assert len(values) == 5
        assert all(v == 1 for v in values), \
            f"legacy rows must backfill to 1, got {values}"

    def test_migration_idempotent(self, tmp_path):
        path = _unique_db(tmp_path, "accum_idem")
        _seed_legacy_cycles_table(path, row_count=3)

        AccumulatorMetrics(db_path=path)
        AccumulatorMetrics(db_path=path)

        conn = sqlite3.connect(path)
        rows = conn.execute(
            "SELECT COUNT(*), SUM(is_dry_run) FROM cycles"
        ).fetchone()
        conn.close()
        assert rows[0] == 3
        assert rows[1] == 3

    def test_record_cycle_persists_dry_run_flag(self, tmp_path):
        path = _unique_db(tmp_path, "accum_record")
        metrics = AccumulatorMetrics(db_path=path)
        now = time.time()

        dry_cycle = CycleRecord(
            slug="dry-1", started_at=now - 30, ended_at=now,
            up_qty=10.0, down_qty=10.0, up_avg_price=0.5, down_avg_price=0.5,
            pair_cost=5.0, pnl=0.2, exit_reason="hedged_settlement",
            reprices_used=0, fill_time_secs=1.0, hedge_time_secs=1.0,
            spread_at_entry=0.0, is_dry_run=True,
        )
        live_cycle = CycleRecord(
            slug="live-1", started_at=now - 30, ended_at=now,
            up_qty=10.0, down_qty=10.0, up_avg_price=0.5, down_avg_price=0.5,
            pair_cost=5.0, pnl=0.3, exit_reason="hedged_settlement",
            reprices_used=0, fill_time_secs=1.0, hedge_time_secs=1.0,
            spread_at_entry=0.0, is_dry_run=False,
        )
        metrics.record_cycle(dry_cycle)
        metrics.record_cycle(live_cycle)

        conn = sqlite3.connect(path)
        rows = dict(conn.execute(
            "SELECT slug, is_dry_run FROM cycles ORDER BY slug"
        ).fetchall())
        conn.close()
        assert rows == {"dry-1": 1, "live-1": 0}

    def test_cycle_record_default_is_dry_run_false(self):
        """Default CycleRecord must NOT claim dry-run — fail-closed."""
        cycle = CycleRecord(
            slug="x", started_at=0, ended_at=1,
            up_qty=0, down_qty=0, up_avg_price=0, down_avg_price=0,
            pair_cost=0, pnl=0, exit_reason="x",
            reprices_used=0, fill_time_secs=0, hedge_time_secs=0,
            spread_at_entry=0,
        )
        assert cycle.is_dry_run is False


class TestPerformanceDBMigration:
    def test_fresh_db_has_column(self, tmp_path):
        path = _unique_db(tmp_path, "perf_fresh")
        PerformanceDB(db_path=path)
        conn = sqlite3.connect(path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
        conn.close()
        assert "is_dry_run" in cols

    def test_legacy_trades_backfilled_to_one(self, tmp_path):
        path = _unique_db(tmp_path, "perf_legacy")
        _seed_legacy_trades_table(path, row_count=4)

        PerformanceDB(db_path=path)

        conn = sqlite3.connect(path)
        values = [r[0] for r in conn.execute("SELECT is_dry_run FROM trades").fetchall()]
        conn.close()
        assert len(values) == 4
        assert all(v == 1 for v in values), \
            f"legacy trades must backfill to 1, got {values}"

    def test_migration_idempotent(self, tmp_path):
        path = _unique_db(tmp_path, "perf_idem")
        _seed_legacy_trades_table(path, row_count=2)

        PerformanceDB(db_path=path)
        PerformanceDB(db_path=path)

        conn = sqlite3.connect(path)
        rows = conn.execute(
            "SELECT COUNT(*), SUM(is_dry_run) FROM trades"
        ).fetchone()
        conn.close()
        assert rows[0] == 2
        assert rows[1] == 2

    def test_get_stats_dry_run_only_filter(self, tmp_path):
        path = _unique_db(tmp_path, "perf_stats")
        db = PerformanceDB(db_path=path)
        now = time.time()

        conn = sqlite3.connect(path)
        for slug, pnl, flag in [
            ("dry-win", 1.0, 1), ("dry-loss", -0.5, 1),
            ("live-win", 2.0, 0), ("live-loss", -1.0, 0),
        ]:
            conn.execute(
                """INSERT INTO trades (trade_id, token_id, slug, entry_time, exit_time,
                    entry_price, exit_price, entry_size, pnl, exit_reason, is_dry_run)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (f"tid-{slug}", f"0x{slug}", slug, now - 60, now, 0.5, 0.55,
                 10.0, pnl, "market_resolved", flag),
            )
        conn.commit()
        conn.close()

        all_stats = db.get_stats()
        dry_stats = db.get_stats(dry_run_only=True)
        live_stats = db.get_stats(dry_run_only=False)

        assert all_stats["total_trades"] == 4
        assert dry_stats["total_trades"] == 2
        assert live_stats["total_trades"] == 2
        assert dry_stats["total_profit_loss"] == pytest.approx(0.5)
        assert live_stats["total_profit_loss"] == pytest.approx(1.0)
        assert dry_stats["resolution_wins"] == 1
        assert live_stats["resolution_wins"] == 1
