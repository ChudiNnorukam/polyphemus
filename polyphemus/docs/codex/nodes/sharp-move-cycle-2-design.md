---
id: sharp-move-cycle-2-design
name: Sharp Move Cycle-2 Design (post-tiny-live experiment)
domain: methodology
aliases:
- cycle-2
- sharp_move cycle 2
- next experiment
- tiny-live followup
- cycle-2 instrumentation
- post-n=30 design
code_refs:
- path: docs/codex/nodes/sharp-move-tiny-live-experiment.md
  lines: 1-118
  sha256: dff7416508e5c23b
- path: docs/codex/nodes/test-to-live-haircut.md
  lines: 33-58
  sha256: 0a8260d7b40cad51
- path: docs/codex/nodes/falsifiable-prediction-discipline.md
  lines: 30-54
  sha256: ad43d9ad8a4da84f
related:
- sharp-move-tiny-live-experiment
- sharp-move-alpha-decay-backtest
- test-to-live-haircut
- falsifiable-prediction-discipline
- codebase-reliability-coverage
- adverse-selection
- regime-detector
- mtc-gate
parent_concepts: []
child_concepts: []
last_verified: '2026-04-28T20:58:00Z'
confidence: inferred
ratified: null
---

## What

This node holds the **pre-committed design for cycle-2** of the
sharp_move tiny-live experiment, drafted while cycle-1 is still
running. The design captures:

1. **Decision tree based on cycle-1 verdict** — what cycle-2 looks like
   (or whether it happens at all) under each of KILL / EXTEND / PROMOTE.
2. **Seven candidate hypotheses for cycle-2** to test, each with a
   cycle-1-data-grounded pre-commit threshold.
3. **Instrumentation requirements** that can ONLY be added between
   cycles per `falsifiable-prediction-discipline` (don't change
   instrumentation mid-experiment).

The design exists BEFORE cycle-1 closes specifically so cycle-2 is not
improvised post-hoc. Every cycle-2 hypothesis here is conditional on a
cycle-1 calibration finding, ratified now, before the data lands.

## Decision tree based on cycle-1 verdict

Cycle-1 closes at n=30 OR 14 days from 2026-04-26 15:49 UTC, whichever
first. Possible verdicts:

### KILL
Triggered by: Wilson LB(WR) < 0.50 OR mean adverse_fill_bps ≥ 30 OR
cumulative loss > $30 OR Markov gate fired permanently.

**Action:** No cycle-2. The strategy's edge does not survive live fills.
Set `ENABLE_SHARP_MOVE=false` on emmanuel. Document the kill rationale
in `sharp-move-tiny-live-experiment.md` Status section. The codex
retains the design for reference but no further capital commitment.

### EXTEND
Triggered by: cycle-1 cleared survive bar (Wilson LB ≥ 0.50, adverse
< 30, no infra failures, n ≥ 30, P9 ≥ 2 disjoint windows) but did NOT
clear PROMOTE bar (WR < 0.55 OR adverse ≥ 15 OR execution_rate < 0.50).

**Action:** Cycle-2 is **another 14-day, $4-cap tiny-live**, with one
pre-committed instrumentation/strategy refinement chosen from the
candidates below. Pick exactly ONE; layering multiple changes
destroys the falsifiability of the cycle. The pick is decided by which
calibration finding from cycle-1 most needs measurement.

### PROMOTE
Triggered by: cycle-1 cleared all 4 PROMOTE criteria (WR ≥ 0.55,
adverse < 15, execution_rate ≥ 0.50, total_pnl positive) at n ≥ 30
with P9 ≥ 2 disjoint windows.

**Action:** Cycle-2 is a **scaled tiny-live: MAX_TRADE_AMOUNT=10,
MAX_DAILY_LOSS=50**, retains all gates, runs another 30 fills as
ratification. THIS IS NOT FULL PRODUCTION. Promotion to medium-live
requires a successful cycle-2 at the higher size. The methodology
forbids "single experiment proves promote-able" framing.

### EARLY KILL (during cycle-1)
Cumulative loss ≥ $30, OR Markov gate fires twice in 7 days.

**Action:** Same as KILL. Cycle-2 cancelled. Refund the experiment
budget tracking and treat this strategy as Phase-1 NO-GO until
fundamental redesign.

## Seven candidate cycle-2 hypotheses

Each is a SEPARATE experiment, not a layered set. Pick one per cycle.

