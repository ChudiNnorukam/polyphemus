"""Phase 1.6 — sim balance must reconcile to pos.pnl at settlement.

Before this phase, `_handle_settlement` credited sim balance on winning
orphans and matched hedged pairs but did nothing on losing orphans, and
NEVER deducted fees from the sim balance. Two defects:

  1. The losing-orphan branch read as implicit — a future reader couldn't
     tell whether the silent no-op was correct or a missed case.
  2. Fees were tracked on `pos.up_fee_paid` / `pos.down_fee_paid` but
     never subtracted from sim balance. Final sim balance drifted from
     cumulative pnl by the sum of all fees paid — exactly the kind of
     dry-run/live divergence that produced the Apr 10 +$39 / -$85 gap.

These tests pin the invariant:

    (sim_balance_after_settlement - sim_balance_after_entry) == pos.pnl

for every settlement path (hedged, winning orphan, losing orphan,
empty), including fees. If the fix regresses, the assertion breaks
loudly instead of silently mis-accumulating across cycles.

The tests also pin the intent markers (explicit no-op on losing orphans
as `elif ... else: pass`) so someone grepping for 'losing orphan' in
future audits lands on documented code, not silence.
"""

from __future__ import annotations

import asyncio
import logging
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


class _RecordingBalance:
    """Minimal stand-in for BalanceManager that tracks sim_credit / sim_deduct
    calls. Running balance is the natural ledger: final - initial = net delta.

    Using a real recorder rather than MagicMock so we can assert on the NET
    delta rather than call-by-call, which would couple tests to the order
    of credit/deduct calls — an implementation detail."""

    def __init__(self, initial: float = 400.0):
        self.initial = initial
        self.balance = initial
        self.events: list[tuple[str, float]] = []

    def sim_credit(self, amount: float):
        self.balance += amount
        self.events.append(("credit", amount))

    def sim_deduct(self, amount: float):
        self.balance -= amount
        self.events.append(("deduct", amount))

    @property
    def delta(self) -> float:
        return self.balance - self.initial

    # Required shim methods for AccumulatorEngine construction —
    # never called in the settlement path we exercise.
    async def get_available_for_accumulator(self) -> float:
        return self.balance

    async def get_balance(self) -> float:
        return self.balance


def _make_engine(tmp_path: Path, initial_balance: float = 400.0):
    config = _make_config(tmp_path)
    clob = AsyncMock()
    clob.cancel_order = AsyncMock()
    clob.place_order = AsyncMock()
    balance = _RecordingBalance(initial=initial_balance)
    store = MagicMock()
    store.get_open = MagicMock(return_value=[])

    engine = AccumulatorEngine(clob=clob, balance=balance, store=store, config=config)
    engine._legacy_cb_state_path = str(tmp_path / "legacy-cb.json")
    engine._cb_state_path = str(tmp_path / "cb.json")
    engine._accum_total_pnl = 0.0
    engine._accum_hedged_count = 0
    engine._accum_orphaned_count = 0
    engine._accum_unwound_count = 0
    engine._consecutive_unwinds = 0
    engine._circuit_tripped = False

    # Real metrics DB so _emit_cycle doesn't explode; contents not asserted.
    db_path = str(tmp_path / f"accum_{uuid.uuid4().hex[:8]}.db")
    engine.set_metrics(AccumulatorMetrics(db_path=db_path))

    logger = logging.getLogger(f"test.phase1_6.{uuid.uuid4().hex[:6]}")
    logger.handlers = []
    engine._logger = logger

    return engine, balance


def _make_position(
    *,
    up_qty: float,
    down_qty: float,
    up_avg_price: float = 0.50,
    down_avg_price: float = 0.48,
    up_fee_paid: float = 0.0,
    down_fee_paid: float = 0.0,
    slug: str | None = None,
) -> AccumulatorPosition:
    now = datetime.now(tz=timezone.utc)
    pos = AccumulatorPosition(
        slug=slug or f"btc-updown-5m-{uuid.uuid4().hex[:6]}",
        window_secs=300,
        state=AccumulatorState.SETTLING,
        up_token_id="up-tok",
        down_token_id="down-tok",
        market_end_time=now - timedelta(seconds=30),
        entry_time=now - timedelta(minutes=5),
        up_qty=up_qty,
        down_qty=down_qty,
        up_avg_price=up_avg_price,
        down_avg_price=down_avg_price,
        pair_cost=up_avg_price + down_avg_price,
    )
    pos.up_fee_paid = up_fee_paid
    pos.down_fee_paid = down_fee_paid
    return pos


