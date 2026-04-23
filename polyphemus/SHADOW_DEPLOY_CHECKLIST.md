> **SUPERSEDED** by `projects/.../memory/project_mtc_gate_verdicts.md` on 2026-04-23. Reason: pair_arb NO-GO (DSR fail, 2026-04-17 MTC verdicts) and accumulator STOPPED since 2026-02-23. This checklist's target strategies are both gated off. Preserved for future revival context. Do NOT treat as a live promotion path. If either strategy is re-entered, re-gate via `/preflight` + `/domain-entry-audit` rather than running this checklist as-is.

---

# Pair-Arb FAK Shadow Deploy Checklist

This checklist is for the accumulator FAK-first patch only. It is a shadow or dry-run deployment checklist, not a live promotion checklist.

## Scope

- Code path: fee-aware accumulator FAK entry, FOK fallback, sellback fee accounting
- Observability: circuit-breaker-aware watchdog and dashboard status
- Deploy path: `/Users/chudinnorukam/Projects/business/predeploy.sh`

## Hard Stop

- Do not promote live while [current_state.json](/Users/chudinnorukam/Projects/business/polyphemus/agent/handoff/current_state.json) remains `NO-GO`
- Do not change live trading params without Phase 2 approval and Phase 4 evidence per [LIFECYCLE.md](/Users/chudinnorukam/Projects/business/LIFECYCLE.md)

## Explicit Rollout Rule

- `ACCUM_ENTRY_MODE` must be set explicitly in the target instance `.env`
- Default code behavior remains `maker`
- Use `ACCUM_ENTRY_MODE=fak` only on the shadow or dry-run instance first

## Local Verification Before Deploy

1. Run:
```bash
python3 -m py_compile \
  /Users/chudinnorukam/Projects/business/polyphemus/accumulator.py \
  /Users/chudinnorukam/Projects/business/polyphemus/config.py \
  /Users/chudinnorukam/Projects/business/polyphemus/health_monitor.py \
  /Users/chudinnorukam/Projects/business/polyphemus/signal_bot.py \
  /Users/chudinnorukam/Projects/business/polyphemus/dashboard.py \
  /Users/chudinnorukam/Projects/business/polyphemus/test_accumulator.py \
  /Users/chudinnorukam/Projects/business/polyphemus/tests/test_operator_tooling.py
```

2. Run:
```bash
python3 -m pytest \
  /Users/chudinnorukam/Projects/business/polyphemus/test_accumulator.py \
  /Users/chudinnorukam/Projects/business/polyphemus/tests/test_operator_tooling.py -q
```

3. Confirm rollout remains opt-in:
```bash
rg -n "accum_entry_mode: str = \"maker\"" /Users/chudinnorukam/Projects/business/polyphemus/config.py
```

## Shadow Deploy Steps

1. Deploy to the shadow or dry-run instance only:
```bash
cd /Users/chudinnorukam/Projects/business
./predeploy.sh --deploy polyphemus --files "config.py accumulator.py health_monitor.py signal_bot.py dashboard.py types.py test_accumulator.py tests/test_operator_tooling.py"
```

2. Set the instance to explicit FAK mode only if this is the shadow target:
```bash
ssh root@82.24.19.114 "grep -E '^ACCUM_ENTRY_MODE=' /opt/lagbot/instances/polyphemus/.env || true"
```

3. If missing, add it using the repo-safe edit path, not inline `sed -i` over SSH.

4. Confirm service came back:
```bash
ssh root@82.24.19.114 "systemctl is-active lagbot@polyphemus"
```

5. Confirm no startup errors:
```bash
ssh root@82.24.19.114 "journalctl -u lagbot@polyphemus --since '60 seconds ago' --no-pager | grep -iE 'error|traceback'"
```

## Runtime Checks After Shadow Deploy

1. Confirm accumulator status includes the new fields:
```bash
ssh root@82.24.19.114 "curl -s http://127.0.0.1:8080/api/accumulator | jq '{entry_mode,circuit_tripped,daily_loss_limit,total_pnl,active_positions}'"
```

2. Confirm status endpoint reflects breaker-aware flags:
```bash
ssh root@82.24.19.114 "curl -s http://127.0.0.1:8080/api/status | jq '{accumulator_circuit_tripped,accumulator_entry_mode,accumulator_daily_loss_limit,accumulator_total_pnl}'"
```

3. Confirm pipeline endpoint no longer mislabels a breaker halt as starvation:
```bash
ssh root@82.24.19.114 "curl -s http://127.0.0.1:8080/api/pipeline | jq '{stage,headline,summary}'"
```

4. Confirm the journal now says the breaker is active instead of generic starvation:
```bash
ssh root@82.24.19.114 "journalctl -u lagbot@polyphemus --since '30 minutes ago' --no-pager | grep -E 'accumulator circuit breaker active|ZERO TRADE ALERT|PIPELINE WATCHDOG'"
```

## Shadow Success Criteria

- No startup exceptions
- `ACCUM_ENTRY_MODE=fak` only on the shadow instance
- `/api/pipeline` returns `stage: "circuit_breaker"` when breaker is tripped
- Dashboard shows `HALTED` instead of `LIVE` for breaker-tripped sessions
- Orphan rate and sellback rate improve relative to the maker-first baseline
- No new execution-path errors appear in journald

## Still Not a Live Go

Even if all of the above passes, this is still only a shadow deploy checkpoint. Live promotion remains blocked until the repo gate clears and the required aligned shadow evidence is complete.
