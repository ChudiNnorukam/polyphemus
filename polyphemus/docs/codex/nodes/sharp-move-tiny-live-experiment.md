---
id: sharp-move-tiny-live-experiment
name: Sharp Move Tiny-Live Experiment (2026-04-26)
domain: trading-strategies
aliases:
- tiny-live
- sharp-move-promotion-experiment
- 60-dollar-information-budget
- shadow-to-tiny-live
code_refs:
- path: binance_momentum.py
  lines: 463-504
  sha256: b0b88bc81dec5130
- path: signal_bot.py
  lines: 566-622
  sha256: f0125091fc0329c2
- path: circuit_breaker.py
  lines: 1-179
  sha256: 4655b31ed0c6ed4d
related:
- sharp-move
- shadow-mode
- adverse-selection
- mtc-gate
- markov-gate
- circuit-breaker
- kelly-haircut
- alpha-decay
parent_concepts:
- sharp-move
child_concepts: []
last_verified: '2026-04-26T05:32:01Z'
confidence: inferred
---

## What

The structured experiment to acquire **ground-truth fill data** on emmanuel's sharp_move strategy by flipping DRY_RUN=false with a tight per-trade cap. Shadow mode mocks fills using Binance prices and cannot measure the things that decide sharp_move's profitability: real Polymarket fill latency, queue position, and adverse selection (the gap between Binance's price and Polymarket's actual fill). 30 days of shadow data has produced zero ground-truth on these axes. This experiment buys that data for a hard-capped budget.

The experiment respects every named principle in `docs/codex/PRINCIPLES.md`:
- **P3** (robust under estimation error): Kelly haircut and Markov gate stay enabled
- **P5** (n≥30 + Wilson LB): kill/promote criteria use n=30 minimum
- **P9** (≥2 disjoint activation windows): explicit precondition on promotion criterion
- **P4** (infra failures ≠ strategy failures): infra-failure trades excluded from the count
- **Chudi-frame "ask before acting"**: the .env flag flip requires explicit operator ratification each time

The experiment does NOT bypass the existing safety harness; it applies it to real fills instead of mocked fills.

## Hypothesis (pre-committed)

**Sharp_move on emmanuel is producing valid directional signals** (515 in 32 days, all guard_passed, all `outcome='shadow'`), but shadow mode cannot tell us whether the entry-price ceiling (`SHARP_MOVE_MAX_ENTRY_PRICE=0.95`), adverse-precheck threshold (`ADVERSE_PRECHECK_THRESHOLD=0.0003`), and Markov gate (`MARKOV_GATE_MAX_LOSSES=3`) compose to produce positive EV in real fills.

**Falsifiable predictions:**
1. After 30 real-fill trades across ≥2 disjoint regime windows, Wilson lower bound on win rate ≥ 0.50
2. Mean `adverse_fill_bps` across those 30 trades < 30 bps
3. No infra-failure trades (per P4) in the count
4. **Execution rate** (`executed / (executed + execution_failed)` for BTC sharp_move signals reaching the order layer, measured via `tools/execution_rate_report.py`) reported alongside WR at n=30 close. Below 0.50 ⇒ verdict downgrades from PROMOTE-eligible to EXTEND-only because selection bias on filled trades dominates. Added 2026-04-27 after day-1 triage measured 0.35; this prediction was retroactively pre-committed before n=8 to close the selection-bias hole flagged by the test-to-live-haircut node.

If predictions 1-3 fail → **kill** sharp_move on emmanuel (set `ENABLE_SHARP_MOVE=false`).
If 1-3 hold AND mean WR ≥ 0.55 AND mean adverse_fill_bps < 15 AND execution_rate ≥ 0.50 → **promote** to `MAX_TRADE_AMOUNT=10`.
If 1-3 hold but execution_rate < 0.50 OR the stronger promotion criteria don't → **continue at $4/trade for 30 more trades** before deciding.

## Budget (hard-capped)

| Limit | Value | Mechanism |
|---|---|---|
| Per-trade max | $4 | `MAX_TRADE_AMOUNT=4` in systemd drop-in |
| Per-day loss cap | $20 | `MAX_DAILY_LOSS=20` (tightened from current $50) |
| Consecutive loss kill | 3 | `MARKOV_GATE_MAX_LOSSES=3` (already set; unchanged) |
| Cumulative experiment cap | $60 | **Manual monitor** + kill switch file (`/opt/lagbot/instances/emmanuel/KILL_SWITCH`) |
| Time cap | 14 days | If P5 (n=30) not reached in 14 days, the strategy is too rare to live; kill |
| Trade count target | 30 | After 30 with P9 satisfied: evaluate kill/promote criteria |

**Worst-case math:** 30 trades × $4 = $120 invested; if WR=0%, max loss = $120. The $60 cumulative cap kicks in BEFORE that worst case, halting at 50% capital loss. Realistic loss expectation at WR=50% is ~$30.

**The $60 is the cost of buying ground-truth fill data that shadow fundamentally cannot produce.** Information has a price.

## Where (operator path to ratify and deploy)

The exact change needed in emmanuel's systemd drop-in (`/etc/systemd/system/lagbot@emmanuel.service.d/dry_run.conf`):

```diff
-ExecStart=... env DRY_RUN=true ENABLE_ACCUMULATOR=false ... MAX_TRADE_AMOUNT=5 MIN_BET=3 MARKOV_GATE_MAX_LOSSES=3 ...
+ExecStart=... env DRY_RUN=false ENABLE_ACCUMULATOR=false ... MAX_TRADE_AMOUNT=4 MIN_BET=3 MARKOV_GATE_MAX_LOSSES=3 MAX_DAILY_LOSS=20 ...
```

Two changes only:
1. `DRY_RUN=true` → `DRY_RUN=false`
2. `MAX_TRADE_AMOUNT=5` → `MAX_TRADE_AMOUNT=4`
3. Add `MAX_DAILY_LOSS=20` if not already set in the drop-in (currently in .env at 50)

Deploy mechanic per project CLAUDE.md:
1. Pre-flight: confirm 0 open positions on emmanuel (`SELECT slug FROM trades WHERE exit_time IS NULL`)
2. Reset circuit_breaker.json to clean state for the experiment
3. Backup current drop-in to `.bak.<ts>`
4. Edit drop-in
5. `systemctl daemon-reload`
6. `systemctl restart lagbot@emmanuel`
7. Post-deploy: `journalctl -u lagbot@emmanuel --since "60 seconds ago" | grep -iE "error|Traceback"`
8. Verify with `systemctl show -p Environment lagbot@emmanuel | grep DRY_RUN` shows `DRY_RUN=false`

## When-to-touch

- "promote sharp_move to live"
- "tiny-live experiment"
- "$60 information budget"
- "DRY_RUN=false on emmanuel"
- "kill or promote sharp_move"
- "adverse fill measurement"
- "shadow mode can't tell us"

## Status

**[Target]** as of 2026-04-26. The .env flag flip has NOT been performed; this node describes the experiment design pending operator ratification. Once ratified and deployed, status becomes `[Inferred]` until the n=30 trade evaluation is complete. After evaluation, status flips to `[Verified]` (kill or promote, depending on outcome).

## Source

Authored 2026-04-26 in response to operator challenge ("why is it so hard to get something to work well enough that it can go from shadow to live?"). The honest answer surfaced: shadow mode cannot validate adverse fill or queue position for taker strategies; we'd been waiting for data that the methodology can't produce. Experiment design follows librarian session ledger event `dc-20260426T043139Z-emmanuel-watchdog-fix` (the watchdog fix that preceded this discussion).
