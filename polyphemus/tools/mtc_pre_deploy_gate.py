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
from typing import Optional

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
    """Pull dry-run trades for a given strategy. Returns per-row dicts.

    v4 attribution columns (``signal_source``, ``fill_model``) are
    surfaced when present so ``--segment-by`` and ``--filter-*`` don't
    need a second query. Missing columns are returned as ``None`` so
    older DBs still load without error — Phase 5 is what guarantees
    populated values.
    """
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
        # Emit NULL placeholders for v4 columns missing on older DBs so
        # the returned row shape is stable regardless of migration state.
        sig_src_expr = "signal_source" if "signal_source" in cols else "NULL AS signal_source"
        fill_model_expr = "fill_model" if "fill_model" in cols else "NULL AS fill_model"
        rows = conn.execute(
            f"SELECT {pnl_col} AS pnl, exit_time, exit_reason, entry_price, entry_size, "
            f"       {sig_src_expr}, {fill_model_expr} "
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
# Attribution segmenting (Phase 4). These helpers let --segment-by and
# --filter-* slice a single trades query by columns that were added in
# the Phase 1 schema migration (signal_source, fill_model) plus the
# derived entry_band bucket. They live here, next to the row shape they
# operate on, rather than in a separate module.
# ---------------------------------------------------------------------------


# Bucket boundaries — must match polyphemus/sql_views/vw_trade_attribution.sql.
# If the SQL changes, this tuple MUST change in lockstep or the gate
# and dashboard will disagree on which trades live in which band.
_ENTRY_BAND_CUTS: tuple[tuple[float, str], ...] = (
    (0.55, "00-55"),
    (0.70, "55-70"),
    (0.85, "70-85"),
    (0.93, "85-93"),
    (0.97, "93-97"),
)


def _derive_entry_band(price: float | None) -> str:
    """Bucket an entry_price into the same bands as vw_trade_attribution.

    Matches the SQL CASE exactly so MTC segment-by results can be cross-
    checked against ``SELECT entry_band, COUNT(*) FROM vw_trade_attribution``.
    ``None`` prices (pre-migration rows) bucket to 'unknown'.
    """
    if price is None:
        return "unknown"
    for upper, label in _ENTRY_BAND_CUTS:
        if price < upper:
            return label
    return "97+"


def _segment_value(row: dict, column: str) -> str:
    """Compute the segment label for one row.

    Supported columns: ``signal_source``, ``fill_model``, ``entry_band``.
    NULL / empty values collapse to the sentinel 'unknown' so the gate
    still produces a verdict. A large 'unknown' slice is itself a signal
    that the Phase-1 migration hasn't finished on this DB.
    """
    if column == "entry_band":
        return _derive_entry_band(row.get("entry_price"))
    val = row.get(column)
    if val is None or val == "":
        return "unknown"
    return str(val)


def _apply_trade_filters(
    rows: list[dict],
    *,
    filter_signal_source: str = "",
    filter_fill_model: str = "",
    filter_entry_band: str = "",
) -> list[dict]:
    """Drop rows that don't match any non-empty filter. Applied uniformly
    by both ``run_gate`` (filters-only mode) and ``run_segmented_gate``
    (filters-then-segment mode)."""
    out = rows
    if filter_signal_source:
        out = [r for r in out if r.get("signal_source") == filter_signal_source]
    if filter_fill_model:
        out = [r for r in out if r.get("fill_model") == filter_fill_model]
    if filter_entry_band:
        out = [
            r for r in out
            if _derive_entry_band(r.get("entry_price")) == filter_entry_band
        ]
    return out


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


def _gate_from_rows(
    source: str,
    rows: list[dict],
    segment: dict,
    *,
    breakeven: float,
    dsr_k: int,
    ref_now: float,
    db_path: str,
    lookback_days: int,
) -> dict:
    """Compute the 5-check verdict over an already-loaded row set.

    Split out of ``run_gate`` so ``run_segmented_gate`` can reuse the
    exact same check sequence per partition. Keeping the verdict shape
    identical matters: receipt consumers and webapp parsers don't need
    to branch on segmented vs global.
    """
    if source == "cycles":
        returns = _cycle_returns(rows)
        wins, total = _cycle_win_count(rows)
        timestamps = _cycle_timestamps(rows)
    elif source == "trades":
        returns = _trade_returns(rows)
        wins, total = _trade_win_count(rows)
        timestamps = _trade_timestamps(rows)
    else:
        raise ValueError(f"source must be 'cycles' or 'trades', got {source!r}")

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
    filter_signal_source: str = "",
    filter_fill_model: str = "",
    filter_entry_band: str = "",
    now: float | None = None,
) -> dict:
    """Run all five checks and return the combined verdict dict.

    The caller decides what to do with the verdict (print, exit, post to a
    deploy pipeline). This function performs no I/O beyond the SQL reads
    and returns a pure dict.

    ``filter_*`` keyword args (Phase 4) accept trades-source attribution
    filters. Empty string means "no filter". Filters are applied after
    the SQL pull so the same query plan serves both global and narrowed
    runs. ``cycles`` source ignores them — accum_metrics.db does not yet
    carry signal_source / fill_model.
    """
    if source == "cycles":
        if not asset:
            raise ValueError("source=cycles requires --asset")
        if window_duration_secs <= 0:
            raise ValueError("source=cycles requires --window-duration > 0")
        rows = _load_cycles(db_path, asset, window_duration_secs, lookback_days)
        segment = {"asset": asset, "window_duration_secs": window_duration_secs}
    elif source == "trades":
        if not strategy:
            raise ValueError("source=trades requires --strategy")
        rows = _load_trades(db_path, strategy, lookback_days)
        rows = _apply_trade_filters(
            rows,
            filter_signal_source=filter_signal_source,
            filter_fill_model=filter_fill_model,
            filter_entry_band=filter_entry_band,
        )
        segment = {"strategy": strategy}
        # Surface active filters inside the segment dict so the receipt
        # records *what was gated*, not just the strategy name. A PASS
        # verdict on signal_bot ∩ v2_probabilistic is very different from
        # a PASS on the whole strategy.
        if filter_signal_source:
            segment["signal_source"] = filter_signal_source
        if filter_fill_model:
            segment["fill_model"] = filter_fill_model
        if filter_entry_band:
            segment["entry_band"] = filter_entry_band
    else:
        raise ValueError(f"source must be 'cycles' or 'trades', got {source!r}")

    ref_now = now if now is not None else time.time()
    return _gate_from_rows(
        source=source,
        rows=rows,
        segment=segment,
        breakeven=breakeven,
        dsr_k=dsr_k,
        ref_now=ref_now,
        db_path=db_path,
        lookback_days=lookback_days,
    )


