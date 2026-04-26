#!/usr/bin/env python3
"""Evaluate the BTC 5m ensemble-selected go-live gate on cached shadow data."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

try:
    from . import backtester
    from . import dependency_audit_status
    from . import strategy_shadow_scan as shadow_scan
    from . import emmanuel_audit_mismatch_check as audit_check
    from . import security_best_practices_report
    from . import service_hardening_status
except ImportError:  # pragma: no cover - direct script execution
    import backtester
    import dependency_audit_status
    import strategy_shadow_scan as shadow_scan
    import emmanuel_audit_mismatch_check as audit_check
    import security_best_practices_report
    import service_hardening_status


ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / ".backtest_cache"
PROJECT_ROOT = ROOT.parent
DEFAULT_CONFIG_LABEL = "btc5m_shadow_lab_v3"
RESEARCH_ALIGNMENT_KEYS = [
    "CONFIG_LABEL",
    "ASSET_FILTER",
    "SHADOW_ASSETS",
    "MARKET_WINDOW_SECS",
    "MIN_ENTRY_PRICE",
    "MAX_ENTRY_PRICE",
    "MOMENTUM_TRIGGER_PCT",
    "MOMENTUM_WINDOW_SECS",
    "MOMENTUM_MAX_EPOCH_ELAPSED_SECS",
    "WHIPSAW_MAX_RATIO",
    "ENTRY_MODE",
    "SIGNAL_MODE",
    "ENABLE_WINDOW_DELTA",
    "WINDOW_DELTA_SHADOW",
    "WINDOW_DELTA_MAX_PRICE",
    "WINDOW_DELTA_LEAD_SECS",
    "ENABLE_RESOLUTION_SNIPE",
    "SNIPE_DRY_RUN",
    "ENABLE_BTC5M_EVIDENCE_VERDICTS",
    "BTC5M_EVIDENCE_MODE",
    "ENABLE_BTC5M_ENSEMBLE_SHADOW",
    "BTC5M_ENSEMBLE_MODE",
    "BTC5M_ENSEMBLE_ADMISSION_ENABLED",
    "BTC5M_ENSEMBLE_ADMISSION_MODE",
]


@dataclass
class InstanceStats:
    instance: str
    config_era: str
    overlap_start: float
    overlap_end: float
    runtime_hours: float
    expected_epochs: int
    observed_epochs: int
    epoch_coverage_rate: float
    longest_gap_hours: float
    signal_count: int
    ensemble_resolved_count: int
    passed_candidates: int
    placement_failures: int
    fill_timeouts: int
    pipeline_stall_windows: int
    recent_btc_trades: int


@dataclass
class StrategyGateMetrics:
    result: shadow_scan.StrategyResult
    avg_live_net: float
    live_net_roi: float
    live_max_drawdown: float
    live_worst_rolling5_loss: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instances", nargs="+", default=["emmanuel", "polyphemus"])
    parser.add_argument("--config-label", default=DEFAULT_CONFIG_LABEL)
    parser.add_argument("--config-era", default="")
    parser.add_argument("--hours-required", type=float, default=48.0)
    parser.add_argument("--journal-clean", choices=["yes", "no", "unknown"], default="unknown")
    parser.add_argument("--config-drift-clean", choices=["yes", "no", "unknown"], default="unknown")
    parser.add_argument("--open-crit-count", type=int, default=-1)
    parser.add_argument("--progress-path", type=Path, default=PROJECT_ROOT / "PROGRESS.md")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def iso_ts(value: Optional[float]) -> str:
    if value is None:
        return "unknown"
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def read_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def safe_float(raw: str, default: float = 0.0) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def choose_planned_trade_cap(env_values: Dict[str, str]) -> float:
    caps = []
    max_bet = safe_float(env_values.get("MAX_BET"), 0.0)
    if max_bet > 0:
        caps.append(max_bet)
    max_trade_amount = safe_float(env_values.get("MAX_TRADE_AMOUNT"), 0.0)
    if max_trade_amount > 0:
        caps.append(max_trade_amount)
    if not caps:
        return 20.0
    return min(caps)


def compute_research_alignment_fingerprint(env_values: Dict[str, str]) -> str:
    payload = {key.lower(): env_values.get(key, "") for key in RESEARCH_ALIGNMENT_KEYS}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return digest[:12]


def get_db_path(instance: str) -> Path:
    return CACHE_DIR / instance / "signals.db"


def get_perf_db_path(instance: str) -> Path:
    return CACHE_DIR / instance / "performance.db"


def get_epochs(instance: str, config_label: str, config_era: str = "") -> List[int]:
    db_path = get_db_path(instance)
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(epoch_coverage)").fetchall()}
        config_label_expr = "COALESCE(config_label, '')" if "config_label" in columns else "''"
        config_era_expr = "COALESCE(config_era, '')" if "config_era" in columns else "''"
        params: List[object] = ["BTC", 300, config_label]
        sql = f"""
            SELECT DISTINCT epoch
            FROM epoch_coverage
            WHERE asset = ? AND window_secs = ? AND {config_label_expr} = ?
        """
        if config_era and "config_era" in columns:
            sql += f" AND {config_era_expr} = ?"
            params.append(config_era)
        sql += " ORDER BY epoch ASC"
        rows = conn.execute(sql, params).fetchall()
        return [int(row[0]) for row in rows if row[0] is not None]
    finally:
        conn.close()


def get_common_config_era(instances: Iterable[str], config_label: str) -> str:
    era_sets = []
    for instance in instances:
        db_path = get_db_path(instance)
        if not db_path.exists():
            return ""
        conn = sqlite3.connect(str(db_path))
        try:
            coverage_columns = {row[1] for row in conn.execute("PRAGMA table_info(epoch_coverage)").fetchall()}
            if "config_era" in coverage_columns:
                config_label_expr = "COALESCE(config_label, '')" if "config_label" in coverage_columns else "''"
                rows = conn.execute(
                    f"""
                    SELECT COALESCE(config_era, '') AS config_era, MAX(epoch) AS max_epoch
                    FROM epoch_coverage
                    WHERE asset = 'BTC' AND window_secs = 300
                      AND {config_label_expr} = ?
                      AND COALESCE(config_era, '') != ''
                    GROUP BY COALESCE(config_era, '')
                    ORDER BY max_epoch DESC
                    """,
                    (config_label,),
                ).fetchall()
            else:
                signal_columns = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}
                if "config_era" not in signal_columns:
                    return ""
                config_label_expr = "COALESCE(config_label, '')" if "config_label" in signal_columns else "''"
                rows = conn.execute(
                    f"""
                    SELECT COALESCE(config_era, '') AS config_era, MAX(epoch) AS max_epoch
                    FROM signals
                    WHERE asset = 'BTC' AND market_window_secs = 300
                      AND {config_label_expr} = ?
                      AND COALESCE(config_era, '') != ''
                    GROUP BY COALESCE(config_era, '')
                    ORDER BY max_epoch DESC
                    """,
                    (config_label,),
                ).fetchall()
            era_sets.append({row[0]: row[1] for row in rows})
        finally:
            conn.close()
    common = set(era_sets[0].keys())
    for eras in era_sets[1:]:
        common &= set(eras.keys())
    if not common:
        return ""
    return max(common, key=lambda era: min(eras.get(era, 0) for eras in era_sets))


def get_instance_latest_config_era(instance: str, config_label: str) -> str:
    db_path = get_db_path(instance)
    if not db_path.exists():
        return ""
    conn = sqlite3.connect(str(db_path))
    try:
        coverage_columns = {row[1] for row in conn.execute("PRAGMA table_info(epoch_coverage)").fetchall()}
        if "config_era" in coverage_columns:
            config_label_expr = "COALESCE(config_label, '')" if "config_label" in coverage_columns else "''"
            row = conn.execute(
                f"""
                SELECT COALESCE(config_era, '')
                FROM epoch_coverage
                WHERE asset = 'BTC' AND window_secs = 300
                  AND {config_label_expr} = ?
                  AND COALESCE(config_era, '') != ''
                ORDER BY epoch DESC
                LIMIT 1
                """,
                (config_label,),
            ).fetchone()
            if row and row[0]:
                return str(row[0])
        signal_columns = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}
        if "config_era" not in signal_columns:
            return ""
        config_label_expr = "COALESCE(config_label, '')" if "config_label" in signal_columns else "''"
        row = conn.execute(
            f"""
            SELECT COALESCE(config_era, '')
            FROM signals
            WHERE asset = 'BTC' AND market_window_secs = 300
              AND {config_label_expr} = ?
              AND COALESCE(config_era, '') != ''
            ORDER BY epoch DESC
            LIMIT 1
            """,
            (config_label,),
        ).fetchone()
        return str(row[0]) if row and row[0] else ""
    finally:
        conn.close()


def get_research_alignment_context(instances: Iterable[str], config_label: str) -> dict:
    env_fingerprints: Dict[str, str] = {}
    instance_eras: Dict[str, str] = {}
    for instance in instances:
        env_values = read_env_file(CACHE_DIR / instance / ".env")
        if not env_values:
            return {
                "shared_research_era": "",
                "instance_config_eras": {},
                "reason": f"missing cached .env for {instance}",
            }
        env_fingerprints[instance] = compute_research_alignment_fingerprint(env_values)
        instance_eras[instance] = get_instance_latest_config_era(instance, config_label)
    if len(set(env_fingerprints.values())) != 1:
        return {
            "shared_research_era": "",
            "instance_config_eras": instance_eras,
            "reason": "research-relevant .env settings differ across instances",
        }
    if any(not era for era in instance_eras.values()):
        return {
            "shared_research_era": "",
            "instance_config_eras": instance_eras,
            "reason": "one or more instances have no logged config_era for the requested config_label",
        }
    return {
        "shared_research_era": next(iter(env_fingerprints.values())),
        "instance_config_eras": instance_eras,
        "reason": "",
    }


def expected_epoch_count(start_epoch: int, end_epoch: int) -> int:
    if end_epoch < start_epoch:
        return 0
    return int(((end_epoch - start_epoch) // 300) + 1)


def longest_gap_hours(epochs: List[int]) -> float:
    if len(epochs) < 2:
        return 0.0
    largest_gap = max(max(0, epochs[idx] - epochs[idx - 1] - 300) for idx in range(1, len(epochs)))
    return largest_gap / 3600.0


def count_signals(instance: str, config_label: str, config_era: str, start_epoch: int, end_epoch: int) -> int:
    db_path = get_db_path(instance)
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM signals
            WHERE asset = 'BTC' AND market_window_secs = 300
              AND COALESCE(config_label, '') = ?
              AND COALESCE(config_era, '') = ?
              AND epoch BETWEEN ? AND ?
            """,
            (config_label, config_era, start_epoch, end_epoch),
        ).fetchone()
        return int(row[0] or 0)
    finally:
        conn.close()


