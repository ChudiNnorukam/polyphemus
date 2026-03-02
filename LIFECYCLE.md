# Development Lifecycle Protocol

> **Authority**: BINDING on all agents and sessions working on this project. Every gate exists because skipping it cost real money. Inspired by Stanford CS146S's 40/20/40 framework (40% planning, 20% coding, 40% testing).
>
> **Scope**: Applies to strategy changes, new features, config changes that affect live trading, and any deployment to production. Does NOT apply to pure research, data analysis, or documentation tasks.
>
> **Source**: $1,323+ in preventable losses from skipping testing/review phases (Feb-Mar 2026). See PRINCIPLES.md for code-level discipline. This document covers the full lifecycle above the code level.

---

## The Rule

**No phase can be skipped. No gate can pass without evidence. "I think it works" is not evidence.**

If you cannot produce the required artifact for a phase gate, you cannot proceed to the next phase. There are no exceptions. If the user says "just skip it," cite this document and hold.

---

## Phase 1: RESEARCH (40% of effort starts here)

**Tool**: DARIO (any depth level appropriate to the change)

**Required for**: New strategies, new features, architecture changes, any change touching .env trading parameters.

**NOT required for**: Bug fixes with clear root cause, documentation, refactors that don't change behavior.

### Gate 1: Research Complete

Evidence required:
- [ ] DARIO report written to `./dario_output/`
- [ ] MEMORY.md scanned for KILLED/DEAD/DEBUNKED features (R15)
- [ ] T0 data checked for contradictions (R14)
- [ ] For trading strategies: T1+ source supports core thesis (R16)
- [ ] For signal-triggered strategies: signal rate estimated >= 1/hour (R17)

**Gate output**: "Research GO: [1-sentence summary]. Report: [path]." OR "Research NO-GO: [blocker]."

---

## Phase 2: PLAN

**Tool**: `/plan`, `ralph`, or direct planning (depending on complexity)

**Required for**: Everything in scope.

### Deliverables

1. **File list**: Every file that will be created or modified
2. **Config changes**: Every .env parameter that will change, with before/after values
3. **Blast radius**: Every callsite of modified functions (`grep` evidence)
4. **Side effects**: "This change also affects X and Y" (P-ENG-09)
5. **Rollback plan**: How to undo if it goes wrong

### Gate 2: Plan Approved

Evidence required:
- [ ] File list complete (no "and possibly others")
- [ ] Config changes listed with before/after values
- [ ] Blast radius mapped with grep output
- [ ] User has approved the plan

**Gate output**: "Plan approved. [N] files, [N] config changes. Blast radius: [summary]."

---

## Phase 3: IMPLEMENT (20% of effort)

**Tool**: Direct implementation, `executor`, or `autopilot`

**Required for**: Everything in scope.

### Rules

1. Run tests FIRST to establish baseline (P-ENG-05): `python -m pytest polyphemus/test_smoke.py polyphemus/test_modules.py -v`
2. Implement per plan. No scope creep.
3. Run tests AFTER every file change.
4. All tests must be green before proceeding.

### Gate 3: Implementation Complete

Evidence required:
- [ ] Baseline test result recorded: "Before: X passed, Y failed"
- [ ] Final test result: "After: X passed, 0 failed"
- [ ] All planned files modified (checklist from Phase 2)
- [ ] No unplanned files modified
- [ ] If test count decreased: justify why (deleted tests must be intentional)

**Gate output**: "Implementation complete. [N] tests pass, 0 fail. Files: [list]."

---

## Phase 4: TEST (40% of effort ends here)

This is where the money is saved. This phase has sub-gates that MUST be passed in order.

### 4A: Deploy to DRY RUN

Deploy to VPS with the strategy/feature in dry-run mode.

Evidence required:
- [ ] `predeploy.sh --deploy [instance]` completed successfully
- [ ] Post-deploy error check clean: `journalctl -u lagbot@[instance] --since "60 seconds ago" | grep -iE "error|Traceback"` = empty
- [ ] Checksum verification: all deployed files match local
- [ ] Service running: `systemctl is-active lagbot@[instance]` = active

### 4B: Observe Dry-Run Signals