def run_segmented_gate(
    db_path: str,
    strategy: str,
    lookback_days: int,
    segment_by: str,
    *,
    breakeven: float = WR_BREAKEVEN_DEFAULT,
    dsr_k: int = DSR_K_DEFAULT,
    filter_signal_source: str = "",
    filter_fill_model: str = "",
    filter_entry_band: str = "",
    now: float | None = None,
) -> dict:
    """Load trades once, partition by ``segment_by`` column, gate each partition.

    Returns an envelope verdict:

        {
            "verdict": "PASS" if every partition passes else "FAIL",
            "passed": bool,
            "source": "trades",
            "strategy": <name>,
            "segmented_by": <column>,
            "filters": {...},
            "segments": {<label>: <per-partition verdict>, ...},
            ...
        }

    Only ``source=trades`` is supported — ``accum_metrics.db`` does not yet
    carry the attribution columns needed to segment cycles. Callers who
    want per-asset × per-window cycle verdicts should invoke ``run_gate``
    multiple times with distinct ``--asset`` / ``--window-duration``.
    """
    if segment_by not in {"signal_source", "fill_model", "entry_band"}:
        raise ValueError(
            "segment_by must be one of signal_source, fill_model, entry_band; "
            f"got {segment_by!r}"
        )
    if not strategy:
        raise ValueError("run_segmented_gate requires strategy (trades source only)")

    rows = _load_trades(db_path, strategy, lookback_days)
    rows = _apply_trade_filters(
        rows,
        filter_signal_source=filter_signal_source,
        filter_fill_model=filter_fill_model,
        filter_entry_band=filter_entry_band,
    )

    ref_now = now if now is not None else time.time()

    partitions: dict[str, list[dict]] = {}
    for r in rows:
        label = _segment_value(r, segment_by)
        partitions.setdefault(label, []).append(r)

    segments: dict[str, dict] = {}
    for label in sorted(partitions.keys()):
        seg_meta = {"strategy": strategy, segment_by: label}
        segments[label] = _gate_from_rows(
            source="trades",
            rows=partitions[label],
            segment=seg_meta,
            breakeven=breakeven,
            dsr_k=dsr_k,
            ref_now=ref_now,
            db_path=db_path,
            lookback_days=lookback_days,
        )

    # Empty-segments case: load returned no rows (e.g. filters cleared
    # everything, or strategy has no dry-run trades in the window). Don't
    # silently pass — a gate with no data is by construction unsafe.
    if not segments:
        return {
            "verdict": "FAIL",
            "passed": False,
            "source": "trades",
            "db_path": db_path,
            "strategy": strategy,
            "segmented_by": segment_by,
            "lookback_days": lookback_days,
            "filters": {
                "signal_source": filter_signal_source,
                "fill_model": filter_fill_model,
                "entry_band": filter_entry_band,
            },
            "segments": {},
            "reason": "no rows after loading + filtering",
            "generated_at": ref_now,
        }

    all_pass = all(v["passed"] for v in segments.values())
    return {
        "verdict": "PASS" if all_pass else "FAIL",
        "passed": all_pass,
        "source": "trades",
        "db_path": db_path,
        "strategy": strategy,
        "segmented_by": segment_by,
        "lookback_days": lookback_days,
        "filters": {
            "signal_source": filter_signal_source,
            "fill_model": filter_fill_model,
            "entry_band": filter_entry_band,
        },
        "segments": segments,
        "generated_at": ref_now,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def segment_key(verdict: dict) -> str:
    """Stable identifier for a gated strategy segment. Used as receipt filename prefix
    so verify callers can find the receipt for a given strategy without scanning.

    Base:
        trades_{strategy}             e.g. trades_signal_bot
        cycles_{asset}_{win}          e.g. cycles_btc_300

    Attribution suffix (trades only, Phase 4) — appended in a stable
    column order whenever the segment carries one of the new keys so
    segmented receipts don't collide in the same output dir:
        trades_signal_bot__signal_source=pair_arb
        trades_signal_bot__signal_source=pair_arb__entry_band=93-97
    """
    seg = verdict["segment"]
    if verdict["source"] == "trades":
        base = f"trades_{seg.get('strategy', 'unknown')}"
        parts = []
        for col in ("signal_source", "fill_model", "entry_band"):
            val = seg.get(col)
            if val:
                parts.append(f"{col}={val}")
        if parts:
            base = base + "__" + "__".join(parts)
        return base
    return f"cycles_{seg.get('asset', 'unknown')}_{seg.get('window_duration_secs', 0)}"


def write_receipt(verdict: dict, receipt_dir: str | Path) -> Path:
    """Write receipt JSON to {receipt_dir}/{segment_key}_{generated_at}.json.
    Creates the directory if missing. Returns the path written."""
    d = Path(receipt_dir)
    d.mkdir(parents=True, exist_ok=True)
    key = segment_key(verdict)
    ts = int(verdict["generated_at"])
    out = d / f"{key}_{ts}.json"
    payload = {"segment_key": key, **verdict}
    out.write_text(json.dumps(payload, indent=2, default=str))
    return out


def find_latest_receipt(receipt_dir: str | Path, seg_key: str) -> Optional[Path]:
    """Return newest receipt for seg_key in receipt_dir, or None if no match.
    Ordering is by generated_at (encoded in filename), not file mtime — mtime
    can be clobbered by scp / cp and we need a deterministic order."""
    d = Path(receipt_dir)
    if not d.is_dir():
        return None
    candidates = list(d.glob(f"{seg_key}_*.json"))
    if not candidates:
        return None
    def ts_of(p: Path) -> int:
        stem = p.stem
        # {seg_key}_{ts}; ts is the trailing integer after the last underscore.
        try:
            return int(stem.rsplit("_", 1)[1])
        except (ValueError, IndexError):
            return 0
    return max(candidates, key=ts_of)


def verify_receipt(
    receipt_dir: str | Path,
    seg_key: str,
    max_age_days: float,
    *,
    now: Optional[float] = None,
) -> dict:
    """Read the newest receipt for seg_key and judge whether it's usable.

    Returns dict with `ok: bool` and `reason: str`. `ok=True` requires:
      - Receipt exists.
      - Receipt generated within max_age_days.
      - Receipt verdict is PASS.

    This is the only check callers (predeploy.sh, LIFECYCLE enforcement) need.
    """
    ref_now = time.time() if now is None else now
    path = find_latest_receipt(receipt_dir, seg_key)
    if path is None:
        return {"ok": False, "reason": f"no receipt found for {seg_key} in {receipt_dir}",
                "path": None, "age_days": None, "verdict": None}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return {"ok": False, "reason": f"receipt unreadable: {e}",
                "path": str(path), "age_days": None, "verdict": None}
    age_secs = ref_now - float(data.get("generated_at", 0))
    age_days = age_secs / 86400.0
    verdict_str = data.get("verdict", "UNKNOWN")
    if age_days > max_age_days:
        return {"ok": False,
                "reason": f"receipt stale: {age_days:.1f}d > {max_age_days}d max",
                "path": str(path), "age_days": age_days, "verdict": verdict_str}
    if verdict_str != "PASS":
        return {"ok": False,
                "reason": f"receipt verdict is {verdict_str} (needs PASS)",
                "path": str(path), "age_days": age_days, "verdict": verdict_str}
    return {"ok": True, "reason": "fresh PASS",
            "path": str(path), "age_days": age_days, "verdict": "PASS"}


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


def _format_human_segmented(envelope: dict) -> str:
    """One-line summary per segment, then an overall tag. The per-segment
    block is the same shape as ``_format_human`` so operators can read a
    segmented verdict without learning a new format."""
    lines = []
    overall = "PASS" if envelope["passed"] else "FAIL"
    lines.append(
        f"[MTC gate] segmented verdict={overall}  "
        f"strategy={envelope['strategy']}  "
        f"segmented_by={envelope['segmented_by']}  "
        f"lookback={envelope['lookback_days']}d  "
        f"segments={len(envelope['segments'])}"
    )
    filters = envelope.get("filters") or {}
    active = {k: v for k, v in filters.items() if v}
    if active:
        lines.append(f"  filters: {active}")
    if not envelope["segments"]:
        lines.append(f"  (no segments -- {envelope.get('reason', 'empty after load')})")
        return "\n".join(lines)
    for label in sorted(envelope["segments"].keys()):
        seg_verdict = envelope["segments"][label]
        tag = "PASS" if seg_verdict["passed"] else "FAIL"
        lines.append(
            f"  [{tag}] {envelope['segmented_by']}={label}  "
            f"n={seg_verdict['n']} wins={seg_verdict['wins']}  "
            f"first_failure={seg_verdict['first_failure']}"
        )
        for c in seg_verdict["checks"]:
            mark = "PASS" if c["passed"] else "FAIL"
            lines.append(f"      {mark} {c['check']}: {c['reason']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Polyphemus MTC pre-deploy gate",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Run mode flags (used when --verify-receipt-dir NOT set)
    p.add_argument("--source", choices=["cycles", "trades"],
                   help="data source (run mode)")
    p.add_argument("--db", help="path to SQLite DB (run mode)")
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
    # Phase 4 attribution segmenting. Only meaningful for --source trades; the
    # cycles DB doesn't carry these columns yet.
    p.add_argument("--segment-by", default="",
                   choices=["", "signal_source", "fill_model", "entry_band"],
                   help="(trades only) partition rows by a column and emit a "
                        "verdict per partition (e.g. one per signal_source)")
    p.add_argument("--filter-signal-source", default="",
                   help="(trades only) restrict rows to this signal_source value")
    p.add_argument("--filter-fill-model", default="",
                   help="(trades only) restrict rows to this fill_model value")
    p.add_argument("--filter-entry-band", default="",
                   help="(trades only) restrict rows to this entry_band label "
                        "(00-55, 55-70, 70-85, 85-93, 93-97, 97+)")
    p.add_argument("--json", action="store_true",
                   help="emit JSON verdict on stdout (else pretty text on stdout)")
    p.add_argument("--write-receipt", default=None, metavar="DIR",
                   help="write verdict receipt JSON into DIR "
                        "(file name = {segment_key}_{generated_at}.json)")
    # Verify mode flags (used when --verify-receipt-dir IS set)
    p.add_argument("--verify-receipt-dir", default=None, metavar="DIR",
                   help="verify mode: read newest receipt in DIR, exit 0 if fresh PASS "
                        "else exit 1 with reason. Skips gate computation entirely.")
    p.add_argument("--segment-key", default=None,
                   help="(verify mode) segment key to find, e.g. trades_signal_bot or "
                        "cycles_btc_300")
    p.add_argument("--max-age-days", type=float, default=7.0,
                   help="(verify mode) max receipt age in days (default 7)")
    args = p.parse_args(argv)

    # Verify mode: skip gate, just check receipt freshness.
    if args.verify_receipt_dir is not None:
        if not args.segment_key:
            p.error("--verify-receipt-dir requires --segment-key")
        result = verify_receipt(
            receipt_dir=args.verify_receipt_dir,
            seg_key=args.segment_key,
            max_age_days=args.max_age_days,
        )
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            tag = "OK" if result["ok"] else "FAIL"
            print(f"[MTC verify] {tag} segment={args.segment_key}  {result['reason']}")
            if result["path"]:
                print(f"  receipt: {result['path']}")
                if result["age_days"] is not None:
                    print(f"  age: {result['age_days']:.2f}d "
                          f"(max {args.max_age_days}d)")
                print(f"  verdict: {result['verdict']}")
        return 0 if result["ok"] else 1

    # Run mode: validate required args that weren't declared required at top
    # (so verify mode can omit them).
    if not args.source or not args.db:
        p.error("run mode requires --source and --db")

    # Attribution flags are trades-only. Catch this at CLI parse time rather
    # than letting run_gate/run_segmented_gate fail deeper in the stack.
    attribution_args_used = bool(
        args.segment_by
        or args.filter_signal_source
        or args.filter_fill_model
        or args.filter_entry_band
    )
    if attribution_args_used and args.source != "trades":
        p.error("--segment-by and --filter-* flags require --source trades")

    if args.segment_by:
        envelope = run_segmented_gate(
            db_path=args.db,
            strategy=args.strategy,
            lookback_days=args.lookback_days,
            segment_by=args.segment_by,
            breakeven=args.breakeven,
            dsr_k=args.dsr_k,
            filter_signal_source=args.filter_signal_source,
            filter_fill_model=args.filter_fill_model,
            filter_entry_band=args.filter_entry_band,
        )
        if args.write_receipt:
            # Segmented receipts: one file per partition, so verify-mode
            # consumers (predeploy, LIFECYCLE) can target a specific
            # segment by key. Overall envelope is NOT receipted; a
            # `trades_signal_bot__signal_source=btc_momentum` style key
            # maps 1:1 to the `segment_key()` convention.
            for label, seg_verdict in envelope["segments"].items():
                receipt_path = write_receipt(seg_verdict, args.write_receipt)
                print(
                    f"[MTC gate] receipt written: {receipt_path}",
                    file=sys.stderr,
                )

        if args.json:
            print(json.dumps(envelope, indent=2, default=str))
        else:
            print(_format_human_segmented(envelope))

        return 0 if envelope["passed"] else 1

    verdict = run_gate(
        source=args.source,
        db_path=args.db,
        lookback_days=args.lookback_days,
        asset=args.asset,
        window_duration_secs=args.window_duration,
        strategy=args.strategy,
        breakeven=args.breakeven,
        dsr_k=args.dsr_k,
        filter_signal_source=args.filter_signal_source,
        filter_fill_model=args.filter_fill_model,
        filter_entry_band=args.filter_entry_band,
    )

    if args.write_receipt:
        receipt_path = write_receipt(verdict, args.write_receipt)
        # Stderr so it doesn't pollute --json stdout
        print(f"[MTC gate] receipt written: {receipt_path}", file=sys.stderr)

    if args.json:
        print(json.dumps(verdict, indent=2, default=str))
    else:
        print(_format_human(verdict))

    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
