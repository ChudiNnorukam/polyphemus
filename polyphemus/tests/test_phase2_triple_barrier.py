"""Phase 2 tests for triple-barrier labeling (Lopez de Prado, AFML Ch. 3).

Pinned reference cases (from the algorithm specification, not from a library):
  - Monotonic upward path crossing upper barrier -> label +1, barrier "upper"
  - Monotonic downward path crossing lower barrier -> label -1, barrier "lower"
  - Flat path that never touches either barrier -> label 0, barrier "time"
  - Path that crosses lower then upper -> label -1 (first touch wins)
  - Short-side mapping flips the labels

The algorithm is simple enough that these should be exact, not approximate.
"""

import pytest

from polyphemus.tools.triple_barrier import (
    apply_triple_barrier,
    label_events,
    label_summary,
)


class TestTripleBarrierLongSide:
    def test_hits_upper_barrier_returns_plus_one(self):
        r = apply_triple_barrier(
            entry_time=0, entry_price=0.50,
            price_path=[(1, 0.52), (2, 0.56), (3, 0.60)],
            pt_threshold=0.05, sl_threshold=0.05,
            horizon_secs=10, side="long",
        )
        assert r["label"] == 1
        assert r["barrier"] == "upper"
        assert r["hit_time"] == 2
        assert r["hit_price"] == 0.56

    def test_hits_lower_barrier_returns_minus_one(self):
        r = apply_triple_barrier(
            entry_time=0, entry_price=0.50,
            price_path=[(1, 0.48), (2, 0.44), (3, 0.40)],
            pt_threshold=0.05, sl_threshold=0.05,
            horizon_secs=10, side="long",
        )
        assert r["label"] == -1
        assert r["barrier"] == "lower"
        assert r["hit_time"] == 2
        assert r["hit_price"] == 0.44

    def test_time_barrier_returns_zero(self):
        r = apply_triple_barrier(
            entry_time=0, entry_price=0.50,
            # Flat path, never hits either barrier
            price_path=[(1, 0.51), (2, 0.49), (3, 0.50), (4, 0.51)],
            pt_threshold=0.10, sl_threshold=0.10,
            horizon_secs=4, side="long",
        )
        assert r["label"] == 0
        assert r["barrier"] == "time"

    def test_first_touch_wins_lower_then_upper(self):
        """Path dips to stop-loss first, then rises past take-profit.
        Only the first touch counts."""
        r = apply_triple_barrier(
            entry_time=0, entry_price=0.50,
            price_path=[(1, 0.51), (2, 0.44), (3, 0.48), (4, 0.56)],
            pt_threshold=0.05, sl_threshold=0.05,
            horizon_secs=10, side="long",
        )
        assert r["label"] == -1
        assert r["barrier"] == "lower"
        assert r["hit_time"] == 2

    def test_empty_path_times_out(self):
        r = apply_triple_barrier(
            entry_time=0, entry_price=0.50,
            price_path=[],
            pt_threshold=0.05, sl_threshold=0.05,
            horizon_secs=10, side="long",
        )
        assert r["label"] == 0
        assert r["barrier"] == "time"

    def test_tick_at_or_before_entry_is_skipped(self):
        """Entry tick itself cannot label the trade."""
        r = apply_triple_barrier(
            entry_time=5, entry_price=0.50,
            price_path=[(5, 0.60), (6, 0.52)],  # First tick at entry_time, not usable
            pt_threshold=0.05, sl_threshold=0.05,
            horizon_secs=10, side="long",
        )
        # 0.52 does not cross upper (0.55 needed), so time barrier fires.
        assert r["label"] == 0
        assert r["barrier"] == "time"

    def test_barrier_levels_are_reported(self):
        r = apply_triple_barrier(
            entry_time=0, entry_price=0.50,
            price_path=[(1, 0.50)],
            pt_threshold=0.08, sl_threshold=0.03,
            horizon_secs=10, side="long",
        )
        assert r["upper_barrier"] == pytest.approx(0.58)
        assert r["lower_barrier"] == pytest.approx(0.47)
        assert r["time_barrier"] == 10


