"""Phase 1.5 — orderbook-confirmed force-close.

`performance_db.force_close_trade(slug, exit_reason, exit_price=0.0)` has
historically assumed a position with exit_price=0 is a total loss and
written `pnl = -(entry_price * entry_size)`. That is only correct when
the token actually has no recoverable value. If the bid side still has a
non-trivial `bid_size * bid_price`, the position could have been sold
for a partial recovery and the "total loss" record misstates P&L.

This module provides a pure helper the DB layer (and callers) can use to
check an orderbook snapshot before zeroing a trade out. Downstream:

  * `force_close_trade` accepts `book_snapshot` + `worthless_threshold`.
    When a caller passes them and the position is not confirmed worthless,
    the DB estimates exit via the bid mid-price and tags the exit_reason
    with an `_unconfirmed` suffix for operator follow-up.
  * Callers that omit the snapshot fall back to the legacy behavior —
    backward compatible.

Threshold default is $0.05 per the plan. Crossing that bar means the bid
side can absorb more than five cents of the position; anything less is
treated as dust.
"""

from __future__ import annotations

from typing import Optional


DEFAULT_WORTHLESS_THRESHOLD_USD = 0.05


def estimate_bid_value(book_snapshot: Optional[dict]) -> float:
    """Return bid_size * bid_price at the top of the book, or 0.0 if the
    snapshot is missing / malformed / empty. Used as the dust test for
    force-close confirmation.

    A CLOB book snapshot is expected to look like
      {"bids": [{"price": "0.42", "size": "100"}, ...], "asks": [...]}
    per py_clob_client conventions. Strings are coerced to float. A missing
    or unparseable bid returns 0.0 (safe: caller will treat as dust).
    """
    if not book_snapshot:
        return 0.0
    bids = book_snapshot.get("bids") or []
    if not bids:
        return 0.0
    top = bids[0]
    try:
        price = float(top.get("price") or 0.0)
        size = float(top.get("size") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if price <= 0 or size <= 0:
        return 0.0
    return price * size


def is_position_worthless(
    book_snapshot: Optional[dict],
    threshold_usd: float = DEFAULT_WORTHLESS_THRESHOLD_USD,
) -> tuple[bool, float]:
    """Decide whether a position can safely be written off as a total loss.

    Returns `(is_worthless, top_bid_value_usd)`:
      - `is_worthless=True` when bid_size * bid_price < threshold_usd.
        Callers should then pass `exit_price=0.0` into `force_close_trade`.
      - `is_worthless=False` when the bid side can absorb more than
        `threshold_usd` of proceeds. Callers should treat as unresolved
        and either retry the sell or mark the trade `_unconfirmed`.

    An empty, missing, or malformed snapshot returns `(True, 0.0)` —
    fail-worthless is the conservative choice when the book cannot be
    read, because it is the outcome that matches legacy behavior.
    """
    bid_value = estimate_bid_value(book_snapshot)
    if threshold_usd <= 0:
        return (bid_value <= 0.0, bid_value)
    return (bid_value < threshold_usd, bid_value)


def top_bid_price(book_snapshot: Optional[dict]) -> float:
    """Top-of-book bid price, 0.0 if unavailable. Used by `force_close_trade`
    to estimate a non-zero exit when the position is not worthless."""
    if not book_snapshot:
        return 0.0
    bids = book_snapshot.get("bids") or []
    if not bids:
        return 0.0
    try:
        return float(bids[0].get("price") or 0.0)
    except (TypeError, ValueError):
        return 0.0
