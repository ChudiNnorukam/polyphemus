# Progress

## 2026-04-01 - Pair Arb Session (Major)

### Current State
- **Pair arb LIVE on Emmanuel** ($376 balance), dry-run on Polyphemus ($162)
- Both: BTC/ETH/SOL/XRP 5m only, max_pair_cost=0.98, max_concurrent=2, maker-only
- Lottery/cheap_side/epoch_accum DISABLED on both
- First dry-run trades: BTC +$0.60 x2, SOL +$0.80 (pair_cost 0.96-0.97)
- Next: Dashboard observability (P0), Doppler cleanup (P1)

### 8 Silent Bugs Fixed
1. ACCUM_MAX_ROUNDS dead .env key (deferred)
2. `set("BTC")`={'B','T','C'} in signal_bot.py:442 -> `set(get_asset_filter())` PATCHED
3. Epoch accumulator no price floor -> added min_price guard PATCHED
4. Epoch accumulator metadata dict -> reverted (DB layer handles json.dumps) RESOLVED
5. session_state.py balance regex matched uptime -> anchored to `balance=` PATCHED
6. Doppler overrides ALL .env silently -> full diff mapped, all 4 mismatches covered
7. ACCUM_DRY_RUN=false in Doppler (CRITICAL) -> systemd drop-in FIXED
8. accumulator.py metadata dict x3 -> reverted (DB layer handles it) RESOLVED

### Doppler Override Architecture
Systemd drop-ins at `/etc/systemd/system/lagbot@{name}.service.d/dry_run.conf` override Doppler.
Without them, Doppler sets DRY_RUN=false, ACCUM_DRY_RUN=false, ENABLE_ACCUMULATOR=false, ACCUM_MAX_PAIR_COST=0.995, CHEAP_SIDE_ENABLED=true, LOTTERY_ENABLED=true.

### P0: Dashboard Blockers
1. **accumulator.py has NO performance.db writes** - trades only in memory + metrics. Need to inject PerformanceTracker and add record_entry calls at HEDGED/settlement/unwind.
2. **Dashboard API active but port unreachable** - check `journalctl -u dashboard`
3. **No pair arb frontend** - need Chart.js views: pair_cost chart, fill rate, P&L, per-asset breakdown, orphan rate.

### Files Modified on VPS
- epoch_accumulator.py (price floor, reverted json.dumps)
- accumulator.py (reverted json.dumps x3)
- signal_bot.py (set(get_asset_filter()))
- tools/session_state.py (balance regex)
- /opt/lagbot/data/circuit_breaker.json (reset)
- /etc/systemd/system/lagbot@{emmanuel,polyphemus}.service.d/dry_run.conf (created)

### Verification
```bash
ssh root@82.24.19.114 "journalctl -u lagbot@emmanuel --since '1 hour ago' | grep -iE 'HEDGED|settlement|pnl'"
ssh root@82.24.19.114 "cat /etc/systemd/system/lagbot@emmanuel.service.d/dry_run.conf"
```

### New Skill
`/silent-fault-tracer` - runtime-first forensic debugger, 6-pattern AI-codebase failure taxonomy.

## 2026-04-02 - Accumulator FAK Hardening

### What Changed Locally
- Added fee-aware FAK-first accumulator entry path with explicit `accum_entry_mode`
- Kept rollout explicit by restoring the config default to `maker`
- Added per-leg fee accounting through FOK fallback, unwind, settlement, and dashboard stats
- Fixed observability so a tripped accumulator breaker is reported as `HALTED` / `circuit_breaker`, not generic pipeline starvation

### New Local Verification
- `python3 -m pytest polyphemus/test_accumulator.py polyphemus/tests/test_operator_tooling.py -q`
- Result: `16 passed, 1 warning`

### Remaining Deployment Posture
- Not deployed
- Not promoted live
- Next legal step is shadow-only deploy with explicit `ACCUM_ENTRY_MODE=fak`
- Live promotion is still blocked by `NO-GO` gate and incomplete aligned shadow evidence

## 2026-04-02 - Polyphemus Shadow FAK Deploy

### Shadow Runtime State
- Deployed to `lagbot@polyphemus` only
- Shadow instance now runs with:
  - `DRY_RUN=true`
  - `ACCUM_DRY_RUN=true`
  - `ACCUM_ENTRY_MODE=fak`
  - `DASHBOARD_PORT=8083`
- Verified on VPS:
  - `/api/status` reports `dry_run=true`, `effective_accumulator_dry_run=true`
  - `/api/accumulator` reports `entry_mode=fak`

### Reporting Fixes
- Fixed a misleading systemd override that had been forcing `DRY_RUN=false` and `ACCUM_DRY_RUN=false` on the shadow service
- Made the dashboard and watchdog accumulator-aware for non-BTC shadow targets
- `localhost:8083` / `/api/pipeline` now reports `Accumulator scanning XRP 5m/15m markets`
- Dashboard badge now shows `SHADOW` for dry-run instances instead of incorrectly showing `LIVE`

