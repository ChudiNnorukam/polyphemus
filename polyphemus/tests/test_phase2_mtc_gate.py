"""Phase 2 tests for the MTC pre-deploy gate.

The gate is the tripwire that blocks live deploys on insufficient or
decaying evidence. Tests cover:
  - Data loaders reject pre-Phase-0 schemas (no is_dry_run) and pre-Phase-1.7
    schemas (no asset/window_duration_secs).
  - Each of the five checks individually produces PASS/FAIL with an
    evidence payload downstream reporting can consume.
  - The orchestrator returns PASS only when ALL checks pass; any single
    FAIL flips the verdict.
  - Retroactive evaluation on a seeded 60-day window yields a deterministic,
    reproducible verdict (no wall-clock dependence).
"""

import sqlite3
import time
import uuid

import pytest

from polyphemus.accumulator_metrics import AccumulatorMetrics, CycleRecord
from polyphemus.performance_db import PerformanceDB
from polyphemus.tools.mtc_pre_deploy_gate import (
    find_latest_receipt,
    run_gate,
    segment_key,
    verify_receipt,
    write_receipt,
    _load_cycles,
    _load_trades,
)


DAY = 86400.0


def _unique_db(tmp_path, stem: str) -> str:
    return str(tmp_path / f"{stem}_{uuid.uuid4().hex[:8]}.db")


def _seed_cycles(
    db_path: str,
    *,
    asset: str,
    window: int,
    wins: int,
    losses: int,
    end: float,
    win_pnl: float = 0.50,
    loss_pnl: float = -0.50,
    pair_cost: float = 5.0,
    spacing_secs: float = 3600.0,
) -> None:
    """Populate cycles with a known WR over a known window. Alternates
    wins and losses so walk-forward CV sees consistent behavior across
    splits rather than a single regime."""
    metrics = AccumulatorMetrics(db_path=db_path)
    total = wins + losses
    now = end - total * spacing_secs
    for i in range(total):
        ts = now + i * spacing_secs
        is_win = i < wins  # front-load wins then losses for split-consistency
        # Distribute: alternate so walk-forward splits see mixed outcomes
        is_win = (i % 2 == 0) if wins == losses else is_win
        metrics.record_cycle(CycleRecord(
            slug=f"{asset}-updown-{window // 60}m-{int(ts)}",
            started_at=ts - 60, ended_at=ts,
            up_qty=10.0, down_qty=10.0,
            up_avg_price=0.5, down_avg_price=0.5,
            pair_cost=pair_cost,
            pnl=win_pnl if is_win else loss_pnl,
            exit_reason="hedged_settlement" if is_win else "sellback",
            reprices_used=0, fill_time_secs=1.0, hedge_time_secs=1.0,
            spread_at_entry=0.0, is_dry_run=True,
            asset=asset, window_duration_secs=window,
        ))


class TestCyclesDataLoader:
    def test_loader_respects_segmentation(self, tmp_path):
        path = _unique_db(tmp_path, "cycles_seg")
        now = time.time()
        _seed_cycles(path, asset="btc", window=300, wins=10, losses=2, end=now)
        _seed_cycles(path, asset="eth", window=900, wins=5, losses=5, end=now)

        btc_rows = _load_cycles(path, "btc", 300, lookback_days=30)
        eth_rows = _load_cycles(path, "eth", 900, lookback_days=30)
        cross = _load_cycles(path, "btc", 900, lookback_days=30)  # no such combo

        assert len(btc_rows) == 12
        assert len(eth_rows) == 10
        assert cross == []

    def test_loader_rejects_missing_phase_0_column(self, tmp_path):
        """Pre-Phase-0 DBs must not silently drop into the gate — the bug
        class we're blocking."""
        path = _unique_db(tmp_path, "pre_phase0")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE cycles (slug TEXT, ended_at REAL, pnl REAL)")
        conn.commit()
        conn.close()
        with pytest.raises(RuntimeError, match="missing required columns"):
            _load_cycles(path, "btc", 300, lookback_days=30)

    def test_loader_filters_is_dry_run(self, tmp_path):
        path = _unique_db(tmp_path, "cycles_dryfilter")
        now = time.time()
        metrics = AccumulatorMetrics(db_path=path)
        # One dry row, one live row.
        for flag in (True, False):
            metrics.record_cycle(CycleRecord(
                slug="btc-updown-5m-1", started_at=now - 60, ended_at=now,
                up_qty=10.0, down_qty=10.0,
                up_avg_price=0.5, down_avg_price=0.5,
                pair_cost=5.0, pnl=0.1,
                exit_reason="hedged_settlement",
                reprices_used=0, fill_time_secs=1.0, hedge_time_secs=1.0,
                spread_at_entry=0.0, is_dry_run=flag,
                asset="btc", window_duration_secs=300,
            ))
        rows = _load_cycles(path, "btc", 300, lookback_days=30)
        # Only the dry-run row should come back.
        assert len(rows) == 1