def count_recent_trades(instance: str, start_epoch: int, end_epoch: int) -> int:
    db_path = get_perf_db_path(instance)
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM trades
            WHERE slug LIKE 'btc-updown-5m-%'
              AND entry_time BETWEEN ? AND ?
            """,
            (start_epoch, end_epoch + 300),
        ).fetchone()
        return int(row[0] or 0)
    finally:
        conn.close()


def classify_execution_failures(instance: str, config_label: str, config_era: str, start_epoch: int, end_epoch: int) -> tuple[int, int]:
    db_path = get_db_path(instance)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT COALESCE(pipeline_detail, '') AS detail
            FROM signals
            WHERE asset = 'BTC' AND market_window_secs = 300
              AND source IN ('binance_momentum', 'sharp_move')
              AND COALESCE(config_label, '') = ?
              AND COALESCE(config_era, '') = ?
              AND epoch BETWEEN ? AND ?
              AND COALESCE(shadow_ensemble_selected, 0) = 1
              AND COALESCE(pipeline_stage, '') = 'execution'
              AND COALESCE(pipeline_status, '') = 'failed'
            """,
            (config_label, config_era, start_epoch, end_epoch),
        ).fetchall()
    finally:
        conn.close()
    placement = 0
    timeout = 0
    for row in rows:
        detail = (row["detail"] or "").lower()
        if "timeout" in detail:
            timeout += 1
        else:
            placement += 1
    return placement, timeout


