#!/usr/bin/env python3
"""Summarize the current aligned BTC 5m shadow window."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

try:
    from . import btc5m_ensemble_go_live_gate as gate
except ImportError:  # pragma: no cover - direct script execution
    import btc5m_ensemble_go_live_gate as gate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instances", nargs="+", default=["emmanuel", "polyphemus"])
    parser.add_argument("--config-label", default="btc5m_shadow_lab_v3")
    parser.add_argument("--hours-required", type=float, default=48.0)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    alignment = gate.get_research_alignment_context(args.instances, args.config_label)
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
            instance: gate.get_epochs(instance, args.config_label, instance_eras[instance])
            for instance in args.instances
        }
        if all(epoch_sets.values()):
            overlap_start = max(min(epochs) for epochs in epoch_sets.values())
            overlap_end = min(max(epochs) for epochs in epoch_sets.values())
            expected = gate.expected_epoch_count(overlap_start, overlap_end)
            status["window_start"] = overlap_start
            status["window_end"] = overlap_end
            status["elapsed_hours"] = max(0.0, (overlap_end - overlap_start) / 3600.0)
            status["thresholds_met"]["runtime_hours"] = status["elapsed_hours"] >= args.hours_required
            signal_ready = True
            for instance, epochs in epoch_sets.items():
                observed = len([epoch for epoch in epochs if overlap_start <= epoch <= overlap_end])
                coverage_rate = (observed / expected) if expected else 0.0
                signal_count = gate.count_signals(instance, args.config_label, instance_eras[instance], overlap_start, overlap_end)
                status["signal_counts_by_instance"][instance] = signal_count
                status["epoch_coverage_by_instance"][instance] = {
                    "observed_epochs": observed,
                    "expected_epochs": expected,
                    "coverage_rate": coverage_rate,
                }
                signal_ready = signal_ready and signal_count >= 30
            status["thresholds_met"]["signal_count"] = signal_ready
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
