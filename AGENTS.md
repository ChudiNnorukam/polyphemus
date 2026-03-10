# Polymarket Trading Bot (Polyphemus) — Project Instructions

## OMC Delegation Rubric

Deterministic routing for this 22-file codebase. If trigger matches, follow the rule.

| Trigger | Required Input | Expected Output | Quality Gate | Route |
|---------|---------------|-----------------|-------------|-------|
| 5+ file change with clear spec | MEMORY.md + file list + diff outline | Modified files + deploy command | 87 tests pass, no new warnings | `ralph` |
| 3+ independent refactors | List of independent changes, zero shared files | Each change as separate patch | Changes truly non-overlapping | `executor` (parallel) |
| Unfamiliar code deep dive | Entry file + error trace or symptom | Root cause at file:line + fix | Fix explains WHY not just WHAT | `architect` |
| EDA / backtesting | .db path + date range + specific question | dario_output/ report with R8 labels | Sample-size labels on every stat | `scientist` or direct |
| VPS/SSH/deploy ops | Instance name + .env path | Verified config state | `journalctl` error check post-deploy | Always direct |
| Single-file edit, known code | File path + exact change spec | Edited file | py_compile + tests pass | Always direct |
| Live trading config change | .env key + before/after values | Updated .env + MEMORY update | LIFECYCLE.md Phase 2+ evidence | Always direct + user approval |

**Hard rules:**
- NEVER delegate SSH or live .env changes. Agents lack MEMORY context and will drift config.
- NEVER delegate if the change touches a live trading parameter. Requires human review.
- `ralph` expects a written spec with file paths. Not "make the bot better."
- `scientist` expects a database path and a specific question. Not "analyze all the data."
- `executor` expects N truly independent tasks with zero shared state. Shared file = run direct.
- `architect` expects an error trace or specific symptom. Not "review the whole codebase."
- **ISOLATION RULE**: When spawning build/executor agents that edit files, ALWAYS use `isolation: "worktree"`. This gives each agent its own git worktree so they cannot corrupt each other's changes. Read-only agents (Explore, Plan, architect) do not need isolation.

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

## Effort Routing (Overhang Implementation, Mar 2026)

When spawning agents for this project, use explicit effort routing:

- **Opus `max`**: Architecture decisions, trading strategy changes, money-related decisions, deep debugging
- **Opus `high`**: Final code review pass, complex root-cause analysis
- **Sonnet `medium`**: Standard implementation, refactoring (default for executor agents)
- **Sonnet `high`**: Multi-file changes with cross-module dependencies
- **Haiku `high`**: Code review triage (first pass), browser automation verification
- **Haiku `medium`**: Test execution, documentation, simple refactoring
- **Haiku `low`**: File search, status checks, codebase exploration

Use `code-review-triage` (Haiku) before `code-reviewer` (Opus) for two-pass review.
Use `test-runner` (Haiku) for test execution and result parsing.
Use `status-checker` (Haiku) for VPS/service health checks.

## Compaction Resilience

When working on complex multi-step tasks in this project:
1. Save progress to `PROGRESS.md` or `.omc/notepad.md` before complex operations
2. After context compaction, re-read `PROGRESS.md` and `git log --oneline -10`
3. For multi-session work, commit with descriptive messages after each feature
4. Critical state (trading parameters, deploy status) must be in files, not just context
