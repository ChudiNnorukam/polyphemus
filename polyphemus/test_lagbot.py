"""Tests for lagbot-critical modules: BinanceMomentumFeed, CircuitBreaker,
BalanceManager, FillOptimizer, ExitHandler."""

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from polyphemus.models import (
    Position, ExitSignal, ExitReason, ExecutionResult, OrderStatus,
    BALANCE_CACHE_TTL, TAKER_POLL_MAX, MIN_SHARES_FOR_SELL,
    ASSET_TO_BINANCE, BINANCE_SYMBOLS, parse_window_from_slug,
)
from polyphemus.config import Settings
from polyphemus.circuit_breaker import KillSwitch, DailyLossMonitor, StreakTracker, CircuitBreaker
from polyphemus.balance_manager import BalanceManager
from polyphemus.fill_optimizer import FillOptimizer, ArmState
from polyphemus.exit_handler import ExitHandler
from polyphemus.binance_momentum import BinanceMomentumFeed, BINANCE_TO_ASSET
from polyphemus.signal_guard import SignalGuard


def make_config(**overrides):
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


def _order_details(status, size_matched=100.0, original_size=100.0, price=0.50):
    """Helper to create get_order_details mock return value."""
    return {"status": status, "size_matched": size_matched, "original_size": original_size, "price": price}


def make_position(**overrides):
    now = datetime.now(timezone.utc)
    defaults = dict(
        token_id="0xtoken001",
        slug="btc-updown-5m-123456",
        entry_price=0.68,
        entry_size=100.0,
        entry_time=now,
        entry_tx_hash="0xhash001",
        market_end_time=now + timedelta(days=7),
    )
    defaults.update(overrides)
    return Position(**defaults)


# ============================================================================
# KillSwitch Tests
# ============================================================================

class TestKillSwitch:
    def test_inactive_when_no_file(self, tmp_path):
        ks = KillSwitch(str(tmp_path / "kill_switch"))
        assert ks.is_active() is False

    def test_active_when_file_exists(self, tmp_path):
        path = tmp_path / "kill_switch"
        path.touch()
        ks = KillSwitch(str(path))
        assert ks.is_active() is True

    def test_inactive_when_path_empty(self):
        ks = KillSwitch("")
        assert ks.is_active() is False

    def test_deactivates_when_file_removed(self, tmp_path):
        path = tmp_path / "kill_switch"
        path.touch()
        ks = KillSwitch(str(path))
        assert ks.is_active() is True
        path.unlink()
        assert ks.is_active() is False


# ============================================================================
# DailyLossMonitor Tests
# ============================================================================

class TestDailyLossMonitor:
    def test_no_limit_when_disabled(self):
        db = MagicMock()
        db.get_daily_pnl.return_value = -100.0
        mon = DailyLossMonitor(db, max_daily_loss=0)
        assert mon.has_hit_limit() is False
        db.get_daily_pnl.assert_not_called()

    def test_limit_hit_when_loss_exceeds(self):
        db = MagicMock()
        db.get_daily_pnl.return_value = -45.0
        mon = DailyLossMonitor(db, max_daily_loss=40.0)
        assert mon.has_hit_limit() is True

    def test_no_limit_when_loss_below(self):
        db = MagicMock()
        db.get_daily_pnl.return_value = -20.0
        mon = DailyLossMonitor(db, max_daily_loss=40.0)
        assert mon.has_hit_limit() is False

    def test_no_limit_when_profit(self):
        db = MagicMock()
        db.get_daily_pnl.return_value = 50.0
        mon = DailyLossMonitor(db, max_daily_loss=40.0)
        assert mon.has_hit_limit() is False

    def test_exactly_at_limit(self):
        db = MagicMock()
        db.get_daily_pnl.return_value = -40.0
        mon = DailyLossMonitor(db, max_daily_loss=40.0)
        assert mon.has_hit_limit() is True

    def test_get_daily_pnl(self):
        db = MagicMock()
        db.get_daily_pnl.return_value = -15.50
        mon = DailyLossMonitor(db, max_daily_loss=40.0)
        assert mon.get_daily_pnl() == -15.50


# ============================================================================
# StreakTracker Tests
# ============================================================================

class TestStreakTracker:
    def test_initial_state(self, tmp_path):
        st = StreakTracker(5, 60, str(tmp_path / "streak.json"))
        assert st.consecutive_losses == 0
        assert st.in_cooldown() is False

    def test_losses_increment(self, tmp_path):
        st = StreakTracker(5, 60, str(tmp_path / "streak.json"))
        st.record_result(-1.0)
        assert st.consecutive_losses == 1
        st.record_result(-2.0)
        assert st.consecutive_losses == 2

    def test_win_resets_streak(self, tmp_path):
        st = StreakTracker(5, 60, str(tmp_path / "streak.json"))
        st.record_result(-1.0)
        st.record_result(-2.0)
        st.record_result(5.0)  # win
        assert st.consecutive_losses == 0

    def test_cooldown_triggers_at_max(self, tmp_path):
        st = StreakTracker(3, 60, str(tmp_path / "streak.json"))
        st.record_result(-1.0)
        st.record_result(-1.0)
        st.record_result(-1.0)  # 3rd loss = max
        assert st.in_cooldown() is True

    def test_cooldown_expires(self, tmp_path):
        st = StreakTracker(3, 60, str(tmp_path / "streak.json"))
        st.record_result(-1.0)
        st.record_result(-1.0)
        st.record_result(-1.0)
        # Manually expire cooldown
        st._cooldown_until = time.time() - 1
        assert st.in_cooldown() is False
        assert st.consecutive_losses == 0  # reset on expiry

    def test_disabled_when_max_zero(self, tmp_path):
        st = StreakTracker(0, 60, str(tmp_path / "streak.json"))
        for _ in range(10):
            st.record_result(-1.0)
        assert st.in_cooldown() is False

    def test_state_persists(self, tmp_path):
        path = str(tmp_path / "streak.json")
        st1 = StreakTracker(5, 60, path)
        st1.record_result(-1.0)
        st1.record_result(-1.0)

        st2 = StreakTracker(5, 60, path)
        assert st2.consecutive_losses == 2

    def test_corrupted_state_file(self, tmp_path):
        path = tmp_path / "streak.json"
        path.write_text("not json{{{")
        st = StreakTracker(5, 60, str(path))
        assert st.consecutive_losses == 0

    def test_missing_state_file(self, tmp_path):
        st = StreakTracker(5, 60, str(tmp_path / "nonexistent.json"))
        assert st.consecutive_losses == 0


# ============================================================================
# CircuitBreaker Facade Tests
# ============================================================================

