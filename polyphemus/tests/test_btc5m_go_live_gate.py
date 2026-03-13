from types import SimpleNamespace

from polyphemus.tools.btc5m_ensemble_go_live_gate import (
    InstanceStats,
    choose_planned_trade_cap,
    evaluate_gates,
)
from polyphemus.tools.strategy_shadow_scan import StrategyProfile, StrategyResult


def make_result(
    *,
    trades: int,
    win_rate: float,
    avg_net: float,
    roi: float,
    max_dd: float,
    rolling5: float,
):
    profile = StrategyProfile(
        name="test",
        principle="test",
        description="test",
        predicate=lambda _: True,
        ranker=lambda _: 0.0,
    )
    return SimpleNamespace(
        result=StrategyResult(
            profile=profile,
            trades=trades,
            wins=int(trades * win_rate),
            losses=trades - int(trades * win_rate),
            win_rate=win_rate,
            total_gross_pnl=0.0,
            avg_gross_pnl=0.0,
            gross_roi_on_cost=0.0,
            total_net_pnl=avg_net * trades,
            avg_net_pnl=avg_net,
            net_roi_on_cost=roi,
            max_drawdown=max_dd,
            trades_per_day=10.0,
            median_price=0.68,
            median_time_remaining=240.0,
            top_sources=["binance_momentum (30)"],
            top_price_buckets=["0.60-0.79 (30)"],
            status_mix=["shadow (30)"],
            r8="MODERATE n=30",
            net_pnl_series=[avg_net] * trades,
            cost_series=[0.68] * trades,
            rolling_5_worst_loss=-rolling5,
        ),
        avg_live_net=avg_net,
        live_net_roi=roi,
        live_max_drawdown=max_dd,
        live_worst_rolling5_loss=rolling5,
    )


def test_choose_planned_trade_cap_uses_tighter_hard_cap():
    env = {"MAX_BET": "25", "MAX_TRADE_AMOUNT": "20"}
    assert choose_planned_trade_cap(env) == 20.0


def test_evaluate_gates_blocks_anecdotal_and_operational_unknowns():
    instances = [
        InstanceStats("emmanuel", "era", 0, 172800, 48.0, 100, 95, 0.95, 0.0, 20, 5, 4, 1, 1, 0, 0),
        InstanceStats("polyphemus", "era", 0, 172800, 48.0, 100, 95, 0.95, 0.0, 20, 5, 0, 0, 0, 0, 0),
    ]
    strategy = make_result(trades=12, win_rate=0.75, avg_net=0.04, roi=0.06, max_dd=5.0, rolling5=4.0)
    benchmark = make_result(trades=12, win_rate=0.72, avg_net=0.03, roi=0.05, max_dd=4.0, rolling5=3.0)

    blockers = evaluate_gates(
        instances,
        strategy,
        benchmark,
        hours_required=48.0,
        max_daily_loss=40.0,
        audit_clean=False,
        audit_reason="clob_db_audit_unresolved",
        journal_clean="unknown",
        config_drift_clean="unknown",
        open_crit_count=-1,
        dashboard_fields_supported=True,
    )

    assert any("sample size 12 < 30" in blocker for blocker in blockers)
    assert any("emmanuel audit" in blocker for blocker in blockers)
    assert any("journal check" in blocker for blocker in blockers)


def test_evaluate_gates_passes_clean_thresholds():
    instances = [
        InstanceStats("emmanuel", "era", 0, 172800, 48.0, 100, 95, 0.95, 0.0, 35, 12, 12, 1, 1, 0, 0),
        InstanceStats("polyphemus", "era", 0, 172800, 48.0, 100, 96, 0.96, 0.0, 37, 14, 0, 0, 0, 0, 0),
    ]
    strategy = make_result(trades=32, win_rate=0.75, avg_net=0.06, roi=0.08, max_dd=8.0, rolling5=6.0)
    benchmark = make_result(trades=32, win_rate=0.70, avg_net=0.03, roi=0.05, max_dd=9.0, rolling5=7.0)

    blockers = evaluate_gates(
        instances,
        strategy,
        benchmark,
        hours_required=48.0,
        max_daily_loss=40.0,
        audit_clean=True,
        audit_reason="clean",
        journal_clean="yes",
        config_drift_clean="yes",
        open_crit_count=0,
        dashboard_fields_supported=True,
    )

    assert blockers == []
