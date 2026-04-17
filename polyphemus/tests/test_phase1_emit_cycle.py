"""Phase 1.1 — _emit_cycle() persistence tests.

Before Phase 1.1, only `_handle_settlement` wrote CycleRecord rows. Sellback and
forced-hold terminal paths silently exited — this is the Apr 10 2026 bug class
(+$39 reported vs -$85 actual P&L because sellbacks were dropped from the
denominator).

Each test below covers one terminal path in `_unwind_orphan` or
`_handle_settlement` and asserts:
  1. A CycleRecord row lands in accum_metrics.cycles
  2. exit_reason is persisted correctly
  3. is_dry_run is set (dry-run engine must tag its cycles)
  4. get_stats aggregation sees the row (not silently dropped)
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import sys
import time
import types
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _install_py_clob_stub():
    if "py_clob_client" in sys.modules:
        return

    pkg = types.ModuleType("py_clob_client")
    clob_types = types.ModuleType("py_clob_client.clob_types")
    constants = types.ModuleType("py_clob_client.order_builder.constants")

    for name in (
        "OrderArgs",
        "MarketOrderArgs",
        "BalanceAllowanceParams",
        "AssetType",
        "OrderType",
        "TradeParams",
    ):
        setattr(clob_types, name, type(name, (), {}))

    constants.BUY = "BUY"
    constants.SELL = "SELL"

    sys.modules["py_clob_client"] = pkg
    sys.modules["py_clob_client.clob_types"] = clob_types
    sys.modules["py_clob_client.order_builder.constants"] = constants


_install_py_clob_stub()

from polyphemus.accumulator import AccumulatorEngine
from polyphemus.accumulator_metrics import AccumulatorMetrics
from polyphemus.models import AccumulatorPosition, AccumulatorState


def _make_config(tmp_path: Path, **overrides):
    defaults = {
        "dry_run": True,
        "enable_accumulator": True,
        "accum_dry_run": True,
        "lagbot_data_dir": str(tmp_path / "instance-data"),
        "max_daily_loss": 50.0,
        "accum_assets": "BTC",
        "accum_window_types": "5m",
        "accum_max_pair_cost": 0.975,
        "accum_min_profit_per_share": 0.02,
        "accum_min_shares": 5.0,
        "accum_max_shares": 500.0,
        "accum_scan_interval": 1,
        "accum_min_secs_remaining": 180,
        "accum_settle_timeout_secs": 60,
        "accum_maker_max_retries": 3,
        "accum_maker_retry_delay": 0.01,
        "accum_maker_price_decrement": 0.005,
        "accum_max_single_side_pct": 0.70,
        "accum_capital_pct": 0.40,
        "accum_order_timeout": 30,
        "accum_reprice_limit": 5,
        "accum_max_concurrent": 3,
        "accum_max_side_price": 0.65,
        "accum_hedge_deadline_secs": 120,
        "dry_run_balance": 400.0,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_engine_with_metrics(tmp_path: Path, **config_overrides):
    """Build an engine wired to a real AccumulatorMetrics on a throwaway DB.

    Uses the same SimpleNamespace + AsyncMock fixture pattern as
    test_accumulator.py so behavior is consistent with existing suite.
    """
    config = _make_config(tmp_path, **config_overrides)
    clob = AsyncMock()
    clob.place_order = AsyncMock(return_value=SimpleNamespace(success=True, order_id="order-1"))
    clob.get_order_book = AsyncMock()
    balance = AsyncMock()
    balance.get_available_for_accumulator = AsyncMock(return_value=200.0)
    balance.get_balance = AsyncMock(return_value=200.0)
    store = MagicMock()
    logger = logging.getLogger("test.emit_cycle")
    logger.handlers = []

    engine = AccumulatorEngine(clob=clob, balance=balance, store=store, config=config)
    engine._legacy_cb_state_path = str(tmp_path / "legacy-data" / "circuit_breaker.json")
    cb_path = Path(engine._cb_state_path)
    if cb_path.exists():
        cb_path.unlink()
    engine._accum_total_pnl = 0.0
    engine._accum_hedged_count = 0
    engine._accum_orphaned_count = 0
    engine._accum_unwound_count = 0
    engine._consecutive_unwinds = 0
    engine._circuit_tripped = False

    db_path = str(tmp_path / f"accum_{uuid.uuid4().hex[:8]}.db")
    metrics = AccumulatorMetrics(db_path=db_path)
    engine.set_metrics(metrics)

    return engine, metrics, db_path


def _make_position(
    *,
    slug: str = "btc-updown-5m-test",
    up_qty: float = 10.0,
    down_qty: float = 10.0,
    up_avg_price: float = 0.50,
    down_avg_price: float = 0.48,
    pair_cost: float = 0.98,
    state: AccumulatorState = AccumulatorState.ACCUMULATING,
    exit_reason: str | None = None,
) -> AccumulatorPosition:
    now = datetime.now(timezone.utc)
    pos = AccumulatorPosition(
        slug=slug,
        window_secs=300,
        state=state,
        up_token_id="up-tok",
        down_token_id="down-tok",
        market_end_time=now,
        entry_time=now,
        up_qty=up_qty,
        down_qty=down_qty,
        up_avg_price=up_avg_price,
        down_avg_price=down_avg_price,
        pair_cost=pair_cost,
    )
    pos.exit_reason = exit_reason
    return pos


def _read_cycles(db_path: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM cycles").fetchall()
    conn.close()
    return rows


class TestEmitCycleTerminalPaths:
    """One test per terminal path. Each must land exactly one row."""

    def test_hedged_settlement_records_cycle(self, tmp_path):
        engine, _, db_path = _make_engine_with_metrics(tmp_path)
        pos = _make_position(exit_reason="hedged_settlement")
        engine._emit_cycle(pos, pnl=0.25, exit_reason="hedged_settlement", hedge_time_if_hedged=True)

        rows = _read_cycles(db_path)
        assert len(rows) == 1
        assert rows[0]["exit_reason"] == "hedged_settlement"
        assert rows[0]["is_dry_run"] == 1
        assert rows[0]["hedge_time_secs"] > 0

    def test_sellback_records_cycle(self, tmp_path):
        """Apr 10 regression guard: sellback cycles MUST land in DB."""
        engine, _, db_path = _make_engine_with_metrics(tmp_path)
        pos = _make_position(slug="btc-updown-5m-sellback")
        engine._emit_cycle(pos, pnl=-0.15, exit_reason="sellback")

        rows = _read_cycles(db_path)
        assert len(rows) == 1
        assert rows[0]["exit_reason"] == "sellback"
        assert rows[0]["is_dry_run"] == 1
        assert rows[0]["pnl"] == pytest.approx(-0.15)

    def test_sellback_skipped_below_min_records_cycle(self, tmp_path):
        """Dust-position sunk-loss path must still be accounted for."""
        engine, _, db_path = _make_engine_with_metrics(tmp_path)
        pos = _make_position(slug="btc-updown-5m-dust", up_qty=2.0, down_qty=0.0)
        engine._emit_cycle(pos, pnl=-1.00, exit_reason="sellback_skipped_below_min")

        rows = _read_cycles(db_path)
        assert len(rows) == 1
        assert rows[0]["exit_reason"] == "sellback_skipped_below_min"
        assert rows[0]["is_dry_run"] == 1

    def test_forced_hold_expired_records_cycle(self, tmp_path):
        engine, _, db_path = _make_engine_with_metrics(tmp_path)
        pos = _make_position(slug="btc-updown-5m-hold-exp")
        engine._emit_cycle(pos, pnl=-0.50, exit_reason="forced_hold_expired")

        rows = _read_cycles(db_path)
        assert len(rows) == 1
        assert rows[0]["exit_reason"] == "forced_hold_expired"
        assert rows[0]["is_dry_run"] == 1

    def test_forced_hold_clob_unindexed_records_cycle(self, tmp_path):
        engine, _, db_path = _make_engine_with_metrics(tmp_path)
        pos = _make_position(slug="btc-updown-5m-unindexed")
        engine._emit_cycle(pos, pnl=-0.30, exit_reason="forced_hold_clob_unindexed")

        rows = _read_cycles(db_path)
        assert len(rows) == 1
        assert rows[0]["exit_reason"] == "forced_hold_clob_unindexed"
        assert rows[0]["is_dry_run"] == 1

    def test_forced_hold_sell_failed_records_cycle(self, tmp_path):
        engine, _, db_path = _make_engine_with_metrics(tmp_path)
        pos = _make_position(slug="btc-updown-5m-fail")
        engine._emit_cycle(pos, pnl=-0.40, exit_reason="forced_hold_sell_failed")

        rows = _read_cycles(db_path)
        assert len(rows) == 1
        assert rows[0]["exit_reason"] == "forced_hold_sell_failed"
        assert rows[0]["is_dry_run"] == 1


class TestEmitCycleSemantics:
    """Guards on the helper's contract, independent of call sites."""

    def test_live_engine_writes_is_dry_run_zero(self, tmp_path):
        engine, _, db_path = _make_engine_with_metrics(
            tmp_path, dry_run=False, accum_dry_run=False
        )
        pos = _make_position(slug="live-cycle")
        engine._emit_cycle(pos, pnl=0.10, exit_reason="hedged_settlement", hedge_time_if_hedged=True)

        rows = _read_cycles(db_path)
        assert len(rows) == 1
        assert rows[0]["is_dry_run"] == 0, \
            "live cycle must NOT be tagged dry-run"

    def test_none_pnl_does_not_record(self, tmp_path):
        """Contract: pnl is None means 'don't know', not 'zero'. Skip the write."""
        engine, _, db_path = _make_engine_with_metrics(tmp_path)
        pos = _make_position()
        engine._emit_cycle(pos, pnl=None, exit_reason="hedged_settlement")  # type: ignore[arg-type]

        rows = _read_cycles(db_path)
        assert len(rows) == 0

    def test_metrics_exception_does_not_propagate(self, tmp_path):
        """Helper must swallow DB failures — a broken metrics DB cannot crash
        the accumulator's exit handler."""
        engine, metrics, _ = _make_engine_with_metrics(tmp_path)
        metrics.record_cycle = MagicMock(side_effect=RuntimeError("disk full"))
        pos = _make_position()
        engine._emit_cycle(pos, pnl=0.10, exit_reason="hedged_settlement")

    def test_missing_metrics_does_not_crash(self, tmp_path):
        """No metrics injected = no-op, not exception."""
        engine, _, _ = _make_engine_with_metrics(tmp_path)
        engine._metrics = None
        pos = _make_position()
        engine._emit_cycle(pos, pnl=0.10, exit_reason="hedged_settlement")


