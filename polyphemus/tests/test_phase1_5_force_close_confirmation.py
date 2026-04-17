"""Phase 1.5 — orderbook-confirmed force-close.

Apr 10-class bug: `force_close_trade(slug, reason, exit_price=0)` has written
`pnl = -(entry_price * entry_size)` regardless of whether the position was
actually unrecoverable. If the CLOB bid side could have absorbed the inventory
for partial recovery, the total-loss record is a lie. With dry-run segregation
from Phase 0, a single optimistic live-side write like this still poisons
stats aggregation.

These tests pin both halves of the fix:
  - the pure helpers in `force_close_confirmation.py` behave as documented
    under every reasonable book shape (missing, malformed, dust, non-dust);
  - `PerformanceDB.force_close_trade()` honors the optional `book_snapshot`
    kwarg: confirmed dust keeps legacy zero-exit behavior, non-dust estimates
    via top bid and tags the exit_reason with '_unconfirmed' for operator
    follow-up.

Callers that omit `book_snapshot` MUST see exactly the legacy behavior — the
kwarg is opt-in so Phase 1.5 is a zero-risk rollout for existing call sites.
"""

from __future__ import annotations

import sqlite3
import time
import uuid

import pytest

from polyphemus.force_close_confirmation import (
    DEFAULT_WORTHLESS_THRESHOLD_USD,
    estimate_bid_value,
    is_position_worthless,
    top_bid_price,
)
from polyphemus.performance_db import PerformanceDB


def _unique_db(tmp_path, stem):
    return str(tmp_path / f"{stem}_{uuid.uuid4().hex[:8]}.db")


