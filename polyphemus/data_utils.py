"""Data utilities for Polyphemus prediction market trading bot.

Eliminates repeated slug parsing, direction normalization, and data access
patterns that have caused bugs across 4+ sessions (case mismatch, missing
metadata, corrupted labels).

Born from Mar 22 2026: 37% of signal labels were inverted due to case
sensitivity in direction matching. Every research session reinvented slug
parsing. DB corruption went undetected for days.

Usage:
    from polyphemus.data_utils import (
        parse_slug, normalize_direction, get_enriched_trades,
        get_labeled_signals, health_check
    )
"""

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class SlugParts:
    asset: str       # BTC, ETH, SOL, XRP (uppercase)
    window: str      # 5m, 15m
    window_secs: int # 300, 900
    epoch: int       # Unix timestamp of market end
    raw: str         # Original slug


def parse_slug(slug: str) -> Optional[SlugParts]:
    """Parse a Polymarket slug into components.

    Format: "{asset}-updown-{window}-{epoch}"
    Example: "btc-updown-5m-1774147200"

    Returns None if slug doesn't match expected format.
    """
    if not slug:
        return None
    m = re.match(r"(btc|eth|sol|xrp)-updown-(\d+m)-(\d+)", slug, re.IGNORECASE)
    if not m:
        return None
    asset = m.group(1).upper()
    window = m.group(2)
    window_secs = 300 if window == "5m" else 900 if window == "15m" else 0
    try:
        epoch = int(m.group(3))
    except ValueError:
        return None
    return SlugParts(asset=asset, window=window, window_secs=window_secs,
                     epoch=epoch, raw=slug)


def normalize_direction(direction: str) -> str:
    """Normalize direction to lowercase: 'Up' -> 'up', 'Down' -> 'down'.

    Prevents the case-sensitivity bug that caused 37% label inversion (Mar 22 2026).
    Always use this before comparing directions across tables.
    """
    if not direction:
        return ""
    return direction.strip().lower()


def directions_match(dir_a: str, dir_b: str) -> bool:
    """Case-insensitive direction comparison."""
    return normalize_direction(dir_a) == normalize_direction(dir_b)


def get_enriched_trades(db_path: str, where: str = "", params: tuple = ()) -> list:
    """Get trades with parsed slug fields. Replaces ad-hoc slug parsing in queries.

    Returns list of dicts with original columns plus:
        parsed_asset, parsed_window, meta_asset, meta_direction, meta_source
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    query = "SELECT * FROM trades WHERE exit_time IS NOT NULL"
    if where:
        query += f" AND ({where})"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    result = []
    for r in rows:
        d = dict(r)
        slug_parts = parse_slug(d.get("slug", ""))
        d["parsed_asset"] = slug_parts.asset if slug_parts else None
        d["parsed_window"] = slug_parts.window if slug_parts else None
        d["parsed_window_secs"] = slug_parts.window_secs if slug_parts else None
        d["parsed_epoch"] = slug_parts.epoch if slug_parts else None
        meta = json.loads(d["metadata"]) if d.get("metadata") else {}
        d["meta_asset"] = meta.get("asset")
        d["meta_direction"] = meta.get("direction")
        d["meta_source"] = meta.get("source")
        d["asset"] = d["meta_asset"] or d["parsed_asset"] or "UNKNOWN"
        d["direction"] = normalize_direction(d["meta_direction"] or "")
        d["source"] = d["meta_source"] or ""
        result.append(d)
    return result


def get_labeled_signals(db_path: str, where: str = "", params: tuple = ()) -> list:
    """Get signals with correct labels. Normalizes direction for safe comparison.

    Returns list of dicts with all signal columns plus normalized_direction.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    query = "SELECT * FROM signals WHERE is_win IS NOT NULL"
    if where:
        query += f" AND ({where})"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    result = []
    for r in rows:
        d = dict(r)
        d["normalized_direction"] = normalize_direction(d.get("direction", ""))
        slug_parts = parse_slug(d.get("slug", ""))
        d["parsed_asset"] = slug_parts.asset if slug_parts else None
        d["parsed_epoch"] = slug_parts.epoch if slug_parts else None
        result.append(d)
    return result


@dataclass
class HealthReport:
    db_name: str
    exists: bool
    integrity_ok: bool
    row_count: int
    latest_timestamp: str
    freshness_secs: float
    is_fresh: bool  # < 600s old
    issues: list


