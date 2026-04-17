"""Phase 1.4 — probabilistic maker fill model for dry-run mode.

Before Phase 1.4, every dry-run resting maker order reported as filled
the instant it was checked. This made dry-run P&L and hedge-rate stats
fraudulent (paired with the Apr 10 2026 aggregation bug, that is how
accum_metrics showed +$39 while live booked -$85).

This module provides a minimal but defensible fill model: probability of
fill scales with price aggression relative to the spread and with elapsed
rest time. No-fill is a real outcome. Orders that never fill stay resting
so the state machine handles the same code paths it would handle in live.

The model is deterministic when seeded: tests pass a seeded `random.Random`
so cumulative probabilities are auditable.

Gated by POLYPHEMUS_DRY_RUN_V2. While the flag is off (default through
Phase 2), callers should preserve the legacy taker-optimistic path so the
rollout is reversible. The plan's feature-flag section lists this env var
explicitly.
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class MakerFillDecision:
    """Outcome of a single maker-fill evaluation.

    `reason` is a debug tag used by logs and tests to distinguish
    buried/crossed/prob_miss/prob_hit paths. When `filled=False`, price
    and qty are 0.0 so callers cannot accidentally double-count.
    """
    filled: bool
    fill_price: float
    fill_qty: float
    reason: str


class MakerFillModel:
    """Conservative maker-fill simulator for dry-run mode.

    Per-second fill rate scales linearly from BASE_RATE_PER_SEC at the top
    of the book's bid (effectively behind the entire queue) to
    BASE_RATE_PER_SEC + RATE_SPAN at the ask (effectively crossing). Over
    `elapsed_secs`, cumulative probability is clamped to MAX_CUMULATIVE so
    no order deterministically fills just by waiting.

    Numbers chosen so a typical $0.50 pair on Polymarket:
      - at best_bid ($0.49 / $0.51) → 3% per sec, ~45% over 20s
      - at mid ($0.50)              → 16.5% per sec, ~97% over 20s (cap 95)
      - at ask ($0.51)              → 30% per sec, capped at 95%

    These are intentionally more generous than real queue positioning; the
    aim is "not fraudulent," not "realistic." Realistic simulation is
    deferred to Phase 4's event-driven backtester with real book replay.
    """

    BASE_RATE_PER_SEC = 0.03
    RATE_SPAN = 0.27
    MAX_CUMULATIVE = 0.95

    def __init__(self, rng: random.Random | None = None):
        self._rng = rng or random.Random()

    def evaluate(
        self,
        our_price: float,
        best_bid: float,
        best_ask: float,
        qty: float,
        elapsed_secs: float,
    ) -> MakerFillDecision:
        if our_price < best_bid:
            return MakerFillDecision(False, 0.0, 0.0, "buried")
        spread = best_ask - best_bid
        if spread <= 0:
            return MakerFillDecision(False, 0.0, 0.0, "crossed_book")
        elapsed = max(0.0, float(elapsed_secs))
        aggression = min(1.0, max(0.0, (our_price - best_bid) / spread))
        per_sec_rate = self.BASE_RATE_PER_SEC + aggression * self.RATE_SPAN
        p_fill = min(self.MAX_CUMULATIVE, 1.0 - (1.0 - per_sec_rate) ** elapsed)
        if self._rng.random() > p_fill:
            return MakerFillDecision(False, 0.0, 0.0, "prob_miss")
        return MakerFillDecision(True, our_price, qty, "prob_hit")


def dry_run_v2_enabled() -> bool:
    """True when POLYPHEMUS_DRY_RUN_V2 is set to 1/true/yes (case-insensitive).

    Default unset → False → legacy taker-optimistic behavior preserved.
    Flip to true to opt a dry-run session into the probabilistic model.
    """
    return os.environ.get("POLYPHEMUS_DRY_RUN_V2", "").strip().lower() in ("1", "true", "yes")
