"""Tests for AccumulatorEngine — TDD coverage for state machine, entry logic,
fill tracking, settlement, and capital isolation."""

import asyncio
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from .types import AccumulatorState, AccumulatorPosition, Position, GAMMA_API_URL
from .config import Settings
from .accumulator import AccumulatorEngine


# ============================================================================
# Order Book Fixtures
# ============================================================================

# Standard order book (BTC-like, bid pair_cost < $0.975)
MOCK_UP_BOOK = {
    "bids": [{"price": 0.48, "size": 200}, {"price": 0.47, "size": 500}],
    "asks": [{"price": 0.52, "size": 200}, {"price": 0.53, "size": 500}],
}
MOCK_DOWN_BOOK = {
    "bids": [{"price": 0.47, "size": 200}, {"price": 0.46, "size": 500}],
    "asks": [{"price": 0.53, "size": 200}, {"price": 0.54, "size": 500}],
}
# bid pair cost: 0.48 + 0.47 = 0.95 → enters

# Expensive book (pair_cost >= threshold)
MOCK_UP_BOOK_EXPENSIVE = {
    "bids": [{"price": 0.50, "size": 200}],
    "asks": [{"price": 0.51, "size": 200}],
}
MOCK_DOWN_BOOK_EXPENSIVE = {
    "bids": [{"price": 0.50, "size": 200}],
    "asks": [{"price": 0.51, "size": 200}],
}
# bid pair cost: 0.50 + 0.50 = 1.00 → skips

# Empty book
MOCK_EMPTY_BOOK = {"bids": [], "asks": []}


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def config():
    """Minimal config for accumulator tests."""
    cfg = MagicMock(spec=Settings)
    cfg.accum_dry_run = True
    cfg.accum_assets = "BTC"
    cfg.accum_window_types = "5m"
    cfg.accum_max_pair_cost = 0.975
    cfg.accum_min_profit_per_share = 0.02
    cfg.accum_min_shares = 5.0
    cfg.accum_max_shares = 500.0
    cfg.accum_max_deployed_pct = 0.80
    cfg.accum_scan_interval = 1
    cfg.accum_min_secs_remaining = 180
    cfg.accum_settle_timeout_secs = 10
    cfg.accum_maker_max_retries = 3
    cfg.accum_maker_retry_delay = 0.01
    cfg.accum_maker_price_decrement = 0.005
    cfg.accum_max_single_side_pct = 0.70
    cfg.accum_capital_pct = 0.40
    cfg.accum_order_timeout = 30
    cfg.accum_reprice_limit = 5
    cfg.accum_max_concurrent = 3
    cfg.accum_max_side_price = 0.55
    cfg.accum_hedge_deadline_secs = 120
    cfg.enable_accumulator = True
    cfg.dry_run = True
    cfg.dry_run_balance = 400.0
    cfg.max_daily_loss = 0.0
    return cfg


@pytest.fixture
def mock_clob():
    clob = AsyncMock()
    clob.get_midpoint = AsyncMock(return_value=0.48)
    clob.get_order_book = AsyncMock(return_value=MOCK_UP_BOOK)
    clob.place_order = AsyncMock(return_value=MagicMock(success=True, order_id="test_order_123"))
    clob.get_order_status = AsyncMock(return_value="FILLED")
    clob.cancel_order = AsyncMock(return_value=True)
    clob.get_balance = AsyncMock(return_value=400.0)
    return clob


@pytest.fixture
def mock_balance():
    balance = AsyncMock()
    balance.get_available_for_accumulator = AsyncMock(return_value=160.0)
    balance.get_available = AsyncMock(return_value=240.0)
    balance.get_balance = AsyncMock(return_value=400.0)
    return balance


@pytest.fixture
def mock_store():
    store = MagicMock()
    store.get_open = MagicMock(return_value=[])
    store.add = MagicMock()
    store.remove = MagicMock()
    store.get = MagicMock(return_value=None)
    return store


@pytest.fixture
def engine(config, mock_clob, mock_balance, mock_store):
    eng = AccumulatorEngine(
        clob=mock_clob,
        balance=mock_balance,
        store=mock_store,
        config=config,
    )
    return eng


def _make_pos(slug="btc-updown-5m-123", state=AccumulatorState.SCANNING, mins_left=5, **kwargs):
    """Helper to create an AccumulatorPosition with defaults."""
    defaults = dict(
        slug=slug,
        window_secs=300,
        state=state,
        up_token_id="up_token",
        down_token_id="down_token",
        market_end_time=datetime.now(tz=timezone.utc) + timedelta(minutes=mins_left),
        entry_time=datetime.now(tz=timezone.utc),
    )
    defaults.update(kwargs)
    return AccumulatorPosition(**defaults)


# ============================================================================
# Entry Evaluation Tests
# ============================================================================