def check_db_health(db_path: str, table: str, ts_column: str = "timestamp",
                    max_age_secs: float = 600) -> HealthReport:
    """Check health of a single database table.

    Returns a HealthReport with existence, integrity, freshness, and issues.
    """
    path = Path(db_path)
    issues = []

    if not path.exists():
        return HealthReport(
            db_name=path.name, exists=False, integrity_ok=False,
            row_count=0, latest_timestamp="", freshness_secs=float("inf"),
            is_fresh=False, issues=["DB file not found"]
        )

    try:
        conn = sqlite3.connect(str(path))
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        integrity_ok = integrity == "ok"
        if not integrity_ok:
            issues.append(f"Integrity: {integrity[:100]}")

        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        latest = conn.execute(
            f"SELECT MAX({ts_column}) FROM {table}"
        ).fetchone()[0] or ""

        # Compute freshness
        freshness = float("inf")
        if latest:
            try:
                # Try ISO format
                from datetime import datetime, timezone
                if "T" in str(latest):
                    dt = datetime.fromisoformat(str(latest).replace("Z", "+00:00"))
                    freshness = (datetime.now(timezone.utc) - dt).total_seconds()
                else:
                    # Try unix timestamp
                    freshness = time.time() - float(latest)
            except (ValueError, TypeError):
                issues.append(f"Cannot parse timestamp: {latest[:50]}")

        is_fresh = freshness < max_age_secs
        if not is_fresh and count > 0:
            issues.append(f"Stale: last entry {freshness:.0f}s ago (max {max_age_secs}s)")

        conn.close()
        return HealthReport(
            db_name=path.name, exists=True, integrity_ok=integrity_ok,
            row_count=count, latest_timestamp=str(latest)[:30],
            freshness_secs=freshness, is_fresh=is_fresh, issues=issues
        )
    except Exception as e:
        return HealthReport(
            db_name=path.name, exists=True, integrity_ok=False,
            row_count=0, latest_timestamp="", freshness_secs=float("inf"),
            is_fresh=False, issues=[f"Error: {e}"]
        )


def health_check(data_dir: str) -> dict:
    """Run health checks on all Polyphemus databases.

    Args:
        data_dir: Path to instance data directory
            (e.g., /opt/lagbot/instances/emmanuel/data)

    Returns:
        Dict with per-DB reports and overall status.
    """
    d = Path(data_dir)
    checks = [
        ("performance.db", "trades", "entry_time", 600),
        ("signals.db", "signals", "timestamp", 600),
        ("signals.db:snapshots", "position_snapshots", "timestamp", 120),
        ("signals.db:epochs", "epoch_coverage", "timestamp", 600),
    ]

    reports = {}
    all_ok = True
    for name, table, ts_col, max_age in checks:
        db_file = name.split(":")[0]
        report = check_db_health(str(d / db_file), table, ts_col, max_age)
        report.db_name = name
        reports[name] = report
        if report.issues:
            all_ok = False

    return {
        "all_ok": all_ok,
        "reports": reports,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def spot_check_labels(signals_db: str, n: int = 10) -> dict:
    """Spot-check signal outcome labels against epoch_coverage.

    Returns dict with correct/incorrect/unverifiable counts and sample mismatches.
    """
    conn = sqlite3.connect(signals_db)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT s.id, s.slug, s.direction, s.is_win, e.oracle_direction
        FROM signals s
        JOIN epoch_coverage e
          ON CAST(SUBSTR(s.slug, INSTR(s.slug, '-5m-') + 4) AS INTEGER) = e.epoch
          AND UPPER(SUBSTR(s.slug, 1, INSTR(s.slug, '-') - 1)) = UPPER(e.asset)
        WHERE s.is_win IS NOT NULL
          AND e.oracle_direction IS NOT NULL
          AND (s.outcome IS NULL OR s.outcome != 'executed')
        ORDER BY RANDOM()
        LIMIT ?
    """, (n,)).fetchall()
    conn.close()

    correct = 0
    incorrect = 0
    mismatches = []
    for r in rows:
        expected = 1 if directions_match(r["direction"], r["oracle_direction"]) else 0
        if r["is_win"] == expected:
            correct += 1
        else:
            incorrect += 1
            mismatches.append({
                "id": r["id"], "slug": r["slug"],
                "direction": r["direction"],
                "oracle": r["oracle_direction"],
                "is_win": r["is_win"], "expected": expected
            })

    return {
        "checked": len(rows),
        "correct": correct,
        "incorrect": incorrect,
        "accuracy": correct / len(rows) if rows else 0,
        "mismatches": mismatches,
    }