class TestGetStatsIncludesAllExits:
    """End-to-end: Apr 10 regression test.

    Previously, sellback cycles were silently dropped from accum_metrics.db, so
    get_stats(dry_run_only=True) excluded sellback P&L — reported a profit
    while the wallet showed a loss. This test ensures every terminal path
    contributes to the aggregate."""

    def test_all_terminal_paths_counted_in_aggregate(self, tmp_path):
        engine, metrics, db_path = _make_engine_with_metrics(tmp_path)

        fixtures = [
            ("hedged_settlement", 0.25, True),
            ("sellback", -0.15, False),
            ("sellback_skipped_below_min", -1.00, False),
            ("forced_hold_expired", -0.50, False),
            ("forced_hold_clob_unindexed", -0.30, False),
            ("forced_hold_sell_failed", -0.40, False),
        ]
        for i, (reason, pnl, is_hedged) in enumerate(fixtures):
            pos = _make_position(slug=f"btc-test-{i}", exit_reason=reason)
            engine._emit_cycle(
                pos,
                pnl=pnl,
                exit_reason=reason,
                hedge_time_if_hedged=is_hedged,
            )

        snapshot = metrics.get_all_stats()
        assert snapshot.total_cycles == 6, \
            "every terminal path must be counted; Apr 10 bug class"

        expected_total = sum(pnl for _, pnl, _ in fixtures)
        assert snapshot.total_pnl == pytest.approx(expected_total), \
            "aggregate P&L must sum every exit type, not just hedged"

        rows = _read_cycles(db_path)
        exit_reasons = {r["exit_reason"] for r in rows}
        expected_reasons = {r[0] for r in fixtures}
        assert exit_reasons == expected_reasons, \
            f"missing exit_reasons in DB: {expected_reasons - exit_reasons}"
        assert all(r["is_dry_run"] == 1 for r in rows), \
            "dry-run engine must tag all its cycles"