### H1 — Fear-regime entry filter
**Trigger:** cycle-1 calibration log shows fear-regime WR ≥ 0.80 AND
n_fear ≥ 10 AND fear-vs-neutral WR gap ≥ 30pp at cycle-1 close.

**Cycle-2 design:** Add `ENTRY_REGIME_FILTER=fear_only` (FG 26-45)
to emmanuel .env. Recompute the 4 pre-committed criteria on cycle-2
fills.

**Grounding:** 2026-04-28 calibration row showed fear (n=4, WR=1.00,
+$2.52) vs neutral (n=3, WR=0.33, −$3.88) at n=7. If this holds at
n=30, regime filtering captures the alpha and discards the tail.

**Risk:** Fear-regime fills are rarer; may not hit n=30 in 14 days.

### H2 — Entry-price band split
**Trigger:** cycle-1 shows that p<0.65 trades have variance ≥ 2× the
p≥0.85 trades' variance, AND mean P/L per band differs by < $0.50.

**Cycle-2 design:** SPLIT sharp_move into two strategies:
`sharp_move_high` (p ≥ 0.85, MAX_TRADE_AMOUNT=4) and
`sharp_move_low` (p < 0.85, MAX_TRADE_AMOUNT=2). Each runs as its
own experiment with separate kill/promote bars.

**Grounding:** 2026-04-28 cycle-2 simulator forced run at n=7 showed
that entry_floor_0.85 removes BOTH the −$3.92 outlier AND the +$2.16
winner. Mean unchanged, variance cut. The split would let the
methodology assess each regime independently.

**Risk:** Doubles the experimental complexity; n=30 per side is hard
in 14 days.

### H3 — Book-depth instrumentation
**Trigger:** cycle-1 close shows execution_rate < 0.50 (the FOK
timeout hypothesis is unrefuted).

**Cycle-2 instrumentation:** In `position_executor.py` at the order
placement site, capture `book_depth_bid` + `book_depth_ask` alongside
the existing `book_spread_at_entry`. ~5 lines, single function.

**Cycle-2 question:** Does FOK timeout correlate with thin book? If
yes, the strategy needs a min-depth gate; if no, the timeout is
latency-bound, not depth-bound.

**Risk:** Touches live trading path. Requires careful predeploy.

### H4 — Mid-drift sampler
**Trigger:** cycle-1 mean adverse_fill_bps is in [10, 25] range AND
distribution is skewed (positive tail dominant). The negative
adverse_fill_bps reading at n=7 (-17.48) needs verification.

**Cycle-2 instrumentation:** New module `mid_drift_tracker.py` that
polls Polymarket mid price at t+90s, t+180s, t+300s after entry and
writes to `trades.mid_t90` / `mid_t180` / `mid_t300`.

**Cycle-2 question:** What is the actual mid drift distribution?
Backtest assumed instant resolution; live shows nuance. Mid-drift
distribution informs hold-time strategy.

**Risk:** Background tracker introduces a new failure mode (polling
loop, network jitter).

### H5 — MAE/MFE continuous sampling
**Trigger:** cycle-1 has at least 5 trades that exited via `time_exit`
rather than `market_resolved`, suggesting some positions had favorable
mid-trade excursion that wasn't captured.

**Cycle-2 instrumentation:** Same tracker as H4 but sampling at 10s
intervals during hold; computes MAE (max adverse) and MFE (max
favorable) per trade. Writes to `trades.mae` / `trades.mfe`.

**Cycle-2 question:** Could a take-profit trigger at MFE > 0.05
improve EV? Could a stop-loss at MAE > X reduce losses without
killing winners?

**Risk:** Continuous sampling = more network traffic + complexity.

### H6 — Fill-latency verification
**Trigger:** cycle-1 close shows `fill_latency_ms` is ALWAYS 0.0
(suggests field is unused, not measured).

**Cycle-2 instrumentation:** Code read of `position_executor.py` to
confirm if `fill_latency_ms` is actually being written. If yes,
investigate what 0.0 means (instant FOK fill?). If no, instrument
properly.

**Cycle-2 question:** Is FOK fill truly instant, or are we losing
fill-latency signal that would inform the FOK-vs-limit-order
decision?

**Risk:** Likely cheap (mostly investigation, not new code).

### H7 — Bot startup-failure handling (resilience)
**Trigger:** cycle-1 had ≥1 incident where the bot entered a restart
storm (more than 5 restarts within 5 minutes due to preflight failure).