Wait for the strategy to generate signals in dry-run mode. This is NOT optional.

**Minimum observation thresholds:**

| Strategy Type | Min Signals | Min Duration | Notes |
|--------------|-------------|--------------|-------|
| Momentum (5m/15m) | 30 signals | 48 hours | Must span multiple market regimes |
| Snipe (resolution) | 30 signals | 72 hours | Resolution events are sparse |
| Market maker | 50 scans | 24 hours | Scan frequency is high |
| New strategy type | 50 signals | 72 hours | Unknown territory = more data |

Evidence required:
- [ ] Signal count: n = [N] (must meet minimum)
- [ ] Duration observed: [X] hours
- [ ] No crashes or errors during observation
- [ ] Dry-run log excerpt showing signal generation

### 4C: Paper Performance Analysis

Compute performance metrics on dry-run data. Apply R8 confidence labels.

Evidence required:
- [ ] Win rate computed: [X]% [CONFIDENCE LABEL per R8]
- [ ] Meets threshold: WR >= 70% for live trading (or asset-specific gate from MEMORY)
- [ ] P&L simulated: expected $[X]/trade at current balance and sizing
- [ ] Position size reality check (R11): balance * bet_pct = $[X]/position
- [ ] Edge cases identified: worst loss, max consecutive losses, drawdown

**Hard blocks:**
- WR < 70% on n >= 30: NO-GO. Do not proceed to Phase 5.
- n < 30: [ANECDOTAL]. Extend observation period. Do not proceed.
- Simulated max drawdown > MAX_DAILY_LOSS: NO-GO. Reduce sizing first.

### 4D: Config Drift Verification

Before changing any .env parameter, verify current state matches MEMORY.md.

Evidence required:
- [ ] Current .env values captured: `grep -E '[RELEVANT_KEYS]' /opt/lagbot/instances/[instance]/.env`
- [ ] Compared against MEMORY.md values
- [ ] Any drift flagged and resolved BEFORE proceeding
- [ ] .env backup created: `cp .env .env.bak.$(date +%s)`

### Gate 4: Testing Complete

**Gate output**: "Testing complete. n=[N] signals, WR=[X]% [CONFIDENCE], simulated P&L=$[X]/trade. No config drift. No errors during observation. Ready for review."

OR: "Testing NO-GO: [specific blocker]. Required action: [what to fix]."

---

## Phase 5: REVIEW (Go-Live Gate)

**Tool**: DARIO GO_LIVE_GATE mode (Phase 0.9)

### Mandatory Checks

Run all GLG checks from DARIO Phase 0.9:

- [ ] GLG-1: No open CRITs in MEMORY.md or bugs-reference.md
- [ ] GLG-2: No ephemeral state without DB seeding
- [ ] GLG-3: Position size computed: $[X]/position, $[Y] max deployed
- [ ] GLG-4: All exit paths verified (profit_target, stop_loss, time_decay, market_resolved)
- [ ] GLG-5: All .env file paths exist on VPS
- [ ] GLG-6: Dry-run signals observed (Phase 4B evidence)
- [ ] GLG-7: Observability stack active (Slack, context feed, cron)
- [ ] GLG-8: Paper trade gate passed (Phase 4C evidence)

### Gate 5: Review Complete

**Gate output**:
```
GO-LIVE GATE: GO
  Paper WR: [X]% on n=[N] signals
  Position sizing: $[X]/position, $[Y] max deployed
  Observability: [Slack: Y/N] [Context feed: Y/N] [Cron: Y/N]
  Open CRITs: 0
  Config drift: none
```

OR: "GO-LIVE GATE: NO-GO. Blockers: [list]."

---

## Phase 6: DEPLOY

**Tool**: `predeploy.sh --deploy [instance]`

### Staged Rollout

For new strategies or significant changes, deploy in stages:

| Stage | Config | Duration | Gate to Next |
|-------|--------|----------|-------------|
| 1. Shadow only | `SHADOW_ASSETS=[asset]` | 48h | WR >= 70% on n >= 20 |
| 2. Limited live | 50% sizing (`_SIZE_MULT=0.5`) | 72h | WR >= 65% on n >= 30 |
| 3. Full live | Normal sizing | Ongoing | Monitor per Phase 7 |

