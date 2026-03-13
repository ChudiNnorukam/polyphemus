#!/usr/bin/env python3
"""Deterministic smoke validations for the hardening quality gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from . import agent_bootstrap, dependency_audit_status, kb_decision_memo, security_best_practices_report, service_hardening_status
    from .kb_common import INTERNAL_RUNTIME_CURRENT_ROOT, PROJECT_ROOT, dump_json, load_json, now_iso
except ImportError:  # pragma: no cover - direct script execution
    import agent_bootstrap
    import dependency_audit_status
    import kb_decision_memo
    import security_best_practices_report
    import service_hardening_status
    from kb_common import INTERNAL_RUNTIME_CURRENT_ROOT, PROJECT_ROOT, dump_json, load_json, now_iso


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def build_payload() -> dict:
    dependency_path = INTERNAL_RUNTIME_CURRENT_ROOT / "dependency_audit_status.json"
    service_path = INTERNAL_RUNTIME_CURRENT_ROOT / "service_hardening_status.json"
    security_path = INTERNAL_RUNTIME_CURRENT_ROOT / "security_audit_status.json"
    dependency = load_json(dependency_path) if dependency_path.exists() else dependency_audit_status.build_status()
    service = load_json(service_path) if service_path.exists() else service_hardening_status.build_status()
    security = load_json(security_path) if security_path.exists() else security_best_practices_report.build_status(PROJECT_ROOT / "security_best_practices_report.md")
    bootstrap = agent_bootstrap.run_bootstrap()
    memo = kb_decision_memo.build_decision_memo()
    checks = {
        "dependency_verdict": dependency["verdict"] == "pass",
        "service_verdict": service["verdict"] == "pass",
        "security_verdict": security["verdict"] == "pass",
        "bootstrap_runtime_ready": bootstrap["runtime_state_ready"] is True,
        "decision_memo_conservative": memo["verdict"] in {"GO", "NO-GO"} and memo["can_go_live_now"] is False,
    }
    return {
        "generated_at": now_iso(),
        "checks": checks,
        "passed": all(checks.values()),
        "dependency_verdict": dependency["verdict"],
        "service_verdict": service["verdict"],
        "security_verdict": security["verdict"],
        "bootstrap_recommended_role": bootstrap["recommended_role"],
        "decision_memo_verdict": memo["verdict"],
    }


def main() -> int:
    args = parse_args()
    payload = build_payload()
    if args.json_out:
        dump_json(args.json_out, payload)
    if args.print_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
