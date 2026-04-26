---
id: sharp-move-alpha-decay-backtest
name: Sharp Move Alpha Decay Backtest (research design)
domain: trading-strategies
aliases:
- alpha-decay-backtest
- sii-polymarket-backtest
- sharp-move-historical-backtest
- did-sharp-move-ever-have-edge
- sii-wangzj-backtest
- polymarket-academic-backtest
code_refs:
- path: docs/external_datasets.md
  lines: 1-28
  sha256: 3587888c1ac003a4
- path: binance_momentum.py
  lines: 463-504
  sha256: b0b88bc81dec5130
- path: binance_momentum.py
  lines: 723-790
  sha256: 5aef07fc09e11e5f
related:
- sharp-move
- alpha-decay
- adverse-selection
- mtc-gate
- entry-band
- external-datasets
- shadow-mode
parent_concepts:
- sharp-move
child_concepts: []
last_verified: '2026-04-26T06:37:41Z'
confidence: inferred
---

## What

The structured research design for answering one specific question with the SII-WANGZJ academic Polymarket dataset (107GB / 1.1B records on HuggingFace): **did sharp_move ever have edge, or was the strategy a regime artifact that never worked at fill-level?**

This is the question shadow mode fundamentally cannot answer. Shadow uses Binance midpoint as a stand-in for fill price; it cannot measure real Polymarket fill latency, queue position, or fee impact. The 2026-04-17 MTC verdict that flagged sharp_move as "NO-GO with alpha decay" was computed on signals where (a) the gate's source filter was stale (fixed in commits 4629312/8766e7e/1f6552c/f917584) AND (b) shadow data with no real fill behavior. The verdict's diagnostic value was near-zero. Real historical fills from the academic dataset close that gap.

This node describes the BACKTEST DESIGN. It does NOT describe data fetching, code implementation, or execution. Those are downstream once this design is operator-ratified.

## Pre-committed research question

> For all Polymarket BTC + ETH + SOL + XRP 5m up/down markets between 2024-01-01 and 2025-12-31 where a sharp_move signal would have fired (per the documented entry rules), what is the realized PnL distribution at real fills, accounting for fees and Markov-gate cooldowns? Is the Wilson lower bound on win rate ≥ 0.50, the mean adverse_fill_bps < 30, AND P9 (≥2 disjoint regime windows) satisfied?

The question is binary at outcome level: edge or no edge. The numeric thresholds match the proposed tiny-live experiment's kill criteria so the two analyses produce comparable verdicts.

## Filter shape (data side — grounded in actual dataset, 2026-04-26 inspection)

