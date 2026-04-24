"""Tests for dry-run-aware startup purge resolution.

Bug: the startup purge at ``SignalBot.start()`` used ``get_share_balance``
to determine win/loss. In DRY_RUN there are no on-chain shares, so every
purged position booked as a total loss regardless of the actual market
outcome. Over the last 7 days of emmanuel data that produced 51 consecutive
``market_resolved`` rows with ``exit_price=0.0`` — a 2^-51 coin-flip that
trained the 2026-04-17 MTC NO-GO verdict on corrupted P&L.

Fix: ``_resolve_dry_run_exit_price`` reuses the same Binance-kline
direction resolver that the phantom_reaper already uses, so startup purge
and steady-state reap agree. This file pins the helper's contract so a
future refactor can't silently regress to the all-loss behavior.

What we're proving:
  1. Trade direction matches kline → exit_price=1.0.
  2. Trade direction mismatches kline → exit_price=0.0.
  3. Flat/error kline (None) → exit_price=entry_price (neutral close).
  4. Missing asset / window / direction metadata → entry_price fallback.
  5. Resolver raising does not crash the caller; returns entry_price.
"""

from __future__ import annotations

import asyncio
import logging
import types

import pytest

from polyphemus import signal_bot as signal_bot_module
from polyphemus.signal_bot import SignalBot


def _fake_pos(
    slug: str = "btc-updown-5m-1776570600",
    entry_price: float = 0.47,
    direction: str | None = "Up",
):
    """Minimal pos stand-in matching the fields the helper reads."""
    md = {"direction": direction} if direction else {}
    return types.SimpleNamespace(
        slug=slug,
        entry_price=entry_price,
        metadata=md,
    )


def _fake_bot_for_resolver(monkeypatch, resolver_return):
    """Bot stub with a patched resolve_direction."""
    async def _stub_resolver(asset, epoch_start_ts, window_secs=300, **kwargs):
        if isinstance(resolver_return, Exception):
            raise resolver_return
        return resolver_return

    monkeypatch.setattr(
        signal_bot_module, "resolve_direction", _stub_resolver
    )

    fake = types.SimpleNamespace()
    fake._logger = logging.getLogger("test.dry_run_purge")
    return fake


def _call(fake, pos, market_end: int = 1_776_570_900) -> float:
    return asyncio.run(
        SignalBot._resolve_dry_run_exit_price(fake, pos, market_end)
    )


class TestResolveDryRunExitPrice:
    def test_direction_matches_kline_returns_one(self, monkeypatch):
        # Trade bet Up, Binance kline also says Up → win → $1.00.
        fake = _fake_bot_for_resolver(monkeypatch, "up")
        pos = _fake_pos(direction="Up")
        assert _call(fake, pos) == 1.0

    def test_direction_mismatches_kline_returns_zero(self, monkeypatch):
        # Trade bet Up, Binance kline says Down → loss → $0.00.
        fake = _fake_bot_for_resolver(monkeypatch, "down")
        pos = _fake_pos(direction="Up")
        assert _call(fake, pos) == 0.0

    def test_flat_candle_returns_entry_price(self, monkeypatch):
        # Binance open==close → resolver returns None → neutral close.
        fake = _fake_bot_for_resolver(monkeypatch, None)
        pos = _fake_pos(direction="Up", entry_price=0.47)
        assert _call(fake, pos) == 0.47

    def test_missing_direction_returns_entry_price(self, monkeypatch):
        # Metadata lacks direction → can't map → neutral close.
        fake = _fake_bot_for_resolver(monkeypatch, "up")
        pos = _fake_pos(direction=None, entry_price=0.51)
        assert _call(fake, pos) == 0.51

    def test_unparseable_slug_returns_entry_price(self, monkeypatch):
        # Weather-style slug has no parseable asset → neutral close.
        fake = _fake_bot_for_resolver(monkeypatch, "up")
        pos = _fake_pos(
            slug="highest-temperature-in-houston-on-apr-19-2026",
            direction="Up",
            entry_price=0.55,
        )
        assert _call(fake, pos) == 0.55

    def test_resolver_exception_does_not_propagate(self, monkeypatch):
        # Network or programming error in resolver must not crash the
        # purge path — falls back to neutral close.
        fake = _fake_bot_for_resolver(monkeypatch, RuntimeError("boom"))
        pos = _fake_pos(direction="Up", entry_price=0.48)
        assert _call(fake, pos) == 0.48

    def test_case_insensitive_direction(self, monkeypatch):
        # Metadata stores "Up" / "Down" with capital; resolver returns
        # lowercase. Helper must compare case-insensitively.
        fake = _fake_bot_for_resolver(monkeypatch, "down")
        pos = _fake_pos(direction="DOWN")  # capital
        assert _call(fake, pos) == 1.0
