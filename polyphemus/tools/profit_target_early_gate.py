#!/usr/bin/env python3
"""
Profit Target Early Gate — counts DRY log events and gates live activation.

Gate criteria:
- n >= 10 DRY log events ("profit_target_early WOULD fire")
- Fee-corrected net gain logged (confirms PROFIT_TARGET_EARLY_APPLY_FEE_CORRECTION=true is active)
- All events must show net_gain > 0 (fee correction must be active)

Usage:
    python3 tools/profit_target_early_gate.py
    python3 tools/profit_target_early_gate.py --since "2h"
    python3 tools/profit_target_early_gate.py --logfile /path/to/logfile.txt

The tool reads from journalctl by default (for VPS). Pass --logfile for local testing.
"""

import argparse
import re
import subprocess
import sys
from typing import Optional


MIN_N = 10
DRY_PATTERN = re.compile(
    r"\[DRY\] profit_target_early WOULD fire.*?"
    r"gross=(?P<gross>[\d.]+)pp.*?"
    r"fee=(?P<fee>[\d.]+)pp.*?"
    r"net=(?P<net>-?[\d.]+)pp",
    re.DOTALL,
)

# Legacy pattern (without fee correction) — used to detect old-format events
LEGACY_PATTERN = re.compile(r"\[DRY\] profit_target_early WOULD fire.*?gain=(?P<gain>[\d.]+)pp")


def parse_events(text: str) -> list[dict]:
    events = []
    for m in DRY_PATTERN.finditer(text):
        events.append({
            "gross": float(m.group("gross")),
            "fee": float(m.group("fee")),
            "net": float(m.group("net")),
            "has_fee_correction": True,
        })
    # Count legacy events (fee correction NOT active — these don't count toward gate)
    legacy_count = len(LEGACY_PATTERN.findall(text)) - len(events)
    return events, max(0, legacy_count)


def fetch_from_journalctl(since: str, unit: str) -> str:
    cmd = ["journalctl", f"--since={since}", f"-u={unit}", "--no-pager"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout


def run_gate(logfile: Optional[str], since: str, unit: str) -> int:
    if logfile:
        with open(logfile) as f:
            text = f.read()
    else:
        text = fetch_from_journalctl(since, unit)

    events, legacy_count = parse_events(text)
    n = len(events)
    bad_events = [e for e in events if e["net"] <= 0]

    print(f"Profit Target Early Gate")
    print(f"  DRY events (with fee correction): {n} / {MIN_N} required")
    if legacy_count > 0:
        print(f"  Legacy events (NO fee correction — do NOT count): {legacy_count}")
        print(f"  WARNING: Fee correction not active during {legacy_count} events. Deploy fix first.")
    if bad_events:
        print(f"  WARNING: {len(bad_events)} events with net_gain <= 0 (would fire at a loss after fees)")
    print()

    if n > 0:
        nets = [e["net"] for e in events]
        print(f"  Net gain range: {min(nets):.4f}pp to {max(nets):.4f}pp")
        print(f"  Avg net gain: {sum(nets)/len(nets):.4f}pp")
        print()

    gate_n = n >= MIN_N
    gate_no_legacy = legacy_count == 0
    gate_positive = len(bad_events) == 0

    if gate_n and gate_no_legacy and gate_positive:
        print("GATE: PASS — Set PROFIT_TARGET_EARLY_DRY_RUN=false to go live.")
        return 0
    else:
        reasons = []
        if not gate_n:
            reasons.append(f"need {MIN_N - n} more fee-corrected DRY events")
        if not gate_no_legacy:
            reasons.append(f"{legacy_count} legacy events (fee correction was off) — reset and recount")
        if not gate_positive:
            reasons.append(f"{len(bad_events)} events fire at net loss after fees — raise profit_target_early_pp")
        print(f"GATE: NO-GO — {'; '.join(reasons)}")
        return 1


def main():
    parser = argparse.ArgumentParser(description="Profit target early gate check")
    parser.add_argument("--logfile", help="Parse this log file instead of journalctl")
    parser.add_argument("--since", default="7d", help="journalctl --since (default: 7d)")
    parser.add_argument("--unit", default="lagbot@emmanuel", help="systemd unit name")
    args = parser.parse_args()
    sys.exit(run_gate(args.logfile, args.since, args.unit))


if __name__ == "__main__":
    main()
