---
id: pwin-estimator-binance
name: "p\u0302_win Estimator from Binance Feeds (Target)"
domain: statistical-methods
aliases:
- pwin estimator
- pwin_estimator
- p_win estimator
- probability estimator
- calibrated pwin
- binance pwin
- binance probability model
- signal-to-pwin model
code_refs:
- path: binance_momentum.py
  lines: 649-722
  sha256: 16f002c5ea252756
- path: binance_momentum.py
  lines: 723-810
  sha256: 538d7c41f71cbe72
- path: binance_momentum.py
  lines: 529-564
  sha256: 5d90e2e355e243ff
- path: tools/build_pwin_dataset.py
  lines: 1-50
  sha256: a1c82855efe28819
- path: tools/baseline_calibration.py
  lines: 1-50
  sha256: 92f5db9138790e0d
related:
- entry-band
- btc-5m-snipe-strategy
- mtc-gate
- adverse-selection
- backtest-harness
- alpha-decay
parent_concepts: []
child_concepts: []
last_verified: '2026-04-23T07:11:23Z'
confidence: target
---

## What

A calibrated estimator that takes live Binance-derived features (60s
momentum magnitude, sharp-move magnitude, window-delta, taker delta,
VPIN, coinbase premium) and outputs `p̂_win` — the probability the YES
side of a Polymarket 5m up/down market will resolve true.

This node exists because the 2026-04-22 entry-band walk-forward ruled
out every raw or `(band, signal_source)` filter tested so far (see
`entry-band.md`). The path forward is explicit: no band filter will
produce edge on this market structure; the only thing that can is a
**calibrated probability estimate whose signal is stronger than
entry_price itself**. Without it, EV_net gating (see
`btc-5m-snipe-strategy.md`) is uncalibrated by construction.

This is a **Target** node. The code does not exist. The scope below is
the scaffolding a dedicated session should execute against. Do not
begin work on this node mid-session on an unrelated thread; spawn it as
its own session with budget stated up front.

## Why it's the only path forward

Three walls the current stack has already hit:

1. **`btc-5m-snipe-strategy.md`** — the EV_net formula requires a
   `p̂_win` input. At every fixed `p̂_win ∈ {0.94…1.00}` the formula
   fires on 61/76 rows of the 93-97 phantom_resolved sample and loses
   money. The formula is correct; its `p̂_win` input is not.
2. **`entry-band.md`** — no band (00-55, 55-70, 70-85, 85-93, 93-97,
   97+) produces edge when entry_price is the only discriminator.
3. **P9 in `PRINCIPLES.md`** — even the two (band, signal_source)
   subsets that passed P5 failed walk-forward because they fired in a
   single regime window. The estimator has to let us fire on a
   **continuous gating signal**, not a band-threshold, so samples
   accumulate across disjoint windows.

## Phases (acceptance criteria per phase, no skipping)

### Phase 1 — Feature dataset (target: 1 session, ≤4h)

**Deliverable:** `data/pwin_features.parquet` (or sqlite view) joining
every row of `trades` with the Binance features observable at
`entry_time`.

**Columns at minimum:**
- Target: `y = (pnl > 0)` on rows with `exit_reason IN
  ('market_resolved','phantom_resolved')`.
- Entry context: `entry_price`, `entry_band`, `signal_source`, `side`,
  `asset` (btc/eth/sol/xrp/etc.), `window_secs` (5m / 15m).
- Binance features at `entry_time` (all from existing code — no new
  plumbing):
  - `binance_momentum_60s_pct` — from `_check_momentum` buffer.
  - `binance_sharp_move_pct` — from `_check_sharp_move`.
  - `binance_window_delta` — from `_check_window_delta`.
  - `taker_delta_60s` — from `get_taker_delta`.
  - `vpin_300s` — from `get_vpin`.
  - `coinbase_premium` — from `get_coinbase_premium`.
