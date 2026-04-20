---
id: accumulator-state
name: Accumulator State Machine
domain: accumulator-state
aliases:
- AccumulatorState
- SCANNING
- ACCUMULATING
- HEDGED
- SETTLING
- _transition
- paired accumulation
code_refs:
- path: accumulator.py
  lines: 1-8
  sha256: d0719d779d287523
- path: accumulator.py
  lines: 263-290
  sha256: f7c0eeb9530dba5a
- path: accumulator.py
  lines: 1512-1516
  sha256: 1db3285ab1e04143
related:
- circuit-breaker
- fill-models
- trade-tracer
parent_concepts: []
child_concepts: []
last_verified: '2026-04-20T05:02:09Z'
confidence: inferred
---

## What

The accumulator's state machine is the bot's "paired accumulation"
controller. Each active position walks one of four states: SCANNING
(discover an updown market whose combined pair cost is below $1.00),
ACCUMULATING (both maker orders resting, watching for fills), HEDGED
(both sides filled, position locked until window close), SETTLING
(cancel leftovers, write cycle record, record P&L). States are
per-position — N concurrent positions can be in different states at
the same tick.

The state machine is where the circuit breaker's "consecutive unwind"
counter gets incremented (only on real strategy unwinds, never on
infra failures — the Bug #46/#47 lesson). Every terminal path
(hedged settlement, sellback, forced-hold, orphaned) must emit a
cycle record or the Apr 10 2026 silent-drop bug class recurs.

## Where

- State machine docstring at
  [accumulator.py:1-8](accumulator.py#L1-L8) — canonical list of
  transitions: SCANNING → ACCUMULATING → HEDGED → SETTLING.
- Main dispatch loop at
  [accumulator.py:263-290](accumulator.py#L263-L290) — per-tick,
  each active position is routed through its current-state handler
  (`_evaluate_and_enter`, `_accumulate_both_sides`,
  `_monitor_hedged_position`, `_handle_settlement`).
- `_transition` at
  [accumulator.py:1512-1516](accumulator.py#L1512-L1516) — single
  mutation point; logs every state change with old → new + slug.

## When-to-touch

Route here when the user says:

- "accumulator stuck in X state" / "position not advancing"
- "hedge deadline" / "SCANNING heartbeat"
- "the paired position didn't close"
- "AccumulatorState enum" / "state transition log"
- "how does the accumulator discover markets"
- "why is there a cycle record with no pnl"
