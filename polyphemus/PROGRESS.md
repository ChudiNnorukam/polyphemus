# Progress

## 2026-03-09

- Built the first-pass BTC 5m evidence/reporting layer.
- Generated live report: `dario_output/btc5m_live_emmanuel_20260310.md`.
- Live snapshot findings:
  - `emmanuel` is `BTC`-only, `binance_momentum`, `DRY_RUN=false`, `ENTRY_MODE=fak`.
  - No `epoch_coverage` rows on live `emmanuel`.
  - No `evidence_*` columns in live `signals.db`.
  - Biggest live BTC 5m losses cluster in high-price / late-entry / `resolution_snipe` buckets.
  - OpenClaw is present and producing `lagbot_context.json`; OpenFang remains unverified.
- Next action in progress:
  - Deploy shadow-only evidence logging to `lagbot@emmanuel`.
  - Enable `ENABLE_BTC5M_EVIDENCE_VERDICTS=true`, `BTC5M_EVIDENCE_MODE=shadow`, `BTC5M_EVIDENCE_MIN_SAMPLES=30`.
  - Do not change entry/exit/trading behavior.

## 2026-03-10 Deploy Result

- `lagbot@emmanuel` stopped cleanly with `0` open positions.
- Uploaded runtime files:
  - `config.py`
  - `signal_bot.py`
  - `signal_logger.py`
  - `evidence_verdict.py`
- VPS `py_compile` passed for all uploaded runtime files.
- Backed up `/opt/lagbot/instances/emmanuel/.env` and enabled:
  - `ENABLE_BTC5M_EVIDENCE_VERDICTS=true`
  - `BTC5M_EVIDENCE_MODE=shadow`
  - `BTC5M_EVIDENCE_MIN_SAMPLES=30`
- Service restarted cleanly and is `active`.
- `signals.db` migrated successfully with `evidence_*` columns.
- Startup journal confirms:
  - `SignalLogger ENABLED`
  - `BTC5MEvidenceEngine ENABLED (mode=shadow, min_samples=30)`
- No trade behavior was changed; this is observability-only.

## 2026-03-10 Dashboard Result

- Extended the embedded dashboard in `dashboard.py`.
- New live API endpoints on `lagbot@emmanuel`:
  - `/api/signals`
  - `/api/evidence`
  - `/api/trades`
  - `/api/filters`
  - `/api/errors`
- Verified after deploy:
  - dashboard still running on `127.0.0.1:8082`
  - `/api/evidence` returns live shadow verdicts
  - `/api/signals` returns recent signal decisions with evidence fields
  - `/api/trades` returns recent normalized trade rows
  - `/api/filters` returns 24h BTC 5m rejection reasons plus price/time buckets
  - `/api/errors` returns recent warning/error lines from `lagbot@emmanuel.service`
- Service restarted cleanly and remained `active`.

## 2026-03-10 Live Debug Result

- Checked `lagbot@emmanuel` health and recent journal output directly on `82.24.19.114`.
- Service was healthy; no crash loop or fatal startup error was present.
- Main live issue was guard behavior, not process stability:
  - `MOMENTUM_MAX_EPOCH_ELAPSED_SECS=60` on the live instance
  - BTC 5m momentum signals after the first minute of the epoch were being rejected as `epoch_too_late`
  - this explains cases like `211s remaining` still being rejected
- Patched `signal_guard.py` to prefer `time_remaining_secs` + `market_window_secs` over raw slug-clock math when computing `epoch_too_late`, falling back to slug parsing only when signal timing fields are absent.
- Patched `signal_bot.py` to include guard context in rejection/shadow logs so live decisions explain their numbers.
- Added focused tests in `test_modules.py` for early/late momentum epoch filtering using signal timing fields.
- Deployed `signal_guard.py` and `signal_bot.py` to `emmanuel`, cleared `__pycache__`, verified `py_compile`, and restarted the service cleanly.
- No live `.env` trading parameter was changed during this debug pass.

## 2026-03-10 BTC 5m Source Verification

- Verified that BTC 5m markets resolve against the oracle/Chainlink-aligned price path rather than Binance direction alone.
- Compared BTC 5m signal sources using recorded outcomes from:
  - `/opt/lagbot/instances/emmanuel/data/signals.db`
  - `/opt/lagbot/instances/polyphemus/data/signals.db`
