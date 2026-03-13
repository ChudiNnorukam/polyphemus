#!/usr/bin/env python3
"""Run the deterministic local hardening and quality gate."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

try:
    from .kb_common import PROJECT_ROOT, dump_json, now_iso
except ImportError:  # pragma: no cover - direct script execution
    from kb_common import PROJECT_ROOT, dump_json, now_iso


FRONTEND_ROOT = PROJECT_ROOT.parent / "bot-dashboard" / "frontend"
EMMANUEL_ENV = PROJECT_ROOT / "tools" / ".backtest_cache" / "emmanuel" / ".env"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def run_step(name: str, cmd: list[str], cwd: Path) -> dict:
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=False)
    return {
        "name": name,
        "command": cmd,
        "cwd": str(cwd),
        "exit_code": result.returncode,
        "passed": result.returncode == 0,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
    }


def build_payload() -> dict:
    python_bin = sys.executable
    py_files = [str(path) for path in PROJECT_ROOT.rglob("*.py") if ".backtest_cache" not in str(path)]
    steps = [
        run_step("python_compile", [python_bin, "-m", "py_compile", *py_files], PROJECT_ROOT.parent),
        run_step(
            "focused_smoke",
            [python_bin, str(PROJECT_ROOT / "tools" / "quality_gate_smoke.py"), "--print-json"],
            PROJECT_ROOT.parent,
        ),
        run_step("desktop_build", ["npm", "run", "build"], FRONTEND_ROOT),
        run_step("dependency_audit_status", [python_bin, str(PROJECT_ROOT / "tools" / "dependency_audit_status.py"), "--print-json"], PROJECT_ROOT.parent),
        run_step(
            "startup_check",
            [
                python_bin,
                "-m",
                "polyphemus.startup_check",
                "--env",
                str(EMMANUEL_ENV),
                "--expected",
                str(PROJECT_ROOT / "config_expected.json"),
                "--no-halt",
            ],
            PROJECT_ROOT.parent,
        ),
        run_step("service_hardening_status", [python_bin, str(PROJECT_ROOT / "tools" / "service_hardening_status.py"), "--print-json"], PROJECT_ROOT.parent),
        run_step("security_best_practices_report", [python_bin, str(PROJECT_ROOT / "tools" / "security_best_practices_report.py"), "--print-json"], PROJECT_ROOT.parent),
        run_step("agent_bootstrap", [python_bin, str(PROJECT_ROOT / "tools" / "agent_bootstrap.py"), "--print-json"], PROJECT_ROOT.parent),
    ]
    blocking = []
    for step in steps:
        if step["name"] == "startup_check":
            if step["exit_code"] == 2:
                blocking.append("startup_check reported CRITICAL findings")
            continue
        if not step["passed"]:
            blocking.append(f"{step['name']} failed")
    return {
        "generated_at": now_iso(),
        "verdict": "pass" if not blocking else "fail",
        "blocking_findings": blocking,
        "steps": steps,
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
    return 0 if payload["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
