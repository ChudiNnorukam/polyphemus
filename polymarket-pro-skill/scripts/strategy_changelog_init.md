# Strategy Changelog

All config changes with justification and measured impact.

| Date | Instance | Change | Reason | Backtest | Measured Impact |
|------|----------|--------|--------|----------|-----------------|
| 2026-03-01 | emmanuel | ENABLE_SHARP_MOVE=false, ASSET_MIN_ENTRY_ETH=0 | Bug #49: per-asset range backdoor leaked momentum trades. -$106.06 loss. | N/A (emergency fix) | Pending measurement |
| 2026-03-01 | both | SNIPE_MAX_SECS_REMAINING=10 | 0-10s only net-positive window (90.5% WR, +$1.29/trade). 30-40s had all big losses. | Historical: 75 trades | Pending measurement |
| 2026-03-01 | both | SNIPE_MAX_PER_EPOCH=3 | Volume recovery after tightening window. 3 assets can fire per epoch. | N/A | Pending measurement |
| 2026-02-28 | both | MIN_ENTRY_PRICE=0.99, MAX_ENTRY_PRICE=0.95 | Kill momentum. Snipe-only mode after triple loss. | N/A (safety) | Pending measurement |
