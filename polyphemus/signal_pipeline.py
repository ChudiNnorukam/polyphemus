"""Signal pipeline helpers for normalization, inspection, and logging.

Keeps the trading path explicit by normalizing raw feed payloads into a
canonical shape before guard/scoring/execution touch them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict

from .types import FilterResult, parse_window_from_slug


def _coerce_asset(asset: Any, slug: str) -> str:
    if asset:
        return str(asset).upper()
    if slug:
        return slug.split("-", 1)[0].upper()
    return ""


def _coerce_outcome(outcome: Any) -> str:
    value = str(outcome or "").strip().lower()
    if value == "up":
        return "Up"
    if value == "down":
        return "Down"
    return str(outcome or "").strip()


def _coerce_direction(direction: Any) -> str:
    value = str(direction or "").strip().upper()
    return value or "BUY"


def _coerce_source(raw_signal: Dict[str, Any]) -> str:
    source = raw_signal.get("source")
    if source:
        return str(source)
    metadata = raw_signal.get("metadata")
    if isinstance(metadata, dict) and metadata.get("source"):
        return str(metadata["source"])
    return "unknown"


def _coerce_price(raw_signal: Dict[str, Any]) -> float:
    for key in ("price", "midpoint", "entry_price"):
        value = raw_signal.get(key)
        if value not in (None, ""):
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _derive_market_window(raw_signal: Dict[str, Any], slug: str) -> int:
    explicit = raw_signal.get("market_window_secs")
    try:
        explicit_int = int(explicit)
    except (TypeError, ValueError):
        explicit_int = 0
    if explicit_int > 0:
        return explicit_int
    if slug:
        return parse_window_from_slug(slug)
    return 0


def _derive_time_remaining(raw_signal: Dict[str, Any], slug: str, market_window_secs: int, now_ts: float) -> tuple[int, list[str]]:
    noise_flags: list[str] = []
    value = raw_signal.get("time_remaining_secs")
    try:
        secs_remaining = int(value)
    except (TypeError, ValueError):
        secs_remaining = 0
    if secs_remaining <= 0 and slug:
        parts = slug.rsplit("-", 1)
        if len(parts) == 2 and parts[1].isdigit() and market_window_secs > 0:
            epoch = int(parts[1])
            secs_remaining = max(0, int(epoch + market_window_secs - now_ts))
            noise_flags.append("derived_time_remaining")
    if secs_remaining < 0:
        secs_remaining = 0
        noise_flags.append("clamped_negative_time_remaining")
    if market_window_secs > 0 and secs_remaining > market_window_secs:
        secs_remaining = market_window_secs
        noise_flags.append("clamped_time_remaining")
    return secs_remaining, noise_flags


@dataclass
class NormalizedSignal:
    """Canonical signal envelope used inside the trading harness."""

    signal: Dict[str, Any]
    noise_flags: list[str] = field(default_factory=list)


def normalize_signal(raw_signal: Dict[str, Any], now_ts: float | None = None) -> NormalizedSignal:
    """Normalize a raw feed signal into a canonical inspection-friendly shape."""
    signal = dict(raw_signal or {})
    now_ts = now_ts if now_ts is not None else time.time()
    slug = str(signal.get("slug", "") or "")
    market_window_secs = _derive_market_window(signal, slug)
    noise_flags: list[str] = []
    if market_window_secs > 0 and not signal.get("market_window_secs"):
        noise_flags.append("derived_market_window")

    time_remaining_secs, time_noise = _derive_time_remaining(
        signal, slug, market_window_secs, now_ts
    )
    noise_flags.extend(time_noise)

    asset = _coerce_asset(signal.get("asset"), slug)
    if asset and not signal.get("asset"):
        noise_flags.append("derived_asset")

    price = _coerce_price(signal)
    if price <= 0:
        noise_flags.append("missing_price")

    signal["slug"] = slug
    signal["asset"] = asset
    signal["outcome"] = _coerce_outcome(signal.get("outcome"))
    signal["direction"] = _coerce_direction(signal.get("direction"))
    signal["source"] = _coerce_source(signal)
    signal["price"] = price
    signal["midpoint"] = price
    signal["market_window_secs"] = market_window_secs
    signal["time_remaining_secs"] = time_remaining_secs
    signal["timestamp"] = float(signal.get("timestamp") or now_ts)
    signal["metadata"] = dict(signal.get("metadata") or {})
    signal["noise_flags"] = sorted(set(noise_flags))
    return NormalizedSignal(signal=signal, noise_flags=signal["noise_flags"])


def build_signal_log_features(
    signal: Dict[str, Any],
    guard_result: FilterResult,
    *,
    market_context: Dict[str, Any] | None = None,
    regime=None,
    config_label: str = "",
    config_era: str = "",
    instance_name: str = "",
) -> Dict[str, Any]:
    """Build a schema-compatible signal logger payload from a normalized signal."""
    market_context = market_context or {}
    asset = signal.get("asset", "")
    features: Dict[str, Any] = {
        "slug": signal.get("slug", ""),
        "asset": asset,
        "direction": signal.get("outcome", ""),
        "token_id": signal.get("token_id", ""),
        "midpoint": signal.get("price", 0.0),
        "momentum_pct": signal.get("momentum_pct", 0.0),
        "market_window_secs": signal.get("market_window_secs", 0),
        "time_remaining_secs": signal.get("time_remaining_secs", 0),
        "guard_passed": 1 if guard_result.passed else 0,
        "guard_reasons": ",".join(guard_result.reasons) if guard_result.reasons else "",
        "source": signal.get("source", ""),
        "spread": signal.get("spread"),
        "book_depth_bid": signal.get("best_bid"),
        "book_depth_ask": signal.get("best_ask"),
        "book_imbalance": signal.get("book_imbalance"),
        "vpin_5m": signal.get("vpin_5m"),
        "taker_delta": signal.get("taker_delta"),
        "strategy_type": signal.get("strategy_type"),
        "pair_cost": signal.get("pair_cost"),
        "dry_run": 1 if signal.get("dry_run") else 0,
        "pipeline_stage": "guard",
        "pipeline_status": "passed" if guard_result.passed else "filtered",
        "pipeline_detail": ",".join(guard_result.reasons) if guard_result.reasons else "guard_passed",
        "noise_flags": ",".join(signal.get("noise_flags", [])),
        "config_label": config_label,
        "config_era": config_era,
        "instance_name": instance_name,
    }
    if regime is not None:
        features["regime"] = getattr(regime, "regime", "")
        features["volatility_1h"] = getattr(regime, "volatility_1h", None)
        features["trend_1h"] = getattr(regime, "trend_1h", None)
    if market_context:
        features["fear_greed"] = market_context.get("fear_greed")
        features["market_regime"] = market_context.get("market_regime", "")
        asset_ctx = market_context.get(asset, {}) if asset else {}
        features["oi_change_pct"] = asset_ctx.get("oi_change_pct")
        features["oi_trend"] = asset_ctx.get("oi_trend", "")
    return features


def build_entry_metadata(signal: Dict[str, Any], *, entry_binance_price: float = 0.0) -> Dict[str, Any]:
    """Build a consistent metadata payload for downstream tracking."""
    metadata = dict(signal.get("metadata") or {})
    metadata["direction"] = signal.get("outcome", "").lower()
    metadata["source"] = signal.get("source", "")
    metadata["asset"] = signal.get("asset", "")
    metadata["entry_price_at_signal"] = signal.get("price", 0.0)
    if entry_binance_price:
        metadata["entry_binance_price"] = entry_binance_price
    return metadata
