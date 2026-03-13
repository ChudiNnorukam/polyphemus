#!/usr/bin/env python3
"""Compute the remaining checklist before the BTC 5m go-live gate is worth running."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

try:
    from . import emmanuel_audit_mismatch_check as audit_check
    from . import shadow_window_status
except ImportError:  # pragma: no cover - direct script execution
    import emmanuel_audit_mismatch_check as audit_check
    import shadow_window_status


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instances", nargs="+", default=["emmanuel", "polyphemus"])
    parser.add_argument("--config-label", default="btc5m_shadow_lab_v3")
    parser.add_argument("--hours-required", type=float, default=48.0)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def build_shadow_status(args: argparse.Namespace) -> dict:
    alignment = shadow_window_status.gate.get_research_alignment_context(args.instances, args.config_label)
    config_era = alignment["shared_research_era"]
    instance_eras = alignment["instance_config_eras"]
    status = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config_label": args.config_label,
        "shared_config_era": config_era,
        "instance_config_eras": instance_eras,
        "elapsed_hours": 0.0,
        "window_start": None,
        "window_end": None,
        "signal_counts_by_instance": {},
        "epoch_coverage_by_instance": {},
        "thresholds_met": {
            "shared_config_era": bool(config_era),
            "runtime_hours": False,
            "signal_count": False,
        },
    }
    if config_era:
        epoch_sets = {
            instance: shadow_window_status.gate.get_epochs(instance, args.config_label, instance_eras.get(instance, ""))
            for instance in args.instances
        }
        if all(epoch_sets.values()):
            overlap_start = max(min(epochs) for epochs in epoch_sets.values())
            overlap_end = min(max(epochs) for epochs in epoch_sets.values())
            expected = shadow_window_status.gate.expected_epoch_count(overlap_start, overlap_end)
            status["window_start"] = overlap_start
            status["window_end"] = overlap_end
            status["elapsed_hours"] = max(0.0, (overlap_end - overlap_start) / 3600.0)
            status["thresholds_met"]["runtime_hours"] = status["elapsed_hours"] >= args.hours_required
            signal_ready = True
            for instance, epochs in epoch_sets.items():
                observed = len([epoch for epoch in epochs if overlap_start <= epoch <= overlap_end])
                coverage_rate = (observed / expected) if expected else 0.0
                signal_count = shadow_window_status.gate.count_signals(
                    instance,
                    args.config_label,
                    instance_eras.get(instance, ""),
                    overlap_start,
                    overlap_end,
                )
                status["signal_counts_by_instance"][instance] = signal_count
                status["epoch_coverage_by_instance"][instance] = {
                    "observed_epochs": observed,
                    "expected_epochs": expected,
                    "coverage_rate": coverage_rate,
                }
                signal_ready = signal_ready and signal_count >= 30
            status["thresholds_met"]["signal_count"] = signal_ready
    return status


def build_status(args: argparse.Namespace) -> dict:
    shadow = build_shadow_status(args)
    audit_state = audit_check.main_for_status()

    items = []
    blockers = []

    shared_era_done = bool(shadow["shared_config_era"])
    items.append({
        "key": "shared_config_era",
        "label": "Both instances share the same post-fix config era",
        "status": "done" if shared_era_done else "pending",
        "detail": shadow["shared_config_era"] if shared_era_done else "Refresh caches and wait for both bots to log the same aligned era.",
    })
    if not shared_era_done:
        blockers.append("No shared post-fix config era exists yet.")

    runtime_done = bool(shadow["thresholds_met"]["runtime_hours"])
    runtime_missing = max(0.0, args.hours_required - float(shadow["elapsed_hours"]))
    items.append({
        "key": "runtime_hours",
        "label": f"Shadow window has at least {int(args.hours_required)} hours of aligned data",
        "status": "done" if runtime_done else "pending",
        "detail": (
            f"{shadow['elapsed_hours']:.1f}h collected."
            if runtime_done
            else f"{shadow['elapsed_hours']:.1f}h collected, {runtime_missing:.1f}h still needed."
        ),
    })
    if not runtime_done:
        blockers.append(f"Need {runtime_missing:.1f} more hours of aligned shadow data.")

    signal_detail_parts = []
    signal_done = True
    for instance in args.instances:
        count = int(shadow["signal_counts_by_instance"].get(instance, 0))
        signal_detail_parts.append(f"{instance}: {count}/30")
        signal_done = signal_done and count >= 30
    items.append({
        "key": "signal_count",
        "label": "Each instance has at least 30 BTC 5m tagged signals in the aligned window",
        "status": "done" if signal_done else "pending",
        "detail": ", ".join(signal_detail_parts) if signal_detail_parts else "No aligned signal counts yet.",
    })
    if not signal_done:
        blockers.append("Need more BTC 5m tagged signals before the gate result is trustworthy.")

    coverage_done = True
    coverage_parts = []
    for instance in args.instances:
        coverage = shadow["epoch_coverage_by_instance"].get(instance, {})
        rate = float(coverage.get("coverage_rate", 0.0))
        coverage_parts.append(f"{instance}: {rate * 100:.1f}%")
        coverage_done = coverage_done and rate >= 0.90
    items.append({
        "key": "epoch_coverage",
        "label": "Epoch coverage is at least 90% on both instances",
        "status": "done" if coverage_done else ("pending" if shared_era_done else "blocked"),
        "detail": ", ".join(coverage_parts) if coverage_parts else "Coverage not measurable yet.",
    })
    if shared_era_done and not coverage_done:
        blockers.append("Epoch coverage is still below the 90% gate threshold.")

    audit_done = audit_state["state"] == "pass"
    items.append({
        "key": "audit_mismatch",
        "label": "emmanuel CLOB to DB audit mismatch is resolved",
        "status": "done" if audit_done else ("blocked" if audit_state["state"] == "fail" else "pending"),
        "detail": audit_state["latest_evidence"],
    })
    if audit_state["state"] == "fail":
        blockers.append("emmanuel still has a CLOB↔DB audit mismatch.")
    elif audit_state["state"] == "unknown":
        blockers.append("Audit mismatch status is unknown.")

    can_run_gate = shared_era_done and runtime_done and signal_done
    if not can_run_gate:
        next_action = "Keep the aligned shadow window running and refresh caches again later."
    elif audit_state["state"] != "pass":
        next_action = "Investigate the emmanuel audit mismatch before trusting any go-live decision."
    else:
        next_action = "Run the ensemble go-live gate on the aligned window."

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config_label": args.config_label,
        "can_run_gate": can_run_gate,
        "next_action": next_action,
        "blockers": blockers,
        "items": items,
        "shadow_window": shadow,
        "audit_mismatch": audit_state,
    }


def main() -> int:
    args = parse_args()
    status = build_status(args)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(status, indent=2, sort_keys=True), encoding="utf-8")
    if args.print_json:
        print(json.dumps(status, indent=2, sort_keys=True))
    else:
        print(json.dumps(status))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
