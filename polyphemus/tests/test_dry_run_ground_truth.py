"""Phase 1.8 ground-truth replay. Final Phase 1 risk gate.

Seeds a fixed 50-cycle synthetic window (plus 3 empty settlements and 5 live
cycles to exercise filters) through the dry-run metrics stack, then asserts
the recorded ledger matches a hand-computed ground truth to 1 cent. Checks:

  - Total P&L (all-rows, dry-only, live-only) matches hand-computed sums
  - Category counts (hedged, sellback, orphan, forced_hold, empty) match
  - Per-asset and per-window segmentation sums match
  - hedge_rate and orphan_rate use capital_committed_cycles (not total)
  - Apr 10 2026 regression pin: if we drop sellback + forced_hold rows the
    apparent P&L flips from -$23.75 to +$6.00 (the $29.75 credibility gap
    that cost Chudi real money). This test proves the current code does
    not drop them.

The ledger is deterministic and declared inline so future readers can audit
every expected number against the cycle that produced it.
"""

import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import List

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


@dataclass(frozen=True)
class LedgerEntry:
    """One hand-audited row of the ground-truth ledger.

    `expected_pnl` is the hand-computed number, not something derived from
    up_qty / down_qty / fees inside this file. That separation is intentional:
    the test must fail if the aggregation layer silently recomputes P&L
    differently from what the caller claimed, because that is exactly the
    Apr 10 failure mode.
    """
    asset: str
    window_secs: int
    exit_reason: str
    expected_pnl: float
    is_dry_run: bool = True


# ----------------------------------------------------------------------------
# Hand-computed ledger — edit only if you also update the totals below.
# ----------------------------------------------------------------------------
# Per-cycle P&L convention:
#   Hedged:          +$0.30 (matched payout minus pair cost minus fees)
#   Sellback winner: +$0.15 (partial fill sold back slightly above entry)
#   Sellback loser:  -$0.25 (partial fill sold back below entry)
#   Orphan winner:   +$4.00 (single side resolved at $1.00)
#   Orphan loser:    -$5.00 (single side resolved at $0.00; entry cost lost)
#   Forced hold:     -$5.00 (SELL failed, resolved against us, same as loser)
#   Empty:           $0.00 (no capital committed, no fills)
#
# Expected totals:
#   Hedged sum      : 20 * 0.30  =  +6.00
#   Sellback winners: 10 * 0.15  =  +1.50
#   Sellback losers :  5 * -0.25 =  -1.25
#   Orphan winners  :  5 * 4.00  = +20.00
#   Orphan losers   :  5 * -5.00 = -25.00
#   Forced holds    :  5 * -5.00 = -25.00
#   Empty           :  3 *  0.00 =   0.00
#   ------------------------------------
#   Dry-run total   :             -23.75
#   Live cycles     :  5 *  1.00 =  +5.00
#   ------------------------------------
#   Grand total     :             -18.75
# ----------------------------------------------------------------------------

EXPECTED_DRY_TOTAL_PNL = -23.75
EXPECTED_LIVE_TOTAL_PNL = 5.00
EXPECTED_GRAND_TOTAL_PNL = -18.75

# Per-asset dry-run sums (live cycles are BTC and are excluded here):
#   BTC: 20 hedged + 5 orphan wins + 3 empty
#        = 20*0.30 + 5*4.00 + 3*0 = 6.00 + 20.00 = +26.00
#   ETH: 10 sellback wins + 5 orphan losses
#        = 10*0.15 + 5*-5.00 = 1.50 - 25.00 = -23.50
#   SOL: 5 sellback losses + 5 forced_hold
#        = 5*-0.25 + 5*-5.00 = -1.25 - 25.00 = -26.25
EXPECTED_DRY_ASSET_PNL = {"btc": 26.00, "eth": -23.50, "sol": -26.25}
#   Sum check: 26.00 - 23.50 - 26.25 = -23.75 ✓

