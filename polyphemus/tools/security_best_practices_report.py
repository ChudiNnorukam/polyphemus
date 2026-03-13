#!/usr/bin/env python3
"""Generate a tracked backend/security/architecture hardening report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from . import dependency_audit_status, service_hardening_status
    from .kb_common import AGENT_HANDOFF_ROOT, INTERNAL_RUNTIME_CURRENT_ROOT, PROJECT_ROOT, dump_json, now_iso
except ImportError:  # pragma: no cover - direct script execution
    import dependency_audit_status
    import service_hardening_status
    from kb_common import AGENT_HANDOFF_ROOT, INTERNAL_RUNTIME_CURRENT_ROOT, PROJECT_ROOT, dump_json, now_iso


FRONTEND_MAIN = PROJECT_ROOT.parent / "bot-dashboard" / "frontend" / "electron" / "main.js"
START_TUNNELS = PROJECT_ROOT / "tools" / "start_tunnels.sh"
QUALITY_GATE = PROJECT_ROOT / "tools" / "quality_gate.py"
REPORT_PATH = PROJECT_ROOT / "security_best_practices_report.md"

SECRET_KEY_MARKERS = ("private_key", "secret", "passphrase", "api_secret", "builder_secret", "clob_secret")
SAFE_RUNTIME_ROOTS = [
    PROJECT_ROOT / "dario_output",
    PROJECT_ROOT / "kb" / "internal" / "runtime" / "current",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--report-out", type=Path, default=REPORT_PATH)
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def flatten_secret_hits(value: Any, prefix: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            next_prefix = f"{prefix}.{key}" if prefix else str(key)
            if any(marker in str(key).lower() for marker in SECRET_KEY_MARKERS):
                hits.append(next_prefix)
            hits.extend(flatten_secret_hits(item, next_prefix))
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            hits.extend(flatten_secret_hits(item, f"{prefix}[{idx}]"))
    return hits


def scan_runtime_secrets() -> list[str]:
    hits: list[str] = []
    for root in (INTERNAL_RUNTIME_CURRENT_ROOT, AGENT_HANDOFF_ROOT):
        if not root.exists():
            continue
        for path in root.rglob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            for hit in flatten_secret_hits(payload):
                hits.append(f"{path.relative_to(PROJECT_ROOT)}:{hit}")
    return hits


def inspect_electron_boundary() -> tuple[list[str], list[str]]:
    text = FRONTEND_MAIN.read_text(encoding="utf-8") if FRONTEND_MAIN.exists() else ""
    failures = []
    fixed = []
    if "const ALLOWED_PYTHON_TOOLS" not in text or "if (!ALLOWED_PYTHON_TOOLS.has(scriptName))" not in text:
        failures.append("Electron main process still allows unbounded Python tool execution.")
    else:
        fixed.append("Electron Python tool execution is restricted to an explicit allowlist.")
    if "allowedReportRoots" not in text or "isAllowedReportPath" not in text or "path not allowed" not in text:
        failures.append("Electron report/file open path is not constrained to approved repo roots.")
    else:
        fixed.append("Electron report opening is constrained to approved repo roots.")
    if "contextIsolation: true" not in text or "nodeIntegration: false" not in text or "sandbox: true" not in text:
        failures.append("Electron BrowserWindow security flags are not locked to the current safe baseline.")
    else:
        fixed.append("Electron BrowserWindow security flags stay on the safe baseline.")
    return failures, fixed


def inspect_dead_host_helper() -> tuple[list[str], list[str]]:
    if not START_TUNNELS.exists():
        return [], []
    text = START_TUNNELS.read_text(encoding="utf-8")
    failures = []
    fixed = []
    if "142.93.143.178" in text or "159.223.236.50" in text:
        failures.append("Tunnel helper still references dead or backup hosts.")
    else:
        fixed.append("Tunnel helper no longer references dead or backup hosts.")
    return failures, fixed


def inspect_types_containment() -> tuple[list[str], list[str]]:
    failures = []
    fixed = []
    if not (PROJECT_ROOT / "types.py").exists():
        return failures, fixed
    if QUALITY_GATE.exists() and "PROJECT_ROOT.parent" in QUALITY_GATE.read_text(encoding="utf-8"):
        fixed.append("types.py shadowing risk is contained by deterministic parent-directory quality-gate invocation.")
    else:
        failures.append("types.py shadowing risk is not yet contained by the shared quality-gate path.")
    return failures, fixed


def build_status(report_out: Path) -> dict:
    dependency_status = dependency_audit_status.build_status()
    service_status = service_hardening_status.build_status()
    secret_hits = scan_runtime_secrets()
    electron_failures, electron_fixed = inspect_electron_boundary()
    helper_failures, helper_fixed = inspect_dead_host_helper()
    types_failures, types_fixed = inspect_types_containment()

    critical_blockers: list[str] = []
    high_findings: list[str] = []
    moderate_findings: list[str] = []
    fixed_findings: list[str] = []

    if dependency_status["frontend"]["blocking_findings"]:
        critical_blockers.append("Shipped frontend dependencies still have blocking advisories.")
    if dependency_status["python"]["blocking_findings"]:
        high_findings.extend([f"Python dependency posture: {item}" for item in dependency_status["python"]["blocking_findings"]])
    if service_status["verdict"] != "pass":
        high_findings.append(
            f"Systemd hardening baseline missing: {', '.join(service_status['missing_controls'])}"
        )
    if secret_hits:
        critical_blockers.append(f"Secret-bearing fields leaked into runtime snapshots: {', '.join(secret_hits)}")
    high_findings.extend(electron_failures)
    moderate_findings.extend(helper_failures)
    moderate_findings.extend(types_failures)

    fixed_findings.extend(electron_fixed)
    fixed_findings.extend(helper_fixed)
    fixed_findings.extend(types_fixed)
    if dependency_status["frontend"]["shipped_surface_clean"]:
        fixed_findings.append("Frontend production dependency audit is clean; remaining advisories are build/dev-only.")
    if not dependency_status["python"]["blocking_findings"]:
        fixed_findings.append("Python deploy path is exact-pinned through requirements-lock.txt.")
    if service_status["verdict"] == "pass":
        fixed_findings.append("Local systemd unit includes the hardening baseline required for shadow-safe deployment.")

    verdict = "pass" if not critical_blockers and not high_findings else "fail"
    status = {
        "generated_at": now_iso(),
        "verdict": verdict,
        "critical_blockers": critical_blockers,
        "high_findings": high_findings,
        "moderate_findings": moderate_findings,
        "fixed_findings": fixed_findings,
        "report_path": str(report_out),
        "dependency_audit_status": dependency_status,
        "service_hardening_status": service_status,
        "safe_to_run_shadow": verdict == "pass",
        "eligible_for_narrow_live_rollout": False,
    }
    return status


def build_report(payload: dict) -> str:
    lines = [
        "# Security Best Practices Report",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Verdict: `{payload['verdict']}`",
        f"- Safe To Run Shadow: `{payload['safe_to_run_shadow']}`",
        f"- Eligible For Narrow Live Rollout: `{payload['eligible_for_narrow_live_rollout']}`",
        "",
        "## Critical",
        "",
    ]
    if payload["critical_blockers"]:
        lines.extend([f"- {item}" for item in payload["critical_blockers"]])
    else:
        lines.append("- none")
    lines.extend([
        "",
        "## High",
        "",
    ])
    if payload["high_findings"]:
        lines.extend([f"- {item}" for item in payload["high_findings"]])
    else:
        lines.append("- none")
    lines.extend([
        "",
        "## Moderate",
        "",
    ])
    if payload["moderate_findings"]:
        lines.extend([f"- {item}" for item in payload["moderate_findings"]])
    else:
        lines.append("- none")
    lines.extend([
        "",
        "## Fixed In This Pass",
        "",
    ])
    if payload["fixed_findings"]:
        lines.extend([f"- {item}" for item in payload["fixed_findings"]])
    else:
        lines.append("- none")
    lines.extend([
        "",
        "## Dependency Posture",
        "",
        f"- Frontend shipped-surface clean: `{payload['dependency_audit_status']['frontend']['shipped_surface_clean']}`",
        f"- Frontend raw findings: `{payload['dependency_audit_status']['frontend']['raw_total']}`",
        f"- Frontend prod findings: `{payload['dependency_audit_status']['frontend']['prod_total']}`",
        f"- Python pinned deploy path clean: `{not payload['dependency_audit_status']['python']['blocking_findings']}`",
        "",
        "## Service Hardening",
        "",
        f"- Service verdict: `{payload['service_hardening_status']['verdict']}`",
        f"- Missing controls: `{', '.join(payload['service_hardening_status']['missing_controls']) or 'none'}`",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    payload = build_status(args.report_out)
    args.report_out.write_text(build_report(payload), encoding="utf-8")
    if args.json_out:
        dump_json(args.json_out, payload)
    if args.print_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
