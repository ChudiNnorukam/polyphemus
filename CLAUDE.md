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
- **VPS**: `142.93.143.178`, systemd service `polyphemus`, package at `/opt/polyphemus/polyphemus/`
- **Deploy script**: `deploy_polyphemus.sh` — copies to `/opt/polyphemus/` on VPS
- **Deploy pattern**: Edit locally → `scp` to VPS → `py_compile` → `systemctl restart polyphemus`
- **NEVER use inline `python3 -c` via SSH** — write to file, scp, execute remotely
- **Performance DB**: `data/performance.db` on VPS
- **All patches via SSH**: stop service → scp files → verify syntax → start service
- **NOTE**: VPS still uses old `sigil` service name until next deploy. Local codebase is renamed to `polyphemus`.
