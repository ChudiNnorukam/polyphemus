#!/usr/bin/env python3
"""Check the local systemd unit baseline for the bot runtime."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .kb_common import PROJECT_ROOT, dump_json, now_iso
except ImportError:  # pragma: no cover - direct script execution
    from kb_common import PROJECT_ROOT, dump_json, now_iso


SERVICE_PATH = PROJECT_ROOT / "polyphemus.service"
REQUIRED_DIRECTIVES = {
    "NoNewPrivileges": "true",
    "PrivateTmp": "true",
    "PrivateDevices": "true",
    "ProtectControlGroups": "true",
    "ProtectKernelModules": "true",
    "ProtectKernelTunables": "true",
    "ProtectHostname": "true",
    "ProtectClock": "true",
    "ProtectHome": "true",
    "RestrictSUIDSGID": "true",
    "LockPersonality": "true",
    "MemoryDenyWriteExecute": "true",
    "RestrictRealtime": "true",
    "SystemCallArchitectures": "native",
    "UMask": "0077",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def parse_service(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def build_status() -> dict:
    actual = parse_service(SERVICE_PATH)
    checks = []
    missing = []
    for directive, expected in REQUIRED_DIRECTIVES.items():
        actual_value = actual.get(directive, "")
        present = actual_value.lower() == expected.lower()
        checks.append(
            {
                "directive": directive,
                "expected": expected,
                "actual": actual_value,
                "present": present,
            }
        )
        if not present:
            missing.append(directive)
    return {
        "generated_at": now_iso(),
        "service_name": "polyphemus.service",
        "service_path": str(SERVICE_PATH),
        "checks": checks,
        "missing_controls": missing,
        "verdict": "pass" if not missing else "fail",
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
