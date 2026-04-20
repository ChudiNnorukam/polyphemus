---
id: adverse-selection
name: Adverse Selection
domain: adverse-selection
aliases:
- adverse fill
- adverse bps
- binance drift check
- phantom adverse-check
- binance_price_at_fill
- adverse_fill_bps
code_refs:
- path: signal_bot.py
  lines: 561-617
  sha256: f0125091fc0329c2
- path: performance_db.py
  lines: 888-941
  sha256: 92135856205d7411
- path: sql_views/vw_adverse_selection.sql
  lines: 24-41
  sha256: 3d7fc43b35484db7
related:
- fill-models
- entry-band
- mtc-gate
- trade-tracer
parent_concepts: []
child_concepts: []
last_verified: '2026-04-20T05:02:09Z'
confidence: verified
---

## What

Adverse selection is the bot's self-audit for "did we get picked off?"
After every fill (live or phantom dry-run), the bot samples the
reference Binance spot price at fill time and again `check_window_secs`
later. If the mark moved against the side we just bought, the trade is
flagged `adverse_fill=1` and the signed drift is recorded as
`adverse_fill_bps`. This is the single most important field for
answering "is our fill quality any good?" — without it, win rate is
indistinguishable from being on the wrong side of faster market-makers.

The check runs fire-and-forget via `asyncio.create_task` (see
async-pitfalls for the GC anti-pattern that nearly broke this once) and
never raises back into the trade path. NULL `binance_at_check` means
the post-fill poll timed out, not that the fill was good — distinguish
the two when reading a row.

## Where

- Phantom (DRY_RUN) adverse-check at
  [signal_bot.py:561-617](signal_bot.py#L561-L617) — mirror of the live
  path, scheduled at fill time after ORDER_FILLED emit. Without this,
  100% of phantom fills had `binance_price_at_fill=NULL`.
- Write path at
  [performance_db.py:888-941](performance_db.py#L888-L941)
  (`update_adverse_selection`) — computes signed bps, flags adverse,
  UPDATEs the row, and emits ADVERSE_CHECK_RUN for the trade tracer.
- Aggregation view at
  [sql_views/vw_adverse_selection.sql:24-41](sql_views/vw_adverse_selection.sql#L24-L41)
  — rollup per (signal_source, entry_mode, fill_model, is_dry_run).
  `is_dry_run` was added 2026-04-19 (bug_010) so phantoms and live
  don't contaminate each other.

## When-to-touch

Route here when the user says:

- "did we get picked off" / "was that trade adverse"
- "binance drift" / "binance_price_at_fill is NULL" / "why is adverse_bps empty"
- "fill quality" / "how bad is our adverse haircut"
- "phantom vs live fills are showing different adverse numbers"
- "the friend's 0.93-0.97 hypothesis" (adverse bps dominates edge at deep favorites)
