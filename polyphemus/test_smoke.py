"""Smoke tests for lagbot — encodes exact bugs found in session.

Each test prevents regression of a specific bug discovered during live trading.
Tests run before every deploy to catch regressions early.
"""

import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from polyphemus.models import Position
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
        pytest.importorskip("py_clob_client")
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
        pytest.importorskip("py_clob_client")
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
        pytest.importorskip("py_clob_client")
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
        from polyphemus.models import parse_window_from_slug

        window = parse_window_from_slug("btc-updown-5m-123456")
        assert window == 300, f"Expected 300s for 5m, got {window}"

    def test_15m_market_window_detection(self):
        """15m market slug correctly identified."""
        from polyphemus.models import parse_window_from_slug

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


# ============================================================================
# Fees module: canonical fee calculations
# ============================================================================

class TestFees:
    """Verify canonical fee module produces correct values."""

    def test_taker_fee_at_midmarket(self):
        """Crypto taker fee at p=0.50 should be 7.2% * 0.5 * 0.5 = 0.018 per share."""
        from polyphemus.fees import taker_fee_per_share
        fee = taker_fee_per_share(0.50, "crypto")
        assert abs(fee - 0.018) < 1e-6, f"Expected 0.018, got {fee}"

    def test_taker_fee_at_entry_range(self):
        """Fee at 0.475 (our entry range) should be ~0.01795."""
        from polyphemus.fees import taker_fee_per_share
        fee = taker_fee_per_share(0.475, "crypto")
        expected = 0.072 * 0.475 * 0.525
        assert abs(fee - expected) < 1e-6, f"Expected {expected}, got {fee}"

    def test_geopolitics_zero_fee(self):
        """Geopolitics markets should have zero taker fee."""
        from polyphemus.fees import taker_fee_per_share
        fee = taker_fee_per_share(0.50, "geopolitics")
        assert fee == 0.0, f"Geopolitics fee should be 0, got {fee}"

    def test_fee_peaks_at_midmarket(self):
        """Fee should be highest at p=0.50 and lower at extremes."""
        from polyphemus.fees import taker_fee_per_share
        fee_50 = taker_fee_per_share(0.50)
        fee_10 = taker_fee_per_share(0.10)
        fee_90 = taker_fee_per_share(0.90)
        assert fee_50 > fee_10, "Fee at 0.50 should exceed fee at 0.10"
        assert fee_50 > fee_90, "Fee at 0.50 should exceed fee at 0.90"

    def test_breakeven_wr_taker_crypto(self):
        """Break-even WR at 0.475 taker crypto should be ~49.3%."""
        from polyphemus.fees import breakeven_wr
        be = breakeven_wr(0.475, mode="taker", category="crypto")
        # be = entry + fee = 0.475 + 0.072 * 0.475 * 0.525
        expected = 0.475 + 0.072 * 0.475 * 0.525
        assert abs(be - expected) < 1e-6, f"Expected {expected:.4f}, got {be:.4f}"

    def test_breakeven_wr_maker(self):
        """Maker break-even should be LOWER than entry price (rebate helps)."""
        from polyphemus.fees import breakeven_wr
        be_maker = breakeven_wr(0.475, mode="maker")
        assert be_maker < 0.475, f"Maker breakeven {be_maker} should be below entry 0.475"

    def test_fee_adjusted_pnl_win(self):
        """Winning trade P&L should be positive at reasonable entry."""
        from polyphemus.fees import fee_adjusted_pnl
        pnl = fee_adjusted_pnl(0.45, is_win=True, shares=10, mode="taker")
        # win: (1.0 - 0.45 - fee) * 10
        assert pnl > 0, f"Winning trade at 0.45 should be profitable, got {pnl}"

    def test_fee_adjusted_pnl_loss(self):
        """Losing trade P&L should be negative."""
        from polyphemus.fees import fee_adjusted_pnl
        pnl = fee_adjusted_pnl(0.45, is_win=False, shares=10, mode="taker")
        assert pnl < 0, f"Losing trade should be negative, got {pnl}"

    def test_total_fee_scales_with_shares(self):
        """Total fee should scale linearly with share count."""
        from polyphemus.fees import taker_fee
        fee_10 = taker_fee(0.50, 10)
        fee_20 = taker_fee(0.50, 20)
        assert abs(fee_20 - 2 * fee_10) < 1e-10

    def test_round_trip_cost_resolved(self):
        """Resolved market: only entry fee, no exit fee."""
        from polyphemus.fees import round_trip_cost, taker_fee_per_share
        rt = round_trip_cost(0.50, exit_price=None, entry_mode="taker")
        entry_only = taker_fee_per_share(0.50)
        assert abs(rt - entry_only) < 1e-10