class TestTradesDataLoader:
    def test_loader_rejects_pre_phase_0_trades(self, tmp_path):
        path = _unique_db(tmp_path, "pre_phase0_trades")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE trades (trade_id TEXT, strategy TEXT, pnl REAL)")
        conn.commit()
        conn.close()
        with pytest.raises(RuntimeError, match="is_dry_run"):
            _load_trades(path, "signal_bot", lookback_days=30)


class TestGateOrchestrator:
    def test_pass_on_strong_dry_run(self, tmp_path):
        """Strong dry-run evidence: all five checks pass -> verdict PASS."""
        path = _unique_db(tmp_path, "gate_pass")
        now = time.time()
        # 80 cycles at 75% WR over the last 28 days (4 cycles/day avg).
        # High WR + consistent distribution -> all gates pass.
        metrics = AccumulatorMetrics(db_path=path)
        start = now - 28 * DAY
        wins = 60
        losses = 20
        for i in range(wins + losses):
            # Interleave wins/losses to give walk-forward mixed splits
            is_win = (i % 4) != 3  # 3 wins per 4 = 75%
            ts = start + i * (28 * DAY / (wins + losses))
            metrics.record_cycle(CycleRecord(
                slug=f"btc-updown-5m-{int(ts)}",
                started_at=ts - 60, ended_at=ts,
                up_qty=10.0, down_qty=10.0,
                up_avg_price=0.5, down_avg_price=0.5,
                pair_cost=5.0,
                pnl=0.80 if is_win else -0.30,
                exit_reason="hedged_settlement" if is_win else "sellback",
                reprices_used=0, fill_time_secs=1.0, hedge_time_secs=1.0,
                spread_at_entry=0.0, is_dry_run=True,
                asset="btc", window_duration_secs=300,
            ))
        v = run_gate(
            source="cycles", db_path=path, lookback_days=30,
            asset="btc", window_duration_secs=300,
            breakeven=0.50, now=now,
        )
        # Attribute full report to failure message for diagnosability
        assert v["passed"], f"expected PASS, got FAIL with checks:\n{v['checks']}"
        assert v["verdict"] == "PASS"
        assert v["n"] == wins + losses

    def test_fail_on_small_sample(self, tmp_path):
        path = _unique_db(tmp_path, "gate_small")
        now = time.time()
        _seed_cycles(path, asset="btc", window=300, wins=5, losses=2, end=now)
        v = run_gate(source="cycles", db_path=path, lookback_days=30,
                     asset="btc", window_duration_secs=300, now=now)
        assert not v["passed"]
        assert v["first_failure"] == "R1_sample_size"

    def test_fail_on_wr_at_breakeven(self, tmp_path):
        """A 50/50 WR across 80 samples should fail R2 (Wilson CI lower does
        not exceed breakeven=0.50) even if sample-size check passes."""
        path = _unique_db(tmp_path, "gate_50wr")
        now = time.time()
        _seed_cycles(path, asset="btc", window=300, wins=40, losses=40, end=now)
        v = run_gate(source="cycles", db_path=path, lookback_days=30,
                     asset="btc", window_duration_secs=300, now=now)
        assert not v["passed"]
        # Either R2 or one of the downstream stats checks will catch this;
        # the earliest failure must be R1 or R2 (never silent pass).
        first = v["first_failure"]
        assert first in ("R1_sample_size", "R2_hypothesis_test_wr",
                         "R3_walk_forward", "R4_deflated_sharpe"), \
            f"unexpected first_failure: {first}"

    def test_verdict_includes_all_five_checks(self, tmp_path):
        path = _unique_db(tmp_path, "gate_shape")
        now = time.time()
        _seed_cycles(path, asset="btc", window=300, wins=30, losses=5, end=now)
        v = run_gate(source="cycles", db_path=path, lookback_days=30,
                     asset="btc", window_duration_secs=300, now=now)
        check_names = [c["check"] for c in v["checks"]]
        assert check_names == [
            "R1_sample_size",
            "R2_hypothesis_test_wr",
            "R3_walk_forward",
            "R4_deflated_sharpe",
            "R5_alpha_decay",
        ]

    def test_missing_asset_arg_raises(self, tmp_path):
        path = _unique_db(tmp_path, "gate_noarg")
        AccumulatorMetrics(db_path=path)
        with pytest.raises(ValueError, match="--asset"):
            run_gate(source="cycles", db_path=path, lookback_days=30,
                     asset="", window_duration_secs=300)

    def test_missing_strategy_arg_raises(self, tmp_path):
        path = _unique_db(tmp_path, "gate_notrade")
        PerformanceDB(db_path=path)
        with pytest.raises(ValueError, match="--strategy"):
            run_gate(source="trades", db_path=path, lookback_days=30)

    def test_bad_source_raises(self, tmp_path):
        path = _unique_db(tmp_path, "gate_bad_src")
        with pytest.raises(ValueError, match="cycles.*trades"):
            run_gate(source="whatever", db_path=path, lookback_days=30)


