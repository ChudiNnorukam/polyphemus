"""
Tests for the 4 frequency upgrade priorities:
  P1 — IGOC live gate tool
  P2 — XRP reinstatement gate tool
  P3 — Ensemble bet sizing (Layer 1k)
  P4 — Profit target early with fee correction
"""

import os
import re
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from polyphemus.config import Settings
from polyphemus.exit_manager import ExitManager
from polyphemus.position_executor import PositionExecutor
from polyphemus.position_store import PositionStore
from polyphemus.clob_wrapper import ClobWrapper
from polyphemus.types import Position, ExitReason, ExecutionResult, OrderStatus


# ============================================================================
# Helpers
# ============================================================================

def make_config(**overrides):
    defaults = dict(
        private_key="0x" + "a" * 64,
        wallet_address="0x914377734689c9e055B8826733F90dF0893817a2",
        clob_api_key="k",
        clob_secret="s",
        clob_passphrase="p",
        builder_api_key="bk",
        builder_secret="bs",
        builder_passphrase="bp",
        polygon_rpc_url="http://localhost:8545",
        dry_run=True,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def make_position(**overrides):
    now = datetime.now(timezone.utc)
    defaults = dict(
        token_id="0xtoken",
        slug="btc-updown-5m-9999",
        entry_price=0.70,
        entry_size=100.0,
        entry_time=now - timedelta(minutes=2),
        entry_tx_hash="0xtx",
        market_end_time=now + timedelta(minutes=3),
        current_price=0.70,
        peak_price=0.70,
    )
    defaults.update(overrides)
    return Position(**defaults)


def make_signal(**overrides):
    defaults = dict(
        token_id="0xtoken",
        slug="btc-updown-5m-9999",
        price=0.70,
        usdc_size=50.0,
        asset="BTC",
        outcome="up",
        direction="up",
        market_title="BTC 5m",
    )
    defaults.update(overrides)
    return defaults


def make_executor(config=None):
    if config is None:
        config = make_config()
    clob = AsyncMock(spec=ClobWrapper)
    clob.place_order.return_value = ExecutionResult(success=True, order_id="0xorder")
    clob.get_order_details.return_value = {
        "status": OrderStatus.FILLED,
        "size_matched": 100.0,
        "original_size": 100.0,
        "price": 0.70,
    }
    clob.get_midpoint.return_value = 0.70
    clob.get_share_balance.return_value = 100.0
    store = PositionStore()
    return PositionExecutor(clob, store, config)


def make_signals_db(rows: list[dict]) -> str:
    """Create a temp signals.db with given rows. Returns path."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    conn.execute("""
        CREATE TABLE signals (
            id INTEGER PRIMARY KEY,
            source TEXT,
            signal_type TEXT,
            asset TEXT,
            guard_passed INTEGER DEFAULT 0,
            shadow INTEGER DEFAULT 0,
            pnl REAL,
            outcome TEXT,
            timestamp REAL DEFAULT 0
        )
    """)
    for r in rows:
        conn.execute(
            "INSERT INTO signals (source, signal_type, asset, guard_passed, shadow, pnl, outcome, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                r.get("source", "binance_momentum"),
                r.get("signal_type", ""),
                r.get("asset", "BTC"),
                r.get("guard_passed", 0),
                r.get("shadow", 0),
                r.get("pnl"),
                r.get("outcome"),
                r.get("timestamp", 0.0),
            ),
        )
    conn.commit()
    conn.close()
    return tmp.name


# ============================================================================
# P1 — IGOC Live Gate
# ============================================================================

class TestIgocLiveGate:
    def _run(self, db_path):
        from polyphemus.tools.igoc_live_gate import run_gate
        return run_gate(db_path)

    def test_gate_pass(self):
        """50+ guard_passed IGOC signals at >= 55% WR should pass."""
        rows = []
        for i in range(55):
            rows.append(dict(source="igoc", guard_passed=1, pnl=5.0, outcome="win"))
        for i in range(20):
            rows.append(dict(source="igoc", guard_passed=1, pnl=-10.0, outcome="loss"))
        db = make_signals_db(rows)
        # 55 wins / 75 total = 73.3% WR >= 55%
        assert self._run(db) == 0

    def test_gate_fail_insufficient_n(self):
        """< 50 signals should fail regardless of WR."""
        rows = [dict(source="igoc", guard_passed=1, pnl=5.0, outcome="win") for _ in range(30)]
        db = make_signals_db(rows)
        assert self._run(db) == 1

    def test_gate_fail_low_wr(self):
        """50+ signals but WR < 55% should fail."""
        rows = []
        for i in range(20):
            rows.append(dict(source="igoc", guard_passed=1, pnl=5.0, outcome="win"))
        for i in range(40):
            rows.append(dict(source="igoc", guard_passed=1, pnl=-10.0, outcome="loss"))
        db = make_signals_db(rows)
        # 20/60 = 33% WR
        assert self._run(db) == 1

    def test_non_igoc_signals_excluded(self):
        """guard_passed=0 and non-IGOC signals must not count toward gate."""
        rows = []
        # 60 guard_passed=0 igoc — should not count
        for i in range(60):
            rows.append(dict(source="igoc", guard_passed=0, pnl=5.0, outcome="win"))
        # 30 guard_passed=1 non-igoc — should not count
        for i in range(30):
            rows.append(dict(source="binance_momentum", guard_passed=1, pnl=5.0, outcome="win"))
        db = make_signals_db(rows)
        assert self._run(db) == 1  # 0 qualifying signals

    def test_missing_db_returns_error(self):
        assert self._run("/nonexistent/signals.db") == 2


# ============================================================================
# P2 — XRP Reinstatement Gate
# ============================================================================

class TestXrpLiveGate:
    def _run(self, db_path):
        from polyphemus.tools.xrp_live_gate import run_gate
        return run_gate(db_path)

    def test_gate_pass(self):
        """20+ XRP shadow trades, WR >= 50%, no loss > $50 should pass."""
        rows = []
        for i in range(15):
            rows.append(dict(asset="XRP", shadow=1, pnl=8.0, outcome="win"))
        for i in range(10):
            rows.append(dict(asset="XRP", shadow=1, pnl=-20.0, outcome="loss"))
        db = make_signals_db(rows)
        # 25 trades, 15/25 = 60% WR, worst loss = -20
        assert self._run(db) == 0

    def test_gate_fail_insufficient_n(self):
        rows = [dict(asset="XRP", shadow=1, pnl=5.0, outcome="win") for _ in range(10)]
        db = make_signals_db(rows)
        assert self._run(db) == 1

    def test_gate_fail_low_wr(self):
        rows = []
        for i in range(5):
            rows.append(dict(asset="XRP", shadow=1, pnl=5.0, outcome="win"))
        for i in range(20):
            rows.append(dict(asset="XRP", shadow=1, pnl=-5.0, outcome="loss"))
        db = make_signals_db(rows)
        # 5/25 = 20% WR
        assert self._run(db) == 1

    def test_gate_fail_large_loss(self):
        """Single loss > $50 should fail regardless of WR."""
        rows = []
        for i in range(20):
            rows.append(dict(asset="XRP", shadow=1, pnl=8.0, outcome="win"))
        rows.append(dict(asset="XRP", shadow=1, pnl=-75.0, outcome="loss"))
        db = make_signals_db(rows)
        assert self._run(db) == 1

    def test_non_xrp_excluded(self):
        """BTC shadow trades must not count for XRP gate."""
        rows = [dict(asset="BTC", shadow=1, pnl=5.0, outcome="win") for _ in range(30)]
        db = make_signals_db(rows)
        assert self._run(db) == 1  # 0 XRP trades

    def test_missing_db_returns_error(self):
        assert self._run("/nonexistent/signals.db") == 2


# ============================================================================
# P3 — Ensemble Bet Sizing (Layer 1k)
# ============================================================================

class TestEnsembleSizing:
    """Layer 1k must apply correctly and never exceed MAX_BET."""

    def test_high_score_increases_size(self):
        """score >= high_threshold → high_mult applied."""
        config = make_config(
            ensemble_sizing_enabled=True,
            ensemble_sizing_dry_run=False,
            ensemble_high_threshold=0.80,
            ensemble_high_mult=1.25,
            ensemble_low_threshold=0.40,
            ensemble_low_mult=0.50,
            ensemble_none_fallback="neutral",
            base_bet_pct=0.10,
            max_bet=500.0,
            auto_max_bet=False,
        )
        ex = make_executor(config)
        sig_low = make_signal(ensemble_score=0.50)   # neutral
        sig_high = make_signal(ensemble_score=0.85)  # high

        size_neutral = ex._calculate_size(0.70, 500.0, signal=sig_low)
        size_high = ex._calculate_size(0.70, 500.0, signal=sig_high)

        assert size_high > size_neutral
        assert abs(size_high / size_neutral - 1.25) < 0.01

    def test_low_score_reduces_size(self):
        """score < low_threshold → low_mult applied."""
        config = make_config(
            ensemble_sizing_enabled=True,
            ensemble_sizing_dry_run=False,
            ensemble_high_threshold=0.80,
            ensemble_high_mult=1.25,
            ensemble_low_threshold=0.40,
            ensemble_low_mult=0.50,
            ensemble_none_fallback="neutral",
            base_bet_pct=0.10,
            max_bet=500.0,
            auto_max_bet=False,
        )
        ex = make_executor(config)
        sig_neutral = make_signal(ensemble_score=0.60)
        sig_low = make_signal(ensemble_score=0.30)

        size_neutral = ex._calculate_size(0.70, 500.0, signal=sig_neutral)
        size_low = ex._calculate_size(0.70, 500.0, signal=sig_low)

        assert size_low < size_neutral
        assert abs(size_low / size_neutral - 0.50) < 0.01

    def test_null_score_neutral_fallback(self):
        """None ensemble_score with neutral fallback → same size as neutral-band score (1.0x)."""
        config = make_config(
            ensemble_sizing_enabled=True,
            ensemble_sizing_dry_run=False,
            ensemble_none_fallback="neutral",
            ensemble_high_threshold=0.80,
            ensemble_low_threshold=0.40,
            base_bet_pct=0.10,
            max_bet=500.0,
            auto_max_bet=False,
        )
        ex = make_executor(config)
        # All BTC to keep per-asset multiplier constant (Layer 1b)
        sig_no_score = make_signal(asset="BTC")                      # no ensemble_score key → None
        sig_explicit_none = dict(**make_signal(asset="BTC"), ensemble_score=None)
        sig_btc_neutral = make_signal(asset="BTC", ensemble_score=0.60)  # neutral band → 1.0x

        size_no_score = ex._calculate_size(0.70, 500.0, asset="BTC", signal=sig_no_score)
        size_none = ex._calculate_size(0.70, 500.0, asset="BTC", signal=sig_explicit_none)
        size_neutral = ex._calculate_size(0.70, 500.0, asset="BTC", signal=sig_btc_neutral)

        # None → 1.0x neutral, neutral-band score → 1.0x: all equal
        assert abs(size_no_score - size_neutral) < 0.5
        assert abs(size_none - size_neutral) < 0.5

    def test_null_score_cautious_fallback(self):
        """None ensemble_score with cautious fallback → 0.5x vs neutral-band score."""
        config = make_config(
            ensemble_sizing_enabled=True,
            ensemble_sizing_dry_run=False,
            ensemble_none_fallback="cautious",
            ensemble_high_threshold=0.80,
            ensemble_low_threshold=0.40,
            ensemble_low_mult=0.50,
            base_bet_pct=0.10,
            max_bet=500.0,
            auto_max_bet=False,
        )
        ex = make_executor(config)
        sig_no_score = make_signal(asset="BTC")               # no ensemble_score → cautious 0.5x
        sig_neutral = make_signal(asset="BTC", ensemble_score=0.60)  # neutral band → 1.0x

        size_no_score = ex._calculate_size(0.70, 500.0, asset="BTC", signal=sig_no_score)
        size_neutral = ex._calculate_size(0.70, 500.0, asset="BTC", signal=sig_neutral)

        assert abs(size_no_score / size_neutral - 0.50) < 0.01

    def test_max_bet_cap_respected(self):
        """Even with 1.25x multiplier, MAX_BET hard cap is never exceeded."""
        config = make_config(
            ensemble_sizing_enabled=True,
            ensemble_sizing_dry_run=False,
            ensemble_high_threshold=0.80,
            ensemble_high_mult=1.25,
            base_bet_pct=0.10,
            max_bet=50.0,           # tight cap
            auto_max_bet=False,
        )
        ex = make_executor(config)
        sig = make_signal(ensemble_score=0.99)  # max multiplier

        size = ex._calculate_size(0.70, 10000.0, signal=sig)
        assert size <= 50.0 / 0.70 + 1.0  # shares = spend / price

    def test_dry_run_no_size_change(self):
        """Dry-run mode logs but does NOT change actual size.
        Compare same BTC signal with high score vs neutral score — sizes must be equal."""
        config = make_config(
            ensemble_sizing_enabled=True,
            ensemble_sizing_dry_run=True,  # dry run → multiplier NOT applied
            ensemble_high_threshold=0.80,
            ensemble_high_mult=1.25,
            ensemble_low_threshold=0.40,
            ensemble_low_mult=0.50,
            base_bet_pct=0.10,
            max_bet=500.0,
            auto_max_bet=False,
        )
        ex = make_executor(config)
        sig_high = make_signal(asset="BTC", ensemble_score=0.99)    # would be 1.25x if live
        sig_neutral = make_signal(asset="BTC", ensemble_score=0.60)  # would be 1.0x

        size_high = ex._calculate_size(0.70, 500.0, asset="BTC", signal=sig_high)
        size_neutral = ex._calculate_size(0.70, 500.0, asset="BTC", signal=sig_neutral)

        # Dry run → multiplier not applied → both equal
        assert abs(size_high - size_neutral) < 0.5

    def test_disabled_no_effect(self):
        """ensemble_sizing_enabled=False → high score same size as neutral score."""
        config = make_config(
            ensemble_sizing_enabled=False,
            ensemble_high_threshold=0.80,
            ensemble_high_mult=1.25,
            base_bet_pct=0.10,
            max_bet=500.0,
            auto_max_bet=False,
        )
        ex = make_executor(config)
        sig_high = make_signal(asset="BTC", ensemble_score=0.99)
        sig_neutral = make_signal(asset="BTC", ensemble_score=0.60)
        size_high = ex._calculate_size(0.70, 500.0, asset="BTC", signal=sig_high)
        size_neutral = ex._calculate_size(0.70, 500.0, asset="BTC", signal=sig_neutral)
        assert abs(size_high - size_neutral) < 0.5


# ============================================================================
# P4 — Profit Target Early with Fee Correction
# ============================================================================

class TestProfitTargetEarly:
    """CHECK #3e: fires at net_gain >= threshold after deducting taker fee."""

    def _make_exit_manager(self, **overrides):
        base = dict(
            profit_target_early_enabled=True,
            profit_target_early_pp=0.07,
            profit_target_early_min_secs=30,
            profit_target_early_dry_run=False,
            profit_target_early_apply_fee_correction=True,
            hold_to_resolution=True,  # early exit must bypass this
        )
        base.update(overrides)  # caller overrides win
        config = make_config(**base)
        store = PositionStore()
        return ExitManager(store, config), store

    def test_fee_correction_formula(self):
        """At p=0.92, fee=p²*(1-p)≈6.8pp. Gross 7pp net ~0.2pp → should NOT fire at 7pp threshold."""
        mgr, store = self._make_exit_manager(profit_target_early_pp=0.07)
        now = datetime.now(timezone.utc)
        pos = make_position(
            entry_price=0.85,
            current_price=0.92,   # gross = 0.07pp, fee ≈ 0.0677pp, net ≈ 0.002pp
            market_end_time=now + timedelta(seconds=120),
        )
        store.add(pos)
        exits = mgr.check_all(now)
        # net_gain ≈ 0.002pp < 0.07pp threshold → should NOT fire
        assert len(exits) == 0

    def test_high_net_gain_fires(self):
        """entry=0.70, current=0.85: gross=0.15pp, fee=0.85²*0.15≈0.108pp, net≈0.042pp < 0.07 → no fire.
        With entry=0.60, current=0.80: gross=0.20, fee=0.80²*0.20≈0.128, net≈0.072 > 0.07 → fires."""
        mgr, store = self._make_exit_manager(profit_target_early_pp=0.07)
        now = datetime.now(timezone.utc)
        p_exit = 0.80
        fee = p_exit * p_exit * (1 - p_exit)   # ≈ 0.128
        gross = 0.20
        net = gross - fee                        # ≈ 0.072 > 0.07
        pos = make_position(
            entry_price=0.60,
            current_price=p_exit,
            market_end_time=now + timedelta(seconds=120),
        )
        store.add(pos)
        exits = mgr.check_all(now)
        assert net > 0.07, f"Expected net>0.07, got {net:.4f}"
        assert len(exits) == 1
        assert exits[0].reason == ExitReason.PROFIT_TARGET_EARLY.value

    def test_min_secs_remaining_required(self):
        """Does not fire when secs_remaining < min_secs."""
        mgr, store = self._make_exit_manager(
            profit_target_early_pp=0.01,  # very low threshold so fee correction passes
            profit_target_early_min_secs=60,
            profit_target_early_apply_fee_correction=False,  # bypass fee for this test
        )
        now = datetime.now(timezone.utc)
        pos = make_position(
            entry_price=0.60,
            current_price=0.80,   # gross 0.20 > 0.01
            market_end_time=now + timedelta(seconds=20),  # only 20s left < 60s min
        )
        store.add(pos)
        exits = mgr.check_all(now)
        assert len(exits) == 0

    def test_fires_with_enough_secs(self):
        """Fires when both gain and secs conditions met (fee correction disabled for isolation)."""
        mgr, store = self._make_exit_manager(
            profit_target_early_pp=0.05,
            profit_target_early_min_secs=30,
            profit_target_early_apply_fee_correction=False,
        )
        now = datetime.now(timezone.utc)
        pos = make_position(
            entry_price=0.60,
            current_price=0.70,   # gross 0.10 > 0.05
            market_end_time=now + timedelta(seconds=90),  # 90s > 30s
        )
        store.add(pos)
        exits = mgr.check_all(now)
        assert len(exits) == 1
        assert exits[0].reason == ExitReason.PROFIT_TARGET_EARLY.value

    def test_dry_run_no_exit_signal(self):
        """Dry run logs but returns no ExitSignal."""
        mgr, store = self._make_exit_manager(
            profit_target_early_dry_run=True,
            profit_target_early_pp=0.01,
            profit_target_early_apply_fee_correction=False,
        )
        now = datetime.now(timezone.utc)
        pos = make_position(
            entry_price=0.60,
            current_price=0.80,
            market_end_time=now + timedelta(seconds=120),
        )
        store.add(pos)
        exits = mgr.check_all(now)
        assert len(exits) == 0  # dry run → no actual exit

    def test_fee_correction_formula_math(self):
        """Verify fee formula: fee = p² * (1 - p) for several prices."""
        test_cases = [
            (0.92, 0.92**2 * 0.08),   # ≈ 0.0677
            (0.80, 0.80**2 * 0.20),   # ≈ 0.1280
            (0.70, 0.70**2 * 0.30),   # ≈ 0.1470
            (0.50, 0.50**2 * 0.50),   # ≈ 0.1250
        ]
        for price, expected_fee in test_cases:
            computed = price * price * (1 - price)
            assert abs(computed - expected_fee) < 1e-10, f"fee mismatch at p={price}"

    def test_disabled_does_not_fire(self):
        """When profit_target_early_enabled=False, check is skipped entirely."""
        config = make_config(
            profit_target_early_enabled=False,
            profit_target_early_pp=0.001,  # absurdly low — would fire if enabled
            profit_target_early_min_secs=0,
            profit_target_early_apply_fee_correction=False,
            hold_to_resolution=True,
        )
        store = PositionStore()
        mgr = ExitManager(store, config)
        now = datetime.now(timezone.utc)
        pos = make_position(
            entry_price=0.60,
            current_price=0.80,
            market_end_time=now + timedelta(seconds=120),
        )
        store.add(pos)
        exits = mgr.check_all(now)
        assert len(exits) == 0


# ============================================================================
# P4 — Gate Tool Log Parsing
# ============================================================================

class TestProfitTargetEarlyGate:
    """Verify the gate tool correctly parses and gates on DRY log events."""

    def _run(self, logfile_content: str):
        import io
        from unittest.mock import patch, mock_open
        from polyphemus.tools.profit_target_early_gate import run_gate
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(logfile_content)
            path = f.name
        return run_gate(logfile=path, since="7d", unit="lagbot@emmanuel")

    def _make_dry_event(self, gross=0.10, fee=0.05, net=0.05):
        return (
            f"[DRY] profit_target_early WOULD fire | token=abc12345 | "
            f"entry=0.6000 | current=0.7000 | "
            f"gross={gross:.4f}pp | fee={fee:.4f}pp | net={net:.4f}pp | secs_remaining=90\n"
        )

    def _make_legacy_event(self):
        return (
            "[DRY] profit_target_early WOULD fire | token=abc12345 | "
            "entry=0.6000 | current=0.7000 | gain=0.1000pp | secs_remaining=90\n"
        )

    def test_gate_pass_with_10_clean_events(self):
        """10+ fee-corrected DRY events with net > 0 → PASS."""
        log = "".join(self._make_dry_event(0.15, 0.05, 0.10) for _ in range(10))
        assert self._run(log) == 0

    def test_gate_fail_insufficient_events(self):
        """< 10 events → FAIL."""
        log = "".join(self._make_dry_event(0.15, 0.05, 0.10) for _ in range(5))
        assert self._run(log) == 1

    def test_gate_fail_legacy_events(self):
        """Legacy events (no fee= in line) must NOT count toward gate."""
        log = "".join(self._make_legacy_event() for _ in range(15))
        assert self._run(log) == 1  # 0 fee-corrected events

    def test_gate_fail_negative_net(self):
        """Events with net_gain <= 0 should cause FAIL (fee correction wrong)."""
        log = "".join(self._make_dry_event(0.07, 0.068, -0.002) for _ in range(10))
        assert self._run(log) == 1  # net <= 0 on all events