- Main finding:
  - `binance_momentum` has the best final-resolution alignment among meaningful sources.
  - `resolution_snipe` is frequently directionally right but still strongly loss-making due to late, expensive entries.
  - `sharp_move` is not ready for live use.
- Wrote candidate live ruleset report:
  - `dario_output/btc5m_live_ruleset_candidate_20260310.md`
- No live `.env` changes were applied in this step; this is a preparation/evidence artifact only.

## 2026-03-10 PnL Protection Package

- Pulled current `emmanuel` sizing/risk/exit settings from `/opt/lagbot/instances/emmanuel/.env`.
- Verified:
  - `resolution_snipe` and `sharp_move` should remain off for live BTC 5m
  - `window_delta` should remain shadow-only
  - expensive BTC entry buckets are the dominant loss source
  - `pre_resolution_exit` is currently the only clearly helpful non-resolution exit
  - `AUTO_MAX_BET=true` leaves sizing less deterministic than desired for a fragile edge
- Wrote approval-ready package:
  - `dario_output/pnl_protection_package_20260310.md`
- Package recommends:
  - keep BTC-only, first-minute, cheap-entry momentum path
  - keep stop-loss / mid-price-stop / reversal exits off
  - reduce size and simultaneous exposure with:
    - `BASE_BET_PCT=0.02`
    - `MAX_OPEN_POSITIONS=1`
    - `MAX_BET=20`
    - `AUTO_MAX_BET=false`
    - `MAX_TRADE_AMOUNT=20`
- No live config was changed in this step.

## 2026-03-11 PnL Protection Applied To `emmanuel`

- Backed up `/opt/lagbot/instances/emmanuel/.env` before changing live values.
- Applied approved protective `.env` delta:
  - `BASE_BET_PCT=0.02`
  - `MAX_OPEN_POSITIONS=1`
  - `MAX_BET=20`
  - `AUTO_MAX_BET=false`
  - `MAX_TRADE_AMOUNT=20`
- Stopped `lagbot@emmanuel`, updated `.env`, cleared `__pycache__`, and verified VPS syntax with:
  - `python3 -m py_compile /opt/lagbot/lagbot/*.py`
- Attempted `startup_check.py`, but the known VPS `types.py` shadowing issue still blocks that path. Used service preflight + journal verification instead.
- Restarted `lagbot@emmanuel` cleanly and confirmed:
  - `systemctl is-active` => `active`
  - startup preflight passed
  - wallet balance check passed
  - dashboard recovered on `127.0.0.1:8082`
  - status API returned `running`, `errors=0`, `binance_connected=true`
- Live trading remains enabled; this was a protective sizing/config rollout, not a dry-run change.

## 2026-03-11 Pipeline Watchdog Added

- Implemented a pipeline watchdog in:
  - `dashboard.py`
  - `health_monitor.py`
  - `signal_bot.py`
- Added dashboard endpoint:
  - `/api/pipeline`
- Added dashboard UI card:
  - `BTC Pipeline Watchdog`
- The watchdog now reports:
  - last BTC 5m decision time
  - last BTC `binance_momentum` candidate time
  - last BTC guard pass time
  - last executed BTC 5m trade time
  - counts over `15m`, `1h`, and `6h`
  - top BTC guard blockers
  - recent passed BTC signals
- Health monitor now logs explicit starvation warnings when:
  - no BTC 5m decision in 15 minutes
  - no BTC `binance_momentum` candidate in 1 hour
  - BTC candidates exist but 0 pass guards
  - no executed BTC 5m trade in 6 hours
- Deployed the patch to the correct VPS import path at `/opt/lagbot/lagbot/*.py`.
- Verified live:
  - service restarted cleanly and remained `active`
  - `/api/pipeline` returns live watchdog data
  - current stage is `stalled`
  - current headline: `No BTC 5m decision in 15 minutes`

## 2026-03-11 BTC 5m Entry Retry Implementation

- Implemented a bounded BTC 5m execution-reliability path in:
  - `position_executor.py`
  - `config.py`
  - `dashboard.py`
  - `signal_bot.py`
  - `test_modules.py`
