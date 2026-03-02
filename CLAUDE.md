# Polymarket Trading Bot (Polyphemus) — Project Instructions

## OMC Routing (guidelines, not mandatory)

Consider these routing options. For this 22-file codebase, Opus working directly with full MEMORY context is usually faster and more accurate than delegation. Only delegate when there's genuine parallel work or deep research.

| User Intent | Consider | When to delegate vs do directly |
|-------------|----------|-------------------------------|
| New feature / multi-file change | `/plan` then `ralph` | Delegate if 5+ files, do directly if < 5 files with clear spec |
| Data analysis / backtesting | `scientist` agent or direct | Direct is usually better (has full session + MEMORY context) |
| Deep debugging / root cause | `architect` agent or direct | Direct for known codebase, delegate for unfamiliar code |
| Batch code changes / refactor | `executor` agents (parallel) | Delegate only if 3+ truly independent changes |
| Simple file writes / deploy scripts | Direct Opus | Always direct (needs VPS + bug context from MEMORY) |
| Live monitoring / SSH ops | Direct Opus | Always direct |

## Project Context

- **Codebase**: `/Users/chudinnorukam/Projects/business/polyphemus/` (22 files, ~4,000 LOC)
- **VPS**: `82.24.19.114` (QuantVPS), shared codebase at `/opt/lagbot/lagbot/`, instances at `/opt/lagbot/instances/`
- **Active services**: `lagbot@emmanuel` (RUNNING, DRY_RUN=false, LIVE), `lagbot@chudi` (STOPPED), `polyphemus` (STOPPED)
- **Deploy pattern**: `./predeploy.sh --deploy emmanuel` (automated: compile, test, checksum, scp, verify, restart). Manual fallback: edit locally, scp to VPS, py_compile, systemctl restart.
- **NEVER use inline `python3 -c` via SSH** — scp the script, then run it
- **NEVER import from /opt/lagbot/lagbot/ in diagnostic Python on VPS** — types.py shadows stdlib (Bug #39). Grep .env directly instead.
- **Performance DB**: `data/performance.db` relative to `LAGBOT_DATA_DIR` (per-instance)
- **All patches via SSH**: stop service → scp files → clear `__pycache__` → verify syntax → start service
- **Before any `sed -i` on .env**: `cp .env .env.bak.$(date +%s)` first
- **DO NOT use 142.93.143.178** — that is backup only. DO NOT use 159.223.236.50 — dead.
- **LIFECYCLE RULE**: Any strategy change, new feature, or config change touching .env trading parameters MUST follow [LIFECYCLE.md](LIFECYCLE.md) phases. No phase can be skipped. Evidence from each phase must be documented before proceeding. Going live without Phase 4 (testing) evidence is a hard block. Born from $1,323 in preventable losses (Feb-Mar 2026).
