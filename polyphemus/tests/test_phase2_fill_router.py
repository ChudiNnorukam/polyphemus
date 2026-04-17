"""Phase 2 tests for the unified dry-run fill router.

The router is the single entry point for dry-run fill decisions. Before
Phase 2 the V2 probabilistic model was wired only into accumulator.py;
this module collapses the V1-vs-V2 branching into one call so every
dry-run entry path populates the v4 observability columns uniformly.

Coverage focuses on the guarantees every caller depends on:

  1. V1 (flag off) always returns filled=True with fill_model="v1_taker"
     so the legacy instant-fill behavior is preserved.
  2. V2 (flag on) routes to MakerFillModel and surfaces the reason tag
     (prob_hit / prob_miss / crossed_book / buried).
  3. V2 falls back to V1 when book data is absent — callers at module
     boot (before the WS feed warms up) should not blow up.
  4. book_spread_at_decision reflects actual (ask - bid), not metadata.
  5. elapsed_secs + depth are passed through unchanged for attribution.
"""

import random

import pytest

from polyphemus.dry_run_fill_model import MakerFillModel
from polyphemus.fill_router import FillRecord, route_dry_run_fill


@pytest.fixture
def v2_off(monkeypatch):
    """Ensure POLYPHEMUS_DRY_RUN_V2 is unset for legacy-path tests."""
    monkeypatch.delenv("POLYPHEMUS_DRY_RUN_V2", raising=False)


@pytest.fixture
def v2_on(monkeypatch):
    """Enable the V2 probabilistic fill model for these tests."""
    monkeypatch.setenv("POLYPHEMUS_DRY_RUN_V2", "true")


class TestV1LegacyPath:
    def test_v1_always_fills_instantly(self, v2_off):
        record = route_dry_run_fill(
            our_price=0.55, best_bid=0.54, best_ask=0.56, qty=10.0,
            elapsed_secs=0.0,
        )
        assert record.filled is True
        assert record.fill_price == 0.55
        assert record.fill_qty == 10.0
        assert record.fill_model == "v1_taker"
        assert record.fill_model_reason == "v1_instant"

    def test_v1_populates_book_state_anyway(self, v2_off):
        """v4 columns must be uniform regardless of fill model choice."""
        record = route_dry_run_fill(
            our_price=0.55, best_bid=0.54, best_ask=0.56, qty=10.0,
            book_depth_bid=1000.0, book_depth_ask=800.0, elapsed_secs=2.5,
        )
        assert record.book_spread_at_decision == pytest.approx(0.02)
        assert record.book_depth_bid == 1000.0
        assert record.book_depth_ask == 800.0
        assert record.elapsed_secs_at_fill == 2.5

    def test_v1_handles_missing_book(self, v2_off):
        """Best-bid/ask zero = no WS data yet. Should still instant-fill."""
        record = route_dry_run_fill(
            our_price=0.55, best_bid=0.0, best_ask=0.0, qty=10.0,
        )
        assert record.filled is True
        assert record.fill_model == "v1_taker"
        assert record.book_spread_at_decision == 0.0


class TestV2ProbabilisticPath:
    def test_v2_flag_routes_to_maker_model(self, v2_on):
        """With a seeded RNG at aggression=1.0, 20s = near-certain fill."""
        model = MakerFillModel(rng=random.Random(42))
        record = route_dry_run_fill(
            our_price=0.56, best_bid=0.54, best_ask=0.56, qty=10.0,
            elapsed_secs=20.0, model=model,
        )
        assert record.fill_model == "v2_probabilistic"
        assert record.fill_model_reason == "prob_hit"
        assert record.filled is True

    def test_v2_buried_order_does_not_fill(self, v2_on):
        """Price below bid = behind the queue = no fill ever."""
        record = route_dry_run_fill(
            our_price=0.50, best_bid=0.54, best_ask=0.56, qty=10.0,
            elapsed_secs=30.0,
        )
        assert record.fill_model == "v2_probabilistic"
        assert record.fill_model_reason == "buried"
        assert record.filled is False
        assert record.fill_price == 0.0

    def test_v2_crossed_book_does_not_fill(self, v2_on):
        """Inverted book (bid >= ask) is a data error; refuse to fill.

        Our_price must stay >= best_bid or MakerFillModel short-circuits to
        "buried" before reaching the spread check.
        """
        record = route_dry_run_fill(
            our_price=0.57, best_bid=0.56, best_ask=0.54, qty=10.0,
        )
        assert record.fill_model_reason == "crossed_book"
        assert record.filled is False

    def test_v2_prob_miss_is_not_buried(self, v2_on):
        """Seeded RNG that rolls above p_fill must yield prob_miss (not buried).

        At aggression=0.5, elapsed=1s, p_fill≈0.165. ``random.Random(0)`` rolls
        0.844 on first call — well above p_fill — so the model returns
        ``prob_miss`` (distinct from ``buried`` which requires price<bid).
        """
        model = MakerFillModel(rng=random.Random(0))
        record = route_dry_run_fill(
            our_price=0.55, best_bid=0.54, best_ask=0.56, qty=10.0,
            elapsed_secs=1.0, model=model,
        )
        assert record.fill_model == "v2_probabilistic"
        assert record.fill_model_reason == "prob_miss"
        assert record.filled is False

    def test_v2_falls_back_to_v1_when_book_missing(self, v2_on):
        """Flag on but no book state → V1 instant fill (not a permanent miss).

        A signal_bot cycle can fire before the WS feed warms; we can't
        refuse to fill just because the book snapshot is stale.
        """
        record = route_dry_run_fill(
            our_price=0.55, best_bid=0.0, best_ask=0.0, qty=10.0,
        )
        assert record.fill_model == "v1_taker"
        assert record.filled is True


class TestRecordShape:
    def test_record_is_frozen_dataclass(self, v2_off):
        record = route_dry_run_fill(
            our_price=0.55, best_bid=0.54, best_ask=0.56, qty=10.0,
        )
        with pytest.raises((AttributeError, Exception)):
            record.filled = False  # type: ignore[misc]

    def test_record_has_all_v4_fields(self, v2_off):
        """Every field the v4 schema needs must be present — callers pass
        these straight to record_entry without extra glue.
        """
        record = route_dry_run_fill(
            our_price=0.55, best_bid=0.54, best_ask=0.56, qty=10.0,
        )
        assert isinstance(record, FillRecord)
        # Spot-check the observability fields.
        for attr in (
            "fill_model", "fill_model_reason", "book_spread_at_decision",
            "book_depth_bid", "book_depth_ask", "elapsed_secs_at_fill",
        ):
            assert hasattr(record, attr), f"FillRecord missing v4 field: {attr}"