- Added new config surface for shadow-first retry behavior:
  - `BTC5M_ENTRY_RETRY_ENABLED`
  - `BTC5M_ENTRY_RETRY_MODE`
  - `BTC5M_ENTRY_RETRY_MAX_PLACEMENT_RETRIES`
  - `BTC5M_ENTRY_RETRY_MAX_FILL_RETRIES`
  - `BTC5M_ENTRY_RETRY_DELAY_MS`
  - `BTC5M_ENTRY_RETRY_REPRICE_CENTS`
  - `BTC5M_ENTRY_RETRY_MIN_SECS_REMAINING`
  - `BTC5M_ENTRY_RETRY_MAX_OVERPAY_CENTS`
- Behavior:
  - only applies to `BTC` + `5m` + `binance_momentum`
  - placement retry is bounded to one transient retry
  - fill retry is bounded to one zero-fill timeout retry
  - retries abort on midpoint-above-cap, too-late, or overpay-cap violations
  - shadow mode logs retry eligibility without placing the retry
- Added pipeline/dashboard fields:
  - `passed_btc_candidates`
  - `placement_failures`
  - `fill_timeouts`
  - `retry_recovered`
  - `retry_skip_reasons`
- Verification:
  - `python3 -m py_compile` passed for the changed files
  - direct manual scenario checks passed for:
    - placement retry success
    - fill retry success
    - midpoint-above-cap skip
    - too-late skip
    - shadow-only retry logging
    - non-BTC/non-scope no-retry behavior
    - dashboard pipeline summary counter rendering
- Not yet deployed to VPS.
- Not yet enabled in any live `.env`; default remains disabled/shadow-safe.

## 2026-03-11 BTC 5m Entry Retry Deployed To `emmanuel`

- Deployed updated runtime files to `/opt/lagbot/lagbot/`:
  - `config.py`
  - `position_executor.py`
  - `dashboard.py`
  - `signal_bot.py`
- Backed up `/opt/lagbot/instances/emmanuel/.env` before rollout.
- Enabled shadow-only retry config on `emmanuel`:
  - `BTC5M_ENTRY_RETRY_ENABLED=true`
  - `BTC5M_ENTRY_RETRY_MODE=shadow`
  - `BTC5M_ENTRY_RETRY_MAX_PLACEMENT_RETRIES=1`
  - `BTC5M_ENTRY_RETRY_MAX_FILL_RETRIES=1`
  - `BTC5M_ENTRY_RETRY_DELAY_MS=500`
  - `BTC5M_ENTRY_RETRY_REPRICE_CENTS=1`
  - `BTC5M_ENTRY_RETRY_MIN_SECS_REMAINING=45`
  - `BTC5M_ENTRY_RETRY_MAX_OVERPAY_CENTS=5`
- Restart procedure:
  - stopped `lagbot@emmanuel`
  - copied runtime files
  - cleared `__pycache__`
  - verified syntax with `python3 -m py_compile`
  - restarted `lagbot@emmanuel`

- Verified live:
  - `systemctl is-active` => `active`
  - dashboard recovered on `127.0.0.1:8082`
  - `/api/pipeline` now includes:
    - `passed_btc_candidates`
    - `placement_failures`
    - `fill_timeouts`
    - `retry_recovered`
    - `retry_skip_reasons`
  - current retry counters are `0` because no passed BTC candidate has hit the retry path since restart
- Important unrelated finding during restart:
  - startup logged `CLOB↔DB trade audit FAILED: CRITICAL: CLOB has 1 trades in 24h but DB has 0`
  - this predates the retry rollout and should be investigated separately before trusting execution/accounting completeness

## 2026-03-11 Blocked Signal Relaxation Audit

- Ran a BTC 5m blocked-signal counterfactual across cached `emmanuel` and `polyphemus` signal DBs.
- Resolved historical market outcomes by slug against Polymarket Gamma API.
- Result:
  - blocked BTC 5m rows were gross positive in the simple hold-to-resolution counterfactual

## 2026-03-12 Multi-Strategy Shadow Lab Upgrade

- Implemented the three requested research upgrades plus one essential fourth action locally:
  - dedicated BTC 5m ensemble shadow comparison path
  - fee-aware multi-strategy replay
  - live `epoch_coverage` logging on the always-on Binance price path
  - config-era / config-label / instance tagging for future backtests
- Added new runtime modules and schema support:
  - `ensemble_shadow.py`
  - additive `signals` fields for ensemble annotations and config-era tags
  - additive `epoch_coverage` fields for instance/config-era tagging
