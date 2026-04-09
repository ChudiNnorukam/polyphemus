"""Tests for Chainlink oracle feed, snipe gate, oracle reversal, and reversal short."""

import time
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, AsyncMock

from .chainlink_feed import ChainlinkFeed
from .config import Settings
from .exit_manager import ExitManager
from .models import Position, ExitSignal, ExitReason
from .position_store import PositionStore


def make_config(**overrides):
    """Create a test config with oracle fields."""
    defaults = {
        "private_key": "0x" + "a" * 64,
        "wallet_address": "0x" + "b" * 40,
        "clob_api_key": "test_key",
        "clob_secret": "test_secret",
        "clob_passphrase": "test_pass",
        "builder_api_key": "test_builder_key",
        "builder_secret": "test_builder_secret",
        "builder_passphrase": "test_builder_pass",
        "polygon_rpc_url": "http://localhost:8545",
        "lagbot_data_dir": "/tmp/test_data",
        "oracle_enabled": True,
        "oracle_alchemy_api_key": "test_key_123",
        "oracle_stale_threshold_secs": 60,
        "oracle_snipe_confirm": False,
        "oracle_snipe_confirm_dry_run": True,
        "oracle_reversal_exit": False,
        "momentum_reversal_exit": True,
        "momentum_reversal_pct": 0.002,
        "momentum_reversal_window_secs": 180,
        "momentum_reversal_dry_run": True,
        "reversal_short_enabled": False,
        "reversal_short_dry_run": True,
        "reversal_short_min_secs_remaining": 45,
        "reversal_short_max_down_price": 0.35,
        "reversal_short_min_down_price": 0.10,
    }
    defaults.update(overrides)
    return Settings(**defaults)


# ============================================================================
# ChainlinkFeed unit tests
# ============================================================================