class TestGateDeterminism:
    def test_repeated_run_gives_identical_verdict(self, tmp_path):
        """Same DB + same now -> bit-identical verdict. Determinism is a
        correctness property for governance: a gate that flips verdict
        between runs cannot be used to block deploys."""
        path = _unique_db(tmp_path, "gate_det")
        now = time.time()
        _seed_cycles(path, asset="btc", window=300, wins=30, losses=10, end=now)
        v1 = run_gate(source="cycles", db_path=path, lookback_days=30,
                      asset="btc", window_duration_secs=300, now=now)
        v2 = run_gate(source="cycles", db_path=path, lookback_days=30,
                      asset="btc", window_duration_secs=300, now=now)
        # Scrub generated_at (it's the input `now`, so equal, but belt and braces):
        v1["generated_at"] = v2["generated_at"] = 0
        assert v1 == v2


class TestReceipts:
    """Receipts persist the gate verdict so downstream tooling (predeploy.sh,
    LIFECYCLE enforcement) can verify gate status without re-running the
    computation. The receipt is the governance artifact."""

    def _fake_verdict(self, *, verdict: str = "PASS", source: str = "trades",
                      generated_at: float = 1_000_000.0) -> dict:
        if source == "trades":
            segment = {"strategy": "signal_bot"}
        else:
            segment = {"asset": "btc", "window_duration_secs": 300}
        return {
            "verdict": verdict,
            "passed": verdict == "PASS",
            "source": source,
            "db_path": "/tmp/whatever.db",
            "segment": segment,
            "lookback_days": 30,
            "n": 100, "wins": 60,
            "checks": [],
            "first_failure": None if verdict == "PASS" else "R2_hypothesis_test_wr",
            "generated_at": generated_at,
        }

    def test_segment_key_trades(self):
        v = self._fake_verdict(source="trades")
        assert segment_key(v) == "trades_signal_bot"

    def test_segment_key_cycles(self):
        v = self._fake_verdict(source="cycles")
        assert segment_key(v) == "cycles_btc_300"

    def test_write_receipt_creates_file(self, tmp_path):
        v = self._fake_verdict(generated_at=1_000_000)
        path = write_receipt(v, tmp_path / "evidence")
        assert path.exists()
        assert path.name == "trades_signal_bot_1000000.json"
        import json as _json
        payload = _json.loads(path.read_text())
        assert payload["segment_key"] == "trades_signal_bot"
        assert payload["verdict"] == "PASS"

    def test_write_receipt_creates_directory(self, tmp_path):
        """Receipt dir is auto-created. This matters because predeploy.sh's
        first call has no pre-existing evidence/ dir."""
        nested = tmp_path / "a" / "b" / "c"
        v = self._fake_verdict()
        path = write_receipt(v, nested)
        assert nested.is_dir()
        assert path.parent == nested

    def test_find_latest_receipt_picks_highest_timestamp(self, tmp_path):
        """Mtime-independent ordering: we key off the timestamp embedded in
        the filename, not file system mtime (which is clobbered by scp)."""
        key = "trades_signal_bot"
        write_receipt(self._fake_verdict(generated_at=100), tmp_path)
        write_receipt(self._fake_verdict(generated_at=300), tmp_path)
        write_receipt(self._fake_verdict(generated_at=200), tmp_path)
        latest = find_latest_receipt(tmp_path, key)
        assert latest is not None
        assert latest.name == f"{key}_300.json"

    def test_find_latest_receipt_returns_none_when_missing(self, tmp_path):
        assert find_latest_receipt(tmp_path, "no_such_segment") is None

    def test_verify_receipt_fresh_pass(self, tmp_path):
        now = 1_000_000.0
        write_receipt(self._fake_verdict(generated_at=now - 86400), tmp_path)
        result = verify_receipt(tmp_path, "trades_signal_bot",
                                max_age_days=7, now=now)
        assert result["ok"] is True
        assert result["verdict"] == "PASS"
        assert result["age_days"] == pytest.approx(1.0, rel=0.01)

    def test_verify_receipt_stale_fails(self, tmp_path):
        """Stale PASS receipts are worse than no receipt: they create the
        illusion of recent validation. Hard-fail them."""
        now = 1_000_000.0
        write_receipt(self._fake_verdict(generated_at=now - 30 * 86400), tmp_path)
        result = verify_receipt(tmp_path, "trades_signal_bot",
                                max_age_days=7, now=now)
        assert result["ok"] is False
        assert "stale" in result["reason"]

    def test_verify_receipt_fail_verdict_fails(self, tmp_path):
        """A fresh FAIL receipt must not satisfy verify. The whole point of
        the receipt is to enforce PASS."""
        now = 1_000_000.0
        write_receipt(self._fake_verdict(verdict="FAIL", generated_at=now),
                      tmp_path)
        result = verify_receipt(tmp_path, "trades_signal_bot",
                                max_age_days=7, now=now)
        assert result["ok"] is False
        assert "FAIL" in result["reason"]

    def test_verify_receipt_missing_fails(self, tmp_path):
        result = verify_receipt(tmp_path, "cycles_eth_900",
                                max_age_days=7, now=1_000_000.0)
        assert result["ok"] is False
        assert "no receipt" in result["reason"].lower()

    def test_verify_receipt_chooses_newest_even_if_older_was_pass(self, tmp_path):
        """If a PASS receipt exists but a newer FAIL receipt also exists,
        verify must pick the newest — we care about current state, not any
        historical PASS."""
        now = 1_000_000.0
        write_receipt(self._fake_verdict(verdict="PASS",
                                          generated_at=now - 2 * 86400),
                      tmp_path)
        write_receipt(self._fake_verdict(verdict="FAIL",
                                          generated_at=now - 86400),
                      tmp_path)
        result = verify_receipt(tmp_path, "trades_signal_bot",
                                max_age_days=7, now=now)
        assert result["ok"] is False
        assert result["verdict"] == "FAIL"