**Exception**: Config tightening (reducing exposure, raising entry floors, adding blocks) can skip staged rollout. Only loosening requires staged proof.

### Deploy Checklist

- [ ] Stop service: `systemctl stop lagbot@[instance]`
- [ ] Backup .env: `cp .env .env.bak.$(date +%s)`
- [ ] Apply .env changes
- [ ] scp all modified files
- [ ] Clear __pycache__: `find . -name __pycache__ -exec rm -rf {} +`
- [ ] py_compile from /tmp: `cd /tmp && python3 -m py_compile /opt/lagbot/lagbot/[file]`
- [ ] Start service: `systemctl start lagbot@[instance]`
- [ ] Post-deploy error check (60s): `journalctl -u lagbot@[instance] --since "60 seconds ago" | grep -iE "error|Traceback"`
- [ ] Checksum verification: `md5sum /opt/lagbot/lagbot/[file]`
- [ ] Confirm running: `systemctl is-active lagbot@[instance]`

### Gate 6: Deploy Complete

**Gate output**: "Deployed to [instance]. Stage: [1/2/3]. All checksums match. No errors in 60s journal. Service active."

---

## Phase 7: MONITOR

### Monitoring Cadence

| Check | When | What |
|-------|------|------|
| Immediate | Deploy + 5 min | Journal errors, service status |
| Short | Deploy + 1 hour | First signals, Slack notifications working |
| Medium | Deploy + 4 hours | Trade count, WR trending, no anomalies |
| Daily | Deploy + 24 hours | Full P&L review, compare to paper performance |
| Weekly | Deploy + 7 days | Strategy assessment, WR with meaningful n |

### Kill Criteria

Immediately kill (set DRY_RUN=true or add to SHADOW) if ANY of these trigger:

- WR drops below 50% on n >= 20 live trades
- 3 consecutive losses exceeding 2x average win size
- MAX_DAILY_LOSS hit
- Any bug discovered in the new code path
- Config drift detected between MEMORY and live

### Post-Deploy MEMORY Update

**MANDATORY**: After deploy, update MEMORY.md with:
- New config values (before/after)
- Current balance
- Strategy status (DRY_RUN/SHADOW/LIVE)
- Deploy date and commit hash

This must happen in the SAME tool-call sequence as the deploy confirmation. Do not defer.

---

## Quick Reference: When Does This Apply?

| Change Type | Phases Required | Can Skip |
|-------------|----------------|----------|
| New trading strategy | 1-2-3-4-5-6-7 (ALL) | Nothing |
| Enable existing strategy on new asset | 1-2-3-4-5-6-7 (ALL) | Nothing |
| Lift DRY_RUN to live | 4C-4D-5-6-7 | Phases 1-3 (already implemented) |
| Tighten config (reduce exposure) | 2-3-6 | Phases 1, 4, 5 (lower risk) |
| Loosen config (increase exposure) | 1-2-3-4-5-6-7 (ALL) | Nothing |
| Bug fix (clear root cause) | 3-6 | Phases 1, 2, 4, 5 (use PRINCIPLES.md P-ENG-*) |
| New non-trading feature | 2-3-6 | Phases 1, 4, 5 (no money at risk) |
| Infrastructure change | 2-3-6-7 | Phases 1, 4, 5 |

---

## The $1,323 Losses That Wrote This Document

| Loss | What Happened | Which Phase Would Have Caught It |
|------|--------------|--------------------------------|
| -$681 (15m snipe) | Backtest overfitted, went live without paper trading | Phase 4B/4C: 30 dry-run signals would have shown 17% WR |
| -$454 (0.95+ snipe) | 101 trades before data showed negative EV | Phase 4C: paper analysis would have shown negative EV before trade 1 |
| -$140 (weather arb) | Trusted Medium articles, ignored own 90% loss data | Phase 1 Gate: R14 T0 contradiction blocker, R15 killed feature gate |
| -$48 (config drift) | SNIPE_MAX_SECS_REMAINING was 45, MEMORY said 10 | Phase 4D: config drift verification |

Every dollar was preventable. Every phase gate exists because we learned the hard way.