### Human-in-the-Loop Access
- Tunnel active: `localhost:8083` -> `lagbot@polyphemus`
- Tunnel active: `localhost:8082` -> `lagbot@emmanuel`

### Remaining Posture
- Shadow deploy complete
- Live still blocked by `NO-GO` gate and shadow-window evidence requirements

## 2026-04-02 - Shadow Eval Automation

### Eval Tooling
- Added standalone shadow snapshot collector: `polyphemus/tools/shadow_eval_snapshot.py`
- Added standalone markdown eval report generator: `polyphemus/tools/shadow_eval_report.py`
- Added regression coverage for runtime-truth reporting and report rendering in `polyphemus/tests/test_shadow_eval_tooling.py`

### Evidence Artifacts
- Gated plan written to:
  - `/.omx/plans/2026-04-02-proven-polymarket-crypto-updown-ready-system.md`
  - `polyphemus/.omc/plans/2026-04-02-proven-polymarket-crypto-updown-ready-system.md`
- First local eval artifact:
  - `polyphemus/dario_output/shadow_eval_polyphemus_20260402_175432.md`
- Latest local eval artifact (runtime-truth slice):
  - `polyphemus/dario_output/shadow_eval_polyphemus_20260402_180026.md`

### Snapshot Collection
- Local snapshot history:
  - `polyphemus/data/shadow_eval/polyphemus/hourly_snapshots.jsonl`
- VPS snapshot history:
  - `/opt/lagbot/instances/polyphemus/data/shadow_eval/hourly_snapshots.jsonl`
- Installed hourly VPS cron:
  - `7 * * * * /opt/lagbot/venv/bin/python3 /opt/lagbot/lagbot/tools/shadow_eval_snapshot.py --instance polyphemus --base-url http://127.0.0.1:8083 >> /opt/lagbot/instances/polyphemus/data/shadow_eval/collector.log 2>&1`

### Current Read
- Runtime-truth snapshot now records:
  - target slice `XRP / 5m,15m`
  - `accumulator_entry_mode=fak`
  - `effective_accumulator_dry_run=true`
- Eval verdict remains:
  - `NO-GO: shadow window immature`
- Remaining blocker is time/sample accumulation, not deployment mechanics

## 2026-04-02 - Quant Research Operating System

### Research OS
- Added repo-native quant research operating system playbook:
  - `polyphemus/kb/playbooks/quant_research_operating_system.md`
- Added reusable experiment templates:
  - `polyphemus/.omc/templates/quant_hypothesis.md`
  - `polyphemus/.omc/templates/quant_evidence_log.md`
  - `polyphemus/.omc/templates/quant_promotion_review.md`
- Added scaffold tool:
  - `polyphemus/tools/quant_experiment_scaffold.py`
- Added regression coverage:
  - `polyphemus/tests/test_quant_experiment_scaffold.py`

### Operating Principle
- One hypothesis per era
- One promotable slice at a time
- No live claim without current-era proof
- Replay and dry-run support shadow admission only; they do not authorize live promotion

### Initial Artifacts
- Domain audit captured in:
  - `polyphemus/.omc/compass/polymarket-quant-readiness/audit.md`
- First scaffolded experiment for the active shadow slice:
  - `polyphemus/.omc/experiments/xrp-5m-15m-fak-accumulator-shadow/hypothesis.md`
  - `polyphemus/.omc/experiments/xrp-5m-15m-fak-accumulator-shadow/evidence_log.md`
  - `polyphemus/.omc/experiments/xrp-5m-15m-fak-accumulator-shadow/promotion_review.md`
- Primary BTC live-candidate scaffold created from the repo's existing promotion shape:
  - `polyphemus/.omc/experiments/btc-5m-ensemble-selected-live-v1/hypothesis.md`
  - `polyphemus/.omc/experiments/btc-5m-ensemble-selected-live-v1/evidence_log.md`
  - `polyphemus/.omc/experiments/btc-5m-ensemble-selected-live-v1/promotion_review.md`

### Current BTC Primary Candidate Read
- Narrow slice:
  - `emmanuel / BTC / 5m / binance_momentum / shadow_ensemble_selected=1`
- Current evidence baked into the candidate:
  - `WR=70%`, `n=10`, positive live expectancy on the narrow slice
  - still `NO-GO` because runtime is only `16.7h`, sample is anecdotal, and stall-window quality is unresolved
- This is now the canonical BTC candidate to evaluate. Do not broaden it until this exact slice either matures or dies.

### Candidate Refresh Tooling
- Added runtime-bundle refresh tool for the BTC primary candidate:
  - `polyphemus/tools/quant_candidate_refresh.py`
- Added one-shot refresh pipeline:
  - `polyphemus/tools/quant_refresh_pipeline.py`
- Added launchd automation wrapper:
  - `polyphemus/.omc/automation/run_quant_refresh_pipeline.sh`
  - `polyphemus/.omc/automation/com.polyphemus.quant-refresh.plist`
- Added regression coverage:
  - `polyphemus/tests/test_quant_candidate_refresh.py`
  - `polyphemus/tests/test_quant_refresh_pipeline.py`
- Refresh command:
  - `python3 polyphemus/tools/quant_candidate_refresh.py --write-json`
