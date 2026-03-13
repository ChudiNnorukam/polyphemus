#!/usr/bin/env python3
"""Assess dependency posture for shipped and build-only surfaces."""

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
REQUIREMENTS_PATH = PROJECT_ROOT / "requirements.txt"
REQUIREMENTS_LOCK_PATH = PROJECT_ROOT / "requirements-lock.txt"
PYTHON_LOCK_PACKAGES = {
    "py_clob_client",
    "py-builder-relayer-client",
    "aiohttp",
    "pydantic",
    "pydantic-settings",
    "python-dotenv",
    "web3",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def run_cmd(cmd: list[str], cwd: Path) -> tuple[int, dict, str]:
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=False)
    payload = {}
    stdout = result.stdout.strip()
    if stdout:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = {}
    return result.returncode, payload, result.stderr.strip() or stdout


def summarize_vulnerabilities(payload: dict) -> list[dict]:
    findings = []
    for name, details in sorted(payload.get("vulnerabilities", {}).items()):
        via_entries = details.get("via", [])
        advisories = []
        for entry in via_entries:
            if isinstance(entry, dict):
                advisories.append(
                    {
                        "title": entry.get("title", ""),
                        "severity": entry.get("severity", details.get("severity", "unknown")),
                        "url": entry.get("url", ""),
                        "range": entry.get("range", details.get("range", "")),
                    }
                )
        findings.append(
            {
                "package": name,
                "severity": details.get("severity", "unknown"),
                "range": details.get("range", ""),
                "fix_available": bool(details.get("fixAvailable")),
                "advisories": advisories,
            }
        )
    return findings


def evaluate_frontend() -> dict:
    all_code, all_payload, all_err = run_cmd(["npm", "audit", "--json"], FRONTEND_ROOT)
    prod_code, prod_payload, prod_err = run_cmd(["npm", "audit", "--omit=dev", "--json"], FRONTEND_ROOT)
    all_findings = summarize_vulnerabilities(all_payload)
    prod_findings = summarize_vulnerabilities(prod_payload)
    prod_blockers = [
        finding
        for finding in prod_findings
        if finding["severity"] in {"high", "critical"}
    ]
    dev_only_findings = [finding for finding in all_findings if finding["package"] not in {x["package"] for x in prod_findings}]
    return {
        "tool": "npm audit",
        "workspace": str(FRONTEND_ROOT),
        "all_exit_code": all_code,
        "prod_exit_code": prod_code,
        "all_findings": all_findings,
        "prod_findings": prod_findings,
        "dev_only_findings": dev_only_findings,
        "raw_total": all_payload.get("metadata", {}).get("vulnerabilities", {}).get("total", 0),
        "prod_total": prod_payload.get("metadata", {}).get("vulnerabilities", {}).get("total", 0),
        "blocking_findings": prod_blockers,
        "shipped_surface_clean": len(prod_blockers) == 0,
        "notes": (
            "Production dependencies are clean; remaining audit findings are build/dev-only."
            if len(prod_blockers) == 0 and all_findings
            else all_err or prod_err or ""
        ),
    }


def parse_requirement_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def evaluate_python() -> dict:
    req_lines = parse_requirement_lines(REQUIREMENTS_PATH)
    lock_lines = parse_requirement_lines(REQUIREMENTS_LOCK_PATH)
    exact_pins = {}
    unpinned = []
    for line in lock_lines:
        if "==" not in line:
            unpinned.append(line)
            continue
        name, version = line.split("==", 1)
        exact_pins[name.strip()] = version.strip()
    command = [sys.executable, "-m", "pip", "show", *sorted(PYTHON_LOCK_PACKAGES)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    installed_versions = {}
    if result.stdout:
        name = None
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if line.startswith("Name: "):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("Version: ") and name:
                installed_versions[name] = line.split(":", 1)[1].strip()
                name = None
    mismatch = []
    for name, pinned_version in exact_pins.items():
        installed = installed_versions.get(name)
        if installed and installed != pinned_version:
            mismatch.append({"package": name, "expected": pinned_version, "installed": installed})
    missing_pins = sorted(PYTHON_LOCK_PACKAGES - set(exact_pins.keys()))
    blocking_findings = []
    if req_lines != ["-r requirements-lock.txt"]:
        blocking_findings.append("requirements.txt must point only to requirements-lock.txt")
    if unpinned:
        blocking_findings.append(f"requirements-lock.txt contains non-exact entries: {', '.join(unpinned)}")
    if missing_pins:
        blocking_findings.append(f"requirements-lock.txt is missing packages: {', '.join(missing_pins)}")
    if mismatch:
        blocking_findings.append("Installed package versions do not match requirements-lock.txt")
    return {
        "tool": "pinned-requirements-check",
        "requirements_path": str(REQUIREMENTS_PATH),
        "requirements_lock_path": str(REQUIREMENTS_LOCK_PATH),
        "pip_audit_available": False,
        "requirements_file_clean": req_lines == ["-r requirements-lock.txt"],
        "lock_exact_pins": exact_pins,
        "installed_versions": installed_versions,
        "version_mismatches": mismatch,
        "blocking_findings": blocking_findings,
        "notes": (
            "Python packages are exact-pinned through requirements-lock.txt; no external vulnerability scanner is installed locally."
        ),
    }


def build_status() -> dict:
    frontend = evaluate_frontend()
    python = evaluate_python()
    blocking_findings = []
    if frontend["blocking_findings"]:
        blocking_findings.extend(
            [f"frontend:{item['package']}:{item['severity']}" for item in frontend["blocking_findings"]]
        )
    if python["blocking_findings"]:
        blocking_findings.extend([f"python:{item}" for item in python["blocking_findings"]])
    return {
        "generated_at": now_iso(),
        "frontend": frontend,
        "python": python,
        "blocking_findings": blocking_findings,
        "verdict": "pass" if not blocking_findings else "fail",
    }


def main() -> int:
    args = parse_args()
    payload = build_status()
    if args.json_out:
        dump_json(args.json_out, payload)
    if args.print_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