# Per-window dry-run sums:
#   300  (5m):  20 hedged (btc) + 5 sellback loss (sol) + 5 orphan win (btc)
#               + 5 forced_hold (sol) + 3 empty (btc)
#             = 6.00 - 1.25 + 20.00 - 25.00 + 0.00 = -0.25
#   900  (15m): 10 sellback win (eth) + 5 orphan loss (eth)
#             = 1.50 - 25.00 = -23.50
EXPECTED_DRY_WINDOW_PNL = {300: -0.25, 900: -23.50}
#   Sum check: -0.25 - 23.50 = -23.75 ✓


def _fixed_ledger() -> List[LedgerEntry]:
    entries: List[LedgerEntry] = []
    entries += [LedgerEntry("btc", 300, "hedged_settlement", 0.30) for _ in range(20)]
    entries += [LedgerEntry("eth", 900, "sellback",          0.15) for _ in range(10)]
    entries += [LedgerEntry("sol", 300, "sellback",         -0.25) for _ in range(5)]
    entries += [LedgerEntry("btc", 300, "orphaned_settlement", 4.00) for _ in range(5)]
    entries += [LedgerEntry("eth", 900, "orphaned_settlement",-5.00) for _ in range(5)]
    entries += [LedgerEntry("sol", 300, "forced_hold_sell_failed",-5.00) for _ in range(5)]
    # 3 empty settlements — not capital-committed; must NOT count toward rate denominators.
    entries += [LedgerEntry("btc", 300, "empty_settlement",   0.00) for _ in range(3)]
    # 5 live cycles, all hedged at a different pnl so the live/dry filters expose any mixing.
    entries += [LedgerEntry("btc", 300, "hedged_settlement",  1.00, is_dry_run=False) for _ in range(5)]
    return entries


def _seed(db_path: str, entries: List[LedgerEntry]) -> None:
    metrics = AccumulatorMetrics(db_path=db_path)
    now = time.time()
    for i, entry in enumerate(entries):
        metrics.record_cycle(CycleRecord(
            slug=f"{entry.asset}-ledger-{i}",
            started_at=now - 60,
            ended_at=now - i * 0.01,   # staggered so ORDER BY ended_at is stable
            up_qty=10.0,
            down_qty=10.0 if entry.exit_reason in EXIT_REASONS_HEDGED else 0.0,
            up_avg_price=0.5,
            down_avg_price=0.5,
            pair_cost=5.0,
            pnl=entry.expected_pnl,
            exit_reason=entry.exit_reason,
            reprices_used=0,
            fill_time_secs=1.0,
            hedge_time_secs=1.0 if entry.exit_reason in EXIT_REASONS_HEDGED else 0.0,
            spread_at_entry=0.0,
            is_dry_run=entry.is_dry_run,
            asset=entry.asset,
            window_duration_secs=entry.window_secs,
        ))


class TestGroundTruthTotals:
    def test_dry_run_total_pnl_matches_hand_computed(self, tmp_path):
        path = str(tmp_path / f"gt_total_{uuid.uuid4().hex[:8]}.db")
        _seed(path, _fixed_ledger())
        metrics = AccumulatorMetrics(db_path=path)

        snap = metrics.get_stats(window_mins=60, dry_run_only=True)

        assert snap.total_pnl == pytest.approx(EXPECTED_DRY_TOTAL_PNL, abs=0.01), (
            f"dry-run total P&L drift: got ${snap.total_pnl:.4f}, "
            f"expected ${EXPECTED_DRY_TOTAL_PNL:.2f}"
        )

    def test_live_total_pnl_matches_hand_computed(self, tmp_path):
        path = str(tmp_path / f"gt_live_{uuid.uuid4().hex[:8]}.db")
        _seed(path, _fixed_ledger())
        metrics = AccumulatorMetrics(db_path=path)

        snap = metrics.get_stats(window_mins=60, dry_run_only=False)

        assert snap.total_pnl == pytest.approx(EXPECTED_LIVE_TOTAL_PNL, abs=0.01)
        assert snap.total_cycles == 5

    def test_grand_total_pnl_matches_hand_computed(self, tmp_path):
        path = str(tmp_path / f"gt_grand_{uuid.uuid4().hex[:8]}.db")
        _seed(path, _fixed_ledger())
        metrics = AccumulatorMetrics(db_path=path)

        snap = metrics.get_stats(window_mins=60, dry_run_only=None)

        assert snap.total_pnl == pytest.approx(EXPECTED_GRAND_TOTAL_PNL, abs=0.01)


