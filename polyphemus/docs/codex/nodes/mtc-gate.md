---
id: mtc-gate
name: MTC Pre-Deploy Gate
domain: statistical-methods
aliases:
- MTC gate
- pre-deploy gate
- R1 R2 R3 R4 R5
- Wilson lower bound check
- deflated sharpe
- walk-forward
- alpha decay
code_refs:
- path: tools/mtc_pre_deploy_gate.py
  lines: 293-335
  sha256: 0aa42456ddf6aaaf
- path: tools/mtc_pre_deploy_gate.py
  lines: 421-473
  sha256: 8597900fc25edf24
related:
- entry-band
- adverse-selection
- deploy-lifecycle
parent_concepts: []
child_concepts: []
last_verified: '2026-04-20T05:02:09Z'
confidence: verified
---

## What

The MTC (Minimum Trustworthy Confirmation) gate is the "can we trust
this backtest" check that runs before any strategy change hits live.
It applies five standard-issue statistical checks over the trade log:
R1 sample size, R2 Wilson-CI hypothesis test that WR > breakeven, R3
walk-forward consistency across time splits, R4 deflated Sharpe
against multiple-testing bias, R5 alpha decay over the lookback window.
Any check fails => verdict FAIL.

`_gate_from_rows` is the single integrative function. Both the global
`run_gate` and the `run_segmented_gate` (per-source, per-band, per-fill-model)
routes reuse it so receipt consumers and the webapp don't need to
branch on segmented-vs-global. The verdict dict shape is the public
contract — do not change without updating both callsites.

## Where

- Individual check functions (R1/R2 shown; R3/R4/R5 follow the same
  shape) at
  [tools/mtc_pre_deploy_gate.py:293-335](tools/mtc_pre_deploy_gate.py#L293-L335).
- Integrative verdict at
  [tools/mtc_pre_deploy_gate.py:421-473](tools/mtc_pre_deploy_gate.py#L421-L473)
  (`_gate_from_rows`) — runs the 5-check sequence and returns the
  receipt-shaped dict.
- Segmentation wrappers (`--segment-by`, `--filter-entry-band`) live
  immediately after, shared between CLI and webapp.

## When-to-touch

Route here when the user says:

- "run the gate" / "verdict" / "is this strategy ready to ship"
- "Wilson CI" / "walk-forward" / "deflated Sharpe" / "alpha decay"
- "segment by source" / "per-band verdict"
- "can I deploy this" / "is this passing"
- anything that looks like R1/R2/R3/R4/R5 failure reasons
