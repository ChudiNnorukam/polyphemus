#!/usr/bin/env python3
"""Refresh cached VPS artifacts used by the operator workflow."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import backtester


DEFAULT_CONTEXT_REMOTE = "root@82.24.19.114:/opt/openclaw/data/lagbot_context.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instances", nargs="+", default=["emmanuel", "polyphemus"])
    parser.add_argument("--context-remote", default=DEFAULT_CONTEXT_REMOTE)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def scp_artifact(remote: str, local: Path) -> dict:
    local.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["scp", "-q", remote, str(local)],
        capture_output=True,
        text=True,
    )
    ok = result.returncode == 0 and local.exists() and local.stat().st_size > 0
    return {
        "remote": remote,
        "local": str(local),
        "ok": ok,
        "stderr": result.stderr.strip(),
        "size": local.stat().st_size if ok else 0,
    }


def refresh_instance(instance: str, context_remote: str) -> dict:
    cache_dir = backtester.LOCAL_CACHE / instance
    signals_db, performance_db = backtester.download_dbs(instance)
    env_result = scp_artifact(
        f"{backtester.VPS_HOST}:/opt/lagbot/instances/{instance}/.env",
        cache_dir / ".env",
    )
    context_result = scp_artifact(context_remote, cache_dir / "lagbot_context.json")
    return {
        "instance": instance,
        "signals_db": {
            "ok": signals_db is not None,
            "local": str(signals_db) if signals_db else str(cache_dir / "signals.db"),
        },
        "performance_db": {
            "ok": performance_db is not None,
            "local": str(performance_db) if performance_db else str(cache_dir / "performance.db"),
        },
        "env": env_result,
        "context": context_result,
    }


def main() -> int:
    args = parse_args()
    status = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "instances": [refresh_instance(instance, args.context_remote) for instance in args.instances],
    }
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(status, indent=2, sort_keys=True), encoding="utf-8")
    if args.print_json:
        print(json.dumps(status, indent=2, sort_keys=True))
    else:
        print(json.dumps(status))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
