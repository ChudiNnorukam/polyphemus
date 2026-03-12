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
