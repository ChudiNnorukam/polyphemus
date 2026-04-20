---
id: entry-band
name: Entry Band
domain: data-model
aliases:
- entry price band
- price bucket
- deep favorite
- 0.93-0.97
- entry_band
code_refs:
- path: tools/mtc_pre_deploy_gate.py
  lines: 224-245
  sha256: a2e7bda5d63e95d6
- path: tools/mtc_pre_deploy_gate.py
  lines: 264-284
  sha256: 47ccac283ae203b6
- path: sql_views/vw_trade_attribution.sql
  lines: 1-40
  sha256: b8886523be3737ea
related:
- mtc-gate
- adverse-selection
- trading-strategies
parent_concepts: []
child_concepts: []
last_verified: '2026-04-20T05:02:09Z'
confidence: verified
---

## What

Entry band is how the bot buckets a fill by entry price so we can
segment performance across "cheap side" (0.00-0.55), "middle"
(0.55-0.85), "favorite" (0.85-0.93), "deep favorite" (0.93-0.97), and
"chalk" (0.97+). The bucket boundaries are the same between the MTC
gate's Python filter and the `vw_trade_attribution` SQL view — if one
diverges, the gate and dashboard disagree on which trade lives in
which band, which has burned us before.

The live question driving this node: the friend's 0.93-0.97 hypothesis.
At deep-favorite prices the payoff is 5-7¢ for 93-95¢ risked, so any
adverse selection bps dominates the edge. Segmenting by entry_band is
the only way to honestly verdict whether that strategy is viable.

## Where

- Bucket definition (authoritative Python source) at
  [tools/mtc_pre_deploy_gate.py:224-245](tools/mtc_pre_deploy_gate.py#L224-L245)
  — `_ENTRY_BAND_CUTS` tuple + `_derive_entry_band(price)`.
- Filter application at
  [tools/mtc_pre_deploy_gate.py:264-284](tools/mtc_pre_deploy_gate.py#L264-L284)
  (`_apply_trade_filters` with `filter_entry_band=...`).
- SQL view mirror at
  [sql_views/vw_trade_attribution.sql:1-40](sql_views/vw_trade_attribution.sql#L1-L40)
  — must match `_ENTRY_BAND_CUTS` exactly. Comment at line 223 is the
  lockstep contract.

## When-to-touch

Route here when the user says:

- "the 0.93-0.97 band" / "deep favorites" / "the friend's hypothesis"
- "segment by price band" / "bucket by entry price"
- "does strategy X work above 0.85"
- "cheap side" / "0.50 entries" (routes to 00-55 bucket)
- "chalk entries" / "0.97 and up" (routes to 97+ bucket)
