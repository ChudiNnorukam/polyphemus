# Bulenox Bot Progress

## Current State (Mar 25 2026)

### Bot Status
- **Running on VPS** (82.24.19.114, systemd service `bulenox`)
- **Config**: FADE 0.5% / TP=50 / SL=15 / 15min hold (walk-forward validated on fresh 95-day data)
- **Trades**: 7 total (2W/5L), real net P&L: ~-$73
- **Dry run**: DRY_RUN=true

### What Was Built This Session (Mar 19-25)
- Bulenox bot: 18 Python files, Rithmic WebSocket, Coinbase feed, SQLite persistence
- Order lifecycle: PASSED on Rithmic Test
- VPS deploy: isolated from Polymarket at /opt/bulenox/
- Infrastructure: preflight test, ticker watchdog, heartbeat log, health check cron, Friday force-close
- Tick-level TP/SL (~81ms reaction time)
- Adaptive threshold, trend filter, signal persistence to SQLite

### Skills Created/Updated
- `/domain-entry-audit` v2.1.0 (cost verification, regime check, trial integrity)
- `/proof-of-edge` v1.0.0 (paper trading trial framework)
- `/bulenox-quant` v1.0.0 (domain knowledge base)
- `/stitch` v2.0.0 (compass-validated, GitHub repo ready)
- `/dario` v5.9.0 (R21 authoritative cost, R22 cross-check code, R23 data freshness)
- `thesis-validation-gate.md` (universal pre-flight rule)
- Claude Bridge MCP (two-way Claude.ai <-> Claude Code)

### Key Discoveries
1. Momentum-follow LOSES on CME futures (606 configs tested)
2. FADE (mean-reversion) WINS but only at 0.5%+ threshold (walk-forward validated)
3. Real cost $5.52/RT (not $3.54 estimated) - from Bulenox Rates.pdf
4. P&L stored as price points not dollars (10x display bug, fixed)
5. Walk-forward results depend critically on data freshness and split point
6. Only 0.5% threshold stable on fresh 14-day test window (0.3% decayed)

### Blockers
- Bulenox Paper Trading creds (BX97517) - helpdesk ticket open, awaiting response
- Contract rollover MBTH6 -> MBTJ6 (due ~Mar 30, auto-rollover code deployed)

### Next Session: What to Do
1. Run `/dario DEEP` on `/bulenox-quant` to fill expertise gaps (user requested)
2. Check for Bulenox helpdesk response on Paper Trading creds
3. Monitor bot trades under new 0.5%/TP=50/SL=15 config
4. At n=25: run `/proof-of-edge:check` hypothesis tests
5. At n=50: run `/proof-of-edge:verdict` for GO/NO-GO
6. Update `/bulenox-quant` knowledge base with corrected economics