class TestEvaluateOpportunity:
    def test_accepts_cheap_pair(self, engine):
        """pair_cost=0.95 < 0.975, profit=0.05 > 0.02 → True"""
        assert engine._evaluate_opportunity(0.47, 0.48) is True

    def test_rejects_expensive_pair(self, engine):
        """pair_cost=0.98 >= 0.975 → False"""
        assert engine._evaluate_opportunity(0.49, 0.49) is False

    def test_rejects_low_profit(self, engine):
        """pair_cost=0.97, profit=0.03 > 0.02 → True (barely)"""
        assert engine._evaluate_opportunity(0.485, 0.485) is True

    def test_rejects_zero_profit(self, engine):
        """pair_cost=0.99, profit=0.01 < 0.02 → False"""
        assert engine._evaluate_opportunity(0.495, 0.495) is False

    def test_rejects_asymmetric_prices(self, engine):
        """UP=0.70, DOWN=0.25, pair=0.95 → True from _evaluate_opportunity (pair cost OK).
        Directional guard (Bug #39, accum_max_side_price) now enforced in _evaluate_and_enter."""
        assert engine._evaluate_opportunity(0.70, 0.25) is True

    def test_rejects_at_boundary(self, engine):
        """pair_cost exactly at 0.975 → False (>= check)"""
        assert engine._evaluate_opportunity(0.4875, 0.4875) is False


# ============================================================================
# State Transition Tests
# ============================================================================


class TestStateTransitions:
    def test_initial_state_is_idle(self, engine):
        assert engine._get_aggregate_state() == "idle"
        assert len(engine._positions) == 0

    def test_transition_updates_position_state(self, engine):
        pos = _make_pos(state=AccumulatorState.SCANNING)
        engine._positions[pos.slug] = pos
        engine._transition(AccumulatorState.ACCUMULATING, pos)
        assert pos.state == AccumulatorState.ACCUMULATING

    def test_aggregate_state_active_with_positions(self, engine):
        pos = _make_pos()
        engine._positions[pos.slug] = pos
        assert engine._get_aggregate_state() == "active"


# ============================================================================
# Scan for Window Tests
# ============================================================================


class TestScanForWindow:
    @pytest.mark.asyncio
    async def test_creates_position_on_opportunity(self, engine, mock_clob):
        """When market discovered with good bid pair cost, create position in SCANNING."""
        # expires_at = now + 260s means market opened 40s ago (300s window) — passes 30s guard
        engine._discover_markets = AsyncMock(return_value=[{
            "slug": "btc-updown-5m-123",
            "up_token_id": "up_token",
            "down_token_id": "down_token",
            "window_secs": 300,
            "expires_at": datetime.now(tz=timezone.utc) + timedelta(seconds=260),
        }])
        # bid pair cost: 0.48 + 0.47 = 0.95 → enters
        mock_clob.get_order_book = AsyncMock(side_effect=[MOCK_UP_BOOK, MOCK_DOWN_BOOK])

        await engine._scan_for_window()

        assert "btc-updown-5m-123" in engine._positions
        assert engine._positions["btc-updown-5m-123"].state == AccumulatorState.SCANNING

    @pytest.mark.asyncio
    async def test_stays_empty_no_markets(self, engine):
        """When no markets discovered, no positions created."""
        engine._discover_markets = AsyncMock(return_value=[])

        await engine._scan_for_window()

        assert len(engine._positions) == 0

    @pytest.mark.asyncio
    async def test_stays_empty_expensive_pair(self, engine, mock_clob):
        """When bid pair cost too high, no positions created."""
        engine._discover_markets = AsyncMock(return_value=[{
            "slug": "btc-updown-5m-123",
            "up_token_id": "up_token",
            "down_token_id": "down_token",
            "window_secs": 300,
            "expires_at": datetime.now(tz=timezone.utc) + timedelta(minutes=5),
        }])
        # bid pair cost: 0.50 + 0.50 = 1.00 → skips
        mock_clob.get_order_book = AsyncMock(side_effect=[
            MOCK_UP_BOOK_EXPENSIVE, MOCK_DOWN_BOOK_EXPENSIVE
        ])

        await engine._scan_for_window()

        assert len(engine._positions) == 0

    @pytest.mark.asyncio
    async def test_scan_skips_empty_order_book(self, engine, mock_clob):
        """When order book is empty, skip market and log debug."""
        engine._discover_markets = AsyncMock(return_value=[{
            "slug": "btc-updown-5m-123",
            "up_token_id": "up_token",
            "down_token_id": "down_token",
            "window_secs": 300,
            "expires_at": datetime.now(tz=timezone.utc) + timedelta(minutes=5),
        }])
        mock_clob.get_order_book = AsyncMock(side_effect=[MOCK_UP_BOOK, MOCK_EMPTY_BOOK])

        await engine._scan_for_window()

        assert len(engine._positions) == 0

    @pytest.mark.asyncio
    async def test_scan_skips_active_slugs(self, engine, mock_clob):
        """Scan should skip slugs that already have active positions."""
        # Pre-populate an active position
        existing = _make_pos(slug="btc-updown-5m-123")
        engine._positions["btc-updown-5m-123"] = existing

        engine._discover_markets = AsyncMock(return_value=[{
            "slug": "btc-updown-5m-123",
            "up_token_id": "up_token",
            "down_token_id": "down_token",
            "window_secs": 300,
            "expires_at": datetime.now(tz=timezone.utc) + timedelta(minutes=5),
        }])

        await engine._scan_for_window()

        # Should still have only the original position (no duplicate)
        assert len(engine._positions) == 1
        assert engine._positions["btc-updown-5m-123"] is existing


