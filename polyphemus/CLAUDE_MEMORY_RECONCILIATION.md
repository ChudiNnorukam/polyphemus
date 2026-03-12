# Claude Memory Reconciliation

## Authoritative Current Truth

- The primary VPS is `82.24.19.114` on QuantVPS. Current project instructions explicitly forbid using the older backup and dead hosts.
- The live trading instance is `lagbot@emmanuel`. `polyphemus` is currently stopped.
- Any strategy change, new feature, or live trading config change touching `.env` must follow the lifecycle gates in `LIFECYCLE.md`.
- The current codebase already consumes OpenClaw context through `market_context_path=/opt/openclaw/data/lagbot_context.json` and injects that data at signal time.
- Current repo instructions override stale OMC notes when they conflict.

## Historical But Stale

- Older OMC notes refer to `159.223.236.50` and `142.93.143.178` as active VPS targets. Those references are historical only and must not drive new deployment or audit work.
- Older notes also describe pre-QuantVPS service layouts and deployment flows that no longer match the current `lagbot@emmanuel` setup.
- Historical sizing, threshold, and uptime notes remain useful as context, but they are not authoritative unless corroborated by current code, current DB evidence, or live VPS state.

## Confirmed Research Signals

- BTC 5m is the only currently supportable primary optimization scope. Recent DARIO analysis confirms BTC 5m is the only asset-window cell with positive PnL and sufficient sample to survive minimum evidence gating.
- ETH 5m remains a secondary watchlist candidate, not a primary optimization target. It has directional support in prior research but does not yet have enough current live evidence to drive broader changes.
- 15m markets are not in scope for improvement. Multiple prior analyses point to structurally negative results driven by decay, time exits, and stop-loss bleed.
- SOL and XRP are not in scope for improvement. Prior research and local summaries both point to weak or negative edge for this strategy family.
- Existing overhang is real: the repo already logs signals, epoch coverage, trade outcomes, regime features, and OpenClaw context, but that information is not yet unified into a decision-grade BTC 5m retrieval workflow.

## Open Verification Gaps

- Whether OpenFang is currently present on the live VPS, and if so what role it plays in the production stack.
- The current `emmanuel` `.env` values and whether they still match the repo-era assumptions in historical notes.
- The current `signals.db` and `performance.db` coverage on the live VPS, including how much BTC 5m data exists on the active instance.
- The freshness and producer of `lagbot_context.json` on the live VPS, including whether it is being updated by the expected OpenClaw process.

## Defaults

- No historical note is treated as authoritative unless it is corroborated by current repo instructions, current code, current DB evidence, or live VPS state.
- The first implementation pass remains non-live. Build shared truth, retrieval, replay, and reporting surfaces before touching live trading parameters.
