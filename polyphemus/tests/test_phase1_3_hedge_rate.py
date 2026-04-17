"""Phase 1.3 — hedge_rate/orphan_rate denominators include sellbacks.

Apr 10 2026 precedent: accum_metrics reported 97% hedge_rate and +$39 profit
while live P&L was -$85. The silent-drop path was that sellback and forced_hold
rows never reached `get_stats`, so the rate denominator ignored them.

Phase 1.1 wired every terminal path to `_emit_cycle`, so the rows now exist.
Phase 1.3 rewrites `get_stats` so the denominator acknowledges them:

  * `capital_committed_cycles` = hedged + orphaned + sellback + forced_hold
  * `hedge_rate` = hedged / capital_committed_cycles
  * `orphan_rate` = (orphaned + sellback + forced_hold) / capital_committed_cycles
  * empty_settlement / unknown rows are counted but excluded from rates

Hand-computed expectations here fail loudly if someone reintroduces the drop.
"""

from __future__ import annotations

import time
import uuid

import pytest

from polyphemus.accumulator_metrics import (
    EXIT_REASONS_CAPITAL_COMMITTED,
    EXIT_REASONS_FORCED_HOLD,
    EXIT_REASONS_HEDGED,
    EXIT_REASONS_NEUTRAL,
    EXIT_REASONS_ORPHANED,
    EXIT_REASONS_SELLBACK,
    AccumulatorMetrics,
    CycleRecord,
)


def _unique_db(tmp_path, stem: str = "phase13") -> str:
    return str(tmp_path / f"{stem}_{uuid.uuid4().hex[:8]}.db")


def _cycle(
    slug: str,
    exit_reason: str,
    *,
    pnl: float = 0.0,
    pair_cost: float = 0.95,
    is_dry_run: bool = True,
    ended_offset: float = 0.0,
) -> CycleRecord:
    now = time.time()
    return CycleRecord(
        slug=slug,
        started_at=now - 60,
        ended_at=now - ended_offset,
        up_qty=10.0,
        down_qty=10.0 if exit_reason in EXIT_REASONS_HEDGED else 0.0,
        up_avg_price=0.50,
        down_avg_price=0.50 if exit_reason in EXIT_REASONS_HEDGED else 0.0,
        pair_cost=pair_cost,
        pnl=pnl,
        exit_reason=exit_reason,
        reprices_used=1,
        fill_time_secs=2.0,
        hedge_time_secs=3.0 if exit_reason in EXIT_REASONS_HEDGED else 0.0,
        spread_at_entry=1.0,
        is_dry_run=is_dry_run,
    )


def _seed(metrics: AccumulatorMetrics, records):
    for r in records:
        metrics.record_cycle(r)


class TestCategorizationConstants:
    def test_capital_committed_set_matches_union(self):
        """`EXIT_REASONS_CAPITAL_COMMITTED` must equal the union of the four
        non-neutral buckets. If this drifts, rate denominators go wrong."""
        assert EXIT_REASONS_CAPITAL_COMMITTED == (
            EXIT_REASONS_HEDGED
            | EXIT_REASONS_ORPHANED
            | EXIT_REASONS_SELLBACK
            | EXIT_REASONS_FORCED_HOLD
        )

    def test_neutral_is_disjoint_from_capital(self):
        """neutral rows (empty_settlement, unknown) must be excluded from
        capital-committed; otherwise idle scans dilute the tuner signal."""
        assert not (EXIT_REASONS_NEUTRAL & EXIT_REASONS_CAPITAL_COMMITTED)


