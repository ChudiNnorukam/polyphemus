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
last_verified: '2026-04-26T06:26:09Z'
confidence: inferred
---

## What

The structured research design for answering one specific question with the SII-WANGZJ academic Polymarket dataset (107GB / 1.1B records on HuggingFace): **did sharp_move ever have edge, or was the strategy a regime artifact that never worked at fill-level?**

This is the question shadow mode fundamentally cannot answer. Shadow uses Binance midpoint as a stand-in for fill price; it cannot measure real Polymarket fill latency, queue position, or fee impact. The 2026-04-17 MTC verdict that flagged sharp_move as "NO-GO with alpha decay" was computed on signals where (a) the gate's source filter was stale (fixed in commits 4629312/8766e7e/1f6552c/f917584) AND (b) shadow data with no real fill behavior. The verdict's diagnostic value was near-zero. Real historical fills from the academic dataset close that gap.

This node describes the BACKTEST DESIGN. It does NOT describe data fetching, code implementation, or execution. Those are downstream once this design is operator-ratified.

## Pre-committed research question

> For all Polymarket BTC + ETH + SOL + XRP 5m up/down markets between 2024-01-01 and 2025-12-31 where a sharp_move signal would have fired (per the documented entry rules), what is the realized PnL distribution at real fills, accounting for fees and Markov-gate cooldowns? Is the Wilson lower bound on win rate ≥ 0.50, the mean adverse_fill_bps < 30, AND P9 (≥2 disjoint regime windows) satisfied?

The question is binary at outcome level: edge or no edge. The numeric thresholds match the proposed tiny-live experiment's kill criteria so the two analyses produce comparable verdicts.

## Filter shape (data side)

Applied against `trades.parquet` (32GB, 293M rows) joined to `markets.parquet` (68MB, 268K rows):

- `markets.title` matches regex `(BTC|Bitcoin|ETH|Ethereum|SOL|Solana|XRP) Up or Down — \d{1,2}:\d{2} (AM|PM) ET` OR similar 5m crypto-market naming convention (verify against actual `markets.title` distribution before committing)
- `markets.resolution_at - markets.created_at` ≈ 300 seconds (5m windows)
- Date range: 2024-01-01 → 2025-12-31 inclusive
- Trade type: `taker_filled` rows only (sharp_move is a taker strategy)
- Trade price: ≤ 0.95 (sharp_move's entry-price ceiling per `SHARP_MOVE_MAX_ENTRY_PRICE=0.95`)
- Time-to-resolution at trade time: 60-280 seconds (sharp_move only fires when ≥60s remaining)
- The sharp_move signal would have fired: this requires reconstructing Binance momentum at trade time from a separate Binance kline source. NOT in the SII-WANGZJ dataset; needs supplementary data fetch from Binance's free klines API. **Dependency on external supplementary data.**

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
- Fetch script: `polyphemus/tools/fetch_sii_polymarket_data.py`
- Backtester: `polyphemus/tools/sharp_move_alpha_decay_backtest.py`
- Output report: `dario_output/sharp-move-alpha-decay-<YYYY-MM-DD>.md`
- Post-exec node: append result-row to `external-datasets` history + log self-ledger annotate.

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
