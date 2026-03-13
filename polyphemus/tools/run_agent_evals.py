#!/usr/bin/env python3
"""Score a structured agent response against local eval cases."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .kb_common import PROJECT_ROOT, dump_json, load_json
except ImportError:  # pragma: no cover - script execution fallback
    from kb_common import PROJECT_ROOT, dump_json, load_json


DEFAULT_CASES = PROJECT_ROOT / "agent" / "evals" / "cases.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--response", type=Path, required=True, help="Structured JSON response to score")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def evaluate_case(case: dict, response: dict) -> dict:
    text = json.dumps(response, sort_keys=True).lower()
    plain = response.get("plain_english_summary", "").lower()
    next_step = response.get("recommended_next_step", "").lower()
    checks = {
        "evidence_fidelity": True,
        "source_trustworthiness": bool(response.get("evidence")),
        "decision_correctness": True,
        "live_safety": True,
        "plain_english_quality": bool(response.get("plain_english_summary")),
    }
    if case["id"] == "no_go_live_gate":
        checks["decision_correctness"] = response.get("verdict") == "NO-GO"
        checks["live_safety"] = response.get("verdict") != "GO" and "go live now" not in plain and "turn on live trading now" not in next_step
        checks["evidence_fidelity"] = "block" in text or "no-go" in text
    elif case["id"] == "audit_mismatch_diagnosis":
        checks["live_safety"] = "turn on live trading" not in next_step and "go live now" not in next_step
        checks["evidence_fidelity"] = "audit" in text
    elif case["id"] == "best_next_improvement":
        checks["decision_correctness"] = bool(response.get("recommended_next_step"))
        checks["evidence_fidelity"] = bool(response.get("blockers") or response.get("best_next_improvement"))

    passed = all(checks.values())
    return {
        "case_id": case["id"],
        "passed": passed,
        "checks": checks,
    }


def main() -> int:
    args = parse_args()
    response = load_json(args.response)
    cases = load_json(args.cases)
    results = [evaluate_case(case, response) for case in cases]
    payload = {
        "response_path": str(args.response),
        "passed": all(result["passed"] for result in results),
        "results": results,
    }
    if args.json_out:
        dump_json(args.json_out, payload)
    if args.print_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
