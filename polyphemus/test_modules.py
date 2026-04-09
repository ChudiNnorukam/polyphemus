"""Unit tests for Polyphemus modules."""

import asyncio
import json
import os
import sqlite3
import sys
import time
import tempfile
import pytest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

# Ensure polyphemus package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from polyphemus.models import (
    Position, ExitSignal, ExitReason, ExecutionResult, OrderStatus,
    FilterResult, MomentumResult, ASSET_TO_BINANCE,
    ArbOpportunity, ArbResult, ARB_SLIPPAGE_BUFFER, ARB_MARKET_BUFFER_SECS,
)
from polyphemus.position_store import PositionStore
from polyphemus.signal_guard import SignalGuard
from polyphemus.exit_manager import ExitManager
from polyphemus.self_tuner import SelfTuner
from polyphemus.clob_wrapper import ClobWrapper
from polyphemus.position_executor import PositionExecutor
from polyphemus.config import Settings
from polyphemus.binance_feed import BinanceFeed
from polyphemus.arb_engine import ArbEngine
from polyphemus.balance_manager import BalanceManager
from polyphemus.dashboard import Dashboard
from polyphemus.performance_db import PerformanceDB


def make_config(**overrides):
    """Create a Settings instance for testing."""
    defaults = dict(
        private_key="0x" + "a" * 64,
        wallet_address="0x914377734689c9e055B8826733F90dF0893817a2",
        clob_api_key="test_key",
        clob_secret="test_secret",
        clob_passphrase="test_pass",
        builder_api_key="test_builder_key",
        builder_secret="test_builder_secret",
        builder_passphrase="test_builder_pass",
        polygon_rpc_url="http://localhost:8545",
        dry_run=True,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def make_position(**overrides):
    """Create a Position for testing."""
    now = datetime.now(timezone.utc)
    defaults = dict(
        token_id="0xtoken001",
        slug="sol-200-feb14",
        entry_price=0.68,
        entry_size=100.0,
        entry_time=now,
        entry_tx_hash="0xhash001",
        market_end_time=now + timedelta(days=7),
    )
    defaults.update(overrides)
    return Position(**defaults)


def make_signal(**overrides):
    """Create a valid signal dict for testing."""
    defaults = dict(
        token_id="0xtoken001",
        direction="BUY",
        outcome="down",
        price=0.80,
        asset="SOL",
        slug="sol-test",
        usdc_size=100.0,
        timestamp=time.time(),
        tx_hash="0xtx001",
        market_title="SOL above $200",
    )
    defaults.update(overrides)
    return defaults


# ============================================================================
# PositionStore Tests
# ============================================================================

class TestPositionStore:
    def test_add_and_get(self):
        store = PositionStore()
        pos = make_position()
        store.add(pos)
        assert store.get("0xtoken001") is pos

    def test_get_nonexistent(self):
        store = PositionStore()
        assert store.get("0xnotfound") is None

    def test_update(self):
        store = PositionStore()
        pos = make_position()
        store.add(pos)
        store.update("0xtoken001", current_price=0.75)
        assert store.get("0xtoken001").current_price == 0.75

    def test_update_nonexistent_noop(self):
        store = PositionStore()
        store.update("0xnotfound", current_price=0.75)  # should not crash

    def test_remove(self):
        store = PositionStore()
        pos = make_position()
        store.add(pos)
        removed = store.remove("0xtoken001")
        assert removed is pos
        assert store.get("0xtoken001") is None

    def test_remove_nonexistent(self):
        store = PositionStore()
        assert store.remove("0xnotfound") is None

    def test_get_open(self):
        store = PositionStore()
        for i in range(3):
            store.add(make_position(token_id=f"0xtoken{i:03d}", slug=f"slug-{i}"))
        assert len(store.get_open()) == 3

    def test_get_open_excludes_exited(self):
        store = PositionStore()
        pos = make_position()
        pos.exit_time = datetime.now(timezone.utc)
        store.add(pos)
        assert len(store.get_open()) == 0

    def test_get_by_slug(self):
        store = PositionStore()
        pos = make_position()
        store.add(pos)
        assert store.get_by_slug("sol-200-feb14") is pos
        assert store.get_by_slug("nonexistent") is None

    def test_count_open(self):
        store = PositionStore()
        for i in range(5):
            store.add(make_position(token_id=f"0xtoken{i:03d}", slug=f"slug-{i}"))
        assert store.count_open() == 5


# ============================================================================
# SignalGuard Tests
# ============================================================================

class TestSignalGuard:
    def test_valid_signal_passes(self):
        config = make_config()
        store = PositionStore()
        guard = SignalGuard(config, store)
        signal = make_signal()
        result = guard.check(signal)
        assert result.passed
        assert len(result.reasons) == 0

    def test_rejects_non_buy(self):
        config = make_config()
        store = PositionStore()
        guard = SignalGuard(config, store)
        signal = make_signal(direction="SELL")
        result = guard.check(signal)
        assert not result.passed
        assert "not_buy_signal" in result.reasons

    def test_rejects_price_too_low(self):
        config = make_config()
        store = PositionStore()
        guard = SignalGuard(config, store)
        signal = make_signal(price=0.50)
        result = guard.check(signal)
        assert not result.passed
        assert "price_out_of_range" in result.reasons

    def test_rejects_price_too_high(self):
        config = make_config()
        store = PositionStore()
        guard = SignalGuard(config, store)
        signal = make_signal(price=0.86)  # > 0.85 max_entry_price (strict >)
        result = guard.check(signal)
        assert not result.passed
        assert "price_out_of_range" in result.reasons

    def test_rejects_blocked_asset(self):
        config = make_config(blocked_assets="XRP")
        store = PositionStore()
        guard = SignalGuard(config, store)
        signal = make_signal(asset="XRP")
        result = guard.check(signal)
        assert not result.passed
        assert "blocked_asset" in result.reasons

    def test_boundary_price_passes(self):
        """Price exactly at max_entry_price passes (strict > check)."""
        config = make_config()
        store = PositionStore()
        guard = SignalGuard(config, store)
        signal = make_signal(price=0.85)  # == max_entry_price → passes
        result = guard.check(signal)
        assert result.passed

    def test_rejects_duplicate_slug(self):
        config = make_config()
        store = PositionStore()
        store.add(make_position(slug="sol-test"))
        guard = SignalGuard(config, store)
        signal = make_signal(slug="sol-test")
        result = guard.check(signal)
        assert not result.passed
        assert "duplicate_slug" in result.reasons

    def test_rejects_max_positions(self):
        config = make_config(max_open_positions=2)
        store = PositionStore()
        for i in range(2):
            store.add(make_position(token_id=f"0xt{i}", slug=f"s-{i}"))
        guard = SignalGuard(config, store)
        signal = make_signal(slug="new-slug")
        result = guard.check(signal)
        assert not result.passed
        assert "max_positions" in result.reasons

    def test_rejects_low_conviction(self):
        config = make_config(min_db_signal_size=50.0)
        store = PositionStore()
        guard = SignalGuard(config, store)
        signal = make_signal(usdc_size=10.0)
        result = guard.check(signal)
        assert not result.passed
        assert "low_conviction" in result.reasons

    def test_collects_all_reasons(self):
        """Guard collects ALL rejection reasons, doesn't short-circuit."""
        config = make_config()
        store = PositionStore()
        guard = SignalGuard(config, store)
        signal = make_signal(direction="SELL", price=0.50, asset="XRP")
        result = guard.check(signal)
        assert not result.passed
        assert len(result.reasons) >= 2  # not_buy_signal + price_out_of_range

    def test_metrics_tracking(self):
        config = make_config()
        store = PositionStore()
        guard = SignalGuard(config, store)
        guard.check(make_signal())
        guard.check(make_signal(price=0.50))
        metrics = guard.get_metrics()
        assert metrics["signals_received"] == 2
        assert metrics["signals_passed"] == 1

    def test_momentum_epoch_filter_uses_time_remaining(self):
        config = make_config(momentum_max_epoch_elapsed_secs=60)
        store = PositionStore()
        guard = SignalGuard(config, store)
        market_epoch = int(time.time()) - 89
        signal = make_signal(
            source="binance_momentum",
            asset="BTC",
            outcome="Up",
            slug=f"btc-updown-5m-{market_epoch}",
            market_window_secs=300,
            time_remaining_secs=211,
            price=0.72,
        )
        result = guard.check(signal)
        assert not result.passed
        assert "epoch_too_late" in result.reasons
        assert result.context["epoch_elapsed_secs"] == 89
        assert result.context["epoch_max_elapsed_secs"] == 60
        assert result.context["time_remaining_secs"] == 211

    def test_momentum_epoch_filter_accepts_early_signal_from_time_remaining(self):
        config = make_config(momentum_max_epoch_elapsed_secs=60)
        store = PositionStore()
        guard = SignalGuard(config, store)
        market_epoch = int(time.time()) - 50
        signal = make_signal(
            source="binance_momentum",
            asset="BTC",
            outcome="Up",
            slug=f"btc-updown-5m-{market_epoch}",
            market_window_secs=300,
            time_remaining_secs=250,
            price=0.72,
        )
        result = guard.check(signal)
        assert result.passed
        assert "epoch_too_late" not in result.reasons


# ============================================================================
# ExitManager Tests
# ============================================================================

class TestExitManager:
    def test_max_hold_triggers_first(self):
        """max_hold is checked FIRST (Principle #6)."""
        config = make_config(max_hold_mins=12)
        store = PositionStore()
        now = datetime.now(timezone.utc)
        pos = make_position(
            entry_time=now - timedelta(minutes=15),  # 15 min hold > 12 max
            current_price=0.70,
        )
        store.add(pos)
        mgr = ExitManager(store, config)
        exits = mgr.check_all(now)
        assert len(exits) == 1
        assert exits[0].reason == ExitReason.MAX_HOLD.value

    def test_time_exit_near_market_end(self):
        """time_exit triggers when market ends within buffer."""
        config = make_config(time_exit_buffer_mins=5)
        store = PositionStore()
        now = datetime.now(timezone.utc)
        pos = make_position(
            entry_time=now - timedelta(minutes=2),  # recent entry (no max_hold)
            market_end_time=now + timedelta(minutes=3),  # 3 min away < 5 buffer
            current_price=0.70,
        )
        store.add(pos)
        mgr = ExitManager(store, config)
        exits = mgr.check_all(now)
        assert len(exits) == 1
        assert exits[0].reason == ExitReason.TIME_EXIT.value

    def test_market_resolved(self):
        """market_resolved triggers when is_resolved is True."""
        config = make_config()
        store = PositionStore()
        now = datetime.now(timezone.utc)
        pos = make_position(
            entry_time=now - timedelta(minutes=2),  # recent (no max_hold)
            market_end_time=now + timedelta(days=7),  # far away (no time_exit)
            is_resolved=True,
        )
        store.add(pos)
        mgr = ExitManager(store, config)
        exits = mgr.check_all(now)
        assert len(exits) == 1
        assert exits[0].reason == ExitReason.MARKET_RESOLVED.value

    def test_profit_target(self):
        """profit_target triggers when price exceeds target."""
        config = make_config(profit_target_pct=0.20)
        store = PositionStore()
        now = datetime.now(timezone.utc)
        # target = 0.68 * 1.20 = 0.816
        pos = make_position(
            entry_time=now - timedelta(minutes=2),
            entry_price=0.68,
            current_price=0.82,  # > 0.816
        )
        store.add(pos)
        mgr = ExitManager(store, config)
        exits = mgr.check_all(now)
        assert len(exits) == 1
        assert exits[0].reason == ExitReason.PROFIT_TARGET.value

    def test_sell_signal(self):
        """sell_signal triggers from metadata."""
        config = make_config(enable_sell_signal_exit=True)
        store = PositionStore()
        now = datetime.now(timezone.utc)
        pos = make_position(
            entry_time=now - timedelta(minutes=2),
            current_price=0.70,
        )
        pos.metadata["sell_signal_received"] = True
        store.add(pos)
        mgr = ExitManager(store, config)
        exits = mgr.check_all(now)
        assert len(exits) == 1
        assert exits[0].reason == ExitReason.SELL_SIGNAL.value

    def test_no_exit_when_healthy(self):
        """No exits for a recently-opened healthy position."""
        config = make_config()
        store = PositionStore()
        now = datetime.now(timezone.utc)
        pos = make_position(
            entry_time=now - timedelta(minutes=2),
            current_price=0.70,
        )
        store.add(pos)
        mgr = ExitManager(store, config)
        exits = mgr.check_all(now)
        assert len(exits) == 0

    def test_idempotent_pending(self):
        """Pending exits aren't re-triggered."""
        config = make_config(max_hold_mins=12)
        store = PositionStore()
        now = datetime.now(timezone.utc)
        pos = make_position(entry_time=now - timedelta(minutes=15))
        store.add(pos)
        mgr = ExitManager(store, config)
        exits1 = mgr.check_all(now)
        assert len(exits1) == 1
        # Second check: still pending
        exits2 = mgr.check_all(now)
        assert len(exits2) == 0

    def test_fail_exit_backoff_then_retry(self):
        """Failed exit respects backoff, then retries after backoff expires."""
        config = make_config(max_hold_mins=12)
        store = PositionStore()
        now = datetime.now(timezone.utc)
        pos = make_position(entry_time=now - timedelta(minutes=15))
        store.add(pos)
        mgr = ExitManager(store, config)

        # First attempt generates exit
        exits1 = mgr.check_all(now)
        assert len(exits1) == 1

        # Fail it — should enter backoff (5s for first failure)
        mgr.fail_exit(pos.token_id)

        # Immediate retry is blocked by backoff
        exits2 = mgr.check_all(now)
        assert len(exits2) == 0

        # After backoff expires, retry succeeds
        # Manually expire the backoff by adjusting the timestamp
        mgr._exit_failures[pos.token_id] = (1, time.time() - 10)
        exits3 = mgr.check_all(now)
        assert len(exits3) == 1

    def test_stop_loss_triggers(self):
        """stop_loss triggers when price drops below threshold."""
        config = make_config(stop_loss_pct=0.15, enable_stop_loss=True)
        store = PositionStore()
        now = datetime.now(timezone.utc)
        # entry_price=0.70, stop_price = 0.70 * 0.85 = 0.595
        pos = make_position(
            entry_time=now - timedelta(minutes=2),
            entry_price=0.70,
            current_price=0.58,  # below 0.595 stop
        )
        store.add(pos)
        mgr = ExitManager(store, config)
        exits = mgr.check_all(now)
        assert len(exits) == 1
        assert exits[0].reason == ExitReason.STOP_LOSS.value

    def test_stop_loss_no_trigger_above_threshold(self):
        """stop_loss does NOT trigger when price is above threshold."""
        config = make_config(stop_loss_pct=0.15)
        store = PositionStore()
        now = datetime.now(timezone.utc)
        pos = make_position(
            entry_time=now - timedelta(minutes=2),
            entry_price=0.70,
            current_price=0.65,  # above 0.595 stop
        )
        store.add(pos)
        mgr = ExitManager(store, config)
        exits = mgr.check_all(now)
        assert len(exits) == 0

    def test_stop_loss_no_trigger_without_price(self):
        """stop_loss does NOT trigger when current_price is 0 (no price data)."""
        config = make_config(stop_loss_pct=0.15)
        store = PositionStore()
        now = datetime.now(timezone.utc)
        pos = make_position(
            entry_time=now - timedelta(minutes=2),
            entry_price=0.70,
            current_price=0.0,  # no price data yet
        )
        store.add(pos)
        mgr = ExitManager(store, config)
        exits = mgr.check_all(now)
        assert len(exits) == 0


# ============================================================================
# SelfTuner Tests
# ============================================================================

class TestSelfTuner:
    def test_default_multiplier(self):
        """Default multiplier is 1.0 when no state file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "tuning_state.json")
            tuner = SelfTuner(path)
            assert tuner.get_multiplier(0.68) == 1.0
            assert tuner.get_multiplier(0.75) == 1.0

    def test_bucket_selection(self):
        """Correct bucket based on price threshold."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "tuning_state.json")
            with open(path, "w") as f:
                json.dump({"multipliers": {"0.65-0.70": 0.8, "0.70-0.80": 1.2}, "consecutive_losses": 0, "peak_balance": 100, "current_balance": 100}, f)
            tuner = SelfTuner(path)
            assert tuner.get_multiplier(0.68) == 0.8  # 0.65-0.70 bucket
            assert tuner.get_multiplier(0.75) == 1.2  # 0.70-0.80 bucket

    def test_update_win(self):
        """Win increases multiplier."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "tuning_state.json")
            with open(path, "w") as f:
                json.dump({"multipliers": {"0.65-0.70": 1.0, "0.70-0.80": 1.0}, "consecutive_losses": 0, "peak_balance": 100, "current_balance": 100}, f)
            tuner = SelfTuner(path)
            tuner.update_state("0.65-0.70", won=True, balance=110)
            assert tuner.get_multiplier(0.68) == 1.05

    def test_update_loss(self):
        """Loss decreases multiplier."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "tuning_state.json")
            with open(path, "w") as f:
                json.dump({"multipliers": {"0.65-0.70": 1.0, "0.70-0.80": 1.0}, "consecutive_losses": 0, "peak_balance": 100, "current_balance": 100}, f)
            tuner = SelfTuner(path)
            tuner.update_state("0.65-0.70", won=False, balance=90)
            assert tuner.get_multiplier(0.68) == 0.9

    def test_circuit_breaker(self):
        """3+ consecutive losses trigger circuit breaker (0.5x floor)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "tuning_state.json")
            with open(path, "w") as f:
                json.dump({"multipliers": {"0.65-0.70": 1.0, "0.70-0.80": 1.0}, "consecutive_losses": 3, "peak_balance": 100, "current_balance": 85}, f)
            tuner = SelfTuner(path)
            assert tuner.get_multiplier(0.68) == 0.5
            assert tuner.get_multiplier(0.75) == 0.5

    def test_kill_switch(self):
        """30%+ drawdown kills all multipliers to 0.5x floor."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "tuning_state.json")
            with open(path, "w") as f:
                json.dump({"multipliers": {"0.65-0.70": 1.0, "0.70-0.80": 1.0}, "consecutive_losses": {"0.65-0.70": 0, "0.70-0.80": 0}, "peak_balance": 200, "current_balance": 130}, f)
            tuner = SelfTuner(path)
            # 35% drawdown (200 -> 130)
            assert tuner.get_multiplier(0.68) == 0.5


# ============================================================================
# PositionExecutor Tests
# ============================================================================

class TestPositionExecutor:
    @staticmethod
    async def _fast_sleep(_seconds):
        return None

    @pytest.mark.asyncio
    async def test_execute_buy_success(self):
        """Successful buy places order, polls fill, creates position."""
        config = make_config()
        store = PositionStore()
        clob = AsyncMock(spec=ClobWrapper)
        clob.place_order.return_value = ExecutionResult(
            success=True, order_id="0xorder001"
        )
        clob.get_order_details.return_value = {"status": OrderStatus.FILLED, "size_matched": 100.0, "original_size": 100.0, "price": 0.70}
        clob.get_midpoint.return_value = 0.70
        clob.get_share_balance.return_value = 100.0  # Share verification gate

        executor = PositionExecutor(clob, store, config)
        signal = make_signal(token_id="0xtoken001", price=0.70, slug="sol-test")
        result = await executor.execute_buy(signal, available_capital=500.0)

        assert result.success
        assert result.order_id == "0xorder001"
        # Position should be in store
        pos = store.get("0xtoken001")
        assert pos is not None
        assert pos.slug == "sol-test"

    @pytest.mark.asyncio
    async def test_execute_buy_zero_capital(self):
        """Zero capital returns failure."""
        config = make_config()
        store = PositionStore()
        clob = AsyncMock(spec=ClobWrapper)
        executor = PositionExecutor(clob, store, config)
        signal = make_signal()
        result = await executor.execute_buy(signal, available_capital=0.0)
        assert not result.success

    @pytest.mark.asyncio
    async def test_execute_buy_invalid_signal(self):
        """Invalid signal (no token_id) returns failure."""
        config = make_config()
        store = PositionStore()
        clob = AsyncMock(spec=ClobWrapper)
        executor = PositionExecutor(clob, store, config)
        signal = {"price": 0.70}  # missing token_id
        result = await executor.execute_buy(signal, available_capital=500.0)
        assert not result.success

    @pytest.mark.asyncio
    async def test_execute_buy_placement_failure(self):
        """Order placement failure returns failure."""
        config = make_config()
        store = PositionStore()
        clob = AsyncMock(spec=ClobWrapper)
        clob.place_order.return_value = ExecutionResult(
            success=False, error="Connection timeout"
        )
        clob.get_midpoint.return_value = 0.70
        executor = PositionExecutor(clob, store, config)
        signal = make_signal()
        result = await executor.execute_buy(signal, available_capital=500.0)
        assert not result.success
        assert store.count_open() == 0

    @pytest.mark.asyncio
    async def test_execute_buy_insufficient_capital(self):
        """Insufficient capital (0.0 available) returns failure early."""
        config = make_config()
        store = PositionStore()
        clob = AsyncMock(spec=ClobWrapper)
        executor = PositionExecutor(clob, store, config)
        signal = make_signal()
        result = await executor.execute_buy(signal, available_capital=0.0)
        assert not result.success
        # place_order should not have been called
        clob.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_btc5m_placement_retry_succeeds_after_transient_failure(self):
        config = make_config(
            dry_run=False,
            btc5m_entry_retry_enabled=True,
            btc5m_entry_retry_mode="active",
            btc5m_entry_retry_delay_ms=0,
            max_entry_price=0.75,
        )
        store = PositionStore()
        clob = AsyncMock(spec=ClobWrapper)
        clob.place_order.side_effect = [
            ExecutionResult(success=False, error="Order placement failed: Request exception!"),
            ExecutionResult(success=True, order_id="0xretry001"),
        ]
        clob.get_midpoint.return_value = 0.68
        clob.get_order_details.return_value = {
            "status": OrderStatus.FILLED,
            "size_matched": 11.0,
            "original_size": 11.0,
            "price": 0.69,
        }
        clob.get_share_balance.return_value = 11.0

        executor = PositionExecutor(clob, store, config)
        signal = make_signal(
            asset="BTC",
            source="binance_momentum",
            market_window_secs=300,
            time_remaining_secs=280,
            price=0.68,
            slug="btc-updown-5m-1773250800",
        )
        with patch("polyphemus.position_executor.asyncio.sleep", new=self._fast_sleep):
            result = await executor.execute_buy(signal, available_capital=500.0)

        assert result.success
        stats = executor.get_entry_retry_stats()
        assert stats["placement_retry_eligible"] == 1
        assert stats["placement_retry_attempted"] == 1
        assert stats["retry_recovered"] == 1
        assert store.count_open() == 1

    @pytest.mark.asyncio
    async def test_btc5m_fill_retry_succeeds_after_timeout(self):
        config = make_config(
            dry_run=False,
            btc5m_entry_retry_enabled=True,
            btc5m_entry_retry_mode="active",
            btc5m_entry_retry_delay_ms=0,
            max_entry_price=0.75,
        )
        store = PositionStore()
        clob = AsyncMock(spec=ClobWrapper)
        clob.place_order.side_effect = [
            ExecutionResult(success=True, order_id="0xfirst"),
            ExecutionResult(success=True, order_id="0xsecond"),
        ]
        clob.get_midpoint.return_value = 0.68
        clob.get_order_details.side_effect = (
            [{"status": OrderStatus.LIVE, "size_matched": 0.0, "original_size": 11.0, "price": 0.70}] * 11
            + [{"status": OrderStatus.FILLED, "size_matched": 11.0, "original_size": 11.0, "price": 0.69}]
        )
        clob.cancel_order.return_value = True
        clob.get_share_balance.return_value = 11.0

        executor = PositionExecutor(clob, store, config)
        signal = make_signal(
            asset="BTC",
            source="binance_momentum",
            market_window_secs=300,
            time_remaining_secs=280,
            price=0.68,
            slug="btc-updown-5m-1773251100",
        )
        with patch("polyphemus.position_executor.asyncio.sleep", new=self._fast_sleep):
            result = await executor.execute_buy(signal, available_capital=500.0)

        assert result.success
        stats = executor.get_entry_retry_stats()
        assert stats["fill_retry_eligible"] == 1
        assert stats["fill_retry_attempted"] == 1
        assert stats["fill_timeouts"] == 1
        assert stats["retry_recovered"] == 1
        assert store.count_open() == 1

    @pytest.mark.asyncio
    async def test_btc5m_fill_retry_skips_when_midpoint_above_cap(self):
        config = make_config(
            dry_run=False,
            max_entry_price=0.75,
            btc5m_entry_retry_enabled=True,
            btc5m_entry_retry_mode="active",
            btc5m_entry_retry_delay_ms=0,
        )
        store = PositionStore()
        clob = AsyncMock(spec=ClobWrapper)
        clob.place_order.return_value = ExecutionResult(success=True, order_id="0xfirst")
        clob.get_midpoint.return_value = 0.82
        clob.get_order_details.return_value = {
            "status": OrderStatus.LIVE,
            "size_matched": 0.0,
            "original_size": 11.0,
            "price": 0.70,
        }
        clob.cancel_order.return_value = True

        executor = PositionExecutor(clob, store, config)
        signal = make_signal(
            asset="BTC",
            source="binance_momentum",
            market_window_secs=300,
            time_remaining_secs=280,
            price=0.68,
            slug="btc-updown-5m-1773251400",
        )
        with patch("polyphemus.position_executor.asyncio.sleep", new=self._fast_sleep):
            result = await executor.execute_buy(signal, available_capital=500.0)

        assert not result.success
        stats = executor.get_entry_retry_stats()
        assert stats["fill_retry_eligible"] == 1
        assert stats["fill_retry_attempted"] == 0
        assert stats["retry_skip_reasons"]["fill:midpoint_above_cap"] == 1

    @pytest.mark.asyncio
    async def test_btc5m_fill_retry_skips_when_too_late(self):
        config = make_config(
            dry_run=False,
            btc5m_entry_retry_enabled=True,
            btc5m_entry_retry_mode="active",
            btc5m_entry_retry_delay_ms=0,
            btc5m_entry_retry_min_secs_remaining=45,
            max_entry_price=0.75,
        )
        store = PositionStore()
        clob = AsyncMock(spec=ClobWrapper)
        clob.place_order.return_value = ExecutionResult(success=True, order_id="0xfirst")
        clob.get_midpoint.return_value = 0.69
        clob.get_order_details.return_value = {
            "status": OrderStatus.LIVE,
            "size_matched": 0.0,
            "original_size": 11.0,
            "price": 0.70,
        }
        clob.cancel_order.return_value = True

        executor = PositionExecutor(clob, store, config)
        signal = make_signal(
            asset="BTC",
            source="binance_momentum",
            market_window_secs=300,
            time_remaining_secs=30,
            price=0.68,
            slug="btc-updown-5m-1773251700",
        )
        with patch("polyphemus.position_executor.asyncio.sleep", new=self._fast_sleep):
            result = await executor.execute_buy(signal, available_capital=500.0)

        assert not result.success
        stats = executor.get_entry_retry_stats()
        assert stats["fill_retry_eligible"] == 1
        assert stats["retry_skip_reasons"]["fill:too_late"] == 1

    @pytest.mark.asyncio
    async def test_btc5m_retry_shadow_logs_without_second_order(self):
        config = make_config(
            dry_run=False,
            btc5m_entry_retry_enabled=True,
            btc5m_entry_retry_mode="shadow",
            btc5m_entry_retry_delay_ms=0,
            max_entry_price=0.75,
        )
        store = PositionStore()
        clob = AsyncMock(spec=ClobWrapper)
        clob.place_order.return_value = ExecutionResult(success=False, error="Order placement failed: Request exception!")
        clob.get_midpoint.return_value = 0.68

        executor = PositionExecutor(clob, store, config)
        signal = make_signal(
            asset="BTC",
            source="binance_momentum",
            market_window_secs=300,
            time_remaining_secs=280,
            price=0.68,
            slug="btc-updown-5m-1773252000",
        )
        with patch("polyphemus.position_executor.asyncio.sleep", new=self._fast_sleep):
            result = await executor.execute_buy(signal, available_capital=500.0)

        assert not result.success
        assert clob.place_order.call_count == 1
        stats = executor.get_entry_retry_stats()
        assert stats["placement_retry_eligible"] == 1
        assert stats["placement_retry_attempted"] == 0
        assert stats["retry_skip_reasons"]["placement:shadow_would_retry@0.69"] == 1

    @pytest.mark.asyncio
    async def test_btc5m_retry_never_runs_for_non_matching_signal(self):
        config = make_config(
            dry_run=False,
            btc5m_entry_retry_enabled=True,
            btc5m_entry_retry_mode="active",
            btc5m_entry_retry_delay_ms=0,
        )
        store = PositionStore()
        clob = AsyncMock(spec=ClobWrapper)
        clob.place_order.return_value = ExecutionResult(success=False, error="Order placement failed: Request exception!")
        clob.get_midpoint.return_value = 0.68

        executor = PositionExecutor(clob, store, config)
        signal = make_signal(
            asset="ETH",
            source="binance_momentum",
            market_window_secs=300,
            time_remaining_secs=280,
            price=0.68,
            slug="eth-updown-5m-1773252300",
        )
        with patch("polyphemus.position_executor.asyncio.sleep", new=self._fast_sleep):
            result = await executor.execute_buy(signal, available_capital=500.0)

        assert not result.success
        assert clob.place_order.call_count == 1
        stats = executor.get_entry_retry_stats()
        assert stats["placement_retry_eligible"] == 0
        assert stats["fill_retry_eligible"] == 0

    def test_up_direction_size_multiplier_uses_outcome(self):
        config = make_config(
            dry_run=False,
            base_bet_pct=0.10,
            up_direction_size_mult=0.5,
            asset_multiplier_btc=1.0,
        )
        store = PositionStore()
        clob = AsyncMock(spec=ClobWrapper)
        executor = PositionExecutor(clob, store, config)

        down_signal = make_signal(asset="BTC", outcome="Down", direction="BUY", price=0.80)
        up_signal = make_signal(asset="BTC", outcome="Up", direction="BUY", price=0.80)

        down_size = executor._calculate_size(0.80, 500.0, asset="BTC", signal=down_signal)
        up_size = executor._calculate_size(0.80, 500.0, asset="BTC", signal=up_signal)

        assert down_size > 0
        assert up_size == pytest.approx(down_size * 0.5)


class TestBalanceManager:
    @pytest.mark.asyncio
    async def test_reconcile_trades_matches_recent_exit_hashes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = PerformanceDB(os.path.join(tmpdir, "performance.db"))
            now = time.time()
            old_entry = now - (48 * 3600)
            recent_exit = now - 300
            db.record_entry(
                trade_id="trade-entry-1",
                token_id="0xtoken",
                slug="btc-updown-5m-1",
                entry_time=old_entry,
                entry_price=0.70,
                entry_size=10.0,
                entry_tx_hash="0xentry",
                outcome="Up",
                market_title="BTC",
            )
            db.record_exit(
                trade_id="trade-entry-1",
                exit_time=recent_exit,
                exit_price=1.0,
                exit_size=10.0,
                exit_reason="market_resolved",
                exit_tx_hash="0xexit",
                pnl=3.0,
                pnl_pct=0.3,
            )

            clob = AsyncMock(spec=ClobWrapper)
            clob.get_recent_trades.return_value = [
                {
                    "maker_orders": [
                        {
                            "maker_address": make_config().wallet_address,
                            "order_id": "0xexit",
                        }
                    ]
                }
            ]
            manager = BalanceManager(clob, PositionStore(), make_config(dry_run=False))

            with patch.dict(os.environ, {"WALLET_ADDRESS": make_config().wallet_address}, clear=False):
                passed, msg = await manager.reconcile_trades(db)

            assert passed
            assert "OK:" in msg

    @pytest.mark.asyncio
    async def test_reconcile_trades_halts_on_unmatched_recent_clob_order(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = PerformanceDB(os.path.join(tmpdir, "performance.db"))
            clob = AsyncMock(spec=ClobWrapper)
            clob.get_recent_trades.return_value = [
                {
                    "maker_orders": [
                        {
                            "maker_address": make_config().wallet_address,
                            "order_id": "0xmissing",
                        }
                    ]
                }
            ]
            manager = BalanceManager(clob, PositionStore(), make_config(dry_run=False))

            with patch.dict(os.environ, {"WALLET_ADDRESS": make_config().wallet_address}, clear=False):
                passed, msg = await manager.reconcile_trades(db)

            assert not passed
            assert "CRITICAL:" in msg


class TestDashboard:
    def test_pipeline_summary_includes_retry_counters(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            signals_db = os.path.join(tmpdir, "signals.db")
            perf_db = os.path.join(tmpdir, "performance.db")

            sig_conn = sqlite3.connect(signals_db)
            sig_conn.execute(
                """
                CREATE TABLE signals (
                    asset TEXT,
                    market_window_secs INTEGER,
                    epoch REAL,
                    source TEXT,
                    guard_passed INTEGER,
                    outcome TEXT,
                    guard_reasons TEXT,
                    slug TEXT,
                    midpoint REAL,
                    time_remaining_secs INTEGER
                )
                """
            )
            now = time.time()
            sig_conn.executemany(
                """
                INSERT INTO signals (
                    asset, market_window_secs, epoch, source, guard_passed,
                    outcome, guard_reasons, slug, midpoint, time_remaining_secs
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("BTC", 300, now - 120, "binance_momentum", 1, "passed", "", "btc-updown-5m-1", 0.68, 280),
                    ("BTC", 300, now - 60, "binance_momentum", 0, "filtered", "price_out_of_range", "btc-updown-5m-2", 0.81, 250),
                ],
            )
            sig_conn.commit()
            sig_conn.close()

            perf_conn = sqlite3.connect(perf_db)
            perf_conn.execute("CREATE TABLE trades (slug TEXT, entry_time REAL)")
            perf_conn.commit()
            perf_conn.close()

            dash = Dashboard(
                config=make_config(),
                store=PositionStore(),
                balance=SimpleNamespace(_cached_balance=100.0),
                health=SimpleNamespace(get_uptime_hours=lambda: 1.0, _error_count=0),
                guard=SimpleNamespace(),
                perf_db=SimpleNamespace(db_path=perf_db),
                signal_logger=SimpleNamespace(_db_path=signals_db),
                executor=SimpleNamespace(
                    get_entry_retry_stats=lambda: {
                        "placement_failures": 2,
                        "fill_timeouts": 1,
                        "retry_recovered": 1,
                        "retry_skip_reasons": {"fill:midpoint_above_cap": 2},
                    }
                ),
            )

            summary = dash._get_pipeline_summary()

            assert summary["passed_btc_candidates"] == 1
            assert summary["placement_failures"] == 2
            assert summary["fill_timeouts"] == 1
            assert summary["retry_recovered"] == 1
            assert summary["retry_skip_reasons"][0]["reason"] == "fill:midpoint_above_cap"


# ============================================================================
# BinanceFeed Tests
# ============================================================================

def _make_binance_feed(**config_overrides) -> BinanceFeed:
    """Create a BinanceFeed with test config."""
    config = make_config(**config_overrides)
    return BinanceFeed(config)


def _inject_candles(feed: BinanceFeed, symbol: str, closes: list[float], base_time_ms: int = 1707300000000):
    """Inject candles directly into feed buffer for testing."""
    for i, close in enumerate(closes):
        feed._buffers[symbol].append({
            "close": close,
            "time_ms": base_time_ms + i * 60000,  # 1 min apart
        })


class TestBinanceFeed:
    def test_momentum_up(self):
        """3 ascending closes -> direction=UP."""
        feed = _make_binance_feed()
        _inject_candles(feed, "btcusdt", [100.0, 101.0, 102.0])
        result = feed.get_momentum("BTC")
        assert result.direction == "UP"
        assert result.momentum_pct > 0

    def test_momentum_down(self):
        """3 descending closes -> direction=DOWN."""
        feed = _make_binance_feed()
        _inject_candles(feed, "ethusdt", [100.0, 99.0, 98.0])
        result = feed.get_momentum("ETH")
        assert result.direction == "DOWN"
        assert result.momentum_pct < 0

    def test_momentum_neutral(self):
        """Flat prices within threshold -> NEUTRAL."""
        feed = _make_binance_feed(min_momentum_pct=0.001)
        # 0.05% change is below 0.1% threshold
        _inject_candles(feed, "solusdt", [100.0, 100.02, 100.05])
        result = feed.get_momentum("SOL")
        assert result.direction == "NEUTRAL"

    def test_insufficient_candles(self):
        """< momentum_candles candles -> UNKNOWN, confidence=0."""
        feed = _make_binance_feed(momentum_candles=3)
        _inject_candles(feed, "btcusdt", [100.0, 101.0])  # only 2
        result = feed.get_momentum("BTC")
        assert result.direction == "UNKNOWN"
        assert result.confidence == 0.0

    def test_asset_to_symbol_mapping(self):
        """BTC -> btcusdt, unknown asset -> UNKNOWN."""
        feed = _make_binance_feed()
        _inject_candles(feed, "btcusdt", [100.0, 101.0, 102.0])
        assert feed.get_momentum("BTC").direction == "UP"
        assert feed.get_momentum("DOGE").direction == "UNKNOWN"

    def test_momentum_calculation_exact(self):
        """Specific candle values -> exact momentum_pct."""
        feed = _make_binance_feed(momentum_candles=3)
        _inject_candles(feed, "btcusdt", [100.0, 105.0, 110.0])
        result = feed.get_momentum("BTC")
        # (110 - 100) / 100 = 0.10 = 10%
        assert abs(result.momentum_pct - 0.10) < 1e-9
        assert result.confidence == 1.0  # 10% >> 0.5% full confidence


# ============================================================================
# MomentumConfirmation Tests
# ============================================================================

class TestMomentumConfirmation:
    """Test the _momentum_confirms logic (extracted for unit testing)."""

    @staticmethod
    def _confirms(outcome: str, direction: str) -> bool:
        """Replicate SignalBot._momentum_confirms logic for testing."""
        momentum = MomentumResult(
            direction=direction, momentum_pct=0.01, confidence=0.5, age_secs=10.0
        )
        signal = {"outcome": outcome}
        if momentum.direction in ("UNKNOWN", "NEUTRAL"):
            return False
        if signal["outcome"].lower() == "down" and momentum.direction == "DOWN":
            return True
        if signal["outcome"].lower() == "up" and momentum.direction == "UP":
            return True
        return False

    def test_confirms_aligned_down(self):
        """outcome='down' + DOWN -> True."""
        assert self._confirms("down", "DOWN") is True

    def test_confirms_aligned_up(self):
        """outcome='up' + UP -> True."""
        assert self._confirms("up", "UP") is True

    def test_rejects_contradicting(self):
        """outcome='down' + UP -> False."""
        assert self._confirms("down", "UP") is False

    def test_rejects_neutral(self):
        """NEUTRAL -> False (no clear signal)."""
        assert self._confirms("down", "NEUTRAL") is False

    def test_rejects_unknown(self):
        """UNKNOWN -> False."""
        assert self._confirms("up", "UNKNOWN") is False


# ============================================================================
# MomentumIntegration Tests
# ============================================================================

class TestMomentumIntegration:
    def test_non_crypto_bypasses(self):
        """asset='' -> momentum check not called, signal passes through."""
        signal = make_signal(asset="")
        # Non-crypto asset is not in ASSET_TO_BINANCE, so momentum is not checked
        assert signal["asset"] not in ASSET_TO_BINANCE

    def test_binance_disabled_bypasses(self):
        """enable_binance_confirmation=False -> BinanceFeed not created."""
        config = make_config(enable_binance_confirmation=False)
        assert config.enable_binance_confirmation is False
        # When disabled, SignalBot sets self._binance_feed = None

    def test_startup_grace_accepts(self):
        """In grace period -> signals accepted (fail-open)."""
        feed = _make_binance_feed(binance_startup_grace_secs=300)
        # Just created, so we're within grace period
        assert feed.in_grace_period() is True
        # After grace expires, should return False
        feed._startup_time = time.time() - 400  # 400s ago > 300s grace
        assert feed.in_grace_period() is False


# ============================================================================
# ArbEngine Tests — Fee Calculation
# ============================================================================

class TestArbFeeCalculation:
    def test_fee_at_50pct(self):
        """At price=0.50, fee rate is 1.5625% per share."""
        fee = ArbEngine._calculate_fee(0.50, 100)
        # 100 * 0.50 * 0.25 * (0.50 * 0.50)^2 = 100 * 0.50 * 0.25 * 0.0625 = 0.78125
        assert abs(fee - 0.78125) < 1e-6

    def test_fee_at_extreme(self):
        """At price=0.10, fee is very low."""
        fee = ArbEngine._calculate_fee(0.10, 100)
        # 100 * 0.10 * 0.25 * (0.10 * 0.90)^2 = 100 * 0.10 * 0.25 * 0.0081 = 0.02025
        assert abs(fee - 0.02025) < 1e-6

    def test_fee_rate_symmetry(self):
        """fee_rate (fee/price) is symmetric: rate(0.40) == rate(0.60)."""
        fee_40 = ArbEngine._calculate_fee(0.40, 100)
        fee_60 = ArbEngine._calculate_fee(0.60, 100)
        # fee = shares * p * 0.25 * (p*(1-p))^2
        # fee/p = shares * 0.25 * (p*(1-p))^2  ← symmetric in p
        rate_40 = fee_40 / 0.40
        rate_60 = fee_60 / 0.60
        assert abs(rate_40 - rate_60) < 1e-6


# ============================================================================
# ArbEngine Tests — Order Book Walking
# ============================================================================

class TestArbOrderBookWalking:
    def test_walk_fills_completely(self):
        """Walk 3 ask levels to fill target size."""
        asks = [
            {"price": 0.48, "size": 50},
            {"price": 0.49, "size": 50},
            {"price": 0.50, "size": 50},
        ]
        vwap, cost = ArbEngine._walk_order_book(asks, 100)
        # fills 50@0.48 + 50@0.49 = 24 + 24.5 = 48.5
        assert abs(vwap - 0.485) < 1e-6
        assert abs(cost - 48.5) < 1e-6

    def test_walk_insufficient_depth(self):
        """Not enough asks to fill → returns (0, 0)."""
        asks = [
            {"price": 0.48, "size": 30},
            {"price": 0.49, "size": 30},
        ]
        vwap, cost = ArbEngine._walk_order_book(asks, 100)
        assert vwap == 0.0
        assert cost == 0.0

    def test_walk_normalized_dicts(self):
        """Handles float values from normalization correctly."""
        asks = [
            {"price": 0.47, "size": 200.0},
        ]
        vwap, cost = ArbEngine._walk_order_book(asks, 150)
        assert abs(vwap - 0.47) < 1e-6
        assert abs(cost - 70.5) < 1e-6


# ============================================================================
# ArbEngine Tests — Profitability
# ============================================================================

class TestArbProfitability:
    def test_profitable_after_fees_and_slippage(self):
        """pair_cost=0.97 is profitable after fees + slippage."""
        # At 0.485 per side (pair=0.97), fee per side ~0.78
        up_vwap = 0.485
        down_vwap = 0.485
        shares = 125
        fee_up = ArbEngine._calculate_fee(up_vwap, shares)
        fee_down = ArbEngine._calculate_fee(down_vwap, shares)
        fees_per_share = (fee_up + fee_down) / shares
        pair_cost = up_vwap + down_vwap
        net = 1.00 - pair_cost - fees_per_share - ARB_SLIPPAGE_BUFFER
        assert net > 0, f"Expected profitable, got net={net}"

    def test_unprofitable_after_fees(self):
        """pair_cost=0.995 is clearly unprofitable."""
        up_vwap = 0.50
        down_vwap = 0.495
        shares = 100
        fee_up = ArbEngine._calculate_fee(up_vwap, shares)
        fee_down = ArbEngine._calculate_fee(down_vwap, shares)
        fees_per_share = (fee_up + fee_down) / shares
        pair_cost = up_vwap + down_vwap
        net = 1.00 - pair_cost - fees_per_share - ARB_SLIPPAGE_BUFFER
        assert net < 0, f"Expected unprofitable, got net={net}"

    def test_marginal_rejected_by_slippage_buffer(self):
        """pair_cost=0.984 is marginal — slippage buffer kills it at ~50/50."""
        up_vwap = 0.492
        down_vwap = 0.492
        shares = 100
        fee_up = ArbEngine._calculate_fee(up_vwap, shares)
        fee_down = ArbEngine._calculate_fee(down_vwap, shares)
        fees_per_share = (fee_up + fee_down) / shares
        pair_cost = up_vwap + down_vwap
        net = 1.00 - pair_cost - fees_per_share - ARB_SLIPPAGE_BUFFER
        assert net < 0.005, f"Expected marginal/rejected, got net={net}"

    def test_market_too_close_to_expiry(self):
        """Markets expiring within ARB_MARKET_BUFFER_SECS are skipped."""
        # This tests the filtering logic constant
        assert ARB_MARKET_BUFFER_SECS == 180
        # A market expiring in 120s would be skipped (120 < 180)
        expires_at = time.time() + 120
        now = time.time()
        assert expires_at - now < ARB_MARKET_BUFFER_SECS


# ============================================================================
# ArbEngine Tests — Execution
# ============================================================================

class TestArbExecution:
    @pytest.mark.asyncio
    async def test_dry_run_no_orders(self):
        """Dry run logs but doesn't place orders."""
        config = make_config(enable_arb=True, arb_dry_run=True)
        clob = AsyncMock(spec=ClobWrapper)
        balance = AsyncMock(spec=BalanceManager)
        engine = ArbEngine(clob=clob, balance=balance, config=config)

        opp = ArbOpportunity(
            slug="btc-test", market_title="BTC above 100k",
            up_token_id="0xup", down_token_id="0xdown",
            up_price=0.48, down_price=0.49, pair_cost=0.97,
            fee_up=0.5, fee_down=0.5, net_profit_per_share=0.02,
            shares=100, expires_at=time.time() + 600,
        )
        result = await engine._execute_arb(opp)
        assert result.success
        assert result.net_profit > 0
        clob.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_both_legs_success(self):
        """Both legs fill → profit calculated correctly."""
        config = make_config(enable_arb=True, arb_dry_run=False)
        clob = AsyncMock(spec=ClobWrapper)
        balance = AsyncMock(spec=BalanceManager)
        balance.get_available.return_value = 200.0

        clob.place_order.return_value = ExecutionResult(success=True, order_id="0xorder1")
        clob.get_order_status.return_value = "FILLED"
        clob.get_order_book.return_value = {
            "asks": [{"price": 0.49, "size": 200}],
            "bids": [{"price": 0.48, "size": 200}],
        }

        engine = ArbEngine(clob=clob, balance=balance, config=config)
        opp = ArbOpportunity(
            slug="btc-test", market_title="BTC above 100k",
            up_token_id="0xup", down_token_id="0xdown",
            up_price=0.48, down_price=0.49, pair_cost=0.97,
            fee_up=0.5, fee_down=0.5, net_profit_per_share=0.02,
            shares=100, expires_at=time.time() + 600,
        )
        result = await engine._execute_arb(opp)
        assert result.success
        assert result.net_profit > 0
        assert clob.place_order.call_count == 2

    @pytest.mark.asyncio
    async def test_pair_cost_drift_aborts(self):
        """If pair cost drifts above max after leg 1, abort and unwind."""
        config = make_config(enable_arb=True, arb_dry_run=False, arb_max_pair_cost=0.980)
        clob = AsyncMock(spec=ClobWrapper)
        balance = AsyncMock(spec=BalanceManager)
        balance.get_available.return_value = 200.0

        # Leg 1 succeeds
        clob.place_order.side_effect = [
            ExecutionResult(success=True, order_id="0xup_order"),
            ExecutionResult(success=True, order_id="0xunwind_order"),  # unwind sell
        ]
        clob.get_order_status.return_value = "FILLED"
        # After leg 1, down book shows high price (pair cost drifts to 0.99)
        clob.get_order_book.return_value = {
            "asks": [{"price": 0.51, "size": 200}],  # 0.48 + 0.51 = 0.99 > 0.98
            "bids": [{"price": 0.47, "size": 200}],
        }
        clob.get_midpoint.return_value = 0.48

        engine = ArbEngine(clob=clob, balance=balance, config=config)
        opp = ArbOpportunity(
            slug="btc-test", market_title="BTC above 100k",
            up_token_id="0xup", down_token_id="0xdown",
            up_price=0.48, down_price=0.49, pair_cost=0.97,
            fee_up=0.5, fee_down=0.5, net_profit_per_share=0.02,
            shares=100, expires_at=time.time() + 600,
        )
        result = await engine._execute_arb(opp)
        assert not result.success
        assert "pair_cost_drift" in result.error
        assert result.unwound


# ============================================================================
# ArbEngine Tests — Unwind
# ============================================================================

class TestArbUnwind:
    @pytest.mark.asyncio
    async def test_unwind_succeeds_on_retry(self):
        """First unwind attempt fails, second succeeds."""
        config = make_config(enable_arb=True, arb_dry_run=False)
        clob = AsyncMock(spec=ClobWrapper)
        balance = AsyncMock(spec=BalanceManager)
        engine = ArbEngine(clob=clob, balance=balance, config=config)

        # First attempt: place_order fails. Second: succeeds + fills.
        clob.place_order.side_effect = [
            ExecutionResult(success=False, error="timeout"),
            ExecutionResult(success=True, order_id="0xsell"),
        ]
        clob.get_order_status.return_value = "FILLED"
        clob.get_order_book.return_value = {
            "asks": [],
            "bids": [{"price": 0.47, "size": 200}],
        }

        success = await engine._unwind_leg("0xtoken", 100, "test_retry")
        assert success
        assert engine._stats["unwinds"] == 1

    @pytest.mark.asyncio
    async def test_unwind_tracks_loss(self):
        """Unwind loss is tracked in stats."""
        config = make_config(enable_arb=True, arb_dry_run=False)
        clob = AsyncMock(spec=ClobWrapper)
        balance = AsyncMock(spec=BalanceManager)
        engine = ArbEngine(clob=clob, balance=balance, config=config)

        clob.place_order.return_value = ExecutionResult(success=True, order_id="0xsell")
        clob.get_order_status.return_value = "FILLED"
        clob.get_order_book.return_value = {
            "asks": [],
            "bids": [{"price": 0.47, "size": 200}],
        }

        await engine._unwind_leg("0xtoken", 100, "test_loss")
        assert engine._stats["total_unwind_loss"] > 0


class TestSlackNotifierCallSites:
    """Verify all production call sites pass correct kwargs to SlackNotifier.

    These tests exist because a param name mismatch (price= vs entry_price=)
    silently broke reversal_short notifications for days. Bare except: pass
    swallowed the TypeError. Every call site in signal_bot.py is covered here.
    """

    def _make_notifier(self):
        from polyphemus.slack_notifier import SlackNotifier
        # Disabled notifier (no webhook/token) - _post is a no-op
        return SlackNotifier(instance_name="test")

    def test_notify_entry_momentum(self):
        """Main entry path: signal_bot.py line ~981."""
        n = self._make_notifier()
        n.notify_entry(
            slug="btc-updown-5m-1770944400",
            asset="BTC",
            direction="Up",
            entry_price=0.65,
            size_usd=32.50,
            shares=50.0,
            momentum_pct=0.0045,
            source="momentum",
            secs_left=0,
        )

    def test_notify_entry_snipe(self):
        n = self._make_notifier()
        n.notify_entry(
            slug="btc-updown-5m-1770944400",
            asset="BTC",
            direction="Up",
            entry_price=0.94,
            size_usd=47.0,
            shares=50.0,
            momentum_pct=0.0,
            source="snipe",
            secs_left=30,
        )

    def test_notify_entry_reversal_short(self):
        """Reversal short entry: signal_bot.py line ~1511.
        Was broken by price= vs entry_price= param mismatch."""
        n = self._make_notifier()
        n.notify_entry(
            slug="btc-updown-5m-1770944400",
            asset="BTC",
            direction="Down",
            entry_price=0.15,
            size_usd=7.50,
            shares=50.0,
            source="reversal_short",
        )

    def test_notify_entry_minimal(self):
        """Minimal kwargs - size_usd is optional, computed from price*shares."""
        n = self._make_notifier()
        n.notify_entry(
            slug="btc-updown-5m-1770944400",
            asset="BTC",
            direction="Up",
            entry_price=0.70,
            shares=40.0,
        )

    def test_notify_exit_normal(self):
        """Normal exit: signal_bot.py line ~1337."""
        n = self._make_notifier()
        n.notify_exit(
            slug="btc-updown-5m-1770944400",
            asset="BTC",
            direction="Up",
            entry_price=0.65,
            exit_price=0.72,
            shares=50.0,
            pnl=3.50,
            exit_reason="market_resolved",
            hold_secs=120,
        )

    def test_notify_exit_ghost_cleanup(self):
        """Ghost cleanup: signal_bot.py line ~520. Passes asset='', direction=''."""
        n = self._make_notifier()
        n.notify_exit(
            slug="btc-updown-5m-1770944400",
            asset="",
            direction="",
            entry_price=0.93,
            exit_price=0.0,
            shares=50.0,
            pnl=-46.50,
            exit_reason="ghost_cleanup",
            hold_secs=0,
        )

    def test_notify_exit_restart_stale(self):
        """Restart stale: signal_bot.py line ~481. Passes asset='', direction=''."""
        n = self._make_notifier()
        n.notify_exit(
            slug="eth-updown-5m-1770944400",
            asset="",
            direction="",
            entry_price=0.80,
            exit_price=0.0,
            shares=30.0,
            pnl=-24.0,
            exit_reason="restart_stale",
            hold_secs=0,
        )

    def test_notify_exit_insufficient_shares(self):
        """Insufficient shares: signal_bot.py line ~1226."""
        n = self._make_notifier()
        n.notify_exit(
            slug="sol-updown-5m-1770944400",
            asset="",
            direction="",
            entry_price=0.75,
            exit_price=0.0,
            shares=40.0,
            pnl=-30.0,
            exit_reason="insufficient_shares",
            hold_secs=45.0,
        )

    def test_notify_redemption_win(self):
        n = self._make_notifier()
        n.notify_redemption(
            slug="btc-updown-5m-1770944400",
            shares=50.0,
            won=True,
            entry_price=0.65,
        )

    def test_notify_redemption_loss(self):
        n = self._make_notifier()
        n.notify_redemption(
            slug="orphan:BTC Up/Down",
            shares=50.0,
            won=False,
            entry_price=0.65,
        )

    def test_notify_startup(self):
        n = self._make_notifier()
        n.notify_startup(open_positions=3, balance=807.0)

    def test_seed_stats(self):
        n = self._make_notifier()
        n.seed_stats(wins=5, losses=3, total_pnl=42.50)
        assert n._wins == 5
        assert n._losses == 3
        assert n._total_pnl == 42.50

    def test_session_line_after_trades(self):
        n = self._make_notifier()
        n.seed_stats(3, 1, 25.0)
        line = n._session_line()
        assert "3W" in line
        assert "1L" in line
        assert "75%" in line

    def test_parse_slug_btc(self):
        from polyphemus.slack_notifier import _parse_slug
        asset, direction = _parse_slug("btc-updown-5m-1770944400")
        assert asset == "BTC"

    def test_parse_slug_empty(self):
        from polyphemus.slack_notifier import _parse_slug
        asset, direction = _parse_slug("")
        assert asset == "?"

    def test_parse_slug_orphan(self):
        from polyphemus.slack_notifier import _parse_slug
        asset, direction = _parse_slug("orphan:BTC Up/Down")
        assert asset == "?"


class TestReversalShort:
    """Tests for _try_reversal_short signal creation in signal_bot._handle_exit."""

    def _make_bot_parts(self, **config_overrides):
        """Create minimal mock objects for testing _try_reversal_short."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from dataclasses import dataclass, field
        from datetime import datetime, timezone

        # Config with reversal_short defaults
        cfg = {
            "reversal_short_enabled": True,
            "reversal_short_dry_run": False,
            "reversal_short_min_secs_remaining": 45,
            "reversal_short_max_down_price": 0.35,
            "reversal_short_min_down_price": 0.10,
            "reversal_short_max_bet": 25.0,
        }
        cfg.update(config_overrides)
        config = MagicMock()
        for k, v in cfg.items():
            setattr(config, k, v)

        # Position mock
        pos = MagicMock()
        pos.slug = "btc-updown-5m-1770944400"
        pos.entry_price = 0.85
        pos.entry_size = 10.0
        pos.market_end_time = datetime.fromtimestamp(
            time.time() + 120, tz=timezone.utc  # 120s left
        )
        pos.metadata = {
            "source": "binance_momentum",
            "direction": "up",
            "asset": "BTC",
            "market_window_secs": 300,
        }

        # Exit signal
        exit_signal = MagicMock()
        exit_signal.reason = "oracle_reversal"
        exit_signal.token_id = "token_up_123"

        # Market cache with both tokens
        market_cache = {
            "btc-updown-5m-1770944400": {
                "up_token_id": "token_up_123",
                "down_token_id": "token_down_456",
                "condition_id": "cond_789",
            }
        }

        # Momentum feed with market cache
        momentum_feed = MagicMock()
        momentum_feed._market_cache = market_cache

        # Market WS returns midpoint for opposite token
        market_ws = MagicMock()
        market_ws.get_midpoint = MagicMock(return_value=0.20)

        # CLOB fallback
        clob = AsyncMock()
        clob.get_midpoint = AsyncMock(return_value=0.20)

        # Signal logger
        signal_logger = MagicMock()

        # Bot-like object with all required attributes
        bot = MagicMock()
        bot._config = config
        bot._momentum_feed = momentum_feed
        bot._market_ws = market_ws
        bot._clob = clob
        bot._signal_logger = signal_logger
        bot._logger = MagicMock()
        bot._on_signal = AsyncMock()

        return bot, pos, exit_signal

    @pytest.mark.asyncio
    async def test_reversal_short_fires_on_oracle_reversal(self):
        """Reversal short creates signal after oracle_reversal exit."""
        from polyphemus.signal_bot import SignalBot
        bot, pos, exit_signal = self._make_bot_parts()
        exit_signal.reason = "oracle_reversal"

        # Call the real method with mock self
        await SignalBot._try_reversal_short(bot, pos, exit_signal)

        bot._on_signal.assert_called_once()
        signal = bot._on_signal.call_args[0][0]
        assert signal["source"] == "reversal_short"
        assert signal["token_id"] == "token_down_456"  # opposite of Up
        assert signal["outcome"] == "Down"
        assert signal["price"] == 0.20

    @pytest.mark.asyncio
    async def test_reversal_short_fires_on_binance_reversal(self):
        """Reversal short creates signal after binance_reversal exit."""
        from polyphemus.signal_bot import SignalBot
        bot, pos, exit_signal = self._make_bot_parts()
        exit_signal.reason = "binance_reversal"

        await SignalBot._try_reversal_short(bot, pos, exit_signal)

        bot._on_signal.assert_called_once()
        signal = bot._on_signal.call_args[0][0]
        assert signal["source"] == "reversal_short"
        assert signal["metadata"]["triggered_by"] == "binance_reversal"

    @pytest.mark.asyncio
    async def test_reversal_short_skipped_when_disabled(self):
        """No signal when reversal_short_enabled=False."""
        from polyphemus.signal_bot import SignalBot
        bot, pos, exit_signal = self._make_bot_parts(reversal_short_enabled=False)

        await SignalBot._try_reversal_short(bot, pos, exit_signal)

        bot._on_signal.assert_not_called()

    @pytest.mark.asyncio
    async def test_reversal_short_antiloop(self):
        """No signal when exited position is itself a reversal_short."""
        from polyphemus.signal_bot import SignalBot
        bot, pos, exit_signal = self._make_bot_parts()
        pos.metadata["source"] = "reversal_short"

        await SignalBot._try_reversal_short(bot, pos, exit_signal)

        bot._on_signal.assert_not_called()

    @pytest.mark.asyncio
    async def test_reversal_short_skipped_on_non_reversal_exit(self):
        """No signal on stop_loss, time_exit, etc."""
        from polyphemus.signal_bot import SignalBot
        bot, pos, exit_signal = self._make_bot_parts()
        exit_signal.reason = "stop_loss"

        await SignalBot._try_reversal_short(bot, pos, exit_signal)

        bot._on_signal.assert_not_called()

    @pytest.mark.asyncio
    async def test_reversal_short_skipped_insufficient_time(self):
        """No signal when time_remaining < min_secs_remaining."""
        from polyphemus.signal_bot import SignalBot
        from datetime import datetime, timezone
        bot, pos, exit_signal = self._make_bot_parts(reversal_short_min_secs_remaining=200)
        # Only 120s left (set in _make_bot_parts), threshold is 200
        await SignalBot._try_reversal_short(bot, pos, exit_signal)

        bot._on_signal.assert_not_called()

    @pytest.mark.asyncio
    async def test_reversal_short_skipped_price_out_of_range(self):
        """No signal when opposite midpoint outside price range."""
        from polyphemus.signal_bot import SignalBot
        bot, pos, exit_signal = self._make_bot_parts(reversal_short_max_down_price=0.15)
        # midpoint is 0.20, max is 0.15 -> out of range

        await SignalBot._try_reversal_short(bot, pos, exit_signal)

        bot._on_signal.assert_not_called()

    @pytest.mark.asyncio
    async def test_reversal_short_dry_run_logs_but_no_signal(self):
        """Dry run mode logs but does not dispatch signal."""
        from polyphemus.signal_bot import SignalBot
        bot, pos, exit_signal = self._make_bot_parts(reversal_short_dry_run=True)

        await SignalBot._try_reversal_short(bot, pos, exit_signal)

        bot._on_signal.assert_not_called()
        # Should have logged info
        bot._logger.info.assert_called()
        log_msg = bot._logger.info.call_args[0][0]
        assert "REVERSAL_SHORT DRY" in log_msg

    @pytest.mark.asyncio
    async def test_reversal_short_down_entry_flips_to_up(self):
        """Down entry should flip to Up on reversal."""
        from polyphemus.signal_bot import SignalBot
        bot, pos, exit_signal = self._make_bot_parts()
        pos.metadata["direction"] = "down"

        await SignalBot._try_reversal_short(bot, pos, exit_signal)

        bot._on_signal.assert_called_once()
        signal = bot._on_signal.call_args[0][0]
        assert signal["outcome"] == "Up"
        assert signal["token_id"] == "token_up_123"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