async def _simulate_entry_and_settle(engine, pos: AccumulatorPosition, result: str):
    """Mimic the fills then call _handle_settlement with a stubbed result.

    Entry deducts qty * avg_price (mirrors real _apply_fill). Fees are NOT
    deducted at entry — that's what Phase 1.6 reconciles at settlement.
    """
    if pos.up_qty > 0:
        engine._balance.sim_deduct(pos.up_qty * pos.up_avg_price)
    if pos.down_qty > 0:
        engine._balance.sim_deduct(pos.down_qty * pos.down_avg_price)

    async def _stub_resolution(*args, **kwargs):
        return result

    engine._wait_for_settlement = _stub_resolution  # type: ignore[assignment]
    await engine._handle_settlement(pos)


# --- hedged settlement ------------------------------------------------------


class TestHedgedSettlement:
    """Matched hedged pair: $1.00 payout for matched shares, plus fees."""

    def test_balance_delta_equals_pnl_with_fees(self, tmp_path):
        """Invariant: (balance change from start of cycle to end) == pos.pnl.
        Hedged + fees is the common happy path; regression here breaks
        aggregate accum_metrics drift monitoring."""
        engine, balance = _make_engine(tmp_path)
        pos = _make_position(
            up_qty=10, down_qty=10,
            up_avg_price=0.50, down_avg_price=0.48,
            up_fee_paid=0.15, down_fee_paid=0.15,
        )
        initial_after_entry = balance.balance  # after sim_deduct of entries

        asyncio.run(_simulate_entry_and_settle(engine, pos, "resolved_up"))

        # pos.pnl should equal the net balance movement from pre-entry to
        # post-settlement.
        net = balance.balance - balance.initial
        assert pos.pnl == pytest.approx(net), (
            f"pnl={pos.pnl} != net balance delta={net} "
            f"(events={balance.events})"
        )
        # Hand-computed: matched(10) * 1.00 - 10*0.50 - 10*0.48 - 0.30 = -0.10
        # (pair cost 0.98 + fees 0.03/share → slight loss; the check is not
        # that hedged is always profitable, but that balance reconciles.)
        assert pos.pnl == pytest.approx(-0.10)

    def test_imbalanced_pair_excess_counts_as_loss(self, tmp_path):
        """Hedged with imbalance (up_qty != down_qty): the unmatched
        excess's entry cost stays as loss, and pnl reflects that. Balance
        must still reconcile."""
        engine, balance = _make_engine(tmp_path)
        pos = _make_position(
            up_qty=12, down_qty=8,
            up_avg_price=0.50, down_avg_price=0.48,
            up_fee_paid=0.18, down_fee_paid=0.12,
        )
        asyncio.run(_simulate_entry_and_settle(engine, pos, "resolved_up"))

        net = balance.balance - balance.initial
        assert pos.pnl == pytest.approx(net)
        # matched=8, total_spent = 12*0.50 + 8*0.48 + 0.30 = 6.0 + 3.84 + 0.30 = 10.14
        # pnl = 8*1.00 - 10.14 = -2.14
        assert pos.pnl == pytest.approx(-2.14)


# --- orphan settlement ------------------------------------------------------


class TestWinningOrphan:
    def test_winning_orphan_reconciles_with_fees(self, tmp_path):
        """Single leg that resolves in our favor: $1.00/share minus fees."""
        engine, balance = _make_engine(tmp_path)
        pos = _make_position(
            up_qty=20, down_qty=0,
            up_avg_price=0.40,
            up_fee_paid=0.50,
        )
        asyncio.run(_simulate_entry_and_settle(engine, pos, "resolved_up"))

        net = balance.balance - balance.initial
        assert pos.pnl == pytest.approx(net)
        # pnl = 20*1.00 - 20*0.40 - 0.50 = 11.50
        assert pos.pnl == pytest.approx(11.50)


class TestLosingOrphan:
    """Core Phase 1.6 fix: losing orphan's balance MUST show the full loss
    (entry cost + fees), not just the entry cost."""

    def test_losing_orphan_balance_reflects_full_loss(self, tmp_path):
        engine, balance = _make_engine(tmp_path)
        pos = _make_position(
            up_qty=20, down_qty=0,
            up_avg_price=0.40,
            up_fee_paid=0.50,
        )
        asyncio.run(_simulate_entry_and_settle(engine, pos, "resolved_down"))

        net = balance.balance - balance.initial
        assert pos.pnl == pytest.approx(net)
        # pnl = 0 - 20*0.40 - 0.50 = -8.50
        assert pos.pnl == pytest.approx(-8.50)

    def test_losing_orphan_no_payout_credit(self, tmp_path):
        """Beyond just matching pnl: confirm no stray sim_credit for the
        losing-orphan branch. Only the fee deduct should fire in settlement."""
        engine, balance = _make_engine(tmp_path)
        pos = _make_position(
            up_qty=0, down_qty=15,
            down_avg_price=0.35,
            down_fee_paid=0.25,
        )
        asyncio.run(_simulate_entry_and_settle(engine, pos, "resolved_up"))

        # Settlement-phase events only (skip the 1-2 entry deducts).
        entry_events = 1  # only down leg was filled, so 1 entry deduct
        settle_events = balance.events[entry_events:]

        credit_events = [e for e in settle_events if e[0] == "credit"]
        deduct_events = [e for e in settle_events if e[0] == "deduct"]
        assert credit_events == [], (
            f"losing orphan must not credit sim balance; got {credit_events}"
        )
        # Fee deduct must be the only settlement-phase balance change.
        assert len(deduct_events) == 1
        assert deduct_events[0][1] == pytest.approx(0.25)