- Full pipeline command:
  - `python3 polyphemus/tools/quant_refresh_pipeline.py --print-json`
- This updates:
  - `polyphemus/.omc/experiments/btc-5m-ensemble-selected-live-v1/evidence_log.md`
  - `polyphemus/.omc/experiments/btc-5m-ensemble-selected-live-v1/promotion_review.md`
  - `polyphemus/.omc/experiments/btc-5m-ensemble-selected-live-v1/current_status.json`

### Local Automation
- Installed local launchd agent:
  - label: `com.polyphemus.quant-refresh`
  - plist: `~/Library/LaunchAgents/com.polyphemus.quant-refresh.plist`
  - interval: hourly (`StartInterval=3600`)
  - run-at-load: enabled
- Logs:
  - `polyphemus/.omc/automation/logs/quant_refresh_pipeline.log`
  - `polyphemus/.omc/automation/logs/launchd.stdout.log`
  - `polyphemus/.omc/automation/logs/launchd.stderr.log`
- Verified first run succeeded and refreshed:
  - VPS cache bundle
  - runtime bootstrap bundle
  - BTC primary candidate artifacts

### Verification
- `python3 -m pytest polyphemus/tests/test_quant_experiment_scaffold.py polyphemus/tests/test_shadow_eval_tooling.py polyphemus/tests/test_operator_tooling.py -q`
- Result: `19 passed, 1 warning`
- `python3 -m py_compile polyphemus/tools/quant_experiment_scaffold.py`

## 2026-03-09

- Built the first-pass BTC 5m evidence/reporting layer.

## 2026-04-02

- Patched `polyphemus/dashboard.py` to expose `dashboard_build_id` via `/api/status`, stamp the current build into the HTML footer, and auto-reload stale open tabs when the server build changes.
- Deployed the updated `dashboard.py` directly to `lagbot@polyphemus` on the VPS using the safe flow:
  - stop service
  - `scp` file
  - clear `__pycache__`
  - `python3 -m py_compile` from `/tmp`
  - restart service
  - verify `systemctl is-active`
- Verified shadow runtime after deploy:
  - `dry_run=true`
  - `accumulator_entry_mode=fak`
  - `/api/pipeline` headline `Accumulator scanning XRP 5m/15m markets`
  - `/api/status.dashboard_build_id` present and changing on deploy
- Root cause for "localhost:8083 hasn't changed":
  - backend/API was updating
  - open browser tab predated the HTML template change, so new widgets would not appear until a page reload
  - future dashboard deploys should now self-reload once the page has loaded a build-aware version

## 2026-04-02 - XRP Shadow Experiment Resume

### What Was Recovered
- Ran the required runtime bootstrap and refreshed VPS caches with:
  - `python3 polyphemus/tools/quant_refresh_pipeline.py --print-json`
- Confirmed the legacy BTC live-candidate bundle still resolves to the old `2026-03-13` aligned era and remains `NO-GO`; the useful active research lane is the XRP FAK shadow slice, not BTC promotion.
- Pulled current VPS shadow snapshot history to:
  - `polyphemus/data/shadow_eval/polyphemus/hourly_snapshots.vps.jsonl`

### New Tooling
- Added shadow experiment refresh tool:
  - `polyphemus/tools/quant_shadow_experiment_refresh.py`
- Added regression coverage:
  - `polyphemus/tests/test_quant_shadow_experiment_refresh.py`

### Verification
- `python3 -m pytest polyphemus/tests/test_quant_shadow_experiment_refresh.py polyphemus/tests/test_shadow_eval_tooling.py -q`
- Result: `7 passed`
- `python3 -m py_compile polyphemus/tools/quant_shadow_experiment_refresh.py`

### XRP FAK Shadow Read
- Refreshed the experiment bundle with:
  - `python3 polyphemus/tools/quant_shadow_experiment_refresh.py --snapshots polyphemus/data/shadow_eval/polyphemus/hourly_snapshots.vps.jsonl --write-json`
- Updated artifacts:
  - `polyphemus/.omc/experiments/xrp-5m-15m-fak-accumulator-shadow/evidence_log.md`
  - `polyphemus/.omc/experiments/xrp-5m-15m-fak-accumulator-shadow/promotion_review.md`
  - `polyphemus/.omc/experiments/xrp-5m-15m-fak-accumulator-shadow/current_status.json`
- Current state:
  - `NO-GO: shadow window immature`
  - `6.20h` observed shadow duration
  - `ANECDOTAL n=20`
  - net session P&L `+$4.83`
  - expectancy per completed opportunity `+$0.2415`

### New Research Read
- The shadow slice is not failing on expectancy yet.
- The immediate risk is throughput starvation, not P&L:
  - completed opportunities stayed flat (`completed_delta=0`)
  - candidates increased (`candidates_delta=24`)
  - latest block reason was directional pricing:
    - `directional: up_ask=0.550 down_ask=0.510 max_side=$0.52`
- Next quant task is not promotion. It is a narrow diagnostic on why the XRP FAK slice is seeing candidates without converting them into new completed cycles.