class TestTripleBarrierShortSide:
    def test_short_upper_is_loss(self):
        r = apply_triple_barrier(
            entry_time=0, entry_price=0.50,
            price_path=[(1, 0.56)],
            pt_threshold=0.05, sl_threshold=0.05,
            horizon_secs=10, side="short",
        )
        # Upper barrier for short = stop-loss = label -1
        assert r["label"] == -1
        assert r["barrier"] == "upper"

    def test_short_lower_is_profit(self):
        r = apply_triple_barrier(
            entry_time=0, entry_price=0.50,
            price_path=[(1, 0.44)],
            pt_threshold=0.05, sl_threshold=0.05,
            horizon_secs=10, side="short",
        )
        # Lower barrier for short = take-profit = label +1
        assert r["label"] == 1
        assert r["barrier"] == "lower"

    def test_short_uses_symmetric_thresholds(self):
        """For short, upper = entry + sl, lower = entry - pt (profit below)."""
        r = apply_triple_barrier(
            entry_time=0, entry_price=0.50,
            price_path=[(1, 0.50)],
            pt_threshold=0.03, sl_threshold=0.08,
            horizon_secs=10, side="short",
        )
        assert r["upper_barrier"] == pytest.approx(0.58)  # sl above
        assert r["lower_barrier"] == pytest.approx(0.47)  # pt below


class TestTripleBarrierValidation:
    def test_negative_pt_threshold_raises(self):
        with pytest.raises(ValueError, match="pt_threshold"):
            apply_triple_barrier(0, 0.50, [(1, 0.51)], pt_threshold=-0.01,
                                 sl_threshold=0.05, horizon_secs=10)

    def test_zero_sl_threshold_raises(self):
        with pytest.raises(ValueError, match="sl_threshold"):
            apply_triple_barrier(0, 0.50, [(1, 0.51)], pt_threshold=0.05,
                                 sl_threshold=0, horizon_secs=10)

    def test_zero_horizon_raises(self):
        with pytest.raises(ValueError, match="horizon_secs"):
            apply_triple_barrier(0, 0.50, [(1, 0.51)], pt_threshold=0.05,
                                 sl_threshold=0.05, horizon_secs=0)

    def test_invalid_side_raises(self):
        with pytest.raises(ValueError, match="side"):
            apply_triple_barrier(0, 0.50, [(1, 0.51)], pt_threshold=0.05,
                                 sl_threshold=0.05, horizon_secs=10, side="flat")

    def test_unsorted_path_raises(self):
        with pytest.raises(ValueError, match="sorted by timestamp"):
            apply_triple_barrier(
                0, 0.50,
                [(2, 0.51), (1, 0.52)],  # descending
                pt_threshold=0.05, sl_threshold=0.05, horizon_secs=10,
            )


class TestLabelEventsBatch:
    def test_label_events_preserves_order_and_ids(self):
        events = [
            {"event_id": "A", "entry_time": 0, "entry_price": 0.50,
             "price_path": [(1, 0.56)], "side": "long"},
            {"event_id": "B", "entry_time": 10, "entry_price": 0.50,
             "price_path": [(11, 0.44)], "side": "long"},
        ]
        out = label_events(events, pt_threshold=0.05, sl_threshold=0.05,
                           horizon_secs=20)
        assert [x["event_id"] for x in out] == ["A", "B"]
        assert out[0]["label"] == 1
        assert out[1]["label"] == -1

    def test_label_events_default_side_is_long(self):
        events = [{"entry_time": 0, "entry_price": 0.50,
                   "price_path": [(1, 0.56)]}]
        out = label_events(events, pt_threshold=0.05, sl_threshold=0.05,
                           horizon_secs=10)
        assert out[0]["side"] == "long"
        assert out[0]["label"] == 1


class TestLabelSummary:
    def test_summary_counts(self):
        labels = [
            {"label": 1, "barrier": "upper"},
            {"label": 1, "barrier": "upper"},
            {"label": -1, "barrier": "lower"},
            {"label": 0, "barrier": "time"},
            {"label": 0, "barrier": "time"},
        ]
        s = label_summary(labels)
        assert s["n"] == 5
        assert s["wins"] == 2
        assert s["losses"] == 1
        assert s["timeouts"] == 2
        assert s["win_rate"] == pytest.approx(0.4)
        assert s["timeout_rate"] == pytest.approx(0.4)
        assert s["barrier_counts"] == {"upper": 2, "lower": 1, "time": 2}

    def test_empty_summary(self):
        s = label_summary([])
        assert s["n"] == 0
        assert s["win_rate"] == 0.0