# ============================================================================
# Evaluate and Enter Tests
# ============================================================================


class TestEvaluateAndEnter:
    @pytest.mark.asyncio
    async def test_places_both_orders_simultaneously(self, engine, mock_clob, mock_balance):
        """P1 fix: Both UP and DOWN orders placed simultaneously on first call (eliminates leg risk)."""
        pos = _make_pos()
        engine._positions[pos.slug] = pos
        # UP best_bid=0.46, DOWN best_bid=0.48, pair=0.94 < 0.975
        up_book = {"bids": [{"price": 0.46, "size": 200}], "asks": [{"price": 0.54, "size": 200}]}
        down_book = {"bids": [{"price": 0.48, "size": 200}], "asks": [{"price": 0.52, "size": 200}]}
        mock_clob.get_order_book = AsyncMock(side_effect=[up_book, down_book])

        # Call 1: places BOTH orders simultaneously → ACCUMULATING (orders resting, not yet filled)
        await engine._evaluate_and_enter(pos)
        assert pos.state == AccumulatorState.ACCUMULATING
        assert pos.up_order_id is not None
        assert pos.down_order_id is not None
        # _set_side_price called for UP then DOWN; DOWN price is set last → pending_order_price=down_bid
        assert pos.pending_order_price == pytest.approx(0.48, abs=0.001)

        # Call 2: _accumulate_both_sides detects dry_run instant fills, records first_fill_time
        await engine._accumulate_both_sides(pos)
        assert pos.up_qty > 0
        assert pos.down_qty > 0
        # Call 3: both legs already filled → transitions to HEDGED
        await engine._accumulate_both_sides(pos)
        assert pos.state == AccumulatorState.HEDGED
        assert pos.up_qty > 0
        assert pos.down_qty > 0

    @pytest.mark.asyncio
    async def test_removes_on_time_expiry(self, engine):
        """When time < min_secs_remaining, remove position."""
        pos = _make_pos(mins_left=1)  # Only 60s left < 180s min
        engine._positions[pos.slug] = pos

        await engine._evaluate_and_enter(pos)

        assert pos.slug not in engine._positions

    @pytest.mark.asyncio
    async def test_stays_scanning_if_price_high(self, engine, mock_clob):
        """If bid pair cost rises above threshold, stay in SCANNING."""
        pos = _make_pos()
        engine._positions[pos.slug] = pos
        # bid pair cost: 0.50 + 0.50 = 1.00 (too expensive)
        mock_clob.get_order_book = AsyncMock(side_effect=[
            MOCK_UP_BOOK_EXPENSIVE, MOCK_DOWN_BOOK_EXPENSIVE
        ])

        await engine._evaluate_and_enter(pos)

        assert pos.state == AccumulatorState.SCANNING

    @pytest.mark.asyncio
    async def test_evaluate_uses_fresh_book(self, engine, mock_clob, mock_balance):
        """_evaluate_and_enter() fetches fresh order book, stores order_id."""
        pos = _make_pos()
        engine._positions[pos.slug] = pos
        mock_clob.get_order_book = AsyncMock(side_effect=[MOCK_UP_BOOK, MOCK_DOWN_BOOK])

        await engine._evaluate_and_enter(pos)

        # Verify get_order_book was called (not get_midpoint)
        assert mock_clob.get_order_book.call_count == 2
        mock_clob.get_midpoint.assert_not_called()
        # Verify both order IDs stored (dual simultaneous placement)
        assert pos.up_order_id is not None or pos.down_order_id is not None
        assert engine._orders_placed == 2


# ============================================================================
# Accumulate Both Sides Tests
# ============================================================================


class TestAccumulateBothSides:
    @pytest.mark.asyncio
    async def test_buys_missing_down_side(self, engine, mock_clob):
        """When only UP held, place DOWN order then fill on next cycle (dry_run)."""
        pos = _make_pos(
            state=AccumulatorState.ACCUMULATING, mins_left=3,
            up_qty=100.0, up_avg_price=0.46,
        )
        engine._positions[pos.slug] = pos
        # DOWN best_bid=0.47 → projected pair = 0.46 + 0.47 = 0.93
        mock_clob.get_order_book = AsyncMock(return_value=MOCK_DOWN_BOOK)

        # Call 1: places DOWN order (non-blocking)
        await engine._accumulate_both_sides(pos)
        assert pos.down_order_id is not None
        assert pos.state == AccumulatorState.ACCUMULATING

        # Call 2: dry_run instant fill → HEDGED
        await engine._accumulate_both_sides(pos)
        assert pos.down_qty > 0
        assert pos.state == AccumulatorState.HEDGED

    @pytest.mark.asyncio
    async def test_buys_missing_up_side(self, engine, mock_clob):
        """When only DOWN held, place UP order then fill on next cycle (dry_run)."""
        pos = _make_pos(
            state=AccumulatorState.ACCUMULATING, mins_left=3,
            down_qty=100.0, down_avg_price=0.46,
        )
        engine._positions[pos.slug] = pos
        # UP best_bid=0.48 → projected pair = 0.48 + 0.46 = 0.94
        mock_clob.get_order_book = AsyncMock(return_value=MOCK_UP_BOOK)

        # Call 1: places UP order (non-blocking)
        await engine._accumulate_both_sides(pos)
        assert pos.up_order_id is not None
        assert pos.state == AccumulatorState.ACCUMULATING

        # Call 2: dry_run instant fill → HEDGED
        await engine._accumulate_both_sides(pos)
        assert pos.up_qty > 0
        assert pos.state == AccumulatorState.HEDGED

    @pytest.mark.asyncio
    async def test_transitions_to_settling_on_time(self, engine):
        """When < 60s left, go to SETTLING."""
        pos = _make_pos(
            state=AccumulatorState.ACCUMULATING,
            up_qty=100.0, up_avg_price=0.46,
        )
        pos.market_end_time = datetime.now(tz=timezone.utc) + timedelta(seconds=30)
        engine._positions[pos.slug] = pos

        await engine._accumulate_both_sides(pos)

        assert pos.state == AccumulatorState.SETTLING

    @pytest.mark.asyncio
    async def test_skips_if_projected_pair_too_expensive(self, engine, mock_clob):
        """If buying opposite side would make pair too expensive, don't buy."""
        pos = _make_pos(
            state=AccumulatorState.ACCUMULATING, mins_left=3,
            up_qty=100.0, up_avg_price=0.50,
        )
        engine._positions[pos.slug] = pos
        # DOWN best_bid=0.50 → projected pair = 0.50 + 0.50 = 1.00 >= 0.975
        mock_clob.get_order_book = AsyncMock(return_value=MOCK_DOWN_BOOK_EXPENSIVE)

        await engine._accumulate_both_sides(pos)

        assert pos.down_qty == 0  # Did NOT buy
        assert pos.state == AccumulatorState.ACCUMULATING  # Still accumulating


