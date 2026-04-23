#!/usr/bin/env python3
"""Capture hourly shadow-eval snapshots from a dashboard API.

Standalone by design: safe to run from cron on VPS or from a local tunnel.
No imports from lagbot runtime modules.

Usage:
    python3 tools/shadow_eval_snapshot.py --instance polyphemus
    python3 tools/shadow_eval_snapshot.py --base-url http://127.0.0.1:8083 --instance polyphemus
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


def read_env(env_path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not env_path.exists():
        return env
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def resolve_instance_paths(instance: str) -> tuple[Path, Path]:
    vps_env_path = Path(f"/opt/lagbot/instances/{instance}/.env")
    local_root = Path(__file__).resolve().parents[1]
    local_env_path = local_root / "data" / "shadow_eval" / instance / ".env.placeholder"
    env_path = vps_env_path if vps_env_path.exists() else local_env_path
    if vps_env_path.exists():
        data_dir = Path(f"/opt/lagbot/instances/{instance}/data/shadow_eval")
    else:
        data_dir = local_root / "data" / "shadow_eval" / instance
    return env_path, data_dir


def get_base_url(instance: str, explicit_base_url: str | None) -> str:
    if explicit_base_url:
        return explicit_base_url.rstrip("/")
    env_path, _ = resolve_instance_paths(instance)
    env = read_env(env_path)
    host = env.get("DASHBOARD_HOST", "127.0.0.1") or "127.0.0.1"
    port = env.get("DASHBOARD_PORT", "8080") or "8080"
    return f"http://{host}:{port}"


def _read_proc_environ(pid: str) -> dict[str, str]:
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes().decode("utf-8", errors="ignore")
    except Exception:
        return {}
    env: dict[str, str] = {}
    for chunk in raw.split("\0"):
        if "=" in chunk:
            key, value = chunk.split("=", 1)
            env[key] = value
    return env


def _find_python_descendant(root_pid: str) -> str | None:
    """BFS the descendant tree of root_pid; return the first python process PID."""
    seen: set[str] = set()
    stack = [root_pid]
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        try:
            comm = Path(f"/proc/{pid}/comm").read_text().strip()
        except Exception:
            comm = ""
        if pid != root_pid and comm.startswith("python"):
            return pid
        try:
            children = subprocess.run(
                ["pgrep", "-P", pid],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout.split()
        except Exception:
            children = []
        stack.extend(children)
    return None


def read_service_env(instance: str) -> dict[str, str]:
    # The service's MainPID is often a wrapper (e.g. doppler) that does not
    # carry the env vars injected via the drop-in's `env K=V ...` prefix —
    # those land in the python child's environment only. Walk descendants
    # and read the python process's /proc/<pid>/environ when present.
    service = f"lagbot@{instance}"
    try:
        main_pid = subprocess.run(
            ["systemctl", "show", "-p", "MainPID", "--value", service],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout.strip()
    except Exception:
        return {}
    if not main_pid or main_pid == "0":
        return {}
    target_pid = _find_python_descendant(main_pid) or main_pid
    return _read_proc_environ(target_pid)


def fetch_json(url: str) -> dict:
    try:
        with urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        return {"_error": str(exc), "_url": url}


def join_list(values: object) -> str | None:
    if isinstance(values, list):
        filtered = [str(v) for v in values if str(v)]
        return ",".join(filtered) if filtered else None
    if values is None:
        return None
    value = str(values).strip()
    return value or None


def build_snapshot(instance: str, base_url: str) -> dict:
    env_path, _ = resolve_instance_paths(instance)
    file_env = read_env(env_path)
    service_env = read_service_env(instance)
    env_overlay = {k: v for k, v in service_env.items() if k in {
        "DRY_RUN",
        "ACCUM_DRY_RUN",
        "ACCUM_ENTRY_MODE",
        "ACCUM_ASSETS",
        "ACCUM_WINDOW_TYPES",
        "ASSET_FILTER",
        "DASHBOARD_PORT",
    }}
    env = dict(file_env)
    env.update(env_overlay)
    status = fetch_json(f"{base_url}/api/status")
    accumulator = fetch_json(f"{base_url}/api/accumulator")
    pipeline = fetch_json(f"{base_url}/api/pipeline")
    balance = fetch_json(f"{base_url}/api/balance")

    runtime_assets = (
        join_list(accumulator.get("assets"))
        or join_list(status.get("accumulator_assets"))
        or env.get("ACCUM_ASSETS")
    )
    runtime_windows = (
        join_list(accumulator.get("window_types"))
        or join_list(status.get("accumulator_window_types"))
        or env.get("ACCUM_WINDOW_TYPES")
    )
    runtime_entry_mode = (
        accumulator.get("entry_mode")
        or status.get("accumulator_entry_mode")
        or env.get("ACCUM_ENTRY_MODE")
    )
    runtime_dry_run = status.get("dry_run")
    runtime_accum_dry_run = accumulator.get("accum_dry_run", status.get("accum_dry_run"))
    runtime_effective_accum_dry_run = accumulator.get(
        "effective_accumulator_dry_run",
        status.get("effective_accumulator_dry_run"),
    )
    now = datetime.now(timezone.utc)
    return {
        "captured_at": now.isoformat(),
        "captured_ts": now.timestamp(),
        "instance": instance,
        "base_url": base_url,
        "config": {
            "dry_run": str(runtime_dry_run).lower() if runtime_dry_run is not None else env.get("DRY_RUN"),
            "accum_dry_run": str(runtime_accum_dry_run).lower() if runtime_accum_dry_run is not None else env.get("ACCUM_DRY_RUN"),
            "effective_accumulator_dry_run": (
                str(runtime_effective_accum_dry_run).lower()
                if runtime_effective_accum_dry_run is not None
                else None
            ),
            "accum_entry_mode": runtime_entry_mode,
            "accum_assets": runtime_assets,
            "accum_window_types": runtime_windows,
            "asset_filter": env.get("ASSET_FILTER"),
            "dashboard_port": env.get("DASHBOARD_PORT"),
        },
        "config_debug": {
            "env_file": {
                "dry_run": file_env.get("DRY_RUN"),
                "accum_dry_run": file_env.get("ACCUM_DRY_RUN"),
                "accum_entry_mode": file_env.get("ACCUM_ENTRY_MODE"),
                "accum_assets": file_env.get("ACCUM_ASSETS"),
                "accum_window_types": file_env.get("ACCUM_WINDOW_TYPES"),
                "asset_filter": file_env.get("ASSET_FILTER"),
                "dashboard_port": file_env.get("DASHBOARD_PORT"),
            },
            "service_env": {
                "dry_run": env_overlay.get("DRY_RUN"),
                "accum_dry_run": env_overlay.get("ACCUM_DRY_RUN"),
                "accum_entry_mode": env_overlay.get("ACCUM_ENTRY_MODE"),
                "accum_assets": env_overlay.get("ACCUM_ASSETS"),
                "accum_window_types": env_overlay.get("ACCUM_WINDOW_TYPES"),
                "asset_filter": env_overlay.get("ASSET_FILTER"),
                "dashboard_port": env_overlay.get("DASHBOARD_PORT"),
            },
        },
        "status": status,
        "accumulator": accumulator,
        "pipeline": pipeline,
        "balance": balance,
    }


def append_snapshot(snapshot: dict, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "hourly_snapshots.jsonl"
    with out_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot, sort_keys=True) + "\n")
    return out_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture hourly shadow eval snapshots")
    parser.add_argument("--instance", default="polyphemus")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--output-dir", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env_path, default_output_dir = resolve_instance_paths(args.instance)
    _ = env_path  # path resolved for side effects / future debugging
    base_url = get_base_url(args.instance, args.base_url or None)
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir
    snapshot = build_snapshot(args.instance, base_url)
    out_path = append_snapshot(snapshot, output_dir)
    print(
        json.dumps(
            {
                "ok": True,
                "instance": args.instance,
                "base_url": base_url,
                "output_path": str(out_path),
                "captured_at": snapshot["captured_at"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
