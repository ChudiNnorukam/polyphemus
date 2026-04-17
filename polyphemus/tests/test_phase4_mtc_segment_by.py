"""Phase 4 tests — MTC pre-deploy gate ``--segment-by`` / ``--filter-*``.

These tests pin the *contract* that the gate exposes for attribution slicing,
not the statistical internals. We already have 23 tests covering R1-R5; here
we only need to know that:

  1. Pure helpers (``_derive_entry_band``, ``_segment_value``,
     ``_apply_trade_filters``) behave per the docstring.
  2. ``run_gate`` with ``filter_*`` kwargs narrows the row set and records
     the active filters inside the segment dict (so receipts are
     self-describing).
  3. ``run_segmented_gate`` partitions rows by the requested column and
     returns one verdict per partition, plus an envelope verdict that is
     PASS iff every partition is PASS.
  4. Empty-after-filter collapses to an explicit FAIL (no silent pass).
  5. CLI entrypoints accept the new flags, validate trades-only usage,
     and emit segmented JSON when asked.

We seed small data on purpose — the gate will mostly FAIL on R1 sample size.
That's fine; we assert on structure and on which partitions exist, never
on a PASS verdict.
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
from polyphemus.tools import mtc_pre_deploy_gate as gate


def _unique_db(tmp_path, stem: str) -> str:
    return str(tmp_path / f"{stem}_{uuid.uuid4().hex[:8]}.db")


def _seed(
    db: PerformanceDB,
    trade_id: str,
    *,
    entry_price: float,
    exit_price: float,
    signal_source: str = "binance_momentum",
    fill_model: str = "v2_probabilistic",
    strategy: str = "signal_bot",
    entry_size: float = 10.0,
    exit_ts_offset: float = 60.0,
) -> None:
    """Seed one dry-run closed trade with the attribution columns set."""
    now = time.time()
    db.record_entry(
        trade_id=trade_id, token_id="0xtok", slug=trade_id,
        entry_time=now, entry_price=entry_price, entry_size=entry_size,
        entry_tx_hash="0xhash", outcome="UP", market_title=f"m-{trade_id}",
        strategy=strategy,
        fill_model=fill_model, signal_source=signal_source,
        entry_mode="maker", is_dry_run=True,
    )
    pnl = (exit_price - entry_price) * entry_size
    pnl_pct = (exit_price - entry_price) / entry_price if entry_price > 0 else 0.0
    db.record_exit(
        trade_id=trade_id, exit_time=now + exit_ts_offset, exit_price=exit_price,
        exit_size=entry_size, exit_reason="profit_target",
        exit_tx_hash="0xexit", pnl=pnl, pnl_pct=pnl_pct,
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestDeriveEntryBand:
    @pytest.mark.parametrize(
        "price,expected",
        [
            (0.10, "00-55"),
            (0.549, "00-55"),
            (0.55, "55-70"),
            (0.699, "55-70"),
            (0.70, "70-85"),
            (0.849, "70-85"),
            (0.85, "85-93"),
            (0.929, "85-93"),
            (0.93, "93-97"),
            (0.969, "93-97"),
            (0.97, "97+"),
            (0.999, "97+"),
        ],
    )
    def test_band_boundary_matches_sql_view(self, price, expected):
        """Python bands must match the CASE in vw_trade_attribution.sql."""
        assert gate._derive_entry_band(price) == expected

    def test_none_bands_to_unknown(self):
        assert gate._derive_entry_band(None) == "unknown"


class TestSegmentValue:
    def test_known_column_returns_value(self):
        row = {"signal_source": "pair_arb", "fill_model": "v2_probabilistic"}
        assert gate._segment_value(row, "signal_source") == "pair_arb"
        assert gate._segment_value(row, "fill_model") == "v2_probabilistic"

    def test_null_value_collapses_to_unknown(self):
        assert gate._segment_value({"signal_source": None}, "signal_source") == "unknown"
        assert gate._segment_value({"signal_source": ""}, "signal_source") == "unknown"
        assert gate._segment_value({}, "fill_model") == "unknown"

    def test_entry_band_is_derived_not_stored(self):
        row = {"entry_price": 0.92}
        assert gate._segment_value(row, "entry_band") == "85-93"


class TestApplyTradeFilters:
    def _rows(self):
        return [
            {"signal_source": "binance_momentum", "fill_model": "v2_probabilistic",
             "entry_price": 0.90},
            {"signal_source": "pair_arb", "fill_model": "v2_probabilistic",
             "entry_price": 0.60},
            {"signal_source": "binance_momentum", "fill_model": "v1_taker",
             "entry_price": 0.60},
        ]

    def test_empty_filters_keep_all(self):
        rows = self._rows()
        assert gate._apply_trade_filters(rows) == rows

    def test_signal_source_filter(self):
        kept = gate._apply_trade_filters(
            self._rows(), filter_signal_source="pair_arb"
        )
        assert len(kept) == 1
        assert kept[0]["signal_source"] == "pair_arb"

    def test_fill_model_filter(self):
        kept = gate._apply_trade_filters(
            self._rows(), filter_fill_model="v1_taker"
        )
        assert len(kept) == 1
        assert kept[0]["fill_model"] == "v1_taker"

    def test_entry_band_filter_uses_derived_bucket(self):
        kept = gate._apply_trade_filters(
            self._rows(), filter_entry_band="85-93"
        )
        assert len(kept) == 1
        assert kept[0]["entry_price"] == 0.90

    def test_filters_compose_as_intersection(self):
        kept = gate._apply_trade_filters(
            self._rows(),
            filter_signal_source="binance_momentum",
            filter_fill_model="v2_probabilistic",
        )
        assert len(kept) == 1
        assert kept[0]["entry_price"] == 0.90


# ---------------------------------------------------------------------------
# run_gate with attribution filters
# ---------------------------------------------------------------------------


class TestRunGateWithFilters:
    def test_filter_narrows_rows_and_is_surfaced_in_segment(self, tmp_path):
        """A signal_source filter must actually exclude the other source
        AND the segment dict in the verdict must record the active filter
        so receipts are self-describing."""
        db = PerformanceDB(db_path=_unique_db(tmp_path, "filt"))
        for i in range(5):
            _seed(db, f"mm-{i}", entry_price=0.60, exit_price=0.65,
                  signal_source="binance_momentum")
            _seed(db, f"pa-{i}", entry_price=0.60, exit_price=0.62,
                  signal_source="pair_arb")

        verdict = gate.run_gate(
            source="trades", db_path=db.db_path,
            lookback_days=30, strategy="signal_bot",
            filter_signal_source="pair_arb",
            now=time.time(),
        )
        # 5 pair_arb rows, 5 momentum rows filtered out.
        assert verdict["n"] == 5
        assert verdict["segment"]["signal_source"] == "pair_arb"
        assert verdict["segment"]["strategy"] == "signal_bot"

    def test_filter_entry_band_by_derived_bucket(self, tmp_path):
        db = PerformanceDB(db_path=_unique_db(tmp_path, "band"))
        _seed(db, "low", entry_price=0.30, exit_price=0.35)  # 00-55
        _seed(db, "mid", entry_price=0.60, exit_price=0.65)  # 55-70
        _seed(db, "high", entry_price=0.95, exit_price=0.97)  # 93-97

        verdict = gate.run_gate(
            source="trades", db_path=db.db_path,
            lookback_days=30, strategy="signal_bot",
            filter_entry_band="55-70",
            now=time.time(),
        )
        assert verdict["n"] == 1
        assert verdict["segment"]["entry_band"] == "55-70"

    def test_cycles_source_ignores_trades_filters(self, tmp_path):
        """Cycles loader doesn't carry signal_source; passing the filter
        is a no-op (not an error) so callers can share CLI wrappers."""
        # Build a minimal cycles DB so the loader doesn't blow up.
        path = str(tmp_path / "cycles.db")
        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE cycles (
                id INTEGER PRIMARY KEY,
                asset TEXT, window_duration_secs INTEGER,
                is_dry_run INTEGER, pnl REAL, pair_cost REAL,
                ended_at REAL, exit_reason TEXT
            )
        """)
        conn.commit()
        conn.close()

        verdict = gate.run_gate(
            source="cycles", db_path=path,
            asset="btc", window_duration_secs=300,
            lookback_days=30,
            filter_signal_source="pair_arb",  # ignored for cycles
            now=time.time(),
        )
        # Zero rows, sample-size fails — but no exception, and segment
        # shape is unchanged.
        assert verdict["n"] == 0
        assert "asset" in verdict["segment"]
        assert "signal_source" not in verdict["segment"]


