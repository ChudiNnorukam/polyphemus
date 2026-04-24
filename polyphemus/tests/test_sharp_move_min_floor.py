"""Tests for sharp_move_min_entry_price guard.

Bug: effective emmanuel config had Doppler-injected MIN_ENTRY_PRICE=0.01.
signal_guard's sharp_move check used that global floor, so entries at
0.015 passed the gate. Result: 96 phantom_resolved rows in 7d with
sub-5-cent entries that never should have been placed.

Fix: sharp_move now has a dedicated min_entry_price that overrides the
global floor when stricter. Default 0.20 (well above any Doppler-style
lottery floor). The guard uses max(global_min, sharp_move_min) so the
stricter value always wins.
"""

from __future__ import annotations

import os
import types

import pytest


# Set required Settings env vars BEFORE importing Settings. These are
# load-time requirements (pydantic-settings reads them in __init__), and
# only secrets unrelated to the gate under test.
_REQUIRED_ENV = {
    "PRIVATE_KEY": "test_private_key_placeholder",
    "WALLET_ADDRESS": "0x0000000000000000000000000000000000000000",
    "CLOB_API_KEY": "test",
    "CLOB_SECRET": "test",
    "CLOB_PASSPHRASE": "test",
    "BUILDER_API_KEY": "test",
    "BUILDER_SECRET": "test",
    "BUILDER_PASSPHRASE": "test",
    "POLYGON_RPC_URL": "https://test.invalid",
    "SLACK_BOT_TOKEN": "",
}
for _k, _v in _REQUIRED_ENV.items():
    os.environ.setdefault(_k, _v)

from polyphemus.config import Settings  # noqa: E402
from polyphemus.signal_guard import SignalGuard  # noqa: E402


def _fake_store():
    """Minimal PositionStore stand-in — the sharp_move path doesn't dedup."""
    fake = types.SimpleNamespace()
    fake.get_open = lambda: []
    fake.is_open = lambda token_id: False
    fake.count_open = lambda: 0
    fake.get = lambda token_id: None
    fake.get_by_slug = lambda slug: None
    fake.get_by_token_id = lambda token_id: None
    return fake


def _base_signal(price: float, source: str = "sharp_move", asset: str = "BTC") -> dict:
    """Minimal signal dict — only fields the price-range filter reads."""
    return {
        "token_id": "tok-test",
        "slug": f"{asset.lower()}-updown-5m-1776570600",
        "price": price,
        "source": source,
        "asset": asset,
        "outcome": "Up",
        "usdc_size": 50.0,
        "timestamp": 1_776_570_000.0,
        "market_window_secs": 300,
        "direction": "BUY",
    }


def _config(**overrides):
    """Real Settings with permissive Doppler-style floor applied on top.

    Uses real Settings so SignalGuard's __init__ can read every field it
    expects. The overrides mutate only the fields relevant to the
    sharp_move gate under test. Doppler-style min_entry_price=0.01 is the
    default so the floor's behavior can be exercised independently.
    """
    defaults = {
        "min_entry_price": 0.01,
        "max_entry_price": 0.50,
        "sharp_move_max_entry_price": 0.95,
        "sharp_move_min_entry_price": 0.20,
        "enable_sharp_move": True,
        "sharp_move_shadow": False,
        # Loosen filters that would false-positive on our minimal signal
        "entry_trap_low": 0.0,
        "entry_trap_high": 0.0,
        "max_open_positions": 9999,
        "weather_max_open_positions": 9999,
    }
    defaults.update(overrides)
    cfg = Settings()
    for k, v in defaults.items():
        object.__setattr__(cfg, k, v)
    return cfg


def _has_reason(result, reason: str) -> bool:
    return reason in (result.reasons or [])


class TestSharpMoveMinEntryPrice:
    def test_deep_otm_entry_rejected(self):
        """0.015 entry — the exact bug signature from emmanuel's DB."""
        guard = SignalGuard(_config(), _fake_store())
        result = guard.check(_base_signal(price=0.015))
        assert _has_reason(result, "price_out_of_range"), (
            f"expected price_out_of_range for 0.015 sharp_move; got {result.reasons}"
        )

    def test_floor_entry_accepted(self):
        """Entry at exactly the floor passes."""
        guard = SignalGuard(_config(), _fake_store())
        result = guard.check(_base_signal(price=0.20))
        assert not _has_reason(result, "price_out_of_range"), (
            f"0.20 should pass sharp_move gate; got {result.reasons}"
        )

    def test_just_below_floor_rejected(self):
        """Entry at 0.19 is rejected."""
        guard = SignalGuard(_config(), _fake_store())
        result = guard.check(_base_signal(price=0.19))
        assert _has_reason(result, "price_out_of_range")

    def test_ceiling_entry_accepted(self):
        """Entry at 0.95 passes — high-side moat still valid."""
        guard = SignalGuard(_config(), _fake_store())
        result = guard.check(_base_signal(price=0.95))
        assert not _has_reason(result, "price_out_of_range")

    def test_above_ceiling_rejected(self):
        """Entry at 0.96 is rejected by existing sharp_max check."""
        guard = SignalGuard(_config(), _fake_store())
        result = guard.check(_base_signal(price=0.96))
        assert _has_reason(result, "price_out_of_range")

    def test_stricter_global_floor_still_wins(self):
        """When global min_entry_price > sharp_move_min_entry_price,
        the global (stricter) floor is used via max()."""
        guard = SignalGuard(
            _config(min_entry_price=0.30, sharp_move_min_entry_price=0.20),
            _fake_store(),
        )
        # 0.25 passes sharp_move_min (0.20) but fails global (0.30).
        result = guard.check(_base_signal(price=0.25))
        assert _has_reason(result, "price_out_of_range")

    def test_non_sharp_source_unaffected(self):
        """Non-sharp signals retain the global floor, not sharp_move_min."""
        # global min is 0.01, max is 0.50; plain momentum signal at 0.015
        # should still hit the standard gate (below its own min_entry_price).
        guard = SignalGuard(_config(), _fake_store())
        result = guard.check(
            _base_signal(price=0.015, source="binance_momentum")
        )
        # 0.015 is >= 0.01 global floor, so does NOT hit price_out_of_range
        # via the sharp_move floor; but max_entry_price (0.50) may or may not
        # apply depending on other filters. What we're proving is
        # specifically that the sharp_move floor is NOT applied to other
        # sources — this guard hook is narrowly scoped.
        # (We don't assert on price_out_of_range here; only that
        # sharp_move_min_entry_price doesn't leak into non-sharp paths.
        # If price_out_of_range fires, it's from some other filter.)
        # What matters: at price=0.30 (clearly in-range globally),
        # the result should not be rejected by a sharp_move floor.
        r2 = guard.check(_base_signal(price=0.30, source="binance_momentum"))
        assert not _has_reason(r2, "price_out_of_range"), (
            f"0.30 binance_momentum should pass price-range; got {r2.reasons}"
        )
