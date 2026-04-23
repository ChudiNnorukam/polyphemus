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
- path: tools/verdict_all_bands.py
  lines: 1-40
  sha256: b9874092c2456d32
- path: tools/drill_bands.py
  lines: 1-40
  sha256: 2255e27cfa869b57
- path: tools/walk_forward_survivors.py
  lines: 1-60
  sha256: 570f7436169e3f1d
related:
- mtc-gate
- adverse-selection
- resolution-snipe
- btc-5m-snipe-strategy
- pwin-estimator-binance
parent_concepts: []
child_concepts: []
last_verified: '2026-04-23T07:11:23Z'
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

The live question that drove this node historically: the friend's
0.93-0.97 hypothesis. At deep-favorite prices the payoff is 5-7¢ for
93-95¢ risked, so any mispricing dominates the edge. Segmenting by
entry_band is the only way to honestly verdict whether that strategy
is viable — and as of 2026-04-22 the verdict is IN (see next section).

## Efficiency verdict per band (2026-04-22)

All rows filter to `exit_reason IN ('market_resolved','phantom_resolved') AND pnl IS NOT NULL`
on emmanuel's `performance.db`. Breakeven WR = band midpoint (Polymarket maker fee = 0).
Wilson LB is the 95% lower bound on WR at n. See "Data quality caveats" below.

| Band | n | raw WR | Wilson LB | Breakeven | PnL | Verdict |
|---|---:|---:|---:|---:|---:|---|
| **00-55** | 210 | 0.610 | 0.542 | 0.275 | +$21,057 | **ARTIFACT — treat as KILLED.** Headline PnL is one phantom_resolved trade: `btc-updown-5m-1776824400` entry=0.015 pnl=+$20,853.89 (sharp_move, signal_bot). Remove it and one +$193 sibling → n=208 WR 60.6% PnL +$9.39 avg +$0.05. `[cheap-side-signal]` already killed the live strategy 2026-04-19 at Wilson LB=0. The $21k is a dry-run accounting artifact (phantom resolution credited at face value against entry of 0.015), not a real edge. |
| **55-70** | 61 | 0.984 | 0.913 | 0.625 | +$234 | **In-sample DEFENSIBLE, walk-forward REJECTED.** Wilson LB 0.913 ≥ breakeven 0.625 on the raw sample; `flat_regime_rtds` (n=39, 100% WR, +$234) carried the number. But all 39 trades fired inside a single ~18h window on 2026-03-14 — walk-forward verdict is UNDERPOWERED (see Walk-forward section). Treat as narrow-activation artifact, not edge. |
| **70-85** | 75 | 0.880 | 0.787 | 0.775 | −$313 | **In-sample AMBIGUOUS, walk-forward REJECTED.** Band-level PnL is negative; the healthy-looking subset `binance_momentum` (n=41, WR 97.6%, +$78) fired across 3 days only (2026-03-21→03-24) and failed walk-forward (LOOKAHEAD — second half Wilson 0.773 vs breakeven 0.775). Null-source rows (n=27, −$399) remain the sink. Net: AVOID the band. |
| **85-93** | 69 | 0.638 | 0.520 | 0.890 | −$56 | **FAILED.** Wilson LB 0.520 < breakeven 0.890 (gap −37 pp). Dominated by null-source (n=53, WR 54.7%, −$63). Healthy subsets are too small to verdict (binance_momentum n=5, sharp_move n=8). |
| **93-97** | 217 | 0.313 | 0.255 | 0.950 | −$1,118 | **NO EDGE.** Wilson LB 0.255 vs 0.950 breakeven → gap −70 pp. Earlier falsification used the phantom_resolved-only subset (n=76, WR 42.1%, -$170). Full resolved sample makes the verdict stronger, not weaker. Book spread ≤ 0.010 on 75/76 sampled rows (spread filter did no work); adverse median 0 bps. [btc-5m-snipe-strategy: Falsification 2026-04-22] |
| **97+** (chalk) | 85 | 0.918 | 0.840 | 0.990 | −$185 | **FAILED** (previously undecidable at n=9; n=85 now clears P5 threshold). At midpoint 0.99, breakeven is 0.99; raw WR 91.8% is below. Avg_win +$0.70, avg_loss −$34.16 — **one loss erases ~49 wins.** Four null-source market_resolved losers at entries 0.97-0.98 carry the −$185 headline. Same structural problem as 93-97: payoff asymmetry + no calibrated p̂_win estimator. |

**Operator rule of thumb (updated 2026-04-22 post-walk-forward):** no
entry band, and no (band, signal_source) subset tested so far, has
produced walk-forward-robust edge on this dataset. When someone asks
"should we try entering at 0.95+ again?" / "can we take cheap-side
signals?" / "didn't flat_regime_rtds look great at 55-70?" the answer
is NO unless BOTH (a) a p̂_win estimator calibrated against a signal
stronger than entry_price has been built AND (b) the proposed strategy
has fired across at least two disjoint regime episodes. Re-raising
without both is re-investigating a decided matter.

