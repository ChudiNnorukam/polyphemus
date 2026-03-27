"""Infrastructure hardening tests for snipe exit path + FAK SELL + config safety.

Tests cover:
1. Config validation (Pydantic validators for percentage/ratio fields)
2. mid_price_stop + snipe_with_stop bypass (ExitManager._evaluate)
3. FAK SELL routing in ExitHandler.execute_exit
4. _poll_for_fill lifecycle (fill, partial, timeout, cancel)
5. Edge cases (metadata=None, 0-fill, race conditions)
"""

import asyncio
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from polyphemus.types import (
    Position, ExitSignal, ExitReason, ExecutionResult, OrderStatus,
    TAKER_POLL_MAX, MIN_SHARES_FOR_SELL, ORDER_POLL_INTERVAL,
)
from polyphemus.config import Settings
from polyphemus.exit_manager import ExitManager
from polyphemus.exit_handler import ExitHandler
from polyphemus.position_store import PositionStore


# ============================================================================
# Test Helpers
# ============================================================================

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
        token_id="0xtoken_exit_test",
        slug="btc-updown-5m-123456",
        entry_price=0.90,
        entry_size=100.0,
        entry_time=now - timedelta(seconds=10),  # 10s ago (past 2s hold)
        entry_tx_hash="0xhash_exit",
        market_end_time=now + timedelta(minutes=3),
        current_price=0.85,
    )
    defaults.update(overrides)
    return Position(**defaults)


def _order_details(status, size_matched=100.0, original_size=100.0, price=0.50):
    """Helper to create get_order_details mock return value."""
    return {"status": status, "size_matched": size_matched,
            "original_size": original_size, "price": price}


def _make_exit_handler(**config_overrides):
    """Create ExitHandler with mocked ClobWrapper."""
    config = make_config(**config_overrides)
    clob = AsyncMock()
    return ExitHandler(clob, config), clob


# ============================================================================
# 1. Config Validation Tests
# ============================================================================

class TestConfigValidation:
    """Pydantic validators catch bad percentage/ratio values at construction time."""

    def test_valid_mid_price_stop_pct(self):
        """0.08 (8%) loads successfully."""
        config = make_config(mid_price_stop_pct=0.08)
        assert config.mid_price_stop_pct == 0.08

    def test_mid_price_stop_pct_zero_is_valid(self):
        """0.0 means disabled, must not reject."""
        config = make_config(mid_price_stop_pct=0.0)
        assert config.mid_price_stop_pct == 0.0

    def test_mid_price_stop_pct_one_is_valid(self):
        """1.0 (100% drop) is edge but valid."""
        config = make_config(mid_price_stop_pct=1.0)
        assert config.mid_price_stop_pct == 1.0

    def test_mid_price_stop_pct_negative_rejected(self):
        """Negative stop pct would cause stop_price > entry_price (instant exit)."""
        with pytest.raises(Exception):
            make_config(mid_price_stop_pct=-0.1)

    def test_mid_price_stop_pct_over_one_rejected(self):
        """Stop pct > 1.0 causes negative stop_price (never fires)."""
        with pytest.raises(Exception):
            make_config(mid_price_stop_pct=1.5)

    def test_igoc_stop_pct_negative_rejected(self):
        with pytest.raises(Exception):
            make_config(igoc_stop_pct=-0.05)

    def test_igoc_stop_pct_over_one_rejected(self):
        with pytest.raises(Exception):
            make_config(igoc_stop_pct=1.1)

    def test_trailing_stop_pct_negative_rejected(self):
        with pytest.raises(Exception):
            make_config(trailing_stop_pct=-0.01)

    def test_trailing_stop_pct_over_one_rejected(self):
        with pytest.raises(Exception):
            make_config(trailing_stop_pct=1.2)

    def test_stop_loss_pct_negative_rejected(self):
        with pytest.raises(Exception):
            make_config(stop_loss_pct=-0.1)

    def test_stop_loss_pct_over_one_rejected(self):
        with pytest.raises(Exception):
            make_config(stop_loss_pct=2.0)