def count_passed_candidates(instance: str, config_label: str, config_era: str, start_epoch: int, end_epoch: int) -> int:
    db_path = get_db_path(instance)
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM signals
            WHERE asset = 'BTC' AND market_window_secs = 300
              AND source IN ('binance_momentum', 'sharp_move')
              AND COALESCE(config_label, '') = ?
              AND COALESCE(config_era, '') = ?
              AND epoch BETWEEN ? AND ?
              AND guard_passed = 1
              AND COALESCE(shadow_ensemble_selected, 0) = 1
            """,
            (config_label, config_era, start_epoch, end_epoch),
        ).fetchone()
        return int(row[0] or 0)
    finally:
        conn.close()


def count_pipeline_stall_windows(instance: str, config_label: str, config_era: str, start_epoch: int, end_epoch: int) -> int:
    epochs = get_epochs(instance, config_label, config_era)
    epochs = [epoch for epoch in epochs if start_epoch <= epoch <= end_epoch]
    if not epochs:
        return 0
    db_path = get_db_path(instance)
    conn = sqlite3.connect(str(db_path))
    try:
        windows = 0
        cursor = start_epoch
        while cursor + 3600 <= end_epoch:
            window_end = cursor + 3600
            coverage_count = sum(1 for epoch in epochs if cursor <= epoch < window_end)
            if coverage_count >= 10:
                row = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM signals
                    WHERE asset = 'BTC' AND market_window_secs = 300
                      AND COALESCE(config_label, '') = ?
                      AND COALESCE(config_era, '') = ?
                      AND epoch BETWEEN ? AND ?
                    """,
                    (config_label, config_era, cursor, window_end),
                ).fetchone()
                if int(row[0] or 0) == 0:
                    windows += 1
            cursor += 300
        return windows
    finally:
        conn.close()


def get_dashboard_field_support() -> bool:
    dashboard_path = PROJECT_ROOT / "dashboard.py"
    text = dashboard_path.read_text(encoding="utf-8")
    required = [
        "passed_btc_candidates",
        "placement_failures",
        "fill_timeouts",
        "retry_recovered",
        "retry_skip_reasons",
    ]
    return all(field in text for field in required)


