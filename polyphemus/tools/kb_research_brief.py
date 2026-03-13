#!/usr/bin/env python3
"""Produce a plain-English research brief from the KB and current runtime state."""

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


def run_query(query: str, limit: int = 3) -> list[dict]:
    payload = run_script("kb_query.py", "--query", query, "--limit", str(limit))
    return payload.get("hits", [])


def build_research_brief() -> dict:
    checklist = run_script("shadow_window_checklist.py")
    gate = run_script("btc5m_ensemble_go_live_gate.py")
    market_structure_hits = run_query("Polymarket orderbook websocket matching engine execution")
    risk_hits = run_query("negative live pnl gate no-go deflated sharpe calibration")
    execution_hits = run_query("matching engine error codes placement failure fill timeout")
    repo_hits = run_query("btc5m strategy shadow scan go live gate progress")

    if gate["verdict"] == "GO":
        outlook = "The current gate says GO, but live changes still require human review."
    else:
        outlook = "The bot is not live-ready yet. Current evidence says NO-GO."

    next_step = checklist["next_action"]
    best_next_improvement = checklist["blockers"][0] if checklist["blockers"] else next_step
    return {
        "generated_at": checklist["generated_at"],
        "outlook": outlook,
        "single_next_step": next_step,
        "best_next_improvement": best_next_improvement,
        "market_structure": {
            "summary": "Polymarket execution assumptions must be grounded in official orderbook, matching-engine, and WebSocket docs.",
            "citations": market_structure_hits,
        },
        "execution": {
            "summary": "Execution quality is still a live bottleneck, so runtime errors and fill behavior matter as much as signal quality.",
            "citations": execution_hits,
        },
        "risk": {
            "summary": "Current live P&L and gate blockers override attractive replay stories. Small size does not validate a negative process.",
            "citations": risk_hits,
        },
        "current_repo_evidence": {
            "summary": "Use the latest gate, shadow checklist, and strategy scans before making any promotion claim.",
            "citations": repo_hits,
        },
    }


def main() -> int:
    args = parse_args()
    payload = build_research_brief()
    if args.json_out:
        dump_json(args.json_out, payload)
    if args.print_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
