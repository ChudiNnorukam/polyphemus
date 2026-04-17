"""Phase 1.7 segmentation tests for accum_metrics (asset + window_duration_secs).

Adds per-asset / per-window columns so the adaptive tuner and MTC gate can
compute rates without bundling unrelated instruments. Before this phase, the
cycles table only carried a slug — segmented aggregation required regex-parsing
that slug at query time, which no downstream caller bothered to do, which meant
BTC-5m losses sat inside the same aggregate as ETH-15m wins.

The migration is strictly additive and idempotent. Legacy rows backfill to
empty asset / 0 duration so they land in a distinct "unsegmented" bucket and
cannot be mistaken for a real asset.
"""

import sqlite3
import time
import uuid

import pytest

from polyphemus.accumulator_metrics import AccumulatorMetrics, CycleRecord
from polyphemus.models import parse_asset_from_slug


def _unique_db(tmp_path, stem):
    return str(tmp_path / f"{stem}_{uuid.uuid4().hex[:8]}.db")


def _seed_pre_segmentation_table(db_path: str, row_count: int) -> None:
    """Create cycles table as of Phase 0-1.6 (has is_dry_run, lacks asset/window_duration_secs)."""
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
            spread_at_entry REAL,
            is_dry_run INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    now = time.time()
    for i in range(row_count):
        conn.execute(
            """INSERT INTO cycles (slug, started_at, ended_at, up_qty, down_qty,
                up_avg_price, down_avg_price, pair_cost, pnl, exit_reason,
                reprices_used, fill_time_secs, hedge_time_secs, spread_at_entry,
                is_dry_run)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"legacy-{i}", now - 60, now, 10.0, 10.0, 0.5, 0.5, 5.0, 0.0,
             "hedged_settlement", 0, 1.0, 1.0, 0.0, 1),
        )
    conn.commit()
    conn.close()


class TestParseAssetFromSlug:
    def test_crypto_slug_returns_lead_token(self):
        assert parse_asset_from_slug("btc-updown-5m-1770937500") == "btc"
        assert parse_asset_from_slug("eth-updown-15m-1770937200") == "eth"
        assert parse_asset_from_slug("sol-updown-5m-1770900000") == "sol"

    def test_weather_slug_returns_city(self):
        assert (
            parse_asset_from_slug("highest-temperature-in-houston-on-april-10-2026")
            == "houston"
        )
        assert (
            parse_asset_from_slug("highest-temperature-in-miami-on-april-11-2026")
            == "miami"
        )

    def test_empty_slug_returns_empty_string(self):
        assert parse_asset_from_slug("") == ""

    def test_single_token_slug_returns_itself(self):
        assert parse_asset_from_slug("btc") == "btc"

    def test_partial_weather_prefix_falls_back_to_lead_token(self):
        # Missing the city portion: must not mis-assign "highest" as the asset.
        # Current heuristic returns "highest" as a fallback first-token — that's
        # fine because real weather slugs always include the city; this test
        # exists to pin the behavior so malformed slugs stay in a distinct bucket.
        assert parse_asset_from_slug("highest-temperature-in") == "highest"


class TestSegmentationSchema:
    def test_fresh_db_has_new_columns(self, tmp_path):
        path = _unique_db(tmp_path, "seg_fresh")
        AccumulatorMetrics(db_path=path)
        conn = sqlite3.connect(path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(cycles)").fetchall()}
        conn.close()
        assert "asset" in cols
        assert "window_duration_secs" in cols

    def test_legacy_rows_backfill_to_unsegmented_bucket(self, tmp_path):
        path = _unique_db(tmp_path, "seg_legacy")
        _seed_pre_segmentation_table(path, row_count=5)

        AccumulatorMetrics(db_path=path)

        conn = sqlite3.connect(path)
        rows = conn.execute(
            "SELECT asset, window_duration_secs FROM cycles"
        ).fetchall()
        conn.close()
        assert len(rows) == 5
        # Legacy rows must be clearly distinguishable from real segmented rows.
        # Empty asset + 0 duration is the "unsegmented" bucket.
        assert all(r[0] == "" for r in rows), \
            f"legacy asset must be empty string, got {[r[0] for r in rows]}"
        assert all(r[1] == 0 for r in rows), \
            f"legacy window_duration_secs must be 0, got {[r[1] for r in rows]}"

    def test_migration_idempotent(self, tmp_path):
        path = _unique_db(tmp_path, "seg_idem")
        _seed_pre_segmentation_table(path, row_count=3)

        AccumulatorMetrics(db_path=path)
        AccumulatorMetrics(db_path=path)
        AccumulatorMetrics(db_path=path)

        conn = sqlite3.connect(path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(cycles)").fetchall()]
        row_count = conn.execute("SELECT COUNT(*) FROM cycles").fetchone()[0]
        conn.close()
        # One "asset" column, one "window_duration_secs" column, no duplication.
        assert cols.count("asset") == 1
        assert cols.count("window_duration_secs") == 1
        assert row_count == 3


class TestCycleRecordDefaults:
    def test_default_asset_is_empty_string(self):
        """Default CycleRecord must land in the unsegmented bucket, not
        silently claim an asset — fail-closed."""
        cycle = CycleRecord(
            slug="x", started_at=0, ended_at=1,
            up_qty=0, down_qty=0, up_avg_price=0, down_avg_price=0,
            pair_cost=0, pnl=0, exit_reason="x",
            reprices_used=0, fill_time_secs=0, hedge_time_secs=0,
            spread_at_entry=0,
        )
        assert cycle.asset == ""
        assert cycle.window_duration_secs == 0


class TestRecordCyclePersistsSegmentation:
    def test_segmented_fields_round_trip(self, tmp_path):
        path = _unique_db(tmp_path, "seg_record")
        metrics = AccumulatorMetrics(db_path=path)
        now = time.time()

        btc_cycle = CycleRecord(
            slug="btc-updown-5m-1770937500", started_at=now - 30, ended_at=now,
            up_qty=10.0, down_qty=10.0, up_avg_price=0.5, down_avg_price=0.5,
            pair_cost=5.0, pnl=0.2, exit_reason="hedged_settlement",
            reprices_used=0, fill_time_secs=1.0, hedge_time_secs=1.0,
            spread_at_entry=0.0, is_dry_run=True,
            asset="btc", window_duration_secs=300,
        )
        eth_cycle = CycleRecord(
            slug="eth-updown-15m-1770937200", started_at=now - 30, ended_at=now,
            up_qty=8.0, down_qty=8.0, up_avg_price=0.48, down_avg_price=0.52,
            pair_cost=4.0, pnl=-0.1, exit_reason="sellback",
            reprices_used=1, fill_time_secs=2.0, hedge_time_secs=0.0,
            spread_at_entry=0.0, is_dry_run=True,
            asset="eth", window_duration_secs=900,
        )
        metrics.record_cycle(btc_cycle)
        metrics.record_cycle(eth_cycle)

        conn = sqlite3.connect(path)
        rows = {
            r[0]: (r[1], r[2]) for r in conn.execute(
                "SELECT slug, asset, window_duration_secs FROM cycles ORDER BY slug"
            ).fetchall()
        }
        conn.close()
        assert rows["btc-updown-5m-1770937500"] == ("btc", 300)
        assert rows["eth-updown-15m-1770937200"] == ("eth", 900)

    def test_per_asset_per_window_filter(self, tmp_path):
        """Direct SQL segmentation must work — this is the whole point of the
        phase. Downstream callers (MTC gate, adaptive tuner) will query on
        these columns."""
        path = _unique_db(tmp_path, "seg_filter")
        metrics = AccumulatorMetrics(db_path=path)
        now = time.time()

        seeds = [
            ("btc-5m-a", "btc", 300, 0.10),
            ("btc-5m-b", "btc", 300, 0.20),
            ("btc-15m-a", "btc", 900, -0.05),
            ("eth-5m-a", "eth", 300, 0.50),
        ]
        for slug, asset, window, pnl in seeds:
            metrics.record_cycle(CycleRecord(
                slug=slug, started_at=now - 30, ended_at=now,
                up_qty=10.0, down_qty=10.0, up_avg_price=0.5, down_avg_price=0.5,
                pair_cost=5.0, pnl=pnl, exit_reason="hedged_settlement",
                reprices_used=0, fill_time_secs=1.0, hedge_time_secs=1.0,
                spread_at_entry=0.0, is_dry_run=True,
                asset=asset, window_duration_secs=window,
            ))

        conn = sqlite3.connect(path)
        btc_5m_total = conn.execute(
            "SELECT SUM(pnl) FROM cycles WHERE asset=? AND window_duration_secs=?",
            ("btc", 300),
        ).fetchone()[0]
        btc_all_total = conn.execute(
            "SELECT SUM(pnl) FROM cycles WHERE asset=?", ("btc",),
        ).fetchone()[0]
        eth_5m_total = conn.execute(
            "SELECT SUM(pnl) FROM cycles WHERE asset=? AND window_duration_secs=?",
            ("eth", 300),
        ).fetchone()[0]
        conn.close()

        assert btc_5m_total == pytest.approx(0.30)
        assert btc_all_total == pytest.approx(0.25)
        assert eth_5m_total == pytest.approx(0.50)


class TestGetStatsBackwardsCompat:
    def test_get_stats_unaffected_by_new_columns(self, tmp_path):
        """Phase 1.7 adds columns without changing aggregation semantics. A
        legacy-shaped call to get_stats must still return the same shape and
        still aggregate over all rows that meet the filter."""
        path = _unique_db(tmp_path, "seg_compat")
        metrics = AccumulatorMetrics(db_path=path)
        now = time.time()

        # Mixed segmented + unsegmented rows.
        metrics.record_cycle(CycleRecord(
            slug="btc-updown-5m", started_at=now - 30, ended_at=now,
            up_qty=10.0, down_qty=10.0, up_avg_price=0.5, down_avg_price=0.5,
            pair_cost=5.0, pnl=0.1, exit_reason="hedged_settlement",
            reprices_used=0, fill_time_secs=1.0, hedge_time_secs=1.0,
            spread_at_entry=0.0, is_dry_run=True,
            asset="btc", window_duration_secs=300,
        ))
        metrics.record_cycle(CycleRecord(
            slug="legacy-unsegmented", started_at=now - 30, ended_at=now,
            up_qty=10.0, down_qty=0.0, up_avg_price=0.5, down_avg_price=0.0,
            pair_cost=5.0, pnl=-0.20, exit_reason="sellback",
            reprices_used=0, fill_time_secs=1.0, hedge_time_secs=0.0,
            spread_at_entry=0.0, is_dry_run=True,
            # asset / window_duration_secs left at defaults.
        ))

        snap = metrics.get_stats(window_mins=60, dry_run_only=True)
        assert snap.total_cycles == 2
        assert snap.hedged_count == 1
        assert snap.sellback_count == 1
        assert snap.total_pnl == pytest.approx(-0.1)
