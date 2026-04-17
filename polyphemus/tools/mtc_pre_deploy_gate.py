#!/usr/bin/env python3
"""MTC pre-deploy gate: block live deploys that don't pass statistical muster.

Five sequential checks. ANY fails -> overall FAIL. Zero tolerance: a strategy
that squeaks past four checks and flunks the fifth is not live-eligible.

    R1 Sample size         n >= MIN_SAMPLE (30)
    R2 Hypothesis test WR  Wilson CI lower bound > breakeven, one-sided p < alpha
    R3 Walk-forward        N >= 5 splits AND splits_positive >= 60%
    R4 Deflated Sharpe     DSR > DSR_FLOOR (0.5 -- "overfit risk: MODERATE")
    R5 Alpha decay         7d Sharpe has not dropped >= 0.5 vs prior 7d

Inputs come from one of two dry-run data sources:
    cycles  (accum_metrics.db)  Per-cycle returns for accumulator strategies.
                                Segmented by (asset, window_duration_secs).
    trades  (performance.db)    Per-trade returns for signal_bot / weather_arb.
                                Segmented by (strategy).

Exit code 0 on PASS, 1 on FAIL. JSON report on stdout; human-readable summary
on stderr. The gate is deterministic and reproducible -- same inputs, same
verdict, no randomness.

Usage:
    tools/mtc_pre_deploy_gate.py --source cycles \\
        --db polyphemus/data/accum_metrics.db \\
        --asset btc --window-duration 300 --lookback-days 30

    tools/mtc_pre_deploy_gate.py --source trades \\
        --db polyphemus/data/performance.db \\
        --strategy signal_bot --lookback-days 30

Design note: this is the tripwire that was missing in Feb-Mar 2026. The
Apr 10 precedent (accum_metrics showed +$39 profit while real P&L was -$85
because sellbacks were dropped from the aggregate) would not have been
blocked by any check here -- the fix for *that* was Phase 1.1. What this
gate catches is: statistically-valid-looking backtests that wouldn't
survive out-of-sample validation, small-sample noise, overfit Sharpes,
and strategies whose edge has already started decaying.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

# Add project root to path so tools/ can import polyphemus/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from polyphemus.tools.trader_stats import (  # noqa: E402
    hypothesis_test_wr,
    walk_forward_cv,
    deflated_sharpe,
)
from polyphemus.tools.alpha_decay import alpha_decay_check  # noqa: E402


# ---------------------------------------------------------------------------
# Gate thresholds. Tuning these is a governance decision, not a code change.
# If a threshold needs to loosen it should happen via a CLI flag, not by
# editing these constants -- they exist so the default behavior is the
# "safe" answer.
# ---------------------------------------------------------------------------

MIN_SAMPLE = 30                  # R1 absolute minimum
WR_ALPHA = 0.05                  # R2 one-sided p threshold
WR_BREAKEVEN_DEFAULT = 0.50      # R2 null hypothesis WR; caller overrides for fee-adjusted
WF_MIN_SPLITS = 5                # R3 minimum splits to even run walk-forward
WF_MIN_CONSISTENCY = 0.60        # R3 splits_positive fraction required
DSR_FLOOR = 0.5                  # R4 DSR minimum (LOW/MODERATE overfit risk)
DSR_K_DEFAULT = 3                # R4 number of strategy-parameter knobs tested
DECAY_WINDOW_DAYS = 7            # R5 each alpha-decay window (7d vs 7d)
DECAY_THRESHOLD = 0.5            # R5 max tolerated Sharpe drop


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------


def _load_cycles(
    db_path: str,
    asset: str,
    window_duration_secs: int,
    lookback_days: int,
) -> list[dict]:
    """Pull dry-run accumulator cycles for (asset, window). Returns per-row dicts."""
    cutoff = time.time() - lookback_days * 86400
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(cycles)").fetchall()}
        required = {"asset", "window_duration_secs", "is_dry_run", "pnl",
                    "pair_cost", "ended_at", "exit_reason"}
        missing = required - cols
        if missing:
            raise RuntimeError(
                f"{db_path} cycles table missing required columns {missing}. "
                f"Expected Phase 0 (is_dry_run) and Phase 1.7 (asset, "
                f"window_duration_secs) migrations to have run."
            )
        rows = conn.execute(
            "SELECT pnl, pair_cost, ended_at, exit_reason FROM cycles "
            "WHERE asset = ? AND window_duration_secs = ? "
            "AND is_dry_run = 1 AND ended_at > ? "
            "ORDER BY ended_at ASC",
            (asset, window_duration_secs, cutoff),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _load_trades(
    db_path: str,
    strategy: str,
    lookback_days: int,
) -> list[dict]:
    """Pull dry-run trades for a given strategy. Returns per-row dicts."""
    cutoff = time.time() - lookback_days * 86400
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
        if "is_dry_run" not in cols:
            raise RuntimeError(
                f"{db_path} trades table missing is_dry_run column. "
                f"Open the DB once via polyphemus.performance_db.PerformanceDB "
                f"to run the Phase 0 migration, or add the column manually."
            )
        # Detect pnl column (V1 'profit_loss' vs V2 'pnl'), matching
        # PerformanceDB._detect_pnl_column semantics.
        pnl_col = "pnl" if "pnl" in cols else "profit_loss"
        rows = conn.execute(
            f"SELECT {pnl_col} AS pnl, exit_time, exit_reason, entry_price, entry_size "
            f"FROM trades WHERE strategy = ? AND is_dry_run = 1 "
            f"AND exit_time IS NOT NULL AND exit_time > ? "
            f"ORDER BY exit_time ASC",
            (strategy, cutoff),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------


def _cycle_returns(rows: list[dict]) -> list[float]:
    """Per-dollar returns: pnl / pair_cost. Matches how the accumulator
    measures its own edge and keeps scale across asset/window segments."""
    out = []
    for r in rows:
        cost = r.get("pair_cost") or 0.0
        if cost > 0:
            out.append(r["pnl"] / cost)
        else:
            # Zero-cost cycle (shouldn't happen for capital-committed rows
            # but may appear for empty_settlement). Treat as 0 return rather
            # than skipping -- silent skip is the Apr 10 bug class.
            out.append(0.0)
    return out


def _trade_returns(rows: list[dict]) -> list[float]:
    """Dollar P&L per trade. Used directly for Sharpe/DSR; fine for walk-forward
    since each trade is one bet of roughly-similar size."""
    return [r["pnl"] for r in rows if r["pnl"] is not None]


def _cycle_win_count(rows: list[dict]) -> tuple[int, int]:
    """For R2 WR: a cycle wins when pnl > 0. Returns (wins, total)."""
    wins = sum(1 for r in rows if (r["pnl"] or 0.0) > 0)
    return wins, len(rows)


def _trade_win_count(rows: list[dict]) -> tuple[int, int]:
    """For R2 WR: a trade wins when pnl > 0."""
    wins = sum(1 for r in rows if (r["pnl"] or 0.0) > 0)
    total = sum(1 for r in rows if r["pnl"] is not None)
    return wins, total


def _cycle_timestamps(rows: list[dict]) -> list[float]:
    return [r["ended_at"] for r in rows]


def _trade_timestamps(rows: list[dict]) -> list[float]:
    return [r["exit_time"] for r in rows]


# ---------------------------------------------------------------------------
# Individual gate checks. Each returns dict with keys: check, passed, reason,
# evidence (arbitrary structured data for the report).
# ---------------------------------------------------------------------------


def _check_sample_size(n: int) -> dict:
    passed = n >= MIN_SAMPLE
    return {
        "check": "R1_sample_size",
        "passed": passed,
        "reason": (
            f"n={n} >= {MIN_SAMPLE}" if passed
            else f"n={n} < {MIN_SAMPLE} (minimum sample size for any statistical claim)"
        ),
        "evidence": {"n": n, "threshold": MIN_SAMPLE},
    }


def _check_hypothesis_test_wr(
    wins: int, total: int, breakeven: float, alpha: float
) -> dict:
    if total == 0:
        return {
            "check": "R2_hypothesis_test_wr",
            "passed": False,
            "reason": "no trades -> cannot reject null",
            "evidence": {"wins": 0, "total": 0},
        }
    result = hypothesis_test_wr(
        wins, total, breakeven=breakeven, alpha=alpha, alternative="greater"
    )
    ci_lower = result["wilson_ci"][0]
    ci_passes = ci_lower > breakeven
    p_passes = result["significant"]
    passed = bool(ci_passes and p_passes)
    reason = (
        f"observed_wr={result['observed_wr']:.3f}, "
        f"Wilson CI lower={ci_lower:.3f} "
        f"({'>' if ci_passes else '<='} breakeven={breakeven:.3f}), "
        f"p={result['p_value']:.4f} "
        f"({'< alpha' if p_passes else '>= alpha'}={alpha})"
    )
    return {
        "check": "R2_hypothesis_test_wr",
        "passed": passed,
        "reason": reason,
        "evidence": result,
    }


def _check_walk_forward(returns: list[float]) -> dict:
    result = walk_forward_cv(returns, n_splits=WF_MIN_SPLITS)
    n_splits = len(result["split_results"])
    if n_splits < WF_MIN_SPLITS:
        return {
            "check": "R3_walk_forward",
            "passed": False,
            "reason": (
                f"only {n_splits} splits produced (need >= {WF_MIN_SPLITS}); "
                f"insufficient data for walk-forward"
            ),
            "evidence": result,
        }
    consistency = result["splits_positive"] / n_splits
    passed = consistency >= WF_MIN_CONSISTENCY
    return {
        "check": "R3_walk_forward",
        "passed": passed,
        "reason": (
            f"{result['splits_positive']}/{n_splits} splits positive "
            f"(consistency {consistency:.0%}, "
            f"{'>= ' if passed else '< '}{WF_MIN_CONSISTENCY:.0%} threshold); "
            f"mean test WR={result['mean_test_wr']:.3f}"
        ),
        "evidence": result,
    }


def _check_deflated_sharpe(returns: list[float], k: int) -> dict:
    result = deflated_sharpe(returns, k=k)
    dsr = result.get("dsr_value", 0.0)
    passed = dsr > DSR_FLOOR
    return {
        "check": "R4_deflated_sharpe",
        "passed": passed,
        "reason": (
            f"Sharpe={result.get('sharpe_hat', 0.0):.3f}, "
            f"DSR={dsr:.3f} ({'>' if passed else '<='} floor={DSR_FLOOR}), "
            f"overfit risk={result.get('overfit_risk', 'UNKNOWN')}, "
            f"k={k} params tested"
        ),
        "evidence": result,
    }


def _check_alpha_decay(timestamps: list[float], returns: list[float], now: float) -> dict:
    result = alpha_decay_check(
        timestamps, returns,
        window_days=DECAY_WINDOW_DAYS,
        drop_threshold=DECAY_THRESHOLD,
        now=now,
    )
    # Missing-data case: not a pass -- we do not declare safety from ignorance.
    # But gate-layer semantics: fail CLOSED on missing data so a low-volume
    # strategy cannot coast past decay checks.
    if result["current_sharpe"] is None or result["prior_sharpe"] is None:
        return {
            "check": "R5_alpha_decay",
            "passed": False,
            "reason": (
                f"insufficient data to assess decay "
                f"(current_n={result['current_n']}, prior_n={result['prior_n']})"
            ),
            "evidence": result,
        }
    passed = not result["decayed"]
    return {
        "check": "R5_alpha_decay",
        "passed": passed,
        "reason": (
            f"Sharpe {result['prior_sharpe']:.2f} -> {result['current_sharpe']:.2f} "
            f"over back-to-back {DECAY_WINDOW_DAYS}d windows "
            f"(delta={result['delta']:+.2f}, threshold={DECAY_THRESHOLD})"
        ),
        "evidence": result,
    }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_gate(
    source: str,
    db_path: str,
    lookback_days: int,
    *,
    asset: str = "",
    window_duration_secs: int = 0,
    strategy: str = "",
    breakeven: float = WR_BREAKEVEN_DEFAULT,
    dsr_k: int = DSR_K_DEFAULT,
    now: float | None = None,
) -> dict:
    """Run all five checks and return the combined verdict dict.

    The caller decides what to do with the verdict (print, exit, post to a
    deploy pipeline). This function performs no I/O beyond the SQL reads
    and returns a pure dict.
    """
    if source == "cycles":
        if not asset:
            raise ValueError("source=cycles requires --asset")
        if window_duration_secs <= 0:
            raise ValueError("source=cycles requires --window-duration > 0")
        rows = _load_cycles(db_path, asset, window_duration_secs, lookback_days)
        returns = _cycle_returns(rows)
        wins, total = _cycle_win_count(rows)
        timestamps = _cycle_timestamps(rows)
        segment = {"asset": asset, "window_duration_secs": window_duration_secs}
    elif source == "trades":
        if not strategy:
            raise ValueError("source=trades requires --strategy")
        rows = _load_trades(db_path, strategy, lookback_days)
        returns = _trade_returns(rows)
        wins, total = _trade_win_count(rows)
        timestamps = _trade_timestamps(rows)
        segment = {"strategy": strategy}
    else:
        raise ValueError(f"source must be 'cycles' or 'trades', got {source!r}")

    ref_now = now if now is not None else time.time()

    checks = [
        _check_sample_size(total),
        _check_hypothesis_test_wr(wins, total, breakeven=breakeven, alpha=WR_ALPHA),
        _check_walk_forward(returns),
        _check_deflated_sharpe(returns, k=dsr_k),
        _check_alpha_decay(timestamps, returns, now=ref_now),
    ]
    passed = all(c["passed"] for c in checks)

    first_fail = next((c for c in checks if not c["passed"]), None)
    verdict = "PASS" if passed else "FAIL"

    return {
        "verdict": verdict,
        "passed": passed,
        "source": source,
        "db_path": db_path,
        "segment": segment,
        "lookback_days": lookback_days,
        "n": total,
        "wins": wins,
        "checks": checks,
        "first_failure": first_fail["check"] if first_fail else None,
        "generated_at": ref_now,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _format_human(verdict: dict) -> str:
    lines = []
    tag = "PASS" if verdict["passed"] else "FAIL"
    lines.append(f"[MTC gate] verdict={tag}  source={verdict['source']}  "
                 f"segment={verdict['segment']}  "
                 f"lookback={verdict['lookback_days']}d  "
                 f"n={verdict['n']} wins={verdict['wins']}")
    for c in verdict["checks"]:
        mark = "PASS" if c["passed"] else "FAIL"
        lines.append(f"  {mark} {c['check']}: {c['reason']}")
    if not verdict["passed"]:
        lines.append(f"[MTC gate] first failure: {verdict['first_failure']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Polyphemus MTC pre-deploy gate",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--source", required=True, choices=["cycles", "trades"])
    p.add_argument("--db", required=True, help="path to SQLite DB")
    p.add_argument("--lookback-days", type=int, default=30)
    p.add_argument("--asset", default="", help="(cycles only) e.g. btc, eth, houston")
    p.add_argument("--window-duration", type=int, default=0,
                   help="(cycles only) market window in seconds, e.g. 300 for 5m")
    p.add_argument("--strategy", default="",
                   help="(trades only) e.g. signal_bot, weather_arb")
    p.add_argument("--breakeven", type=float, default=WR_BREAKEVEN_DEFAULT,
                   help="null-hypothesis WR for R2 (default 0.50; fee-adjust per strategy)")
    p.add_argument("--dsr-k", type=int, default=DSR_K_DEFAULT,
                   help="k params tested (R4 DSR adjustment)")
    p.add_argument("--json", action="store_true",
                   help="emit JSON verdict on stdout (else pretty text on stdout)")
    args = p.parse_args(argv)

    verdict = run_gate(
        source=args.source,
        db_path=args.db,
        lookback_days=args.lookback_days,
        asset=args.asset,
        window_duration_secs=args.window_duration,
        strategy=args.strategy,
        breakeven=args.breakeven,
        dsr_k=args.dsr_k,
    )

    if args.json:
        print(json.dumps(verdict, indent=2, default=str))
    else:
        print(_format_human(verdict))

    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
