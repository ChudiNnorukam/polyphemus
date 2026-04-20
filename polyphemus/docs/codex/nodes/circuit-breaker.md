---
id: circuit-breaker
name: Circuit Breaker
domain: sizing-gating
aliases:
- _circuit_tripped
- _consecutive_unwinds
- circuit_breaker.json
- max_daily_loss
- max_consecutive_unwinds
code_refs:
- path: accumulator.py
  lines: 110-119
  sha256: c649a21a8c1eddec
- path: accumulator.py
  lines: 121-157
  sha256: 238c251f551c3926
related:
- markov-gate
- outcome-gate
- accumulator-state
parent_concepts: []
child_concepts: []
last_verified: '2026-04-20T05:02:09Z'
confidence: inferred
---

## What

The circuit breaker is the bot's last-resort "just stop" switch. It
tracks cumulative accumulator PnL and consecutive unwinds, persists
the counters to `circuit_breaker.json` so a restart doesn't forget,
and trips when either cap breaks: daily PnL below `max_daily_loss`
or `consecutive_unwinds >= max_consecutive_unwinds`. Tripped = no new
trades until the user manually resets by overwriting the JSON with
zeros.

This is broader than the Markov and outcome gates — those live in
the signal filter (per-signal decisions). The circuit breaker lives
in the accumulator (per-cycle outcome tallying). Bug #46/#47 taught
us infra failures must NOT increment `_consecutive_unwinds`; only
real strategy unwinds count. Legacy state paths migrate into the
instance data dir automatically so multi-instance deploys don't fight
over a shared file.

## Where

- State init in `Accumulator.__init__` at
  [accumulator.py:110-119](accumulator.py#L110-L119) — six fields
  (`_consecutive_unwinds`, `_max_consecutive_unwinds`,
  `_daily_loss_limit`, `_circuit_tripped`, instance + legacy state
  paths, load-on-startup).
- Persistence at
  [accumulator.py:121-157](accumulator.py#L121-L157)
  (`_load_circuit_breaker_state`) — reads instance path first, falls
  back to legacy, trips on load if PnL below daily limit, migrates
  legacy state forward.

## When-to-touch

Route here when the user says:

- "circuit tripped" / "bot is not trading"
- "reset the circuit breaker" / "zero out circuit_breaker.json"
- "daily loss limit" / "max_daily_loss"
- "consecutive unwinds" / "max_consecutive_unwinds"
- "are infra failures counting against the breaker"
- "where is circuit_breaker.json"
