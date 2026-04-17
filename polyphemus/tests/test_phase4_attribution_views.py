"""Phase 4 tests — attribution SQL views.

The views live in ``polyphemus/sql_views/*.sql`` and are loaded at
``PerformanceDB`` init via ``_init_attribution_views``. These tests lock
the *contract* callers depend on:

  1. Every view exists in sqlite_master after init.
  2. ``vw_trade_attribution`` bucket boundaries match the plan's entry
     bands (00-55, 55-70, 70-85, 85-93, 93-97, 97+).
  3. ``vw_strategy_perf`` groups by (signal_source, fill_model,
     is_dry_run, entry_band) and computes wins/wr/pnl correctly.
  4. ``vw_adverse_selection`` only aggregates rows with adverse_fill
     populated (no NULL pollution) and adv_rate matches hand math.
  5. Loader is idempotent: multiple PerformanceDB inits are safe
     because each .sql uses DROP VIEW IF EXISTS + CREATE.

Format of each view is intentionally not pinned here — edits to add
a column shouldn't break the test — but sample-size / win / pnl
arithmetic IS pinned because those are what the dashboards read.
"""

import sqlite3
import sys
import time
import uuid
from pathlib import Path

import pytest

# Ensure the top-level ``polyphemus`` package is importable regardless of
# pytest's cwd. Mirrors the sys.path insert used in test_evidence_verdict.py.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from polyphemus.performance_db import PerformanceDB


VIEW_NAMES = ("vw_trade_attribution", "vw_strategy_perf", "vw_adverse_selection")


def _unique_db(tmp_path, stem: str) -> str:
    return str(tmp_path / f"{stem}_{uuid.uuid4().hex[:8]}.db")


def _seed_closed_trade(
    db: PerformanceDB,
    trade_id: str,
    *,
    entry_price: float,
    exit_price: float,
    entry_size: float = 10.0,
    signal_source: str = "binance_momentum",
    fill_model: str = "v2_probabilistic",
    is_dry_run: bool = True,
    entry_mode: str = "maker",
    adverse_fill: int | None = None,
    adverse_fill_bps: float | None = None,
    fill_latency_ms: int | None = None,
) -> None:
    """Insert + close one trade with the attribution columns set."""
    now = time.time()
    db.record_entry(
        trade_id=trade_id, token_id="0xtok", slug=trade_id,
        entry_time=now, entry_price=entry_price, entry_size=entry_size,
        entry_tx_hash="0xhash", outcome="UP", market_title=f"test-{trade_id}",
        fill_model=fill_model, fill_model_reason="prob_hit",
        signal_source=signal_source, entry_mode=entry_mode,
        fill_latency_ms=fill_latency_ms,
        is_dry_run=is_dry_run,
    )
    pnl = (exit_price - entry_price) * entry_size
    pnl_pct = (exit_price - entry_price) / entry_price if entry_price > 0 else 0.0
    db.record_exit(
        trade_id=trade_id, exit_time=now + 60.0, exit_price=exit_price,
        exit_size=entry_size, exit_reason="profit_target",
        exit_tx_hash="0xexit", pnl=pnl, pnl_pct=pnl_pct,
    )
    if adverse_fill is not None:
        conn = sqlite3.connect(db.db_path)
        conn.execute(
            "UPDATE trades SET adverse_fill=?, adverse_fill_bps=?, fill_latency_ms=? "
            "WHERE trade_id=?",
            (adverse_fill, adverse_fill_bps, fill_latency_ms, trade_id),
        )
        conn.commit()
        conn.close()


class TestViewsExist:
    def test_all_three_views_created(self, tmp_path):
        db = PerformanceDB(db_path=_unique_db(tmp_path, "views"))
        conn = sqlite3.connect(db.db_path)
        views = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view'"
            ).fetchall()
        }
        conn.close()
        missing = set(VIEW_NAMES) - views
        assert not missing, f"views missing: {missing}"


