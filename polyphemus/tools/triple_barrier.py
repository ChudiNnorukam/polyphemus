"""Triple-barrier labeling per Lopez de Prado, AFML Ch. 3.

Labels a trade entry by which of three barriers is hit first:
    +1  take-profit barrier (upper for long, lower for short)
    -1  stop-loss barrier (lower for long, upper for short)
     0  time barrier (horizon reached before either price barrier)

This gives a supervised target for classifiers (is-this-trade-worth-taking)
and a ground-truth label for backtesters that want to score strategy output
against first-touch outcomes rather than arbitrary fixed-horizon returns.

All functions are pure. The caller provides a sorted-by-timestamp price
path and a trade entry; we return the label plus the barrier-hit metadata
for downstream attribution. No DB, no mocks, no wall clock.

Ties (upper and lower hit on the SAME price-path row) resolve toward the
profit barrier — favorable bias for scoring, consistent with AFML's rule
that the first price to touch wins. Same-timestamp hits in practice come
from aggregated ticks; preferring profit keeps the label generous rather
than pessimistic.

Reference implementation constraints:
    - Absolute price offsets (entry_price + pt_threshold, entry_price - sl_threshold).
      Prediction-market prices live in [0, 1] — relative % offsets break at the
      bounds, absolute offsets don't.
    - `side` is "long" (bet resolves YES) or "short" (bet resolves NO). Short
      flips the mapping: upper barrier becomes the stop-loss.
    - Path must be in ascending timestamp order; this is the caller's contract.
      We check it once up front (cheap) rather than sorting silently.
"""

from __future__ import annotations

from typing import Literal, Optional


Side = Literal["long", "short"]
Barrier = Literal["upper", "lower", "time"]


def apply_triple_barrier(
    entry_time: float,
    entry_price: float,
    price_path: list[tuple[float, float]],
    pt_threshold: float,
    sl_threshold: float,
    horizon_secs: float,
    side: Side = "long",
) -> dict:
    """Label one trade by first-touched barrier.

    Args:
        entry_time: entry timestamp (Unix epoch seconds).
        entry_price: entry price — anchor for the upper and lower barriers.
        price_path: list of (timestamp, price) tuples in ASCENDING timestamp
            order. Entries with timestamp <= entry_time are filtered out
            before labeling (a trade cannot be labeled by its own entry tick).
        pt_threshold: take-profit offset in price units. Must be > 0.
            Upper barrier = entry_price + pt_threshold (long side) or
            lower barrier = entry_price - pt_threshold (short side).
        sl_threshold: stop-loss offset in price units. Must be > 0.
            Lower barrier = entry_price - sl_threshold (long side) or
            upper barrier = entry_price + sl_threshold (short side).
        horizon_secs: maximum holding period. Must be > 0.
        side: "long" (profit on upward move) or "short" (profit on downward).

    Returns:
        dict with:
            label: int in {-1, 0, +1}
            barrier: "upper" | "lower" | "time"
            hit_time: float (Unix seconds)
            hit_price: float
            holding_secs: float
            upper_barrier: float (absolute price level)
            lower_barrier: float (absolute price level)
            time_barrier: float (Unix seconds)
            side: echoed input
    """
    if pt_threshold <= 0:
        raise ValueError(f"pt_threshold must be > 0, got {pt_threshold}")
    if sl_threshold <= 0:
        raise ValueError(f"sl_threshold must be > 0, got {sl_threshold}")
    if horizon_secs <= 0:
        raise ValueError(f"horizon_secs must be > 0, got {horizon_secs}")
    if side not in ("long", "short"):
        raise ValueError(f"side must be 'long' or 'short', got {side!r}")

    upper = entry_price + pt_threshold if side == "long" else entry_price + sl_threshold
    lower = entry_price - sl_threshold if side == "long" else entry_price - pt_threshold
    time_barrier = entry_time + horizon_secs

    # Long: upper = profit (+1), lower = loss (-1).
    # Short: upper = loss (-1), lower = profit (+1).
    upper_label = 1 if side == "long" else -1
    lower_label = -1 if side == "long" else 1

    def _base(barrier: Barrier, label: int, hit_t: float, hit_p: float) -> dict:
        return {
            "label": label,
            "barrier": barrier,
            "hit_time": hit_t,
            "hit_price": hit_p,
            "holding_secs": hit_t - entry_time,
            "upper_barrier": upper,
            "lower_barrier": lower,
            "time_barrier": time_barrier,
            "side": side,
        }

    prev_t: Optional[float] = None
    for t, p in price_path:
        if prev_t is not None and t < prev_t:
            raise ValueError(
                f"price_path must be sorted by timestamp ascending; "
                f"saw {t} after {prev_t}"
            )
        prev_t = t
        if t <= entry_time:
            continue
        if t > time_barrier:
            # Time barrier fired before we reached this tick.
            return _base("time", 0, time_barrier, entry_price)
        # Tie resolution: check upper first so a same-row dual hit labels
        # as profit. Fine for long (upper=profit); for short we still
        # check upper first to keep behavior deterministic — upper_label
        # encodes whether that's profit or loss for this side.
        if p >= upper:
            return _base("upper", upper_label, t, p)
        if p <= lower:
            return _base("lower", lower_label, t, p)

    # Walked the whole path without touching a price barrier. If the path
    # ends at or after the time barrier, label as time-out; if the path ends
    # BEFORE the time barrier, we have insufficient data — honesty dictates
    # still returning 0 (the trade was not closed by a price move in the
    # data we have) but the caller should treat truncated paths as less
    # trustworthy than full-horizon ones.
    return _base("time", 0, time_barrier, entry_price)


