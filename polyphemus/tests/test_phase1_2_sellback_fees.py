"""Phase 1.2 — sellback taker fee must flow into recorded P&L.

Regression guard for the Apr 10 2026 bug class: a dry-run sellback's recorded
loss must include BOTH the entry taker fee and the sell taker fee, not just
the price delta. If someone drops the fee term, the cycle row underreports the
loss and the aggregate is optimistic by fee_rate * shares per cycle.

These tests pin the math end-to-end: call `_unwind_orphan` under dry-run, read
the cycle back from accum_metrics.db, compare to hand-computed expectations.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone
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


def _estimate_taker_fee(price: float) -> float:
    """Mirror of AccumulatorEngine._estimate_taker_fee_per_share. Kept here so
    the test fails if the formula constant drifts silently."""
    return price * (1.0 - price) * 0.0624


def _make_engine(tmp_path: Path, **overrides):
    defaults = {
        "dry_run": True,
        "enable_accumulator": True,
        "accum_dry_run": True,
        "lagbot_data_dir": str(tmp_path / "inst"),
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
    config = SimpleNamespace(**defaults)

    clob = AsyncMock()
    balance = AsyncMock()
    balance.sim_credit = MagicMock()
    balance.get_balance = AsyncMock(return_value=200.0)
    balance._cache_time = 0
    store = MagicMock()
    logging.getLogger("test.sellback_fees").handlers = []

    engine = AccumulatorEngine(clob=clob, balance=balance, store=store, config=config)
    cb = Path(engine._cb_state_path)
    if cb.exists():
        cb.unlink()
    engine._legacy_cb_state_path = str(tmp_path / "legacy" / "cb.json")
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


def _make_orphan_position(
    *,
    filled_side: str = "UP",
    qty: float = 10.0,
    avg_price: float = 0.50,
    entry_fee_paid: float = 0.10,
    secs_to_expiry: int = 120,
) -> AccumulatorPosition:
    """Build a position where ONE side filled and the other is empty."""
    now = datetime.now(timezone.utc)
    end = now + timedelta(seconds=secs_to_expiry)
    pos = AccumulatorPosition(
        slug=f"btc-updown-sellback-{uuid.uuid4().hex[:6]}",
        window_secs=300,
        state=AccumulatorState.ACCUMULATING,
        up_token_id="up-tok",
        down_token_id="down-tok",
        market_end_time=end,
        entry_time=now - timedelta(seconds=30),
    )
    if filled_side == "UP":
        pos.up_qty = qty
        pos.up_avg_price = avg_price
        pos.up_fee_paid = entry_fee_paid
    else:
        pos.down_qty = qty
        pos.down_avg_price = avg_price
        pos.down_fee_paid = entry_fee_paid
    pos.pair_cost = avg_price
    return pos


def _read_one_cycle(db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM cycles").fetchall()
    conn.close()
    assert len(rows) == 1, f"expected exactly 1 cycle row, got {len(rows)}"
    return dict(rows[0])


class TestSellbackFeeFlow:
    def test_dry_run_sellback_pnl_includes_entry_and_sell_taker_fees(self, tmp_path):
        """Hand-compute the unwind_loss and assert the recorded cycle matches."""
        engine, _, db_path = _make_engine(tmp_path)
        qty, avg_price, entry_fee = 10.0, 0.50, 0.10
        pos = _make_orphan_position(
            filled_side="UP", qty=qty, avg_price=avg_price, entry_fee_paid=entry_fee
        )

        asyncio.run(engine._unwind_orphan(pos, "UP"))

        row = _read_one_cycle(db_path)
        assert row["exit_reason"] == "sellback"
        assert row["is_dry_run"] == 1

        expected_sell_price = max(avg_price - 0.02, 0.01)
        expected_sell_fee = _estimate_taker_fee(expected_sell_price) * qty
        expected_unwind_loss = (
            (avg_price - expected_sell_price) * qty + entry_fee + expected_sell_fee
        )
        assert row["pnl"] == pytest.approx(-expected_unwind_loss, abs=1e-6), \
            "sellback pnl must equal -((entry - sell) * qty + entry_fee + sell_fee)"

        fee_component = entry_fee + expected_sell_fee
        assert fee_component > 0
        assert abs(row["pnl"]) > (avg_price - expected_sell_price) * qty, \
            "pnl magnitude must exceed bare spread loss; fees were dropped"

    def test_dry_run_sellback_credits_sim_balance_with_proceeds(self, tmp_path):
        """Live invariant: balance credit = sell_price * qty (proceeds, not P&L)."""
        engine, _, _ = _make_engine(tmp_path)
        qty, avg_price = 10.0, 0.50
        pos = _make_orphan_position(filled_side="UP", qty=qty, avg_price=avg_price)

        asyncio.run(engine._unwind_orphan(pos, "UP"))

        engine._balance.sim_credit.assert_called_once()
        credited = engine._balance.sim_credit.call_args[0][0]
        expected_sell_price = max(avg_price - 0.02, 0.01)
        assert credited == pytest.approx(expected_sell_price * qty, abs=1e-6)

    def test_dust_position_sunk_loss_equals_entry_cost_plus_entry_fee(self, tmp_path):
        """qty<5 path: nothing sold, so sunk_loss = entry cost + entry fee only.
        Sell fee must NOT appear (no sale happened)."""
        engine, _, db_path = _make_engine(tmp_path)
        qty, avg_price, entry_fee = 3.0, 0.50, 0.05
        pos = _make_orphan_position(
            filled_side="UP", qty=qty, avg_price=avg_price, entry_fee_paid=entry_fee
        )

        asyncio.run(engine._unwind_orphan(pos, "UP"))

        row = _read_one_cycle(db_path)
        assert row["exit_reason"] == "sellback_skipped_below_min"
        expected_sunk = avg_price * qty + entry_fee
        assert row["pnl"] == pytest.approx(-expected_sunk, abs=1e-6)

    def test_expired_market_forced_hold_loss_is_entry_cost_plus_entry_fee(self, tmp_path):
        """Expired market: cannot sell, sunk = shares * entry_price + entry_fee.
        No sell fee (no sale)."""
        engine, _, db_path = _make_engine(tmp_path)
        qty, avg_price, entry_fee = 10.0, 0.45, 0.15
        pos = _make_orphan_position(
            filled_side="DOWN", qty=qty, avg_price=avg_price,
            entry_fee_paid=entry_fee, secs_to_expiry=-10,
        )

        asyncio.run(engine._unwind_orphan(pos, "DOWN"))

        row = _read_one_cycle(db_path)
        assert row["exit_reason"] == "forced_hold_expired"
        expected_loss = avg_price * qty + entry_fee
        assert row["pnl"] == pytest.approx(-expected_loss, abs=1e-6)

    def test_down_side_sellback_uses_down_fee_not_up_fee(self, tmp_path):
        """Regression guard: filled_side=DOWN must read down_fee_paid, not up_fee_paid."""
        engine, _, db_path = _make_engine(tmp_path)
        qty, avg_price = 10.0, 0.52
        down_entry_fee = 0.18
        pos = _make_orphan_position(
            filled_side="DOWN", qty=qty, avg_price=avg_price,
            entry_fee_paid=down_entry_fee,
        )
        pos.up_fee_paid = 999.0

        asyncio.run(engine._unwind_orphan(pos, "DOWN"))

        row = _read_one_cycle(db_path)
        assert row["exit_reason"] == "sellback"
        expected_sell_price = max(avg_price - 0.02, 0.01)
        expected_sell_fee = _estimate_taker_fee(expected_sell_price) * qty
        expected_loss = (
            (avg_price - expected_sell_price) * qty + down_entry_fee + expected_sell_fee
        )
        assert row["pnl"] == pytest.approx(-expected_loss, abs=1e-6), \
            "down-side sellback must use down_fee_paid"