def _seed_open_trade(
    db_path: str,
    slug: str,
    *,
    entry_price: float,
    entry_size: float,
    is_dry_run: int = 1,
) -> None:
    """Insert a single open trade (exit_time NULL) so force_close_trade
    has something to close. Uses the current schema, so this test doubles
    as a migration regression."""
    conn = sqlite3.connect(db_path)
    try:
        now = time.time()
        conn.execute(
            """INSERT INTO trades (trade_id, token_id, slug, entry_time,
                entry_price, entry_size, exit_reason, is_dry_run)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                f"tid-{uuid.uuid4().hex[:8]}",
                f"0x{slug[:10]}",
                slug,
                now - 300,
                entry_price,
                entry_size,
                "",
                is_dry_run,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _fetch_closed(db_path: str, slug: str) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            """SELECT exit_price, exit_reason, pnl, pnl_pct, exit_tx_hash
                FROM trades WHERE slug = ?""",
            (slug,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise AssertionError(f"no trade row for {slug}")
    return {
        "exit_price": row[0],
        "exit_reason": row[1],
        "pnl": row[2],
        "pnl_pct": row[3],
        "exit_tx_hash": row[4],
    }


# --- helper purity -----------------------------------------------------------


class TestEstimateBidValue:
    """estimate_bid_value must never throw on weird books — worst case returns
    0.0 so the caller treats the position as dust and the legacy code path
    runs. Throwing would crash the sweeper mid-cleanup."""

    def test_none_returns_zero(self):
        assert estimate_bid_value(None) == 0.0

    def test_empty_dict_returns_zero(self):
        assert estimate_bid_value({}) == 0.0

    def test_missing_bids_returns_zero(self):
        assert estimate_bid_value({"asks": [{"price": "0.5", "size": "100"}]}) == 0.0

    def test_empty_bids_returns_zero(self):
        assert estimate_bid_value({"bids": []}) == 0.0

    def test_null_bids_returns_zero(self):
        assert estimate_bid_value({"bids": None}) == 0.0

    def test_valid_string_prices_coerce(self):
        book = {"bids": [{"price": "0.42", "size": "100"}]}
        assert estimate_bid_value(book) == pytest.approx(42.0)

    def test_valid_float_values(self):
        book = {"bids": [{"price": 0.10, "size": 5.5}]}
        assert estimate_bid_value(book) == pytest.approx(0.55)

    def test_malformed_price_returns_zero(self):
        book = {"bids": [{"price": "not-a-number", "size": "100"}]}
        assert estimate_bid_value(book) == 0.0

    def test_missing_size_returns_zero(self):
        book = {"bids": [{"price": "0.50"}]}
        assert estimate_bid_value(book) == 0.0

    def test_zero_price_returns_zero(self):
        book = {"bids": [{"price": "0.0", "size": "1000"}]}
        assert estimate_bid_value(book) == 0.0

    def test_zero_size_returns_zero(self):
        book = {"bids": [{"price": "0.50", "size": "0"}]}
        assert estimate_bid_value(book) == 0.0

    def test_top_of_book_only(self):
        """Deeper levels are ignored. If the top bid is dust, cycling through
        worse bids can't change the verdict: we'd have to eat the top tick
        first, and that already isn't worth it."""
        book = {"bids": [
            {"price": "0.01", "size": "1"},
            {"price": "0.50", "size": "1000"},
        ]}
        assert estimate_bid_value(book) == pytest.approx(0.01)


class TestIsPositionWorthless:
    """Returns (is_worthless, bid_value). Fail-worthless on missing book
    is the conservative choice: it preserves legacy behavior on callers
    that can't fetch a snapshot."""

    def test_none_book_is_worthless(self):
        result, value = is_position_worthless(None)
        assert result is True
        assert value == 0.0

    def test_empty_book_is_worthless(self):
        result, value = is_position_worthless({"bids": []})
        assert result is True
        assert value == 0.0

    def test_below_threshold_is_worthless(self):
        book = {"bids": [{"price": "0.001", "size": "10"}]}  # $0.01 < $0.05
        result, value = is_position_worthless(book)
        assert result is True
        assert value == pytest.approx(0.01)

    def test_above_threshold_is_not_worthless(self):
        book = {"bids": [{"price": "0.05", "size": "10"}]}  # $0.50 > $0.05
        result, value = is_position_worthless(book)
        assert result is False
        assert value == pytest.approx(0.50)

    def test_at_exact_threshold_is_not_worthless(self):
        """Strict less-than: bid value of exactly $0.05 is NOT worthless.
        Matches the '< threshold' in the helper — above-or-equal means the
        book can pay out, and we should record that, not a total loss."""
        book = {"bids": [{"price": "0.05", "size": "1"}]}  # $0.05 == threshold
        result, _ = is_position_worthless(book, threshold_usd=0.05)
        assert result is False

    def test_custom_threshold_raises_bar(self):
        book = {"bids": [{"price": "0.10", "size": "5"}]}  # $0.50
        assert is_position_worthless(book, threshold_usd=1.00)[0] is True
        assert is_position_worthless(book, threshold_usd=0.05)[0] is False

    def test_zero_threshold_only_true_zero_is_worthless(self):
        """Zero threshold: any positive bid value counts as recoverable.
        Only literal zero is worthless. Edge case for callers that want
        strict confirmation."""
        assert is_position_worthless({"bids": []}, threshold_usd=0.0)[0] is True
        book = {"bids": [{"price": "0.001", "size": "1"}]}
        assert is_position_worthless(book, threshold_usd=0.0)[0] is False

    def test_default_threshold_constant(self):
        """Constant documented in docstring matches implementation. If this
        changes, audit every caller's tolerance before bumping."""
        assert DEFAULT_WORTHLESS_THRESHOLD_USD == 0.05


class TestTopBidPrice:
    def test_none_returns_zero(self):
        assert top_bid_price(None) == 0.0

    def test_empty_bids_returns_zero(self):
        assert top_bid_price({"bids": []}) == 0.0

    def test_valid_string_coerces(self):
        assert top_bid_price({"bids": [{"price": "0.42", "size": "100"}]}) == pytest.approx(0.42)

    def test_malformed_returns_zero(self):
        assert top_bid_price({"bids": [{"price": "nope"}]}) == 0.0


# --- DB integration ----------------------------------------------------------


class TestForceCloseLegacyBehavior:
    """No book_snapshot arg → legacy total-loss write, identical to pre-
    Phase-1.5 behavior. Existing callers must not regress."""

    def test_no_book_zero_exit_is_total_loss(self, tmp_path):
        path = _unique_db(tmp_path, "force_legacy")
        db = PerformanceDB(db_path=path)
        _seed_open_trade(path, "leg-1", entry_price=0.60, entry_size=10.0)

        assert db.force_close_trade("leg-1", "market_resolved") is True

        row = _fetch_closed(path, "leg-1")
        assert row["exit_price"] == 0.0
        assert row["exit_reason"] == "market_resolved"
        assert row["pnl"] == pytest.approx(-(0.60 * 10.0))
        assert row["pnl_pct"] == pytest.approx(-1.0)
        assert row["exit_tx_hash"] == "force_closed"

    def test_no_book_nonzero_exit_uses_provided_price(self, tmp_path):
        """exit_price > 0 with no book: use provided price, unchanged from
        legacy. book_snapshot only kicks in when exit_price <= 0."""
        path = _unique_db(tmp_path, "force_legacy_known")
        db = PerformanceDB(db_path=path)
        _seed_open_trade(path, "leg-2", entry_price=0.50, entry_size=20.0)

        assert db.force_close_trade("leg-2", "sold", exit_price=0.45) is True

        row = _fetch_closed(path, "leg-2")
        assert row["exit_price"] == pytest.approx(0.45)
        assert row["exit_reason"] == "sold"
        assert row["pnl"] == pytest.approx((0.45 - 0.50) * 20.0)
        assert row["pnl_pct"] == pytest.approx((0.45 - 0.50) / 0.50)


class TestForceCloseConfirmedWorthless:
    """book_snapshot provided, bid side is dust: legacy total-loss behavior
    is preserved, exit_reason unchanged (we confirmed it's genuinely dust)."""

    def test_empty_book_confirms_worthless(self, tmp_path):
        path = _unique_db(tmp_path, "force_empty_book")
        db = PerformanceDB(db_path=path)
        _seed_open_trade(path, "wl-1", entry_price=0.70, entry_size=5.0)

        assert db.force_close_trade(
            "wl-1", "market_resolved", book_snapshot={"bids": []}
        ) is True

        row = _fetch_closed(path, "wl-1")
        assert row["exit_price"] == 0.0
        assert row["exit_reason"] == "market_resolved"  # no _unconfirmed suffix
        assert row["pnl"] == pytest.approx(-(0.70 * 5.0))

    def test_dust_bid_confirms_worthless(self, tmp_path):
        """Dust bid (bid_value < $0.05) → zero exit, no suffix."""
        path = _unique_db(tmp_path, "force_dust")
        db = PerformanceDB(db_path=path)
        _seed_open_trade(path, "wl-2", entry_price=0.55, entry_size=12.0)

        book = {"bids": [{"price": "0.001", "size": "40"}]}  # $0.04 < $0.05

        assert db.force_close_trade(
            "wl-2", "ghost_cleanup", book_snapshot=book
        ) is True

        row = _fetch_closed(path, "wl-2")
        assert row["exit_price"] == 0.0
        assert row["exit_reason"] == "ghost_cleanup"
        assert row["pnl"] == pytest.approx(-(0.55 * 12.0))


class TestForceCloseUnconfirmed:
    """Non-dust bid means the position had recoverable value and we
    can't claim total loss. Mid-bid estimate goes in, exit_reason gets
    '_unconfirmed' suffix so operators can audit."""

    def test_recoverable_bid_estimates_via_top_bid(self, tmp_path):
        path = _unique_db(tmp_path, "force_recoverable")
        db = PerformanceDB(db_path=path)
        _seed_open_trade(path, "uc-1", entry_price=0.60, entry_size=10.0)

        # $1.00 bid value, well above $0.05 threshold
        book = {"bids": [{"price": "0.50", "size": "2"}]}

        assert db.force_close_trade(
            "uc-1", "market_resolved", book_snapshot=book
        ) is True

        row = _fetch_closed(path, "uc-1")
        assert row["exit_price"] == pytest.approx(0.50)
        assert row["exit_reason"] == "market_resolved_unconfirmed"
        # pnl = (0.50 - 0.60) * 10 = -1.00, not -6.00
        assert row["pnl"] == pytest.approx(-1.00)
        assert row["pnl_pct"] == pytest.approx((0.50 - 0.60) / 0.60)

    def test_recoverable_bid_partial_win_not_total_loss(self, tmp_path):
        """Extreme case: entry $0.40, top bid $0.45 → position is a small
        winner, not a total loss. Legacy path would record -$4.00; with
        confirmation we record +$0.50."""
        path = _unique_db(tmp_path, "force_recov_win")
        db = PerformanceDB(db_path=path)
        _seed_open_trade(path, "uc-2", entry_price=0.40, entry_size=10.0)

        book = {"bids": [{"price": "0.45", "size": "20"}]}  # $9.00 recoverable

        assert db.force_close_trade(
            "uc-2", "forced_exit", book_snapshot=book
        ) is True

        row = _fetch_closed(path, "uc-2")
        assert row["exit_price"] == pytest.approx(0.45)
        assert row["exit_reason"] == "forced_exit_unconfirmed"
        assert row["pnl"] == pytest.approx((0.45 - 0.40) * 10.0)  # +$0.50

    def test_custom_threshold_lowers_bar(self, tmp_path):
        """Threshold is a caller knob. A tighter threshold catches smaller
        recoverable bids that the default would miss."""
        path = _unique_db(tmp_path, "force_threshold")
        db = PerformanceDB(db_path=path)
        _seed_open_trade(path, "uc-3", entry_price=0.30, entry_size=5.0)

        # $0.02 bid — under $0.05 default but over $0.01 custom
        book = {"bids": [{"price": "0.002", "size": "10"}]}

        assert db.force_close_trade(
            "uc-3", "x", book_snapshot=book, worthless_threshold=0.01
        ) is True

        row = _fetch_closed(path, "uc-3")
        assert row["exit_price"] == pytest.approx(0.002)
        assert row["exit_reason"] == "x_unconfirmed"

    def test_explicit_exit_price_overrides_book(self, tmp_path):
        """If caller already knows the exit (passed in >0), book_snapshot
        is ignored. Protects the sellback path where we have a real fill."""
        path = _unique_db(tmp_path, "force_explicit")
        db = PerformanceDB(db_path=path)
        _seed_open_trade(path, "uc-4", entry_price=0.50, entry_size=8.0)

        book = {"bids": [{"price": "0.30", "size": "20"}]}

        assert db.force_close_trade(
            "uc-4", "sold", exit_price=0.48, book_snapshot=book
        ) is True

        row = _fetch_closed(path, "uc-4")
        assert row["exit_price"] == pytest.approx(0.48)
        assert row["exit_reason"] == "sold"  # no suffix — already confirmed
        assert row["pnl"] == pytest.approx((0.48 - 0.50) * 8.0)
