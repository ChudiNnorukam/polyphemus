# Quant Research Operating System

Use this when proposing, testing, or promoting any trading strategy change in Polyphemus.

This playbook exists to solve the actual failure mode in this repo:
- strong systems work
- weak evidence discipline
- too many moving parts changed before a strategy thesis was cleanly tested

## Purpose

Turn quant work into a constrained operating system:
- one hypothesis per era
- one promotable slice at a time
- fixed evidence gates
- no live claim without current-era proof

## Non-Negotiable Rules

1. One hypothesis per era.
   If entry, exit, asset, window, or sizing thesis changes, the era changes.
2. One promotable slice at a time.
   Do not mix BTC, ETH, XRP, cheap-side, momentum, and accumulator changes into one judgment.
3. Replay is support, not authorization.
   Backtests and replays can justify shadow testing. They cannot justify live promotion alone.
4. Dry-run is not live.
   Treat dry-run as execution rehearsal. Use live evidence for live claims.
5. Current runtime beats historical notes.
   If current runtime and old reports disagree, current runtime wins.
6. `NO-GO` ends the decision.
   If the shared gate is `NO-GO`, stop promotion talk and collect the missing evidence.

## Operating Loop

### Phase 0: Hypothesis Registration

Before code or `.env` changes, create an experiment scaffold:

```bash
python3 tools/quant_experiment_scaffold.py \
  --slug btc-cheap-side-v4 \
  --asset BTC \
  --windows 5m \
  --entry-family cheap_side \
  --mode shadow
```

Required artifact:
- `.omc/experiments/<slug>/hypothesis.md`

This file must define:
- exact thesis
- exact slice
- break-even math
- expected signal rate
- expected failure mode
- kill criteria
- what will NOT change during the experiment

No scaffold, no experiment.

### Phase 1: Replay / Research

Required questions:
1. Does the thesis have any domain support?
2. Does break-even work after realistic fees and fill assumptions?
3. Is expected signal rate high enough to reach decision-grade sample sizes?

Required outputs:
- replay/backtest artifact in `dario_output/`
- hypothesis updated with break-even and support summary

Gate:
- `ABORT` if break-even exceeds realistic performance
- `CONDITIONAL` if replay supports shadow-only testing

### Phase 2: Shadow Execution

Run the slice in shadow with no hypothesis drift.

Required:
- fixed config era
- fixed asset/window/entry family
- hourly snapshots
- no silent restarts that contaminate evidence

Minimum gate:
- `48h+` aligned runtime for momentum-style slices
- `>=30` completed opportunities for first directional judgment
- `>=50` completed opportunities for profitability judgment

Required artifact:
- `.omc/experiments/<slug>/evidence_log.md`

This log must record:
- start/end timestamps
- config era
- runtime incidents
- sample size
- expectancy
- drawdown
- whether the slice stayed frozen

### Phase 3: Promotion Review

A slice is eligible for review only if all of these are true:
- shared repo gate is not `NO-GO`
- current-era evidence is clean
- execution integrity is clean
- expectancy is positive after costs
- sample size is decision-grade

Required artifact:
- `.omc/experiments/<slug>/promotion_review.md`

Questions to answer:
1. What is the exact slice being promoted?
2. What current-era sample supports it?
3. What is the fee-adjusted expectancy?
4. What execution artifacts could still be faking the result?
5. What is the narrowest live rollout that tests the thesis without broadening scope?

### Phase 4: Narrow Live Canary

Only after Promotion Review passes.

Rules:
- start with the smallest credible capital/risk footprint
- no scope broadening during canary
- no second hypothesis inside the same canary

Minimum live proof before stronger claims:
- `>=50` live trades in the current era
- `WR >= 70%`
- no deployment-disrupted trades
- documented drawdown and avg loss / avg win ratio

### Phase 5: Proven Slice

Only call a slice "proven" when:
- live expectancy is positive
- sample size is no longer anecdotal
- at least one regime transition has been survived or explicitly tested

### Phase 6: Proven System

Do not claim "proven polymarket-crypto-updown-ready system" until:
- one slice has been validated end-to-end: replay -> shadow -> live
- `>=200` live trades exist across at least two regimes
- positive expectancy is documented per asset and per hour bucket
- strategy quality remains positive after execution-quality attribution

## Fixed Evidence Gates

### Gate A: Can We Even Test This?
- thesis is explicit
- break-even is computed
- signal rate is plausible
- kill criteria are explicit

### Gate B: Is Shadow Mature?
- `48h+` aligned runtime
- `>=90%` expected epoch coverage
- no unresolved audit mismatch
- no unexplained stall windows that invalidate the slice

### Gate C: Is Sample Size Enough?
- `<30` completed opportunities: anecdotal
- `30-49`: directional only, not profitability-proof
- `50-199`: supervised profitability judgment
- `200+`: stronger regime-aware evidence

### Gate D: Is the Result Actually Alpha?
- positive expectancy after costs
- not explained purely by execution artifact
- not dependent on a contaminated era
- not contradicted by live degradation evidence

## Forbidden Moves

- Promoting because replay looked strong
- Mixing BTC evidence into ETH/XRP promotion
- Calling a system profitable on `n < 50`
- Changing multiple strategic knobs before the previous hypothesis resolved
- Counting dry-run outcomes as live proof
- Claiming quant mastery because the infrastructure is now clean

## Current Repo Position

As of the latest shared runtime state:
- the system is still `NO-GO`
- the current blocker is still insufficient aligned shadow maturity
- XRP FAK accumulator work is shadow-only
- BTC remains the only asset with any meaningful live-adjacent evidence base

## Related Artifacts

- `LIFECYCLE.md`
- `AGENT_ROLE_LADDER.md`
- `kb/playbooks/go_live_gating.md`
- `kb/playbooks/strategy_promotion_review.md`
- `.omc/compass/polymarket-quant-readiness/audit.md`
