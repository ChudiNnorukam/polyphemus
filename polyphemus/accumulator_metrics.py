"""Accumulator Metrics — structured trade metrics for adaptive tuning.

Records every accumulator cycle (hedged, orphaned, unwound) to SQLite.
Exposes MetricsSnapshot for the adaptive tuner to make decisions.
"""

import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

from .config import setup_logger


EXIT_REASONS_HEDGED = frozenset({"hedged_settlement"})
EXIT_REASONS_ORPHANED = frozenset({"orphaned_settlement"})
EXIT_REASONS_SELLBACK = frozenset({"sellback"})
EXIT_REASONS_FORCED_HOLD = frozenset({
    "forced_hold_expired",
    "forced_hold_clob_unindexed",
    "forced_hold_sell_failed",
    "sellback_skipped_below_min",
})
EXIT_REASONS_NEUTRAL = frozenset({"empty_settlement", "unknown"})
EXIT_REASONS_CAPITAL_COMMITTED = (
    EXIT_REASONS_HEDGED
    | EXIT_REASONS_ORPHANED
    | EXIT_REASONS_SELLBACK
    | EXIT_REASONS_FORCED_HOLD
)


@dataclass
class CycleRecord:
    """One complete accumulator cycle (entry → settlement/unwind)."""
    slug: str
    started_at: float          # time.time()
    ended_at: float
    up_qty: float
    down_qty: float
    up_avg_price: float
    down_avg_price: float
    pair_cost: float
    pnl: float
    exit_reason: str           # hedged_settlement, orphaned_settlement, unwound, sellback
    reprices_used: int
    fill_time_secs: float      # time to fill first leg
    hedge_time_secs: float     # time to fill second leg (0 if orphaned)
    spread_at_entry: float     # up_bid + down_bid when scanning
    is_dry_run: bool = False   # True when cycle ran under DRY_RUN/ACCUM_DRY_RUN; required for live/dry segregation


@dataclass
class MetricsSnapshot:
    """Aggregated metrics over a time window.

    Counts: every row in the query window, split by exit category.
    Rates: use `capital_committed_cycles` (excludes empty/unknown) as denominator
    so idle scans do not dilute the signal the adaptive tuner acts on.
    """
    total_cycles: int              # raw row count; includes empty_settlement
    hedged_count: int              # hedged_settlement
    orphan_count: int              # orphaned_settlement (resolved orphan)
    unwind_count: int              # sellback (the standard unwind path)
    sellback_count: int            # alias of unwind_count for call-site clarity
    forced_hold_count: int         # forced_hold_* + sellback_skipped_below_min
    empty_count: int               # empty_settlement (no fills)
    capital_committed_cycles: int  # hedged + orphan + sellback + forced_hold
    hedge_rate: float              # hedged / capital_committed_cycles
    orphan_rate: float             # (orphan + sellback + forced_hold) / capital_committed_cycles
    avg_pair_cost: float           # mean pair_cost of hedged rows
    avg_pnl_per_hedged: float      # mean pnl of hedged rows
    avg_fill_time: float           # mean over all rows
    avg_reprices: float            # mean over all rows
    total_pnl: float               # sum over all rows
    orphan_loss_total: float       # sum pnl of every non-hedged non-neutral row