- Updated runtime paths:
  - `signal_bot.py` now records config tags, annotates signals with ensemble-shadow verdicts, and marks epoch coverage when a signal is seen
  - `binance_momentum.py` now logs BTC 5m epoch transitions from the main Binance/Coinbase tick path instead of relying on the stale snipe-only path
  - `dashboard.py` now exposes current-guarded vs ensemble-selected counts in the pipeline summary when the schema supports it
- Added fee-aware replay to `tools/strategy_shadow_scan.py` and regenerated the report:
  - `dario_output/btc5m_strategy_shadow_scan_20260312_084012.md`
- Latest 14-day fee-aware replay result:
  - `discount_window_delta`: `20` trades, `75.0%` WR, `$4.509` total net PnL, `43.2%` net ROI on deployed cost (`ANECDOTAL`)
  - `ensemble_ranked`: `134` trades, `72.4%` WR, `$8.921` total net PnL, `10.1%` net ROI on deployed cost (`MODERATE`)
  - `current_guarded_shadow`: `92` trades, `68.5%` WR, `$3.224` total net PnL, `5.4%` net ROI on deployed cost (`LOW`)
- Direct local verification passed for:
  - signal normalization
  - schema-aware signal logging overrides
  - guard-stage rejection logging
  - epoch coverage config tagging
  - ensemble-shadow reselection behavior
- Next action in progress:
  - deploy the shadow-only runtime upgrade to VPS shared code
  - set `INSTANCE_NAME`, `CONFIG_LABEL`, `ENABLE_BTC5M_ENSEMBLE_SHADOW=true`, and `BTC5M_ENSEMBLE_MODE=shadow` on `emmanuel` and `polyphemus`
  - verify both services restart cleanly and expose the new dashboard counters

## 2026-03-12 Multi-Strategy Shadow Lab Deployed

- Uploaded the shared runtime files to `/opt/lagbot/lagbot/` on `82.24.19.114`:
  - `config.py`
  - `ensemble_shadow.py`
  - `signal_logger.py`
  - `signal_pipeline.py`
  - `signal_bot.py`
  - `binance_momentum.py`
  - `dashboard.py`
- Backed up both live instance env files before editing:
  - `/opt/lagbot/instances/emmanuel/.env.bak.<timestamp>`
  - `/opt/lagbot/instances/polyphemus/.env.bak.<timestamp>`
- Applied shadow-only research flags:
  - `INSTANCE_NAME=emmanuel|polyphemus`
  - `CONFIG_LABEL=btc5m_shadow_lab_v3`
  - `ENABLE_SIGNAL_LOGGING=true`
  - `ENABLE_BTC5M_ENSEMBLE_SHADOW=true`
  - `BTC5M_ENSEMBLE_MODE=shadow`
- Cleared VPS `__pycache__`, verified syntax with `python3 -m py_compile`, and restarted:
  - `lagbot@emmanuel`
  - `lagbot@polyphemus`
- Verified after restart:
  - both services are `active`
  - both journals show:
    - `SignalLogger ENABLED`
    - `BTC5MEvidenceEngine ENABLED (mode=shadow, min_samples=30)`
    - `BTC5MEnsembleShadow ENABLED (mode=shadow)`
    - `SignalBot initialized successfully`
  - both pipeline APIs are up:
    - `http://127.0.0.1:8082/api/pipeline`
    - `http://127.0.0.1:8083/api/pipeline`
  - both live `signals.db` files are now recording fresh `epoch_coverage` rows with:
    - `instance_name`
    - `config_label=btc5m_shadow_lab_v3`
    - non-empty `config_era`
- Current caveats:
  - `emmanuel` still logs the pre-existing `CLOB↔DB trade audit FAILED: CLOB has 1 trades in 24h but DB has 0` startup mismatch
  - there have not yet been fresh post-restart BTC signal rows, so ensemble-selected signal annotations are not visible in the DB yet

## 2026-03-12 Initial Live Shadow Lab Comparison

- Pulled the first post-rollout live sample directly from VPS SQLite.
- Wrote comparison artifact:
  - `dario_output/btc5m_shadow_lab_v3_initial_live_comparison_20260312.md`
- Initial result:
  - both instances logged `90` BTC 5m epochs under `btc5m_shadow_lab_v3`
  - `emmanuel` saw BTC signals in only `11` of those epochs
  - `polyphemus` saw BTC signals in `46` of those epochs
