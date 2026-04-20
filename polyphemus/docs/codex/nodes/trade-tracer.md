---
id: trade-tracer
name: Trade Tracer
domain: observability
aliases:
- trade_events
- _trace_emit
- ADVERSE_CHECK_SCHEDULED
- ORDER_FILLED
- ADVERSE_CHECK_RUN
- timeline
- POLYPHEMUS_TRACER_JSONL
code_refs:
- path: trade_tracer.py
  lines: 78-126
  sha256: a6030b54e43d845a
- path: trade_tracer.py
  lines: 140-165
  sha256: f08955e14ebd1b69
related:
- adverse-selection
- fill-models
- deploy-lifecycle
parent_concepts: []
child_concepts: []
last_verified: '2026-04-20T05:02:09Z'
confidence: inferred
---

## What

The trade tracer is the bot's "one trade, one timeline" facility.
Every lifecycle event (signal_fired, order_placed, order_filled,
adverse_check_run, exit_decision, etc.) is written to a
`trade_events` table keyed by `trade_id`, with a JSON payload and a
timestamp. `TradeTracer.timeline(trade_id)` replays the events
oldest-first so a debugger can reconstruct exactly what the bot saw
and did, without grepping journalctl.

Two discipline rules make this safe: `emit` never raises (a DB
hiccup must not block the trade path), and `timeline` does raise (a
reader asking for a trace that errors should see the error, not an
empty result). Optional JSONL sidecar (`POLYPHEMUS_TRACER_JSONL`)
mirrors events to disk for grep-ability and future log pipelines.

## Where

- Writer class + `emit` at
  [trade_tracer.py:78-126](trade_tracer.py#L78-L126) — INSERT into
  `trade_events`, WAL mode, broad-except to guarantee no propagation
  back to the trade path.
- Reader `timeline` at
  [trade_tracer.py:140-165](trade_tracer.py#L140-L165) — ORDER BY ts
  ASC, event_id ASC; deserializes JSON payloads. Raises on DB error.

## When-to-touch

Route here when the user says:

- "trace this trade" / "timeline" / "trade_id X replay"
- "debug_trade CLI" / "what happened on that trade"
- "event_type X isn't firing"
- "why is trade_events empty"
- "ADVERSE_CHECK_SCHEDULED" / "ORDER_FILLED" / "ADVERSE_CHECK_RUN"
- "POLYPHEMUS_TRACER_JSONL" / "jsonl sidecar"
