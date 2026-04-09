#!/usr/bin/env python3
"""VPS Health Monitor Agent -- lightweight cron watchdog.

Checks service status, disk, CPU, memory, error logs.
Sends alert via Telegram if anything is wrong.
No Haiku call needed for routine checks -- only calls AI on anomalies.

Schedule: every 15 minutes
Cost: $0 (routine), ~$0.005 on anomaly
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from agent_framework import (
    ask_haiku, send_telegram, write_finding, get_journal_logs,
)

SERVICES = ["lagbot@emmanuel", "lagbot@polyphemus", "bulenox", "dashboard", "nginx"]


def check_services() -> dict:
    results = {}
    for svc in SERVICES:
        try:
            r = subprocess.run(["systemctl", "is-active", svc], capture_output=True, text=True, timeout=5)
            results[svc] = r.stdout.strip()
        except Exception:
            results[svc] = "unknown"
    return results


def check_resources() -> dict:
    try:
        # CPU load
        with open("/proc/loadavg") as f:
            load = float(f.read().split()[0])
        # Memory
        r = subprocess.run(["free", "-m"], capture_output=True, text=True, timeout=5)
        lines = r.stdout.strip().split("\n")
        mem_parts = lines[1].split()
        total_mb = int(mem_parts[1])
        used_mb = int(mem_parts[2])
        # Disk
        r = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5)
        disk_line = r.stdout.strip().split("\n")[1].split()
        disk_pct = int(disk_line[4].replace("%", ""))
        return {
            "cpu_load_1m": load,
            "mem_used_mb": used_mb,
            "mem_total_mb": total_mb,
            "mem_pct": round(used_mb / total_mb * 100, 1),
            "disk_pct": disk_pct,
        }
    except Exception as e:
        return {"error": str(e)}


def check_errors() -> dict:
    errors = {}
    for svc in ["lagbot@emmanuel", "lagbot@polyphemus", "bulenox"]:
        logs = get_journal_logs(svc, since="15 minutes ago", grep="error|traceback|exception")
        count = len([l for l in logs.split("\n") if l.strip()]) if logs.strip() else 0
        if count > 0:
            errors[svc] = {"count": count, "sample": logs[:300]}
    return errors


def main():
    agent_name = "vps_health"
    services = check_services()
    resources = check_resources()
    errors = check_errors()

    # Detect problems
    problems = []
    for svc, status in services.items():
        if status != "active":
            problems.append(f"\u274c {svc}: {status}")

    if resources.get("cpu_load_1m", 0) > 6:  # 75% of 8 cores
        problems.append(f"\u26a0\ufe0f CPU load: {resources['cpu_load_1m']}")
    if resources.get("mem_pct", 0) > 85:
        problems.append(f"\u26a0\ufe0f Memory: {resources['mem_pct']}%")
    if resources.get("disk_pct", 0) > 90:
        problems.append(f"\u26a0\ufe0f Disk: {resources['disk_pct']}%")

    for svc, err in errors.items():
        if err["count"] >= 3:
            problems.append(f"\u26a0\ufe0f {svc}: {err['count']} errors in 15m")

    if problems:
        # Alert
        msg = (
            f"\U0001f6a8 *VPS Health Alert*\n\n"
            + "\n".join(problems)
            + f"\n\nCPU: {resources.get('cpu_load_1m', '?')} | "
            f"RAM: {resources.get('mem_pct', '?')}% | "
            f"Disk: {resources.get('disk_pct', '?')}%"
        )
        send_telegram(msg)

        # If errors are complex, ask Haiku for diagnosis
        if errors:
            error_context = json.dumps(errors, indent=2)
            diagnosis = ask_haiku(
                system_prompt="You are a VPS operations monitor. Diagnose service errors concisely.",
                user_message=f"These errors occurred in the last 15 minutes:\n{error_context}\n\nDiagnose root cause in 2-3 sentences.",
                max_tokens=300,
            )
            write_finding(agent_name, "health_alert", "Service Errors Detected", diagnosis, "warning", errors)
    else:
        # Quiet -- just log a heartbeat every hour (not every 15 min)
        now = datetime.now(timezone.utc)
        if now.minute < 15:  # Only on the hour
            write_finding(
                agent_name, "health_ok",
                f"VPS Healthy {now.strftime('%H:%M UTC')}",
                f"All {len(SERVICES)} services active. CPU {resources.get('cpu_load_1m', '?')}, RAM {resources.get('mem_pct', '?')}%, Disk {resources.get('disk_pct', '?')}%",
                "info",
                {"services": services, "resources": resources},
            )


if __name__ == "__main__":
    main()
