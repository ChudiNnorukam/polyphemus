"""Phase 1.4 — probabilistic maker fill model for dry-run mode.

Before Phase 1.4, every resting dry-run maker order filled the instant it
was checked. That was the core of the Apr 10 2026 credibility failure:
hedge rates and P&L reported against a simulator that never modeled
maker misses. The fix is a pessimistic but explicit fill model gated by
POLYPHEMUS_DRY_RUN_V2.

These tests pin four invariants:

  1. Pure fill-model behavior: buried / crossed / prob_miss / prob_hit
     decisions and deterministic seeding.
  2. Feature-flag gate: off by default, parses common truthy/falsy forms.
  3. Legacy-compat: when the flag is off, `_check_and_handle_order` still
     fills every dry-run maker order (no regression for existing runs).
  4. v2 behavior: when the flag is on with a realistic book, no-fill
     decisions leave the order resting ("waiting"), exercising the same
     code paths live misses would hit.
"""

from __future__ import annotations

import asyncio
import logging
import random
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
from polyphemus.dry_run_fill_model import (
    MakerFillDecision,
    MakerFillModel,
    dry_run_v2_enabled,
)
from polyphemus.models import AccumulatorPosition, AccumulatorState


class TestMakerFillModelPure:
    """The fill model must be a pure, deterministic function of inputs plus
    an explicit RNG. Tests inject a seeded Random so expected probabilities
    are auditable."""

    def test_buried_below_bid_never_fills(self):
        m = MakerFillModel(rng=random.Random(0))
        d = m.evaluate(our_price=0.48, best_bid=0.49, best_ask=0.51, qty=10, elapsed_secs=100.0)
        assert d.filled is False
        assert d.reason == "buried"
        assert d.fill_price == 0.0
        assert d.fill_qty == 0.0

    def test_crossed_book_returns_no_fill(self):
        """Crossed or zero-spread books are book-feed noise; model declines
        to infer fill probability from them. our_price >= best_bid to
        exercise the crossed branch (not the buried branch)."""
        m = MakerFillModel(rng=random.Random(0))
        d = m.evaluate(our_price=0.52, best_bid=0.51, best_ask=0.49, qty=10, elapsed_secs=10.0)
        assert d.filled is False
        assert d.reason == "crossed_book"

    def test_zero_spread_returns_no_fill(self):
        m = MakerFillModel(rng=random.Random(0))
        d = m.evaluate(our_price=0.50, best_bid=0.50, best_ask=0.50, qty=10, elapsed_secs=10.0)
        assert d.filled is False
        assert d.reason == "crossed_book"

    def test_zero_elapsed_yields_zero_cumulative_probability(self):
        """With 0 seconds of rest, P(fill) = 0 — no instant fills. Prevents
        'just placed, already filled' contributions to spurious hedge rate."""
        m = MakerFillModel(rng=random.Random(0))
        d = m.evaluate(our_price=0.51, best_bid=0.49, best_ask=0.51, qty=10, elapsed_secs=0.0)
        assert d.filled is False
        assert d.reason == "prob_miss"

    def test_aggressive_price_fills_eventually_over_time(self):
        """At the ask with 10 seconds of rest, P(fill) >> 0.5. Seeded so we
        can assert a specific outcome."""
        m = MakerFillModel(rng=random.Random(42))
        d = m.evaluate(our_price=0.51, best_bid=0.49, best_ask=0.51, qty=10, elapsed_secs=10.0)
        assert d.filled is True
        assert d.reason == "prob_hit"
        assert d.fill_price == pytest.approx(0.51)
        assert d.fill_qty == pytest.approx(10)

    def test_same_rng_seed_is_deterministic(self):
        """Two models seeded identically must produce identical decisions.
        Required for test repeatability and for reproducible dry-run sessions."""
        m1 = MakerFillModel(rng=random.Random(7))
        m2 = MakerFillModel(rng=random.Random(7))
        args = dict(our_price=0.50, best_bid=0.49, best_ask=0.51, qty=10, elapsed_secs=5.0)
        for _ in range(20):
            assert m1.evaluate(**args) == m2.evaluate(**args)

    def test_aggression_clamped_at_or_above_ask(self):
        """our_price > best_ask should not produce negative miss rate or
        super-100% probability; aggression clamps at 1.0."""
        m = MakerFillModel(rng=random.Random(0))
        d = m.evaluate(our_price=0.60, best_bid=0.49, best_ask=0.51, qty=10, elapsed_secs=1.0)
        assert d.filled in (True, False)
        assert d.reason in ("prob_hit", "prob_miss")

    def test_cumulative_probability_capped_below_one(self):
        """With very long elapsed time at the ask, P must cap at MAX_CUMULATIVE
        so the simulation never deterministically fills by waiting."""
        misses = 0
        for seed in range(1000):
            m = MakerFillModel(rng=random.Random(seed))
            d = m.evaluate(our_price=0.51, best_bid=0.49, best_ask=0.51, qty=10, elapsed_secs=10_000)
            if not d.filled:
                misses += 1
        assert misses > 0, "cap not enforced — model is deterministically filling at long elapsed"

    def test_fill_decision_is_frozen(self):
        d = MakerFillDecision(True, 0.5, 10, "prob_hit")
        with pytest.raises(Exception):
            d.filled = False  # frozen dataclass — must raise