# ============================================================================
# 2. mid_price_stop + snipe_with_stop Bypass Tests (ExitManager)
# ============================================================================

class TestSnipeWithStop:
    """Verify snipe_with_stop bypass enables mid_price_stop for high-entry snipes
    while preserving all other hold_to_resolution protections."""

    def _make_snipe_position(self, entry_price=0.90, source="resolution_snipe",
                             current_price=None, secs_held=10):
        """Create a snipe position with configurable entry and current price."""
        now = datetime.now(timezone.utc)
        if current_price is None:
            # Default: price dropped 8% (at stop level)
            current_price = entry_price * (1 - 0.08)
        return Position(
            token_id="tok_snipe_stop",
            slug=f"btc-updown-5m-{int(time.time() // 300) * 300}",
            entry_price=entry_price,
            entry_size=100.0,
            entry_time=now - timedelta(seconds=secs_held),
            entry_tx_hash="0xsnipe_stop",
            market_end_time=now + timedelta(seconds=120),
            current_price=current_price,
            metadata={
                "source": source,
                "entry_momentum_direction": "up",
            },
        )

    def test_snipe_at_080_triggers_mid_price_stop(self):
        """Snipe with entry >= 0.80 should trigger mid_price_stop when price drops 8%."""
        config = make_config(
            mid_price_stop_enabled=True,
            mid_price_stop_pct=0.08,
        )
        store = PositionStore()
        pos = self._make_snipe_position(entry_price=0.90, current_price=0.90 * 0.919)  # just below stop
        store.add(pos)

        mgr = ExitManager(store, config)
        exits = mgr.check_all(datetime.now(timezone.utc))

        mid_stops = [e for e in exits if e.reason == ExitReason.MID_PRICE_STOP.value]
        assert len(mid_stops) == 1, f"Expected mid_price_stop, got {[e.reason for e in exits]}"

    def test_snipe_at_080_boundary_triggers(self):
        """Entry at exactly 0.80 should trigger (>= gate)."""
        config = make_config(
            mid_price_stop_enabled=True,
            mid_price_stop_pct=0.08,
        )
        store = PositionStore()
        pos = self._make_snipe_position(entry_price=0.80, current_price=0.80 * 0.919)
        store.add(pos)

        mgr = ExitManager(store, config)
        exits = mgr.check_all(datetime.now(timezone.utc))

        mid_stops = [e for e in exits if e.reason == ExitReason.MID_PRICE_STOP.value]
        assert len(mid_stops) == 1

    def test_snipe_at_079_does_not_trigger(self):
        """Entry at 0.79 (below 0.80 gate) should NOT trigger mid_price_stop."""
        config = make_config(
            mid_price_stop_enabled=True,
            mid_price_stop_pct=0.08,
        )
        store = PositionStore()
        pos = self._make_snipe_position(entry_price=0.79, current_price=0.79 * 0.91)
        store.add(pos)

        mgr = ExitManager(store, config)
        exits = mgr.check_all(datetime.now(timezone.utc))

        mid_stops = [e for e in exits if e.reason == ExitReason.MID_PRICE_STOP.value]
        assert len(mid_stops) == 0, f"Snipe at 0.79 should NOT trigger mid_price_stop"

    def test_snipe_15m_also_triggers(self):
        """resolution_snipe_15m source should also get the bypass."""
        config = make_config(
            mid_price_stop_enabled=True,
            mid_price_stop_pct=0.08,
        )
        store = PositionStore()
        pos = self._make_snipe_position(source="resolution_snipe_15m", entry_price=0.90,
                                         current_price=0.90 * 0.919)
        store.add(pos)

        mgr = ExitManager(store, config)
        exits = mgr.check_all(datetime.now(timezone.utc))

        mid_stops = [e for e in exits if e.reason == ExitReason.MID_PRICE_STOP.value]
        assert len(mid_stops) == 1

    def test_oracle_flip_not_stopped(self):
        """oracle_flip should NEVER trigger mid_price_stop (not in bypass list)."""
        config = make_config(
            mid_price_stop_enabled=True,
            mid_price_stop_pct=0.08,
        )
        store = PositionStore()
        now = datetime.now(timezone.utc)
        pos = Position(
            token_id="tok_flip_nostop",
            slug=f"btc-updown-5m-{int(time.time() // 300) * 300}",
            entry_price=0.05,
            entry_size=500.0,
            entry_time=now - timedelta(seconds=10),
            entry_tx_hash="0xflip_nostop",
            market_end_time=now + timedelta(seconds=120),
            current_price=0.005,  # 90% drop
            metadata={"source": "oracle_flip"},
        )
        store.add(pos)

        mgr = ExitManager(store, config)
        exits = mgr.check_all(now)

        mid_stops = [e for e in exits if e.reason == ExitReason.MID_PRICE_STOP.value]
        assert len(mid_stops) == 0, "oracle_flip must never trigger mid_price_stop"

    def test_reversal_short_not_stopped(self):
        """reversal_short should NOT trigger mid_price_stop (hold_to_resolution, no bypass)."""
        config = make_config(
            mid_price_stop_enabled=True,
            mid_price_stop_pct=0.08,
        )
        store = PositionStore()
        now = datetime.now(timezone.utc)
        pos = Position(
            token_id="tok_rev_nostop",
            slug=f"btc-updown-5m-{int(time.time() // 300) * 300}",
            entry_price=0.30,
            entry_size=100.0,
            entry_time=now - timedelta(seconds=10),
            entry_tx_hash="0xrev_nostop",
            market_end_time=now + timedelta(seconds=120),
            current_price=0.10,  # massive drop
            metadata={"source": "reversal_short"},
        )
        store.add(pos)

        mgr = ExitManager(store, config)
        exits = mgr.check_all(now)

        mid_stops = [e for e in exits if e.reason == ExitReason.MID_PRICE_STOP.value]
        assert len(mid_stops) == 0, "reversal_short must never trigger mid_price_stop"

    def test_momentum_still_triggers_mid_price_stop(self):
        """Regular momentum position (no hold_to_resolution) should still trigger."""
        config = make_config(
            mid_price_stop_enabled=True,
            mid_price_stop_pct=0.08,
        )
        store = PositionStore()
        now = datetime.now(timezone.utc)
        pos = Position(
            token_id="tok_momentum_stop",
            slug=f"btc-updown-5m-{int(time.time() // 300) * 300}",
            entry_price=0.85,
            entry_size=100.0,
            entry_time=now - timedelta(seconds=10),
            entry_tx_hash="0xmomentum_stop",
            market_end_time=now + timedelta(seconds=120),
            current_price=0.85 * 0.919,  # below 8% stop
            metadata={"source": "binance_momentum"},
        )
        store.add(pos)

        mgr = ExitManager(store, config)
        exits = mgr.check_all(now)

        mid_stops = [e for e in exits if e.reason == ExitReason.MID_PRICE_STOP.value]
        assert len(mid_stops) == 1, "Momentum should still trigger mid_price_stop"

    def test_snipe_still_reaches_pre_resolution_exit(self):
        """Snipe should still fire pre_resolution_exit when losing (hold_to_resolution intact)."""
        config = make_config(
            mid_price_stop_enabled=False,  # disabled to isolate pre_resolution_exit
            pre_resolution_exit_secs=15,
        )
        store = PositionStore()
        now = datetime.now(timezone.utc)
        pos = Position(
            token_id="tok_snipe_pre",
            slug=f"btc-updown-5m-{int(time.time() // 300) * 300}",
            entry_price=0.90,
            entry_size=100.0,
            entry_time=now - timedelta(seconds=60),
            entry_tx_hash="0xsnipe_pre",
            market_end_time=now + timedelta(seconds=10),  # 10s left < 15s threshold
            current_price=0.80,  # losing
            metadata={"source": "resolution_snipe"},
        )
        store.add(pos)

        mgr = ExitManager(store, config)
        exits = mgr.check_all(now)

        pre_exits = [e for e in exits if e.reason == ExitReason.PRE_RESOLUTION_EXIT.value]
        assert len(pre_exits) == 1, "Losing snipe should still reach pre_resolution_exit"

    def test_snipe_max_hold_does_not_fire(self):
        """Snipe held beyond max_hold should NOT trigger max_hold (hold_to_resolution)."""
        config = make_config(max_hold_mins=5)
        store = PositionStore()
        now = datetime.now(timezone.utc)
        pos = Position(
            token_id="tok_snipe_mh",
            slug=f"btc-updown-5m-{int(time.time() // 300) * 300}",
            entry_price=0.90,
            entry_size=100.0,
            entry_time=now - timedelta(minutes=10),  # way past max_hold
            entry_tx_hash="0xsnipe_mh",
            market_end_time=now + timedelta(seconds=120),
            current_price=0.85,
            metadata={"source": "resolution_snipe"},
        )
        store.add(pos)

        mgr = ExitManager(store, config)
        exits = mgr.check_all(now)

        max_holds = [e for e in exits if e.reason == ExitReason.MAX_HOLD.value]
        assert len(max_holds) == 0, "Snipe must never trigger max_hold"

    def test_snipe_uses_global_stop_pct_not_igoc(self):
        """Snipe should use mid_price_stop_pct, NOT igoc_stop_pct."""
        config = make_config(
            mid_price_stop_enabled=True,
            mid_price_stop_pct=0.08,
            igoc_stop_pct=0.25,  # different value
        )
        store = PositionStore()
        # Price dropped exactly 10%: below 8% stop, above 25% igoc stop
        pos = self._make_snipe_position(entry_price=0.90, current_price=0.81)
        store.add(pos)

        mgr = ExitManager(store, config)
        exits = mgr.check_all(datetime.now(timezone.utc))

        mid_stops = [e for e in exits if e.reason == ExitReason.MID_PRICE_STOP.value]
        assert len(mid_stops) == 1, "Should use 8% global stop, not 25% IGOC stop"

    def test_mid_price_stop_disabled_no_trigger(self):
        """When mid_price_stop_enabled=False, nothing fires."""
        config = make_config(
            mid_price_stop_enabled=False,
            mid_price_stop_pct=0.08,
        )
        store = PositionStore()
        pos = self._make_snipe_position(entry_price=0.90, current_price=0.50)  # massive drop
        store.add(pos)

        mgr = ExitManager(store, config)
        exits = mgr.check_all(datetime.now(timezone.utc))

        mid_stops = [e for e in exits if e.reason == ExitReason.MID_PRICE_STOP.value]
        assert len(mid_stops) == 0

    def test_2s_hold_requirement(self):
        """mid_price_stop requires 2s hold time. Entry 1s ago should not trigger."""
        config = make_config(
            mid_price_stop_enabled=True,
            mid_price_stop_pct=0.08,
        )
        store = PositionStore()
        pos = self._make_snipe_position(entry_price=0.90, current_price=0.80, secs_held=1)
        store.add(pos)

        mgr = ExitManager(store, config)
        exits = mgr.check_all(datetime.now(timezone.utc))

        mid_stops = [e for e in exits if e.reason == ExitReason.MID_PRICE_STOP.value]
        assert len(mid_stops) == 0, "Entry < 2s ago should not trigger mid_price_stop"

    def test_price_above_stop_no_trigger(self):
        """Price above stop threshold should not trigger."""
        config = make_config(
            mid_price_stop_enabled=True,
            mid_price_stop_pct=0.08,
        )
        store = PositionStore()
        # Price at 0.84 = 6.7% drop from 0.90 (below 8% threshold)
        pos = self._make_snipe_position(entry_price=0.90, current_price=0.84)
        store.add(pos)

        mgr = ExitManager(store, config)
        exits = mgr.check_all(datetime.now(timezone.utc))

        mid_stops = [e for e in exits if e.reason == ExitReason.MID_PRICE_STOP.value]
        assert len(mid_stops) == 0


