# Polymarket Trading Bot (Polyphemus) — Project Instructions

## Auto OMC Routing

When the user requests any of these task types, automatically invoke the corresponding OMC mode without being asked:

| User Intent | Auto-Invoke | Example Triggers |
|-------------|-------------|------------------|
| New feature / multi-file change | `/plan` then `ralph` | "add Kelly sizing", "implement new exit strategy", "add a new module" |
| Data analysis / backtesting | `scientist` agent | "backtest", "analyze trades", "what's our WR", "compare strategies", "run numbers" |
| Deep debugging / root cause | `architect` agent | "why is X happening", "find the bug", "investigate", "debug this" |
| Batch code changes / refactor | `executor` agents (parallel) | "refactor exits", "rename across files", "update all handlers" |
| Simple file writes / deploy scripts | `executor-low` (haiku) | "write a patch script", "create deploy script", "write a simple util" |
| Live monitoring / SSH ops | Direct Opus (no delegation) | "check logs", "status", "deploy this fix", "keep monitoring" |

## Project Context

- **Codebase**: `/Users/chudinnorukam/Projects/business/polyphemus/` (22 files, ~4,000 LOC)
- **VPS**: `82.24.19.114` (QuantVPS), shared codebase at `/opt/lagbot/lagbot/`, instances at `/opt/lagbot/instances/`
- **Active services**: `lagbot@emmanuel` (RUNNING, DRY_RUN=true), `lagbot@chudi` (STOPPED), `polyphemus` (STOPPED)
- **Deploy pattern**: Edit locally → `scp` to VPS → `py_compile` → `systemctl restart lagbot@<instance>`
- **NEVER use inline `python3 -c` via SSH** — scp the script, then run it
- **Performance DB**: `data/performance.db` relative to `LAGBOT_DATA_DIR` (per-instance)
- **All patches via SSH**: stop service → scp files → clear `__pycache__` → verify syntax → start service
- **DO NOT use 142.93.143.178** — that is backup only. DO NOT use 159.223.236.50 — dead.