class TestCircuitBreakerFacade:
    def _make_cb(self, tmp_path, kill_active=False, daily_pnl=0.0,
                 max_daily=40.0, max_consec=5, cooldown_mins=60):
        ks_path = str(tmp_path / "kill_switch")
        if kill_active:
            (tmp_path / "kill_switch").touch()
        ks = KillSwitch(ks_path)

        db = MagicMock()
        db.get_daily_pnl.return_value = daily_pnl
        lm = DailyLossMonitor(db, max_daily)

        st = StreakTracker(max_consec, cooldown_mins, str(tmp_path / "streak.json"))
        logger = MagicMock()

        return CircuitBreaker(ks, lm, st, logger)

    def test_all_clear(self, tmp_path):
        cb = self._make_cb(tmp_path)
        allowed, reason = cb.is_trading_allowed()
        assert allowed is True
        assert reason == ""

    def test_kill_switch_blocks(self, tmp_path):
        cb = self._make_cb(tmp_path, kill_active=True)
        allowed, reason = cb.is_trading_allowed()
        assert allowed is False
        assert "kill_switch" in reason

    def test_daily_loss_blocks(self, tmp_path):
        cb = self._make_cb(tmp_path, daily_pnl=-50.0, max_daily=40.0)
        allowed, reason = cb.is_trading_allowed()
        assert allowed is False
        assert "daily_loss" in reason

    def test_streak_blocks(self, tmp_path):
        cb = self._make_cb(tmp_path, max_consec=2)
        cb.record_trade_result(-1.0)
        cb.record_trade_result(-1.0)
        allowed, reason = cb.is_trading_allowed()
        assert allowed is False
        assert "consecutive_loss" in reason

    def test_record_win_clears_streak(self, tmp_path):
        cb = self._make_cb(tmp_path, max_consec=3)
        cb.record_trade_result(-1.0)
        cb.record_trade_result(-1.0)
        cb.record_trade_result(5.0)
        allowed, _ = cb.is_trading_allowed()
        assert allowed is True

    def test_priority_kill_switch_first(self, tmp_path):
        """Kill switch checked before daily loss."""
        cb = self._make_cb(tmp_path, kill_active=True, daily_pnl=-50.0)
        _, reason = cb.is_trading_allowed()
        assert "kill_switch" in reason


# ============================================================================
# BalanceManager Tests
# ============================================================================

class TestBalanceManager:
    def _make_bm(self, balance=400.0, positions=None, **config_overrides):
        config = make_config(**config_overrides)
        clob = AsyncMock()
        clob.get_balance = AsyncMock(return_value=balance)
        from polyphemus.position_store import PositionStore
        store = PositionStore()
        if positions:
            for p in positions:
                store.add(p)
        return BalanceManager(clob, store, config), clob

    @pytest.mark.asyncio
    async def test_dry_run_returns_simulated(self):
        bm, _ = self._make_bm(dry_run=True, dry_run_balance=500.0)
        balance = await bm.get_balance()
        assert balance == 500.0

    @pytest.mark.asyncio
    async def test_live_fetches_from_clob(self):
        bm, clob = self._make_bm(balance=162.07, dry_run=False)
        balance = await bm.get_balance()
        assert balance == 162.07
        clob.get_balance.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_prevents_refetch(self):
        bm, clob = self._make_bm(balance=200.0, dry_run=False)
        await bm.get_balance()
        await bm.get_balance()
        # Only 1 fetch despite 2 calls
        assert clob.get_balance.call_count == 1

    @pytest.mark.asyncio
    async def test_cache_expires(self):
        bm, clob = self._make_bm(balance=200.0, dry_run=False)
        await bm.get_balance()
        # Expire cache
        bm._cache_time = time.time() - BALANCE_CACHE_TTL - 1
        await bm.get_balance()
        assert clob.get_balance.call_count == 2

    @pytest.mark.asyncio
    async def test_get_available_subtracts_deployed(self):
        pos = make_position(entry_price=0.50, entry_size=100.0)
        bm, _ = self._make_bm(positions=[pos], dry_run_balance=200.0)
        available = await bm.get_available()
        # 200 - (0.50 * 100) = 150
        assert abs(available - 150.0) < 0.01

    @pytest.mark.asyncio
    async def test_get_available_floors_at_zero(self):
        pos = make_position(entry_price=0.90, entry_size=300.0)
        bm, _ = self._make_bm(positions=[pos], dry_run_balance=200.0)
        available = await bm.get_available()
        assert available == 0.0

    @pytest.mark.asyncio
    async def test_momentum_excludes_accum_reserve(self):
        bm, _ = self._make_bm(dry_run_balance=400.0, accum_capital_pct=0.40)
        available = await bm.get_available_for_momentum()
        # 400 - 0 deployed - (400 * 0.40 reserved) = 240
        assert abs(available - 240.0) < 0.01

    @pytest.mark.asyncio
    async def test_momentum_excludes_accum_positions(self):
        accum_pos = make_position(
            token_id="0xaccum", slug="accum-slug",
            entry_price=0.50, entry_size=100.0,
            metadata={"is_accumulator": True}
        )
        momentum_pos = make_position(
            token_id="0xmomentum", slug="momentum-slug",
            entry_price=0.60, entry_size=50.0,
        )
        bm, _ = self._make_bm(
            positions=[accum_pos, momentum_pos],
            dry_run_balance=400.0, accum_capital_pct=0.40,
        )
        available = await bm.get_available_for_momentum()
        # 400 - 30 (momentum deployed: 0.60*50) - 160 (accum reserved: 400*0.40) = 210
        assert abs(available - 210.0) < 0.01

    @pytest.mark.asyncio
    async def test_accumulator_gets_reserved_share(self):
        bm, _ = self._make_bm(
            dry_run_balance=400.0,
            enable_accumulator=True,
            accum_capital_pct=0.40,
        )
        available = await bm.get_available_for_accumulator()
        # 400 * 0.40 - 0 deployed = 160
        assert abs(available - 160.0) < 0.01

    @pytest.mark.asyncio
    async def test_accumulator_disabled_returns_zero(self):
        bm, _ = self._make_bm(enable_accumulator=False)
        available = await bm.get_available_for_accumulator()
        assert available == 0.0

    def test_deployment_ratio_no_positions(self):
        bm, _ = self._make_bm(dry_run_balance=400.0)
        bm._cached_balance = 400.0
        assert bm.get_deployment_ratio() == 0.0

    def test_deployment_ratio_with_positions(self):
        pos = make_position(entry_price=0.50, entry_size=100.0)
        bm, _ = self._make_bm(positions=[pos], dry_run_balance=400.0)
        bm._cached_balance = 400.0
        ratio = bm.get_deployment_ratio()
        # deployed=50, total=50+400=450, ratio=50/450≈0.111
        assert abs(ratio - 50.0 / 450.0) < 0.001

    def test_deployment_ratio_zero_total(self):
        bm, _ = self._make_bm()
        bm._cached_balance = 0.0
        assert bm.get_deployment_ratio() == 0.0

    @pytest.mark.asyncio
    async def test_safe_to_trade_ok(self):
        bm, _ = self._make_bm(
            dry_run_balance=200.0,
            low_balance_threshold=10.0,
            max_deployment_ratio=0.50,
        )
        assert await bm.is_safe_to_trade() is True

    @pytest.mark.asyncio
    async def test_safe_to_trade_low_balance(self):
        bm, _ = self._make_bm(
            dry_run_balance=5.0,
            low_balance_threshold=10.0,
        )
        assert await bm.is_safe_to_trade() is False

    @pytest.mark.asyncio
    async def test_safe_to_trade_high_deployment(self):
        pos = make_position(entry_price=0.80, entry_size=500.0)
        bm, _ = self._make_bm(
            positions=[pos],
            dry_run_balance=100.0,
            max_deployment_ratio=0.50,
        )
        bm._cached_balance = 100.0  # pre-populate cache for ratio calc
        assert await bm.is_safe_to_trade() is False

    @pytest.mark.asyncio
    async def test_reconcile_dry_run_passes(self):
        bm, _ = self._make_bm(dry_run=True, dry_run_balance=400.0)
        result = await bm.reconcile_at_startup()
        assert result is True

    @pytest.mark.asyncio
    async def test_reconcile_live_zero_balance(self):
        bm, _ = self._make_bm(balance=0.0, dry_run=False)
        result = await bm.reconcile_at_startup()
        assert result is False

    @pytest.mark.asyncio
    async def test_reconcile_live_low_balance(self):
        bm, _ = self._make_bm(balance=30.0, dry_run=False)
        result = await bm.reconcile_at_startup()
        assert result is False

    @pytest.mark.asyncio
    async def test_reconcile_live_healthy(self):
        bm, _ = self._make_bm(balance=200.0, dry_run=False)
        result = await bm.reconcile_at_startup()
        assert result is True


