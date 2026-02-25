"""Economic calendar blackout windows.

Provides is_macro_blackout() to block trading during major US macro releases.
BTC moves 1-3% on FOMC/CPI/NFP — the latency arb thesis breaks down during
these events because the move is fundamental repricing, not a lag.

Events covered: FOMC (x8/year), CPI (x12), NFP (x12), PCE (x12).
All times are UTC. FOMC = statement release time. CPI/NFP/PCE = 13:30 UTC (8:30am ET).
"""

from datetime import datetime, timezone

UTC = timezone.utc

MACRO_EVENTS_2026 = [
    # --- January 2026 ---
    datetime(2026, 1, 8,  13, 30, tzinfo=UTC),   # NFP
    datetime(2026, 1, 15, 13, 30, tzinfo=UTC),   # CPI
    datetime(2026, 1, 29, 13, 30, tzinfo=UTC),   # PCE
    datetime(2026, 1, 29, 19, 0,  tzinfo=UTC),   # FOMC

    # --- February 2026 ---
    datetime(2026, 2, 5,  13, 30, tzinfo=UTC),   # NFP
    datetime(2026, 2, 12, 13, 30, tzinfo=UTC),   # CPI
    datetime(2026, 2, 26, 13, 30, tzinfo=UTC),   # PCE

    # --- March 2026 ---
    datetime(2026, 3, 6,  13, 30, tzinfo=UTC),   # NFP
    datetime(2026, 3, 12, 13, 30, tzinfo=UTC),   # CPI
    datetime(2026, 3, 18, 18, 0,  tzinfo=UTC),   # FOMC
    datetime(2026, 3, 26, 13, 30, tzinfo=UTC),   # PCE

    # --- April 2026 ---
    datetime(2026, 4, 2,  13, 30, tzinfo=UTC),   # NFP
    datetime(2026, 4, 10, 13, 30, tzinfo=UTC),   # CPI
    datetime(2026, 4, 30, 13, 30, tzinfo=UTC),   # PCE

    # --- May 2026 ---
    datetime(2026, 5, 7,  13, 30, tzinfo=UTC),   # NFP
    datetime(2026, 5, 7,  18, 0,  tzinfo=UTC),   # FOMC
    datetime(2026, 5, 13, 13, 30, tzinfo=UTC),   # CPI
    datetime(2026, 5, 28, 13, 30, tzinfo=UTC),   # PCE

    # --- June 2026 ---
    datetime(2026, 6, 5,  13, 30, tzinfo=UTC),   # NFP
    datetime(2026, 6, 11, 13, 30, tzinfo=UTC),   # CPI
    datetime(2026, 6, 17, 18, 0,  tzinfo=UTC),   # FOMC
    datetime(2026, 6, 25, 13, 30, tzinfo=UTC),   # PCE

    # --- July 2026 ---
    datetime(2026, 7, 2,  13, 30, tzinfo=UTC),   # NFP
    datetime(2026, 7, 15, 13, 30, tzinfo=UTC),   # CPI
    datetime(2026, 7, 29, 18, 0,  tzinfo=UTC),   # FOMC
    datetime(2026, 7, 30, 13, 30, tzinfo=UTC),   # PCE

    # --- August 2026 ---
    datetime(2026, 8, 7,  13, 30, tzinfo=UTC),   # NFP
    datetime(2026, 8, 12, 13, 30, tzinfo=UTC),   # CPI
    datetime(2026, 8, 27, 13, 30, tzinfo=UTC),   # PCE

    # --- September 2026 ---
    datetime(2026, 9, 4,  13, 30, tzinfo=UTC),   # NFP
    datetime(2026, 9, 10, 13, 30, tzinfo=UTC),   # CPI
    datetime(2026, 9, 16, 18, 0,  tzinfo=UTC),   # FOMC
    datetime(2026, 9, 24, 13, 30, tzinfo=UTC),   # PCE

    # --- October 2026 ---
    datetime(2026, 10, 2,  13, 30, tzinfo=UTC),  # NFP
    datetime(2026, 10, 14, 13, 30, tzinfo=UTC),  # CPI
    datetime(2026, 10, 29, 13, 30, tzinfo=UTC),  # PCE

    # --- November 2026 ---
    datetime(2026, 11, 6,  13, 30, tzinfo=UTC),  # NFP
    datetime(2026, 11, 5,  18, 0,  tzinfo=UTC),  # FOMC (estimated)
    datetime(2026, 11, 12, 13, 30, tzinfo=UTC),  # CPI
    datetime(2026, 11, 25, 13, 30, tzinfo=UTC),  # PCE

    # --- December 2026 ---
    datetime(2026, 12, 4,  13, 30, tzinfo=UTC),  # NFP
    datetime(2026, 12, 10, 13, 30, tzinfo=UTC),  # CPI
    datetime(2026, 12, 16, 19, 0,  tzinfo=UTC),  # FOMC
    datetime(2026, 12, 22, 13, 30, tzinfo=UTC),  # PCE
]


def is_macro_blackout(window_mins: int = 45) -> bool:
    """Return True if current time is within window_mins of a major macro event.

    Args:
        window_mins: Minutes before/after event to blackout. Default 45.

    Returns:
        True if currently in a blackout window.
    """
    now = datetime.now(UTC)
    window_secs = window_mins * 60
    return any(
        abs((now - event).total_seconds()) <= window_secs
        for event in MACRO_EVENTS_2026
    )


def next_macro_event() -> tuple:
    """Return (event_time, secs_until) for the next upcoming macro event."""
    now = datetime.now(UTC)
    future = [(e, (e - now).total_seconds()) for e in MACRO_EVENTS_2026 if e > now]
    if not future:
        return None, None
    future.sort(key=lambda x: x[1])
    return future[0]
