#!/usr/bin/env python3
"""Shared helpers for cross-agent activation, runtime state, and handoff."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List

try:
    from .kb_common import (
        AGENT_CONTRACT_ROOT,
        AGENT_HANDOFF_ROOT,
        INDEX_PATH,
        INTERNAL_RUNTIME_CURRENT_ROOT,
        PROJECT_ROOT,
        dump_json,
        load_json,
        now_iso,
    )
except ImportError:  # pragma: no cover - direct script execution
    from kb_common import (
        AGENT_CONTRACT_ROOT,
        AGENT_HANDOFF_ROOT,
        INDEX_PATH,
        INTERNAL_RUNTIME_CURRENT_ROOT,
        PROJECT_ROOT,
        dump_json,
        load_json,
        now_iso,
    )


CONTRACT_PATH = AGENT_CONTRACT_ROOT / "repo_agent_contract.json"
STATE_BUNDLE_PATH = AGENT_HANDOFF_ROOT / "current_state.json"
NEXT_ROLE_PATH = AGENT_HANDOFF_ROOT / "next_role.json"
BOOTSTRAP_STATUS_PATH = INTERNAL_RUNTIME_CURRENT_ROOT / "agent_bootstrap_status.json"
STATE_BUNDLE_RUNTIME_PATH = INTERNAL_RUNTIME_CURRENT_ROOT / "agent_state_bundle.json"
HANDOFF_RUNTIME_PATH = INTERNAL_RUNTIME_CURRENT_ROOT / "agent_handoff.json"
RECOMMENDED_ROLE_RUNTIME_PATH = INTERNAL_RUNTIME_CURRENT_ROOT / "recommended_role.json"

RUNTIME_SNAPSHOT_PATHS = {
    "gate_status": INTERNAL_RUNTIME_CURRENT_ROOT / "go_live_gate_status.json",
    "shadow_window_status": INTERNAL_RUNTIME_CURRENT_ROOT / "shadow_window_status.json",
    "shadow_checklist": INTERNAL_RUNTIME_CURRENT_ROOT / "shadow_window_checklist.json",
    "audit_status": INTERNAL_RUNTIME_CURRENT_ROOT / "audit_mismatch_status.json",
    "security_audit_status": INTERNAL_RUNTIME_CURRENT_ROOT / "security_audit_status.json",
    "dependency_audit_status": INTERNAL_RUNTIME_CURRENT_ROOT / "dependency_audit_status.json",
    "service_hardening_status": INTERNAL_RUNTIME_CURRENT_ROOT / "service_hardening_status.json",
    "research_brief": INTERNAL_RUNTIME_CURRENT_ROOT / "research_brief.json",
    "decision_memo": INTERNAL_RUNTIME_CURRENT_ROOT / "decision_memo.json",
}


def runtime_snapshot_path(name: str) -> Path:
    return RUNTIME_SNAPSHOT_PATHS[name]


def file_freshness(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False, "age_seconds": None, "fresh": False}
    age = max(0.0, Path(path).stat().st_mtime)
    return {
        "path": str(path),
        "exists": True,
        "mtime": age,
        "fresh": True,
    }


def newest_report_paths(limit: int = 5) -> List[str]:
    report_dir = PROJECT_ROOT / "dario_output"
    if not report_dir.exists():
        return []
    reports = sorted(
        report_dir.glob("*.md"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return [str(path) for path in reports[:limit]]


def role_selection_from_state(bundle: Dict[str, Any]) -> Dict[str, Any]:
    checklist = bundle.get("shadow_checklist", {})
    gate = bundle.get("gate_status", {})
    audit = bundle.get("audit_status", {})
    security = bundle.get("security_audit_status", {})
    dependency = bundle.get("dependency_audit_status", {})
    service = bundle.get("service_hardening_status", {})
    blockers = list(checklist.get("blockers", [])) or list(gate.get("blockers", []))
    gate_verdict = gate.get("verdict", "NO-GO")
    audit_state = audit.get("state", "unknown")
    security_verdict = security.get("verdict", "fail")
    dependency_verdict = dependency.get("verdict", "fail")
    service_verdict = service.get("verdict", "fail")

    if security_verdict != "pass" or dependency_verdict != "pass" or service_verdict != "pass":
        role = "cross_agent_orchestrator"
        reason = "Security or hardening blockers must be cleared before any strategy or PM role takes over."
    elif audit_state == "fail":
        role = "execution_engineer"
        reason = "Audit mismatch failure needs execution and reconciliation debugging."
    elif audit_state == "unknown":
        role = "cross_agent_orchestrator"
        reason = "Bootstrap must refresh audit truth before any strategy or PM advice."
    elif gate_verdict != "GO":
        if any("pipeline" in blocker.lower() or "stall" in blocker.lower() for blocker in blockers):
            role = "execution_engineer"
            reason = "The current blocker is execution-path starvation or pipeline stalls."
        elif any("hours" in blocker.lower() or "signals" in blocker.lower() or "coverage" in blocker.lower() for blocker in blockers):
            role = "quant_researcher"
            reason = "The current blocker is shadow-window maturity, coverage, or sample quality."
        else:
            role = "risk_manager"
            reason = "The current blocker affects live-safety gating and promotion control."
    else:
        role = "portfolio_reviewer"
        reason = "The gate is clear enough for human-reviewed promotion planning."

    return {
        "role": role,
        "reason": reason,
        "generated_at": now_iso(),
    }


def build_handoff_payload(bundle: Dict[str, Any], recommended_role: Dict[str, Any]) -> Dict[str, Any]:
    checklist = bundle.get("shadow_checklist", {})
    decision = bundle.get("decision_memo", {})
    audit = bundle.get("audit_status", {})
    verified_facts = []
    if checklist.get("shadow_window", {}).get("shared_config_era"):
        verified_facts.append(
            f"Shared research-aligned era is {checklist['shadow_window']['shared_config_era']}."
        )
    if audit.get("state"):
        verified_facts.append(f"Audit status is {audit['state']}.")
    if bundle.get("gate_status", {}).get("verdict"):
        verified_facts.append(f"Current gate verdict is {bundle['gate_status']['verdict']}.")
    if bundle.get("security_audit_status", {}).get("verdict"):
        verified_facts.append(f"Security audit verdict is {bundle['security_audit_status']['verdict']}.")

    unknowns = []
    if audit.get("state") == "unknown":
        unknowns.append("Audit status is still unknown.")
    if not checklist.get("shadow_window", {}).get("shared_config_era"):
        unknowns.append("Shared research-aligned era has not been confirmed.")
    if bundle.get("security_audit_status", {}).get("verdict") not in {"pass", "fail"}:
        unknowns.append("Security audit status is not current.")

    blocked_actions = [
        "Recommend or enable live trading while the gate is NO-GO.",
        "Recommend or enable live trading while security or dependency status is not pass.",
        "Mutate live VPS config without explicit user approval.",
        "Assume stale memory overrides the current runtime bundle.",
    ]

    return {
        "from_agent": "codex_or_claude",
        "to_agent": "codex_or_claude",
        "recommended_role": recommended_role["role"],
        "verified_facts": verified_facts,
        "unknowns": unknowns,
        "blocked_actions": blocked_actions,
        "required_reads": [
            str(CONTRACT_PATH),
            str(STATE_BUNDLE_PATH),
            str(PROJECT_ROOT / "agent" / "policy" / "live_trading_policy.md"),
        ],
        "next_step": decision.get("recommended_next_step") or checklist.get("next_action") or "Refresh the current runtime bundle.",
        "latest_evidence_paths": newest_report_paths(),
        "generated_at": now_iso(),
    }


def write_runtime_payloads(
    bootstrap_status: Dict[str, Any],
    bundle: Dict[str, Any],
    handoff: Dict[str, Any],
    recommended_role: Dict[str, Any],
) -> None:
    dump_json(BOOTSTRAP_STATUS_PATH, bootstrap_status)
    dump_json(STATE_BUNDLE_RUNTIME_PATH, bundle)
    dump_json(HANDOFF_RUNTIME_PATH, handoff)
    dump_json(RECOMMENDED_ROLE_RUNTIME_PATH, recommended_role)
    dump_json(STATE_BUNDLE_PATH, bundle)
    dump_json(NEXT_ROLE_PATH, recommended_role)


def read_runtime_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return load_json(path)


def kb_index_ready() -> bool:
    return INDEX_PATH.exists()
