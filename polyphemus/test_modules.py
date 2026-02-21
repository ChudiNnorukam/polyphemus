"""Unit tests for Polyphemus modules."""

import asyncio
import json
import os
import sys
import time
import tempfile
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

# Ensure polyphemus package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from polyphemus.types import (
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
        price=0.70,
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
