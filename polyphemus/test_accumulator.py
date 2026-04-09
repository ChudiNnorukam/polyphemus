"""Focused tests for the hardened accumulator path."""

import asyncio
import json
import logging
import sys
import types
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
import polyphemus.accumulator as accumulator_module
from polyphemus.models import AccumulatorPosition, AccumulatorState, ExecutionResult


def make_config(tmp_path: Path, **overrides):
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


def make_engine(tmp_path: Path, **config_overrides):
    config = make_config(tmp_path, **config_overrides)
    clob = AsyncMock()
    clob.place_order = AsyncMock(return_value=SimpleNamespace(success=True, order_id="order-1"))
    clob.get_order_book = AsyncMock()
    balance = AsyncMock()
    balance.get_available_for_accumulator = AsyncMock(return_value=200.0)
    balance.get_balance = AsyncMock(return_value=200.0)
    store = MagicMock()
    logger = logging.getLogger("test.accumulator")
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
    engine._settlements = []
    return (
        engine,
        clob,
        balance,
        store,
    )


def test_candidate_rejected_before_activation_stays_out_of_active_positions(tmp_path):
    engine, clob, _, _ = make_engine(tmp_path)
    engine._discover_markets = AsyncMock(return_value=[{
        "slug": "btc-updown-5m-1",
        "asset": "btc",
        "window_type": "5m",
        "up_token_id": "up",
        "down_token_id": "down",
        "window_secs": 300,
        "expires_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        + __import__("datetime").timedelta(seconds=260),
        "condition_id": "cond-1",
    }])
    clob.get_order_book = AsyncMock(side_effect=[
        {"bids": [{"price": 0.47, "size": 200}], "asks": []},
        {"bids": [{"price": 0.46, "size": 200}], "asks": []},
        {"bids": [{"price": 0.50, "size": 200}], "asks": []},
        {"bids": [{"price": 0.49, "size": 200}], "asks": []},
    ])

    asyncio.run(engine._scan_for_window())

    assert engine.stats["active_positions"] == 0
    assert engine.stats["candidates_seen"] == 1
    assert engine.stats["candidates_rejected"] == 1
    assert engine.stats["last_candidate_slug"] == "btc-updown-5m-1"
    assert "bid_pair_expensive" in engine.stats["last_eval_block_reason"]
    assert clob.place_order.await_count == 0


def test_accumulator_requires_matching_dry_run_flags(tmp_path):
    with pytest.raises(ValueError, match="DRY_RUN and ACCUM_DRY_RUN to match"):
        make_engine(tmp_path, dry_run=True, accum_dry_run=False)


def test_legacy_circuit_breaker_state_is_migrated_to_instance_dir(tmp_path):
    engine, _, _, _ = make_engine(tmp_path)
    legacy_path = tmp_path / "legacy-data" / "circuit_breaker.json"
    instance_path = tmp_path / "instance-data" / "circuit_breaker.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(json.dumps({
        "total_pnl": -1.25,
        "hedged_count": 2,
        "unwound_count": 1,
        "consecutive_unwinds": 1,
        "orphaned_count": 3,
    }))

    engine._cb_state_path = str(instance_path)
    engine._legacy_cb_state_path = str(legacy_path)
    if instance_path.exists():
        instance_path.unlink()
    engine._accum_total_pnl = 0.0
    engine._accum_hedged_count = 0
    engine._accum_unwound_count = 0
    engine._accum_orphaned_count = 0
    engine._consecutive_unwinds = 0

    engine._load_circuit_breaker_state()

    assert engine.stats["total_pnl"] == -1.25
    assert engine.stats["hedged_count"] == 2
    assert engine.stats["unwound_count"] == 1
    assert engine.stats["orphaned_count"] == 3
    assert json.loads(instance_path.read_text())["total_pnl"] == -1.25


def test_stats_expose_monitoring_fields(tmp_path):
    engine, _, _, _ = make_engine(tmp_path)

    stats = engine.stats

    for key in (
        "last_candidate_slug",
        "last_candidate_bid_pair",
        "last_eval_block_reason",
        "candidates_seen",
        "candidates_rejected",
        "consecutive_unwinds",
        "circuit_tripped",
        "accum_dry_run",
        "effective_accumulator_dry_run",
    ):
        assert key in stats
    assert stats["effective_accumulator_dry_run"] is True


def test_dry_run_accumulator_never_calls_real_order_placement(tmp_path):
    engine, clob, _, _ = make_engine(tmp_path, dry_run=True, accum_dry_run=True)

    order_id = asyncio.run(engine._place_maker_order("token-1", 0.47, 25))

    assert order_id.startswith("dry_run_")
    assert clob.place_order.await_count == 0