class TestCategoryCounts:
    def test_dry_run_counts_match(self, tmp_path):
        path = str(tmp_path / f"gt_counts_{uuid.uuid4().hex[:8]}.db")
        _seed(path, _fixed_ledger())
        metrics = AccumulatorMetrics(db_path=path)

        snap = metrics.get_stats(window_mins=60, dry_run_only=True)

        assert snap.total_cycles == 53, "all dry rows must be visible"
        assert snap.hedged_count == 20
        assert snap.orphan_count == 10         # orphan winners + orphan losers
        assert snap.sellback_count == 15       # sellback winners + sellback losers
        assert snap.unwind_count == 15         # alias of sellback_count
        assert snap.forced_hold_count == 5
        assert snap.empty_count == 3
        assert snap.capital_committed_cycles == 50, (
            "empty settlements must be excluded from capital_committed_cycles"
        )


class TestRateDenominators:
    def test_hedge_and_orphan_rates_use_capital_committed(self, tmp_path):
        path = str(tmp_path / f"gt_rates_{uuid.uuid4().hex[:8]}.db")
        _seed(path, _fixed_ledger())
        metrics = AccumulatorMetrics(db_path=path)

        snap = metrics.get_stats(window_mins=60, dry_run_only=True)

        # 20 hedged / 50 capital_committed — NOT 20/53.
        assert snap.hedge_rate == pytest.approx(20 / 50, abs=1e-6)
        # (10 orphan + 15 sellback + 5 forced_hold) / 50 = 30/50 = 0.60
        assert snap.orphan_rate == pytest.approx(30 / 50, abs=1e-6)


class TestPerAssetSegmentation:
    def test_per_asset_sums_match_hand_computed(self, tmp_path):
        path = str(tmp_path / f"gt_asset_{uuid.uuid4().hex[:8]}.db")
        _seed(path, _fixed_ledger())

        conn = sqlite3.connect(path)
        per_asset = {
            row[0]: row[1] for row in conn.execute(
                "SELECT asset, SUM(pnl) FROM cycles "
                "WHERE is_dry_run=1 GROUP BY asset ORDER BY asset"
            ).fetchall()
        }
        conn.close()

        for asset, expected in EXPECTED_DRY_ASSET_PNL.items():
            assert per_asset[asset] == pytest.approx(expected, abs=0.01), (
                f"asset={asset} drifted: got ${per_asset[asset]:.4f}, "
                f"expected ${expected:.2f}"
            )

        # Sum of per-asset buckets must equal the dry-run grand total.
        assert sum(per_asset.values()) == pytest.approx(EXPECTED_DRY_TOTAL_PNL, abs=0.01)


class TestPerWindowSegmentation:
    def test_per_window_sums_match_hand_computed(self, tmp_path):
        path = str(tmp_path / f"gt_window_{uuid.uuid4().hex[:8]}.db")
        _seed(path, _fixed_ledger())

        conn = sqlite3.connect(path)
        per_window = {
            row[0]: row[1] for row in conn.execute(
                "SELECT window_duration_secs, SUM(pnl) FROM cycles "
                "WHERE is_dry_run=1 AND window_duration_secs > 0 "
                "GROUP BY window_duration_secs"
            ).fetchall()
        }
        conn.close()

        for window, expected in EXPECTED_DRY_WINDOW_PNL.items():
            assert per_window[window] == pytest.approx(expected, abs=0.01), (
                f"window={window}s drifted: got ${per_window[window]:.4f}, "
                f"expected ${expected:.2f}"
            )