- Implied probability: `entry_price` itself (the market's own p̂).

**Acceptance:**
1. Every row has ≥4 non-null Binance features.
2. `y` is defined on ≥500 rows (sample size for train/test split).
3. Temporal coverage spans ≥3 calendar days with ≥100 trades per day
   (violates P9 if concentrated).

**Blocker resolution before Phase 2:** if fewer than ~500 labelled rows
exist with live Binance features, the dataset is underpowered.
Options: (a) collect more via shadow mode — see `shadow-mode.md`, or
(b) accept limited scope (BTC-only 5m) and document the narrowness.

### Phase 2 — Baseline calibration check (target: 2-3h)

**Deliverable:** a calibration plot of `entry_price` vs observed
outcome frequency on the Phase 1 dataset. 10 bins of `entry_price`;
for each bin, plot the bin midpoint on x and observed WR on y. Overlay
y=x (perfect calibration).

**Why:** before training any model, confirm the market's own price is
already well-calibrated. If Polymarket prices at deep-favorite are
already correct, there is no edge for a model to extract at that band
regardless of features. If prices are systematically miscalibrated,
that's the first finding — the model doesn't need to be smart, just
orthogonal to price.

**Acceptance:**
1. Plot persisted at `docs/research/pwin_baseline_calibration.png`.
2. Calibration table persisted alongside as CSV.
3. One-paragraph verdict: "entry_price is/is-not a calibrated
   probability on this sample."

**Phase 2 result (2026-04-22, `tools/baseline_calibration.py`):**

`entry_price` is **NOT** a calibrated probability on this sample.
Feature cohort (n=326) shows weighted mean |gap| = 0.259, with 5/10
price bins having their Wilson 95% CI entirely excluding y=x. Full
labelled cohort (n=707) is worse: weighted mean |gap| = 0.326,
8/10 bins miss y=x.

Two load-bearing miscalibrations, both consistent with the
[entry-band.md](entry-band.md) 2026-04-22 verdicts:

1. **Deep-favorite 0.9-1.0** (n=115 feature / n=343 full): observed
   WR = 0.417 / 0.481 vs price-implied 0.95, gap ≈ -47 to -53 pp,
   Wilson CI [0.331, 0.509] entirely below 0.95. This IS the 93-97
   band falsification expressed in calibration terms.
2. **Mid-favorite 0.5-0.8**: observed WR consistently 88-97% on
   bins whose midpoints are 0.55 / 0.65 / 0.75. Gaps +21 pp to +43 pp.
   Matches the `flat_regime_rtds@55-70` + `binance_momentum@70-85`
   in-sample survivors before P9 killed them as episodic artifacts.

**Caveat (don't over-read).** The sample is filtered through the
bot's entry logic: this measures miscalibration conditional on the
bot choosing to enter, not a random-sample calibration of Polymarket
prices globally. The finding is "the bot's own entries are
systematically priced wrong by the market at deep-favorite and
mid-favorite bands," not "Polymarket is broken."

**Implication for Phase 3.** The calibration gap is wide enough that
a model using entry-time features (momentum, taker delta, VPIN) can
plausibly beat `entry_price` as `p̂_win` — the estimator path is NOT
foreclosed by "the market already knows." However, Phase 3 is
**directly blocked by P9 on the test split only**: a 60/20/20
temporal split on 326 rows lands 21/3/**2** training/validation/test
calendar days, and 2 days on test fails the
`COUNT(DISTINCT date(entry_time)) ≥ 3` cheap check in
`PRINCIPLES.md` §P9. The 2-day test span is narrower than the
aggregate 25-distinct-day coverage because 2026-04-21 alone
contributed 88 rows (27% of the cohort), pushing the 20% cutoff
deep inside that single-day spike. See Phase 2.5 below for the
collection plan.

Artifacts:
- [docs/research/pwin_baseline_calibration.png](../../research/pwin_baseline_calibration.png)
- [docs/research/pwin_baseline_calibration.csv](../../research/pwin_baseline_calibration.csv)
- `tools/baseline_calibration.py` (producer; stdlib + matplotlib only)

### Phase 2.5 — Data collection sprint (target: ≤10 days wall-clock, spec only)

**Purpose.** Bridge Phase 2 → Phase 3 by closing the test-split P9
gap discovered above. Scoping deliverable, no code on the trading
path, no `.env` change. Written 2026-04-22 after the ratified
Decision A ("scope shadow-mode collection").

**The gap, quantified (2026-04-22 snapshot):**

| split | rows | calendar days | P9 gate |
|---|---:|---:|---|
| train (60%) | 195 | 21 | PASS |
| val (20%) | 65 | 3 | PASS |
| test (20%) | 66 | **2** | **FAIL (need ≥3)** |

The fix is NOT "collect 45 more days." It is "collect enough post-
2026-04-22 days so that the 20% tail crosses ≥3 distinct calendar
dates." At ~10 full-feature rows per normal day (median excluding
the 04-21 spike), ~7-10 additional full-feature days push ~70-100
rows into the tail, which will span ≥3 new dates and extend total
n past the 500-row Phase 1 target simultaneously.

**Why shadow-mode is the wrong framing (clarification).** The
estimator does not yet exist, so there is nothing to run under
`shadow-mode.md` discipline. Phase 4's shadow is for the trained
model's `p̂_win` inference. Phase 2.5's collection uses existing
signal + DRY_RUN phantom-fill plumbing — features are already
captured in `signals.db`, outcomes are already captured in
`trades` via phantom_resolved rows, and the Phase 1 join already
works. Phase 2.5 is purely a throughput-and-time question, not a
new plumbing question.

**Preconditions emmanuel must maintain for collection to run:**

1. `lagbot@emmanuel` active; DRY_RUN=true is acceptable (phantom
   fills produce the labelled rows).
2. signals.db continues receiving rows with all three Binance
   features non-null: `momentum_pct`, `taker_delta`, `vpin_5m`.
   Current coverage is ≥95% post-2026-03-14. Regression here would
   invalidate collection.
3. No rollback of the phantom adverse-check patch (signal_bot.py
   `_run_phantom_adverse_check`, shipped 2026-04-18). This is what
   populates the adverse columns that downstream Phase 3 will use
   if we add adverse features to the model. Verified in
   [btc-5m-snipe-strategy] active-sequencing deploy log.
4. `coinbase_premium_bps` emission bug remains open but does NOT
   block Phase 2.5 — that feature is 0/16,978 across all of
   signals.db today, meaning none of Phase 3's 326 rows have it,
   so the feature is effectively dropped from Phase 3's input
   vector until the emission is fixed (separate task).

**Verification queries (run daily or on demand):**

```bash
ssh root@82.24.19.114 '/opt/lagbot/venv/bin/python3 -c "
import sqlite3
trades_db = sqlite3.connect(\"/opt/lagbot/instances/emmanuel/data/performance.db\")
sig_db    = \"/opt/lagbot/instances/emmanuel/data/signals.db\"
trades_db.execute(f\"ATTACH DATABASE \\\"{sig_db}\\\" AS sig\")
# rows added since 2026-04-22 with all 3 binance features
q = \"\"\"
SELECT COUNT(*) AS n, COUNT(DISTINCT date(t.entry_time, \\\"unixepoch\\\")) AS days
FROM trades t
JOIN sig.signals s
  ON (s.id = t.signal_id
      OR (s.slug = t.slug AND ABS(s.epoch - t.entry_time) < 300))
WHERE t.exit_reason IN (\\\"market_resolved\\\",\\\"phantom_resolved\\\")
  AND t.pnl IS NOT NULL
  AND s.momentum_pct IS NOT NULL
  AND s.taker_delta IS NOT NULL
  AND s.vpin_5m IS NOT NULL
  AND t.entry_time > strftime(\\\"%s\\\", \\\"2026-04-22\\\")
\"\"\"
print(trades_db.execute(q).fetchone())
"'
```

Target: `n ≥ 70` AND `days ≥ 3`. When the query returns both,
Phase 2.5 is complete; rerun `build_pwin_dataset.py` and
`baseline_calibration.py` to refresh Phase 1/2 artifacts, then
re-check the 60/20/20 test-split P9 gate before advancing to
Phase 3.

**Acceptance:**
1. Full-feature row count post-2026-04-22 reaches ≥70.
2. Distinct calendar dates post-2026-04-22 reaches ≥3.
3. On the refreshed dataset, `test` split (last 20% by
   `entry_time`) satisfies `COUNT(DISTINCT date(entry_time)) ≥ 3`.
4. No infra regression on emmanuel (`is-active`, no Tracebacks
   in `journalctl --since "24h ago"`, feature non-null rate
   ≥95% on new signals).

**Check-in cadence.** Poll the verification query once every
~48h; do NOT babysit daily. If acceptance #1-#3 land, proceed to
Phase 3. If a week passes without progress, re-route to Phase
2.5 diagnosis (why are full-feature rows stalled?) rather than
re-raising the estimator question.

**Stop conditions (re-route to operator, do not auto-escalate):**
- Feature non-null rate drops below 95% on post-04-22 signals
  (instrumentation regression).
- 10 wall-clock days elapse with <3 new test-split days
  accumulated (throughput regression; likely signal-fountain
  dryness — see `binance-momentum.md` watchdog, not this node).
- Any infra change to signal_bot, signals.db schema, or the
  phantom-resolution path lands without being traced through
  this node's preconditions.

**Reversibility (P7).** Phase 2.5 is pure spec + verification
query. Nothing deploys. Nothing touches `.env`. Rolling back is
deleting this section. No work to undo.

### Phase 3 — Train a calibrated model (target: 1 session, ≤6h)

**Deliverable:** `tools/pwin_estimator_binance.py` — stdlib + scikit
only, no heavyweight frameworks. Produces calibrated probability
output (isotonic or Platt scaling, not raw logistic).

**Model constraints:**
- Logistic regression OR small GBM (≤50 trees). Nothing exotic.
- Walk-forward split: train on first 60% of `entry_time`, validate on
  middle 20%, test on last 20%. P9 cheap-check on each split (≥3
  distinct activation days per split).
- Isotonic calibration fit on validation split, evaluated on test
  split.
- Features: the Phase 1 columns minus `entry_price` (so the model is
  orthogonal to the market's own estimate).

**Acceptance:**
1. Test-set Brier score < `entry_price`-as-pwin baseline Brier.
2. Test-set log-loss < `entry_price` baseline.
3. Reliability diagram on test split shows |observed - predicted| ≤
   0.05 in every bin with ≥20 samples.
4. Passes P5 (test n ≥ 30) AND P9 (test spans ≥2 disjoint activation
   windows, checked via `COUNT(DISTINCT date(entry_time))` ≥ 3).

### Phase 4 — Shadow deployment (target: 2 weeks wall-clock)

**Deliverable:** estimator runs in production under `shadow-mode.md`
discipline — every inference is logged; no orders are placed on its
output. Matched against realized outcomes.

**Acceptance:**
1. `shadow_pwin` column populated on every live or dry-run signal for
   ≥500 new rows.
2. Out-of-sample Brier score (shadow period) within 10% of test-split
   Brier.
3. No infra regressions: `is-active`, no Tracebacks, latency budget
   unchanged.
4. MTC R1-R5 on the shadow cohort passes at its entry band.

### Phase 5 — Activation (target: after Phase 4 MTC pass)

**Deliverable:** `ENABLE_PWIN_GATE=true` in `emmanuel/.env` wiring
`btc-5m-snipe-strategy`'s EV_net formula to consume `pwin_estimator`
output instead of a fixed `p̂_win` constant.

**Acceptance:** governed by existing deploy-lifecycle per P8. No new
rule here.

## Where

Feature inputs live in existing code, no plumbing needed:

- [binance_momentum.py:649-722](binance_momentum.py#L649-L722) —
  `_check_momentum` (60s momentum %).
- [binance_momentum.py:723-810](binance_momentum.py#L723-L810) —
  `_check_sharp_move` (sharp-move magnitude).
- [binance_momentum.py:529-564](binance_momentum.py#L529-L564) —
  `get_taker_delta`, `get_vpin`, `get_vpin_sustained_alert` (order
  flow).
- [binance_momentum.py:572-596](binance_momentum.py#L572-L596) —
  `get_coinbase_premium` (cross-exchange mispricing).

**Phase 1-2 artifacts (landed 2026-04-22, commit `306d454`):**

- `tools/build_pwin_dataset.py` — Phase 1 dataset builder (exists).
- `tools/baseline_calibration.py` — Phase 2 calibration check (exists).
- `data/pwin_features.csv` — Phase 1 feature+label store.
- `docs/research/pwin_baseline_calibration.png` — Phase 2 plot.

**Target artifacts** (Phase 3+, do NOT exist yet):

- `tools/pwin_estimator_binance.py` — training + inference entry point.

## Budget & guardrails

- **Total estimated cost:** 3 sessions × ~4-6h = 12-18h of dedicated
  work, excluding the 2-week Phase 4 wall-clock.
- **Budget for session 1:** Phase 1 only. If dataset underpowered, STOP
  and decide collection strategy before continuing.
- **Reversibility (P7):** Phases 1-3 are file-only, fully reversible.
  Phase 4 (shadow) is reversible — shadow is non-trading. Phase 5
  (activation) is gated by full deploy-lifecycle; standard per P8.
- **No live-parameter edits in Phases 1-4.** The estimator must prove
  itself on a shadow cohort before touching any `.env`.
- **P9 check at every split:** `COUNT(DISTINCT date(entry_time))` ≥ 3
  is the cheap check from `PRINCIPLES.md` §P9. Any phase that produces
  a <3-day sample STOPS the pipeline for more data collection.

## When-to-touch

Route here when the user says:

- "calibrated p_win" / "pwin estimator" / "probability model"
- "build the p̂_win estimator" / "start the estimator"
- "what's blocking btc-5m-snipe" (the calibrated-p̂_win dependency)
- "why did the band filter fail" (this is the alternative path)
- "scope the ML work" / "scope the estimator"
- "do we need a model?" (yes, and this is the shape of it)

Do NOT route here when the user is asking about individual feed
plumbing — that's `binance-momentum.md` or `signal-pipeline.md`. This
node owns the model layer on top of those feeds.

## Not started

The scaffolding above is the **plan**, not the work. Every Phase must
ratify its acceptance criteria before the next begins. `confidence:
target` stays until Phase 3 produces a model that passes P5 + P9 on a
real test split.