def test_fak_entry_mode_uses_fee_aware_taker_entry(tmp_path):
    engine, clob, _, store = make_engine(
        tmp_path,
        dry_run=False,
        accum_dry_run=False,
        accum_entry_mode="fak",
        accum_max_pair_cost=0.995,
        accum_min_profit_per_share=0.001,
    )
    clob.place_fak_order = AsyncMock(side_effect=[
        ExecutionResult(success=True, order_id="up-fak"),
        ExecutionResult(success=True, order_id="down-fak"),
    ])
    clob.get_order_details = AsyncMock(side_effect=[
        {"price": 0.47, "size_matched": 20.0},
        {"price": 0.48, "size_matched": 20.0},
    ])
    pos = AccumulatorPosition(
        slug="eth-updown-5m-1",
        window_secs=300,
        state=AccumulatorState.SCANNING,
        up_token_id="up-token",
        down_token_id="down-token",
        market_end_time=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        + __import__("datetime").timedelta(seconds=240),
        entry_time=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        condition_id="cond-1",
    )
    up_book = {
        "bids": [{"price": 0.44, "size": 100}],
        "asks": [{"price": 0.47, "size": 100}],
    }
    down_book = {
        "bids": [{"price": 0.45, "size": 100}],
        "asks": [{"price": 0.48, "size": 100}],
    }

    entered = asyncio.run(engine._evaluate_and_enter_fak(pos, 240, up_book, down_book))

    assert entered is True
    assert pos.state == AccumulatorState.ACCUMULATING
    assert pos.up_qty == pytest.approx(20.0)
    assert pos.down_qty == pytest.approx(20.0)
    assert pos.up_fee_paid > 0
    assert pos.down_fee_paid > 0
    assert clob.place_fak_order.await_count == 2
    assert clob.place_order.await_count == 0
    assert store.add.call_count == 2


def test_fak_entry_mode_rejects_fee_negative_pairs(tmp_path):
    engine, clob, _, _ = make_engine(
        tmp_path,
        dry_run=False,
        accum_dry_run=False,
        accum_entry_mode="fak",
        accum_max_pair_cost=0.975,
    )
    pos = AccumulatorPosition(
        slug="btc-updown-5m-2",
        window_secs=300,
        state=AccumulatorState.SCANNING,
        up_token_id="up-token",
        down_token_id="down-token",
        market_end_time=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        + __import__("datetime").timedelta(seconds=240),
        entry_time=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        condition_id="cond-2",
    )
    up_book = {
        "bids": [{"price": 0.40, "size": 100}],
        "asks": [{"price": 0.49, "size": 100}],
    }
    down_book = {
        "bids": [{"price": 0.40, "size": 100}],
        "asks": [{"price": 0.49, "size": 100}],
    }

    entered = asyncio.run(engine._evaluate_and_enter_fak(pos, 240, up_book, down_book))

    assert entered is False
    assert "ask_pair_expensive" in engine.stats["last_eval_block_reason"]
    assert clob.place_fak_order.await_count == 0


def test_fok_fallback_uses_actual_fill_size_and_fee_accounting(tmp_path):
    engine, clob, _, store = make_engine(
        tmp_path,
        dry_run=False,
        accum_dry_run=False,
        accum_entry_mode="fak",
    )
    pos = AccumulatorPosition(
        slug="btc-updown-5m-fallback",
        window_secs=300,
        state=AccumulatorState.ACCUMULATING,
        up_token_id="up-token",
        down_token_id="down-token",
        market_end_time=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        + __import__("datetime").timedelta(seconds=240),
        entry_time=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        up_qty=20.0,
        up_avg_price=0.47,
        up_fee_paid=0.11,
        condition_id="cond-fallback",
    )
    clob.get_order_book = AsyncMock(return_value={
        "asks": [{"price": 0.48, "size": 100}],
        "bids": [{"price": 0.47, "size": 100}],
    })
    clob.place_fok_order = AsyncMock(return_value=ExecutionResult(success=True, order_id="fok-1"))
    clob.get_order_details = AsyncMock(return_value={
        "price": 0.48,
        "size_matched": 19.0,
    })

    hedged = asyncio.run(engine._try_fok_fallback(pos))

    expected_fee = engine._estimate_taker_fee_per_share(0.48) * 19.0

    assert hedged is True
    assert pos.state == AccumulatorState.HEDGED
    assert pos.down_qty == pytest.approx(19.0)
    assert pos.down_avg_price == pytest.approx(0.48)
    assert pos.down_fee_paid == pytest.approx(expected_fee)
    assert pos.pair_cost == pytest.approx(0.47 + 0.48 + ((0.11 + expected_fee) / 19.0))
    assert engine.stats["orders_filled"] == 1
    assert store.add.call_count == 1


def test_dry_run_unwind_includes_entry_and_exit_fees(tmp_path):
    engine, _, balance, store = make_engine(
        tmp_path,
        dry_run=True,
        accum_dry_run=True,
        accum_entry_mode="fak",
    )
    balance.sim_credit = MagicMock()
    pos = AccumulatorPosition(
        slug="eth-updown-5m-unwind",
        window_secs=300,
        state=AccumulatorState.ACCUMULATING,
        up_token_id="up-token",
        down_token_id="down-token",
        market_end_time=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        + __import__("datetime").timedelta(seconds=240),
        entry_time=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        up_qty=10.0,
        up_avg_price=0.47,
        up_fee_paid=0.10,
        condition_id="cond-unwind",
    )
    engine._positions[pos.slug] = pos

    asyncio.run(engine._unwind_orphan(pos, "UP"))

    sell_price = 0.45
    sell_fee = engine._estimate_taker_fee_per_share(sell_price) * 10.0
    expected_loss = (0.47 - sell_price) * 10.0 + 0.10 + sell_fee

    assert engine.stats["total_pnl"] == pytest.approx(round(-expected_loss, 2))
    assert engine.stats["unwound_count"] == 1
    assert engine.stats["active_positions"] == 0
    assert engine.stats["settlements"][-1]["exit_reason"] == "sellback"
    assert engine.stats["settlements"][-1]["pnl"] == pytest.approx(round(-expected_loss, 2))
    balance.sim_credit.assert_called_once_with(sell_price * 10.0)
    store.remove.assert_called_once_with("up-token")


def test_fak_rollout_remains_explicit_opt_in(tmp_path):
    engine, _, _, _ = make_engine(tmp_path)

    assert engine._accum_entry_mode() == "maker"