# ============================================================================
# Fill Tracking Tests
# ============================================================================


class TestApplyFill:
    def test_first_fill_up(self, engine):
        """First UP fill sets qty and avg_price."""
        pos = _make_pos(state=AccumulatorState.ACCUMULATING)
        engine._positions[pos.slug] = pos
        engine._apply_fill(pos, "UP", 0.46, 100.0, "order1")

        assert pos.up_qty == 100.0
        assert pos.up_avg_price == 0.46

    def test_weighted_average_on_second_fill(self, engine):
        """Second fill updates weighted average."""
        pos = _make_pos(
            state=AccumulatorState.ACCUMULATING,
            up_qty=100.0, up_avg_price=0.46,
        )
        engine._positions[pos.slug] = pos
        engine._apply_fill(pos, "UP", 0.48, 100.0, "order2")

        assert pos.up_qty == 200.0
        # Weighted avg = (100*0.46 + 100*0.48) / 200 = 0.47
        assert abs(pos.up_avg_price - 0.47) < 0.001

    def test_pair_cost_calculated_when_both_sides(self, engine):
        """Pair cost updated when both UP and DOWN have fills."""
        pos = _make_pos(
            state=AccumulatorState.ACCUMULATING,
            up_qty=100.0, up_avg_price=0.46,
        )
        engine._positions[pos.slug] = pos
        engine._apply_fill(pos, "DOWN", 0.48, 100.0, "order3")

        assert pos.pair_cost == pytest.approx(0.94, abs=0.001)

    def test_stores_position_in_store(self, engine, mock_store):
        """Fill adds Position to PositionStore with is_accumulator metadata."""
        pos = _make_pos(
            state=AccumulatorState.ACCUMULATING,
            up_token_id="up_token_123", down_token_id="down_token_456",
        )
        engine._positions[pos.slug] = pos
        engine._apply_fill(pos, "UP", 0.46, 100.0, "order1")

        mock_store.add.assert_called_once()
        stored = mock_store.add.call_args[0][0]
        assert stored.token_id == "up_token_123"
        assert stored.metadata["is_accumulator"] is True
        assert stored.metadata["accum_side"] == "UP"


# ============================================================================
# Settlement Tests
# ============================================================================


class TestSettlement:
    @pytest.mark.asyncio
    async def test_hedged_settlement_profit(self, engine, mock_clob):
        """Hedged position with pair_cost < 1.0 yields profit."""
        pos = _make_pos(
            state=AccumulatorState.SETTLING,
            up_qty=100.0, up_avg_price=0.46,
            down_qty=100.0, down_avg_price=0.48,
            pair_cost=0.94, is_fully_hedged=True,
        )
        pos.market_end_time = datetime.now(tz=timezone.utc) - timedelta(seconds=10)
        engine._positions[pos.slug] = pos
        # Simulate resolution: UP wins
        mock_clob.get_midpoint = AsyncMock(side_effect=[0.99, 0.01])

        await engine._handle_settlement(pos)

        assert pos.slug not in engine._positions

    @pytest.mark.asyncio
    async def test_orphaned_settlement_win(self, engine, mock_clob):
        """Orphaned UP leg that wins resolution."""
        pos = _make_pos(
            state=AccumulatorState.SETTLING,
            up_qty=100.0, up_avg_price=0.46,
        )
        pos.market_end_time = datetime.now(tz=timezone.utc) - timedelta(seconds=10)
        engine._positions[pos.slug] = pos
        # UP wins
        mock_clob.get_midpoint = AsyncMock(side_effect=[0.99, 0.01])

        await engine._handle_settlement(pos)

        assert pos.slug not in engine._positions

    @pytest.mark.asyncio
    async def test_orphaned_settlement_loss(self, engine, mock_clob):
        """Orphaned UP leg that loses resolution."""
        pos = _make_pos(
            state=AccumulatorState.SETTLING,
            up_qty=100.0, up_avg_price=0.46,
        )
        pos.market_end_time = datetime.now(tz=timezone.utc) - timedelta(seconds=10)
        engine._positions[pos.slug] = pos
        # DOWN wins (UP loses)
        mock_clob.get_midpoint = AsyncMock(side_effect=[0.01, 0.99])

        await engine._handle_settlement(pos)

        assert pos.slug not in engine._positions


