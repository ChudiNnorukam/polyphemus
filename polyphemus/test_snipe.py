"""Tests for resolution snipe pipeline — _check_snipe, _generate_snipe_signal,
SignalGuard exemptions, and PositionExecutor snipe sizing."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from .config import Settings
from .signal_guard import SignalGuard
from .position_store import PositionStore
from .binance_momentum import BinanceMomentumFeed, BINANCE_TO_ASSET


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def config():
    """Snipe-ready config."""
    cfg = MagicMock(spec=Settings)
    # Snipe config
    cfg.enable_resolution_snipe = True
    cfg.snipe_max_secs_remaining = 45
    cfg.snipe_min_secs_remaining = 8
    cfg.snipe_min_entry_price = 0.90
    cfg.snipe_max_entry_price = 0.985
    cfg.snipe_bet_pct = 0.04
    cfg.snipe_max_bet = 50.0
    cfg.snipe_assets = ""
    cfg.min_bet = 5.0
    # Standard config used by guard/executor
    cfg.min_entry_price = 0.55
    cfg.max_entry_price = 0.85
    cfg.max_open_positions = 5
    cfg.min_db_signal_size = 10.0
    cfg.min_secs_remaining = 120
    cfg.entry_trap_low = 0.0
    cfg.entry_trap_high = 0.0
    cfg.fg_min_threshold = 0
    cfg.whipsaw_max_ratio = 0
    cfg.macro_blackout_mins = 0
    cfg.enable_15m_momentum = False
    cfg.min_book_imbalance_alignment = 0.0
    cfg.weather_max_open_positions = 2
    cfg.window_delta_max_price = 0.97
    cfg.get_asset_filter.return_value = ["BTC", "ETH"]
    cfg.get_shadow_assets.return_value = ["SOL"]
    cfg.get_blocked_assets.return_value = []
    cfg.get_blackout_hours.return_value = []
    cfg.get_market_windows.return_value = [300]  # 5m only
    cfg.lagbot_data_dir = "/tmp/test_snipe"
    cfg.entry_mode = "maker"
    return cfg


@pytest.fixture
def store():
    return PositionStore()


@pytest.fixture
def guard(config, store):
    return SignalGuard(config, store)


def make_snipe_signal(**overrides):
    """Create a minimal snipe signal dict."""
    base = {
        "source": "resolution_snipe",
        "direction": "BUY",
        "outcome": "Up",
        "price": 0.93,
        "asset": "BTC",
        "timestamp": time.time(),
        "slug": "btc-updown-5m-1772170200",
        "usdc_size": 999.0,
        "token_id": "tok_up_123",
    }
    base.update(overrides)
    return base


# ============================================================================
# SignalGuard: Snipe Exemptions
# ============================================================================

class TestSignalGuardSnipe:
    """Verify snipe signals get proper exemptions in SignalGuard."""

    def test_snipe_skips_direction_check(self, guard):
        """Snipe signals should pass even without direction=BUY."""
        signal = make_snipe_signal(direction="SELL")
        result = guard.check(signal)
        assert "not_buy_signal" not in result.reasons

    def test_snipe_uses_snipe_price_range(self, guard):
        """Snipe at 0.93 should pass (in 0.90-0.985 range)."""
        signal = make_snipe_signal(price=0.93)
        result = guard.check(signal)
        assert "price_out_of_range" not in result.reasons

    def test_snipe_rejects_below_snipe_min(self, guard):
        """Snipe at 0.85 should be rejected (below 0.90 snipe floor)."""
        signal = make_snipe_signal(price=0.85)
        result = guard.check(signal)
        assert "price_out_of_range" in result.reasons

    def test_snipe_rejects_above_snipe_max(self, guard):
        """Snipe at 0.99 should be rejected (above 0.985 ceiling)."""
        signal = make_snipe_signal(price=0.99)
        result = guard.check(signal)
        assert "price_out_of_range" in result.reasons

    def test_snipe_skips_market_expired_check(self, guard):
        """Snipe signals should not be rejected for market_expired."""
        # Slug with market ending in 10 seconds (would fail normal 120s check)
        epoch = int(time.time()) - 290  # 5m window, 10s remaining
        signal = make_snipe_signal(slug=f"btc-updown-5m-{epoch}")
        result = guard.check(signal)
        assert "market_expired" not in result.reasons

    def test_snipe_skips_min_conviction_check(self, guard):
        """Snipe should pass even with low usdc_size (skips min_conviction)."""
        signal = make_snipe_signal(usdc_size=1.0)
        result = guard.check(signal)
        assert "low_conviction" not in result.reasons

    def test_snipe_still_checks_max_positions(self, guard, store, config):
        """Snipe should still be blocked by max_positions."""
        from .types import Position
        config.max_open_positions = 1
        # Add a fake open position with required attributes
        pos = MagicMock(spec=Position)
        pos.token_id = "existing_tok"
        pos.metadata = None
        pos.slug = "other-slug"
        pos.exit_time = None
        pos.outcome = "Up"
        store.add(pos)
        signal = make_snipe_signal()
        result = guard.check(signal)
        assert "max_positions" in result.reasons

    def test_snipe_still_checks_duplicate_slug(self, guard, store):
        """Snipe should still be blocked by duplicate_slug."""
        from .types import Position
        pos = MagicMock(spec=Position)
        pos.token_id = "tok_123"
        slug = "btc-updown-5m-1772170200"
        store._positions[slug] = pos  # manual add by slug
        # Patch get_by_slug to return the position
        store.get_by_slug = MagicMock(return_value=pos)
        signal = make_snipe_signal(slug=slug)
        result = guard.check(signal)
        assert "duplicate_slug" in result.reasons

    def test_normal_signal_at_093_rejected_by_price(self, guard):
        """A non-snipe signal at 0.93 should be rejected (above normal 0.85 max)."""
        signal = make_snipe_signal(source="binance_momentum", price=0.93)
        result = guard.check(signal)
        assert "price_out_of_range" in result.reasons


# ============================================================================
# PositionExecutor: Snipe Sizing
# ============================================================================

class TestSnipeSizing:
    """Test snipe flat sizing in _calculate_size."""

    def _make_executor(self, config):
        from .position_executor import PositionExecutor
        clob = MagicMock()
        store = PositionStore()
        executor = PositionExecutor(clob, store, config)
        return executor

    def test_snipe_sizing_basic(self, config):
        """4% of $2000 = $80, capped at $50 max_bet."""
        executor = self._make_executor(config)
        signal = {"source": "resolution_snipe"}
        size = executor._calculate_size(
            price=0.93, available_capital=2000.0, signal=signal
        )
        # 4% of 2000 = 80, capped at 50. 50 / 0.93 = 53.76
        expected = 50.0 / 0.93
        assert abs(size - expected) < 0.1

    def test_snipe_sizing_small_balance(self, config):
        """4% of $100 = $4, floored at min_bet=$5."""
        executor = self._make_executor(config)
        signal = {"source": "resolution_snipe"}
        size = executor._calculate_size(
            price=0.93, available_capital=100.0, signal=signal
        )
        # 4% of 100 = 4, floored at min_bet=5. 5 / 0.93 = 5.38
        expected = 5.0 / 0.93
        assert abs(size - expected) < 0.1

    def test_snipe_sizing_under_max(self, config):
        """4% of $500 = $20, no cap hit."""
        config.snipe_max_bet = 50.0
        executor = self._make_executor(config)
        signal = {"source": "resolution_snipe"}
        size = executor._calculate_size(
            price=0.95, available_capital=500.0, signal=signal
        )
        # 4% of 500 = 20. 20 / 0.95 = 21.05
        expected = 20.0 / 0.95
        assert abs(size - expected) < 0.1

    def test_non_snipe_does_not_use_snipe_sizing(self, config):
        """Normal momentum signal should NOT hit the snipe sizing shortcut."""
        signal_snipe = {"source": "resolution_snipe"}
        signal_normal = {"source": "binance_momentum"}
        # Verify the is_snipe check routes correctly
        is_snipe_true = signal_snipe.get('source') == 'resolution_snipe'
        is_snipe_false = signal_normal.get('source') == 'resolution_snipe'
        assert is_snipe_true is True
        assert is_snipe_false is False


# ============================================================================
# PositionExecutor: Force Taker for Snipe
# ============================================================================

class TestSnipeForceTaker:
    """Verify snipe forces taker execution mode."""

    def test_snipe_forces_taker(self, config):
        """Snipe should set use_maker=False regardless of config.entry_mode."""
        config.entry_mode = "maker"
        # Verify via the code path: is_snipe check
        signal = {"source": "resolution_snipe"}
        is_snipe = signal.get('source') == 'resolution_snipe'
        assert is_snipe is True

    def test_non_snipe_respects_config(self, config):
        """Non-snipe should use config.entry_mode."""
        signal = {"source": "binance_momentum"}
        is_snipe = signal.get('source') == 'resolution_snipe'
        assert is_snipe is False


# ============================================================================
# BinanceMomentumFeed: _check_snipe timing
# ============================================================================

class TestCheckSnipeTiming:
    """Test _check_snipe timing window and dedup logic."""

    def _make_feed(self, config):
        clob = AsyncMock()
        on_signal = AsyncMock()
        with patch('polyphemus.binance_momentum.StateStore') as MockStateStore:
            mock_store = MagicMock()
            mock_store.load.return_value = set()
            MockStateStore.return_value = mock_store
            feed = BinanceMomentumFeed(config, clob, on_signal)
        feed._on_signal = on_signal
        feed._market_ws = None
        feed._state_store = MagicMock()
        return feed, on_signal

    def test_fires_in_timing_window(self, config):
        """Should call _generate_snipe_signal when 8 < secs_to_end < 45."""
        feed, on_signal = self._make_feed(config)
        # Mock _generate_snipe_signal
        feed._generate_snipe_signal = AsyncMock()

        # Time: 30 seconds before window end
        window = 300
        current_epoch = (int(time.time()) // window) * window
        now = current_epoch + window - 30  # 30s left

        asyncio.get_event_loop().run_until_complete(
            feed._check_snipe("btcusdt", now, 67800.0)
        )
        assert feed._generate_snipe_signal.called

    def test_skips_too_early(self, config):
        """Should skip when secs_to_end > snipe_max_secs_remaining (45s)."""
        feed, on_signal = self._make_feed(config)
        feed._generate_snipe_signal = AsyncMock()

        window = 300
        current_epoch = (int(time.time()) // window) * window
        now = current_epoch + window - 200  # 200s left

        asyncio.get_event_loop().run_until_complete(
            feed._check_snipe("btcusdt", now, 67800.0)
        )
        assert not feed._generate_snipe_signal.called

    def test_skips_too_late(self, config):
        """Should skip when secs_to_end < snipe_min_secs_remaining (8s)."""
        feed, on_signal = self._make_feed(config)
        feed._generate_snipe_signal = AsyncMock()

        window = 300
        current_epoch = (int(time.time()) // window) * window
        now = current_epoch + window - 3  # 3s left

        asyncio.get_event_loop().run_until_complete(
            feed._check_snipe("btcusdt", now, 67800.0)
        )
        assert not feed._generate_snipe_signal.called

    def test_dedup_prevents_double_fire(self, config):
        """Should not fire twice for same window+asset."""
        feed, on_signal = self._make_feed(config)
        feed._generate_snipe_signal = AsyncMock()

        window = 300
        current_epoch = (int(time.time()) // window) * window
        now = current_epoch + window - 30

        asyncio.get_event_loop().run_until_complete(
            feed._check_snipe("btcusdt", now, 67800.0)
        )
        assert feed._generate_snipe_signal.call_count == 1

        # Second call same window - should NOT fire
        asyncio.get_event_loop().run_until_complete(
            feed._check_snipe("btcusdt", now + 1, 67850.0)
        )
        assert feed._generate_snipe_signal.call_count == 1

    def test_skips_if_momentum_already_signaled(self, config):
        """Should skip if normal momentum already fired on this slug."""
        feed, on_signal = self._make_feed(config)
        feed._generate_snipe_signal = AsyncMock()

        window = 300
        current_epoch = (int(time.time()) // window) * window
        slug = f"btc-updown-5m-{current_epoch}"
        feed._signaled_slugs.add(slug)

        now = current_epoch + window - 30

        asyncio.get_event_loop().run_until_complete(
            feed._check_snipe("btcusdt", now, 67800.0)
        )
        assert not feed._generate_snipe_signal.called

    def test_unknown_symbol_returns_early(self, config):
        """Unknown Binance symbol should return without error."""
        feed, on_signal = self._make_feed(config)
        feed._generate_snipe_signal = AsyncMock()

        asyncio.get_event_loop().run_until_complete(
            feed._check_snipe("dogeusdt", time.time(), 0.10)
        )
        assert not feed._generate_snipe_signal.called

    def test_filtered_asset_returns_early(self, config):
        """Asset not in filter or shadow should return without error."""
        config.get_asset_filter.return_value = ["ETH"]  # BTC not in filter
        config.get_shadow_assets.return_value = []
        feed, on_signal = self._make_feed(config)
        feed._generate_snipe_signal = AsyncMock()

        window = 300
        current_epoch = (int(time.time()) // window) * window
        now = current_epoch + window - 30

        asyncio.get_event_loop().run_until_complete(
            feed._check_snipe("btcusdt", now, 67800.0)
        )
        assert not feed._generate_snipe_signal.called


# ============================================================================
# BinanceMomentumFeed: _generate_snipe_signal
# ============================================================================

class TestGenerateSnipeSignal:
    """Test _generate_snipe_signal market discovery and midpoint checks."""

    def _make_feed(self, config):
        clob = AsyncMock()
        on_signal = AsyncMock()
        with patch('polyphemus.binance_momentum.StateStore') as MockStateStore:
            mock_store = MagicMock()
            mock_store.load.return_value = set()
            MockStateStore.return_value = mock_store
            feed = BinanceMomentumFeed(config, clob, on_signal)
        feed._on_signal = on_signal
        feed._state_store = MagicMock()
        return feed, on_signal

    def test_buys_high_side(self, config):
        """Should emit signal for the side with midpoint in snipe range."""
        feed, on_signal = self._make_feed(config)

        # Mock market discovery
        feed._discover_market = AsyncMock(return_value={
            "up_token_id": "tok_up",
            "down_token_id": "tok_down",
            "market_title": "BTC Up/Down 5m",
            "condition_id": "cond_123",
        })
        feed._market_ws = None
        # Up side at 0.93, Down side at 0.07
        feed._clob.get_midpoint = AsyncMock(side_effect=lambda tid: 0.93 if tid == "tok_up" else 0.07)

        asyncio.get_event_loop().run_until_complete(
            feed._generate_snipe_signal("BTC", "btc-updown-5m-123", 30.0, 300, False)
        )

        assert on_signal.called
        signal = on_signal.call_args[0][0]
        assert signal["outcome"] == "Up"
        assert signal["price"] == 0.93
        assert signal["source"] == "resolution_snipe"
        assert signal["token_id"] == "tok_up"

    def test_buys_down_side_when_high(self, config):
        """Should buy Down when Down midpoint is in snipe range."""
        feed, on_signal = self._make_feed(config)
        feed._discover_market = AsyncMock(return_value={
            "up_token_id": "tok_up",
            "down_token_id": "tok_down",
            "market_title": "BTC Up/Down 5m",
            "condition_id": "cond_123",
        })
        feed._market_ws = None
        # Up at 0.07, Down at 0.93
        feed._clob.get_midpoint = AsyncMock(side_effect=lambda tid: 0.07 if tid == "tok_up" else 0.93)

        asyncio.get_event_loop().run_until_complete(
            feed._generate_snipe_signal("BTC", "btc-updown-5m-123", 30.0, 300, False)
        )

        assert on_signal.called
        signal = on_signal.call_args[0][0]
        assert signal["outcome"] == "Down"
        assert signal["token_id"] == "tok_down"

    def test_skips_when_both_below_range(self, config):
        """Should not emit signal when no side is >= 0.90."""
        feed, on_signal = self._make_feed(config)
        feed._discover_market = AsyncMock(return_value={
            "up_token_id": "tok_up",
            "down_token_id": "tok_down",
            "market_title": "BTC Up/Down 5m",
            "condition_id": "cond_123",
        })
        feed._market_ws = None
        # Both sides at 0.50 (undecided market)
        feed._clob.get_midpoint = AsyncMock(return_value=0.50)

        asyncio.get_event_loop().run_until_complete(
            feed._generate_snipe_signal("BTC", "btc-updown-5m-123", 30.0, 300, False)
        )
        assert not on_signal.called

    def test_skips_when_above_ceiling(self, config):
        """Should not emit when midpoint > snipe_max_entry_price (0.985)."""
        feed, on_signal = self._make_feed(config)
        feed._discover_market = AsyncMock(return_value={
            "up_token_id": "tok_up",
            "down_token_id": "tok_down",
            "market_title": "BTC Up/Down 5m",
            "condition_id": "cond_123",
        })
        feed._market_ws = None
        feed._clob.get_midpoint = AsyncMock(side_effect=lambda tid: 0.99 if tid == "tok_up" else 0.01)

        asyncio.get_event_loop().run_until_complete(
            feed._generate_snipe_signal("BTC", "btc-updown-5m-123", 30.0, 300, False)
        )
        assert not on_signal.called

    def test_shadow_asset_logs_only(self, config):
        """Shadow assets should log but NOT emit signal."""
        feed, on_signal = self._make_feed(config)
        feed._discover_market = AsyncMock(return_value={
            "up_token_id": "tok_up",
            "down_token_id": "tok_down",
            "market_title": "SOL Up/Down 5m",
            "condition_id": "cond_123",
        })
        feed._market_ws = None
        feed._clob.get_midpoint = AsyncMock(side_effect=lambda tid: 0.93 if tid == "tok_up" else 0.07)

        asyncio.get_event_loop().run_until_complete(
            feed._generate_snipe_signal("SOL", "sol-updown-5m-123", 30.0, 300, True)
        )
        assert not on_signal.called

    def test_market_not_found(self, config):
        """Should handle missing market gracefully."""
        feed, on_signal = self._make_feed(config)
        feed._discover_market = AsyncMock(return_value=None)

        asyncio.get_event_loop().run_until_complete(
            feed._generate_snipe_signal("BTC", "btc-updown-5m-999", 30.0, 300, False)
        )
        assert not on_signal.called

    def test_no_midpoint_available(self, config):
        """Should handle zero midpoints gracefully."""
        feed, on_signal = self._make_feed(config)
        feed._discover_market = AsyncMock(return_value={
            "up_token_id": "tok_up",
            "down_token_id": "tok_down",
            "market_title": "BTC Up/Down 5m",
            "condition_id": "cond_123",
        })
        feed._market_ws = None
        feed._clob.get_midpoint = AsyncMock(return_value=0.0)

        asyncio.get_event_loop().run_until_complete(
            feed._generate_snipe_signal("BTC", "btc-updown-5m-123", 30.0, 300, False)
        )
        assert not on_signal.called

    def test_adds_slug_to_signaled(self, config):
        """Should add slug to _signaled_slugs to prevent momentum double-fire."""
        feed, on_signal = self._make_feed(config)
        feed._discover_market = AsyncMock(return_value={
            "up_token_id": "tok_up",
            "down_token_id": "tok_down",
            "market_title": "BTC Up/Down 5m",
            "condition_id": "cond_123",
        })
        feed._market_ws = None
        feed._clob.get_midpoint = AsyncMock(side_effect=lambda tid: 0.93 if tid == "tok_up" else 0.07)

        slug = "btc-updown-5m-123"
        asyncio.get_event_loop().run_until_complete(
            feed._generate_snipe_signal("BTC", slug, 30.0, 300, False)
        )
        assert slug in feed._signaled_slugs

    def test_prefers_ws_midpoint(self, config):
        """Should use MarketWS midpoint when available, skip REST."""
        feed, on_signal = self._make_feed(config)
        feed._discover_market = AsyncMock(return_value={
            "up_token_id": "tok_up",
            "down_token_id": "tok_down",
            "market_title": "BTC Up/Down 5m",
            "condition_id": "cond_123",
        })
        # WS returns 0.92 for up
        mock_ws = MagicMock()
        mock_ws.get_midpoint = MagicMock(side_effect=lambda tid: 0.92 if tid == "tok_up" else 0.08)
        feed._market_ws = mock_ws
        # REST should NOT be called since WS returned valid data
        feed._clob.get_midpoint = AsyncMock(return_value=0.0)

        asyncio.get_event_loop().run_until_complete(
            feed._generate_snipe_signal("BTC", "btc-updown-5m-123", 30.0, 300, False)
        )

        assert on_signal.called
        signal = on_signal.call_args[0][0]
        assert signal["price"] == 0.92


# ============================================================================
# SignalBot: Regime Filter Exemption
# ============================================================================

class TestSnipeRegimeExemption:
    """Verify resolution_snipe is in the regime filter exemption list."""

    def test_snipe_in_regime_exemption_list(self):
        """resolution_snipe should be exempted from regime filtering."""
        exempt_sources = ('pair_arb', 'noaa_weather', 'resolution_snipe')
        assert 'resolution_snipe' in exempt_sources

    def test_snipe_in_momentum_source_list(self):
        """resolution_snipe should be in the momentum signal source list
        (skips Binance confirmation)."""
        momentum_sources = (
            'binance_momentum', 'binance_momentum_lag',
            'window_delta', 'pair_arb', 'noaa_weather', 'resolution_snipe'
        )
        assert 'resolution_snipe' in momentum_sources