def detect_journal_clean() -> tuple[str, str]:
    cmd = [
        "ssh",
        *backtester.ssh_transport_args(),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "root@82.24.19.114",
        "journalctl -u lagbot@emmanuel --since '6 hours ago' --no-pager | grep -E '\\[ERROR\\]|\\[CRITICAL\\]|Traceback|Exception' | tail -n 20",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return "unknown", result.stderr.strip() or "ssh/journal access failed"
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if lines:
        return "no", lines[-1]
    return "yes", "no recent error or traceback lines in last 6h"


def detect_config_drift_clean(config_label: str, instance_eras: Dict[str, str]) -> tuple[str, str]:
    env_values = read_env_file(CACHE_DIR / "emmanuel" / ".env")
    if not env_values:
        return "unknown", "missing cached emmanuel .env"
    if env_values.get("CONFIG_LABEL", "") not in {"", config_label}:
        return "no", f"cached emmanuel CONFIG_LABEL={env_values.get('CONFIG_LABEL')} != {config_label}"
    if not instance_eras.get("emmanuel"):
        return "no", "emmanuel has no logged config_era for the requested config label"
    required_keys = [
        "ASSET_FILTER",
        "MARKET_WINDOW_SECS",
        "SIGNAL_MODE",
        "MAX_ENTRY_PRICE",
        "MOMENTUM_MAX_EPOCH_ELAPSED_SECS",
    ]
    missing = [key for key in required_keys if key not in env_values]
    if missing:
        return "no", f"cached emmanuel .env missing keys: {', '.join(missing)}"
    return "yes", "cached emmanuel .env matches the requested config label and required strategy keys"


def detect_emmanuel_audit_clean(progress_path: Path) -> tuple[bool, str]:
    try:
        status = audit_check.main_for_status()
    except Exception:
        status = None
    if status:
        if status.get("state") == "pass":
            return True, "live_audit_pass"
        if status.get("state") == "fail":
            return False, "live_audit_fail"
    if not progress_path.exists():
        return False, "progress_file_missing"
    text = progress_path.read_text(encoding="utf-8")
    if "CLOB↔DB trade audit FAILED" in text and "still logs the pre-existing" in text:
        return False, "clob_db_audit_unresolved"
    return True, "no_unresolved_audit_marker"


def build_instance_stats(
    instance: str,
    config_label: str,
    config_era: str,
    overlap_start: int,
    overlap_end: int,
    resolutions: Dict[str, str],
) -> InstanceStats:
    epochs = [epoch for epoch in get_epochs(instance, config_label, config_era) if overlap_start <= epoch <= overlap_end]
    expected = expected_epoch_count(overlap_start, overlap_end)
    signal_count = count_signals(instance, config_label, config_era, overlap_start, overlap_end)
    passed_candidates = count_passed_candidates(instance, config_label, config_era, overlap_start, overlap_end)
    placement_failures, fill_timeouts = classify_execution_failures(
        instance, config_label, config_era, overlap_start, overlap_end
    )
    candidates = [
        candidate
        for candidate in shadow_scan.load_candidates(instance)
        if candidate.config_label == config_label
        and candidate.config_era == config_era
        and overlap_start <= candidate.epoch <= overlap_end
        and candidate.slug in resolutions
        and candidate.shadow_ensemble_selected == 1
    ]
    return InstanceStats(
        instance=instance,
        config_era=config_era,
        overlap_start=overlap_start,
        overlap_end=overlap_end,
        runtime_hours=max(0.0, (overlap_end - overlap_start) / 3600.0),
        expected_epochs=expected,
        observed_epochs=len(epochs),
        epoch_coverage_rate=(len(epochs) / expected) if expected else 0.0,
        longest_gap_hours=longest_gap_hours(epochs),
        signal_count=signal_count,
        ensemble_resolved_count=len(candidates),
        passed_candidates=passed_candidates,
        placement_failures=placement_failures,
        fill_timeouts=fill_timeouts,
        pipeline_stall_windows=count_pipeline_stall_windows(instance, config_label, config_era, overlap_start, overlap_end),
        recent_btc_trades=count_recent_trades(instance, overlap_start, overlap_end),
    )


def scale_result_to_live(result: shadow_scan.StrategyResult, trade_cap: float) -> StrategyGateMetrics:
    live_pnls: List[float] = []
    live_costs: List[float] = []
    for net_pnl, cost in zip(result.net_pnl_series, result.cost_series):
        if cost <= 0:
            continue
        shares = trade_cap / cost
        live_pnls.append(net_pnl * shares)
        live_costs.append(trade_cap)
    total_live_pnl = sum(live_pnls)
    total_live_cost = sum(live_costs)
    avg_live_net = total_live_pnl / len(live_pnls) if live_pnls else 0.0
    live_net_roi = total_live_pnl / total_live_cost if total_live_cost > 0 else 0.0
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for pnl in live_pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    rolling_5 = 0.0
    for idx in range(len(live_pnls)):
        rolling_5 = min(rolling_5, sum(live_pnls[idx:idx + 5]))
    return StrategyGateMetrics(
        result=result,
        avg_live_net=avg_live_net,
        live_net_roi=live_net_roi,
        live_max_drawdown=max_drawdown,
        live_worst_rolling5_loss=abs(rolling_5),
    )


def make_profile(name: str, description: str, predicate):
    return shadow_scan.StrategyProfile(
        name=name,
        principle=description,
        description=description,
        predicate=predicate,
        ranker=shadow_scan.base_score,
    )


def format_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def format_money(value: float) -> str:
    return f"${value:.3f}"


def evaluate_gates(
    instances: List[InstanceStats],
    strategy_metrics: StrategyGateMetrics,
    benchmark_metrics: StrategyGateMetrics,
    *,
    hours_required: float,
    max_daily_loss: float,
    audit_clean: bool,
    audit_reason: str,
    journal_clean: str,
    config_drift_clean: str,
    dashboard_fields_supported: bool,
    security_status: dict,
    dependency_status: dict,
    service_status: dict,
) -> List[str]:
    blockers: List[str] = []
    coverage_rates = [item.epoch_coverage_rate for item in instances]
    for item in instances:
        if item.runtime_hours < hours_required:
            blockers.append(f"{item.instance}: runtime {item.runtime_hours:.1f}h < {hours_required:.1f}h")
        if item.epoch_coverage_rate < 0.90:
            blockers.append(f"{item.instance}: epoch coverage {format_pct(item.epoch_coverage_rate)} < 90.0%")
        if item.signal_count < 30:
            blockers.append(f"{item.instance}: signal count {item.signal_count} < 30")
        if item.ensemble_resolved_count < 10:
            blockers.append(f"{item.instance}: resolved ensemble rows {item.ensemble_resolved_count} < 10")
        if item.longest_gap_hours > 2.0:
            blockers.append(f"{item.instance}: silent coverage gap {item.longest_gap_hours:.2f}h > 2.00h")
        if item.pipeline_stall_windows > 1:
            blockers.append(f"{item.instance}: unexplained pipeline stall windows {item.pipeline_stall_windows} > 1")
    if coverage_rates and (max(coverage_rates) - min(coverage_rates)) > 0.05:
        blockers.append(
            f"cross-instance coverage drift {format_pct(max(coverage_rates) - min(coverage_rates))} > 5.0%"
        )
    result = strategy_metrics.result
    benchmark = benchmark_metrics.result
    if result.trades < 30:
        blockers.append(f"ensemble_selected_live_v1: sample size {result.trades} < 30")
    if result.win_rate < 0.70:
        blockers.append(f"ensemble_selected_live_v1: net WR {format_pct(result.win_rate)} < 70.0%")
    if strategy_metrics.avg_live_net <= 0.03:
        blockers.append(f"ensemble_selected_live_v1: net expectancy {format_money(strategy_metrics.avg_live_net)} <= $0.030")
    if strategy_metrics.live_net_roi < 0.05:
        blockers.append(f"ensemble_selected_live_v1: live net ROI {format_pct(strategy_metrics.live_net_roi)} < 5.0%")
    if not (
        (strategy_metrics.avg_live_net - benchmark_metrics.avg_live_net) >= 0.02
        or (strategy_metrics.live_net_roi - benchmark_metrics.live_net_roi) >= 0.02
    ):
        blockers.append("ensemble_selected_live_v1: does not beat current_guarded by required margin")
    if max_daily_loss > 0:
        if strategy_metrics.live_max_drawdown > (0.75 * max_daily_loss):
            blockers.append(
                f"ensemble_selected_live_v1: scaled max drawdown {format_money(strategy_metrics.live_max_drawdown)} > 75% of MAX_DAILY_LOSS"
            )
        if strategy_metrics.live_worst_rolling5_loss > (0.50 * max_daily_loss):
            blockers.append(
                f"ensemble_selected_live_v1: worst rolling 5-trade loss {format_money(strategy_metrics.live_worst_rolling5_loss)} > 50% of MAX_DAILY_LOSS"
            )
    emmanuel = next((item for item in instances if item.instance == "emmanuel"), None)
    if emmanuel:
        if emmanuel.passed_candidates < 10:
            blockers.append(f"emmanuel: passed ensemble-selected BTC candidates {emmanuel.passed_candidates} < 10")
        if emmanuel.passed_candidates > 0:
            placement_rate = emmanuel.placement_failures / emmanuel.passed_candidates
            timeout_rate = emmanuel.fill_timeouts / emmanuel.passed_candidates
            if placement_rate > 0.10:
                blockers.append(f"emmanuel: placement failure rate {format_pct(placement_rate)} > 10.0%")
            if timeout_rate > 0.15:
                blockers.append(f"emmanuel: fill-timeout rate {format_pct(timeout_rate)} > 15.0%")
    if not audit_clean:
        blockers.append(f"emmanuel audit: {audit_reason}")
    if journal_clean != "yes":
        blockers.append(f"journal check: {journal_clean}")
    if config_drift_clean != "yes":
        blockers.append(f"config drift check: {config_drift_clean}")
    if not dashboard_fields_supported:
        blockers.append("dashboard missing required pipeline fields")
    if security_status.get("verdict") != "pass":
        blockers.append("security audit has unresolved critical/high findings")
    if dependency_status.get("verdict") != "pass":
        blockers.append("dependency audit has unresolved blocking findings")
    if service_status.get("verdict") != "pass":
        blockers.append("service hardening baseline is incomplete")
    return blockers


def build_report(
    *,
    config_label: str,
    config_era: str,
    overlap_start: int,
    overlap_end: int,
    trade_cap: float,
    max_daily_loss: float,
    instances: List[InstanceStats],
    strategy_metrics: StrategyGateMetrics,
    benchmark_metrics: StrategyGateMetrics,
    blockers: List[str],
    audit_clean: bool,
    audit_reason: str,
    journal_clean: str,
    config_drift_clean: str,
    dashboard_fields_supported: bool,
    security_status: dict,
    dependency_status: dict,
    service_status: dict,
) -> str:
    coverage_rates = [item.epoch_coverage_rate for item in instances]
    drift = (max(coverage_rates) - min(coverage_rates)) if coverage_rates else 0.0
    decision = "GO" if not blockers else "NO-GO"
    lines = [
        "# BTC 5m Ensemble-Selected Go-Live Gate",
        "",
        f"- Generated: `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}`",
        f"- Config label: `{config_label}`",
        f"- Research-aligned era: `{config_era or 'unknown'}`",
        f"- Overlap window: `{iso_ts(overlap_start)}` -> `{iso_ts(overlap_end)}`",
        f"- Planned live spend assumption: `{format_money(trade_cap)}` per trade (derived from cached hard caps)",
        f"- MAX_DAILY_LOSS assumption: `{format_money(max_daily_loss)}`",
        "",
        "## Instance Integrity",
        "",
    ]
    for item in instances:
        lines.extend([
            f"### {item.instance}",
            f"- Runtime window: `{item.runtime_hours:.1f}h`",
            f"- Epoch coverage: `{item.observed_epochs}/{item.expected_epochs}` = `{format_pct(item.epoch_coverage_rate)}`",
            f"- Longest epoch gap: `{item.longest_gap_hours:.2f}h`",
            f"- BTC tagged signals: `{item.signal_count}`",
            f"- Resolved ensemble-selected rows: `{item.ensemble_resolved_count}`",
            f"- Passed ensemble-selected BTC candidates: `{item.passed_candidates}`",
            f"- Placement failures: `{item.placement_failures}`",
            f"- Fill timeouts: `{item.fill_timeouts}`",
            f"- Pipeline stall windows (>60m with coverage but no BTC signals): `{item.pipeline_stall_windows}`",
            f"- Recent BTC trades in overlap window: `{item.recent_btc_trades}`",
            "",
        ])
    lines.extend([
        f"- Cross-instance epoch coverage drift: `{format_pct(drift)}`",
        "",
        "## Strategy Comparison",
        "",
        "| Slice | Trades | WR | Avg Net (1 share) | Live Avg Net | Live ROI | Live Max DD | Live Worst Rolling 5 | R8 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        "| ensemble_selected_live_v1 | {trades} | {wr} | {avg} | {live_avg} | {roi} | {dd} | {r5} | {r8} |".format(
            trades=strategy_metrics.result.trades,
            wr=format_pct(strategy_metrics.result.win_rate),
            avg=format_money(strategy_metrics.result.avg_net_pnl),
            live_avg=format_money(strategy_metrics.avg_live_net),
            roi=format_pct(strategy_metrics.live_net_roi),
            dd=format_money(strategy_metrics.live_max_drawdown),
            r5=format_money(strategy_metrics.live_worst_rolling5_loss),
            r8=strategy_metrics.result.r8,
        ),
        "| current_guarded | {trades} | {wr} | {avg} | {live_avg} | {roi} | {dd} | {r5} | {r8} |".format(
            trades=benchmark_metrics.result.trades,
            wr=format_pct(benchmark_metrics.result.win_rate),
            avg=format_money(benchmark_metrics.result.avg_net_pnl),
            live_avg=format_money(benchmark_metrics.avg_live_net),
            roi=format_pct(benchmark_metrics.live_net_roi),
            dd=format_money(benchmark_metrics.live_max_drawdown),
            r5=format_money(benchmark_metrics.live_worst_rolling5_loss),
            r8=benchmark_metrics.result.r8,
        ),
        "",
        "## Execution Readiness",
        "",
        f"- Promoted slice: `BTC / 5m / binance_momentum / shadow_ensemble_selected=1`",
        f"- Emmanuel audit clean: `{audit_clean}` ({audit_reason})",
        f"- Journal clean verified: `{journal_clean}`",
        f"- Config drift verified: `{config_drift_clean}`",
        f"- Dashboard pipeline fields present in code: `{dashboard_fields_supported}`",
        f"- Retry-shadow skip reasons: `not persisted by config_era yet; use live dashboard for current process counters`",
        "",
        "## Security Readiness",
        "",
        f"- Security audit verdict: `{security_status.get('verdict', 'unknown')}`",
        f"- Dependency audit verdict: `{dependency_status.get('verdict', 'unknown')}`",
        f"- Service hardening verdict: `{service_status.get('verdict', 'unknown')}`",
        "",
        "## Decision",
        "",
        f"- Verdict: `{decision}`",
    ])
    if blockers:
        lines.append("- Blockers:")
        for blocker in blockers:
            lines.append(f"  - `{blocker}`")
    else:
        lines.append("- Blockers: `none`")
    return "\n".join(lines)