def label_events(
    events: list[dict],
    pt_threshold: float,
    sl_threshold: float,
    horizon_secs: float,
) -> list[dict]:
    """Apply triple-barrier labeling to a batch of events.

    Args:
        events: list of dicts, each with keys:
            entry_time, entry_price, price_path, side (optional, default "long").
        pt_threshold, sl_threshold, horizon_secs: shared barrier config.
            Pass per-event in a wrapper if you need variable thresholds.

    Returns:
        list of label dicts (output of apply_triple_barrier) in input order.
        Each dict carries the input event_id (if present) for join-back.
    """
    out = []
    for ev in events:
        label = apply_triple_barrier(
            entry_time=ev["entry_time"],
            entry_price=ev["entry_price"],
            price_path=ev["price_path"],
            pt_threshold=pt_threshold,
            sl_threshold=sl_threshold,
            horizon_secs=horizon_secs,
            side=ev.get("side", "long"),
        )
        if "event_id" in ev:
            label["event_id"] = ev["event_id"]
        out.append(label)
    return out


def label_summary(labels: list[dict]) -> dict:
    """Aggregate a batch of triple-barrier labels into counts + distribution.

    Thin convenience for Phase 2 diagnostics: the MTC gate wants to see the
    shape of labels (is the strategy mostly winning by price target or by
    time-barrier coin flip?) without having to re-derive counts at each site.
    """
    n = len(labels)
    if n == 0:
        return {
            "n": 0,
            "wins": 0,
            "losses": 0,
            "timeouts": 0,
            "win_rate": 0.0,
            "timeout_rate": 0.0,
            "barrier_counts": {"upper": 0, "lower": 0, "time": 0},
        }
    wins = sum(1 for L in labels if L["label"] == 1)
    losses = sum(1 for L in labels if L["label"] == -1)
    timeouts = sum(1 for L in labels if L["label"] == 0)
    bc = {"upper": 0, "lower": 0, "time": 0}
    for L in labels:
        bc[L["barrier"]] += 1
    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "timeouts": timeouts,
        "win_rate": wins / n,
        "timeout_rate": timeouts / n,
        "barrier_counts": bc,
    }