# ============================================================================
# Emergency Cleanup Tests
# ============================================================================


class TestEmergencyCleanup:
    @pytest.mark.asyncio
    async def test_cancels_orders_and_removes(self, engine, mock_clob):
        """Emergency cleanup cancels resting orders and removes position."""
        pos = _make_pos(
            state=AccumulatorState.ACCUMULATING,
            up_order_id="order_up", down_order_id="order_down",
        )
        engine._positions[pos.slug] = pos

        await engine._emergency_cleanup(pos)

        assert pos.slug not in engine._positions
        assert mock_clob.cancel_order.call_count == 2


# ============================================================================
# Dry Run Tests
# ============================================================================


class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_returns_fake_order_id(self, engine):
        """In dry run, _place_maker_order returns fake order ID."""
        result = await engine._place_maker_order("token123", 0.48, 100.0)
        assert result is not None
        assert result.startswith("dry_run_")

    @pytest.mark.asyncio
    async def test_dry_run_fill_is_instant(self, engine):
        """In dry run, _check_and_handle_order fills immediately."""
        pos = _make_pos(
            state=AccumulatorState.ACCUMULATING,
            up_order_id="dry_run_123",
            up_order_time=datetime.now(tz=timezone.utc),
            pending_order_price=0.46,
            target_shares=100.0,
        )
        engine._positions[pos.slug] = pos
        result = await engine._check_and_handle_order(
            pos, "UP", "dry_run_123", datetime.now(tz=timezone.utc)
        )
        assert result == "filled"
        assert pos.up_qty == 100.0
        assert engine._orders_filled == 1


# ============================================================================
# Non-Blocking Order Management Tests
# ============================================================================


class TestNonBlockingOrders:
    def test_get_resting_order_up(self, engine):
        """Returns UP order when up_order_id is set."""
        pos = _make_pos(
            up_order_id="order_123",
            up_order_time=datetime.now(tz=timezone.utc),
        )
        side, oid, otime = engine._get_resting_order(pos)
        assert side == "UP"
        assert oid == "order_123"

    def test_get_resting_order_none(self, engine):
        """Returns (None, None, None) when no resting orders."""
        pos = _make_pos()
        side, oid, otime = engine._get_resting_order(pos)
        assert side is None
        assert oid is None

    def test_clear_order_up(self, engine):
        """_clear_order clears UP order fields."""
        pos = _make_pos(
            up_order_id="order_123",
            up_order_time=datetime.now(tz=timezone.utc),
        )
        engine._clear_order(pos, "UP")
        assert pos.up_order_id is None
        assert pos.up_order_time is None

    @pytest.mark.asyncio
    async def test_check_filled_order(self, engine, mock_clob):
        """Filled order applies fill and returns 'filled'."""
        engine._dry_run = False
        pos = _make_pos(
            up_order_id="order_123",
            up_order_time=datetime.now(tz=timezone.utc),
            pending_order_price=0.46,
            target_shares=100.0,
        )
        engine._positions[pos.slug] = pos
        mock_clob.get_order_status = AsyncMock(return_value="FILLED")
        mock_clob.get_order_details = AsyncMock(return_value={
            "status": "FILLED", "size_matched": 100.0, "price": 0.46
        })

        result = await engine._check_and_handle_order(
            pos, "UP", "order_123", datetime.now(tz=timezone.utc)
        )

        assert result == "filled"
        assert pos.up_qty == 100.0
        assert pos.up_order_id is None

    @pytest.mark.asyncio
    async def test_check_cancelled_increments_reprice(self, engine, mock_clob):
        """Cancelled order increments reprice_count."""
        engine._dry_run = False
        pos = _make_pos(
            up_order_id="order_123",
            pending_order_price=0.46,
            target_shares=100.0,
        )
        engine._positions[pos.slug] = pos
        mock_clob.get_order_status = AsyncMock(return_value="CANCELLED")

        result = await engine._check_and_handle_order(
            pos, "UP", "order_123", datetime.now(tz=timezone.utc)
        )

        assert result == "repriced"
        assert pos.reprice_count == 1
        assert pos.up_order_id is None

    @pytest.mark.asyncio
    async def test_reprice_limit_abandons(self, engine, mock_clob):
        """When reprice_count >= limit, returns 'abandoned'."""
        engine._dry_run = False
        pos = _make_pos(
            up_order_id="order_123",
            pending_order_price=0.46,
            target_shares=100.0,
            up_reprice_count=4,  # One below per-side limit of 5; reprice_count is legacy
        )
        engine._positions[pos.slug] = pos
        mock_clob.get_order_status = AsyncMock(return_value="CANCELLED")

        result = await engine._check_and_handle_order(
            pos, "UP", "order_123", datetime.now(tz=timezone.utc)
        )

        assert result == "abandoned"
        assert pos.reprice_count == 5


