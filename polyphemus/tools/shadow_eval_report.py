#!/usr/bin/env python3
"""Generate a markdown eval artifact from shadow snapshot history.

Standalone by design so it can run locally against tunnel-backed data or on VPS.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def confidence_label(n: int) -> str:
    if n < 30:
        return f"ANECDOTAL n={n}"
    if n < 107:
        return f"LOW n={n}"
    if n < 385:
        return f"MODERATE n={n}"
    return f"SIGNIFICANT n={n}"


def verdict(duration_hours: float, completed: int, expectancy: float | None) -> str:
    if duration_hours < 48:
        return "NO-GO: shadow window immature"
    if completed < 30:
        return "NO-GO: insufficient completed opportunity count"
    if expectancy is None:
        return "NO-GO: expectancy unavailable"
    if expectancy <= 0:
        return "NO-GO: non-positive expectancy"
    return "CANARY-ELIGIBLE: narrow live review only"


def load_snapshots(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def compute_summary(rows: list[dict]) -> dict:
    if not rows:
        return {
            "duration_hours": 0.0,
            "completed": 0,
            "expectancy": None,
            "confidence": confidence_label(0),
            "verdict": "NO-GO: no snapshot data",
        }

    rows = sorted(rows, key=lambda item: item.get("captured_ts", 0))
    first = rows[0]
    latest = rows[-1]
    duration_hours = max(0.0, (latest["captured_ts"] - first["captured_ts"]) / 3600)
    accum = latest.get("accumulator", {})
    completed = int(accum.get("hedged_count", 0) or 0) + int(accum.get("unwound_count", 0) or 0)
    total_pnl = float(accum.get("total_pnl", 0.0) or 0.0)
    expectancy = (total_pnl / completed) if completed > 0 else None
    return {
        "first": first,
        "latest": latest,
        "duration_hours": duration_hours,
        "completed": completed,
        "total_pnl": total_pnl,
        "expectancy": expectancy,
        "confidence": confidence_label(completed),
        "verdict": verdict(duration_hours, completed, expectancy),
    }


def render_report(instance: str, summary: dict) -> str:
    latest = summary.get("latest", {})
    status = latest.get("status", {})
    accum = latest.get("accumulator", {})
    pipeline = latest.get("pipeline", {})
    config = latest.get("config", {})
    captured_at = latest.get("captured_at", datetime.now(timezone.utc).isoformat())
    expectancy = summary.get("expectancy")
    expectancy_str = f"${expectancy:.4f}" if expectancy is not None else "n/a"

    assets = ",".join(accum.get("assets", [])) or config.get("accum_assets") or "unknown"
    windows = ",".join(accum.get("window_types", [])) or config.get("accum_window_types") or "unknown"
    effective_dry_run = status.get(
        "effective_accumulator_dry_run",
        config.get("effective_accumulator_dry_run", accum.get("effective_accumulator_dry_run")),
    )

    lines = [
        f"# Shadow Eval Report - {instance}",
        "",
        f"- Generated: `{captured_at}`",
        f"- Target slice: `{assets}` / `{windows}`",
        f"- Entry mode: `{status.get('accumulator_entry_mode', accum.get('entry_mode', 'unknown'))}`",
        f"- Effective dry run: `{effective_dry_run}`",
        "",
        "## Readiness Verdict",
        "",
        f"- Verdict: **{summary['verdict']}**",
        f"- Confidence: `{summary['confidence']}`",
        f"- Shadow duration: `{summary['duration_hours']:.2f}h`",
        f"- Completed opportunities: `{summary['completed']}`",
        f"- Net session P&L: `${summary.get('total_pnl', 0.0):.2f}`",
        f"- Expectancy per completed opportunity: `{expectancy_str}`",
        "",
        "## Current Runtime Snapshot",
        "",
        f"- Status: `{status.get('status', 'unknown')}`",
        f"- Circuit tripped: `{status.get('accumulator_circuit_tripped', accum.get('circuit_tripped'))}`",
        f"- Active positions: `{accum.get('active_positions', 0)}`",
        f"- Scan count: `{accum.get('scan_count', 0)}`",
        f"- Candidates seen: `{accum.get('candidates_seen', 0)}`",
        f"- Hedged count: `{accum.get('hedged_count', 0)}`",
        f"- Unwound count: `{accum.get('unwound_count', 0)}`",
        f"- Orphaned count: `{accum.get('orphaned_count', 0)}`",
        "",
        "## Pipeline Surface",
        "",
        f"- Stage: `{pipeline.get('stage', 'unknown')}`",
        f"- Headline: {pipeline.get('headline', 'n/a')}",
        f"- Summary: {pipeline.get('summary', 'n/a')}",
        "",
        "## Gate Read",
        "",
        "- This artifact is for config-era-specific shadow evaluation.",
        "- A positive artifact here does not override the shared `NO-GO` gate by itself.",
        "- Promotion remains blocked until duration, sample size, and execution-integrity gates are satisfied.",
    ]
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render shadow eval report from snapshot history")
    parser.add_argument("--instance", default="polyphemus")
    parser.add_argument("--snapshots", default="")
    parser.add_argument("--output", default="")
    return parser.parse_args()


def default_paths(instance: str) -> tuple[Path, Path]:
    root = Path(__file__).resolve().parents[1]
    snapshots = root / "data" / "shadow_eval" / instance / "hourly_snapshots.jsonl"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output = root / "dario_output" / f"shadow_eval_{instance}_{stamp}.md"
    return snapshots, output


def main() -> int:
    args = parse_args()
    default_snapshots, default_output = default_paths(args.instance)
    snapshots_path = Path(args.snapshots) if args.snapshots else default_snapshots
    output_path = Path(args.output) if args.output else default_output
    rows = load_snapshots(snapshots_path)
    summary = compute_summary(rows)
    report = render_report(args.instance, summary)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(json.dumps({"ok": True, "snapshots": str(snapshots_path), "output": str(output_path), "verdict": summary["verdict"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
