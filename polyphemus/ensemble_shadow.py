"""Shadow-only BTC 5m ranked strategy comparison.

Tracks a simple ensemble profile alongside the current guarded BTC momentum
profile so signals can be compared head-to-head without changing live orders.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class EnsembleShadowVerdict:
    current_guarded_hit: bool
    ensemble_candidate: bool
    ensemble_selected: bool
    score: float
    reason: str

    def as_signal_updates(self) -> dict:
        return {
            "shadow_current_guarded": 1 if self.current_guarded_hit else 0,
            "shadow_ensemble_candidate": 1 if self.ensemble_candidate else 0,
            "shadow_ensemble_selected": 1 if self.ensemble_selected else 0,
            "shadow_ensemble_score": self.score,
            "shadow_ensemble_reason": self.reason,
        }


class BTC5MEnsembleShadow:
    """Compare a ranked BTC 5m shadow strategy against the current guard slice."""

    def __init__(self, logger=None):
        self._logger = logger
        self._best_by_slug: dict[str, tuple[int, float]] = {}

    def _directionality(self, signal: dict) -> float:
        vol = signal.get("volatility_1h")
        trend = signal.get("trend_1h")
        if vol in (None, 0) or trend is None:
            return 0.0
        return abs(float(trend)) / float(vol)

    def _aligned_book(self, signal: dict) -> bool:
        imbalance = signal.get("book_imbalance")
        if imbalance is None:
            return False
        if signal.get("outcome") == "Up":
            return float(imbalance) >= 0.53
        return float(imbalance) <= 0.47

    def _aligned_taker(self, signal: dict) -> bool:
        delta = signal.get("taker_delta")
        if delta is None:
            return False
        if signal.get("outcome") == "Up":
            return float(delta) > 0
        return float(delta) < 0

    def current_guarded_hit(self, signal: dict) -> bool:
        return (
            signal.get("source") == "binance_momentum"
            and float(signal.get("price") or 0.0) <= 0.80
            and int(signal.get("time_remaining_secs") or 0) >= 210
        )

    def _score(self, signal: dict) -> float:
        price = float(signal.get("price") or 0.0)
        momentum_pct = abs(float(signal.get("momentum_pct") or 0.0))
        time_remaining = int(signal.get("time_remaining_secs") or 0)
        score = max(0.0, 1.0 - price) * 2.2
        score += min(momentum_pct, 0.01) * 45.0
        score += min(time_remaining, 300) / 300.0
        score += min(self._directionality(signal), 1.0) * 0.8
        if self._aligned_book(signal):
            score += 0.35
        if self._aligned_taker(signal):
            score += 0.25
        if signal.get("regime") in {"trending", "volatile"}:
            score += 0.15
        if signal.get("source") == "resolution_snipe":
            score -= 0.30
        return round(score, 6)

    def ensemble_candidate(self, signal: dict) -> bool:
        return (
            signal.get("source") in {"binance_momentum", "window_delta", "resolution_snipe"}
            and float(signal.get("price") or 0.0) <= 0.82
            and int(signal.get("time_remaining_secs") or 0) >= 60
        )

    def evaluate(self, signal: dict, signal_id: int, signal_logger=None) -> EnsembleShadowVerdict | None:
        if signal.get("asset") != "BTC" or int(signal.get("market_window_secs") or 0) != 300:
            return None

        current_guarded = self.current_guarded_hit(signal)
        ensemble_candidate = self.ensemble_candidate(signal)
        score = self._score(signal) if ensemble_candidate else 0.0
        slug = signal.get("slug", "")
        selected = False
        reason = "not_eligible"

        if ensemble_candidate and slug and signal_id > 0:
            best = self._best_by_slug.get(slug)
            if best is None or score > best[1]:
                if best and signal_logger and hasattr(signal_logger, "update_signal"):
                    signal_logger.update_signal(best[0], {"shadow_ensemble_selected": 0})
                self._best_by_slug[slug] = (signal_id, score)
                selected = True
                reason = f"ranked_best score={score:.3f}"
            else:
                reason = f"ranked_out score={score:.3f}<best={best[1]:.3f}"
        elif current_guarded:
            reason = "current_guarded_only"

        verdict = EnsembleShadowVerdict(
            current_guarded_hit=current_guarded,
            ensemble_candidate=ensemble_candidate,
            ensemble_selected=selected,
            score=score,
            reason=reason,
        )
        if signal_logger and hasattr(signal_logger, "update_signal") and signal_id > 0:
            signal_logger.update_signal(signal_id, verdict.as_signal_updates())
        if self._logger and ensemble_candidate:
            self._logger.info(
                "BTC5m ensemble shadow: %s | %s | current_guarded=%s selected=%s score=%.3f",
                slug or "unknown",
                signal.get("source", "unknown"),
                current_guarded,
                selected,
                score,
            )
        return verdict
