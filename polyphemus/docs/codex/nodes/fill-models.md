---
id: fill-models
name: Fill Models
domain: fill-models
aliases:
- v1_taker
- v2_probabilistic
- MakerFillModel
- fill_router
- dry_run_v2
- POLYPHEMUS_DRY_RUN_V2
- prob_hit
- prob_miss
- buried
- crossed_book
code_refs:
- path: dry_run_fill_model.py
  lines: 43-88
  sha256: d2615cd65cba457f
- path: fill_router.py
  lines: 70-128
  sha256: 7f416d6308584993
related:
- adverse-selection
- entry-band
- trade-tracer
parent_concepts: []
child_concepts: []
last_verified: '2026-04-20T05:02:09Z'
confidence: inferred
---

## What

"Fill model" is the bot's answer to "did we actually get that order, and at
what price?" There are three: `live` (real CLOB fill, price from the
exchange), `v1_taker` (legacy dry-run; every order assumed to fill
instantly at our quoted price), and `v2_probabilistic` (new dry-run; each
order gets a per-second fill probability scaled by how aggressive our
price is in the spread, capped at 95% cumulative). V2 is gated behind
`POLYPHEMUS_DRY_RUN_V2` so we can roll back instantly — V1 still works,
schema stays uniform.

The reason this matters: before V2, dry-run win-rate was systematically
overstated because every order "filled" with no queue risk. At deep-
favorite prices (see entry-band) the overstatement dwarfs the edge.
Post-fill attribution writes `fill_model` + `fill_model_reason` into
the trades row so MTC segment queries can verdict each model separately.

## Where

- `MakerFillModel.evaluate` at
  [dry_run_fill_model.py:43-88](dry_run_fill_model.py#L43-L88) —
  aggression/spread math, `p_fill = 1 - (1 - per_sec_rate)^elapsed`,
  returns a `MakerFillDecision` with reason `prob_hit` / `prob_miss` /
  `buried` / `crossed_book`.
- `route_dry_run_fill` at
  [fill_router.py:70-128](fill_router.py#L70-L128) — single routing
  entry point; V1 branch preserves legacy behavior, V2 branch
  delegates to `MakerFillModel.evaluate` and tags the FillRecord.

## When-to-touch

Route here when the user says:

- "fill model" / "v1 vs v2" / "probabilistic fill"
- "flip the dry-run v2 flag" / "POLYPHEMUS_DRY_RUN_V2"
- "why did my dry-run order not fill"
- "prob_hit / prob_miss / buried / crossed_book" in logs
- "is the dry-run fill rate realistic" / "queue risk"
- "attribution by fill model"
