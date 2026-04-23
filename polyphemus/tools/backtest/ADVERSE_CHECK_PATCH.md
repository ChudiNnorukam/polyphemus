# Phantom Adverse-Selection Patch — 2026-04-17

Task #3 from the "1, 2 then 3" sequence. Root cause + fix for the
`binance_price_at_fill` dropout that collapsed adverse-selection
instrumentation to 0% on emmanuel from Apr 11 onward.

## Root cause (not a bug, a code gap)

The existing adverse-selection pipeline is wired only on the LIVE fill path:

- `position_executor.py:547-551` — captures `_pending_entry_binance_price` at
  order placement.
- `position_executor.py:567-603` — after `fill_result.success`, schedules
  `asyncio.create_task(self._run_adverse_check(...))`.
- `position_executor.py:808-851` — `_run_adverse_check` sleeps the check
  window, re-reads Binance via `self._momentum_feed`, then calls
  `performance_db.update_adverse_selection(...)` which writes
  `binance_price_at_fill`, `binance_price_30s`, `adverse_fill`,
  `adverse_fill_bps`, `check_window_secs`.

The DRY_RUN phantom path in `signal_bot.py:1358-1520` creates phantom trades
entirely outside `position_executor`. It emits SIZING_COMPUTED →
SIGNAL_FIRED → ORDER_PLACED → `record_entry` → ORDER_FILLED trace events, but
**never schedules an adverse check**. The fields stay NULL forever.

When emmanuel flipped `DRY_RUN=false → true` between Apr 12 and Apr 16, every
new trade routed through the phantom path, and adverse-selection coverage
went from 100% → 0% overnight. The code didn't break — the path was never
instrumented in the first place.

## Patch

Adds a companion `_run_phantom_adverse_check` method on `SignalBot` that
mirrors `PositionExecutor._run_adverse_check` (same window cap, same timeout,
same NULL-on-miss semantics), then schedules it via
`asyncio.create_task` immediately after the phantom ORDER_FILLED emit.

Key properties:

- **Fire-and-forget**: wrapped in try/except so a scheduling error never
  blocks the phantom return. Mirrors the live path's safety envelope.
- **Uses existing infrastructure**: `self._momentum_feed` (set at
  `signal_bot.py:245-259`) and `self._tracker.db` (PerformanceDB at L211).
  No new dependencies, no new config flags.
- **Window capped at epoch boundary**: same `max(1, min(30, secs_to_end - 5))`
  rule as the live path — never reads Binance after the market resolves.
- **Epoch end derivation**: prefer `signal["market_end_time_iso"]`; fall back
  to parsing the slug suffix via `models.parse_window_from_slug`.

Diff: `git diff polyphemus/signal_bot.py` — 117 lines added, 0 removed.
Insertion points:
- L519-573: new `_run_phantom_adverse_check` method (before `_run_preflight`).
- L1555-1617: scheduling block inserted after ORDER_FILLED emit, before the
  signal logger update.

## Verification (already run locally)

```
python3 -c "import ast; ast.parse(open('polyphemus/signal_bot.py').read())"
# → AST parses
python3 -m py_compile polyphemus/signal_bot.py
# → OK

python3 -m pytest polyphemus/test_accumulator.py           # 10/10 pass
python3 -m pytest polyphemus/tests/test_dry_run_ground_truth.py \
                  polyphemus/tests/test_phase3_trade_tracer.py \
                  polyphemus/tests/test_phase2_record_entry_enrichment.py \
                  polyphemus/test_smoke.py                  # 53 pass, 3 skip
```

`tests/test_signal_pipeline.py` fails at import due to the pre-existing
missing `py_clob_client` in the local venv. Not related to this patch; that
module is installed on the VPS.

## Deploy instructions (NOT deployed — requires Chudi OK)