class TestHedgeRateDenominator:
    def test_sellback_rows_appear_in_denominator(self, tmp_path):
        """Apr 10 regression guard: hedge_rate must NOT be 100% when one row
        is hedged and one is sellback. Before Phase 1.3 the sellback row was
        silently dropped and hedge_rate would read 1.0. Now it must be 0.5."""
        metrics = AccumulatorMetrics(db_path=_unique_db(tmp_path))
        _seed(metrics, [
            _cycle("h-1", "hedged_settlement", pnl=0.5),
            _cycle("s-1", "sellback", pnl=-0.3),
        ])
        snap = metrics.get_all_stats()
        assert snap.capital_committed_cycles == 2
        assert snap.hedge_rate == pytest.approx(0.5)
        assert snap.orphan_rate == pytest.approx(0.5)

    def test_forced_hold_rows_appear_in_denominator(self, tmp_path):
        """forced_hold_* counts as capital-committed orphan variants. A 1-of-3
        hedge rate means 33%, not 100%."""
        metrics = AccumulatorMetrics(db_path=_unique_db(tmp_path))
        _seed(metrics, [
            _cycle("h-1", "hedged_settlement", pnl=0.5),
            _cycle("f-1", "forced_hold_expired", pnl=-5.0),
            _cycle("f-2", "forced_hold_clob_unindexed", pnl=-4.0),
        ])
        snap = metrics.get_all_stats()
        assert snap.capital_committed_cycles == 3
        assert snap.hedge_rate == pytest.approx(1 / 3)
        assert snap.orphan_rate == pytest.approx(2 / 3)
        assert snap.forced_hold_count == 2

    def test_empty_settlement_excluded_from_rate_denominator(self, tmp_path):
        """Idle scans (empty_settlement) must not dilute rates. Two hedged
        + two empty = hedge_rate 100%, not 50%."""
        metrics = AccumulatorMetrics(db_path=_unique_db(tmp_path))
        _seed(metrics, [
            _cycle("h-1", "hedged_settlement", pnl=0.5),
            _cycle("h-2", "hedged_settlement", pnl=0.5),
            _cycle("e-1", "empty_settlement", pnl=0.0),
            _cycle("e-2", "empty_settlement", pnl=0.0),
        ])
        snap = metrics.get_all_stats()
        assert snap.total_cycles == 4
        assert snap.empty_count == 2
        assert snap.capital_committed_cycles == 2
        assert snap.hedge_rate == pytest.approx(1.0)
        assert snap.orphan_rate == pytest.approx(0.0)

    def test_all_empty_window_yields_zero_rate_not_crash(self, tmp_path):
        """Denominator-zero edge case: no capital-committed rows must yield
        0.0 rates (not ZeroDivisionError). The adaptive tuner reads this
        every 60s and cannot tolerate a crash."""
        metrics = AccumulatorMetrics(db_path=_unique_db(tmp_path))
        _seed(metrics, [_cycle("e-1", "empty_settlement")])
        snap = metrics.get_all_stats()
        assert snap.total_cycles == 1
        assert snap.capital_committed_cycles == 0
        assert snap.hedge_rate == 0.0
        assert snap.orphan_rate == 0.0