class TestRepriceOrder:
    @pytest.mark.asyncio
    async def test_reprice_ascending(self, engine, mock_clob):
        """Reprice goes UP (ascending) toward ask, not down."""
        engine._dry_run = False
        pos = _make_pos(
            up_token_id="up_token",
            up_order_id="old_order",
            target_shares=100.0,
        )
        engine._positions[pos.slug] = pos
        # Fresh book: bid=0.48, ask=0.52
        mock_clob.get_order_book = AsyncMock(return_value=MOCK_UP_BOOK)
        mock_clob.place_order = AsyncMock(return_value=MagicMock(success=True, order_id="new_order"))

        result = await engine._reprice_order(pos, "UP", "old_order")

        assert result == "new_order"
        # reprice_count=1, price = 0.48 + (1 * 0.005) = 0.485
        assert pos.pending_order_price == pytest.approx(0.485, abs=0.001)
        assert pos.up_order_id == "new_order"

    @pytest.mark.asyncio
    async def test_reprice_caps_at_ask(self, engine, mock_clob):
        """Reprice never crosses the spread (capped at ask - tick)."""
        engine._dry_run = False
        pos = _make_pos(
            up_token_id="up_token",
            up_order_id="old_order",
            target_shares=100.0,
            up_reprice_count=4,  # After increment: rc=5 >= limit(5) → abandon
        )
        engine._positions[pos.slug] = pos
        # Tight spread: bid=0.50, ask=0.51
        tight_book = {
            "bids": [{"price": 0.50, "size": 200}],
            "asks": [{"price": 0.51, "size": 200}],
        }
        mock_clob.get_order_book = AsyncMock(return_value=tight_book)

        result = await engine._reprice_order(pos, "UP", "old_order")

        # Reprice limit reached (rc=5) → abandoned rather than crossing spread
        assert result is None

    @pytest.mark.asyncio
    async def test_reprice_fetches_fresh_book(self, engine, mock_clob):
        """Reprice always fetches fresh order book."""
        engine._dry_run = False
        pos = _make_pos(
            up_token_id="up_token",
            up_order_id="old_order",
            target_shares=100.0,
        )
        engine._positions[pos.slug] = pos
        mock_clob.get_order_book = AsyncMock(return_value=MOCK_UP_BOOK)
        mock_clob.place_order = AsyncMock(return_value=MagicMock(success=True, order_id="new_order"))

        await engine._reprice_order(pos, "UP", "old_order")

        # Called once for the repriced side (up_token)
        # Pair-cost guard skipped because no fills yet (other_price=0)
        mock_clob.get_order_book.assert_called_once_with("up_token")
        mock_clob.cancel_order.assert_called_once_with("old_order")


class TestAdaptiveTimeout:
    @pytest.mark.asyncio
    async def test_timeout_shortens_near_expiry(self, engine, mock_clob):
        """With 30s remaining, adaptive timeout = max(5, min(30, 30/6)) = 5s."""
        engine._dry_run = False
        pos = _make_pos(
            up_order_id="order_123",
            up_order_time=datetime.now(tz=timezone.utc) - timedelta(seconds=6),
            pending_order_price=0.46,
            target_shares=100.0,
        )
        pos.market_end_time = datetime.now(tz=timezone.utc) + timedelta(seconds=30)
        engine._positions[pos.slug] = pos
        # Order is 6s old, adaptive timeout = 5s → should trigger reprice
        mock_clob.get_order_status = AsyncMock(return_value="LIVE")
        # Reprice will fetch book
        mock_clob.get_order_book = AsyncMock(return_value=MOCK_UP_BOOK)
        mock_clob.place_order = AsyncMock(return_value=MagicMock(success=True, order_id="new_order"))

        result = await engine._check_and_handle_order(
            pos, "UP", "order_123", pos.up_order_time
        )

        assert result == "repriced"
        assert engine._orders_timed_out == 1


class TestStatsProperty:
    def test_stats_structure(self, engine):
        """Stats property returns correct dict structure."""
        stats = engine.stats
        assert "state" in stats
        assert "scan_count" in stats
        assert "best_bid_pair" in stats
        assert "orders_placed" in stats
        assert "orders_filled" in stats
        assert "orders_timed_out" in stats
        assert "positions" in stats
        assert "active_positions" in stats
        assert "max_concurrent" in stats
        assert stats["positions"] == []
        assert stats["active_positions"] == 0

    def test_stats_with_position(self, engine):
        """Stats includes position details when active."""
        pos = _make_pos(
            state=AccumulatorState.ACCUMULATING,
            up_qty=100.0, up_avg_price=0.46,
            pair_cost=0.94,
        )
        engine._positions[pos.slug] = pos
        stats = engine.stats
        assert stats["active_positions"] == 1
        assert len(stats["positions"]) == 1
        assert stats["positions"][0]["slug"] == "btc-updown-5m-123"
        assert stats["positions"][0]["up_qty"] == 100.0

    def test_stats_multiple_positions(self, engine):
        """Stats includes all active positions."""
        pos1 = _make_pos(slug="btc-updown-5m-100", state=AccumulatorState.SCANNING)
        pos2 = _make_pos(slug="btc-updown-5m-200", state=AccumulatorState.HEDGED)
        engine._positions[pos1.slug] = pos1
        engine._positions[pos2.slug] = pos2
        stats = engine.stats
        assert stats["active_positions"] == 2
        assert stats["state"] == "active"
        assert len(stats["positions"]) == 2