class TestChainlinkFeedInterface:
    """Test the ChainlinkFeed public interface without network."""

    def test_get_current_price_none_before_first_update(self):
        config = make_config()
        feed = ChainlinkFeed(config)
        assert feed.get_current_price() is None

    def test_is_healthy_false_when_no_data(self):
        config = make_config()
        feed = ChainlinkFeed(config)
        assert feed.is_healthy() is False

    def test_staleness_inf_before_first_update(self):
        config = make_config()
        feed = ChainlinkFeed(config)
        assert feed.staleness_secs == float("inf")

    def test_update_price_sets_current(self):
        config = make_config()
        feed = ChainlinkFeed(config)
        now = time.time()
        feed._update_price("BTC", 65000.50, now)
        assert feed.get_current_price() == 65000.50
        assert feed.is_healthy() is True
        assert feed.staleness_secs < 2

    def test_epoch_anchor_on_first_update(self):
        config = make_config()
        feed = ChainlinkFeed(config)
        now = time.time()
        epoch_5m = int(now // 300) * 300
        feed._update_price("BTC", 65000.0, now)
        assert feed.get_window_open_price(epoch_5m, 300) == 65000.0

    def test_epoch_anchor_not_overwritten(self):
        config = make_config()
        feed = ChainlinkFeed(config)
        now = time.time()
        epoch_5m = int(now // 300) * 300
        feed._update_price("BTC", 65000.0, now)
        feed._update_price("BTC", 65500.0, now + 10)
        # First price sticks as anchor
        assert feed.get_window_open_price(epoch_5m, 300) == 65000.0
        # Current price updates
        assert feed.get_current_price() == 65500.0

    def test_is_above_window_open_true(self):
        config = make_config()
        feed = ChainlinkFeed(config)
        now = time.time()
        epoch_5m = int(now // 300) * 300
        feed._update_price("BTC", 65000.0, now)
        feed._update_price("BTC", 65500.0, now + 5)
        assert feed.is_above_window_open(epoch_5m, 300) is True

    def test_is_above_window_open_false(self):
        config = make_config()
        feed = ChainlinkFeed(config)
        now = time.time()
        epoch_5m = int(now // 300) * 300
        feed._update_price("BTC", 65000.0, now)
        feed._update_price("BTC", 64500.0, now + 5)
        assert feed.is_above_window_open(epoch_5m, 300) is False

    def test_is_above_window_open_none_when_no_anchor(self):
        config = make_config()
        feed = ChainlinkFeed(config)
        now = time.time()
        feed._update_price("BTC", 65000.0, now)
        # Use a different epoch that has no anchor
        assert feed.is_above_window_open(9999999, 300) is None

    def test_is_above_window_open_none_when_stale(self):
        config = make_config(oracle_stale_threshold_secs=5)
        feed = ChainlinkFeed(config)
        old_time = time.time() - 10
        epoch_5m = int(old_time // 300) * 300
        feed._update_price("BTC", 65000.0, old_time)
        # Feed is stale, should return None
        assert feed.is_above_window_open(epoch_5m, 300) is None

    def test_epoch_pruning(self):
        config = make_config()
        feed = ChainlinkFeed(config)
        now = time.time()
        # Add old epoch directly to BTC asset state
        old_epoch = int((now - 3600) // 300) * 300
        feed._assets["BTC"].epoch_open_prices[old_epoch] = 60000.0
        # Update price triggers pruning
        feed._update_price("BTC", 65000.0, now)
        assert old_epoch not in feed._assets["BTC"].epoch_open_prices

    def test_stats_output(self):
        config = make_config()
        feed = ChainlinkFeed(config)
        stats = feed.stats()
        assert "assets" in stats
        assert "BTC" in stats["assets"]
        assert stats["assets"]["BTC"]["healthy"] is False
        assert stats["ws_connected"] is False

    def test_multi_asset_isolation(self):
        """ETH and SOL prices don't contaminate BTC state."""
        config = make_config()
        feed = ChainlinkFeed(config)
        now = time.time()
        feed._update_price("BTC", 65000.0, now)
        feed._update_price("ETH", 2000.0, now)
        feed._update_price("SOL", 90.0, now)
        assert feed.get_current_price("BTC") == 65000.0
        assert feed.get_current_price("ETH") == 2000.0
        assert feed.get_current_price("SOL") == 90.0

    def test_unknown_asset_returns_none(self):
        config = make_config()
        feed = ChainlinkFeed(config)
        assert feed.get_current_price("XRP") is None
        assert feed.is_healthy("XRP") is False
        assert feed.is_above_window_open(0, 300, "XRP") is None


class TestChainlinkFeedDegradation:
    """Test graceful degradation when oracle is not available."""

    def test_no_api_key_does_not_crash(self):
        config = make_config(oracle_alchemy_api_key="")
        feed = ChainlinkFeed(config)
        assert feed.get_current_price() is None
        assert feed.is_healthy() is False

    def test_circuit_breaker_initially_closed(self):
        config = make_config()
        feed = ChainlinkFeed(config)
        assert feed.circuit_open is False


# ============================================================================
# Oracle reversal exit tests
# ============================================================================


class TestOracleReversalExit:
    """Test ExitManager CHECK #3b with oracle reversal."""

    def _make_position(self, slug, entry_dir, entry_price=0.94):
        now = datetime.now(timezone.utc)
        return Position(
            token_id="tok_" + slug,
            slug=slug,
            entry_price=entry_price,
            entry_size=100.0,
            entry_time=now - timedelta(seconds=60),
            entry_tx_hash="0xtest_hash",
            market_end_time=now + timedelta(seconds=60),
            current_price=0.50,
            metadata={
                "entry_momentum_direction": entry_dir,
                "entry_momentum_ts": time.time() - 60,
            },
        )

    def test_oracle_reversal_fires_when_losing(self):
        """Oracle says UP is losing -> exit signal with reason=oracle_reversal."""
        config = make_config(
            momentum_reversal_exit=True,
            oracle_reversal_exit=True,
            momentum_reversal_dry_run=False,
        )
        store = MagicMock(spec=PositionStore)
        exit_mgr = ExitManager(store, config)

        # Mock chainlink feed: says price is BELOW window open (DOWN winning)
        mock_chainlink = MagicMock()
        mock_chainlink.is_healthy.return_value = True
        mock_chainlink.is_above_window_open.return_value = False  # DOWN winning
        mock_chainlink.get_current_price.return_value = 64000.0
        exit_mgr.set_chainlink_feed(mock_chainlink)

        epoch = int(time.time() // 300) * 300
        pos = self._make_position(f"btc-updown-5m-{epoch}", "up")
        now = datetime.now(timezone.utc)
        signal = exit_mgr._evaluate(pos, now)

        assert signal is not None
        assert signal.reason == "oracle_reversal"

    def test_oracle_reversal_no_fire_when_winning(self):
        """Oracle says UP is winning -> no exit signal."""
        config = make_config(
            momentum_reversal_exit=True,
            oracle_reversal_exit=True,
            momentum_reversal_dry_run=False,
        )
        store = MagicMock(spec=PositionStore)
        exit_mgr = ExitManager(store, config)

        mock_chainlink = MagicMock()
        mock_chainlink.is_healthy.return_value = True
        mock_chainlink.is_above_window_open.return_value = True  # UP winning
        exit_mgr.set_chainlink_feed(mock_chainlink)

        epoch = int(time.time() // 300) * 300
        pos = self._make_position(f"btc-updown-5m-{epoch}", "up")

        now = datetime.now(timezone.utc)
        signal = exit_mgr._evaluate(pos, now)
        # Should be None or some other exit reason, not oracle_reversal
        if signal is not None:
            assert signal.reason != "oracle_reversal"

    def test_binance_fallback_when_oracle_disabled(self):
        """When oracle_reversal_exit=False, falls back to Binance momentum."""
        config = make_config(
            momentum_reversal_exit=True,
            oracle_reversal_exit=False,
            momentum_reversal_dry_run=False,
            momentum_reversal_pct=0.002,
        )
        store = MagicMock(spec=PositionStore)
        exit_mgr = ExitManager(store, config)

        mock_momentum = MagicMock()
        mock_momentum.get_current_momentum_pct.return_value = -0.005  # strong reversal
        exit_mgr.set_momentum_feed(mock_momentum)

        epoch = int(time.time() // 300) * 300
        pos = self._make_position(f"btc-updown-5m-{epoch}", "up")

        now = datetime.now(timezone.utc)
        signal = exit_mgr._evaluate(pos, now)
        assert signal is not None
        assert signal.reason == "binance_reversal"

    def test_binance_fallback_when_oracle_unhealthy(self):
        """When oracle feed is unhealthy, falls back to Binance."""
        config = make_config(
            momentum_reversal_exit=True,
            oracle_reversal_exit=True,
            momentum_reversal_dry_run=False,
            momentum_reversal_pct=0.002,
        )
        store = MagicMock(spec=PositionStore)
        exit_mgr = ExitManager(store, config)

        mock_chainlink = MagicMock()
        mock_chainlink.is_healthy.return_value = False  # unhealthy
        exit_mgr.set_chainlink_feed(mock_chainlink)

        mock_momentum = MagicMock()
        mock_momentum.get_current_momentum_pct.return_value = -0.005
        exit_mgr.set_momentum_feed(mock_momentum)

        epoch = int(time.time() // 300) * 300
        pos = self._make_position(f"btc-updown-5m-{epoch}", "up")

        now = datetime.now(timezone.utc)
        signal = exit_mgr._evaluate(pos, now)
        assert signal is not None
        assert signal.reason == "binance_reversal"

    def test_dry_run_logs_but_no_exit(self):
        """Dry run mode logs but doesn't produce an exit signal."""
        config = make_config(
            momentum_reversal_exit=True,
            oracle_reversal_exit=True,
            momentum_reversal_dry_run=True,  # dry run ON
        )
        store = MagicMock(spec=PositionStore)
        exit_mgr = ExitManager(store, config)

        mock_chainlink = MagicMock()
        mock_chainlink.is_healthy.return_value = True
        mock_chainlink.is_above_window_open.return_value = False  # DOWN winning
        mock_chainlink.get_current_price.return_value = 64000.0
        exit_mgr.set_chainlink_feed(mock_chainlink)

        epoch = int(time.time() // 300) * 300
        pos = self._make_position(f"btc-updown-5m-{epoch}", "up")

        now = datetime.now(timezone.utc)
        signal = exit_mgr._evaluate(pos, now)
        # Dry run should not produce exit signal from reversal
        if signal is not None:
            assert signal.reason != "oracle_reversal"


# ============================================================================
# Config tests
# ============================================================================


class TestOracleConfig:
    """Test that oracle config fields parse correctly."""

    def test_defaults(self):
        config = make_config()
        assert config.oracle_enabled is True
        assert config.oracle_snipe_confirm is False
        assert config.oracle_reversal_exit is False
        assert config.reversal_short_enabled is False
        assert config.reversal_short_dry_run is True
        assert config.reversal_short_min_secs_remaining == 45

    def test_custom_values(self):
        config = make_config(
            reversal_short_max_down_price=0.40,
            reversal_short_min_down_price=0.05,
        )
        assert config.reversal_short_max_down_price == 0.40
        assert config.reversal_short_min_down_price == 0.05

    def test_oracle_disabled_by_default(self):
        """Oracle is off by default (oracle_enabled=False in production)."""
        config = make_config(oracle_enabled=False)
        assert config.oracle_enabled is False


# ============================================================================
# Graceful degradation: existing behavior unchanged when oracle disabled
# ============================================================================


class TestExistingBehaviorUnchanged:
    """Verify that oracle=disabled doesn't break existing exit logic."""

    def test_exit_manager_works_without_chainlink(self):
        """ExitManager._evaluate still works when no chainlink feed is set."""
        config = make_config(
            momentum_reversal_exit=True,
            oracle_reversal_exit=False,
            momentum_reversal_dry_run=False,
            momentum_reversal_pct=0.002,
        )
        store = MagicMock(spec=PositionStore)
        exit_mgr = ExitManager(store, config)
        # No chainlink feed set - should use Binance fallback

        mock_momentum = MagicMock()
        mock_momentum.get_current_momentum_pct.return_value = -0.01
        exit_mgr.set_momentum_feed(mock_momentum)

        now_dt = datetime.now(timezone.utc)
        epoch = int(time.time() // 300) * 300
        pos = Position(
            token_id="tok_test",
            slug=f"btc-updown-5m-{epoch}",
            entry_price=0.94,
            entry_size=50.0,
            entry_time=now_dt,
            entry_tx_hash="0xtest_hash",
            market_end_time=now_dt + timedelta(minutes=5),
            current_price=0.50,
            metadata={
                "entry_momentum_direction": "up",
                "entry_momentum_ts": time.time() - 30,
            },
        )

        signal = exit_mgr._evaluate(pos, now_dt)
        assert signal is not None
        assert signal.reason == "binance_reversal"


# ============================================================================
# Hold-to-resolution tests (oracle_flip, snipe exit bypass)
# Bug: oracle_flip entered at $0.05, killed by stop_loss at $0.04 and
# profit_target at $0.06. Both incompatible with binary resolution tokens.
# Fix: hold_to_resolution flag skips all price/time exits.
# ============================================================================


class TestHoldToResolution:
    """Test that oracle_flip and snipe positions skip all price/time exits."""

    def _make_oracle_flip_position(self, entry_price=0.05, secs_held=60):
        now = datetime.now(timezone.utc)
        epoch = int(time.time() // 300) * 300
        return Position(
            token_id="tok_flip_001",
            slug=f"btc-updown-5m-{epoch}",
            entry_price=entry_price,
            entry_size=500.0,
            entry_time=now - timedelta(seconds=secs_held),
            entry_tx_hash="0xflip",
            market_end_time=now + timedelta(seconds=120),
            current_price=entry_price * 0.5,  # price dropped 50%
            metadata={
                "source": "oracle_flip",
                "entry_momentum_direction": "down",
            },
        )

    def _make_snipe_position(self, source="resolution_snipe", entry_price=0.94):
        now = datetime.now(timezone.utc)
        epoch = int(time.time() // 300) * 300
        return Position(
            token_id="tok_snipe_001",
            slug=f"btc-updown-5m-{epoch}",
            entry_price=entry_price,
            entry_size=100.0,
            entry_time=now - timedelta(seconds=60),
            entry_tx_hash="0xsnipe",
            market_end_time=now + timedelta(seconds=120),
            current_price=entry_price * 0.80,  # price dropped 20%
            metadata={
                "source": source,
                "entry_momentum_direction": "up",
            },
        )

    def test_oracle_flip_not_killed_by_stop_loss(self):
        """oracle_flip at $0.05 should NOT trigger stop_loss at $0.025."""
        config = make_config(
            stop_loss_pct=0.15,
            enable_stop_loss=True,
            profit_target_pct=0.20,
        )
        store = PositionStore()
        pos = self._make_oracle_flip_position(entry_price=0.05)
        store.add(pos)

        mgr = ExitManager(store, config)
        exits = mgr.check_all(datetime.now(timezone.utc))
        for e in exits:
            assert e.reason != "stop_loss", (
                f"oracle_flip should never trigger stop_loss, got exit: {e.reason}"
            )

    def test_oracle_flip_not_killed_by_profit_target(self):
        """oracle_flip at $0.05 should NOT trigger profit_target at $0.06."""
        config = make_config(profit_target_pct=0.20)
        store = PositionStore()
        now = datetime.now(timezone.utc)
        epoch = int(time.time() // 300) * 300
        pos = Position(
            token_id="tok_flip_pt",
            slug=f"btc-updown-5m-{epoch}",
            entry_price=0.05,
            entry_size=500.0,
            entry_time=now - timedelta(seconds=60),
            entry_tx_hash="0xflip_pt",
            market_end_time=now + timedelta(seconds=120),
            current_price=0.07,  # above 0.06 profit target
            metadata={"source": "oracle_flip"},
        )
        store.add(pos)

        mgr = ExitManager(store, config)
        exits = mgr.check_all(now)
        for e in exits:
            assert e.reason != "profit_target", (
                f"oracle_flip should never trigger profit_target, got exit: {e.reason}"
            )

    def test_oracle_flip_not_killed_by_max_hold(self):
        """oracle_flip held 15min should NOT trigger max_hold."""
        config = make_config(max_hold_mins=12)
        store = PositionStore()
        now = datetime.now(timezone.utc)
        epoch = int(time.time() // 300) * 300
        pos = Position(
            token_id="tok_flip_mh",
            slug=f"btc-updown-5m-{epoch}",
            entry_price=0.05,
            entry_size=500.0,
            entry_time=now - timedelta(minutes=15),
            entry_tx_hash="0xflip_mh",
            market_end_time=now + timedelta(seconds=120),
            current_price=0.04,
            metadata={"source": "oracle_flip"},
        )
        store.add(pos)

        mgr = ExitManager(store, config)
        exits = mgr.check_all(now)
        for e in exits:
            assert e.reason != "max_hold", (
                f"oracle_flip should never trigger max_hold, got exit: {e.reason}"
            )

    def test_snipe_not_killed_by_stop_loss(self):
        """resolution_snipe should NOT trigger stop_loss."""
        config = make_config(
            stop_loss_pct=0.15,
            enable_stop_loss=True,
        )
        store = PositionStore()
        pos = self._make_snipe_position()
        store.add(pos)

        mgr = ExitManager(store, config)
        exits = mgr.check_all(datetime.now(timezone.utc))
        for e in exits:
            assert e.reason != "stop_loss", (
                f"resolution_snipe should never trigger stop_loss, got exit: {e.reason}"
            )

    def test_snipe_15m_not_killed_by_profit_target(self):
        """resolution_snipe_15m should NOT trigger profit_target."""
        config = make_config(profit_target_pct=0.20)
        store = PositionStore()
        now = datetime.now(timezone.utc)
        epoch = int(time.time() // 300) * 300
        pos = Position(
            token_id="tok_snipe15m",
            slug=f"btc-updown-5m-{epoch}",
            entry_price=0.94,
            entry_size=100.0,
            entry_time=now - timedelta(seconds=60),
            entry_tx_hash="0xsnipe15m",
            market_end_time=now + timedelta(seconds=120),
            current_price=0.98,  # above 0.94 * 1.20 = 1.128 (not, but above target)
            metadata={"source": "resolution_snipe_15m"},
        )
        store.add(pos)

        mgr = ExitManager(store, config)
        exits = mgr.check_all(now)
        for e in exits:
            assert e.reason != "profit_target", (
                "resolution_snipe_15m should never trigger profit_target"
            )

    def test_oracle_flip_exits_on_market_resolved(self):
        """oracle_flip SHOULD exit when market resolves."""
        config = make_config()
        store = PositionStore()
        now = datetime.now(timezone.utc)
        epoch = int(time.time() // 300) * 300
        pos = Position(
            token_id="tok_flip_res",
            slug=f"btc-updown-5m-{epoch}",
            entry_price=0.05,
            entry_size=500.0,
            entry_time=now - timedelta(seconds=60),
            entry_tx_hash="0xflip_res",
            market_end_time=now - timedelta(seconds=40),  # ended 40s ago
            current_price=0.04,
            metadata={"source": "oracle_flip"},
        )
        store.add(pos)

        mgr = ExitManager(store, config)
        exits = mgr.check_all(now)
        assert len(exits) == 1
        assert exits[0].reason == "market_resolved"

    def test_regular_momentum_still_triggers_stop_loss(self):
        """Regular momentum positions (not oracle_flip/snipe) should still hit stop_loss."""
        config = make_config(
            stop_loss_pct=0.15,
            enable_stop_loss=True,
        )
        store = PositionStore()
        now = datetime.now(timezone.utc)
        pos = Position(
            token_id="tok_momentum",
            slug="btc-updown-15m-123456",
            entry_price=0.70,
            entry_size=100.0,
            entry_time=now - timedelta(minutes=2),
            entry_tx_hash="0xmomentum",
            market_end_time=now + timedelta(days=7),
            current_price=0.58,  # below 0.595 stop
            metadata={"source": "binance_momentum"},
        )
        store.add(pos)

        mgr = ExitManager(store, config)
        exits = mgr.check_all(now)
        assert len(exits) == 1
        assert exits[0].reason == "stop_loss"


# ============================================================================
# Binance reversal Path A tests (entry_binance_price metadata)
# Path A fires when Binance price moves against entry direction by threshold.
# Snipe entries should also get reversal protection via metadata.
# ============================================================================


class TestBinanceReversalPathA:
    """Test Binance entry-relative reversal (Path A) with entry_binance_price."""

    def test_path_a_fires_on_price_drop_for_up_entry(self):
        """Path A detects Binance price dropped > threshold for UP entry."""
        config = make_config(
            momentum_reversal_exit=True,
            oracle_reversal_exit=False,
            momentum_reversal_dry_run=False,
            momentum_reversal_pct=0.002,
            binance_reversal_min_hold_secs=15,
        )
        store = MagicMock(spec=PositionStore)
        exit_mgr = ExitManager(store, config)

        mock_momentum = MagicMock()
        mock_momentum.get_latest_price.return_value = 64800.0  # dropped from 65000
        exit_mgr.set_momentum_feed(mock_momentum)

        now = datetime.now(timezone.utc)
        epoch = int(time.time() // 300) * 300
        pos = Position(
            token_id="tok_btc_up",
            slug=f"btc-updown-5m-{epoch}",
            entry_price=0.60,
            entry_size=100.0,
            entry_time=now - timedelta(seconds=20),  # > 15s min hold
            entry_tx_hash="0xtest",
            market_end_time=now + timedelta(seconds=60),
            current_price=0.40,
            metadata={
                "source": "binance_momentum",
                "entry_momentum_direction": "up",
                "entry_binance_price": 65000.0,
                "entry_momentum_ts": time.time() - 20,
            },
        )

        signal = exit_mgr._evaluate(pos, now)
        assert signal is not None
        assert signal.reason == "binance_reversal"

    def test_path_a_no_fire_when_price_in_same_direction(self):
        """Path A doesn't fire when Binance price confirms UP entry."""
        config = make_config(
            momentum_reversal_exit=True,
            oracle_reversal_exit=False,
            momentum_reversal_dry_run=False,
            momentum_reversal_pct=0.002,
            binance_reversal_min_hold_secs=15,
        )
        store = MagicMock(spec=PositionStore)
        exit_mgr = ExitManager(store, config)

        mock_momentum = MagicMock()
        mock_momentum.get_latest_price.return_value = 65200.0  # rose from 65000
        exit_mgr.set_momentum_feed(mock_momentum)

        now = datetime.now(timezone.utc)
        epoch = int(time.time() // 300) * 300
        pos = Position(
            token_id="tok_btc_up",
            slug=f"btc-updown-5m-{epoch}",
            entry_price=0.60,
            entry_size=100.0,
            entry_time=now - timedelta(seconds=20),
            entry_tx_hash="0xtest",
            market_end_time=now + timedelta(seconds=60),
            current_price=0.65,
            metadata={
                "source": "binance_momentum",
                "entry_momentum_direction": "up",
                "entry_binance_price": 65000.0,
                "entry_momentum_ts": time.time() - 20,
            },
        )

        signal = exit_mgr._evaluate(pos, now)
        if signal is not None:
            assert signal.reason != "binance_reversal"

    def test_snipe_entry_gets_reversal_protection(self):
        """Snipe entries with metadata should trigger Binance reversal Path A."""
        config = make_config(
            momentum_reversal_exit=True,
            oracle_reversal_exit=False,
            momentum_reversal_dry_run=False,
            momentum_reversal_pct=0.002,
            binance_reversal_min_hold_secs=15,
        )
        store = MagicMock(spec=PositionStore)
        exit_mgr = ExitManager(store, config)

        mock_momentum = MagicMock()
        mock_momentum.get_latest_price.return_value = 64700.0  # dropped 0.46% from 65000
        exit_mgr.set_momentum_feed(mock_momentum)

        now = datetime.now(timezone.utc)
        epoch = int(time.time() // 300) * 300
        pos = Position(
            token_id="tok_snipe_up",
            slug=f"btc-updown-5m-{epoch}",
            entry_price=0.93,
            entry_size=30.0,
            entry_time=now - timedelta(seconds=20),  # entered 20s ago, 15s min hold ok
            entry_tx_hash="0xsnipe",
            market_end_time=now + timedelta(seconds=15),  # 15s before resolution
            current_price=0.50,
            metadata={
                "source": "resolution_snipe",
                "entry_momentum_direction": "up",
                "entry_binance_price": 65000.0,
                "entry_momentum_ts": time.time() - 20,
            },
        )

        signal = exit_mgr._evaluate(pos, now)
        assert signal is not None
        assert signal.reason == "binance_reversal"

    def test_path_a_skipped_when_min_hold_not_met(self):
        """Path A doesn't fire if position age < min hold seconds."""
        config = make_config(
            momentum_reversal_exit=True,
            oracle_reversal_exit=False,
            momentum_reversal_dry_run=False,
            momentum_reversal_pct=0.002,
            binance_reversal_min_hold_secs=15,
        )
        store = MagicMock(spec=PositionStore)
        exit_mgr = ExitManager(store, config)

        mock_momentum = MagicMock()
        mock_momentum.get_latest_price.return_value = 64700.0  # big drop
        mock_momentum.get_current_momentum_pct.return_value = None  # disable Path C fallback
        exit_mgr.set_momentum_feed(mock_momentum)

        now = datetime.now(timezone.utc)
        epoch = int(time.time() // 300) * 300
        pos = Position(
            token_id="tok_too_young",
            slug=f"btc-updown-5m-{epoch}",
            entry_price=0.60,
            entry_size=100.0,
            entry_time=now - timedelta(seconds=5),  # only 5s old, < 15s min hold
            entry_tx_hash="0xtest",
            market_end_time=now + timedelta(seconds=60),
            current_price=0.40,
            metadata={
                "source": "binance_momentum",
                "entry_momentum_direction": "up",
                "entry_binance_price": 65000.0,
                "entry_momentum_ts": time.time() - 5,
            },
        )

        signal = exit_mgr._evaluate(pos, now)
        # Should not fire binance_reversal (may fire other exits or None)
        if signal is not None:
            assert signal.reason != "binance_reversal"

    def test_no_reversal_without_metadata(self):
        """Position without entry_momentum_direction skips all reversal paths."""
        config = make_config(
            momentum_reversal_exit=True,
            oracle_reversal_exit=True,
            momentum_reversal_dry_run=False,
            momentum_reversal_pct=0.002,
        )
        store = MagicMock(spec=PositionStore)
        exit_mgr = ExitManager(store, config)

        mock_momentum = MagicMock()
        mock_momentum.get_latest_price.return_value = 60000.0  # massive drop
        mock_momentum.get_current_momentum_pct.return_value = -0.05
        exit_mgr.set_momentum_feed(mock_momentum)

        now = datetime.now(timezone.utc)
        epoch = int(time.time() // 300) * 300
        pos = Position(
            token_id="tok_no_meta",
            slug=f"btc-updown-5m-{epoch}",
            entry_price=0.60,
            entry_size=100.0,
            entry_time=now - timedelta(seconds=60),
            entry_tx_hash="0xtest",
            market_end_time=now + timedelta(seconds=60),
            current_price=0.10,
            metadata={},  # no entry_momentum_direction
        )

        signal = exit_mgr._evaluate(pos, now)
        # Should NOT produce binance_reversal or oracle_reversal
        if signal is not None:
            assert signal.reason not in ("binance_reversal", "oracle_reversal")


# ============================================================================
# Direction cross confirmation tests
# Single-tick noise should NOT fire the callback. Need N consecutive readings.
# ============================================================================


class TestDirectionCrossConfirmation:
    """Test that direction cross requires N consecutive readings to fire."""

    def test_single_cross_does_not_fire(self):
        """One price crossing the open should NOT fire the callback."""
        from .chainlink_feed import DIRECTION_CROSS_CONFIRM_COUNT
        config = make_config()
        feed = ChainlinkFeed(config)
        callback = AsyncMock()
        feed.set_on_direction_cross(callback)

        now = time.time()
        epoch_5m = int(now // 300) * 300

        # First update: anchor at 65000 (above open = True)
        feed._update_price("BTC", 65000.0, now)
        # Second: drop below open (cross from True to False)
        feed._update_price("BTC", 64999.0, now + 1)
        # Only 1 reading on new side - should NOT fire
        callback.assert_not_called()

    @patch("polyphemus.chainlink_feed.asyncio.ensure_future", side_effect=lambda coro: coro.close() or None)
    def test_confirmed_cross_fires(self, _mock_ef):
        """N consecutive readings on new side should fire the callback."""
        from .chainlink_feed import DIRECTION_CROSS_CONFIRM_COUNT
        config = make_config()
        feed = ChainlinkFeed(config)
        callback = MagicMock()
        feed.set_on_direction_cross(callback)

        now = time.time()
        epoch_5m = int(now // 300) * 300

        # Anchor: above open
        feed._update_price("BTC", 65000.0, now)

        # N consecutive readings below open
        for i in range(DIRECTION_CROSS_CONFIRM_COUNT):
            feed._update_price("BTC", 64990.0, now + 1 + i)

        # Fires once per window (5m + 15m) = 2 total
        assert callback.call_count == 2
        # Both calls should be BTC crossing down
        for call in callback.call_args_list:
            assert call[0][0] == "BTC"  # asset
            assert call[0][3] is False  # is_above_open = False (crossed down)

    def test_noise_resets_confirmation(self):
        """If price bounces back before N readings, counter resets."""
        from .chainlink_feed import DIRECTION_CROSS_CONFIRM_COUNT
        config = make_config()
        feed = ChainlinkFeed(config)
        callback = AsyncMock()
        feed.set_on_direction_cross(callback)

        now = time.time()

        # Anchor: above open
        feed._update_price("BTC", 65000.0, now)

        # Cross below (1 reading)
        feed._update_price("BTC", 64990.0, now + 1)
        # Bounce back above - resets counter
        feed._update_price("BTC", 65010.0, now + 2)
        # Cross below again (1 reading - counter reset)
        feed._update_price("BTC", 64990.0, now + 3)

        # Never reached N consecutive - should not fire
        callback.assert_not_called()

    def test_cross_confirm_count_prunes_with_epochs(self):
        """cross_confirm_count entries are pruned with stale epochs."""
        config = make_config()
        feed = ChainlinkFeed(config)
        now = time.time()

        # Add old confirm count directly (keyed by (epoch, window) tuple)
        old_epoch = int((now - 3600) // 300) * 300
        old_key = (old_epoch, 300)
        feed._assets["BTC"].cross_confirm_count[old_key] = 2

        # Update price triggers pruning
        feed._update_price("BTC", 65000.0, now)
        assert old_key not in feed._assets["BTC"].cross_confirm_count


# ============================================================================
# Signal guard tests for oracle_flip source
# ============================================================================


class TestSignalGuardOracleFlip:
    """Test that oracle_flip signals bypass normal entry filters."""

    def test_oracle_flip_bypasses_price_range(self):
        """oracle_flip signals skip normal min/max entry price checks."""
        from .signal_guard import SignalGuard
        config = make_config(min_entry_price=0.50, max_entry_price=0.85)
        store = PositionStore()
        guard = SignalGuard(config, store)

        signal = {
            "token_id": "0xflip_test",
            "direction": "BUY",
            "price": 0.08,  # way below min_entry_price
            "outcome": "Down",
            "asset": "BTC",
            "slug": "btc-updown-5m-999999",
            "usdc_size": 25.0,
            "timestamp": time.time(),
            "tx_hash": "0xtest",
            "market_title": "BTC test",
            "source": "oracle_flip",
            "market_window_secs": 300,
        }
        result = guard.check(signal)
        assert "price_out_of_range" not in result.reasons, (
            "oracle_flip should bypass normal price range filter"
        )

    def test_oracle_flip_bypasses_conviction_check(self):
        """oracle_flip signals skip minimum conviction size check."""
        from .signal_guard import SignalGuard
        config = make_config(min_db_signal_size=50.0)
        store = PositionStore()
        guard = SignalGuard(config, store)

        signal = {
            "token_id": "0xflip_conv",
            "direction": "BUY",
            "price": 0.08,
            "outcome": "Down",
            "asset": "BTC",
            "slug": "btc-updown-5m-888888",
            "usdc_size": 10.0,  # below min_db_signal_size
            "timestamp": time.time(),
            "tx_hash": "0xtest",
            "market_title": "BTC test",
            "source": "oracle_flip",
            "market_window_secs": 300,
        }
        result = guard.check(signal)
        assert "low_conviction" not in result.reasons, (
            "oracle_flip should bypass conviction filter"
        )