- Ensemble-shadow comparison in the initial sample:
  - `emmanuel`: `17` tagged BTC rows, `7` ensemble-selected, `3` current-guarded
  - `polyphemus`: `55` tagged BTC rows, `5` ensemble-selected, `2` current-guarded
- Main conclusion:
  - `polyphemus` is currently the better BTC 5m research sensor
  - `emmanuel` remains materially starved even after the shadow-lab rollout
  - next diagnostic target should be the per-instance BTC signal generation gap, not another strategy guess

## 2026-03-12 Window Delta Drift Diagnosis And Fix

- Root-caused one concrete cross-instance drift in the BTC shadow lab:
  - `polyphemus` was running `WINDOW_DELTA_LEAD_SECS=60`
  - `emmanuel` had no explicit env override, so it fell back to the shared-code default `10`
- This made the two shadow boxes incomparable:
  - `polyphemus` could capture earlier, cheaper `window_delta` opportunities
  - `emmanuel` often only saw the same epoch near the tail end, after price had already worsened
- Fixed the code path in `binance_momentum.py`:
  - `window_delta` is now evaluated in a dedicated 1-second loop instead of only on raw tick timing
  - epoch open is derived from buffered prices, not whichever live tick first hit that instance
  - epoch coverage now uses the derived Binance open instead of the previously broken symbol-vs-tuple key check
- Added focused regression coverage in `test_lagbot.py` for:
  - deriving epoch open from buffered prices
  - late `window_delta` checks still using the true epoch open
- Verified locally:
  - `py_compile` passed for `binance_momentum.py` and `test_lagbot.py`
  - direct targeted tests passed
- Deployed the shared code fix to VPS and restarted both services.
- Verified live startup:
  - both services are `active`
  - both now log `Window delta loop started`
  - `emmanuel` now also starts with `lead=60s`
- Remaining caveat:
  - after aligning the lead window, `polyphemus` still appears to emit more `window_delta` shadow rows than `emmanuel`, so there may be an additional data-path or market-state difference still to diagnose

## 2026-03-12 Post-Fix Convergence Check

- Verified the actual live process environment after restart:
  - `emmanuel` now has `WINDOW_DELTA_LEAD_SECS=60`
  - `polyphemus` also has `WINDOW_DELTA_LEAD_SECS=60`
- Checked only the post-fix window (`>= 2026-03-12T16:17:00Z`) instead of mixing older rows.
- Result:
  - both instances logged exactly the same fresh BTC 5m shadow signal:
    - slug: `btc-updown-5m-1773332400`
    - source: `binance_momentum`
    - midpoint: `0.81`
    - time remaining: `233s`
    - `shadow_ensemble_selected=1`
  - both also logged the same corresponding BTC 15m shadow row for that timestamp
  - both logged `1` BTC epoch and `1` BTC epoch-with-signal in `epoch_coverage` for the post-fix sample
- Conclusion:
  - the previously observed `emmanuel` vs `polyphemus` BTC shadow divergence was primarily caused by mixed-window comparison and the hidden `WINDOW_DELTA_LEAD_SECS` config drift
  - the shadow lab is now producing comparable post-fix behavior across both instances
  - strongest current relax candidates are `MAX_ENTRY_PRICE`, `WHIPSAW_MAX_RATIO`, and `MOMENTUM_MAX_EPOCH_ELAPSED_SECS`
  - `flat_regime`, `duplicate_slug`, and `max_positions` should stay strict
- Wrote research note:
  - `dario_output/blocked_signal_relaxation_audit_20260311.md`
- Missing-data audit added to the note:
  - absent `epoch_coverage`
  - missing terminal decision states
  - missing order-attempt telemetry
  - missing realized fee/slippage
  - missing config-era tagging

## 2026-03-11 BTC 5m Guard Relaxation Shadow Rollout To `emmanuel`

- Applied the Stage 1 guard-loosening experiment to `/opt/lagbot/instances/emmanuel/.env`.
- This was deployed as a true shadow-stage change by setting `SHADOW_ASSETS=BTC`, so BTC signals are observed but not executed during the experiment window.
- Updated config values:
  - `MAX_ENTRY_PRICE=0.80` (was `0.75`)
  - `WHIPSAW_MAX_RATIO=0.20` (was `0.50`)
  - `MOMENTUM_MAX_EPOCH_ELAPSED_SECS=90` (was `60`)
  - `SHADOW_ASSETS=BTC` (new for the stage)
