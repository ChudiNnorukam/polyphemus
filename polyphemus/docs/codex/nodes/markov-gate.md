---
id: markov-gate
name: Markov Gate
domain: sizing-gating
aliases:
- markov
- cold-regime gate
- loss-streak breaker
- MARKOV_GATE_MAX_LOSSES
- markov_cold_regime
code_refs:
- path: signal_guard.py
  lines: 53-58
  sha256: cf5c8bfaad5e99b6
- path: signal_guard.py
  lines: 478-503
  sha256: 601b17a7ee6b0cf3
related:
- adverse-selection
- entry-band
- mtc-gate
- outcome-gate
- circuit-breaker
- kelly-haircut
parent_concepts: []
child_concepts: []
last_verified: '2026-04-20T05:02:09Z'
confidence: verified
---

## What

The Markov gate is the bot's "cold-regime breaker." It tracks consecutive
win/loss streaks on the signal path and, after a loss streak crosses
`MARKOV_GATE_MAX_LOSSES`, blocks new entries on the theory that P(W|L) is
measurably lower than P(W|W) (observed ~28pp swing). Unlike the outcome
gate, which is a rolling window, Markov is purely sequential: one win
after a cold streak clears the block; otherwise an auto-unblock timer
probes the regime so the gate cannot deadlock.

When this gate fires, rejection reason `markov_cold_regime` shows up in
`signal_guard.FilterResult.reasons` and the filter exits FAIL. With
`markov_gate_dry_run=true` it logs `markov_blocked_dry_run` instead of
blocking, so you can see how often it would have fired without
suppressing trades.

## Where

- State initialized on `SignalGuard.__init__` at
  [signal_guard.py:53-58](signal_guard.py#L53-L58) — five fields
  (`consecutive_wins/losses`, `blocked`, `blocked_since`, `total_outcomes`).
- Filter logic in `SignalGuard.check` at
  [signal_guard.py:478-503](signal_guard.py#L478-L503) — gated behind
  `markov_gate_enabled and not is_weather`; includes the auto-unblock
  timer and the dry-run branch.
- Tuning lives in `.env` as `MARKOV_GATE_*` (see emmanuel's current:
  `MAX_LOSSES=3`, re-eval pending — plan Step 3).

## When-to-touch

Route to this node when the user says:

- "make the bot more cautious about cold streaks / losing streaks"
- "tighten the gate" (ambiguous — also consider outcome-gate)
- "the bot keeps blocking after startup" (cold-start cache from previous session)
- "MAX_LOSSES" / "markov" / "blocked" in logs
- "it's been blocked for an hour, why isn't it probing"
- any ask about the 3-3-1 audit (see `tools/backtest/MARKOV_GATE_AUDIT.md`)