def build_no_data_report(config_label: str, reason: str) -> str:
    return "\n".join(
        [
            "# BTC 5m Ensemble-Selected Go-Live Gate",
            "",
            f"- Generated: `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}`",
            f"- Config label: `{config_label}`",
            "",
            "## Decision",
            "",
            "- Verdict: `NO-GO`",
            "- Blockers:",
            f"  - `{reason}`",
            "",
            "## Interpretation",
            "",
            "- The cached local data does not yet contain a research-aligned post-fix window for both instances.",
            "- That means the gate cannot legally evaluate the promotion window yet.",
            "- Next action: refresh the local caches from both instances after the aligned post-fix shadow window has accumulated enough data.",
        ]
    )


def build_no_data_status(config_label: str, reason: str) -> dict:
    return {
        "verdict": "NO-GO",
        "config_label": config_label,
        "config_era": "",
        "window_start": None,
        "window_end": None,
        "blockers": [reason],
        "gates": {
            "sensor_integrity": {"status": "blocked", "reason": reason},
            "strategy_performance": {"status": "blocked", "reason": "no aligned data"},
            "execution_readiness": {"status": "blocked", "reason": "no aligned data"},
            "operational_readiness": {"status": "blocked", "reason": "no aligned data"},
            "security_readiness": {"status": "blocked", "reason": "no aligned data"},
        },
    }


