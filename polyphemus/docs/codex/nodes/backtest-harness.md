---
id: backtest-harness
name: Backtest Harness (Filter Search + Walk-Forward)
domain: statistical-methods
aliases:
- filter_search
- walk_forward
- Scenario D
- Scenario D prime
- backtest
- filter candidates
- Wilson lower
- wl_ratio
- breakeven_ratio
- compute_metrics
- FILTER_CANDIDATES
- Pareto
- wilson_lower
- Metrics dataclass
- entry filter
code_refs:
- path: tools/backtest/filter_search.py
  lines: 1-17
  sha256: eecfca9be893441d
- path: tools/backtest/filter_search.py
  lines: 28-96
  sha256: 25d4beeffe6205ea
- path: tools/backtest/walk_forward.py
  lines: 1-40
  sha256: ba40182d7b50b33e
- path: tools/backtest/walk_forward.py
  lines: 42-76
  sha256: bafbd5a203246549
- path: tools/backtest/markov_gate_sim.py
  lines: 1-40
  sha256: 0b3b23433185d946
- path: tools/backtest/scenario_compare.py
  lines: 1-40
  sha256: 956cc807e3549719
related:
- mtc-gate
- mtc-pre-deploy-gate-tool
- entry-band
- alpha-decay
parent_concepts: []
child_concepts: []
last_verified: '2026-04-23T07:11:23Z'
confidence: inferred
---

## What

Backtest Harness is the **offline filter-search + walk-forward** kit we
use to answer "given 500 realized trades, is there a subset with a
deployable edge?" without running new live experiments. It's one dir:
`tools/backtest/`, two core modules: `filter_search.py` (fit) and
`walk_forward.py` (verify the fit wasn't curve-fit noise).

**Critical caveat at `filter_search.py:1-17`**: we do NOT have intra-trade
price data (MAE/MFE/tick history). That means we CANNOT backtest different
stop-losses, profit targets, or holds — those require simulation with
per-tick price paths. What we CAN backtest is **entry filters**: which
subset of the 500 trades we actually took would have been profitable in
aggregate? Every "Scenario D" / "Scenario D'" conversation comes from
this tool's output.

Core math at `filter_search.py:28-96`:

- **`wilson_lower(p, n, z=1.96)`** (:32-38) — 95% lower bound on win rate.
  See Formula 1 in the Phase 5 plan. The MTC gate's R2 check consumes
  exactly this value.
- **`Metrics` dataclass** (:41-54) — `n, wins, wr, wilson_lo, avg_win,
  avg_loss, wl_ratio, breakeven_ratio, gap, sharpe, total_pnl,
  total_pnl_pct`. `gap = wl_ratio - breakeven_ratio`; positive = +EV
  strategy under these filters.
- **`compute_metrics(rows)`** (:57-96) — pure function. Takes a list of
  dicts (each with `pnl`, `pnl_pct`), returns a `Metrics`. No DB access,
  no globals. Makes the walk-forward loop trivial.

Walk-forward machinery at `walk_forward.py:20-76`:

- **`FILTER_CANDIDATES` dict** (:20-39) — pure lambdas over trade rows.
  Each key is a named filter ("A_ep_80_plus", "B_skip_65_80",
  "C_skip_65_80_and_btc_down", "D_whitelist", "F_skip_65_80_plus_whitelist").
  Add a new candidate: append a lambda here.
- **`walk_forward(rows, folds)`** (:42-76) — sorts rows by `entry_time`,
  chops into `k` equal-sized folds, applies each filter candidate to each
  fold, reports per-fold WR/PnL/Sharpe and the aggregate `+folds/total`
  (how many of `k` folds were positive). The MTC R3 rule — `positive_folds
  / folds >= 0.60` — is printed at the bottom as the legend. `tools/
  mtc_pre_deploy_gate.py` applies the same threshold on live trades; this
  tool applies it on historical.

The output is a table, not a verdict. You read the top-Pareto rows and
DECIDE whether to ship as an entry filter. Scenario D (whitelist) produced
the most consistent walk-forward result on the 508-trade Apr 10 snapshot;
Scenario D' (with `entry_price >= 0.80`) was flagged for deferral because
it would contaminate the "natural sampling of 0.93-0.97" experiment
upstream.

This harness is **offline-only**. It does not touch live DBs, the CLOB,
or any .env. Safe to run from any machine with a copy of a performance DB.

## Where

- Module docstring at
  [tools/backtest/filter_search.py:1-17](tools/backtest/filter_search.py#L1-L17)
  — stated purpose + CLI invocation. The "no intra-trade data" caveat is
  explicit here.
- Wilson + Metrics + compute_metrics at
  [tools/backtest/filter_search.py:28-96](tools/backtest/filter_search.py#L28-L96)
  — pure math, pure functions. Reused by `walk_forward.py` via direct
  import.
- Walk-forward doc + FILTER_CANDIDATES at
  [tools/backtest/walk_forward.py:1-40](tools/backtest/walk_forward.py#L1-L40)
  — top-of-file explanation of why walk-forward matters, and the five
  named candidate filters that get tested.
- Walk-forward loop at
  [tools/backtest/walk_forward.py:42-76](tools/backtest/walk_forward.py#L42-L76)
  — the k-fold split + per-fold metric print. MTC R3 legend at the bottom.

## When-to-touch

Route here when the user says:

- "backtest the filter" / "filter search" / "Pareto of filters"
- "Scenario A/B/C/D/F" / "FILTER_CANDIDATES"
- "walk-forward on N trades" / "k folds"
- "Wilson lower bound" / "wilson_lo" / "wl_ratio"
- "breakeven ratio" / "gap" / "compute_metrics"
- "add a new filter candidate"
- "why does the in-sample fit disagree with walk-forward"
- "is this edge curve-fit or real"
- "run the backtest on the latest DB snapshot"