**Grounding:** 2026-04-28 16:08 UTC daily-restart loaded fresh modules
and the CLOB get_balance() call returned 0. systemd's
`Restart=always` policy retried every 13s, hammering Polymarket's
auth endpoint with `create_or_derive_api_creds()` calls on every
attempt. ~150 retries in ~30 min plausibly triggered Polymarket's
rate-limit, which compounded the original failure into a sustained
crash loop. The bot was masked manually to break the storm. On-chain
balance ($94.89) was confirmed intact; the wallet was never drained.

**Cycle-2 instrumentation/policy change:** Modify
`/etc/systemd/system/lagbot@.service` (template) to use:
- `Restart=on-failure` (instead of `always`) — does not retry on
  clean exits, only after non-zero exit
- `StartLimitBurst=3` and `StartLimitIntervalSec=300` — at most 3
  restarts in 5 minutes; after that, systemd marks the unit as failed
  and stops retrying
- `RestartSec=30s` (instead of 10s) — reduces hammer rate
- Optional: `ExecStartPre=` that does its own retry-with-backoff for
  CLOB auth, separate from systemd's restart loop

**Cycle-2 question:** Does removing the retry-storm prevent
cascade-style auth lockouts? And does max-retries-then-fail surface
the original failure to the operator faster than masking?

**Risk:** Touches systemd at the unit-template level — affects all
lagbot@ instances (emmanuel, polyphemus, chudi). Must be tested in
shadow on polyphemus before applying to emmanuel. Reversible via
`mv lagbot@.service.bak lagbot@.service` + `systemctl daemon-reload`.

## Pre-commitment table (cycle-1 → cycle-2 mapping)

| Cycle-1 finding | Triggers cycle-2 hypothesis |
|---|---|
| fear-regime WR ≥ 0.80 with n_fear ≥ 10 | H1 fear filter |
| p<0.65 variance ≥ 2× p≥0.85 variance | H2 price band split |
| execution_rate < 0.50 with n_attempts ≥ 30 | H3 book-depth instrumentation |
| adverse_fill_bps in [10, 25] | H4 mid-drift sampler |
| ≥ 5 time_exit trades | H5 MAE/MFE sampling |
| fill_latency_ms always 0.0 | H6 fill-latency verification |
| ≥1 restart storm (>5 restarts in 5 min) during cycle-1 | H7 startup-failure resilience |

If MULTIPLE triggers fire at cycle-1 close, pick the one whose
cycle-2 hypothesis has the highest **leverage × cost ratio**. The
eval agent surfaces this table; the operator picks.

## Anti-patterns

- **Layering multiple cycle-2 hypotheses.** Each cycle tests ONE
  refinement. Layering destroys falsifiability.
- **Silent re-instrumentation.** Cycle-2 instrumentation changes MUST
  be ratified BEFORE cycle-2 begins, not added during. The mid-experiment
  rule still binds; cycle-2 is a new experiment, not a continuation.
- **Re-running cycle-1 design.** If cycle-1 closes EXTEND, cycle-2 is
  cycle-1 + ONE refinement, not a fresh cycle-1. The same kill/promote
  bars apply.
- **Skipping cycle-2 on PROMOTE.** Even if cycle-1 promotes, cycle-2 at
  $10 stake is mandatory ratification. Single-experiment "proves
  promote-able" is the failure mode the methodology refuses.
- **Promoting straight to medium-live without cycle-2 success.** No.
  Cycle-2 PROMOTE → cycle-3 at MAX_TRADE_AMOUNT=25, then medium-live.
  Each step is its own experiment with its own kill/promote bars.

## Tracking mechanism

This node is referenced by `trig_01Bi82E3s1uhuQTnwPpaJAjR` (the 14-day
evaluation agent). When that agent fires (2026-05-10 16:00 UTC) and
surfaces a verdict, the prompt now instructs it to read THIS node and
include the relevant cycle-2 hypothesis in the verdict email.

If the operator skips cycle-2 design and acts on cycle-1 verdict
without referencing this node, the methodology has been bypassed —
flag as a process failure in the next session ledger.

## Status

**[Target]** — drafted 2026-04-28. Describes desired-state cycle-2
design, not current code. Promotes to [Inferred] at cycle-1 close
when one hypothesis is selected and the cycle-2 experiment is
authored. Promotes to [Verified] only when cycle-2 itself closes
with its own verdict.

This node should NEVER be ratified to [Verified] in its current
generic form — its value is the conditional structure that maps
cycle-1 findings to cycle-2 design choices.
