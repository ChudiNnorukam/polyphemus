"""Backfill historical dry-run market_resolved rows with outcome-aware exit prices.

Prior to the startup-purge fix (commit 4d2edaa), every dry-run position that
hit `_recover_open_trades` was force-closed with exit_price=0.0 regardless of
the actual market outcome — 100% loss, 100% of the time. This tool reruns
the same Binance-kline resolution the phantom_reaper uses (and the fix now
uses at purge time) against every such historical row, rewriting exit_price
and pnl to match the real outcome.

Affected rows identifier:
    exit_reason='market_resolved'
    AND exit_tx_hash='force_closed'
    AND exit_price=0.0
    AND is_dry_run=1
    AND (slug LIKE '%-updown-5m-%' OR slug LIKE '%-updown-15m-%')

Per-row logic mirrors SignalBot._resolve_dry_run_exit_price:
    - outcome (Up/Down) + Binance kline direction:
        match → exit_price=1.0, pnl=(1-entry)*size,  exit_reason='market_resolved_backfill_win'
        mismatch → exit_price=0.0, pnl=-(entry*size), exit_reason='market_resolved_backfill_loss'
        None/flat/error → exit_price=entry_price, pnl=0, exit_reason='market_resolved_backfill_unresolved'

The new exit_reason prefix preserves observability. Analysis queries that
want only "clean" resolutions can filter `exit_reason LIKE 'market_resolved_backfill_%'`.
Rows that don't match the affected identifier are never touched.

Usage:
    # Dry-run (report only, no writes):
    /opt/lagbot/venv/bin/python3 /tmp/backfill_dry_run_resolutions.py \\
        --db /opt/lagbot/instances/emmanuel/data/performance.db --dry-run

    # Write (requires DB backup; the tool creates one automatically):
    /opt/lagbot/venv/bin/python3 /tmp/backfill_dry_run_resolutions.py \\
        --db /opt/lagbot/instances/emmanuel/data/performance.db --execute

Read-only against live service processes: the tool uses its own sqlite3
connection and does not touch the service.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Import resolver — package is named `polyphemus` locally but `lagbot`
# on the VPS. Try both. phantom_resolver itself has no relative imports
# so either path resolves cleanly as long as aiohttp is available.
sys.path.insert(0, "/opt/lagbot")
sys.path.insert(0, "/opt/lagbot/lagbot")
try:
    from polyphemus.phantom_resolver import (  # type: ignore  # noqa: E402
        resolve_direction,
        parse_asset_from_slug,
        parse_window_secs_from_slug,
    )
except ImportError:
    try:
        from lagbot.phantom_resolver import (  # type: ignore  # noqa: E402
            resolve_direction,
            parse_asset_from_slug,
            parse_window_secs_from_slug,
        )
    except ImportError:
        # Last resort: import the module file directly (no package parent).
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "phantom_resolver_standalone",
            "/opt/lagbot/lagbot/phantom_resolver.py",
        )
        _mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_mod)  # type: ignore
        resolve_direction = _mod.resolve_direction
        parse_asset_from_slug = _mod.parse_asset_from_slug
        parse_window_secs_from_slug = _mod.parse_window_secs_from_slug


QUERY_AFFECTED = """
SELECT trade_id, slug, outcome, entry_price, entry_size
FROM trades
WHERE exit_reason = 'market_resolved'
  AND exit_tx_hash = 'force_closed'
  AND exit_price = 0.0
  AND is_dry_run = 1
  AND (slug LIKE '%-updown-5m-%' OR slug LIKE '%-updown-15m-%')
ORDER BY entry_time ASC
"""

UPDATE_SQL = """
UPDATE trades
SET exit_price = ?,
    exit_reason = ?,
    pnl = ?,
    pnl_pct = ?
