#!/usr/bin/env python3
"""Fear & Greed watchdog - auto-start/stop lagbot instances based on market fear level.

Runs via cron every 30 min. Starts bots when F&G > threshold, stops when <= threshold.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from urllib.request import urlopen

THRESHOLD = int(os.environ.get("FG_THRESHOLD", 25))
INSTANCES = ["emmanuel", "polyphemus"]
LOG_PREFIX = "[fg_watchdog]"


def get_fear_greed() -> int:
    """Fetch current Fear & Greed index from alternative.me."""
    try:
        resp = urlopen("https://api.alternative.me/fng/?limit=1", timeout=10)
        data = json.loads(resp.read())
        return int(data["data"][0]["value"])
    except Exception as e:
        print(f"{LOG_PREFIX} ERROR fetching F&G: {e}")
        return -1


def is_active(instance: str) -> bool:
    result = subprocess.run(
        ["systemctl", "is-active", f"lagbot@{instance}"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() == "active"


def start(instance: str):
    subprocess.run(["systemctl", "start", f"lagbot@{instance}"], check=True)
    print(f"{LOG_PREFIX} STARTED lagbot@{instance}")


def stop(instance: str):
    subprocess.run(["systemctl", "stop", f"lagbot@{instance}"], check=True)
    print(f"{LOG_PREFIX} STOPPED lagbot@{instance}")


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    fg = get_fear_greed()

    if fg < 0:
        print(f"{LOG_PREFIX} {now} | F&G fetch failed, skipping")
        return

    print(f"{LOG_PREFIX} {now} | F&G={fg} threshold={THRESHOLD}")

    for inst in INSTANCES:
        active = is_active(inst)

        if fg > THRESHOLD and not active:
            print(f"{LOG_PREFIX} F&G={fg} > {THRESHOLD}, starting {inst}")
            start(inst)
        elif fg <= THRESHOLD and active:
            print(f"{LOG_PREFIX} F&G={fg} <= {THRESHOLD}, stopping {inst}")
            stop(inst)
        else:
            state = "running" if active else "stopped"
            print(f"{LOG_PREFIX} {inst} {state}, no action needed")


if __name__ == "__main__":
    main()