### Walk-forward (2026-04-22)

Both in-sample survivors were temporally split at median `entry_time`
and re-scored per half.

| Subset | Total n | First half | Second half | Verdict |
|---|---:|---|---|---|
| 55-70 + `flat_regime_rtds` | 39 | n=19, 19W/0L, WR 1.000, Wilson 0.832, +$81.94 | n=20, 20W/0L, WR 1.000, Wilson 0.839, +$151.59 | **UNDERPOWERED** (n/half=19<20; full span is ONE ~18h window on 2026-03-14) |
| 70-85 + `binance_momentum` | 41 | n=20, 20W/0L, WR 1.000, Wilson 0.839, +$51.74 | n=21, 20W/1L, WR 0.952, Wilson 0.773, +$26.84 | **LOOKAHEAD** (Wilson 0.773 < breakeven 0.775, gap −0.002; full span is ~3 days 2026-03-21→03-24) |

**Meta-finding — narrow-activation artifact.** Both strategies fire
rarely, concentrate their activation inside a single regime episode,
and the "walk-forward" split ends up comparing morning vs afternoon of
the same window. Neither produced trades across disjoint regime
episodes. Wilson LB on in-sample samples cannot catch this because the
test is within-sample by construction. Net: **no survivors.** The
in-sample findings were regime-episode artifacts, not robust edge.

**Rule consequence (ratified as P9 on 2026-04-22):** for episodic
strategies, P5 ("n≥30 + Wilson LB") is necessary but not sufficient.
P9 adds: **≥2 disjoint activation windows before trust.** See
`PRINCIPLES.md` §P9 for the full rule and the cheap-check query.

**What the 93-97 falsification specifically ruled out:**
- Raw entry-price-based filtering (bid sweep in the band).
- EV_net gating with a fixed p̂_win estimate — at no p̂_win in {0.94..1.00}
  does the formula produce positive expectancy on this sample.
- Rule 2 (spread filter at < 0.010) contributing useful rejection —
  99% of the sample was already inside the spread threshold.

**What it did NOT rule out:**
- A calibrated p̂_win estimator sourced from a separate signal (e.g.,
  Binance 60s trend magnitude regressed against outcome). That
  instrumentation does not exist today — see btc-5m-snipe-strategy
  "new blocker for activation."
- Note: the two in-sample strategy survivors identified on the first
  pass (`flat_regime_rtds@55-70` and `binance_momentum@70-85`) BOTH
  failed walk-forward on 2026-04-22 — see the "Walk-forward" table
  above. They are narrow-activation artifacts, not edge, and were
  correctly rejected by the time-split. Do not resurrect them without
  fresh data from a separate regime episode.

### Data quality caveats

1. **`is_dry_run` is backfilled.** Per MEMORY.md, all 1546 historical
   trades have `is_dry_run=1` regardless of whether they were live at
   the time. The verdict script's live-vs-dry split returns 0 live
   rows in every band — that split is meaningless until the backfill
   is corrected or a fresh live sample is collected.
2. **Phantom-resolved rows are simulated-fill.** The 00-55 $21k
   artifact is a phantom_resolved row where a dry-run fill at
   entry=0.015 was credited at face value on resolution. Any phantom
   PnL extreme outlier (>$100) should be treated as a simulator bug
   until reconciled against on-chain events.
3. **`signal_source IS NULL` rows pre-date the attribution schema.**
   The band verdicts on 70-85 / 85-93 / 97+ where null-source rows
   dominate losses are consistent with "early trades from before
   signal_source was wired in were noisier overall." Do not
   over-interpret subsets that are only-null.

### Reproduce

```bash
# One-shot verdict across all bands (uses phantom_resolved + market_resolved)
scp tools/verdict_all_bands.py root@82.24.19.114:/tmp/
ssh root@82.24.19.114 '/opt/lagbot/venv/bin/python3 /tmp/verdict_all_bands.py'

# Signal_source breakdown per band (ambiguity drill-down)
scp tools/drill_bands.py root@82.24.19.114:/tmp/
ssh root@82.24.19.114 '/opt/lagbot/venv/bin/python3 /tmp/drill_bands.py'

# Walk-forward validation for in-sample (band, signal_source) survivors
scp tools/walk_forward_survivors.py root@82.24.19.114:/tmp/
ssh root@82.24.19.114 '/opt/lagbot/venv/bin/python3 /tmp/walk_forward_survivors.py'
```

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
- "is deep favorite profitable" / "can we enter at 0.95"
- "what did we learn about the friend's hypothesis"
- "efficiency verdict" / "band-level edge"