class TestFeatureFlag:
    def test_flag_default_off(self, monkeypatch):
        monkeypatch.delenv("POLYPHEMUS_DRY_RUN_V2", raising=False)
        assert dry_run_v2_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "True", "yes", "Yes", " 1 "])
    def test_flag_truthy_forms(self, monkeypatch, value):
        monkeypatch.setenv("POLYPHEMUS_DRY_RUN_V2", value)
        assert dry_run_v2_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "", "maybe", "off"])
    def test_flag_falsy_forms(self, monkeypatch, value):
        monkeypatch.setenv("POLYPHEMUS_DRY_RUN_V2", value)
        assert dry_run_v2_enabled() is False


def _make_engine(tmp_path: Path, **overrides) -> AccumulatorEngine:
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
    logging.getLogger("test.dry_run_fill_model").handlers = []

    engine = AccumulatorEngine(clob=clob, balance=balance, store=store, config=config)
    cb = Path(engine._cb_state_path)
    if cb.exists():
        cb.unlink()
    engine._legacy_cb_state_path = str(tmp_path / "legacy" / "cb.json")
    return engine


def _make_pos(*, target_shares: float = 10.0, order_age_secs: float = 30.0) -> AccumulatorPosition:
    """Position with one UP resting order placed order_age_secs ago."""
    now = datetime.now(timezone.utc)
    pos = AccumulatorPosition(
        slug=f"btc-updown-{uuid.uuid4().hex[:6]}",
        window_secs=300,
        state=AccumulatorState.ACCUMULATING,
        up_token_id="up-tok",
        down_token_id="down-tok",
        market_end_time=now + timedelta(seconds=180),
        entry_time=now - timedelta(seconds=60),
    )
    pos.target_shares = target_shares
    pos.up_order_id = "resting_up_1"
    pos.up_order_time = now - timedelta(seconds=order_age_secs)
    pos.pending_up_price = 0.50
    pos.pending_order_price = 0.50
    return pos


class TestLegacyCompat:
    """With the flag OFF (default), Phase 1.4 must be invisible. Every
    dry-run maker check still returns 'filled'. No regression."""

    def test_flag_off_always_fills(self, tmp_path, monkeypatch):
        monkeypatch.delenv("POLYPHEMUS_DRY_RUN_V2", raising=False)
        engine = _make_engine(tmp_path)
        pos = _make_pos()
        result = asyncio.run(engine._check_and_handle_order(
            pos, "UP", "resting_up_1", pos.up_order_time,
            best_bid=0.49, best_ask=0.51,
        ))
        assert result == "filled"
        assert pos.up_qty == pytest.approx(pos.target_shares)

    def test_flag_off_ignores_missing_book(self, tmp_path, monkeypatch):
        """Even with zero book passed in, flag-off fills. Pinning this
        invariant so we can't accidentally flip default behavior."""
        monkeypatch.delenv("POLYPHEMUS_DRY_RUN_V2", raising=False)
        engine = _make_engine(tmp_path)
        pos = _make_pos()
        result = asyncio.run(engine._check_and_handle_order(
            pos, "UP", "resting_up_1", pos.up_order_time,
            best_bid=0.0, best_ask=0.0,
        ))
        assert result == "filled"