- Procedure:
  - backed up `.env`
  - stopped `lagbot@emmanuel`
  - updated `.env`
  - cleared `__pycache__`
  - ran `py_compile` for core momentum/guard files from `/tmp`
  - restarted `lagbot@emmanuel`
- Verified live:
  - `systemctl is-active` => `active`
  - startup log confirms `Signal mode: Binance momentum (primary) | shadow=['BTC']`
  - dashboard recovered on `127.0.0.1:8082`
  - preflight checks passed
- Important caveat:
  - startup still reports `CLOB↔DB trade audit FAILED: CRITICAL: CLOB has 1 trades in 24h but DB has 0`
  - this remains a separate accounting/integrity issue and is not caused by the guard rollout

## 2026-03-11 `polyphemus` Converted To BTC 5m Shadow Research

- `polyphemus` was not stopped; it was running an ETH/XRP-focused config.
- Reconfigured `/opt/lagbot/instances/polyphemus/.env` to a BTC 5m shadow research role.
- Updated config values:
  - `ASSET_FILTER=BTC`
  - `SHADOW_ASSETS=BTC`
  - `MARKET_WINDOW_SECS=300`
  - `MAX_ENTRY_PRICE=0.80`
  - `MOMENTUM_MAX_EPOCH_ELAPSED_SECS=90`
  - `WHIPSAW_MAX_RATIO=0.20`
  - `ENABLE_BTC5M_EVIDENCE_VERDICTS=true`
  - `BTC5M_EVIDENCE_MODE=shadow`
  - `BTC5M_ENTRY_RETRY_ENABLED=true`
  - `BTC5M_ENTRY_RETRY_MODE=shadow`
- Procedure:
  - backed up `.env`
  - stopped `lagbot@polyphemus`
  - updated `.env`
  - cleared `__pycache__`
  - ran `py_compile` for core momentum/guard/dashboard/executor files from `/tmp`
  - restarted `lagbot@polyphemus`
- Verified live:
  - `systemctl is-active` => `active`
  - startup log confirms `Signal mode: Binance momentum (primary) | shadow=['BTC']`
  - dashboard recovered on `127.0.0.1:8083`
  - preflight checks passed
  - CLOB↔DB trade audit is currently clean on startup for `polyphemus` (`CLOB=0, DB=0` in last 3h)

## 2026-03-11 High-Risk Audit Fixes

- Audited the highest-risk local assumptions in execution sizing and startup data integrity checks.
- Fixed `position_executor.py` so `up_direction_size_mult` keys off the actual trade outcome (`Up`/`Down`) instead of `signal["direction"]`, which is normally just `BUY`.
- Fixed `balance_manager.py` trade reconciliation so recent CLOB order ids are matched against both recent DB entry hashes and recent DB exit hashes, avoiding false CRITICAL halts when a recent exit exists without a recent entry.
- Added focused regression coverage in `test_modules.py` for:
  - Up-direction sizing reduction
  - recent-exit reconciliation pass
  - unmatched recent CLOB order still halting as intended
- Verification:
  - `py_compile` passed for `balance_manager.py`, `position_executor.py`, and `test_modules.py`
  - targeted tests were executed directly under Python and passed

## 2026-03-12 Signal Pipeline Refactor

- Added `signal_pipeline.py` to normalize raw feed signals into a canonical shape before guard/scoring/execution.
- Normalization now derives and records:
  - `asset` from slug when missing
  - `market_window_secs` from slug when missing
  - `time_remaining_secs` from slug/epoch when missing
  - `noise_flags` when fields were derived or clamped
- Hardened `signal_logger.py`:
  - schema-aware filtering of logged fields
  - support for `log_signal(..., guard_passed=True, ...)` style callers
  - additive pipeline inspection columns: `pipeline_stage`, `pipeline_status`, `pipeline_detail`, `noise_flags`
- Refactored `SignalBot._on_signal` to use explicit stages:
  - normalization/context enrichment
  - guard
  - momentum confirmation
  - regime check
  - score
  - circuit/balance/sizing
  - dry-run or execution
- Added stage logging for direct feed-side logger writes in `binance_momentum.py` and `reversal_short` dry runs.
- Extended dashboard pipeline summary to surface stage-mix counts when the schema is available, while remaining backward-compatible with older `signals.db` files.
- Added focused tests in `tests/test_signal_pipeline.py` for:
  - normalization and derived/noise fields
  - logger override/schema filtering
  - guard-stage persistence from `SignalBot._on_signal`