WHERE trade_id = ?
"""


def _parse_market_end(slug: str) -> int | None:
    try:
        parts = slug.rsplit("-", 1)
        if len(parts) != 2 or not parts[1].isdigit():
            return None
        epoch_start = int(parts[1])
        window = 300 if "-5m-" in slug else 900 if "-15m-" in slug else 0
        if window == 0:
            return None
        return epoch_start + window
    except Exception:
        return None


async def _resolve_row(row: dict, pause_secs: float) -> tuple[float, str, float, float]:
    """Return (exit_price, new_reason, pnl, pnl_pct) for one row.

    None / flat / error paths return a neutral close at entry_price.
    """
    entry_price = float(row["entry_price"] or 0.0)
    entry_size = float(row["entry_size"] or 0.0)
    trade_dir = (row["outcome"] or "").strip().lower()
    slug = row["slug"] or ""
    asset = parse_asset_from_slug(slug)
    window_secs = parse_window_secs_from_slug(slug)
    market_end = _parse_market_end(slug)

    if not (asset and window_secs and market_end and trade_dir in ("up", "down")):
        return (entry_price, "market_resolved_backfill_unresolved", 0.0, 0.0)

    try:
        binance_dir = await resolve_direction(
            asset,
            float(market_end - window_secs),
            window_secs=window_secs,
        )
    except Exception:
        binance_dir = None

    await asyncio.sleep(pause_secs)

    if binance_dir == trade_dir:
        exit_price = 1.0
        pnl = (1.0 - entry_price) * entry_size
        pnl_pct = (1.0 - entry_price) / entry_price if entry_price > 0 else 0.0
        return (exit_price, "market_resolved_backfill_win", pnl, pnl_pct)
    if binance_dir in ("up", "down"):
        exit_price = 0.0
        pnl = -(entry_price * entry_size)
        pnl_pct = -1.0 if entry_price > 0 else 0.0
        return (exit_price, "market_resolved_backfill_loss", pnl, pnl_pct)
    return (entry_price, "market_resolved_backfill_unresolved", 0.0, 0.0)


def _fetch_affected(db_path: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(QUERY_AFFECTED).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


async def _run(db_path: str, dry_run: bool, pause_secs: float) -> dict[str, Any]:
    rows = _fetch_affected(db_path)
    stats = {
        "scanned": len(rows),
        "win": 0,
        "loss": 0,
        "unresolved": 0,
        "written": 0,
        "pnl_delta": 0.0,
    }
    if not rows:
        return stats

    print(f"[backfill] {len(rows)} affected rows in {db_path}", flush=True)
    backup_path: Path | None = None
    if not dry_run:
        backup_path = Path(f"{db_path}.bak.{int(time.time())}")
        shutil.copy2(db_path, backup_path)
        print(f"[backfill] DB backup written: {backup_path}", flush=True)

    conn = sqlite3.connect(db_path) if not dry_run else None
    try:
        for i, row in enumerate(rows, start=1):
            exit_price, new_reason, pnl, pnl_pct = await _resolve_row(row, pause_secs)
            if new_reason.endswith("win"):
                stats["win"] += 1
            elif new_reason.endswith("loss"):
                stats["loss"] += 1
            else:
                stats["unresolved"] += 1
            old_pnl = -(float(row["entry_price"] or 0.0) * float(row["entry_size"] or 0.0))
            stats["pnl_delta"] += pnl - old_pnl
            if not dry_run and conn is not None:
                conn.execute(
                    UPDATE_SQL,
                    (exit_price, new_reason, pnl, pnl_pct, row["trade_id"]),
                )
                stats["written"] += 1
            if i % 25 == 0:
                msg = f"[backfill] {i}/{len(rows)} win={stats['win']} loss={stats['loss']} unresolved={stats['unresolved']} pnl_delta={stats['pnl_delta']:+.2f}"
                print(msg, flush=True)
        if conn is not None:
            conn.commit()
    finally:
        if conn is not None:
            conn.close()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="absolute path to performance.db")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--pause-secs", type=float, default=0.15,
                    help="sleep between Binance calls (default 0.15s ≈ 6 qps)")
    args = ap.parse_args()
    if args.dry_run == args.execute:
        print("ERROR: pass exactly one of --dry-run or --execute", file=sys.stderr)
        return 2
    if not Path(args.db).exists():
        print(f"ERROR: db not found: {args.db}", file=sys.stderr)
        return 2
    stats = asyncio.run(_run(args.db, dry_run=args.dry_run, pause_secs=args.pause_secs))
    print()
    print(f"[backfill] FINAL: scanned={stats['scanned']} win={stats['win']} "
          f"loss={stats['loss']} unresolved={stats['unresolved']} "
          f"written={stats['written']} pnl_delta=${stats['pnl_delta']:+.2f}")
    print(f"[backfill] mode: {'DRY-RUN (no writes)' if args.dry_run else 'EXECUTE'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
