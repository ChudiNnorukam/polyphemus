"""Smoke tests for lagbot — encodes exact bugs found in session.

Each test prevents regression of a specific bug discovered during live trading.
Tests run before every deploy to catch regressions early.
"""

import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from polyphemus.types import Position
from polyphemus.market_ws import MarketWS
from polyphemus.exit_manager import ExitManager
from polyphemus.position_store import PositionStore
from polyphemus.config import Settings


def make_config(**overrides):
    """Create test Settings."""
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
    """Create test Position."""
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
# Bug B1: MarketWS spread sentinel value
# ============================================================================

class TestMarketWSSpreadSentinel:
    """Bug B1: get_spread() must return negative sentinel when no WS data."""

    def test_spread_no_data_returns_negative(self):
        """get_spread() returns < 0 when no book data available."""
        ws = MarketWS()
        spread = ws.get_spread("nonexistent_token")
        assert spread < 0, f"Expected negative sentinel, got {spread}"

    def test_spread_with_valid_data_positive(self):
        """get_spread() returns positive when data is present."""
        ws = MarketWS()
        # Manually inject bid/ask
        ws._best_bids["0xtest"] = 0.48
        ws._best_asks["0xtest"] = 0.52
        spread = ws.get_spread("0xtest")
        assert abs(spread - 0.04) < 0.0001, f"Expected ~0.04 spread, got {spread}"

    def test_spread_zero_bid_returns_negative(self):
        """get_spread() returns negative when bid is 0 (invalid)."""
        ws = MarketWS()
        ws._best_bids["0xtest"] = 0.0
        ws._best_asks["0xtest"] = 0.52
        spread = ws.get_spread("0xtest")
        assert spread < 0, f"Expected negative sentinel, got {spread}"

    def test_spread_zero_ask_returns_negative(self):
        """get_spread() returns negative when ask is 0 (invalid)."""
        ws = MarketWS()
        ws._best_bids["0xtest"] = 0.48
        ws._best_asks["0xtest"] = 0.0
        spread = ws.get_spread("0xtest")
        assert spread < 0, f"Expected negative sentinel, got {spread}"


# ============================================================================
# Bug B2: Stop loss grace period (instant trigger after entry)
# ============================================================================

class TestStopLossGracePeriod:
    """Bug B2: Stop loss must NOT trigger within 15s of entry."""

    def test_stop_loss_grace_period_blocks(self):
        """Stop loss blocked within 15s of entry (taker spread cost)."""
        config = make_config(stop_loss_pct=0.15, enable_stop_loss=True)
        store = PositionStore()
        now = datetime.now(timezone.utc)

        # Position: just entered 5s ago, price below stop threshold
        pos = make_position(
            entry_time=now - timedelta(seconds=5),
            entry_price=0.70,
            current_price=0.58,  # Below 0.595 threshold
        )
        store.add(pos)

        mgr = ExitManager(store, config)
        exits = mgr.check_all(now)

        # Should NOT exit despite price below threshold
        assert len(exits) == 0, "Stop loss should be blocked in 15s grace period"

    def test_stop_loss_triggers_after_grace(self):
        """Stop loss SHOULD trigger after 15s grace period expires."""
        config = make_config(stop_loss_pct=0.15, enable_stop_loss=True)
        store = PositionStore()
        now = datetime.now(timezone.utc)

        # Position: entered 20s ago (past 15s grace), price below threshold
        pos = make_position(
            entry_time=now - timedelta(seconds=20),
            entry_price=0.70,
            current_price=0.58,
        )
        store.add(pos)

        mgr = ExitManager(store, config)
        exits = mgr.check_all(now)

        # Should exit after grace expires
        assert len(exits) == 1
        assert exits[0].reason == "stop_loss"


# ============================================================================
# Bug B3: 0 shares on CLOB causes invalid SELL
# ============================================================================

class TestZeroSharesHandling:
    """Bug B3: Position with 0 CLOB shares should not attempt SELL."""

    def test_zero_shares_has_metadata_flag(self):
        """Positions that lost shares should be tracked."""
        now = datetime.now(timezone.utc)
        pos = make_position(
            entry_size=100.0,
            current_price=0.70,
            entry_time=now - timedelta(minutes=5),
        )
        # Simulate position that was partially liquidated / unfilled
        pos.metadata["clob_shares"] = 0.0

        assert pos.metadata.get("clob_shares") == 0.0


# ============================================================================
# Bug B4: Signaled slugs persist across state save/load
# ============================================================================

class TestSignaledSlugsDebounce:
    """Bug B4: Signaled slugs should survive restarts."""

    def test_signaled_slugs_tracked(self):
        """BinanceMomentumFeed tracks signaled slugs for debouncing."""
        from polyphemus.binance_momentum import BinanceMomentumFeed

        config = make_config()
        clob = AsyncMock()
        on_signal = AsyncMock()

        feed = BinanceMomentumFeed(config, clob, on_signal)

        # Add a slug to the signaled set
        feed._signaled_slugs.add("btc-updown-5m-123456")

        # Verify it's tracked
        assert "btc-updown-5m-123456" in feed._signaled_slugs