class AccumulatorMetrics:
    """SQLite-backed accumulator cycle metrics."""

    def __init__(self, db_path: str = "data/accum_metrics.db"):
        self._db_path = db_path
        self._logger = setup_logger("polyphemus.accum_metrics")
        self._init_db()

    def _init_db(self):
        import os
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.execute("""
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
                spread_at_entry REAL,
                is_dry_run INTEGER NOT NULL DEFAULT 0
            )
        """)
        self._migrate_is_dry_run(conn)
        conn.commit()
        conn.close()

    def _migrate_is_dry_run(self, conn: sqlite3.Connection) -> None:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(cycles)").fetchall()}
        if "is_dry_run" in cols:
            pre_existing = conn.execute(
                "SELECT COUNT(*) FROM cycles WHERE is_dry_run IS NULL"
            ).fetchone()[0]
            if pre_existing > 0:
                conn.execute("UPDATE cycles SET is_dry_run = 1 WHERE is_dry_run IS NULL")
                self._logger.warning(
                    f"Migration: backfilled {pre_existing} cycles to is_dry_run=1. "
                    "Historical cycles assumed dry-run; update manually if any live cycles pre-migration."
                )
            return
        try:
            conn.execute("ALTER TABLE cycles ADD COLUMN is_dry_run INTEGER NOT NULL DEFAULT 0")
            pre_existing = conn.execute("SELECT COUNT(*) FROM cycles").fetchone()[0]
            if pre_existing > 0:
                conn.execute("UPDATE cycles SET is_dry_run = 1")
                self._logger.warning(
                    f"Migration: added is_dry_run column and backfilled {pre_existing} cycles to 1. "
                    "Historical cycles assumed dry-run; update manually if any live cycles pre-migration."
                )
        except sqlite3.OperationalError as e:
            self._logger.error(f"is_dry_run migration failed: {e}")

    def record_cycle(self, cycle: CycleRecord):
        """Record a completed accumulator cycle."""
        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute(
                """INSERT INTO cycles
                   (slug, started_at, ended_at, up_qty, down_qty,
                    up_avg_price, down_avg_price, pair_cost, pnl,
                    exit_reason, reprices_used, fill_time_secs,
                    hedge_time_secs, spread_at_entry, is_dry_run)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cycle.slug, cycle.started_at, cycle.ended_at,
                    cycle.up_qty, cycle.down_qty,
                    cycle.up_avg_price, cycle.down_avg_price,
                    cycle.pair_cost, cycle.pnl, cycle.exit_reason,
                    cycle.reprices_used, cycle.fill_time_secs,
                    cycle.hedge_time_secs, cycle.spread_at_entry,
                    1 if cycle.is_dry_run else 0,
                ),
            )
            conn.commit()
            conn.close()
            tag = " [DRY]" if cycle.is_dry_run else ""
            self._logger.debug(f"Recorded cycle{tag}: {cycle.slug} | {cycle.exit_reason} | pnl=${cycle.pnl:.2f}")
        except Exception as e:
            self._logger.error(f"Failed to record cycle: {e}")

    def get_stats(
        self,
        window_mins: int = 60,
        dry_run_only: Optional[bool] = None,
    ) -> MetricsSnapshot:
        """Get aggregated metrics for the last N minutes.

        dry_run_only:
            None (default) — all rows
            True — only is_dry_run=1 rows
            False — only is_dry_run=0 rows (live)

        Rates (hedge_rate, orphan_rate) use capital_committed_cycles as the
        denominator. Before Phase 1.1, sellback and forced_hold rows were
        silently dropped before they reached this function; the Apr 10 bug
        was the result. Now that every terminal path writes a row, the
        denominator here is the full capital-committed count, and the rates
        reflect reality.
        """
        cutoff = time.time() - (window_mins * 60)
        query = "SELECT * FROM cycles WHERE ended_at > ?"
        params: tuple = (cutoff,)
        if dry_run_only is True:
            query += " AND is_dry_run = 1"
        elif dry_run_only is False:
            query += " AND is_dry_run = 0"
        query += " ORDER BY ended_at DESC"
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
            conn.close()
        except Exception as e:
            self._logger.error(f"Failed to query metrics: {e}")
            return self._empty_snapshot()

        if not rows:
            return self._empty_snapshot()

        total = len(rows)
        hedged_rows = [r for r in rows if r["exit_reason"] in EXIT_REASONS_HEDGED]
        orphaned_rows = [r for r in rows if r["exit_reason"] in EXIT_REASONS_ORPHANED]
        sellback_rows = [r for r in rows if r["exit_reason"] in EXIT_REASONS_SELLBACK]
        forced_hold_rows = [r for r in rows if r["exit_reason"] in EXIT_REASONS_FORCED_HOLD]
        empty_rows = [r for r in rows if r["exit_reason"] in EXIT_REASONS_NEUTRAL]

        capital_rows = [
            r for r in rows if r["exit_reason"] in EXIT_REASONS_CAPITAL_COMMITTED
        ]
        capital_total = len(capital_rows)
        orphaned_any_count = (
            len(orphaned_rows) + len(sellback_rows) + len(forced_hold_rows)
        )
        non_hedged_capital_rows = [
            r for r in capital_rows if r["exit_reason"] not in EXIT_REASONS_HEDGED
        ]

        return MetricsSnapshot(
            total_cycles=total,
            hedged_count=len(hedged_rows),
            orphan_count=len(orphaned_rows),
            unwind_count=len(sellback_rows),
            sellback_count=len(sellback_rows),
            forced_hold_count=len(forced_hold_rows),
            empty_count=len(empty_rows),
            capital_committed_cycles=capital_total,
            hedge_rate=len(hedged_rows) / capital_total if capital_total > 0 else 0.0,
            orphan_rate=orphaned_any_count / capital_total if capital_total > 0 else 0.0,
            avg_pair_cost=sum(r["pair_cost"] for r in hedged_rows) / len(hedged_rows) if hedged_rows else 0.0,
            avg_pnl_per_hedged=sum(r["pnl"] for r in hedged_rows) / len(hedged_rows) if hedged_rows else 0.0,
            avg_fill_time=sum(r["fill_time_secs"] for r in rows) / total,
            avg_reprices=sum(r["reprices_used"] for r in rows) / total,
            total_pnl=sum(r["pnl"] for r in rows),
            orphan_loss_total=sum(r["pnl"] for r in non_hedged_capital_rows),
        )

    def get_all_stats(
        self,
        dry_run_only: Optional[bool] = None,
    ) -> MetricsSnapshot:
        """Get all-time aggregated metrics."""
        return self.get_stats(window_mins=525600, dry_run_only=dry_run_only)

    def _empty_snapshot(self) -> MetricsSnapshot:
        return MetricsSnapshot(
            total_cycles=0, hedged_count=0, orphan_count=0, unwind_count=0,
            sellback_count=0, forced_hold_count=0, empty_count=0,
            capital_committed_cycles=0,
            hedge_rate=0.0, orphan_rate=0.0, avg_pair_cost=0.0,
            avg_pnl_per_hedged=0.0, avg_fill_time=0.0, avg_reprices=0.0,
            total_pnl=0.0, orphan_loss_total=0.0,
        )