# ---------------------------------------------------------------------------
# run_segmented_gate
# ---------------------------------------------------------------------------


class TestRunSegmentedGate:
    def test_partitions_by_signal_source(self, tmp_path):
        db = PerformanceDB(db_path=_unique_db(tmp_path, "seg"))
        for i in range(4):
            _seed(db, f"mm-{i}", entry_price=0.60, exit_price=0.65,
                  signal_source="binance_momentum")
        for i in range(3):
            _seed(db, f"pa-{i}", entry_price=0.60, exit_price=0.62,
                  signal_source="pair_arb")

        envelope = gate.run_segmented_gate(
            db_path=db.db_path, strategy="signal_bot",
            lookback_days=30, segment_by="signal_source",
            now=time.time(),
        )
        assert envelope["source"] == "trades"
        assert envelope["segmented_by"] == "signal_source"
        assert set(envelope["segments"].keys()) == {"binance_momentum", "pair_arb"}
        assert envelope["segments"]["binance_momentum"]["n"] == 4
        assert envelope["segments"]["pair_arb"]["n"] == 3
        # Per-partition segment meta should include the label so receipts
        # written from segmented runs are unambiguous.
        assert envelope["segments"]["pair_arb"]["segment"]["signal_source"] == "pair_arb"

    def test_partitions_by_entry_band(self, tmp_path):
        db = PerformanceDB(db_path=_unique_db(tmp_path, "band"))
        _seed(db, "a", entry_price=0.30, exit_price=0.35)  # 00-55
        _seed(db, "b", entry_price=0.60, exit_price=0.65)  # 55-70
        _seed(db, "c", entry_price=0.95, exit_price=0.97)  # 93-97
        _seed(db, "d", entry_price=0.96, exit_price=0.97)  # 93-97

        envelope = gate.run_segmented_gate(
            db_path=db.db_path, strategy="signal_bot",
            lookback_days=30, segment_by="entry_band",
            now=time.time(),
        )
        assert set(envelope["segments"].keys()) == {"00-55", "55-70", "93-97"}
        assert envelope["segments"]["93-97"]["n"] == 2

    def test_envelope_passes_only_when_all_segments_pass(self, tmp_path):
        """Sample size fails on 3 rows; envelope must be FAIL."""
        db = PerformanceDB(db_path=_unique_db(tmp_path, "env"))
        for i in range(3):
            _seed(db, f"x-{i}", entry_price=0.60, exit_price=0.65)

        envelope = gate.run_segmented_gate(
            db_path=db.db_path, strategy="signal_bot",
            lookback_days=30, segment_by="signal_source",
            now=time.time(),
        )
        assert envelope["passed"] is False
        assert envelope["verdict"] == "FAIL"

    def test_empty_after_filter_fails_with_reason(self, tmp_path):
        """Filter to a value that exists in no rows — no silent pass."""
        db = PerformanceDB(db_path=_unique_db(tmp_path, "empty"))
        _seed(db, "a", entry_price=0.60, exit_price=0.65)

        envelope = gate.run_segmented_gate(
            db_path=db.db_path, strategy="signal_bot",
            lookback_days=30, segment_by="signal_source",
            filter_fill_model="nonexistent_model",
            now=time.time(),
        )
        assert envelope["passed"] is False
        assert envelope["segments"] == {}
        assert "no rows" in envelope["reason"]

    def test_rejects_invalid_segment_column(self, tmp_path):
        db = PerformanceDB(db_path=_unique_db(tmp_path, "bad"))
        with pytest.raises(ValueError, match="segment_by must be one of"):
            gate.run_segmented_gate(
                db_path=db.db_path, strategy="signal_bot",
                lookback_days=30, segment_by="strategy",  # not an allowed col
            )


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