# ============================================================================
# 3. FAK SELL Routing Tests (ExitHandler)
# ============================================================================

class TestFakSellRouting:
    """Verify FAK SELL fires for snipe mid_price_stop exits and falls back
    to standard taker on failure."""

    @pytest.mark.asyncio
    async def test_fak_fires_for_snipe_mid_price_stop(self):
        """resolution_snipe + mid_price_stop reason should use FAK SELL."""
        handler, clob = _make_exit_handler()
        clob.get_share_balance = AsyncMock(return_value=100.0)
        clob.place_fak_order = AsyncMock(return_value=ExecutionResult(
            success=True, order_id="0xfak001"
        ))
        clob.get_order_details = AsyncMock(return_value=_order_details(OrderStatus.FILLED))

        pos = make_position(
            current_price=0.82, entry_price=0.90,
            metadata={"source": "resolution_snipe"},
        )
        signal = ExitSignal(
            token_id=pos.token_id,
            reason=ExitReason.MID_PRICE_STOP.value,
            exit_price=0.82,
        )

        result = await handler.execute_exit(pos, signal)

        assert result.success is True
        clob.place_fak_order.assert_called_once()
        # Standard taker should NOT be called when FAK succeeds
        clob.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_fak_fires_for_snipe_15m(self):
        """resolution_snipe_15m should also use FAK SELL."""
        handler, clob = _make_exit_handler()
        clob.get_share_balance = AsyncMock(return_value=100.0)
        clob.place_fak_order = AsyncMock(return_value=ExecutionResult(
            success=True, order_id="0xfak002"
        ))
        clob.get_order_details = AsyncMock(return_value=_order_details(OrderStatus.FILLED))

        pos = make_position(
            current_price=0.82, entry_price=0.90,
            metadata={"source": "resolution_snipe_15m"},
        )
        signal = ExitSignal(
            token_id=pos.token_id,
            reason=ExitReason.MID_PRICE_STOP.value,
            exit_price=0.82,
        )

        result = await handler.execute_exit(pos, signal)

        assert result.success is True
        clob.place_fak_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_fak_not_for_momentum(self):
        """Momentum source should use standard taker, NOT FAK."""
        handler, clob = _make_exit_handler()
        clob.get_share_balance = AsyncMock(return_value=100.0)
        clob.place_order = AsyncMock(return_value=ExecutionResult(
            success=True, order_id="0xtaker001"
        ))
        clob.get_order_details = AsyncMock(return_value=_order_details(OrderStatus.FILLED))

        pos = make_position(
            current_price=0.70, entry_price=0.80,
            metadata={"source": "binance_momentum"},
        )
        signal = ExitSignal(
            token_id=pos.token_id,
            reason=ExitReason.MID_PRICE_STOP.value,
            exit_price=0.70,
        )

        result = await handler.execute_exit(pos, signal)

        assert result.success is True
        clob.place_fak_order.assert_not_called()
        clob.place_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_fak_not_for_profit_target(self):
        """Even snipe source with profit_target reason should NOT use FAK."""
        handler, clob = _make_exit_handler()
        clob.get_share_balance = AsyncMock(return_value=100.0)
        clob.place_order = AsyncMock(return_value=ExecutionResult(
            success=True, order_id="0xtaker002"
        ))
        clob.get_order_details = AsyncMock(return_value=_order_details(OrderStatus.FILLED))

        pos = make_position(
            current_price=0.95, entry_price=0.90,
            metadata={"source": "resolution_snipe"},
        )
        signal = ExitSignal(
            token_id=pos.token_id,
            reason=ExitReason.PROFIT_TARGET.value,
            exit_price=0.95,
        )

        result = await handler.execute_exit(pos, signal)

        assert result.success is True
        clob.place_fak_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_fak_zero_fill_falls_back_to_taker(self):
        """FAK with 0 fill should fall through to standard taker SELL."""
        handler, clob = _make_exit_handler()
        clob.get_share_balance = AsyncMock(return_value=100.0)
        # FAK places but gets no fill
        clob.place_fak_order = AsyncMock(return_value=ExecutionResult(
            success=True, order_id="0xfak_nofill"
        ))
        # FAK poll returns CANCELLED with 0 fill
        fak_details = _order_details(OrderStatus.CANCELLED, size_matched=0.0)
        # Standard taker succeeds after FAK fallback
        taker_details = _order_details(OrderStatus.FILLED, size_matched=100.0)
        clob.get_order_details = AsyncMock(side_effect=[fak_details, taker_details])
        clob.place_order = AsyncMock(return_value=ExecutionResult(
            success=True, order_id="0xtaker_fallback"
        ))

        pos = make_position(
            current_price=0.82, entry_price=0.90,
            metadata={"source": "resolution_snipe"},
        )
        signal = ExitSignal(
            token_id=pos.token_id,
            reason=ExitReason.MID_PRICE_STOP.value,
            exit_price=0.82,
        )

        result = await handler.execute_exit(pos, signal)

        assert result.success is True
        clob.place_fak_order.assert_called_once()
        clob.place_order.assert_called_once()  # fell through to taker

    @pytest.mark.asyncio
    async def test_fak_rejection_falls_back_to_taker(self):
        """FAK placement failure should fall through to taker."""
        handler, clob = _make_exit_handler()
        clob.get_share_balance = AsyncMock(return_value=100.0)
        clob.place_fak_order = AsyncMock(return_value=ExecutionResult(
            success=False, error="FAK rejected: no bids"
        ))
        clob.place_order = AsyncMock(return_value=ExecutionResult(
            success=True, order_id="0xtaker_after_fak"
        ))
        clob.get_order_details = AsyncMock(return_value=_order_details(OrderStatus.FILLED))

        pos = make_position(
            current_price=0.82, entry_price=0.90,
            metadata={"source": "resolution_snipe"},
        )
        signal = ExitSignal(
            token_id=pos.token_id,
            reason=ExitReason.MID_PRICE_STOP.value,
            exit_price=0.82,
        )

        result = await handler.execute_exit(pos, signal)

        assert result.success is True
        clob.place_fak_order.assert_called_once()
        clob.place_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_metadata_none_does_not_crash(self):
        """Position with metadata=None should not crash FAK routing."""
        handler, clob = _make_exit_handler()
        clob.get_share_balance = AsyncMock(return_value=100.0)
        clob.place_order = AsyncMock(return_value=ExecutionResult(
            success=True, order_id="0xtaker_nometa"
        ))
        clob.get_order_details = AsyncMock(return_value=_order_details(OrderStatus.FILLED))

        pos = make_position(
            current_price=0.70, entry_price=0.80,
            metadata=None,
        )
        signal = ExitSignal(
            token_id=pos.token_id,
            reason=ExitReason.MID_PRICE_STOP.value,
            exit_price=0.70,
        )

        result = await handler.execute_exit(pos, signal)

        assert result.success is True
        clob.place_fak_order.assert_not_called()  # no source = no FAK

    @pytest.mark.asyncio
    async def test_metadata_empty_dict_does_not_crash(self):
        """Position with metadata={} should not crash FAK routing."""
        handler, clob = _make_exit_handler()
        clob.get_share_balance = AsyncMock(return_value=100.0)
        clob.place_order = AsyncMock(return_value=ExecutionResult(
            success=True, order_id="0xtaker_empty"
        ))
        clob.get_order_details = AsyncMock(return_value=_order_details(OrderStatus.FILLED))

        pos = make_position(
            current_price=0.70, entry_price=0.80,
            metadata={},
        )
        signal = ExitSignal(
            token_id=pos.token_id,
            reason=ExitReason.MID_PRICE_STOP.value,
            exit_price=0.70,
        )

        result = await handler.execute_exit(pos, signal)

        assert result.success is True
        clob.place_fak_order.assert_not_called()