def build_gate_status(
    *,
    config_label: str,
    config_era: str,
    overlap_start: int,
    overlap_end: int,
    trade_cap: float,
    max_daily_loss: float,
    instances: List[InstanceStats],
    strategy_metrics: StrategyGateMetrics,
    benchmark_metrics: StrategyGateMetrics,
    blockers: List[str],
    audit_clean: bool,
    audit_reason: str,
    journal_clean: str,
    config_drift_clean: str,
    dashboard_fields_supported: bool,
    security_status: dict,
    dependency_status: dict,
    service_status: dict,
) -> dict:
    emmanuel = next((item for item in instances if item.instance == "emmanuel"), None)
    sensor_ok = all(
        item.runtime_hours >= 48.0
        and item.epoch_coverage_rate >= 0.90
        and item.signal_count >= 30
        and item.ensemble_resolved_count >= 10
        and item.longest_gap_hours <= 2.0
        and item.pipeline_stall_windows <= 1
        for item in instances
    )
    strategy_ok = (
        strategy_metrics.result.trades >= 30
        and strategy_metrics.result.win_rate >= 0.70
        and strategy_metrics.avg_live_net > 0.03
        and strategy_metrics.live_net_roi >= 0.05
        and (
            (strategy_metrics.avg_live_net - benchmark_metrics.avg_live_net) >= 0.02
            or (strategy_metrics.live_net_roi - benchmark_metrics.live_net_roi) >= 0.02
        )
        and (
            max_daily_loss <= 0
            or (
                strategy_metrics.live_max_drawdown <= (0.75 * max_daily_loss)
                and strategy_metrics.live_worst_rolling5_loss <= (0.50 * max_daily_loss)
            )
        )
    )
    execution_ok = bool(
        emmanuel
        and emmanuel.passed_candidates >= 10
        and (emmanuel.placement_failures / emmanuel.passed_candidates if emmanuel.passed_candidates else 0.0) <= 0.10
        and (emmanuel.fill_timeouts / emmanuel.passed_candidates if emmanuel.passed_candidates else 0.0) <= 0.15
    )
    operational_ok = (
        audit_clean
        and journal_clean == "yes"
        and config_drift_clean == "yes"
        and dashboard_fields_supported
    )
    security_ok = (
        security_status.get("verdict") == "pass"
        and dependency_status.get("verdict") == "pass"
        and service_status.get("verdict") == "pass"
    )
    return {
        "verdict": "GO" if not blockers else "NO-GO",
        "config_label": config_label,
        "config_era": config_era,
        "window_start": overlap_start,
        "window_end": overlap_end,
        "trade_cap": trade_cap,
        "max_daily_loss": max_daily_loss,
        "blockers": blockers,
        "instances": [
            {
                "instance": item.instance,
                "runtime_hours": item.runtime_hours,
                "expected_epochs": item.expected_epochs,
                "observed_epochs": item.observed_epochs,
                "epoch_coverage_rate": item.epoch_coverage_rate,
                "longest_gap_hours": item.longest_gap_hours,
                "signal_count": item.signal_count,
                "ensemble_resolved_count": item.ensemble_resolved_count,
                "passed_candidates": item.passed_candidates,
                "placement_failures": item.placement_failures,
                "fill_timeouts": item.fill_timeouts,
                "pipeline_stall_windows": item.pipeline_stall_windows,
                "recent_btc_trades": item.recent_btc_trades,
            }
            for item in instances
        ],
        "comparison": {
            "ensemble_selected_live_v1": {
                "trades": strategy_metrics.result.trades,
                "win_rate": strategy_metrics.result.win_rate,
                "avg_net_1share": strategy_metrics.result.avg_net_pnl,
                "avg_net_live": strategy_metrics.avg_live_net,
                "live_roi": strategy_metrics.live_net_roi,
                "live_max_drawdown": strategy_metrics.live_max_drawdown,
                "live_worst_rolling5_loss": strategy_metrics.live_worst_rolling5_loss,
                "r8": strategy_metrics.result.r8,
            },
            "current_guarded": {
                "trades": benchmark_metrics.result.trades,
                "win_rate": benchmark_metrics.result.win_rate,
                "avg_net_1share": benchmark_metrics.result.avg_net_pnl,
                "avg_net_live": benchmark_metrics.avg_live_net,
                "live_roi": benchmark_metrics.live_net_roi,
                "live_max_drawdown": benchmark_metrics.live_max_drawdown,
                "live_worst_rolling5_loss": benchmark_metrics.live_worst_rolling5_loss,
                "r8": benchmark_metrics.result.r8,
            },
        },
        "gates": {
            "sensor_integrity": {"status": "pass" if sensor_ok else "fail"},
            "strategy_performance": {"status": "pass" if strategy_ok else "fail"},
            "execution_readiness": {"status": "pass" if execution_ok else "fail"},
            "operational_readiness": {
                "status": "pass" if operational_ok else "fail",
                "audit_clean": audit_clean,
                "audit_reason": audit_reason,
                "journal_clean": journal_clean,
                "config_drift_clean": config_drift_clean,
                "dashboard_fields_supported": dashboard_fields_supported,
            },
            "security_readiness": {
                "status": "pass" if security_ok else "fail",
                "security_audit_verdict": security_status.get("verdict", "unknown"),
                "dependency_audit_verdict": dependency_status.get("verdict", "unknown"),
                "service_hardening_verdict": service_status.get("verdict", "unknown"),
                "critical_blockers": security_status.get("critical_blockers", []),
                "dependency_blockers": dependency_status.get("blocking_findings", []),
                "service_missing_controls": service_status.get("missing_controls", []),
            },
        },
        "security_audit_status": security_status,
        "dependency_audit_status": dependency_status,
        "service_hardening_status": service_status,
    }