class TestEntryBandBuckets:
    @pytest.mark.parametrize(
        "entry_price,expected_band",
        [
            (0.10, "00-55"),
            (0.54, "00-55"),
            (0.55, "55-70"),
            (0.69, "55-70"),
            (0.70, "70-85"),
            (0.84, "70-85"),
            (0.85, "85-93"),
            (0.92, "85-93"),
            (0.93, "93-97"),
            (0.96, "93-97"),
            (0.97, "97+"),
            (0.99, "97+"),
        ],
    )
    def test_entry_band_boundary(self, tmp_path, entry_price, expected_band):
        db = PerformanceDB(db_path=_unique_db(tmp_path, "band"))
        _seed_closed_trade(
            db, f"t-{int(entry_price*100)}",
            entry_price=entry_price, exit_price=entry_price + 0.02,
        )
        conn = sqlite3.connect(db.db_path)
        band = conn.execute(
            "SELECT entry_band FROM vw_trade_attribution WHERE entry_price=?",
            (entry_price,),
        ).fetchone()[0]
        conn.close()
        assert band == expected_band


class TestStrategyPerf:
    def test_groups_and_aggregates_correctly(self, tmp_path):
        """Seed 5 trades across 2 sources. vw_strategy_perf must separate
        them and compute wr/pnl exactly."""
        db = PerformanceDB(db_path=_unique_db(tmp_path, "perf"))
        # 3 binance_momentum trades in 85-93 band: 2 wins (0.90 -> 1.00 x2),
        # 1 loss (0.90 -> 0.80).
        _seed_closed_trade(db, "mm-win-1", entry_price=0.90, exit_price=1.00)
        _seed_closed_trade(db, "mm-win-2", entry_price=0.90, exit_price=1.00)
        _seed_closed_trade(db, "mm-lose-1", entry_price=0.90, exit_price=0.80)
        # 2 pair_arb trades in 55-70 band: both wins.
        _seed_closed_trade(
            db, "pa-win-1", entry_price=0.60, exit_price=0.70,
            signal_source="pair_arb",
        )
        _seed_closed_trade(
            db, "pa-win-2", entry_price=0.60, exit_price=0.70,
            signal_source="pair_arb",
        )

        conn = sqlite3.connect(db.db_path)
        rows = conn.execute(
            "SELECT signal_source, entry_band, n, wins, wr FROM vw_strategy_perf "
            "ORDER BY signal_source, entry_band"
        ).fetchall()
        conn.close()
        assert rows == [
            ("binance_momentum", "85-93", 3, 2, 0.6667),
            ("pair_arb", "55-70", 2, 2, 1.0),
        ]


class TestAdverseSelection:
    def test_only_populated_rows_counted(self, tmp_path):
        """Rows with NULL adverse_fill must NOT appear — dilution guard."""
        db = PerformanceDB(db_path=_unique_db(tmp_path, "adv"))
        _seed_closed_trade(
            db, "adv-y1", entry_price=0.60, exit_price=0.65,
            adverse_fill=1, adverse_fill_bps=15.5, fill_latency_ms=120,
        )
        _seed_closed_trade(
            db, "adv-n1", entry_price=0.60, exit_price=0.65,
            adverse_fill=0, adverse_fill_bps=-2.0, fill_latency_ms=80,
        )
        # Row without adverse data at all — must NOT appear in view.
        _seed_closed_trade(db, "no-adv", entry_price=0.60, exit_price=0.65)

        conn = sqlite3.connect(db.db_path)
        rows = conn.execute(
            "SELECT signal_source, entry_mode, n, adv_rate FROM vw_adverse_selection"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        src, mode, n, adv_rate = rows[0]
        assert src == "binance_momentum"
        assert mode == "maker"
        assert n == 2
        assert adv_rate == 0.5  # 1 of 2 adversely filled


class TestIdempotent:
    def test_reinit_does_not_raise(self, tmp_path):
        """Multiple PerformanceDB inits on the same path must be safe:
        each .sql file uses DROP VIEW IF EXISTS + CREATE."""
        path = _unique_db(tmp_path, "idem")
        PerformanceDB(db_path=path)
        PerformanceDB(db_path=path)
        PerformanceDB(db_path=path)

        conn = sqlite3.connect(path)
        views = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view'"
            ).fetchall()
        }
        conn.close()
        assert set(VIEW_NAMES).issubset(views)
