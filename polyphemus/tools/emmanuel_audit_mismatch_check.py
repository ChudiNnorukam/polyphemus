#!/usr/bin/env python3
"""Diagnose the emmanuel startup CLOB↔DB trade audit mismatch."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from . import backtester
except ImportError:  # pragma: no cover - direct script execution
    import backtester


JOURNAL_CMD = [
    "ssh",
    "root@82.24.19.114",
    "journalctl -u lagbot@emmanuel --since '7 days ago' --no-pager",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def latest_audit_line(journal_text: str) -> str:
    lines = [
        line.strip()
        for line in journal_text.splitlines()
        if "CLOB↔DB trade audit" in line
    ]
    return lines[-1] if lines else ""


def recent_db_order_hash_count() -> int:
    db_path = backtester.LOCAL_CACHE / "emmanuel" / "performance.db"
    if not db_path.exists():
        return 0
    cutoff = time.time() - (24 * 3600)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT trade_id, entry_tx_hash, exit_tx_hash
            FROM trades
            WHERE
                (entry_time IS NOT NULL AND entry_time > ?)
                OR (exit_time IS NOT NULL AND exit_time > ?)
            """,
            (cutoff, cutoff),
        ).fetchall()
    finally:
        conn.close()
    order_ids = set()
    for row in rows:
        for key in ("trade_id", "entry_tx_hash", "exit_tx_hash"):
            value = row[key]
            if value:
                order_ids.add(str(value))
    return len(order_ids)


def main_for_status() -> dict:
    journal = subprocess.run(JOURNAL_CMD, capture_output=True, text=True, check=False)
    line = latest_audit_line(journal.stdout if journal.returncode == 0 else "")
    if "FAILED" in line:
        state = "fail"
        probable = "recent CLOB order ids did not match recent DB entry/exit hashes closely enough"
    elif "OK:" in line or "No trades in last" in line:
        state = "pass"
        probable = "recent CLOB and DB order hashes are aligned"
    else:
        state = "unknown"
        probable = "no recent audit line found or SSH/journal access failed"
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "state": state,
        "latest_evidence": line or (journal.stderr.strip() if journal.stderr else "no audit line available"),
        "probable_cause": probable,
        "recommended_next_step": (
            "refresh caches and inspect recent CLOB order ids vs DB entry/exit hashes"
            if state in {"fail", "unknown"}
            else "continue monitoring"
        ),
        "cached_recent_db_order_hashes_24h": recent_db_order_hash_count(),
        "ssh_ok": journal.returncode == 0,
    }


def main() -> int:
    args = parse_args()
    status = main_for_status()
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