Applied against `trades.parquet` (32GB, 293M rows) joined to `markets.parquet` (121MB, **734,790 markets** — README's 268K count is stale).

**Markets-side filter** (one-time join key set):
- `slug` matches regex `^(btc|eth|sol|xrp)-updown-5m-\d+$` — verified to match **95,797 markets** in the dataset, identical to polyphemus's runtime slug naming convention.
- Equivalent question-text filter: `(Bitcoin|Ethereum|Solana|XRP) Up or Down - .+ ET` matches 197K rows broader than the slug filter (includes 15m windows and other variants).
- `end_date - created_at` ≈ 300 seconds (5m window structural check; Polymarket emits these on a fixed cadence, structural confirmation only).

**Trades-side filter** (applied against the joined view):
- Trade price (`outcome_prices` field, parsed): ≤ 0.95 (sharp_move's entry ceiling per `SHARP_MOVE_MAX_ENTRY_PRICE=0.95`)
- Time-to-resolution at trade time: 60-280s (computed from market `end_date` minus trade timestamp; sharp_move fires when ≥60s remaining and abandons inside last 20s)
- Trade direction: aligned to Up if `signal.outcome == "Up"`, Down otherwise (use Polymarket's `answer1`/`answer2` mapping from markets table)

**Signal-eligibility filter** (separate Binance fetch required):
The sharp_move signal would have fired ⇒ requires reconstructing Binance momentum at trade time from supplementary data. NOT in the SII-WANGZJ dataset.
- Supplementary fetch: Binance free klines API (1m resolution, BTC/ETH/SOL/XRP, 2024-01-01 → 2025-12-31). Estimated ~1-2GB raw, ~300MB parquet-compressed.
- Eligibility = `|momentum_60s_pct| ≥ 0.30%` AND price-direction alignment AND ≥60s-to-resolution AND adverse-pre-check passes simulated.

## Signal reconstruction (synthesizing the filter)

For each candidate Polymarket trade in the filter:
1. Pull Binance kline at `trade_time - 60s` and `trade_time` for the matching asset
2. Compute 60s momentum %: `(close[t] / close[t-60s] - 1) * 100`
3. If `|momentum_pct| ≥ 0.30%` AND direction matches (BTC up + market 'up' OR BTC down + market 'down'), this is a sharp_move-eligible trade
4. Apply Markov gate simulation: skip if 3 prior simulated trades on this asset in last hour all lost
5. Apply 15s adverse pre-check simulation: skip if Binance moved >0.03% adversely between `t-15s` and `t`
6. The remaining set is the sharp_move historical population

**This is non-trivial.** The signal reconstruction requires: Binance kline data (~1-2GB compressed for BTC/ETH/SOL/XRP at 1m resolution, 2 years), and a kline-to-trade-time alignment. Worth scoping the Binance fetch separately before committing to the full backtest.

## Success criteria (kill / promote / inconclusive)

After filter + signal reconstruction yields N eligible historical trades:

| Verdict | Condition |
|---|---|
| **KILL sharp_move on emmanuel** | N ≥ 30, P9 satisfied, AND (Wilson LB(WR) < 0.50 OR mean adverse_fill_bps > 30) |
| **PROMOTE sharp_move (skip tiny-live $60 step)** | N ≥ 30, P9 satisfied, Wilson LB(WR) ≥ 0.55, AND mean adverse_fill_bps < 15 |
| **CONFIRM via tiny-live** | N ≥ 30, P9 satisfied, but verdict between 0.50-0.55 Wilson LB OR adverse 15-30 bps. Backtest is suggestive but not decisive — still want $60 of real-fill confirmation. |
| **INCONCLUSIVE / re-design** | N < 30 OR P9 fails (single regime episode). Filter is too narrow OR strategy fired too rarely historically; widen ceiling temporarily and re-run. |

## What this design does NOT do

- Does not fetch the dataset (107GB; disk gate is separate). 
- Does not implement the backtester code.
- Does not promote anything to live.
- Does not modify any .env trading parameter.
- Does not bypass LIFECYCLE.md Phase 1; this node IS the Phase 1 research design.

## Where (target locations for downstream artifacts; not yet authored)

When this design is ratified and execution begins:
- Fetch script: `polyphemus/tools/fetch_sii_polymarket_subset.py`
- Backtester: `polyphemus/tools/sharp_move_alpha_decay_backtest.py`
- Binance kline fetcher: `polyphemus/tools/fetch_binance_klines_2024_2025.py`
- Output report: `dario_output/sharp-move-alpha-decay-<YYYY-MM-DD>.md`
- Post-exec node: append result-row to `external-datasets` history + log self-ledger annotate.

## Phase 2 — PLAN (per LIFECYCLE.md)

Authored 2026-04-26 against verified HF dataset schemas (footer reads via `HfFileSystem`, no full downloads). All field names below are confirmed from real schema.

### File list

NEW:
- `polyphemus/tools/fetch_sii_polymarket_subset.py` — pyarrow + HfFileSystem streaming. Reads `trades.parquet` row-group-by-row-group with predicate-pushdown filter `market_id in target_set` (95,797 ids). Writes filtered subset to `polyphemus/data/sii_polymarket_subset/trades_crypto_5m.parquet`. Estimated output: 1-3GB (95K of 734K markets ≈ 13%; ~70-100M of 568M trades likely).
- `polyphemus/tools/fetch_binance_klines_2024_2025.py` — Binance free klines API client. Pulls 1m klines for BTCUSDT / ETHUSDT / SOLUSDT / XRPUSDT, 2024-01-01 → 2025-12-31. Estimated 4 × ~525K minutes × 7 fields = ~300MB parquet.
- `polyphemus/tools/sharp_move_alpha_decay_backtest.py` — joins crypto 5m trades to klines, simulates sharp_move signal eligibility per the recipe in this node's "Signal reconstruction" section, computes Wilson LB(WR) + mean adverse_fill_bps + P9 disjoint-window check, emits Markdown report.
- `polyphemus/data/sii_polymarket_subset/` (gitignored) — local cache of filtered parquet
- `polyphemus/data/binance_klines/` (gitignored) — local cache of kline parquet
- `dario_output/sharp-move-alpha-decay-<YYYY-MM-DD>.md` — final report

MODIFIED:
- `polyphemus/.gitignore` — add `data/sii_polymarket_subset/` and `data/binance_klines/`
- `polyphemus/docs/codex/nodes/sharp-move-alpha-decay-backtest.md` (this node) — append post-exec verdict row
- `polyphemus/docs/external_datasets.md` — append history entry naming the integration date + verdict

### Config changes

**NONE.** No `.env` field touched. No systemd drop-in changed. No live trading parameter modified. The backtest is purely offline analysis.

### Blast radius

`grep -rn` on the new function names + modified files:
- New tools live under `polyphemus/tools/` — same dir as `post_deploy_verify.py`, `btc5m_ensemble_go_live_gate.py`, `quant_candidate_refresh.py`, etc. Convention established.
- No imports of these new tools by any production code path (zero blast radius until backtester is invoked manually).
- `.gitignore` change is additive; only affects new data dirs.
- The post-exec node-body update is append-only.
- LIVE_TRADING_PATH_GLOBS untouched.

### Side effects

- **Network egress**: ~2-5GB from HuggingFace (predicate-pushdown filter download of trades.parquet row groups) + ~300MB from Binance klines API. Both free.
- **Disk**: ~3-5GB local under `polyphemus/data/`. Fits in 3.3GB free IFF disk cleanup happens; otherwise blocks.
- **Runtime**: ~20-40 min for fetch (network-bound); ~10-30 min for backtest (compute-bound, single-machine pyarrow + numpy).
- **No live system touched.** emmanuel + polyphemus continue uninterrupted in DRY_RUN.

### Rollback

- `git revert` the commits that introduce the 3 new tools — fully reversible
- `rm -rf polyphemus/data/sii_polymarket_subset/ polyphemus/data/binance_klines/` — recovers all disk
- Worst case: the verdict suggests a tiny-live experiment we then run live — but THAT is gated by an additional Phase-2-PLAN-of-its-own (the existing `sharp-move-tiny-live-experiment` node) and operator ratification

### Phase 2 Gate

Per LIFECYCLE.md "Plan Approved" gate, evidence required:
- ✅ File list complete (no "and possibly others")
- ✅ Config changes listed (NONE, with explicit "no .env touched")
- ✅ Blast radius mapped via convention check (no production imports of new tools)
- ⏳ User has approved the plan — pending ratification of this node

When Phase 2 is ratified, execution begins (Phase 3 IMPLEMENT) starting with the fetch scripts. Phase 4 TESTING is the verdict computation + Markdown report generation. Phase 5 RATIFICATION is the operator's kill / promote / tiny-live decision based on the verdict.

## When-to-touch

- "did sharp_move ever have edge"
- "alpha decay backtest"
- "use the polymarket dataset"
- "sii-wangzj historical backtest"
- "skip the tiny-live experiment"
- "is the MTC NO-GO verdict real"
- "research-question for sharp_move"

## Status

**[Target]** as of 2026-04-26. Pure research design — no data fetched, no backtest run. Becomes `[Inferred]` when the backtester runs and produces a verdict; flips to `[Verified]` when the verdict is operator-ratified into a kill/promote/tiny-live decision.

## Source

Authored 2026-04-26 in response to operator's "do b and c" pick after reading my A/B/C path proposal. Overrides the 2026-04-20 deferral on `external-datasets` because:

1. The deferral cited G1 (revenue before infrastructure) requiring "a tomorrow's 04:37 UTC friend-hypothesis verdict runs on our own data" precondition, which has been blocked by the source-filter rename bug we just fixed.
2. The codebase audit completed today (commits 4629312, 8766e7e, 1f6552c, f917584, dd2dd66) is itself the G1-protective work — protecting the operator's existing position by ensuring the gate machinery actually works. The dataset is now consultable in service of the same revenue protection.

The override is logged as a self-ledger ratify event referencing both decision IDs.