- Verification:
  - `python3 -m py_compile` passed for `signal_pipeline.py`, `signal_logger.py`, `signal_bot.py`, `binance_momentum.py`, and `dashboard.py`
  - targeted direct Python execution passed for:
    - `tests/test_signal_pipeline.py`
    - `test_up_direction_size_multiplier_uses_outcome`
    - `test_reconcile_trades_matches_recent_exit_hashes`
    - `test_reconcile_trades_halts_on_unmatched_recent_clob_order`
    - `test_pipeline_summary_includes_retry_counters`
  - `pytest` still does not return usable output in this harness, so verification was done via direct targeted invocation

## 2026-03-12 Multi-Strategy Shadow Scan

- Downloaded fresh `signals.db` / `performance.db` snapshots for:
  - `emmanuel`
  - `polyphemus`
- Added `tools/strategy_shadow_scan.py` to:
  - load BTC 5m signal candidates from both instances
  - resolve actual market winners from Gamma by slug
  - simulate multiple strategy profiles on the same epoch set
  - rank them by win rate, gross PnL, ROI on deployed cost, and drawdown
- Added a resolution cache at `tools/.backtest_cache/btc5m_resolution_cache.json` and fixed two bugs during verification:
  - generator reuse in outcome resolution returned an empty result set
  - Gamma `outcomes` parsing needed JSON decoding, not raw string iteration
- Generated fresh reports:
  - `dario_output/btc5m_strategy_shadow_scan_20260312_081516.md`
  - `dario_output/btc5m_strategy_shadow_scan_20260312_081720.md`
- Current 14-day headline results from the final report:
  - `discount_window_delta`: best ROI (`43.7%`) but only `20` trades, still anecdotal
  - `ensemble_ranked`: best scalable profile so far (`134` trades, `72.4%` WR, `10.3%` gross ROI)
  - `current_guarded_shadow`: viable but weaker than ensemble (`92` trades, `68.5%` WR, `5.6%` gross ROI)
  - `late_certainty_snipe`: very high win rate but nearly zero ROI because entries are too expensive
- Dataset gaps still blocking stronger confidence:
  - no `epoch_coverage`
  - no fee/slippage/fill modeling for shadow candidates
  - no opposite-side price history for fair side-flip simulation
  - incomplete config-era tagging
- Verification:
  - `python3 -m py_compile` passed for `tools/strategy_shadow_scan.py`
  - fresh report generation completed successfully on the latest downloaded snapshots

## 2026-03-12 Weather Shadow Forward Collector

- Added standalone forward weather shadow collector:
  - `tools/weather_shadow_forward.py`
- Purpose:
  - scan live Polymarket weather markets for the tracked city set
  - record per-profile observations to SQLite
  - open simulated shadow positions when thresholds pass
  - resolve those positions later against final market winners
- Storage:
  - `data/weather_shadow_forward.db`
- Reporting artifacts:
  - `dario_output/weather_shadow_forward_20260312.md`
  - `dario_output/weather_shadow_forward_20260312.json`
  - `dario_output/weather_shadow_forward_20260312_wide.md`
  - `dario_output/weather_shadow_forward_20260312_wide.json`
- Verification:
  - standalone tools compile cleanly when run from the parent directory:
    - `python3 -m py_compile polyphemus/tools/weather_shadow_replay.py polyphemus/tools/weather_shadow_forward.py`
  - `weather_shadow_forward.py all` completed cleanly
  - widened scan with `--future-days 10` also completed cleanly
- Current result:
  - no active weather markets were returned for the tracked cities in the scanned forward window
  - `scan_observations=0`
  - `shadow_positions=0`
- Limitation:
  - local `python3 -m py_compile` from the repo root still trips the known `types.py` stdlib shadowing issue, so standalone verification must be run from the parent directory.

## 2026-03-12 BTC 5m Ensemble Go-Live Gate

- Added a formal offline go-live gate evaluator:
  - `tools/btc5m_ensemble_go_live_gate.py`