# ============================================================================
# 4. _poll_for_fill Lifecycle Tests
# ============================================================================

class TestPollForFill:
    """Test fill polling: full fill, partial fill, timeout, cancel."""

    @pytest.mark.asyncio
    async def test_filled_status_returns_success(self):
        handler, clob = _make_exit_handler()
        clob.get_order_details = AsyncMock(return_value=_order_details(
            OrderStatus.FILLED, size_matched=100.0
        ))

        result = await handler._poll_for_fill(
            order_id="0xfill001", slug="btc-test", sell_price=0.80,
            shares=100.0, exit_reason="mid_price_stop",
        )

        assert result.success is True
        assert result.fill_size == 100.0

    @pytest.mark.asyncio
    async def test_matched_status_returns_success(self):
        handler, clob = _make_exit_handler()
        clob.get_order_details = AsyncMock(return_value=_order_details(
            OrderStatus.MATCHED, size_matched=100.0
        ))

        result = await handler._poll_for_fill(
            order_id="0xmatch001", slug="btc-test", sell_price=0.80,
            shares=100.0, exit_reason="mid_price_stop",
        )

        assert result.success is True

    @pytest.mark.asyncio
    async def test_cancelled_with_partial_fill_returns_success(self):
        """CANCELLED but size_matched >= MIN_SHARES should return partial success."""
        handler, clob = _make_exit_handler()
        clob.get_order_details = AsyncMock(return_value=_order_details(
            OrderStatus.CANCELLED, size_matched=60.0  # > MIN_SHARES_FOR_SELL (5)
        ))

        result = await handler._poll_for_fill(
            order_id="0xpartial001", slug="btc-test", sell_price=0.80,
            shares=100.0, exit_reason="mid_price_stop",
        )

        assert result.success is True
        assert result.fill_size == 60.0

    @pytest.mark.asyncio
    async def test_cancelled_with_no_fill_returns_failure(self):
        """CANCELLED with 0 fill should return failure."""
        handler, clob = _make_exit_handler()
        clob.get_order_details = AsyncMock(return_value=_order_details(
            OrderStatus.CANCELLED, size_matched=0.0
        ))

        result = await handler._poll_for_fill(
            order_id="0xnofill001", slug="btc-test", sell_price=0.80,
            shares=100.0, exit_reason="mid_price_stop",
        )

        assert result.success is False

    @pytest.mark.asyncio
    async def test_timeout_with_partial_fill_captures_it(self):
        """Timeout but size_matched >= MIN_SHARES should capture partial fill."""
        handler, clob = _make_exit_handler()
        # All polls return LIVE with partial fill
        live_details = _order_details(OrderStatus.LIVE, size_matched=40.0)
        # Final check after timeout also returns partial
        timeout_details = _order_details(OrderStatus.LIVE, size_matched=40.0)
        clob.get_order_details = AsyncMock(return_value=live_details)
        clob.cancel_order = AsyncMock(return_value=True)

        result = await handler._poll_for_fill(
            order_id="0xtimeout_partial", slug="btc-test", sell_price=0.80,
            shares=100.0, exit_reason="mid_price_stop", max_polls=1,
        )

        assert result.success is True
        assert result.fill_size == 40.0
        clob.cancel_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_timeout_with_zero_fill_returns_failure(self):
        """Timeout with 0 fill should cancel and return failure."""
        handler, clob = _make_exit_handler()
        clob.get_order_details = AsyncMock(return_value=_order_details(
            OrderStatus.LIVE, size_matched=0.0
        ))
        clob.cancel_order = AsyncMock(return_value=True)

        result = await handler._poll_for_fill(
            order_id="0xtimeout_zero", slug="btc-test", sell_price=0.80,
            shares=100.0, exit_reason="mid_price_stop", max_polls=1,
        )

        assert result.success is False
        assert result.reason == "timeout"
        clob.cancel_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_api_error_continues_polling(self):
        """get_order_details returning None should continue polling, not crash."""
        handler, clob = _make_exit_handler()
        # First poll: API error (None), second: FILLED
        clob.get_order_details = AsyncMock(side_effect=[
            None,
            _order_details(OrderStatus.FILLED, size_matched=100.0),
        ])

        result = await handler._poll_for_fill(
            order_id="0xapi_err", slug="btc-test", sell_price=0.80,
            shares=100.0, exit_reason="mid_price_stop", max_polls=2,
        )

        assert result.success is True


