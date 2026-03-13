# Live Trading Policy

This policy is the anti-hallucination layer for any agent using the Polyphemus KB.

## Hard rules

- No live trading recommendation without current gate evidence.
- Replay-only results are never enough for live promotion.
- Negative live P&L overrides narrative confidence.
- If sources disagree, prefer current internal runtime evidence plus primary docs over commentary.
- Audit mismatch or unknown audit state blocks any live recommendation.
- Missing shared aligned `config_era` blocks strategy promotion claims.

## Safe defaults

- Default posture is `shadow first`.
- `polyphemus` is a research sensor until the live gate passes.
- The first allowed promotion shape stays narrow:
  - `emmanuel`
  - `BTC`
  - `5m`
  - `binance_momentum`
  - only when `shadow_ensemble_selected=1`

## Forbidden claims

- "This will be profitable" without current live evidence.
- "Small bet size makes it safe" when expectancy is unproven.
- "The bot is good" when the pipeline is starved or the gate is blocked.
- "The strategy is validated" when the best evidence is still replay-only.
