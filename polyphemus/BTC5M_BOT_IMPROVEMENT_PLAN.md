# BTC 5m Bot Improvement Plan

## Summary

This roadmap turns the project's current capabilities overhang into a safer BTC 5m improvement program without changing live trading behavior in the first pass.

The first pass builds shared truth, retrieval, replay, and reporting. The second pass uses that evidence to justify selective BTC 5m strategy changes. The plan explicitly rejects "trade every epoch" as a target. The intended system is selective, skip-capable, and evidence-gated.

## First Pass: Truth And Retrieval

- Add a repo-local BTC 5m reporting workflow that unifies:
  - per-instance `signals.db`
  - per-instance `performance.db`
  - `epoch_coverage`
  - current config snapshot
  - current market-context snapshot
  - selected DARIO reports
- Keep scope to BTC + 5m only.
- Produce compact markdown reports with R8 labels for:
  - price buckets
  - time-remaining buckets
  - signal source
  - guard reasons
  - exit reasons
  - regime and context slices when available
- Extend replay tooling so it can filter by asset, window, and source and emit markdown, JSON, or CSV artifacts for review.

## Evidence Requirements Before Strategy Changes

- Use `signals` plus `epoch_coverage` so the system can compare:
  - executed epochs
  - filtered epochs
  - no-signal epochs
- Tag each report run with:
  - instance
  - time range
  - config snapshot hash or explicit config label
  - strategy sources present in the data
- Keep OpenClaw context in scope because it is already part of the signal path.
- Treat OpenFang as unverified until live VPS state confirms its existence and role.

## Planned BTC 5m Strategy Work After Evidence Rebuild

### Phase A: Stabilize Decision Quality

- Keep BTC 5m as the only optimization target.
- Defer ETH, market maker, 15m, SOL, XRP, and broader orchestration work.
- Revalidate entry-price and time-remaining buckets from fresh VPS data.
- Promote "no trade" as a valid and preferred outcome when the matched cohort is anecdotal or negative expectancy.

### Phase B: Remove Confirmed Value Destroyers

- Reassess `time_exit` and `stop_loss` specifically for BTC 5m.
- Prefer entry prevention over bad late exits.
- Only preserve current maker and taker assumptions if fresh VPS data still supports them after fee and fill checks.

### Phase C: Add Evidence Gating

- Start with reportable shadow-only verdicts:
  - `allow`
  - `shadow`
  - `block`
- Candidate gates:
  - bad price bucket
  - too-late-in-epoch
  - weak regime bucket
  - repeated losing guard-reason clusters
- Do not move any gate from shadow to active until lifecycle testing requirements are met.

## Acceptance Criteria

- A BTC 5m-only report can be generated from current or cached instance data.
- Every recommendation in the generated report carries an explicit R8 label.
- Replay tooling can filter by asset, window, and source and write structured artifacts.
- No live config or VPS mutation is required for the first implementation pass.

## Assumptions

- Improvement scope is BTC 5m only.
- `CLAUDE.md` and `LIFECYCLE.md` override stale `.omc` notes on conflict.
- The first pass is intentionally non-live and read-only with respect to production behavior.