# ============================================================================
# FillOptimizer Tests
# ============================================================================

class TestFillOptimizer:
    @pytest.fixture
    def optimizer(self, tmp_path):
        db_path = str(tmp_path / "fill_optimizer.db")
        return FillOptimizer(db_path=db_path)

    def test_init_creates_arms(self, optimizer):
        assert len(optimizer._arms) == 4
        for offset in [0.005, 0.01, 0.015, 0.02]:
            assert offset in optimizer._arms

    def test_arm_initial_state(self, optimizer):
        arm = optimizer._arms[0.01]
        assert arm.alpha == 1.0
        assert arm.beta == 1.0
        assert arm.total_pulls == 0
        assert arm.total_fills == 0
        assert arm.total_profit == 0.0

    def test_select_offset_returns_valid(self, optimizer):
        offset = optimizer.select_offset()
        assert offset in [0.005, 0.01, 0.015, 0.02]

    def test_record_fill_updates_arm(self, optimizer):
        optimizer.record_outcome(0.01, filled=True, slug="test-slug")
        arm = optimizer._arms[0.01]
        assert arm.total_pulls == 1
        assert arm.total_fills == 1
        assert arm.alpha == 2.0  # 1 + 1
        assert arm.beta == 1.0   # unchanged

    def test_record_miss_updates_arm(self, optimizer):
        optimizer.record_outcome(0.01, filled=False, slug="test-slug")
        arm = optimizer._arms[0.01]
        assert arm.total_pulls == 1
        assert arm.total_fills == 0
        assert arm.alpha == 1.0   # unchanged
        assert arm.beta == 2.0    # 1 + 1

    def test_fill_rate(self, optimizer):
        optimizer.record_outcome(0.01, filled=True)
        optimizer.record_outcome(0.01, filled=True)
        optimizer.record_outcome(0.01, filled=False)
        assert abs(optimizer._arms[0.01].fill_rate - 2 / 3) < 0.001

    def test_update_profit(self, optimizer):
        optimizer.record_outcome(0.01, filled=True, slug="btc-5m-test")
        optimizer.update_profit("btc-5m-test", 5.0)
        assert optimizer._arms[0.01].total_profit == 5.0

    def test_update_profit_accumulates(self, optimizer):
        optimizer.record_outcome(0.01, filled=True, slug="slug-1")
        optimizer.update_profit("slug-1", 3.0)
        optimizer.record_outcome(0.01, filled=True, slug="slug-2")
        optimizer.update_profit("slug-2", 2.0)
        assert optimizer._arms[0.01].total_profit == 5.0

    def test_avg_profit(self, optimizer):
        optimizer.record_outcome(0.01, filled=True, slug="s1")
        optimizer.update_profit("s1", 6.0)
        optimizer.record_outcome(0.01, filled=True, slug="s2")
        optimizer.update_profit("s2", 4.0)
        assert abs(optimizer._arms[0.01].avg_profit - 5.0) < 0.001

    def test_unknown_offset_ignored(self, optimizer):
        optimizer.record_outcome(0.999, filled=True, slug="test")
        # Should not crash, just log warning

    def test_get_stats_structure(self, optimizer):
        optimizer.record_outcome(0.01, filled=True, slug="s")
        stats = optimizer.get_stats()
        assert "$0.010" in stats
        assert "pulls" in stats["$0.010"]
        assert "fill_rate" in stats["$0.010"]
        assert stats["$0.010"]["pulls"] == 1

    def test_persistence_across_instances(self, tmp_path):
        db_path = str(tmp_path / "fill_optimizer.db")
        opt1 = FillOptimizer(db_path=db_path)
        opt1.record_outcome(0.01, filled=True, slug="test")
        opt1.record_outcome(0.01, filled=True, slug="test2")
        opt1.close()

        opt2 = FillOptimizer(db_path=db_path)
        arm = opt2._arms[0.01]
        assert arm.total_pulls == 2
        assert arm.total_fills == 2
        assert arm.alpha == 3.0  # 1 + 2 fills
        opt2.close()

    def test_custom_offsets(self, tmp_path):
        db_path = str(tmp_path / "fill_optimizer.db")
        opt = FillOptimizer(offsets=[0.001, 0.002], db_path=db_path)
        assert len(opt._arms) == 2
        assert 0.001 in opt._arms
        assert 0.002 in opt._arms
        opt.close()

    def test_arm_sample_deterministic_with_seed(self, optimizer):
        """Beta sampling is stochastic but always returns 0-1."""
        arm = optimizer._arms[0.01]
        for _ in range(20):
            s = arm.sample()
            assert 0.0 <= s <= 1.0

    def test_exploitation_bias(self, tmp_path):
        """Arm with many fills should be selected more often than arm with no fills."""
        db_path = str(tmp_path / "fill_optimizer.db")
        opt = FillOptimizer(offsets=[0.01, 0.02], db_path=db_path)
        # Give 0.01 lots of fills
        for _ in range(50):
            opt.record_outcome(0.01, filled=True)
        # Give 0.02 lots of misses
        for _ in range(50):
            opt.record_outcome(0.02, filled=False)

        # Sample 100 times, 0.01 should dominate
        selections = [opt.select_offset() for _ in range(100)]
        pct_01 = selections.count(0.01) / len(selections)
        assert pct_01 > 0.80, f"Expected 0.01 to dominate, got {pct_01:.0%}"
        opt.close()