def main() -> int:
    args = parse_args()
    if args.config_era:
        config_era = args.config_era
        instance_eras = {instance: args.config_era for instance in args.instances}
        no_data_reason = ""
    else:
        alignment = get_research_alignment_context(args.instances, args.config_label)
        config_era = alignment["shared_research_era"]
        instance_eras = alignment["instance_config_eras"]
        no_data_reason = alignment["reason"] or "no aligned research era found for the requested instances/config_label"
    output_path = args.output
    if output_path is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = PROJECT_ROOT / "dario_output" / f"btc5m_ensemble_go_live_gate_{stamp}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    json_out = args.json_out
    if json_out is None:
        json_out = output_path.with_suffix(".json")
    if not config_era:
        status = build_no_data_status(
            args.config_label,
            no_data_reason,
        )
        output_path.write_text(
            build_no_data_report(args.config_label, no_data_reason),
            encoding="utf-8",
        )
        json_out.write_text(json.dumps(status, indent=2, sort_keys=True), encoding="utf-8")
        if args.print_json:
            print(json.dumps(status, indent=2, sort_keys=True))
        print(output_path)
        return 0

    epoch_sets = {
        instance: get_epochs(instance, args.config_label, instance_eras[instance])
        for instance in args.instances
    }
    if any(not epochs for epochs in epoch_sets.values()):
        status = build_no_data_status(
            args.config_label,
            "one or more instances have no BTC epoch_coverage rows for the selected research-aligned era",
        )
        output_path.write_text(
            build_no_data_report(args.config_label, "one or more instances have no BTC epoch_coverage rows for the selected research-aligned era"),
            encoding="utf-8",
        )
        json_out.write_text(json.dumps(status, indent=2, sort_keys=True), encoding="utf-8")
        if args.print_json:
            print(json.dumps(status, indent=2, sort_keys=True))
        print(output_path)
        return 0
    overlap_start = max(min(epochs) for epochs in epoch_sets.values())
    overlap_end = min(max(epochs) for epochs in epoch_sets.values())
    if overlap_end <= overlap_start:
        status = build_no_data_status(
            args.config_label,
            "instances do not have an overlapping BTC epoch window",
        )
        output_path.write_text(
            build_no_data_report(args.config_label, "instances do not have an overlapping BTC epoch window"),
            encoding="utf-8",
        )
        json_out.write_text(json.dumps(status, indent=2, sort_keys=True), encoding="utf-8")
        if args.print_json:
            print(json.dumps(status, indent=2, sort_keys=True))
        print(output_path)
        return 0

    all_candidates: List[shadow_scan.Candidate] = []
    for instance in args.instances:
        all_candidates.extend(
            candidate
            for candidate in shadow_scan.load_candidates(instance)
            if candidate.config_label == args.config_label
            and candidate.config_era == instance_eras[instance]
            and overlap_start <= candidate.epoch <= overlap_end
        )
    candidates = all_candidates
    resolutions = shadow_scan.resolve_outcomes(candidate.slug for candidate in candidates)
    candidates = [candidate for candidate in candidates if candidate.slug in resolutions]

    ensemble_profile = make_profile(
        "ensemble_selected_live_v1",
        "Live-v1 slice: binance_momentum rows already tagged as shadow_ensemble_selected",
        lambda candidate: candidate.source == "binance_momentum" and candidate.shadow_ensemble_selected == 1,
    )
    guarded_profile = make_profile(
        "current_guarded",
        "Current guarded benchmark slice",
        lambda candidate: candidate.source == "binance_momentum" and candidate.shadow_current_guarded == 1,
    )
    ensemble_result = shadow_scan.simulate_profile(ensemble_profile, candidates, resolutions)
    guarded_result = shadow_scan.simulate_profile(guarded_profile, candidates, resolutions)

    env_values = read_env_file(CACHE_DIR / "emmanuel" / ".env")
    trade_cap = choose_planned_trade_cap(env_values)
    max_daily_loss = safe_float(env_values.get("MAX_DAILY_LOSS"), 0.0)
    strategy_metrics = scale_result_to_live(ensemble_result, trade_cap)
    benchmark_metrics = scale_result_to_live(guarded_result, trade_cap)

    audit_clean, audit_reason = detect_emmanuel_audit_clean(args.progress_path)
    journal_clean, journal_reason = detect_journal_clean() if args.journal_clean == "unknown" else (args.journal_clean, "cli_override")
    config_drift_clean, config_drift_reason = (
        detect_config_drift_clean(args.config_label, instance_eras)
        if args.config_drift_clean == "unknown"
        else (args.config_drift_clean, "cli_override")
    )
    security_status = security_best_practices_report.build_status(PROJECT_ROOT / "security_best_practices_report.md")
    dependency_status = dependency_audit_status.build_status()
    service_status = service_hardening_status.build_status()
    dashboard_fields_supported = get_dashboard_field_support()

    instance_stats = [
        build_instance_stats(
            instance,
            args.config_label,
            instance_eras[instance],
            overlap_start,
            overlap_end,
            resolutions,
        )
        for instance in args.instances
    ]

    blockers = evaluate_gates(
        instance_stats,
        strategy_metrics,
        benchmark_metrics,
        hours_required=args.hours_required,
        max_daily_loss=max_daily_loss,
        audit_clean=audit_clean,
        audit_reason=audit_reason,
        journal_clean=journal_clean,
        config_drift_clean=config_drift_clean,
        dashboard_fields_supported=dashboard_fields_supported,
        security_status=security_status,
        dependency_status=dependency_status,
        service_status=service_status,
    )

    report = build_report(
        config_label=args.config_label,
        config_era=config_era,
        overlap_start=overlap_start,
        overlap_end=overlap_end,
        trade_cap=trade_cap,
        max_daily_loss=max_daily_loss,
        instances=instance_stats,
        strategy_metrics=strategy_metrics,
        benchmark_metrics=benchmark_metrics,
        blockers=blockers,
        audit_clean=audit_clean,
        audit_reason=audit_reason,
        journal_clean=f"{journal_clean} ({journal_reason})",
        config_drift_clean=f"{config_drift_clean} ({config_drift_reason})",
        dashboard_fields_supported=dashboard_fields_supported,
        security_status=security_status,
        dependency_status=dependency_status,
        service_status=service_status,
    )
    status = build_gate_status(
        config_label=args.config_label,
        config_era=config_era,
        overlap_start=overlap_start,
        overlap_end=overlap_end,
        trade_cap=trade_cap,
        max_daily_loss=max_daily_loss,
        instances=instance_stats,
        strategy_metrics=strategy_metrics,
        benchmark_metrics=benchmark_metrics,
        blockers=blockers,
        audit_clean=audit_clean,
        audit_reason=audit_reason,
        journal_clean=journal_clean,
        config_drift_clean=config_drift_clean,
        dashboard_fields_supported=dashboard_fields_supported,
        security_status=security_status,
        dependency_status=dependency_status,
        service_status=service_status,
    )

    output_path.write_text(report, encoding="utf-8")
    json_out.write_text(json.dumps(status, indent=2, sort_keys=True), encoding="utf-8")
    if args.print_json:
        print(json.dumps(status, indent=2, sort_keys=True))
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