This is a code change to a running service. Per chudi-frame rule #4
("deploy/restart that would touch a service with open positions or
in-flight requests"), needs explicit approval even though it's code-only.

### Pre-deploy checks

```bash
# 1. Confirm no open positions on emmanuel (must be 0)
ssh root@82.24.19.114 "sqlite3 /opt/lagbot/instances/emmanuel/data/performance.db \
  'SELECT COUNT(*) FROM trades WHERE exit_time IS NULL'"
# Expect: 0  (matches the Markov audit; emmanuel is 0-for-14 with nothing open)

# 2. Confirm emmanuel still DRY_RUN=true (otherwise patch is moot)
ssh root@82.24.19.114 "grep -E '^DRY_RUN' /opt/lagbot/instances/emmanuel/.env"
# Expect: DRY_RUN=true
```

### Deploy (from local repo)

```bash
cd /Users/chudinnorukam/Projects/business/polyphemus

# 3. Stop emmanuel
ssh root@82.24.19.114 "systemctl stop lagbot@emmanuel"

# 4. Copy the patched file (SHARED codebase — affects @chudi too if it
#    restarts, but chudi is STOPPED per MEMORY.md)
scp signal_bot.py root@82.24.19.114:/opt/lagbot/lagbot/signal_bot.py

# 5. Clear pycache, verify syntax on VPS
ssh root@82.24.19.114 "rm -rf /opt/lagbot/lagbot/__pycache__ && \
  /opt/lagbot/venv/bin/python3 -m py_compile /opt/lagbot/lagbot/signal_bot.py && \
  echo OK"
# Expect: OK

# 6. Restart
ssh root@82.24.19.114 "systemctl start lagbot@emmanuel && sleep 5 && \
  systemctl is-active lagbot@emmanuel"
# Expect: active
```

### Post-deploy verify (MANDATORY)

```bash
# 7. No crashes in first 60 seconds
ssh root@82.24.19.114 "journalctl -u lagbot@emmanuel --since '60 seconds ago' | \
  grep -iE 'error|traceback' | head -20"
# Expect: no Traceback; occasional benign errors OK

# 8. New log lines appearing — wait ~5 minutes for a signal to fire
ssh root@82.24.19.114 "journalctl -u lagbot@emmanuel --since '5 minutes ago' | \
  grep -E 'ADVERSE_CHECK_SCHEDULED|ADVERSE_SELECTION' | head -10"
# Expect: ADVERSE_CHECK_SCHEDULED lines for each phantom fill,
#         followed ~30s later by ADVERSE_SELECTION result lines

# 9. DB coverage check — after ~30 minutes of runtime
ssh root@82.24.19.114 "sqlite3 /opt/lagbot/instances/emmanuel/data/performance.db \
  \"SELECT COUNT(*) AS n, \
           SUM(CASE WHEN binance_price_at_fill IS NOT NULL THEN 1 ELSE 0 END) AS populated \
    FROM trades WHERE entry_time > strftime('%s','now','-30 minutes')\""
# Expect: populated == n  (100% coverage on new trades)
```

### Rollback

Single-file revert. No DB migrations, no config changes.

```bash
# If something goes wrong:
ssh root@82.24.19.114 "systemctl stop lagbot@emmanuel"
cd /Users/chudinnorukam/Projects/business && git stash push -- polyphemus/signal_bot.py
scp polyphemus/signal_bot.py root@82.24.19.114:/opt/lagbot/lagbot/signal_bot.py
ssh root@82.24.19.114 "rm -rf /opt/lagbot/lagbot/__pycache__ && \
  systemctl start lagbot@emmanuel"
```

## What this unlocks

Once 24-48h of coverage is collected, we can finally answer the question the
previous session (Apr 16-17 0-for-26 collapse) couldn't:

> Is our entry price systematically off from the Binance reference at fill
> time, and by how much?

Concretely, we'll be able to segment `adverse_fill_bps` by:

- `signal_source` (binance_momentum vs accumulator vs pair_arb vs weather)
- `direction` (up vs down)
- `entry_band` (the Task #1 filter finding — is 0.65-0.80 adverse-dominated?)
- `asset` (BTC vs ETH vs SOL vs XRP)

If adverse bps is consistently negative on a specific segment, that's the
root-cause evidence missing from today's backtest work. Task #1's Scenario
D' filter is our best guess without this data; with it, we can graduate to
a data-backed rule.

## Files

- `polyphemus/signal_bot.py` — patched (117 lines added)
- `polyphemus/tools/backtest/ADVERSE_CHECK_PATCH.md` — this file