# ============================================================================
# ExitHandler Tests
# ============================================================================

class TestExitHandler:
    def _make_handler(self, **config_overrides):
        config = make_config(**config_overrides)
        clob = AsyncMock()
        return ExitHandler(clob, config), clob

    @pytest.mark.asyncio
    async def test_market_resolved_skips_sell(self):
        handler, clob = self._make_handler()
        pos = make_position(current_price=0.95)
        signal = ExitSignal(token_id=pos.token_id, reason=ExitReason.MARKET_RESOLVED.value)

        result = await handler.execute_exit(pos, signal)

        assert result.success is True
        assert result.fill_price == 0.95
        assert result.reason == "market_resolved"
        clob.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_market_resolved_uses_entry_when_no_current(self):
        handler, _ = self._make_handler()
        pos = make_position(current_price=0.0, entry_price=0.70)
        signal = ExitSignal(token_id=pos.token_id, reason=ExitReason.MARKET_RESOLVED.value)

        result = await handler.execute_exit(pos, signal)

        assert result.fill_price == 0.70

    @pytest.mark.asyncio
    async def test_insufficient_shares(self):
        handler, clob = self._make_handler()
        clob.get_share_balance = AsyncMock(return_value=2.0)  # < 5.0 min
        pos = make_position()
        signal = ExitSignal(token_id=pos.token_id, reason=ExitReason.STOP_LOSS.value)

        result = await handler.execute_exit(pos, signal)

        assert result.success is False
        assert "insufficient_shares" in result.error

    @pytest.mark.asyncio
    async def test_taker_sell_success(self):
        handler, clob = self._make_handler()
        clob.get_share_balance = AsyncMock(return_value=100.0)
        clob.place_order = AsyncMock(return_value=ExecutionResult(
            success=True, order_id="0xsell001"
        ))
        clob.get_order_details = AsyncMock(return_value=_order_details(OrderStatus.FILLED))

        pos = make_position(current_price=0.75, entry_price=0.68)
        signal = ExitSignal(
            token_id=pos.token_id,
            reason=ExitReason.STOP_LOSS.value,
            exit_price=0.60,
        )

        result = await handler.execute_exit(pos, signal)

        assert result.success is True
        assert result.order_id == "0xsell001"
        clob.place_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_exit_price_fallback_chain(self):
        """exit_price: signal → current → entry."""
        handler, clob = self._make_handler()
        clob.get_share_balance = AsyncMock(return_value=100.0)
        clob.place_order = AsyncMock(return_value=ExecutionResult(
            success=True, order_id="0x1"
        ))
        clob.get_order_details = AsyncMock(return_value=_order_details(OrderStatus.FILLED))

        # No signal exit_price, no current_price → falls back to entry_price
        pos = make_position(current_price=0.0, entry_price=0.70)
        signal = ExitSignal(
            token_id=pos.token_id,
            reason=ExitReason.MAX_HOLD.value,
            exit_price=None,
        )

        result = await handler.execute_exit(pos, signal)

        assert result.success is True
        # sell_price should be entry_price - discount
        call_args = clob.place_order.call_args
        assert call_args.kwargs["price"] == pytest.approx(0.70 - 0.02, abs=0.001)

    @pytest.mark.asyncio
    async def test_urgent_pricing_for_time_exit(self):
        """Time-based exit near expiry uses 5-cent discount."""
        handler, clob = self._make_handler()
        clob.get_share_balance = AsyncMock(return_value=100.0)
        clob.place_order = AsyncMock(return_value=ExecutionResult(
            success=True, order_id="0x1"
        ))
        clob.get_order_details = AsyncMock(return_value=_order_details(OrderStatus.FILLED))

        now = datetime.now(timezone.utc)
        pos = make_position(
            current_price=0.70,
            market_end_time=now + timedelta(minutes=3),  # <7 min = urgent
        )
        signal = ExitSignal(
            token_id=pos.token_id,
            reason=ExitReason.TIME_EXIT.value,
        )

        result = await handler.execute_exit(pos, signal)

        assert result.success is True
        call_args = clob.place_order.call_args
        # Urgent discount: 0.70 - 0.05 = 0.65
        assert call_args.kwargs["price"] == pytest.approx(0.65, abs=0.01)

    @pytest.mark.asyncio
    async def test_normal_pricing_for_stop_loss_near_expiry(self):
        """Stop loss always uses $0.02 discount, even near expiry."""
        handler, clob = self._make_handler()
        clob.get_share_balance = AsyncMock(return_value=100.0)
        clob.place_order = AsyncMock(return_value=ExecutionResult(
            success=True, order_id="0x1"
        ))
        clob.get_order_details = AsyncMock(return_value=_order_details(OrderStatus.FILLED))

        now = datetime.now(timezone.utc)
        pos = make_position(
            current_price=0.60,
            market_end_time=now + timedelta(minutes=3),
        )
        signal = ExitSignal(
            token_id=pos.token_id,
            reason=ExitReason.STOP_LOSS.value,
        )

        result = await handler.execute_exit(pos, signal)

        call_args = clob.place_order.call_args
        # Normal discount: 0.60 - 0.02 = 0.58
        assert call_args.kwargs["price"] == pytest.approx(0.58, abs=0.01)

    @pytest.mark.asyncio
    async def test_maker_exit_for_profit_target(self):
        """Profit target tries maker SELL first, then falls back to taker."""
        handler, clob = self._make_handler(
            maker_exit_enabled=True,
            maker_exit_timeout_polls=3,
        )
        clob.get_share_balance = AsyncMock(return_value=100.0)
        # Maker placed successfully
        maker_result = ExecutionResult(success=True, order_id="0xmaker")
        taker_result = ExecutionResult(success=True, order_id="0xtaker")
        clob.place_order = AsyncMock(side_effect=[maker_result, taker_result])
        # Maker doesn't fill (LIVE for 3 polls + 1 timeout check), taker fills
        clob.get_order_details = AsyncMock(side_effect=[
            _order_details(OrderStatus.LIVE, size_matched=0),
            _order_details(OrderStatus.LIVE, size_matched=0),
            _order_details(OrderStatus.LIVE, size_matched=0),
            _order_details(OrderStatus.LIVE, size_matched=0),  # timeout check
            _order_details(OrderStatus.FILLED),  # taker fill
        ])
        clob.cancel_order = AsyncMock(return_value=True)

        now = datetime.now(timezone.utc)
        pos = make_position(
            current_price=0.85,
            market_end_time=now + timedelta(minutes=10),  # not near expiry
        )
        signal = ExitSignal(
            token_id=pos.token_id,
            reason=ExitReason.PROFIT_TARGET.value,
        )

        result = await handler.execute_exit(pos, signal)

        assert result.success is True
        # Maker was tried (place_order called twice: maker + taker)
        assert clob.place_order.call_count == 2
        # Maker was cancelled (once in _poll_for_fill timeout, once explicitly)
        assert clob.cancel_order.call_count == 2
        clob.cancel_order.assert_any_call("0xmaker")

    @pytest.mark.asyncio
    async def test_maker_exit_skipped_near_expiry(self):
        """Maker exit skipped when < 7 min left."""
        handler, clob = self._make_handler(
            maker_exit_enabled=True,
            maker_exit_timeout_polls=10,
        )
        clob.get_share_balance = AsyncMock(return_value=100.0)
        clob.place_order = AsyncMock(return_value=ExecutionResult(
            success=True, order_id="0xtaker"
        ))
        clob.get_order_details = AsyncMock(return_value=_order_details(OrderStatus.FILLED))

        now = datetime.now(timezone.utc)
        pos = make_position(
            current_price=0.85,
            market_end_time=now + timedelta(minutes=3),  # < 7 min
        )
        signal = ExitSignal(
            token_id=pos.token_id,
            reason=ExitReason.PROFIT_TARGET.value,
        )

        result = await handler.execute_exit(pos, signal)

        assert result.success is True
        # Only taker, no maker attempt
        assert clob.place_order.call_count == 1

    @pytest.mark.asyncio
    async def test_maker_exit_fills(self):
        """Maker SELL fills successfully — no taker fallback needed."""
        handler, clob = self._make_handler(
            maker_exit_enabled=True,
            maker_exit_timeout_polls=5,
        )
        clob.get_share_balance = AsyncMock(return_value=100.0)
        clob.place_order = AsyncMock(return_value=ExecutionResult(
            success=True, order_id="0xmaker"
        ))
        clob.get_order_details = AsyncMock(return_value=_order_details(OrderStatus.FILLED))

        now = datetime.now(timezone.utc)
        pos = make_position(
            current_price=0.85,
            market_end_time=now + timedelta(minutes=10),
        )
        signal = ExitSignal(
            token_id=pos.token_id,
            reason=ExitReason.PROFIT_TARGET.value,
        )

        result = await handler.execute_exit(pos, signal)

        assert result.success is True
        # Only maker, no taker needed
        assert clob.place_order.call_count == 1

    @pytest.mark.asyncio
    async def test_placement_failure(self):
        handler, clob = self._make_handler()
        clob.get_share_balance = AsyncMock(return_value=100.0)
        clob.place_order = AsyncMock(return_value=ExecutionResult(
            success=False, error="Connection timeout"
        ))

        pos = make_position(current_price=0.70)
        signal = ExitSignal(
            token_id=pos.token_id,
            reason=ExitReason.STOP_LOSS.value,
        )

        result = await handler.execute_exit(pos, signal)

        assert result.success is False
        assert "Connection timeout" in result.error

    @pytest.mark.asyncio
    async def test_orderbook_error_treated_as_resolved(self):
        """'does not exist' error during SELL → market_resolved."""
        handler, clob = self._make_handler()
        clob.get_share_balance = AsyncMock(return_value=100.0)
        clob.place_order = AsyncMock(return_value=ExecutionResult(
            success=False, error="Orderbook does not exist"
        ))

        pos = make_position(current_price=0.70)
        signal = ExitSignal(
            token_id=pos.token_id,
            reason=ExitReason.MAX_HOLD.value,
        )

        result = await handler.execute_exit(pos, signal)

        assert result.success is True
        assert result.reason == "market_resolved"
        assert result.fill_price == 0.70

    @pytest.mark.asyncio
    async def test_poll_cancelled_status(self):
        """CANCELLED order status returns failure."""
        handler, clob = self._make_handler()
        clob.get_share_balance = AsyncMock(return_value=100.0)
        clob.place_order = AsyncMock(return_value=ExecutionResult(
            success=True, order_id="0xorder"
        ))
        clob.get_order_details = AsyncMock(return_value=_order_details(OrderStatus.CANCELLED, size_matched=0))

        pos = make_position(current_price=0.70)
        signal = ExitSignal(
            token_id=pos.token_id,
            reason=ExitReason.STOP_LOSS.value,
        )

        result = await handler.execute_exit(pos, signal)

        assert result.success is False

    @pytest.mark.asyncio
    async def test_time_exit_retry_on_timeout(self):
        """Time-based exit retries with deeper discount on timeout."""
        handler, clob = self._make_handler()
        clob.get_share_balance = AsyncMock(return_value=100.0)

        # First attempt: placement succeeds but fill times out
        # Second attempt: placement + fill succeeds
        clob.place_order = AsyncMock(side_effect=[
            ExecutionResult(success=True, order_id="0xfirst"),
            ExecutionResult(success=True, order_id="0xretry"),
        ])
        # First order: LIVE for all polls (timeout) + 1 timeout check, second order: FILLED
        first_polls = [_order_details(OrderStatus.LIVE, size_matched=0)] * TAKER_POLL_MAX
        clob.get_order_details = AsyncMock(side_effect=[
            *first_polls,
            _order_details(OrderStatus.LIVE, size_matched=0),  # timeout check
            _order_details(OrderStatus.FILLED),  # retry fill
        ])
        clob.cancel_order = AsyncMock(return_value=True)

        pos = make_position(current_price=0.70)
        signal = ExitSignal(
            token_id=pos.token_id,
            reason=ExitReason.TIME_EXIT.value,
        )

        result = await handler.execute_exit(pos, signal)

        assert result.success is True
        assert clob.place_order.call_count == 2
        # Retry uses 10-cent discount
        retry_call = clob.place_order.call_args_list[1]
        assert retry_call.kwargs["price"] == pytest.approx(0.70 - 0.10, abs=0.01)

    @pytest.mark.asyncio
    async def test_sell_price_floor(self):
        """Sell price never goes below $0.01."""
        handler, clob = self._make_handler()
        clob.get_share_balance = AsyncMock(return_value=100.0)
        clob.place_order = AsyncMock(return_value=ExecutionResult(
            success=True, order_id="0x1"
        ))
        clob.get_order_details = AsyncMock(return_value=_order_details(OrderStatus.FILLED))

        pos = make_position(current_price=0.02, entry_price=0.02)
        signal = ExitSignal(
            token_id=pos.token_id,
            reason=ExitReason.STOP_LOSS.value,
        )

        result = await handler.execute_exit(pos, signal)

        call_args = clob.place_order.call_args
        assert call_args.kwargs["price"] >= 0.01