# ============================================================================
# Capital Isolation Tests (BalanceManager)
# ============================================================================


class TestCapitalIsolation:
    @pytest.mark.asyncio
    async def test_accumulator_gets_reserved_capital(self, mock_balance):
        """get_available_for_accumulator returns reserved share."""
        available = await mock_balance.get_available_for_accumulator()
        assert available == 160.0  # 40% of $400

    @pytest.mark.asyncio
    async def test_momentum_excludes_accumulator_reserve(self, mock_balance):
        """Momentum check doesn't touch accumulator capital (mocked)."""
        mock_balance.get_available_for_momentum = AsyncMock(return_value=240.0)
        available = await mock_balance.get_available_for_momentum()
        assert available == 240.0  # 60% of $400


# ============================================================================
# Market Discovery Tests
# ============================================================================


class TestDiscoverMarkets:
    @pytest.mark.asyncio
    async def test_cache_returns_cached_results(self, engine):
        """Second call within 60s returns cached results."""
        engine._market_cache = [{"slug": "cached"}]
        engine._market_cache_ts = time.time()
        engine._session = MagicMock()
        engine._session.closed = False

        result = await engine._discover_markets()

        assert result == [{"slug": "cached"}]

    @pytest.mark.asyncio
    async def test_returns_empty_if_no_session(self, engine):
        """Returns empty if session is None."""
        engine._session = None

        result = await engine._discover_markets()

        assert result == []


# ============================================================================
# Wait for Settlement Tests
# ============================================================================


class TestWaitForSettlement:
    @pytest.mark.asyncio
    async def test_detects_up_resolution(self, engine, mock_clob):
        """When UP midpoint >= 0.95, returns resolved_up."""
        pos = _make_pos(state=AccumulatorState.SETTLING)
        engine._positions[pos.slug] = pos
        mock_clob.get_midpoint = AsyncMock(side_effect=[0.99, 0.01])

        result = await engine._wait_for_settlement(pos)

        assert result == "resolved_up"

    @pytest.mark.asyncio
    async def test_detects_down_resolution(self, engine, mock_clob):
        """When DOWN midpoint >= 0.95, returns resolved_down."""
        pos = _make_pos(state=AccumulatorState.SETTLING)
        engine._positions[pos.slug] = pos
        mock_clob.get_midpoint = AsyncMock(side_effect=[0.01, 0.99])

        result = await engine._wait_for_settlement(pos)

        assert result == "resolved_down"


# ============================================================================
# Concurrent Position Tests
# ============================================================================


class TestConcurrentPositions:
    def test_max_concurrent_config(self, engine, config):
        """Config exposes accum_max_concurrent."""
        assert config.accum_max_concurrent == 3

    @pytest.mark.asyncio
    async def test_scan_respects_max_concurrent(self, engine, mock_clob):
        """When at max_concurrent, scan should not be called."""
        # Fill up to max_concurrent
        for i in range(3):
            pos = _make_pos(slug=f"btc-updown-5m-{i}")
            engine._positions[pos.slug] = pos

        # Verify we're at capacity
        assert len(engine._positions) == engine._config.accum_max_concurrent

    @pytest.mark.asyncio
    async def test_positions_independent_states(self, engine):
        """Each position tracks its own state independently."""
        pos1 = _make_pos(slug="btc-updown-5m-100", state=AccumulatorState.SCANNING)
        pos2 = _make_pos(slug="btc-updown-5m-200", state=AccumulatorState.HEDGED)
        pos3 = _make_pos(slug="btc-updown-5m-300", state=AccumulatorState.ACCUMULATING)
        engine._positions[pos1.slug] = pos1
        engine._positions[pos2.slug] = pos2
        engine._positions[pos3.slug] = pos3

        # Transition one position without affecting others
        engine._transition(AccumulatorState.ACCUMULATING, pos1)

        assert pos1.state == AccumulatorState.ACCUMULATING
        assert pos2.state == AccumulatorState.HEDGED
        assert pos3.state == AccumulatorState.ACCUMULATING

    @pytest.mark.asyncio
    async def test_emergency_cleanup_only_affects_target(self, engine, mock_clob):
        """Emergency cleanup removes only the target position."""
        pos1 = _make_pos(slug="btc-updown-5m-100", up_order_id="order1")
        pos2 = _make_pos(slug="btc-updown-5m-200", up_order_id="order2")
        engine._positions[pos1.slug] = pos1
        engine._positions[pos2.slug] = pos2

        await engine._emergency_cleanup(pos1)

        assert pos1.slug not in engine._positions
        assert pos2.slug in engine._positions


# ============================================================================
# Bug #46 Regression — ensure_sell_allowance destroys fresh-fill allowance
# Bug #47 Regression — FOK SELL on expired market
# ============================================================================