class TestApr10Regression:
    """Pin the exact bug class that cost Chudi real money."""

    def test_dropping_sellback_and_forced_hold_flips_reported_pnl(self, tmp_path):
        """Simulate the pre-Phase-1.1 bug: aggregate over hedged rows only.

        That path reports +$6.00 (a credible-looking small profit) while the
        true dry-run P&L is -$23.75. A $29.75 credibility gap on a 50-cycle
        window is exactly how Apr 10 showed +$39 vs real -$85. The test
        asserts this gap exists IF you drop the rows, proving the categories
        matter.
        """
        path = str(tmp_path / f"gt_apr10_{uuid.uuid4().hex[:8]}.db")
        _seed(path, _fixed_ledger())

        conn = sqlite3.connect(path)
        # Simulate the bug: exclude sellback + forced_hold rows.
        hedged_only = conn.execute(
            "SELECT SUM(pnl) FROM cycles "
            "WHERE is_dry_run=1 AND exit_reason IN ('hedged_settlement')"
        ).fetchone()[0]
        # Current code path: include every capital-committed row.
        all_capital = conn.execute(
            f"SELECT SUM(pnl) FROM cycles "
            f"WHERE is_dry_run=1 AND exit_reason IN "
            f"({','.join('?' * len(EXIT_REASONS_CAPITAL_COMMITTED))})",
            tuple(EXIT_REASONS_CAPITAL_COMMITTED),
        ).fetchone()[0]
        conn.close()

        assert hedged_only == pytest.approx(6.00, abs=0.01), (
            "hedged-only aggregation should show the misleading +$6 figure"
        )
        assert all_capital == pytest.approx(EXPECTED_DRY_TOTAL_PNL, abs=0.01), (
            "full-capital aggregation must show the true -$23.75"
        )
        credibility_gap = hedged_only - all_capital
        assert credibility_gap == pytest.approx(29.75, abs=0.01), (
            f"Apr 10 credibility gap pin drifted: expected $29.75, got ${credibility_gap:.2f}"
        )

    def test_get_stats_does_not_reproduce_apr10_bug(self, tmp_path):
        """get_stats is the path any downstream consumer uses. It MUST show
        the true P&L, not the hedged-only lie. If this test ever fails we
        have reintroduced the Apr 10 bug."""
        path = str(tmp_path / f"gt_stats_{uuid.uuid4().hex[:8]}.db")
        _seed(path, _fixed_ledger())
        metrics = AccumulatorMetrics(db_path=path)

        snap = metrics.get_stats(window_mins=60, dry_run_only=True)

        # If get_stats ever drifts back toward the hedged-only figure, this
        # assertion catches it before any downstream tuner ever sees the lie.
        assert snap.total_pnl < 0, (
            f"total_pnl={snap.total_pnl:.4f} — dry-run ledger is a net "
            "loss; a positive reading means sellback/forced_hold rows "
            "are being silently dropped (Apr 10 regression)."
        )
        assert snap.total_pnl == pytest.approx(EXPECTED_DRY_TOTAL_PNL, abs=0.01)


class TestDryLiveSegregationNoBleed:
    def test_dry_filter_excludes_live_rows(self, tmp_path):
        path = str(tmp_path / f"gt_seg_{uuid.uuid4().hex[:8]}.db")
        _seed(path, _fixed_ledger())
        metrics = AccumulatorMetrics(db_path=path)

        dry = metrics.get_stats(window_mins=60, dry_run_only=True)
        live = metrics.get_stats(window_mins=60, dry_run_only=False)

        # Live rows must not leak into the dry aggregate (and vice versa).
        assert dry.total_pnl != live.total_pnl
        assert dry.total_pnl + live.total_pnl == pytest.approx(
            EXPECTED_GRAND_TOTAL_PNL, abs=0.01
        )
        assert dry.total_cycles + live.total_cycles == 58  # 53 dry + 5 live


class TestNoRowsSilentlyDropped:
    def test_every_ledger_entry_reaches_the_db(self, tmp_path):
        """The silent-drop bug is the worst failure mode. Assert every entry
        we seeded is present; anything less means the ledger and the metrics
        stack disagree before aggregation even starts."""
        entries = _fixed_ledger()
        path = str(tmp_path / f"gt_drop_{uuid.uuid4().hex[:8]}.db")
        _seed(path, entries)

        conn = sqlite3.connect(path)
        row_count = conn.execute("SELECT COUNT(*) FROM cycles").fetchone()[0]
        conn.close()

        assert row_count == len(entries), (
            f"{len(entries) - row_count} ledger entries failed to persist"
        )
