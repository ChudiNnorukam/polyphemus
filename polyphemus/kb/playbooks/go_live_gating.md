# Go Live Gating Playbook

Use this when deciding whether any BTC 5m strategy slice can move from shadow to live.

The order is fixed:
1. Check the latest go-live gate verdict.
2. Check the shadow-window checklist.
3. Check the `emmanuel` audit mismatch state.
4. Check recent live P&L and recent execution failure telemetry.
5. Only then review external theory that supports the next change.

Rules:
- A `NO-GO` gate ends the decision.
- Replay-only evidence never overrides negative live P&L.
- Missing shared `config_era` means the comparison window is contaminated.
- Audit mismatch is a hard blocker for any promotion recommendation.

Do not:
- recommend live trading just because a backtest looks strong
- ignore unresolved reconciliation issues
- claim confidence without citing both current repo evidence and primary sources
