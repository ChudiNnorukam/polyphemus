#!/usr/bin/env python3
"""Prepare the shared cross-agent runtime bundle without mutating live systems."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from . import agent_activation, agent_state_bundle
    from .kb_common import (
        INDEX_PATH,
        INTERNAL_RUNTIME_CURRENT_ROOT,
        PROJECT_ROOT,
        dump_json,
        ensure_kb_dirs,
    )
except ImportError:  # pragma: no cover - direct script execution
    import agent_activation
    import agent_state_bundle
    from kb_common import INDEX_PATH, INTERNAL_RUNTIME_CURRENT_ROOT, PROJECT_ROOT, dump_json, ensure_kb_dirs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def run_tool(script_name: str) -> dict:
    cmd = [sys.executable, str(PROJECT_ROOT / "tools" / script_name), "--print-json"]
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT.parent), capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def kb_index_stale() -> bool:
    if not INDEX_PATH.exists():
        return True
    index_mtime = INDEX_PATH.stat().st_mtime
    candidate_roots = [
        PROJECT_ROOT / "kb" / "external" / "primary" / "docs",
        PROJECT_ROOT / "kb" / "internal" / "reports" / "docs",
        PROJECT_ROOT / "kb" / "internal" / "runtime",
        PROJECT_ROOT / "kb" / "playbooks",
    ]
    for root in candidate_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.stat().st_mtime > index_mtime:
                return True
    return False


def snapshot_freshness(bundle: dict) -> dict:
    freshness = {}
    for key in agent_state_bundle.SCRIPT_MAP:
        path = agent_activation.runtime_snapshot_path(key)
        freshness[key] = {
            "path": str(path),
            "exists": path.exists(),
            "mtime_iso": (
                datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
                if path.exists()
                else ""
            ),
        }
    freshness["kb_index"] = {
        "path": str(INDEX_PATH),
        "exists": INDEX_PATH.exists(),
        "mtime_iso": (
            datetime.fromtimestamp(INDEX_PATH.stat().st_mtime, tz=timezone.utc).isoformat()
            if INDEX_PATH.exists()
            else ""
        ),
    }
    return freshness


def build_bootstrap_status(bundle: dict, recommended_role: dict) -> dict:
    checklist = bundle.get("shadow_checklist", {})
    gate = bundle.get("gate_status", {})
    security = bundle.get("security_audit_status", {})
    dependency = bundle.get("dependency_audit_status", {})
    service = bundle.get("service_hardening_status", {})
    blockers = []
    if gate.get("verdict") != "GO":
        blockers.extend(gate.get("blockers", []))
    audit_state = bundle.get("audit_status", {}).get("state", "unknown")
    if audit_state == "unknown":
        blockers.append("Audit status is unknown.")
    if security.get("verdict") != "pass":
        blockers.append("Security audit is not clean.")
    if dependency.get("verdict") != "pass":
        blockers.append("Dependency audit is not clean.")
    if service.get("verdict") != "pass":
        blockers.append("Service hardening baseline is not clean.")

    source_freshness = snapshot_freshness(bundle)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kb_index_ready": INDEX_PATH.exists(),
        "runtime_state_ready": all(bundle.get(key) for key in agent_state_bundle.SCRIPT_MAP),
        "source_freshness": source_freshness,
        "next_action": (
            checklist.get("next_action")
            or bundle.get("decision_memo", {}).get("recommended_next_step")
            or "Refresh the runtime bundle."
        ),
        "blockers": blockers,
        "recommended_role": recommended_role["role"],
        "recommended_role_reason": recommended_role["reason"],
    }


def run_bootstrap() -> dict:
    ensure_kb_dirs()
    run_tool("kb_ingest_internal.py")
    if kb_index_stale():
        run_tool("kb_build_index.py")

    bundle = agent_state_bundle.build_state_bundle(refresh_runtime=False)
    for key in agent_state_bundle.SCRIPT_MAP:
        dump_json(agent_activation.runtime_snapshot_path(key), bundle[key])

    recommended_role = agent_activation.role_selection_from_state(bundle)
    handoff = agent_activation.build_handoff_payload(bundle, recommended_role)
    bootstrap_status = build_bootstrap_status(bundle, recommended_role)
    agent_activation.write_runtime_payloads(bootstrap_status, bundle, handoff, recommended_role)
    return bootstrap_status


def main() -> int:
    args = parse_args()
    payload = run_bootstrap()
    if args.json_out:
        dump_json(args.json_out, payload)
    if args.print_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
