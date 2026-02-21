"""Accumulator Metrics — structured trade metrics for adaptive tuning.

Records every accumulator cycle (hedged, orphaned, unwound) to SQLite.
Exposes MetricsSnapshot for the adaptive tuner to make decisions.
"""

import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

from .config import setup_logger


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
    exit_reason: str           # hedged_settlement, orphaned_settlement, unwound
    reprices_used: int
    fill_time_secs: float      # time to fill first leg
    hedge_time_secs: float     # time to fill second leg (0 if orphaned)
    spread_at_entry: float     # up_bid + down_bid when scanning


@dataclass
class MetricsSnapshot:
    """Aggregated metrics over a time window."""
    total_cycles: int
    hedged_count: int
    orphan_count: int
    unwind_count: int
    hedge_rate: float          # hedged / total (0-1)
    orphan_rate: float         # (orphan + unwind) / total
    avg_pair_cost: float
    avg_pnl_per_hedged: float
    avg_fill_time: float
    avg_reprices: float
    total_pnl: float
    orphan_loss_total: float


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
                spread_at_entry REAL
            )
        """)
        conn.commit()
        conn.close()

    def record_cycle(self, cycle: CycleRecord):
        """Record a completed accumulator cycle."""
        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute(
                """INSERT INTO cycles
                   (slug, started_at, ended_at, up_qty, down_qty,
                    up_avg_price, down_avg_price, pair_cost, pnl,
                    exit_reason, reprices_used, fill_time_secs,
                    hedge_time_secs, spread_at_entry)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cycle.slug, cycle.started_at, cycle.ended_at,
                    cycle.up_qty, cycle.down_qty,
                    cycle.up_avg_price, cycle.down_avg_price,
                    cycle.pair_cost, cycle.pnl, cycle.exit_reason,
                    cycle.reprices_used, cycle.fill_time_secs,
                    cycle.hedge_time_secs, cycle.spread_at_entry,
                ),
            )
            conn.commit()
            conn.close()
            self._logger.debug(f"Recorded cycle: {cycle.slug} | {cycle.exit_reason} | pnl=${cycle.pnl:.2f}")
        except Exception as e:
            self._logger.error(f"Failed to record cycle: {e}")

    def get_stats(self, window_mins: int = 60) -> MetricsSnapshot:
        """Get aggregated metrics for the last N minutes."""
        cutoff = time.time() - (window_mins * 60)
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM cycles WHERE ended_at > ? ORDER BY ended_at DESC",
                (cutoff,),
            ).fetchall()
            conn.close()
        except Exception as e:
            self._logger.error(f"Failed to query metrics: {e}")
            return self._empty_snapshot()

        if not rows:
            return self._empty_snapshot()

        total = len(rows)
        hedged = sum(1 for r in rows if r["exit_reason"] == "hedged_settlement")
        orphaned = sum(1 for r in rows if r["exit_reason"] == "orphaned_settlement")
        unwound = sum(1 for r in rows if r["exit_reason"] == "unwound")

        hedged_rows = [r for r in rows if r["exit_reason"] == "hedged_settlement"]
        loss_rows = [r for r in rows if r["exit_reason"] in ("orphaned_settlement", "unwound")]

        return MetricsSnapshot(
            total_cycles=total,
            hedged_count=hedged,
            orphan_count=orphaned,
            unwind_count=unwound,
            hedge_rate=hedged / total if total > 0 else 0.0,
            orphan_rate=(orphaned + unwound) / total if total > 0 else 0.0,
            avg_pair_cost=sum(r["pair_cost"] for r in hedged_rows) / len(hedged_rows) if hedged_rows else 0.0,
            avg_pnl_per_hedged=sum(r["pnl"] for r in hedged_rows) / len(hedged_rows) if hedged_rows else 0.0,
            avg_fill_time=sum(r["fill_time_secs"] for r in rows) / total,
            avg_reprices=sum(r["reprices_used"] for r in rows) / total,
            total_pnl=sum(r["pnl"] for r in rows),
            orphan_loss_total=sum(r["pnl"] for r in loss_rows),
        )

    def get_all_stats(self) -> MetricsSnapshot:
        """Get all-time aggregated metrics."""
        return self.get_stats(window_mins=525600)  # 1 year

    def _empty_snapshot(self) -> MetricsSnapshot:
        return MetricsSnapshot(
            total_cycles=0, hedged_count=0, orphan_count=0, unwind_count=0,
            hedge_rate=0.0, orphan_rate=0.0, avg_pair_cost=0.0,
            avg_pnl_per_hedged=0.0, avg_fill_time=0.0, avg_reprices=0.0,
            total_pnl=0.0, orphan_loss_total=0.0,
        )
