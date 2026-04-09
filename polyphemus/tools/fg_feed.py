#!/usr/bin/env python3
"""Minimal Fear & Greed feed writer.

Fetches current F&G from alternative.me and writes to the path
signal_bot._read_market_context() expects. Run every 5 minutes via cron.

Output path: /opt/openclaw/data/lagbot_context.json
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

OUT_PATH = "/opt/openclaw/data/lagbot_context.json"
API_URL = "https://api.alternative.me/fng/?limit=1"
TIMEOUT = 10


def _regime(fg: int) -> str:
    if fg <= 20:
        return "extreme_fear"
    if fg <= 40:
        return "fear"
    if fg <= 60:
        return "neutral"
    if fg <= 80:
        return "greed"
    return "extreme_greed"


def main():
    try:
        req = urllib.request.Request(API_URL, headers={"User-Agent": "lagbot/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read())
        fg = int(data["data"][0]["value"])
    except Exception as e:
        print(f"ERROR fetching F&G: {e}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

    payload = {
        "fear_greed": fg,
        "market_regime": _regime(fg),
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "alternative.me",
    }

    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, OUT_PATH)

    print(f"F&G={fg} ({payload['market_regime']}) -> {OUT_PATH}")


if __name__ == "__main__":
    main()