class TestExitBucketCounts:
    def test_all_buckets_populated_correctly(self, tmp_path):
        """One cycle per exit_reason. Counts must isolate each bucket."""
        metrics = AccumulatorMetrics(db_path=_unique_db(tmp_path))
        _seed(metrics, [
            _cycle("h-1", "hedged_settlement"),
            _cycle("o-1", "orphaned_settlement", pnl=-1.0),
            _cycle("s-1", "sellback", pnl=-0.5),
            _cycle("f-1", "forced_hold_expired", pnl=-5.0),
            _cycle("f-2", "forced_hold_clob_unindexed", pnl=-4.0),
            _cycle("f-3", "forced_hold_sell_failed", pnl=-3.0),
            _cycle("f-4", "sellback_skipped_below_min", pnl=-2.0),
            _cycle("e-1", "empty_settlement"),
        ])
        snap = metrics.get_all_stats()
        assert snap.total_cycles == 8
        assert snap.hedged_count == 1
        assert snap.orphan_count == 1
        assert snap.sellback_count == 1
        assert snap.unwind_count == snap.sellback_count
        assert snap.forced_hold_count == 4
        assert snap.empty_count == 1
        assert snap.capital_committed_cycles == 7

    def test_orphan_loss_total_sums_all_non_hedged_non_neutral_pnl(self, tmp_path):
        """orphan_loss_total must aggregate every P&L bucket that burned
        capital but is not counted as success. Missing any bucket here is the
        Apr 10 failure mode. Hand-computed expected total: -15.5."""
        metrics = AccumulatorMetrics(db_path=_unique_db(tmp_path))
        _seed(metrics, [
            _cycle("h-1", "hedged_settlement", pnl=1.0),
            _cycle("o-1", "orphaned_settlement", pnl=-1.0),
            _cycle("s-1", "sellback", pnl=-0.5),
            _cycle("f-1", "forced_hold_expired", pnl=-5.0),
            _cycle("f-2", "forced_hold_clob_unindexed", pnl=-4.0),
            _cycle("f-3", "forced_hold_sell_failed", pnl=-3.0),
            _cycle("f-4", "sellback_skipped_below_min", pnl=-2.0),
            _cycle("e-1", "empty_settlement", pnl=0.0),
        ])
        snap = metrics.get_all_stats()
        assert snap.orphan_loss_total == pytest.approx(-15.5)
        assert snap.total_pnl == pytest.approx(-14.5)

    def test_avg_pair_cost_uses_hedged_rows_only(self, tmp_path):
        """avg_pair_cost is a hedged-only average because pair_cost only makes
        sense when both legs filled. Sellback/forced_hold rows have pair_cost
        set to the single-leg price and would distort the mean."""
        metrics = AccumulatorMetrics(db_path=_unique_db(tmp_path))
        _seed(metrics, [
            _cycle("h-1", "hedged_settlement", pair_cost=0.90),
            _cycle("h-2", "hedged_settlement", pair_cost=0.94),
            _cycle("s-1", "sellback", pair_cost=0.50),
        ])
        snap = metrics.get_all_stats()
        assert snap.avg_pair_cost == pytest.approx(0.92)

    def test_avg_pnl_per_hedged_zero_when_no_hedged_rows(self, tmp_path):
        """Hedged-only aggregate must not crash on empty hedged bucket."""
        metrics = AccumulatorMetrics(db_path=_unique_db(tmp_path))
        _seed(metrics, [
            _cycle("s-1", "sellback", pnl=-0.5),
            _cycle("f-1", "forced_hold_expired", pnl=-5.0),
        ])
        snap = metrics.get_all_stats()
        assert snap.hedged_count == 0
        assert snap.avg_pnl_per_hedged == 0.0
        assert snap.avg_pair_cost == 0.0
        assert snap.hedge_rate == 0.0
        assert snap.orphan_rate == pytest.approx(1.0)