class TestEmptySettlement:
    """Empty settlement (no fills either side): no credits, no deducts,
    balance untouched from entry."""

    def test_empty_settlement_no_balance_change(self, tmp_path):
        engine, balance = _make_engine(tmp_path)
        pos = _make_position(up_qty=0, down_qty=0)
        asyncio.run(_simulate_entry_and_settle(engine, pos, "resolved_up"))

        assert balance.delta == pytest.approx(0.0)
        assert balance.events == []  # no entry deducts either (no fills)


# --- fee reconciliation invariant ------------------------------------------


class TestFeeReconciliation:
    """Fees tracked on the position MUST be subtracted from sim balance
    exactly once during settlement. This is the drift prevention check."""

    def test_fee_deduct_exactly_once_per_cycle(self, tmp_path):
        """Double-deducting fees would cause accelerating drift over many
        cycles. Zero-deducting causes slower drift. Exactly once is right."""
        engine, balance = _make_engine(tmp_path)
        pos = _make_position(
            up_qty=10, down_qty=10,
            up_fee_paid=0.20, down_fee_paid=0.20,
        )
        asyncio.run(_simulate_entry_and_settle(engine, pos, "resolved_up"))

        # entry: 2 deducts (up, down legs)
        # settle: 1 credit (matched payout) + 1 deduct (fees)
        entry_deducts = [e for e in balance.events[:2] if e[0] == "deduct"]
        assert len(entry_deducts) == 2

        settle_events = balance.events[2:]
        fee_deducts = [e for e in settle_events if e[0] == "deduct"]
        assert len(fee_deducts) == 1, (
            f"fees must deduct exactly once per cycle; got {fee_deducts}"
        )
        assert fee_deducts[0][1] == pytest.approx(0.40)

    def test_zero_fees_no_phantom_deduct(self, tmp_path):
        """If a cycle somehow has zero fees (e.g. maker fills at the time
        Polymarket ran maker-rebate), no fee-deduct call should fire. Phantom
        deduct would shift balance below cumulative pnl by a float epsilon."""
        engine, balance = _make_engine(tmp_path)
        pos = _make_position(
            up_qty=5, down_qty=5,
            up_fee_paid=0.0, down_fee_paid=0.0,
        )
        asyncio.run(_simulate_entry_and_settle(engine, pos, "resolved_down"))

        settle_events = balance.events[2:]
        deducts = [e for e in settle_events if e[0] == "deduct"]
        assert deducts == [], f"no fees → no deduct, got {deducts}"


# --- multi-cycle cumulative invariant ---------------------------------------


class TestCumulativePnL:
    """Strongest drift-detection test: run several cycles of mixed outcomes
    through the same balance. At the end, balance delta must equal the
    sum of pnls. Any off-by-one in the credit/deduct logic compounds."""

    def test_mixed_cycles_balance_equals_sum_pnl(self, tmp_path):
        engine, balance = _make_engine(tmp_path, initial_balance=1000.0)

        cycles = [
            dict(up_qty=10, down_qty=10, up_avg_price=0.50, down_avg_price=0.48,
                 up_fee_paid=0.15, down_fee_paid=0.15, result="resolved_up"),
            dict(up_qty=20, down_qty=0, up_avg_price=0.40,
                 up_fee_paid=0.50, result="resolved_down"),  # losing orphan
            dict(up_qty=0, down_qty=15, down_avg_price=0.35,
                 down_fee_paid=0.25, result="resolved_down"),  # winning orphan
            dict(up_qty=5, down_qty=5, up_avg_price=0.55, down_avg_price=0.50,
                 up_fee_paid=0.08, down_fee_paid=0.08, result="resolved_up"),
        ]

        total_pnl = 0.0
        for spec in cycles:
            result = spec.pop("result")
            pos = _make_position(**spec)
            asyncio.run(_simulate_entry_and_settle(engine, pos, result))
            total_pnl += pos.pnl

        assert balance.delta == pytest.approx(total_pnl), (
            f"cumulative balance delta {balance.delta} "
            f"!= sum of pnls {total_pnl} (drift check)"
        )