# ============================================================================
# 5. execute_exit Lifecycle Tests
# ============================================================================

class TestExecuteExitLifecycle:
    """End-to-end tests for the execute_exit method."""

    @pytest.mark.asyncio
    async def test_market_resolved_skips_sell_returns_last_known_price(self):
        handler, clob = _make_exit_handler()
        pos = make_position(current_price=0.95, entry_price=0.90)
        signal = ExitSignal(
            token_id=pos.token_id,
            reason=ExitReason.MARKET_RESOLVED.value,
        )

        result = await handler.execute_exit(pos, signal)

        assert result.success is True
        assert result.fill_price == 0.95
        assert result.reason == "market_resolved"
        clob.place_order.assert_not_called()
        clob.place_fak_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_insufficient_shares_fails(self):
        handler, clob = _make_exit_handler()
        clob.get_share_balance = AsyncMock(return_value=3.0)  # < MIN_SHARES_FOR_SELL (5)
        pos = make_position()
        signal = ExitSignal(
            token_id=pos.token_id,
            reason=ExitReason.STOP_LOSS.value,
        )

        result = await handler.execute_exit(pos, signal)

        assert result.success is False
        assert result.error == "insufficient_shares"

    @pytest.mark.asyncio
    async def test_orderbook_does_not_exist_treated_as_resolved(self):
        """'does not exist' error from CLOB should be treated as market resolved."""
        handler, clob = _make_exit_handler()
        clob.get_share_balance = AsyncMock(return_value=100.0)
        clob.place_order = AsyncMock(return_value=ExecutionResult(
            success=False, error="Orderbook does not exist for this market"
        ))

        pos = make_position(current_price=0.80, entry_price=0.90)
        signal = ExitSignal(
            token_id=pos.token_id,
            reason=ExitReason.STOP_LOSS.value,
            exit_price=0.80,
        )

        result = await handler.execute_exit(pos, signal)

        assert result.success is True
        assert result.reason == "market_resolved"
        assert result.fill_price == 0.80  # last known price

    @pytest.mark.asyncio
    async def test_retry_on_timeout_with_aggressive_pricing(self):
        """First SELL timeout should retry with $0.10 discount."""
        handler, clob = _make_exit_handler()
        clob.get_share_balance = AsyncMock(return_value=100.0)

        # First placement succeeds
        clob.place_order = AsyncMock(side_effect=[
            ExecutionResult(success=True, order_id="0xfirst"),
            ExecutionResult(success=True, order_id="0xretry"),
        ])
        # First attempt: all LIVE for TAKER_POLL_MAX polls + 1 timeout check = 11 calls
        # Then retry: FILLED on first poll
        live = _order_details(OrderStatus.LIVE, size_matched=0.0)
        zero_fill = _order_details(OrderStatus.LIVE, size_matched=0.0)
        filled = _order_details(OrderStatus.FILLED, size_matched=100.0)
        clob.get_order_details = AsyncMock(side_effect=[
            *[live] * TAKER_POLL_MAX,  # 10 polls all LIVE (loop exhausted)
            zero_fill,                  # timeout check: 0 fill -> retry path
            filled,                     # retry poll: FILLED
        ])
        clob.cancel_order = AsyncMock(return_value=True)

        pos = make_position(current_price=0.80, entry_price=0.90)
        signal = ExitSignal(
            token_id=pos.token_id,
            reason=ExitReason.STOP_LOSS.value,
            exit_price=0.80,
        )

        result = await handler.execute_exit(pos, signal)

        assert result.success is True
        # Should have called place_order twice (first + retry)
        assert clob.place_order.call_count == 2
        # Retry price should be exit_price - 0.10 = 0.70
        retry_call = clob.place_order.call_args_list[1]
        assert retry_call.kwargs["price"] == pytest.approx(0.70, abs=0.01)
