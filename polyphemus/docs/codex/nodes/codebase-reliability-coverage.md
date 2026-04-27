---
id: codebase-reliability-coverage
name: Codebase Reliability Coverage
domain: methodology
aliases:
- invariant audit
- coverage audit
- reliability sweep
- codebase clean check
- premature victory pattern
- audit completeness
code_refs:
- path: docs/codex/nodes/falsifiable-prediction-discipline.md
  lines: 30-54
  sha256: ad43d9ad8a4da84f
- path: docs/codex/nodes/test-to-live-haircut.md
  lines: 33-58
  sha256: 0a8260d7b40cad51
related:
- falsifiable-prediction-discipline
- test-to-live-haircut
- mtc-gate
- deploy-lifecycle
- shadow-mode
parent_concepts: []
child_concepts: []
last_verified: '2026-04-27T21:05:46Z'
confidence: inferred
---

## What

The Codebase Reliability Coverage methodology is the discipline of
producing **bounded, calibrated audit claims about non-strategy code**
so the operator can confidently say *"the bug surface that remains is
strategy, not codebase"* instead of the recurring *"we keep finding
more shadow debt."*

The methodology rests on three commitments:

1. **No "clean" claims.** The audit emits `M/N covered, unknowns remain
   by definition.` Never `the codebase is clean.` Never `audit complete.`
   The Verified label attaches to specific verification acts (a
   sha256 match, a passing check) and never to aggregate codebase state.

2. **Enumerable invariant categories.** A finite list of named
   categories, each with a programmatic check that returns
   PASS/FAIL/UNKNOWN with explicit covered-files and out-of-scope-files
   sets. New bug classes from each session that exposed a category
   gap become new categories appended to the list.

3. **Layered coverage, not single-tool.** The methodology operates
   through three peer skills, each with a different blind-spot profile:
   - `/invariant-audit` (this methodology's spine — programmatic checks)
   - `/shadow-debt` (doc-authority pollution; orthogonal blind spot)
   - weekly adversarial sweep (different prompting strategy, surfaces
     blind spots that the human-designed checklist missed)
   Together they bound the unknown space across multiple axes without
   any single tool claiming convergence.

## Why this exists (provenance)

Drafted 2026-04-27 after a 36-hour session in which the assistant
made at least 5 distinct "audit complete" / "codebase clean" claims
that were each falsified by additional findings within hours
(source-rename siblings, ensemble subsystem inertness, ZERO TRADE
WATCHDOG miscalibration, daily-restart side effects). The operator
correctly identified the pattern and asked for a methodology that
gives bounded coverage rather than recurring premature-victory.

The empirical fact is that AI-authored multi-session codebases have
shadow debt as their steady state. No methodology eliminates it.
But methodologies CAN bound it, measure it, and provide signal that
incrementally tightens the unknown-unknown space. This node holds
the discipline so the lesson does not have to be relearned every
session.

## The 6 invariant categories (initial set, drafted from session findings)

| # | Category | Definition | Detection grounding |
|---|---|---|---|
| 1 | **cadence-aware-thresholds** | Every time-thresholded watchdog/timer/restart in business code is calibrated for the active strategy's fill cadence, OR is documented as cadence-independent | `health_monitor.py:355` 1h ZERO TRADE alert misfiring on sharp_move's 5h cadence |
| 2 | **source-name-propagation** | Every allowlist / SQL-IN-clause / dashboard column referring to a strategy source name covers ALL active sources, including any post-rename names | source-rename 2026-03-25 broke 6 files; took two audit waves to catch all of them |
| 3 | **lock↔runtime-sync** | Every entry in `requirements-lock.txt` matches the version installed in the running venv on every host. No silent drift between manifest and process | `python-dotenv` lock=1.0.0 vs VPS=1.2.1 drift discovered 2026-04-26 |
| 4 | **config-validator-clean** | Bot startup config validator emits zero out-of-range warnings, OR each warning has an acknowledged-and-justified ledger entry | `MOMENTUM_TRIGGER_PCT=1.0`, `MOMENTUM_WINDOW_SECS=10`, `MAX_OPEN_POSITIONS=20` warnings 2026-04-27 |
| 5 | **dead-flag-detection** | Every feature flag set to False has zero downstream code paths claiming the flag is True. Every feature flag set to True is exercised by at least one signal/path | `ENABLE_BTC5M_ENSEMBLE_SHADOW=false` while session-authored "ensemble fix" patches assumed it was True (inert patches) |
| 6 | **local↔VPS-sync** | Every business-code .py file (excluding venv, tests, archive, worktrees) has matching sha256 between local repo and VPS path | health_monitor.py drifted from local for the entire session until predeploy.sh reconciled it |

Each category has a corresponding check in `~/.claude/skills/invariant-audit/`
(spine of the methodology). Checks are runnable via `/invariant-audit` and
return a structured result of the shape:

```json
{
  "category": "cadence-aware-thresholds",
  "verdict": "PASS|FAIL|UNKNOWN",
  "covered_files": [...],
  "out_of_scope_files": [...],
  "findings": [...],
  "ts": "<iso>"
}
```

## Where

- The methodology spine: `~/.claude/skills/invariant-audit/SKILL.md`
- State file: `~/.claude/invariant-audit-state.json` (baseline + history)
- Peer skills: `/shadow-debt` (doc-authority pollution),
  weekly adversarial sweep (scheduled remote agent)
- Principle linkage: `docs/codex/PRINCIPLES.md` candidate principle
  *"Codebase audits emit coverage explicitly; 'clean' is never
  asserted, only 'M/N covered, unknowns remain.'"* — to be ratified
  after methodology has 30+ days of operational data.

## When-to-touch

Route here when the user says:

- "is the codebase clean?"
- "are these all the issues?"
- "what's left to fix that isn't strategy?"
- "shadow debt audit"
- "comprehensive audit"
- "I want to confidently say [non-strategy claim]"
- before any "promote tiny-live to medium-live" or "expand strategy
  capital allocation" decision (you must run /invariant-audit first
  and have a current baseline to assert "the bot's reliability
  isn't the bottleneck")
- before any major refactor or migration (capture pre-refactor
  baseline so post-refactor diff is meaningful)

Refuse to ratify a "the codebase is clean" framing. Always rephrase
as "X/6 invariant categories covered, Y files explicitly out-of-scope,
unknown-unknowns remain by definition." This is the discipline.

## Anti-patterns

- **Trusting checklist as cure.** The 6 categories are seeded from
  past bug classes. They cannot catch the next novel bug class.
  Adding categories AFTER each new bug class is the maturation
  path; pretending the current 6 are sufficient is the failure mode
  this entire node exists to prevent.
- **Claiming convergence.** "We're now at 6/6 PASS, codebase is clean"
  is the phrase to refuse. The right phrase is "6/6 PASS on the
  named categories; categories themselves are partial."
- **Skipping the adversarial sweep.** The whole point of layered
  coverage is that no single lens is enough. Skipping the sweep
  collapses D back into A and forfeits the protection.
- **Verifying without reading.** `/invariant-audit` outputs a JSON
  state file. The operator must read and ratify the baseline at
  least once for it to be load-bearing. Without ratification, the
  baseline is just `[Inferred]`.

## Status

**[Inferred]** — methodology drafted 2026-04-27 in response to
operator's calibration challenge. Promotes to [Verified] once
`/invariant-audit` is operational + has a ratified baseline +
the weekly adversarial sweep has surfaced ≥1 category gap that
got appended to this list (proving the maturation path works).
