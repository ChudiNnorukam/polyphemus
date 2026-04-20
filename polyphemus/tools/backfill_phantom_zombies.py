"""One-shot backfill for phantom-exit zombie trades (Task #4).

Context
-------
Before the ``_phantom_reaper_loop`` shipped, DRY_RUN phantom trades whose
in-memory ``Position`` was lost across a bot restart never got an
``exit_time`` written. Those rows sit in ``trades`` with
``exit_time IS NULL`` forever and count against
``SignalGuard.max_positions`` — at 20 zombies on emmanuel the cap was
saturated and the signal pipeline went silent for 16.9h.

This script writes a neutral close on every such zombie using the SAME
filter logic as :py:meth:`polyphemus.signal_bot.SignalBot._phantom_reaper_once`:

* ``is_dry_run = 1``
* slug matches ``*-updown-*`` (the reaper's
  :py:func:`polyphemus.signal_bot._parse_market_end_epoch` only handles
  this shape)
* no ``is_accumulator`` / ``is_weather`` / ``source=pair_arb`` metadata
* ``exit_time IS NULL`` (idempotent; safe to re-run)

The write is:

* ``exit_time`` = now
* ``exit_price`` = ``entry_price`` -> ``pnl = 0``
* ``exit_reason`` = ``phantom_orphaned_backfill`` (distinct from the
  reaper's live ``phantom_orphaned`` so we can tell backfill rows from
  ongoing reaps)
* ``exit_tx_hash`` = ``force_closed``
* ``hold_seconds`` = ``now - entry_time``

Use distinct reason so MTC gate windowing can see/filter backfill rows
explicitly.

Usage
-----
::

    python3 tools/backfill_phantom_zombies.py --db <path>               # preview only
    python3 tools/backfill_phantom_zombies.py --db <path> --execute     # write

Deploy pattern (per CLAUDE.md — NO inline ``python3 -c``):

1. ``scp`` this file to the VPS.
2. Run with ``--db /opt/lagbot/instances/emmanuel/data/performance.db``
   (no ``--execute``) to preview the target rows.
3. Re-run with ``--execute`` to write.
4. Confirm ``SELECT COUNT(*) FROM trades WHERE exit_time IS NULL
   AND is_dry_run=1`` returns 0 (or close to 0, modulo rows opened
   after you started the backfill).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time


# Matches the filter in SignalBot._phantom_reaper_once (same logic,
# expressed in SQL instead of Python so the backfill is a single UPDATE).
# json_extract returns NULL when the key is absent, so the null-or-falsy
# checks below cover both "metadata has no key" and "metadata key is 0".
TARGET_FILTER_SQL = """
    exit_time IS NULL
    AND is_dry_run = 1
    AND slug LIKE '%-updown-%'
    AND COALESCE(json_extract(metadata, '$.is_accumulator'), 0) = 0
    AND COALESCE(json_extract(metadata, '$.is_weather'), 0) = 0
    AND COALESCE(json_extract(metadata, '$.source'), '') != 'pair_arb'
"""


def preview(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return the rows the backfill would modify, ordered by entry_time."""
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        f"""SELECT slug, strategy, entry_time, entry_price,
                   json_extract(metadata, '$.source') AS source,
                   trade_id
            FROM trades
            WHERE {TARGET_FILTER_SQL}
            ORDER BY entry_time ASC""",
    )
    return cur.fetchall()


def execute(conn: sqlite3.Connection) -> int:
    """Perform the neutral-close UPDATE. Returns rows affected."""
    now = time.time()
    cur = conn.cursor()
    cur.execute(
        f"""UPDATE trades SET
              exit_time = ?,
              exit_price = entry_price,
              exit_reason = 'phantom_orphaned_backfill',
              exit_tx_hash = 'force_closed',
              pnl = 0.0,
              pnl_pct = 0.0,
              hold_seconds = CAST(? - entry_time AS INTEGER)
            WHERE {TARGET_FILTER_SQL}""",
        (now, now),
    )
    conn.commit()
    return cur.rowcount


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--db",
        required=True,
        help="Absolute path to performance.db (e.g. "
             "/opt/lagbot/instances/emmanuel/data/performance.db).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually write the backfill. Without this flag, preview only.",
    )
    args = parser.parse_args(argv)

    conn = sqlite3.connect(args.db)
    try:
        rows = preview(conn)
        print(f"Targets: {len(rows)} phantom zombie row(s)")
        if not rows:
            print("No zombies matched filter. Nothing to backfill.")
            return 0
        print()
        print(f"{'slug':<35} {'strategy':<15} {'entry_time':<14} "
              f"{'entry_price':<8} {'source'}")
        for row in rows:
            print(
                f"{row['slug']:<35} {row['strategy']:<15} "
                f"{row['entry_time']:<14.2f} {row['entry_price']:<8.4f} "
                f"{row['source'] or ''}"
            )
        print()

        if not args.execute:
            print("Preview only (no --execute). Re-run with --execute to write.")
            return 0

        affected = execute(conn)
        print(f"Backfilled: exit_time/exit_price/pnl written on {affected} row(s)")
        print("exit_reason='phantom_orphaned_backfill' (distinct from live reaper)")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