# ============================================================================
# BinanceMomentumFeed Tests
# ============================================================================

class TestBinanceMomentumFeed:
    def _make_feed(self, **config_overrides):
        config_defaults = dict(
            momentum_trigger_pct=0.003,
            momentum_window_secs=60,
            entry_cooldown_secs=120,
            asset_filter="",
            direction_filter="",
            market_window_secs=300,
            market_window_15m_assets="",
            dual_window_assets="",
            min_secs_remaining=60,
            entry_mode="maker",
            enable_window_delta=False,
            window_delta_shadow=True,
            window_delta_lead_secs=10,
            window_delta_min_pct=0.001,
            window_delta_assets="",
            window_delta_max_price=0.95,
        )
        config_defaults.update(config_overrides)
        config = make_config(**config_defaults)
        clob = AsyncMock()
        clob.get_midpoint = AsyncMock(return_value=0.50)
        on_signal = AsyncMock()
        feed = BinanceMomentumFeed(config, clob, on_signal)
        return feed, clob, on_signal

    @pytest.mark.asyncio
    async def test_process_price_update_appends_to_buffer(self):
        feed, _, _ = self._make_feed()
        data = {
            "data": {
                "s": "BTCUSDT",
                "k": {"c": "42000.50"},
            }
        }
        await feed._process_price_update(data)
        assert len(feed._price_buffers["btcusdt"]) == 1
        ts, price = feed._price_buffers["btcusdt"][0]
        assert price == 42000.50

    @pytest.mark.asyncio
    async def test_process_ignores_unknown_symbol(self):
        feed, _, _ = self._make_feed()
        data = {"data": {"s": "DOGEUSDT", "k": {"c": "0.10"}}}
        await feed._process_price_update(data)
        assert "dogeusdt" not in feed._price_buffers

    @pytest.mark.asyncio
    async def test_process_ignores_zero_price(self):
        feed, _, _ = self._make_feed()
        data = {"data": {"s": "BTCUSDT", "k": {"c": "0"}}}
        await feed._process_price_update(data)
        assert len(feed._price_buffers["btcusdt"]) == 0

    @pytest.mark.asyncio
    async def test_check_momentum_up(self):
        feed, _, _ = self._make_feed(momentum_trigger_pct=0.003)
        # Inject old price 60s ago
        now = time.time()
        feed._price_buffers["btcusdt"].append((now - 30, 100.0))

        await feed._check_momentum("btcusdt", now, 100.5)  # +0.5% > 0.3%
        assert feed.momentum_detections == 1

    @pytest.mark.asyncio
    async def test_check_momentum_down(self):
        feed, _, _ = self._make_feed(momentum_trigger_pct=0.003)
        now = time.time()
        feed._price_buffers["btcusdt"].append((now - 30, 100.0))

        await feed._check_momentum("btcusdt", now, 99.5)  # -0.5% exceeds threshold
        assert feed.momentum_detections == 1

    @pytest.mark.asyncio
    async def test_check_momentum_below_threshold(self):
        feed, _, _ = self._make_feed(momentum_trigger_pct=0.003)
        now = time.time()
        feed._price_buffers["btcusdt"].append((now - 30, 100.0))

        await feed._check_momentum("btcusdt", now, 100.1)  # +0.1% < 0.3%
        assert feed.momentum_detections == 0

    @pytest.mark.asyncio
    async def test_check_momentum_no_old_data(self):
        feed, _, _ = self._make_feed(momentum_trigger_pct=0.003)
        # No data in buffer
        now = time.time()
        await feed._check_momentum("btcusdt", now, 100.5)
        assert feed.momentum_detections == 0

    @pytest.mark.asyncio
    async def test_entry_cooldown(self):
        feed, _, on_signal = self._make_feed(entry_cooldown_secs=120)
        feed._last_signal_time = {"BTC": time.time()}  # just signaled

        # Mock _discover_market to avoid Gamma API calls
        feed._discover_market = AsyncMock(return_value={
            "up_token_id": "0xup", "down_token_id": "0xdown",
            "market_title": "test"
        })

        await feed._generate_signal("BTC", "UP")
        on_signal.assert_not_called()

    @pytest.mark.asyncio
    async def test_cooldown_expired(self):
        feed, _, on_signal = self._make_feed(entry_cooldown_secs=120)
        feed._last_signal_time = {"BTC": time.time() - 130}  # 130s ago > 120s cooldown

        feed._discover_market = AsyncMock(return_value={
            "up_token_id": "0xup", "down_token_id": "0xdown",
            "market_title": "test"
        })

        await feed._generate_signal("BTC", "UP")
        # Signal should go through (subject to time remaining check)
        # May or may not call on_signal depending on slug timing

    @pytest.mark.asyncio
    async def test_asset_filter_blocks(self):
        feed, _, on_signal = self._make_feed(asset_filter="ETH")
        feed._last_signal_time = {}

        await feed._generate_signal("BTC", "UP")  # BTC not in filter
        on_signal.assert_not_called()

    @pytest.mark.asyncio
    async def test_asset_filter_allows(self):
        feed, _, on_signal = self._make_feed(asset_filter="BTC")
        feed._last_signal_time = {}
        feed._discover_market = AsyncMock(return_value={
            "up_token_id": "0xup", "down_token_id": "0xdown",
            "market_title": "test"
        })

        await feed._generate_signal("BTC", "UP")
        # Should proceed past the asset filter

    @pytest.mark.asyncio
    async def test_direction_filter(self):
        feed, _, on_signal = self._make_feed(direction_filter="Up")
        feed._last_signal_time = {}

        await feed._generate_signal("BTC", "DOWN")  # DOWN filtered out
        on_signal.assert_not_called()

    @pytest.mark.asyncio
    async def test_slug_debounce(self):
        """Same slug can only be signaled once."""
        feed, _, on_signal = self._make_feed(entry_cooldown_secs=0)
        feed._last_signal_time = {}
        feed._discover_market = AsyncMock(return_value={
            "up_token_id": "0xup", "down_token_id": "0xdown",
            "market_title": "test"
        })

        # Compute what the slug will be
        window = 300
        epoch = int(time.time() // window) * window
        slug = f"btc-updown-5m-{epoch}"

        # First call
        await feed._generate_signal_for_window("BTC", "UP", 0.005, window)
        first_call_count = on_signal.call_count

        # Second call — same slug
        await feed._generate_signal_for_window("BTC", "UP", 0.005, window)
        assert on_signal.call_count == first_call_count  # no new call

    @pytest.mark.asyncio
    async def test_signal_dict_structure(self):
        """Verify the signal dict has all required fields."""
        feed, clob, on_signal = self._make_feed(entry_cooldown_secs=0)
        feed._last_signal_time = {}
        clob.get_midpoint = AsyncMock(return_value=0.48)
        feed._discover_market = AsyncMock(return_value={
            "up_token_id": "0xup_token", "down_token_id": "0xdown_token",
            "market_title": "BTC above $100k"
        })

        # Compute valid slug with enough time remaining
        window = 300
        epoch = int(time.time() // window) * window
        market_end = epoch + window
        if market_end - time.time() < 60:
            # Skip if we're too close to window end
            pytest.skip("Too close to window boundary")

        await feed._generate_signal_for_window("BTC", "UP", 0.005, window)

        if on_signal.called:
            signal = on_signal.call_args[0][0]
            assert signal["token_id"] == "0xup_token"
            assert signal["direction"] == "BUY"
            assert signal["outcome"] == "Up"
            assert signal["asset"] == "BTC"
            assert signal["source"] == "binance_momentum"
            assert signal["price"] == 0.48
            assert "momentum_pct" in signal
            assert "time_remaining_secs" in signal
            assert signal["market_window_secs"] == 300

    @pytest.mark.asyncio
    async def test_down_signal_uses_down_token(self):
        feed, clob, on_signal = self._make_feed(entry_cooldown_secs=0)
        feed._last_signal_time = {}
        clob.get_midpoint = AsyncMock(return_value=0.52)
        feed._discover_market = AsyncMock(return_value={
            "up_token_id": "0xup", "down_token_id": "0xdown",
            "market_title": "test"
        })

        window = 300
        epoch = int(time.time() // window) * window
        if (epoch + window) - time.time() < 60:
            pytest.skip("Too close to window boundary")

        await feed._generate_signal_for_window("BTC", "DOWN", 0.005, window)

        if on_signal.called:
            signal = on_signal.call_args[0][0]
            assert signal["token_id"] == "0xdown"
            assert signal["outcome"] == "Down"

    def test_discover_market_caching(self):
        feed, _, _ = self._make_feed()
        feed._market_cache["btc-updown-5m-1234"] = {
            "up_token_id": "0xup", "down_token_id": "0xdown",
            "market_title": "cached"
        }

        result = asyncio.get_event_loop().run_until_complete(
            feed._discover_market("btc-updown-5m-1234")
        )

        assert result["market_title"] == "cached"

    def test_discover_market_cache_miss_returns_none(self):
        feed, _, _ = self._make_feed()
        feed._market_cache["btc-updown-5m-1234"] = None

        result = asyncio.get_event_loop().run_until_complete(
            feed._discover_market("btc-updown-5m-1234")
        )

        assert result is None

    def test_prune_stale_removes_expired(self):
        feed, _, _ = self._make_feed()
        # Add a slug that expired long ago
        old_epoch = int(time.time()) - 1200  # 20 minutes ago
        old_slug = f"btc-updown-5m-{old_epoch}"
        feed._signaled_slugs.add(old_slug)
        feed._market_cache[old_slug] = {"cached": True}

        feed.prune_stale()

        assert old_slug not in feed._signaled_slugs
        assert old_slug not in feed._market_cache

    def test_prune_stale_keeps_active(self):
        feed, _, _ = self._make_feed()
        # Add a slug that's still active
        current_epoch = int(time.time() // 300) * 300
        active_slug = f"btc-updown-5m-{current_epoch}"
        feed._signaled_slugs.add(active_slug)

        feed.prune_stale()

        assert active_slug in feed._signaled_slugs

    def test_binance_to_asset_mapping(self):
        assert BINANCE_TO_ASSET["btcusdt"] == "BTC"
        assert BINANCE_TO_ASSET["ethusdt"] == "ETH"
        assert BINANCE_TO_ASSET["solusdt"] == "SOL"
        assert BINANCE_TO_ASSET["xrpusdt"] == "XRP"

    def test_window_open_price_uses_first_buffer_price_in_epoch(self):
        feed, _, _ = self._make_feed(enable_window_delta=True)
        epoch = 1_773_320_400
        feed._price_buffers["btcusdt"].extend([
            (epoch - 1, 99.0),
            (epoch + 1, 100.0),
            (epoch + 20, 100.3),
        ])

        open_price = feed._get_window_open_price("btcusdt", epoch)

        assert open_price == 100.0

    @pytest.mark.asyncio
    async def test_window_delta_uses_buffer_open_even_when_checked_late(self):
        feed, _, _ = self._make_feed(
            enable_window_delta=True,
            window_delta_lead_secs=60,
            window_delta_min_pct=0.001,
        )
        feed._generate_delta_signal = AsyncMock()
        epoch = 1_773_320_400
        now = epoch + 242  # 58s left
        feed._price_buffers["btcusdt"].extend([
            (epoch + 1, 100.0),
            (epoch + 120, 100.05),
            (now, 100.12),
        ])
        feed._window_open_prices[("BTC", epoch)] = 99.5  # stale/mis-seeded cache should be ignored

        await feed._check_window_delta("btcusdt", now, 100.12)

        feed._generate_delta_signal.assert_awaited_once()
        args = feed._generate_delta_signal.await_args.args
        assert args[0] == "BTC"
        assert args[1] == "UP"
        assert args[2] == f"btc-updown-5m-{epoch}"
        assert args[3] == pytest.approx((100.12 - 100.0) / 100.0)
        assert args[4] == pytest.approx(58.0)

    @pytest.mark.asyncio
    async def test_regime_detector_updated(self):
        feed, _, _ = self._make_feed()
        mock_regime = MagicMock()
        feed._regime_detector = mock_regime

        data = {"data": {"s": "BTCUSDT", "k": {"c": "42000"}}}
        await feed._process_price_update(data)

        mock_regime.update.assert_called_once_with("BTC", 42000.0, pytest.approx(time.time(), abs=1))

    @pytest.mark.asyncio
    async def test_no_signal_when_midpoint_zero(self):
        feed, clob, on_signal = self._make_feed(entry_cooldown_secs=0)
        feed._last_signal_time = {}
        clob.get_midpoint = AsyncMock(return_value=0.0)  # bad midpoint
        feed._discover_market = AsyncMock(return_value={
            "up_token_id": "0xup", "down_token_id": "0xdown",
            "market_title": "test"
        })

        window = 300
        epoch = int(time.time() // window) * window
        if (epoch + window) - time.time() < 60:
            pytest.skip("Too close to window boundary")

        await feed._generate_signal_for_window("BTC", "UP", 0.005, window)
        on_signal.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_signal_when_market_not_found(self):
        feed, _, on_signal = self._make_feed(entry_cooldown_secs=0)
        feed._last_signal_time = {}
        feed._discover_market = AsyncMock(return_value=None)

        window = 300
        epoch = int(time.time() // window) * window
        await feed._generate_signal_for_window("BTC", "UP", 0.005, window)
        on_signal.assert_not_called()

    @pytest.mark.asyncio
    async def test_time_remaining_guard(self):
        """Skip signal when too close to market end."""
        feed, _, on_signal = self._make_feed(
            entry_cooldown_secs=0,
            min_secs_remaining=60,
            market_window_secs=300,
        )
        feed._last_signal_time = {}
        feed._discover_market = AsyncMock(return_value={
            "up_token_id": "0xup", "down_token_id": "0xdown",
            "market_title": "test"
        })

        # Use an epoch that's almost expired (only 10s left)
        window = 300
        epoch = int(time.time() // window) * window
        # If current time is within 60s of market_end, it should be skipped
        secs_left = (epoch + window) - time.time()
        if secs_left >= 60:
            pytest.skip("Test requires being close to window end")

        await feed._generate_signal_for_window("BTC", "UP", 0.005, window)
        on_signal.assert_not_called()

    def test_stats_initialized_at_zero(self):
        feed, _, _ = self._make_feed()
        assert feed.signals_generated == 0
        assert feed.momentum_detections == 0


# ============================================================================
# parse_window_from_slug Tests
# ============================================================================

class TestParseWindowFromSlug:
    def test_5m_slug(self):
        assert parse_window_from_slug("btc-updown-5m-1770937500") == 300

    def test_15m_slug(self):
        assert parse_window_from_slug("eth-updown-15m-1770937200") == 900

    def test_unknown_format(self):
        assert parse_window_from_slug("weird-slug-no-window") == 900  # fallback

    def test_1m_slug(self):
        assert parse_window_from_slug("sol-updown-1m-123") == 60


# ============================================================================
# ArmState Tests
# ============================================================================

class TestArmState:
    def test_fill_rate_zero_pulls(self):
        arm = ArmState(offset=0.01, alpha=1.0, beta=1.0,
                       total_pulls=0, total_fills=0, total_profit=0.0)
        assert arm.fill_rate == 0.0

    def test_fill_rate_calculation(self):
        arm = ArmState(offset=0.01, alpha=1.0, beta=1.0,
                       total_pulls=10, total_fills=7, total_profit=0.0)
        assert abs(arm.fill_rate - 0.7) < 0.001

    def test_avg_profit_zero_fills(self):
        arm = ArmState(offset=0.01, alpha=1.0, beta=1.0,
                       total_pulls=5, total_fills=0, total_profit=0.0)
        assert arm.avg_profit == 0.0

    def test_avg_profit_calculation(self):
        arm = ArmState(offset=0.01, alpha=1.0, beta=1.0,
                       total_pulls=10, total_fills=5, total_profit=25.0)
        assert abs(arm.avg_profit - 5.0) < 0.001

    def test_sample_returns_0_to_1(self):
        arm = ArmState(offset=0.01, alpha=2.0, beta=3.0,
                       total_pulls=4, total_fills=1, total_profit=0.0)
        for _ in range(50):
            s = arm.sample()
            assert 0.0 <= s <= 1.0


# ============================================================================
# Config Helper Method Tests (used by momentum feed)
# ============================================================================

class TestConfigHelpers:
    def test_get_asset_filter_empty(self):
        config = make_config(asset_filter="")
        assert config.get_asset_filter() == []

    def test_get_asset_filter_single(self):
        config = make_config(asset_filter="BTC")
        assert config.get_asset_filter() == ["BTC"]

    def test_get_asset_filter_multiple(self):
        config = make_config(asset_filter="BTC,ETH,SOL")
        assert config.get_asset_filter() == ["BTC", "ETH", "SOL"]

    def test_get_market_window_default(self):
        config = make_config(market_window_secs=300)
        assert config.get_market_window("BTC") == 300

    def test_get_market_window_15m_override(self):
        config = make_config(market_window_secs=300, market_window_15m_assets="ETH,SOL")
        assert config.get_market_window("ETH") == 900
        assert config.get_market_window("BTC") == 300

    def test_get_market_windows_dual(self):
        config = make_config(
            market_window_secs=300,
            dual_window_assets="BTC",
        )
        windows = config.get_market_windows("BTC")
        assert 300 in windows
        assert 900 in windows

    def test_get_market_windows_single(self):
        config = make_config(market_window_secs=300, dual_window_assets="")
        windows = config.get_market_windows("BTC")
        assert windows == [300]

    def test_get_min_secs_remaining_5m(self):
        config = make_config(min_secs_remaining=360)
        # 5m window → capped at 40% of 300 = 120
        assert config.get_min_secs_remaining(300) == 120

    def test_get_min_secs_remaining_15m(self):
        config = make_config(min_secs_remaining=360)
        assert config.get_min_secs_remaining(900) == 360

    def test_get_asset_multiplier(self):
        config = make_config()
        assert config.get_asset_multiplier("BTC") == 1.0
        assert config.get_asset_multiplier("ETH") == 1.2
        assert config.get_asset_multiplier("XRP") == 0.8
        assert config.get_asset_multiplier("DOGE") == 1.0  # default


class TestSignalGuardShadowAssets:
    """Shadow assets pass asset filter so guard_passed reflects real criteria."""

    def _make_guard(self, asset_filter="BTC", shadow_assets="ETH"):
        config = make_config(
            asset_filter=asset_filter,
            shadow_assets=shadow_assets,
            min_entry_price=0.20,
            max_entry_price=0.98,
            momentum_trigger_pct=0.003,
        )
        store = MagicMock()
        store.count_open.return_value = 0
        store.get_open_positions.return_value = []
        return SignalGuard(config, store)

    def _signal(self, asset="ETH", price=0.85, momentum_pct=0.004, time_remaining=180):
        return {
            "asset": asset,
            "price": price,
            "momentum_pct": momentum_pct,
            "time_remaining_secs": time_remaining,
            "source": "binance_momentum",
            "outcome": "Up",
            "slug": f"{asset.lower()}-updown-5m-9999999",
        }

    def test_shadow_asset_not_blocked_by_asset_filter(self):
        guard = self._make_guard(asset_filter="BTC", shadow_assets="ETH")
        result = guard.check(self._signal(asset="ETH", price=0.85))
        assert "asset_not_in_filter" not in result.reasons

    def test_non_shadow_non_filter_asset_still_blocked(self):
        guard = self._make_guard(asset_filter="BTC", shadow_assets="ETH")
        result = guard.check(self._signal(asset="SOL", price=0.85))
        assert "asset_not_in_filter" in result.reasons

    def test_shadow_asset_no_filter_reason_on_pass(self):
        guard = self._make_guard(asset_filter="BTC", shadow_assets="ETH")
        result = guard.check(self._signal(asset="ETH", price=0.85, momentum_pct=0.004))
        assert "asset_not_in_filter" not in result.reasons

    def test_shadow_asset_no_filter_reason_on_fail(self):
        guard = self._make_guard(asset_filter="BTC", shadow_assets="ETH")
        result = guard.check(self._signal(asset="ETH", price=0.10))  # price out of range
        assert "asset_not_in_filter" not in result.reasons
        assert "price_out_of_range" in result.reasons


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