class TestUnwindRegressions:
    """Regression tests for Bug #46 and Bug #47 (unwind path).

    Bug #46: ensure_sell_allowance() calls update_balance_allowance(CONDITIONAL),
    which reads on-chain balance (0 for fresh fills) and OVERWRITES the CLOB's
    internal fill-ledger allowance, causing every FOK SELL to fail.

    Bug #47: 5m slug epoch = market close time. Fills arriving 1-5s after close
    cannot be sold on secondary market. CLOB returns same "not enough balance"
    error as other rejections, causing retry loop + circuit breaker false-trip.
    """

    @pytest.mark.asyncio
    async def test_bug46_ensure_sell_allowance_not_called(self, engine, mock_clob):
        """Bug #46: _unwind_orphan must NOT call ensure_sell_allowance."""
        import inspect
        from polyphemus import accumulator as acc_module
        source = inspect.getsource(acc_module.AccumulatorEngine._unwind_orphan)
        assert "ensure_sell_allowance" not in source, (
            "ensure_sell_allowance() found in _unwind_orphan — Bug #46 regression"
        )
        assert "update_balance_allowance" not in source, (
            "update_balance_allowance() found in _unwind_orphan — Bug #46 regression"
        )

    @pytest.mark.asyncio
    async def test_bug47_unwind_skipped_when_market_expired(self, engine, mock_clob):
        """Bug #47: when market already expired, _unwind_orphan skips FOK SELL entirely."""
        mock_clob.get_order_book = AsyncMock()
        mock_clob.place_fok_order = AsyncMock()

        pos = _make_pos(
            slug="btc-updown-5m-expired",
            state=AccumulatorState.ACCUMULATING,
            mins_left=-1,  # expired 1 minute ago
            up_qty=100.0,
            up_avg_price=0.49,
        )
        engine._positions[pos.slug] = pos
        consecutive_before = engine._consecutive_unwinds

        await engine._unwind_orphan(pos, "UP")

        mock_clob.get_order_book.assert_not_called()
        mock_clob.place_fok_order.assert_not_called()
        assert pos.slug not in engine._positions
        assert engine._consecutive_unwinds == consecutive_before, (
            "Circuit breaker incremented for expired market skip — Bug #47 regression"
        )

    @pytest.mark.asyncio
    async def test_bug47_fok_fallback_skipped_when_market_expired(self, engine, mock_clob):
        """Bug #47: _try_fok_fallback skips FOK BUY when market already expired."""
        mock_clob.get_order_book = AsyncMock()
        mock_clob.place_fok_order = AsyncMock()

        pos = _make_pos(
            slug="btc-updown-5m-expired-fallback",
            state=AccumulatorState.ACCUMULATING,
            mins_left=-1,
            up_qty=100.0,
            up_avg_price=0.49,
        )
        engine._positions[pos.slug] = pos

        result = await engine._try_fok_fallback(pos)

        assert result is False
        mock_clob.place_fok_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_infra_unwind_does_not_trip_circuit_breaker(self, engine, mock_clob):
        """When FOK SELL fails (infra), do not increment consecutive_unwinds."""
        mock_clob.get_order_book = AsyncMock(return_value={
            "bids": [{"price": 0.45, "size": 200}],
            "asks": [],
        })
        mock_clob.get_share_balance = AsyncMock(return_value=100.0)
        # FOK SELL always fails (infra error)
        from polyphemus.types import ExecutionResult
        mock_clob.place_fok_order = AsyncMock(
            return_value=ExecutionResult(success=False, order_id="", error="CLOB timeout")
        )
        engine._dry_run = False
        engine._save_circuit_breaker_state = MagicMock()  # Prevent disk writes

        pos = _make_pos(
            slug="btc-updown-5m-infra-fail",
            state=AccumulatorState.ACCUMULATING,
            mins_left=5,
            up_qty=100.0,
            up_avg_price=0.49,
        )
        engine._positions[pos.slug] = pos
        consecutive_before = engine._consecutive_unwinds

        await engine._unwind_orphan(pos, "UP")

        assert engine._consecutive_unwinds == consecutive_before, (
            "Circuit breaker incremented for infra unwind — should not count infra failures as strategy losses"
        )

    @pytest.mark.asyncio
    async def test_unwind_proceeds_when_market_live(self, engine, mock_clob):
        """Sanity: when market has time remaining, FOK SELL path is entered."""
        mock_clob.get_order_book = AsyncMock(return_value={
            "bids": [{"price": 0.45, "size": 200}],
            "asks": [],
        })
        mock_clob.get_share_balance = AsyncMock(return_value=100.0)
        from polyphemus.types import ExecutionResult
        mock_clob.place_fok_order = AsyncMock(
            return_value=ExecutionResult(success=True, order_id="SELL001")
        )
        engine._dry_run = False
        engine._save_circuit_breaker_state = MagicMock()  # Prevent disk writes

        pos = _make_pos(
            slug="btc-updown-5m-live",
            state=AccumulatorState.ACCUMULATING,
            mins_left=5,
            up_qty=100.0,
            up_avg_price=0.49,
        )
        engine._positions[pos.slug] = pos

        await engine._unwind_orphan(pos, "UP")

        mock_clob.place_fok_order.assert_called_once()
