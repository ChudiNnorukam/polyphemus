#!/usr/bin/env python3
"""Assemble the current cross-agent state bundle from runtime snapshots."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from . import agent_activation
    from .kb_common import PROJECT_ROOT, dump_json
except ImportError:  # pragma: no cover - direct script execution
    import agent_activation
    from kb_common import PROJECT_ROOT, dump_json


SCRIPT_MAP = {
    "gate_status": "btc5m_ensemble_go_live_gate.py",
    "shadow_window_status": "shadow_window_status.py",
    "shadow_checklist": "shadow_window_checklist.py",
    "audit_status": "emmanuel_audit_mismatch_check.py",
    "security_audit_status": "security_best_practices_report.py",
    "dependency_audit_status": "dependency_audit_status.py",
    "service_hardening_status": "service_hardening_status.py",
    "research_brief": "kb_research_brief.py",
    "decision_memo": "kb_decision_memo.py",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def run_script(script_name: str) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
        json_path = Path(handle.name)
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "tools" / script_name),
        "--json-out",
        str(json_path),
        "--print-json",
    ]
    subprocess.run(cmd, cwd=str(PROJECT_ROOT.parent), capture_output=True, text=True, check=True)
    return json.loads(json_path.read_text(encoding="utf-8"))


def build_state_bundle(refresh_runtime: bool = True) -> dict:
    payload = {}
    for key, script_name in SCRIPT_MAP.items():
        if refresh_runtime:
            snapshot = run_script(script_name)
        else:
            snapshot = agent_activation.read_runtime_json(agent_activation.runtime_snapshot_path(key))
            if not snapshot:
                snapshot = run_script(script_name)
        payload[key] = snapshot
    payload["latest_reports"] = agent_activation.newest_report_paths()
    return payload


def main() -> int:
    args = parse_args()
    payload = build_state_bundle(refresh_runtime=True)
    if args.json_out:
        dump_json(args.json_out, payload)
    if args.print_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