class TestDryRunFilter:
    def test_dry_run_only_true_excludes_live_rows(self, tmp_path):
        metrics = AccumulatorMetrics(db_path=_unique_db(tmp_path))
        _seed(metrics, [
            _cycle("dry-1", "hedged_settlement", pnl=0.5, is_dry_run=True),
            _cycle("dry-2", "sellback", pnl=-0.3, is_dry_run=True),
            _cycle("live-1", "hedged_settlement", pnl=0.8, is_dry_run=False),
            _cycle("live-2", "forced_hold_expired", pnl=-5.0, is_dry_run=False),
        ])
        dry = metrics.get_all_stats(dry_run_only=True)
        assert dry.total_cycles == 2
        assert dry.total_pnl == pytest.approx(0.2)
        assert dry.hedge_rate == pytest.approx(0.5)

    def test_dry_run_only_false_excludes_dry_rows(self, tmp_path):
        metrics = AccumulatorMetrics(db_path=_unique_db(tmp_path))
        _seed(metrics, [
            _cycle("dry-1", "hedged_settlement", pnl=0.5, is_dry_run=True),
            _cycle("live-1", "hedged_settlement", pnl=0.8, is_dry_run=False),
            _cycle("live-2", "forced_hold_expired", pnl=-5.0, is_dry_run=False),
        ])
        live = metrics.get_all_stats(dry_run_only=False)
        assert live.total_cycles == 2
        assert live.total_pnl == pytest.approx(-4.2)
        assert live.hedge_rate == pytest.approx(0.5)

    def test_dry_run_only_none_includes_all_rows(self, tmp_path):
        metrics = AccumulatorMetrics(db_path=_unique_db(tmp_path))
        _seed(metrics, [
            _cycle("dry-1", "hedged_settlement", pnl=0.5, is_dry_run=True),
            _cycle("live-1", "hedged_settlement", pnl=0.8, is_dry_run=False),
        ])
        snap = metrics.get_all_stats(dry_run_only=None)
        assert snap.total_cycles == 2
        assert snap.total_pnl == pytest.approx(1.3)

    def test_dry_live_segregation_affects_rates(self, tmp_path):
        """Dry-run stats must not be contaminated by live outcomes and vice
        versa. This is the pre-deploy gate invariant."""
        metrics = AccumulatorMetrics(db_path=_unique_db(tmp_path))
        _seed(metrics, [
            _cycle("dry-h", "hedged_settlement", is_dry_run=True),
            _cycle("dry-h2", "hedged_settlement", is_dry_run=True),
            _cycle("live-o", "orphaned_settlement", pnl=-2.0, is_dry_run=False),
            _cycle("live-o2", "sellback", pnl=-1.0, is_dry_run=False),
        ])
        dry = metrics.get_all_stats(dry_run_only=True)
        live = metrics.get_all_stats(dry_run_only=False)
        assert dry.hedge_rate == pytest.approx(1.0)
        assert live.hedge_rate == pytest.approx(0.0)
        assert live.orphan_rate == pytest.approx(1.0)


class TestWindowFilter:
    def test_window_mins_excludes_rows_outside_window(self, tmp_path):
        """The 60-minute default window must exclude older rows. The adaptive
        tuner reads a rolling window; stale rows would lock it into an
        outdated decision."""
        metrics = AccumulatorMetrics(db_path=_unique_db(tmp_path))
        old_offset = 2 * 3600  # 2 hours ago — outside 60-min window
        fresh_offset = 5  # 5 seconds ago — inside 60-min window
        _seed(metrics, [
            _cycle("old-1", "hedged_settlement", ended_offset=old_offset),
            _cycle("fresh-1", "sellback", pnl=-0.5, ended_offset=fresh_offset),
        ])
        recent = metrics.get_stats(window_mins=60)
        assert recent.total_cycles == 1
        assert recent.sellback_count == 1
        assert recent.hedge_rate == 0.0
        alltime = metrics.get_all_stats()
        assert alltime.total_cycles == 2


class TestAdaptiveTunerContract:
    """Adaptive tuner reads specific fields; pin them so a future refactor
    can't silently drop one (breaking live gating)."""

    REQUIRED_FIELDS = (
        "total_cycles",
        "hedged_count",
        "hedge_rate",
        "orphan_rate",
        "avg_pair_cost",
        "avg_pnl_per_hedged",
        "avg_reprices",
        "total_pnl",
    )

    def test_all_adaptive_tuner_fields_present_on_snapshot(self, tmp_path):
        metrics = AccumulatorMetrics(db_path=_unique_db(tmp_path))
        snap = metrics.get_all_stats()
        for name in self.REQUIRED_FIELDS:
            assert hasattr(snap, name), f"adaptive_tuner requires {name}"

    def test_snapshot_is_stable_under_empty_db(self, tmp_path):
        """Empty DB must yield a non-crashing snapshot with zeroed fields.
        Tuner runs once per minute even with no history."""
        metrics = AccumulatorMetrics(db_path=_unique_db(tmp_path))
        snap = metrics.get_all_stats()
        assert snap.total_cycles == 0
        assert snap.hedge_rate == 0.0
        assert snap.orphan_rate == 0.0
        assert snap.avg_pair_cost == 0.0
        assert snap.total_pnl == 0.0
