"""Alpha-decay tracker — compare recent window Sharpe to prior window.

A strategy's edge is a moving target. A +2.0 Sharpe in January that has
decayed to +0.3 by April is still nominally profitable but is decaying
fast enough that the next month will likely wipe the remaining edge.
This module catches that drift BEFORE the edge turns negative.

The check: compute Sharpe over the most recent `window_days`, compare to
the Sharpe over the prior `window_days` of equal length, and flag DECAY
if the drop exceeds `drop_threshold` (default 0.5). Paired with the MTC
pre-deploy gate this is the tripwire that stops a strategy from going
live on old evidence.

All functions are pure — no DB access, no side effects. Caller pulls the
(timestamp, return) stream from wherever (cycles table, trades table, a
backtest output) and passes it in.
"""

from __future__ import annotations

import math
from typing import Optional


SECS_PER_DAY = 86400.0


def _sharpe(returns: list[float]) -> Optional[float]:
    """Sample Sharpe ratio. Returns None on fewer than 2 samples or zero variance."""
    n = len(returns)
    if n < 2:
        return None
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    if var <= 0.0:
        return None
    return mean / math.sqrt(var)


def rolling_sharpe(
    timestamps: list[float],
    returns: list[float],
    window_days: int = 7,
    now: Optional[float] = None,
) -> dict:
    """Sharpe ratio over the trailing `window_days` ending at `now`.

    Args:
        timestamps: per-trade timestamps (Unix epoch seconds, ascending ok
            or unsorted — the function filters by cutoff and does not
            require order).
        returns: per-trade returns, parallel to `timestamps`. Mixing units
            is the caller's responsibility; the function only cares about
            the relative distribution.
        window_days: lookback window length in days.
        now: reference time (Unix epoch seconds). Defaults to the max
            timestamp in the input so offline analysis works on historical
            data without fighting the wall clock.

    Returns:
        dict with sharpe (float or None), n (int), window_days,
        cutoff_start (Unix seconds), cutoff_end (Unix seconds).
    """
    if len(timestamps) != len(returns):
        raise ValueError(
            f"timestamps and returns must have equal length; got "
            f"{len(timestamps)} vs {len(returns)}"
        )
    if window_days <= 0:
        raise ValueError(f"window_days must be positive, got {window_days}")

    if not timestamps:
        return {
            "sharpe": None,
            "n": 0,
            "window_days": window_days,
            "cutoff_start": 0.0,
            "cutoff_end": 0.0,
        }

    end = now if now is not None else max(timestamps)
    start = end - window_days * SECS_PER_DAY
    windowed = [r for t, r in zip(timestamps, returns) if start <= t <= end]

    return {
        "sharpe": _sharpe(windowed),
        "n": len(windowed),
        "window_days": window_days,
        "cutoff_start": start,
        "cutoff_end": end,
    }


def alpha_decay_check(
    timestamps: list[float],
    returns: list[float],
    window_days: int = 7,
    drop_threshold: float = 0.5,
    now: Optional[float] = None,
) -> dict:
    """Compare the most recent `window_days` of Sharpe to the prior window of equal length.

    Args:
        timestamps: per-trade timestamps (Unix epoch seconds).
        returns: per-trade returns parallel to `timestamps`.
        window_days: length of each comparison window in days.
        drop_threshold: Sharpe drop that triggers the DECAY flag. Per plan
            this is 0.5 — a half-unit Sharpe drop between back-to-back windows
            is already large enough that the next window often crosses zero.
        now: reference time (Unix epoch seconds). Defaults to max timestamp.

    Returns:
        dict with:
            current_sharpe: Sharpe over [now - window_days, now]
            prior_sharpe: Sharpe over [now - 2*window_days, now - window_days]
            delta: prior_sharpe - current_sharpe (positive = decay)
            decayed: True iff both Sharpes defined and delta > drop_threshold
            current_n: sample count in the current window
            prior_n: sample count in the prior window
            interpretation: plain-English summary
            window_days, drop_threshold: echoed inputs for telemetry.

        Sharpes are None when their window has < 2 samples or zero variance;
        in that case `decayed` is False (we don't flag on missing data —
        the gate layer is responsible for enforcing minimum-sample rules).
    """
    if len(timestamps) != len(returns):
        raise ValueError(
            f"timestamps and returns must have equal length; got "
            f"{len(timestamps)} vs {len(returns)}"
        )
    if window_days <= 0:
        raise ValueError(f"window_days must be positive, got {window_days}")
    if drop_threshold < 0:
        raise ValueError(f"drop_threshold must be non-negative, got {drop_threshold}")

    end = now if now is not None else (max(timestamps) if timestamps else 0.0)
    cur_start = end - window_days * SECS_PER_DAY
    prior_start = cur_start - window_days * SECS_PER_DAY

    current = [r for t, r in zip(timestamps, returns) if cur_start <= t <= end]
    prior = [r for t, r in zip(timestamps, returns) if prior_start <= t < cur_start]

    cur_sr = _sharpe(current)
    prior_sr = _sharpe(prior)

    delta: Optional[float]
    decayed: bool
    if cur_sr is None or prior_sr is None:
        delta = None
        decayed = False
        if prior_sr is None and cur_sr is None:
            interp = (
                f"Insufficient data in both windows (need >= 2 trades with non-zero "
                f"variance each over {window_days}d). Gate should treat this as "
                f"inconclusive, not passing."
            )
        elif prior_sr is None:
            interp = (
                f"Prior {window_days}d window has insufficient data "
                f"(n={len(prior)}). No baseline to decay against."
            )
        else:
            interp = (
                f"Current {window_days}d window has insufficient data "
                f"(n={len(current)}). Cannot assess decay."
            )
    else:
        delta = prior_sr - cur_sr
        decayed = delta > drop_threshold
        interp = (
            f"Sharpe {prior_sr:.2f} -> {cur_sr:.2f} over the last two {window_days}d "
            f"windows (delta={delta:+.2f}). "
            f"{'DECAYED: drop exceeds threshold.' if decayed else 'Stable within threshold.'}"
        )

    return {
        "current_sharpe": cur_sr,
        "prior_sharpe": prior_sr,
        "delta": delta,
        "decayed": decayed,
        "current_n": len(current),
        "prior_n": len(prior),
        "window_days": window_days,
        "drop_threshold": drop_threshold,
        "interpretation": interp,
    }


def alpha_decay_panel(
    timestamps: list[float],
    returns: list[float],
    windows_days: tuple[int, ...] = (7, 14, 30),
    drop_threshold: float = 0.5,
    now: Optional[float] = None,
) -> dict:
    """Run alpha_decay_check over the 7/14/30-day panel the plan calls for.

    Returns a dict keyed by window_days, each value the full alpha_decay_check
    output. `any_decayed` is True if ANY window trips the decay flag — the
    gate layer treats that as a blocker regardless of which horizon fired.
    """
    per_window = {
        w: alpha_decay_check(timestamps, returns, window_days=w,
                             drop_threshold=drop_threshold, now=now)
        for w in windows_days
    }
    return {
        "per_window": per_window,
        "any_decayed": any(v["decayed"] for v in per_window.values()),
        "drop_threshold": drop_threshold,
    }