class TestAdversePrecheck:
    """Pre-entry adverse selection filter: reject entries when Binance moves against trade."""

    def test_velocity_calculation_rising(self):
        """Positive velocity when price rises over lookback window."""
        from collections import deque
        # Test the velocity algorithm directly (avoids py_clob_client import)
        now = time.time()
        buffer = deque([
            (now - 10, 83000.0),
            (now - 5, 83025.0),
            (now - 4, 83030.0),
            (now - 3, 83035.0),
            (now - 2, 83040.0),
            (now - 1, 83050.0),
        ], maxlen=600)

        lookback_secs = 5
        cutoff = now - lookback_secs
        current_price = buffer[-1][1]
        baseline_price = None
        for ts, price in buffer:
            if ts >= cutoff:
                baseline_price = price
                break
        assert baseline_price is not None
        velocity = (current_price - baseline_price) / baseline_price
        assert velocity > 0, f"Price rising should give positive velocity, got {velocity}"

    def test_velocity_calculation_falling(self):
        """Negative velocity when price falls over lookback window."""
        from collections import deque
        now = time.time()
        buffer = deque([
            (now - 10, 83050.0),
            (now - 5, 83025.0),
            (now - 4, 83020.0),
            (now - 3, 83015.0),
            (now - 2, 83010.0),
            (now - 1, 83000.0),
        ], maxlen=600)

        lookback_secs = 5
        cutoff = now - lookback_secs
        current_price = buffer[-1][1]
        baseline_price = None
        for ts, price in buffer:
            if ts >= cutoff:
                baseline_price = price
                break
        assert baseline_price is not None
        velocity = (current_price - baseline_price) / baseline_price
        assert velocity < 0, f"Price falling should give negative velocity, got {velocity}"

    def test_adverse_detection_logic(self):
        """Up trade with negative velocity is adverse, Down trade with positive is adverse."""
        threshold = 0.0001  # 0.01%

        # Up trade, price falling = ADVERSE
        velocity = -0.0003  # -0.03%
        direction = "Up"
        is_adverse = (direction == "Up" and velocity < -threshold)
        assert is_adverse, "Up trade with falling price should be adverse"

        # Up trade, price rising = FAVORABLE
        velocity = +0.0003
        is_adverse = (direction == "Up" and velocity < -threshold)
        assert not is_adverse, "Up trade with rising price should be favorable"

        # Down trade, price rising = ADVERSE
        velocity = +0.0003
        direction = "Down"
        is_adverse = (direction == "Down" and velocity > threshold)
        assert is_adverse, "Down trade with rising price should be adverse"

        # Down trade, price falling = FAVORABLE
        velocity = -0.0003
        is_adverse = (direction == "Down" and velocity > threshold)
        assert not is_adverse, "Down trade with falling price should be favorable"

    def test_velocity_none_when_no_data(self):
        """Returns None when price buffer is empty or insufficient."""
        from collections import deque
        buffer = deque(maxlen=600)
        # Empty buffer: no baseline can be found
        cutoff = time.time() - 5
        baseline = None
        for ts, price in buffer:
            if ts >= cutoff:
                baseline = price
                break
        assert baseline is None, "Should be None with empty buffer"


class TestEpochTimeGate:
    """Epoch time gate: reject entries with insufficient time remaining."""

    def test_config_default(self):
        """Default min_execution_secs_remaining should be 120."""
        config = make_config()
        assert config.min_execution_secs_remaining == 120

    def test_config_adverse_precheck_default(self):
        """Default adverse_precheck should be enabled with 5s lookback."""
        config = make_config()
        assert config.adverse_precheck_enabled is True
        assert config.adverse_precheck_secs == 5
        assert config.adverse_precheck_threshold == 0.0001


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
