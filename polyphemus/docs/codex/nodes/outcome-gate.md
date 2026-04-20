---
id: outcome-gate
name: Outcome Gate
domain: sizing-gating
aliases:
- rolling WR gate
- outcome_gate_regime
- outcome_gate_blocked
- OUTCOME_GATE_ENABLED
- hysteresis gate
code_refs:
- path: signal_guard.py
  lines: 49-51
  sha256: 79c449b68a491268
- path: signal_guard.py
  lines: 447-475
  sha256: 99a85adc0384d231
related:
- markov-gate
- circuit-breaker
- mtc-gate
parent_concepts: []
child_concepts: []
last_verified: '2026-04-20T05:02:09Z'
confidence: inferred
---

## What

The outcome gate is the bot's "rolling regime detector." Unlike the
Markov gate (which counts consecutive wins/losses as a streak), the
outcome gate tracks a rolling window of the last N outcomes and blocks
entries when the rolling win rate drops below `min_wr`. It uses
hysteresis: blocks at `min_wr`, only resumes at a higher `resume_wr`
so it doesn't oscillate in and out every trade. A 30-minute auto-
unblock probe keeps the gate from deadlocking when blocking prevents
new outcomes.

Two gates, different jobs: Markov catches loss streaks early (1 recent
loss can block), outcome gate catches longer slow-drift regime shifts
(needs ~3+ outcomes to fire). Both live in `signal_guard.check` and
both can fire together — rejection reasons stack.

## Where

- State init on `SignalGuard.__init__` at
  [signal_guard.py:49-51](signal_guard.py#L49-L51) — rolling deque
  (`_recent_outcomes`) + blocked flag + blocked-since timestamp.
- Filter block `FILTER 8` at
  [signal_guard.py:447-475](signal_guard.py#L447-L475) — gated behind
  `outcome_gate_enabled and not is_weather`; computes rolling WR,
  runs the 30-minute auto-unblock probe, branches on dry_run mode
  vs. real rejection (`outcome_gate_regime`).

## When-to-touch

Route here when the user says:

- "rolling win rate" / "why is WR below X"
- "the bot is blocked on outcomes" / "outcome_gate_blocked"
- "tighten the win-rate gate" (ambiguous — also consider markov-gate)
- "hysteresis" / "resume_wr"
- "OUTCOME_GATE_* env var"
- "probe regime" / "auto-unblock"
