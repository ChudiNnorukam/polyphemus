#!/usr/bin/env python3
"""Refresh repo-local quant artifacts from current VPS/runtime state."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

try:
    from . import quant_candidate_refresh
    from .kb_common import PROJECT_ROOT
except ImportError:  # pragma: no cover - direct script execution fallback
    import quant_candidate_refresh
    from kb_common import PROJECT_ROOT


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-vps-refresh", action="store_true")
    parser.add_argument("--skip-bootstrap", action="store_true")
    parser.add_argument("--candidate-slug", default="btc-5m-ensemble-selected-live-v1")
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def run_script(script_name: str, *extra_args: str) -> dict:
    cmd = [sys.executable, str(PROJECT_ROOT / "tools" / script_name), *extra_args, "--print-json"]
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT.parent), capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def main() -> int:
    args = parse_args()
    steps: list[dict] = []

    if args.skip_vps_refresh:
        steps.append({"step": "refresh_vps_caches", "skipped": True})
    else:
        steps.append({"step": "refresh_vps_caches", "payload": run_script("refresh_vps_caches.py")})

    if args.skip_bootstrap:
        steps.append({"step": "agent_bootstrap", "skipped": True})
    else:
        steps.append({"step": "agent_bootstrap", "payload": run_script("agent_bootstrap.py")})

    candidate_payload = quant_candidate_refresh.refresh_candidate(args.candidate_slug, write_json=True)
    steps.append({"step": "quant_candidate_refresh", "payload": candidate_payload})

    summary = {
        "ok": True,
        "candidate_slug": args.candidate_slug,
        "gate_verdict": candidate_payload.get("gate_verdict"),
        "shadow_elapsed_hours": candidate_payload.get("shadow_elapsed_hours"),
        "candidate_trades": candidate_payload.get("comparison", {}).get("trades"),
        "candidate_win_rate": candidate_payload.get("comparison", {}).get("win_rate"),
        "candidate_avg_net_live": candidate_payload.get("comparison", {}).get("avg_net_live"),
        "blockers": candidate_payload.get("blockers", []),
        "steps": steps,
    }
    if args.print_json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
