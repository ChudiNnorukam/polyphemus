#!/usr/bin/env python3
"""Refresh a shadow experiment from snapshot history."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .shadow_eval_report import compute_summary, load_snapshots
except ImportError:  # pragma: no cover - direct script execution fallback
    from shadow_eval_report import compute_summary, load_snapshots


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SLUG = "xrp-5m-15m-fak-accumulator-shadow"
DEFAULT_INSTANCE = "polyphemus"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", default=DEFAULT_SLUG)
    parser.add_argument("--instance", default=DEFAULT_INSTANCE)
    parser.add_argument("--snapshots", default="")
    parser.add_argument("--write-json", action="store_true")
    return parser.parse_args()


def default_snapshot_path(instance: str) -> Path:
    base = ROOT / "data" / "shadow_eval" / instance
    vps_path = base / "hourly_snapshots.vps.jsonl"
    if vps_path.exists():
        return vps_path
    return base / "hourly_snapshots.jsonl"


def completed_count(row: dict) -> int:
    accum = row.get("accumulator", {})
    return int(accum.get("hedged_count", 0) or 0) + int(accum.get("unwound_count", 0) or 0)


def join_runtime_list(value: object) -> str:
    if isinstance(value, list):
        items = [str(item) for item in value if str(item)]
        return ",".join(items)
    if value is None:
        return ""
    return str(value)


def target_slice(summary: dict) -> tuple[str, str]:
    latest = summary.get("latest", {})
    accum = latest.get("accumulator", {})
    config = latest.get("config", {})
    assets = join_runtime_list(accum.get("assets")) or join_runtime_list(config.get("accum_assets")) or "unknown"
    windows = join_runtime_list(accum.get("window_types")) or join_runtime_list(config.get("accum_window_types")) or "unknown"
    return assets, windows


def entry_mode(summary: dict) -> str:
    latest = summary.get("latest", {})
    accum = latest.get("accumulator", {})
    status = latest.get("status", {})
    config = latest.get("config", {})
    return (
        str(status.get("accumulator_entry_mode") or "")
        or str(accum.get("entry_mode") or "")
        or str(config.get("accum_entry_mode") or "")
        or "unknown"
    )


def effective_dry_run(summary: dict) -> str:
    latest = summary.get("latest", {})
    status = latest.get("status", {})
    accum = latest.get("accumulator", {})
    config = latest.get("config", {})
    value = status.get("effective_accumulator_dry_run")
    if value is None:
        value = accum.get("effective_accumulator_dry_run")
    if value is None:
        value = config.get("effective_accumulator_dry_run")
    return str(value)


def compute_deltas(summary: dict) -> dict:
    first = summary.get("first", {})
    latest = summary.get("latest", {})
    first_accum = first.get("accumulator", {})
    latest_accum = latest.get("accumulator", {})
    first_pipeline = first.get("pipeline", {})
    latest_pipeline = latest.get("pipeline", {})
    return {
        "completed_delta": completed_count(latest) - completed_count(first),
        "candidates_delta": int(latest_accum.get("candidates_seen", 0) or 0) - int(first_accum.get("candidates_seen", 0) or 0),
        "scan_delta": int(latest_accum.get("scan_count", 0) or 0) - int(first_accum.get("scan_count", 0) or 0),
        "stage_stop_delta": len(latest_pipeline.get("stage_stops", []) or []) - len(first_pipeline.get("stage_stops", []) or []),
    }


def recommendation(summary: dict, deltas: dict) -> str:
    duration = float(summary.get("duration_hours", 0.0) or 0.0)
    completed_delta = int(deltas.get("completed_delta", 0) or 0)
    candidates_delta = int(deltas.get("candidates_delta", 0) or 0)
    verdict = str(summary.get("verdict", "NO-GO"))

    if verdict.startswith("CANARY-ELIGIBLE"):
        return "Promotion review can start, but only as a narrow live review for this exact slice."
    if duration >= 4 and completed_delta <= 0 and candidates_delta > 0:
        return (
            "Keep the slice frozen, but investigate throughput starvation before waiting passively; "
            "the bot is seeing candidates without converting them into new completed opportunities."
        )
    return "Keep collecting aligned shadow evidence on this exact frozen slice."


def render_evidence_log(slug: str, summary: dict, deltas: dict) -> str:
    latest = summary.get("latest", {})
    accum = latest.get("accumulator", {})
    pipeline = latest.get("pipeline", {})
    assets, windows = target_slice(summary)
    expectancy = summary.get("expectancy")
    expectancy_str = f"${expectancy:.4f}" if expectancy is not None else "n/a"
    total_pnl = float(summary.get("total_pnl", 0.0) or 0.0)
    started = summary.get("first", {}).get("captured_at", latest.get("captured_at", "unknown"))
    generated = latest.get("captured_at", "unknown")
    stage_stops = pipeline.get("stage_stops", []) or []
    stage_stop_note = f"{len(stage_stops)} logged" if stage_stops else "none"

    return "\n".join(
        [
            "# Quant Evidence Log",
            "",
            f"- Slug: `{slug}`",
            f"- Started: `{started}`",
            "",
            "## Runtime Ledger",
            "",
            "| Timestamp | Slice | Mode | Runtime Hours | Completed Opportunities | Expectancy | Drawdown | Incidents | Notes |",
            "|---|---|---|---:|---:|---:|---:|---|---|",
            (
                f"| {generated} | `{assets}` / `{windows}` | `shadow` | {summary['duration_hours']:.2f} | "
                f"{summary['completed']} | `{expectancy_str}` | `n/a` | "
                f"stage_stops={stage_stop_note}; last_eval_block={accum.get('last_eval_block_reason') or 'none'} | "
                f"completed_delta={deltas['completed_delta']}; candidates_delta={deltas['candidates_delta']}; "
                f"scan_delta={deltas['scan_delta']} |"
            ),
            "",
            "## Incident Notes",
            "",
            "- Keep the slice frozen while collecting shadow evidence.",
            "- Treat any restart, config drift, or dashboard/API outage as evidence contamination.",
            f"- Latest pipeline summary: {pipeline.get('summary', 'n/a')}",
            "",
            "## Interim Read",
            "",
            f"- Current gate: `{summary['verdict']}`",
            f"- Confidence label: `{summary['confidence']}`",
            "- Is the slice still frozen: yes",
            "- Is the evidence still usable: yes for shadow prioritization, no for promotion",
            "",
            "## Decision",
            "",
            f"- {recommendation(summary, deltas)}",
            "- Do not promote while the shared repo gate remains `NO-GO`.",
            "",
        ]
    ) + "\n"


def render_promotion_review(slug: str, summary: dict, deltas: dict) -> str:
    latest = summary.get("latest", {})
    accum = latest.get("accumulator", {})
    pipeline = latest.get("pipeline", {})
    assets, windows = target_slice(summary)
    expectancy = summary.get("expectancy")
    expectancy_str = f"${expectancy:.4f}" if expectancy is not None else "n/a"
    latest_block = accum.get("last_eval_block_reason") or "none"
    stage = pipeline.get("stage", "unknown")
    gate = "CANARY-ELIGIBLE" if str(summary["verdict"]).startswith("CANARY-ELIGIBLE") else "NO-GO"

    return "\n".join(
        [
            "# Quant Promotion Review",
            "",
            f"- Slug: `{slug}`",
            f"- Review date: `{latest.get('captured_at', 'unknown')}`",
            "- Reviewer: `Codex`",
            "",
            "## Candidate Slice",
            "",
            f"- Asset: `{assets}`",
            f"- Windows: `{windows}`",
            f"- Entry family: `accumulator_{entry_mode(summary)}_pair`",
            "- Exit family: `settlement_or_orphan_unwind`",
            f"- Effective dry run: `{effective_dry_run(summary)}`",
            "",
            "## Evidence Summary",
            "",
            f"- Shadow runtime hours: `{summary['duration_hours']:.2f}`",
            f"- Completed opportunities: `{summary['completed']}`",
            f"- Net session P&L: `${float(summary.get('total_pnl', 0.0) or 0.0):.2f}`",
            f"- Expectancy per completed opportunity: `{expectancy_str}`",
            f"- Confidence label: `{summary['confidence']}`",
            "",
            "## Execution Integrity",
            "",
            f"- Runtime stage: `{stage}`",
            f"- Circuit breaker tripped: `{accum.get('circuit_tripped')}`",
            f"- Latest evaluation block: `{latest_block}`",
            f"- New completed opportunities during observed window: `{deltas['completed_delta']}`",
            "",
            "## Gate",
            "",
            f"`{gate}`",
            "",
            "## Narrowest Allowed Next Step",
            "",
            f"- {recommendation(summary, deltas)}",
            "- Keep asset scope, window scope, entry mode, and dry-run posture unchanged.",
            "",
            "## Reopen Review Only With",
            "",
            "- >=48h aligned shadow duration on this exact slice",
            "- >=30 completed opportunities with positive expectancy after costs",
            "- resolved or accepted explanation for any persistent conversion starvation",
            "",
        ]
    ) + "\n"


def refresh_experiment(slug: str, snapshots_path: Path | None = None, write_json: bool = False) -> dict:
    experiment_dir = ROOT / ".omc" / "experiments" / slug
    if not experiment_dir.exists():
        raise FileNotFoundError(f"Experiment does not exist: {experiment_dir}")

    snapshots = load_snapshots(snapshots_path or default_snapshot_path(DEFAULT_INSTANCE))
    summary = compute_summary(snapshots)
    if not snapshots:
        raise ValueError("No shadow snapshots found for refresh")

    deltas = compute_deltas(summary)
    evidence_log = render_evidence_log(slug, summary, deltas)
    promotion_review = render_promotion_review(slug, summary, deltas)

    (experiment_dir / "evidence_log.md").write_text(evidence_log, encoding="utf-8")
    (experiment_dir / "promotion_review.md").write_text(promotion_review, encoding="utf-8")

    assets, windows = target_slice(summary)
    latest = summary.get("latest", {})
    accum = latest.get("accumulator", {})
    payload = {
        "slug": slug,
        "generated_at": latest.get("captured_at"),
        "gate_verdict": summary.get("verdict"),
        "confidence": summary.get("confidence"),
        "duration_hours": summary.get("duration_hours"),
        "completed_opportunities": summary.get("completed"),
        "total_pnl": summary.get("total_pnl"),
        "expectancy_per_completed": summary.get("expectancy"),
        "completed_delta": deltas.get("completed_delta"),
        "candidates_delta": deltas.get("candidates_delta"),
        "scan_delta": deltas.get("scan_delta"),
        "target_assets": assets,
        "target_windows": windows,
        "entry_mode": entry_mode(summary),
        "effective_dry_run": effective_dry_run(summary),
        "latest_eval_block_reason": accum.get("last_eval_block_reason"),
    }
    if write_json:
        (experiment_dir / "current_status.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    args = parse_args()
    snapshots_path = Path(args.snapshots) if args.snapshots else default_snapshot_path(args.instance)
    payload = refresh_experiment(args.slug, snapshots_path=snapshots_path, write_json=args.write_json)
    print(json.dumps({"ok": True, **payload}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
