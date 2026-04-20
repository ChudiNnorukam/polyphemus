---
id: phantom-exit-zombies
name: Phantom Exit Zombies
domain: observability
aliases:
- phantom_orphaned
- phantom_orphaned_backfill
- phantom_reaper
- zombie trade
- zombie rows
- exit_time NULL
- max_positions saturation
- signal silence
- Loaded N positions from DB
- Purging stale LOSS
- _phantom_reaper_loop
- _phantom_reaper_once
- _parse_market_end_epoch
code_refs:
- path: signal_bot.py
  lines: 95-127
  sha256: 85ee979fa134cf1f
- path: signal_bot.py
  lines: 730-786
  sha256: f643d616d76fd73d
- path: signal_bot.py
  lines: 1941-1970
  sha256: 36eaeafa17edafab
- path: signal_bot.py
  lines: 1971-2073
  sha256: 28c68045257176e9
related:
- fill-models
- trade-tracer
- shadow-mode
- deploy-lifecycle
parent_concepts: []
child_concepts: []
last_verified: '2026-04-20T05:02:09Z'
confidence: inferred
---

## What

A phantom exit zombie is a DRY_RUN trade whose `exit_time` never got
written even though its market has long resolved. The row sits in
`trades` with `exit_time IS NULL` indefinitely, and because
`SignalGuard.max_positions` counts open rows, unbounded zombies
saturate the cap and reject every incoming signal. The pipeline goes
silent even though signals are firing — the 2026-04-19 emmanuel
incident: 24 signals passed guard, 144 were rejected for
`max_positions`, zero trades placed over a 16.9h window.

Root cause: the DRY_RUN exit handler does not mirror the live
`position_executor.py` path; when a phantom position resolves there is
no mechanism that always writes `exit_time` on its `trades` row.
Restart orphans (Position lost from `self._store` across a bot
restart) and mid-session leaks (exit path skipped for a bug or race)
both land in the same zombie pile.

Two independent mechanisms keep the cap cleared:

1. **Startup purge** ([signal_bot.py:730-786](signal_bot.py#L730-L786))
   runs once on `start()` after `load_from_db`. Walks
   `self._store.get_open()`; for each position whose slug parses as
   `<asset>-updown-<Nm>-<epoch>` and ended >10min ago, calls
   `get_share_balance(token_id)` to distinguish WIN (≥5 shares held
   — queue for redemption) from LOSS (0 shares — close at $0.0).
   Writes `force_close_trade(reason='market_resolved', exit_price=X)`.
   One-shot; only fires when the bot restarts.

2. **Phantom reaper** ([signal_bot.py:1941-1970](signal_bot.py#L1941-L1970)
   loop and [signal_bot.py:1971-2073](signal_bot.py#L1971-L2073) core)
   runs continuously on a 60s cadence. DRY_RUN-only. Walks
   `get_open_trades()` directly (no `self._store` dependency), skips
   accumulator / weather / pair_arb owners via metadata, skips rows
   whose slug can't be epoch-parsed, and closes past-end (>30s)
   phantom rows with a **neutral** `exit_price=entry_price` →
   `pnl=0`, `exit_reason='phantom_orphaned'`. Correctness (win vs
   loss) is intentionally out of scope, the only job is unblocking
   the position counter. MTC gate queries filter this distinct
   reason out of any verdict window. Each tick emits
   `[PHANTOM_REAPER] tick rows=N reaped=R skipped_unparseable=U
   skipped_not_past_end=S` unconditionally so a silent reaper is
   never observationally indistinguishable from a working one (P1
   observability at write time).

The reaper catches the mid-session case that the startup purge
cannot: zombies produced during a long-running session, before the
next restart. Startup purge catches the restart-orphan case the
reaper cannot: positions never loaded into `self._store` have no
in-memory handle for the 60s reaper walk, but `get_open_trades()` in
the reaper DOES read them from DB directly — so the reaper actually
catches both cases, and startup purge is the belt to the reaper's
suspenders (covering the first 60s before the reaper's first tick).

## Where

- Slug epoch parser at
  [signal_bot.py:95-127](signal_bot.py#L95-L127) —
  `_parse_market_end_epoch(slug)` extracts the unix epoch from
  `<asset>-updown-<Nm>-<epoch>` slugs, strips pair_arb `:up`/`:down`
  suffix, rejects out-of-bounds epochs (before 2020, after 2040).
  Returns `None` for weather / accumulator / malformed shapes.
- Startup purge at
  [signal_bot.py:730-786](signal_bot.py#L730-L786) — loaded-from-DB
  positions with an ended market get closed via
  `force_close_trade(reason='market_resolved', exit_price=1.0|0.0)`.
  Share-balance call distinguishes WIN from LOSS.
- Reaper scheduling at
  [signal_bot.py:1941-1970](signal_bot.py#L1941-L1970), 60s sleep
  loop, wraps `_phantom_reaper_once()` in a try/except so a bug in
  one iteration does not break the cadence.
- Reaper core at
  [signal_bot.py:1971-2073](signal_bot.py#L1971-L2073), the
  testable pass: filter, epoch parse, past-end check,
  `force_close_trade('phantom_orphaned', entry_price)`. Returns
  `{reaped, skipped_unparseable, skipped_not_past_end, rows_scanned}`
  so tests and the tick log can pin behavior. The tick log emits
  unconditionally at the end of each pass.
- Reaper registered into task graph (gated on `self._dry_run`)
  alongside the scorer-retrain loop. The task list is built inside
  `start()`, and the registration lives near the other `_safe_task`
  wires — grep `phantom_reaper` to find the exact line.
- One-shot backfill tool at
  `tools/backfill_phantom_zombies.py` — SQL mirror of the reaper's
  Python filter, distinct `exit_reason='phantom_orphaned_backfill'`
  so the one-shot fix is distinguishable from ongoing reaps. Use
  only when the reaper is NOT yet deployed; once the reaper is
  live, the backfill is redundant.

## When-to-touch

Route here when the user says:

- "why did the bot go silent for N hours"
- "no signals are firing" / "pipeline watchdog fired"
- "why is max_positions saturated"
- "too many open trades" / "open trades won't clear"
- "what is phantom_orphaned" / "what is phantom_orphaned_backfill"
- "reaper" / "phantom reaper" / "phantom exit"
- "`exit_time IS NULL`" / "zombie rows" / "zombie trades"
- "DRY_RUN exit handler doesn't write exit_time"
- "Loaded N positions from DB" followed by "Purging stale"
- "should we backfill the NULL-exit_time rows"
- any mid-session deadlock where signals pass guard but no trades
  land, and the symptom list includes growing `exit_time IS NULL`