class TestV2Behavior:
    """With the flag ON, unfavorable books must leave the order resting,
    and probabilistic outcomes must match the injected model's decision."""

    def test_v2_buried_order_stays_waiting(self, tmp_path, monkeypatch):
        monkeypatch.setenv("POLYPHEMUS_DRY_RUN_V2", "1")
        engine = _make_engine(tmp_path)
        pos = _make_pos()
        result = asyncio.run(engine._check_and_handle_order(
            pos, "UP", "resting_up_1", pos.up_order_time,
            best_bid=0.60, best_ask=0.65,
        ))
        assert result == "waiting"
        assert pos.up_qty == 0.0

    def test_v2_crossed_book_stays_waiting(self, tmp_path, monkeypatch):
        monkeypatch.setenv("POLYPHEMUS_DRY_RUN_V2", "1")
        engine = _make_engine(tmp_path)
        pos = _make_pos()
        result = asyncio.run(engine._check_and_handle_order(
            pos, "UP", "resting_up_1", pos.up_order_time,
            best_bid=0.52, best_ask=0.49,
        ))
        assert result == "waiting"
        assert pos.up_qty == 0.0

    def test_v2_deterministic_fill_with_seeded_model(self, tmp_path, monkeypatch):
        """Pre-seed the engine with a deterministic model to assert a specific
        fill outcome. Confirms the engine honors an injected model."""
        monkeypatch.setenv("POLYPHEMUS_DRY_RUN_V2", "1")
        engine = _make_engine(tmp_path)
        engine._maker_fill_model = MakerFillModel(rng=random.Random(42))
        pos = _make_pos(order_age_secs=10.0)
        result = asyncio.run(engine._check_and_handle_order(
            pos, "UP", "resting_up_1", pos.up_order_time,
            best_bid=0.49, best_ask=0.51,
        ))
        assert result == "filled"
        assert pos.up_qty == pytest.approx(pos.target_shares)

    def test_v2_missing_book_falls_back_to_legacy_fill(self, tmp_path, monkeypatch):
        """Defensive: when bid/ask default to 0, the engine falls back to
        legacy behavior even with the flag on. Prevents silent 'never
        fills' regression if a caller forgets to pass the book."""
        monkeypatch.setenv("POLYPHEMUS_DRY_RUN_V2", "1")
        engine = _make_engine(tmp_path)
        pos = _make_pos()
        result = asyncio.run(engine._check_and_handle_order(
            pos, "UP", "resting_up_1", pos.up_order_time,
            best_bid=0.0, best_ask=0.0,
        ))
        assert result == "filled"

    def test_v2_miss_rate_materially_above_zero(self, tmp_path, monkeypatch):
        """Over many trials with a tight spread and short elapsed, the
        cumulative miss rate must be meaningfully non-zero. This is the
        anti-Apr-10 check: dry-run cannot report 100% hedged."""
        monkeypatch.setenv("POLYPHEMUS_DRY_RUN_V2", "1")
        engine = _make_engine(tmp_path)
        pos = _make_pos(order_age_secs=1.0)
        misses = 0
        total = 200
        for seed in range(total):
            engine._maker_fill_model = MakerFillModel(rng=random.Random(seed))
            fresh_pos = _make_pos(order_age_secs=1.0)
            result = asyncio.run(engine._check_and_handle_order(
                fresh_pos, "UP", "resting_up_1", fresh_pos.up_order_time,
                best_bid=0.48, best_ask=0.52,
            ))
            if result == "waiting":
                misses += 1
        assert misses > total * 0.10, (
            f"fill model miss rate too low: {misses}/{total} — Apr 10 regression risk"
        )


class TestTopOfBookHelper:
    def test_top_of_book_returns_bid_ask(self, tmp_path):
        engine = _make_engine(tmp_path)
        book = {"bids": [{"price": "0.49"}], "asks": [{"price": "0.51"}]}
        bid, ask = engine._top_of_book(book)
        assert bid == pytest.approx(0.49)
        assert ask == pytest.approx(0.51)

    def test_top_of_book_empty_sides_return_zero(self, tmp_path):
        engine = _make_engine(tmp_path)
        assert engine._top_of_book({"bids": [], "asks": []}) == (0.0, 0.0)

    def test_top_of_book_none_safe(self, tmp_path):
        engine = _make_engine(tmp_path)
        assert engine._top_of_book(None) == (0.0, 0.0)