class TestCLI:
    def test_segment_by_rejects_cycles_source(self, tmp_path, capsys):
        """Trades-only flags must error early with a clear message."""
        # Minimal cycles DB
        path = str(tmp_path / "c.db")
        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE cycles (
                id INTEGER PRIMARY KEY, asset TEXT, window_duration_secs INTEGER,
                is_dry_run INTEGER, pnl REAL, pair_cost REAL,
                ended_at REAL, exit_reason TEXT
            )
        """)
        conn.commit()
        conn.close()

        with pytest.raises(SystemExit):
            gate.main([
                "--source", "cycles", "--db", path,
                "--asset", "btc", "--window-duration", "300",
                "--segment-by", "signal_source",
            ])
        err = capsys.readouterr().err
        assert "--segment-by" in err and "trades" in err

    def test_segment_by_emits_segments_in_json(self, tmp_path, capsys):
        db = PerformanceDB(db_path=_unique_db(tmp_path, "cli"))
        for i in range(3):
            _seed(db, f"mm-{i}", entry_price=0.60, exit_price=0.65,
                  signal_source="binance_momentum")
        _seed(db, "pa-0", entry_price=0.60, exit_price=0.62,
              signal_source="pair_arb")

        rc = gate.main([
            "--source", "trades", "--db", db.db_path,
            "--strategy", "signal_bot",
            "--segment-by", "signal_source",
            "--lookback-days", "30", "--json",
        ])
        # Sample size fails (only 3 + 1 rows) so rc == 1 is expected.
        assert rc == 1
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["segmented_by"] == "signal_source"
        assert set(payload["segments"].keys()) == {"binance_momentum", "pair_arb"}

    def test_write_receipt_segmented_writes_per_partition(self, tmp_path, capsys):
        """Segmented runs must write one receipt per segment so verify-mode
        callers can target a specific partition by segment_key()."""
        db = PerformanceDB(db_path=_unique_db(tmp_path, "recpt"))
        _seed(db, "mm", entry_price=0.60, exit_price=0.65,
              signal_source="binance_momentum")
        _seed(db, "pa", entry_price=0.60, exit_price=0.62,
              signal_source="pair_arb")
        receipts_dir = tmp_path / "receipts"

        gate.main([
            "--source", "trades", "--db", db.db_path,
            "--strategy", "signal_bot",
            "--segment-by", "signal_source",
            "--write-receipt", str(receipts_dir),
            "--lookback-days", "30",
        ])
        # Two segments => two receipt files.
        files = sorted(p.name for p in receipts_dir.glob("*.json"))
        assert len(files) == 2