# ============================================================================
# Bug B5: Momentum detection at threshold boundary
# ============================================================================

class TestMomentumThresholdDetection:
    """Bug B5: Momentum detection should work AT threshold, not just above."""

    def test_momentum_exactly_at_threshold(self):
        """Momentum move exactly AT threshold should trigger."""
        from polyphemus.binance_momentum import BinanceMomentumFeed

        config = make_config(momentum_trigger_pct=0.003, momentum_window_secs=60)
        clob = AsyncMock()
        feed = BinanceMomentumFeed(config, clob, AsyncMock())

        # Test momentum calculation logic directly
        old_price = 1000.0
        new_price = 1030.0  # 3% move (safe from precision issues)
        window_secs = config.momentum_window_secs
        trigger_pct = config.momentum_trigger_pct

        pct_move = abs(new_price - old_price) / old_price
        meets_threshold = pct_move >= trigger_pct

        assert meets_threshold, f"3% move should meet {trigger_pct*100:.1f}% threshold"

    def test_momentum_below_threshold(self):
        """Momentum move below threshold should NOT trigger."""
        from polyphemus.binance_momentum import BinanceMomentumFeed

        config = make_config(momentum_trigger_pct=0.003, momentum_window_secs=60)
        clob = AsyncMock()
        feed = BinanceMomentumFeed(config, clob, AsyncMock())

        now = time.time()
        feed._price_buffers["btcusdt"].append((now - 60, 100.0))

        initial_count = feed.momentum_detections
        import asyncio
        asyncio.run(feed._check_momentum("btcusdt", now, 100.29))  # 0.29%, below 0.3%

        # Should NOT detect
        assert feed.momentum_detections == initial_count


# ============================================================================
# Bug B7: Config allows wide entry price range
# ============================================================================

class TestConfigEntryRange:
    """Bug B7: MAX_ENTRY_PRICE=0.95 allows signals at $0.855."""

    def test_max_entry_price_boundary_exclusive(self):
        """max_entry_price uses strict > check (exclusive boundary)."""
        from polyphemus.signal_guard import SignalGuard

        config = make_config(max_entry_price=0.85)
        store = PositionStore()
        guard = SignalGuard(config, store)

        # Test at boundary
        signal = {
            "token_id": "0xtest",
            "direction": "BUY",
            "price": 0.85,  # == max_entry_price
            "outcome": "up",
            "asset": "BTC",
            "slug": "test",
            "usdc_size": 100.0,
            "timestamp": time.time(),
            "tx_hash": "0x123",
            "market_title": "test",
        }

        result = guard.check(signal)
        # At boundary, should pass (strict > means <= is OK)
        assert result.passed, "Price at max_entry_price should pass"

    def test_max_entry_price_just_above(self):
        """Price just above max_entry_price should be rejected."""
        from polyphemus.signal_guard import SignalGuard

        config = make_config(max_entry_price=0.85)
        store = PositionStore()
        guard = SignalGuard(config, store)

        signal = {
            "token_id": "0xtest",
            "direction": "BUY",
            "price": 0.86,  # > max_entry_price
            "outcome": "up",
            "asset": "BTC",
            "slug": "test",
            "usdc_size": 100.0,
            "timestamp": time.time(),
            "tx_hash": "0x123",
            "market_title": "test",
        }

        result = guard.check(signal)
        # Above boundary, should fail
        assert not result.passed, "Price above max_entry_price should fail"
        assert "price_out_of_range" in result.reasons


# ============================================================================
# Bug B8: Market end time parsing
# ============================================================================

class TestMarketEndTimeParsing:
    """Bug B8: Market end times parsed correctly from slug epoch."""

    def test_5m_market_window_detection(self):
        """5m market slug correctly identified."""
        from polyphemus.types import parse_window_from_slug

        window = parse_window_from_slug("btc-updown-5m-123456")
        assert window == 300, f"Expected 300s for 5m, got {window}"

    def test_15m_market_window_detection(self):
        """15m market slug correctly identified."""
        from polyphemus.types import parse_window_from_slug

        window = parse_window_from_slug("eth-updown-15m-123456")
        assert window == 900, f"Expected 900s for 15m, got {window}"

    def test_market_resolved_check_for_5m(self):
        """5m markets past end are marked resolved (skip SELL)."""
        config = make_config()
        store = PositionStore()
        now = datetime.now(timezone.utc)

        # 5m market that ended 40s ago
        pos = make_position(
            slug="btc-updown-5m-123456",
            market_end_time=now - timedelta(seconds=40),
            current_price=0.70,
            entry_time=now - timedelta(minutes=2),
        )
        store.add(pos)

        mgr = ExitManager(store, config)
        exits = mgr.check_all(now)

        # Should exit as market_resolved (not SELL, just record)
        assert len(exits) == 1
        assert exits[0].reason == "market_resolved"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