- Purpose:
  - detect the latest shared post-fix `config_era` for `btc5m_shadow_lab_v3`
  - compute per-instance BTC 5m epoch coverage / signal / ensemble counts
  - compare `ensemble_selected_live_v1` vs `current_guarded`
  - scale drawdown and rolling-loss metrics to current live hard caps from cached `emmanuel` `.env`
  - emit a decision report with explicit `GO` / `NO-GO` blockers
- Added package support for tool imports:
  - `tools/__init__.py`
- Extended `tools/strategy_shadow_scan.py` so candidates now carry:
  - `shadow_current_guarded`
  - `shadow_ensemble_candidate`
  - `shadow_ensemble_selected`
  - plus replay-side net PnL series / cost series for scaled risk checks
- Added a future live-admission hook in:
  - `config.py`
  - `signal_bot.py`
- New config flags:
  - `BTC5M_ENSEMBLE_ADMISSION_ENABLED`
  - `BTC5M_ENSEMBLE_ADMISSION_MODE`
- Behavior:
  - disabled by default
  - when enabled later, only `BTC` `5m` `binance_momentum` signals tagged `shadow_ensemble_selected=1` can proceed
  - non-selected rows are marked `ensemble_filtered`
- Added focused tests:
  - `tests/test_btc5m_go_live_gate.py`
  - extended `tests/test_signal_pipeline.py` with ensemble-admission coverage
- Verification:
  - `python3 -m py_compile` passed for:
    - `config.py`
    - `signal_bot.py`
    - `tools/__init__.py`
    - `tools/strategy_shadow_scan.py`
    - `tools/btc5m_ensemble_go_live_gate.py`
    - `tests/test_signal_pipeline.py`
    - `tests/test_btc5m_go_live_gate.py`
  - targeted direct Python execution passed for:
    - gate threshold tests
    - normalization / logger tests
    - guard-stage logging
    - ensemble reselection
    - ensemble-admission filtering
  - `pytest` still does not return usable output in this harness, so verification used direct targeted invocation from the parent workspace
- First gate report generated:
  - `dario_output/btc5m_ensemble_go_live_gate_20260312_164254.md`
- Current result:
  - `NO-GO`
  - blocker: local cached snapshots do not yet contain a shared post-fix aligned `config_era` for both instances under `btc5m_shadow_lab_v3`

## 2026-03-12 Polyphemus Operator Desktop App

- Built an ADHD-friendly operator workflow on top of the existing Next.js dashboard in:
  - `../bot-dashboard/frontend/src/app/page.tsx`
  - `../bot-dashboard/frontend/src/app/overview/page.tsx`
  - `../bot-dashboard/frontend/src/components/overview-dashboard.tsx`
  - `../bot-dashboard/frontend/src/components/nav.tsx`
  - `../bot-dashboard/frontend/src/lib/desktop.ts`
  - `../bot-dashboard/frontend/electron/main.js`
  - `../bot-dashboard/frontend/electron/preload.js`
- Added structured helper tools for the desktop app:
  - `tools/refresh_vps_caches.py`
  - `tools/shadow_window_status.py`
  - `tools/shadow_window_checklist.py`
  - `tools/emmanuel_audit_mismatch_check.py`
  - extended `tools/btc5m_ensemble_go_live_gate.py` with JSON output
- Desktop workflow now supports:
  - refresh VPS caches
  - inspect shadow-window maturity
  - inspect the `emmanuel` CLOB↔DB mismatch
  - run the ensemble go-live gate
  - open recent reports from `dario_output`
- Added Electron packaging via `electron-builder` in `../bot-dashboard/frontend/package.json`
- Verification:
  - `python3 -m py_compile` passed for the new tool scripts from the parent workspace
  - `shadow_window_status.py --print-json` returned structured output
  - `shadow_window_checklist.py --print-json` returned structured output
  - `btc5m_ensemble_go_live_gate.py --print-json` returned structured output
  - `npm run build` passed in `../bot-dashboard/frontend`
  - `npm run desktop:pack` produced a packaged macOS `.app` bundle under `../bot-dashboard/frontend/dist/mac-arm64`
  - `npm run desktop:build` produced:
    - `../bot-dashboard/frontend/dist/Polyphemus Operator-0.1.0-arm64.dmg`
    - `../bot-dashboard/frontend/dist/Polyphemus Operator-0.1.0-arm64-mac.zip`
- Current limitation:
  - the audit helper reports `unknown` when SSH credentials to `82.24.19.114` are unavailable locally, and the UI now surfaces that as a blocker rather than a clean pass
