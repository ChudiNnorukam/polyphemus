#!/usr/bin/env python3
"""Generate a promotion-ready decision memo from runtime state and KB retrieval."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

try:
    from .kb_common import PROJECT_ROOT, dump_json
except ImportError:  # pragma: no cover - script execution fallback
    from kb_common import PROJECT_ROOT, dump_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def run_script(script_name: str, *extra_args: str) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
        json_path = Path(handle.name)
    cmd = [
        "python3",
        str(PROJECT_ROOT / "tools" / script_name),
        "--json-out",
        str(json_path),
        "--print-json",
        *extra_args,
    ]
    subprocess.run(cmd, cwd=str(PROJECT_ROOT.parent), capture_output=True, text=True, check=True)
    return json.loads(json_path.read_text(encoding="utf-8"))


def run_query(query: str, limit: int = 4) -> list[dict]:
    payload = run_script("kb_query.py", "--query", query, "--limit", str(limit))
    return payload.get("hits", [])


def build_decision_memo() -> dict:
    checklist = run_script("shadow_window_checklist.py")
    gate = run_script("btc5m_ensemble_go_live_gate.py")
    audit = run_script("emmanuel_audit_mismatch_check.py")
    sources = run_query("go live blockers audit mismatch negative live pnl execution")

    can_go_live = gate["verdict"] == "GO" and audit["state"] == "pass"
    irresponsible = [
        "Turning on live trading because a replay looks strong.",
        "Ignoring the current gate verdict or audit blocker.",
        "Assuming small bet size makes an unvalidated strategy safe.",
    ]
    if checklist["blockers"]:
        recommended_next_step = checklist["next_action"]
        blockers = checklist["blockers"]
    else:
        recommended_next_step = "Human review of the narrow emmanuel-only promotion plan."
        blockers = []

    plain = (
        "Nothing new should go live yet. "
        + (blockers[0] if blockers else "The gate is clear, but a human still needs to review the narrow promotion plan.")
    )
    return {
        "generated_at": checklist["generated_at"],
        "verdict": "GO" if can_go_live else "NO-GO",
        "can_go_live_now": can_go_live,
        "blockers": blockers,
        "recommended_next_step": recommended_next_step,
        "best_next_improvement": blockers[0] if blockers else "Keep the narrow promotion shape and verify execution after activation.",
        "irresponsible_actions": irresponsible,
        "evidence": [
            {
                "label": "go_live_gate_status",
                "summary": gate.get("blockers", []),
            },
            {
                "label": "shadow_window_checklist",
                "summary": checklist.get("blockers", []),
            },
            {
                "label": "audit_mismatch_status",
                "summary": audit.get("latest_evidence", ""),
            },
            {
                "label": "knowledge_hits",
                "summary": sources,
            },
        ],
        "plain_english_summary": plain,
    }


def main() -> int:
    args = parse_args()
    payload = build_decision_memo()
    if args.json_out:
        dump_json(args.json_out, payload)
    if args.print_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
