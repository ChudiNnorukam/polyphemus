---
id: shadow-mode
name: Shadow Mode
domain: trading-strategies
aliases:
- shadow
- shadow_assets
- sharp_move_shadow
- window_delta_shadow
- tugao9_shadow
- igoc_shadow_only
- flat_regime_rtds_shadow
- snipe_15m_dry_run
- signal_score_mode shadow
- log-only mode
- shadow-only
- shadow testing
code_refs:
- path: config.py
  lines: 40-43
  sha256: b5a1de6e4139db30
- path: config.py
  lines: 199-205
  sha256: 55bc4bee05e796b4
- path: signal_bot.py
  lines: 1204-1218
  sha256: 2a1cdfe5ef22a2d6
related:
- fill-models
- trade-tracer
- adverse-selection
- deploy-lifecycle
parent_concepts: []
child_concepts: []
last_verified: '2026-04-20T00:02:18Z'
confidence: inferred
---

## What

Shadow mode is how every new strategy enters production before it's
trusted with money. The code runs, the signal is fully computed and
logged, but the execution path terminates before `record_entry` —
no phantom fill, no live order, no DB row in `trades`. Outcome gets
written to `signals.outcome = "shadow"` so the strategy's behavior
can be audited without polluting win-rate math.

Two orthogonal axes activate shadow:

1. **Asset-level**: `shadow_assets="ETH,SOL,XRP"` — any signal on
   these assets is shadow, regardless of which strategy fired. BTC
   is never in this list (that's the production asset).
2. **Strategy-level**: each strategy defines its own `_shadow: bool`
   (e.g. `sharp_move_shadow`, `window_delta_shadow`, `tugao9_shadow`,
   `signal_score_mode="shadow"`). Default is `True` — new strategies
   land shadow-on, promote to live only after validation.

Either gate alone blocks execution. Both must be cleared for a trade
(live or phantom) to reach the trades table. This is why the Apr 19
sharp_move investigation found 22 detections and zero fills: all 22
were on ETH/SOL/XRP (shadow-assets gate) AND the code default
`sharp_move_shadow=True` is still set.

Shadow is orthogonal to `DRY_RUN`. DRY_RUN converts real orders into
phantom fills; shadow prevents even the phantom. A strategy can be
shadow in a DRY_RUN instance — the signal still never produces a row.

## Where

- Asset-level shadow list at
  [config.py:40-43](config.py#L40-L43) — `shadow_assets` as
  comma-separated string; getter at `config.get_shadow_assets()`.
- Canonical per-strategy shadow pattern at
  [config.py:199-205](config.py#L199-L205) — `enable_sharp_move`
  gates whether the strategy runs at all; `sharp_move_shadow`
  gates whether its signals execute. The pair is copied across
  ~15 strategies with the same shape.
- Execution gate at
  [signal_bot.py:1204-1218](signal_bot.py#L1204-L1218) — the
  `if signal.get("shadow"):` branch logs `[SHADOW]`, writes
  `outcome="shadow"` to the signals table, and returns before
  `record_entry`. Every shadow flag eventually routes through this
  early-return.

## When-to-touch

Route here when the user says:

- "shadow mode" / "shadow assets" / "shadow strategy"
- "why is this strategy logging but not trading"
- "why are there signals but no trades in the DB"
- "ETH/SOL/XRP never fires" / "only BTC places trades"
- "promote X from shadow to live" / "un-shadow"
- "log-only mode" / "strategy hasn't graduated"
- anything involving `_shadow`, `shadow_assets`, `signal_score_mode`,
  `outcome="shadow"`, or the `[SHADOW]` log prefix
