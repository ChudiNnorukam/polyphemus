# Shadow-Debt Scan Log

Append-only. Each scan adds a dated section. Findings route to `/librarian`, `/custodian`, or operator per catalog. No item in this file has been auto-executed by shadow-debt — execution requires operator keystroke.

---

## 2026-04-23 — Polyphemus XRP FAK Shadow Resurrect Scan

**Scope:** current (polyphemus repo + VPS live-state verification).
**Trigger:** post-exec review after rewriting `lagbot@polyphemus` drop-in from momentum+cheap-side back to FAK accumulator shadow.
**State:** Four new findings surfaced from reconciling VPS-live-state against repo docs and MEMORY.md. None shadow-debt-executable; all flag-only by safety rule.

### Findings

#### 1. AUTH-CONFLICT — emmanuel strategy

- **Docs side (MEMORY.md, "Lagbot Strategy — BTC 5m Latency Arb"):**
  `Emmanuel (sig_type=1): ENTRY_MODE=taker, BASE_BET_PCT=0.05, MAX_BET=100, HOLD_TO_RESOLUTION=true`
- **Live drop-in (`/etc/systemd/system/lagbot@emmanuel.service.d/dry_run.conf`):**
  `DRY_RUN=true ENABLE_ACCUMULATOR=false ENABLE_SHARP_MOVE=true SHARP_MOVE_MAX_ENTRY_PRICE=0.95 ADVERSE_PRECHECK_SECS=15 ADVERSE_PRECHECK_THRESHOLD=0.0003 CHEAP_SIDE_ENABLED=false MAX_TRADE_AMOUNT=5 MIN_BET=3 MARKOV_GATE_MAX_LOSSES=3`
- **Conflict:** the docs describe a latency-arb taker posture. The live drop-in is running a sharp-move entry strategy with an adverse pre-check. These are not the same strategy.
- **Disposition:** flag only. Operator picks which is canonical; skill cannot auto-resolve AUTH docs.
- **Routes to:** operator.

#### 2. SHADOW — hourly snapshot collector has env-layer blind spot

- **Observable signal:** every row in `data/shadow_eval/polyphemus/hourly_snapshots.jsonl` has `config_debug.service_env` all-null; `config_debug.env_file` is populated.
- **Consequence:** the collector reported `accum_entry_mode=maker` for weeks while the drop-in had `ENABLE_ACCUMULATOR=false`. Systemd drop-in env overrides are invisible to downstream observability.
- **Disposition:** flag only. Modifying observability code is outside shadow-debt's catalog.
- **Routes to:** operator (code fix candidate).

#### 3. ORPHAN — missing `config_expected.json` at `/opt/lagbot/lagbot/config_expected.json`

- **Observable signal:** every `lagbot@polyphemus` startup logs `[startup_check] config_expected.json not found at /opt/lagbot/lagbot/config_expected.json — snapshot checks skipped` followed by `Config drift detected (non-critical)`.
- **Consequence:** pre-deploy config reconciliation cannot run; the "drift detected" warning is benign but noisy, and masks any real drift that would otherwise surface.
- **Disposition:** flag only. Either ship the expected-config file or silence the warning — operator call.
- **Routes to:** operator (code or file-deploy).

#### 4. ORPHAN — `ACCUM_ENTRY_MODE=maker` in polyphemus `.env`

- **Observable signal:** `/opt/lagbot/instances/polyphemus/.env` contains `ACCUM_ENTRY_MODE=maker`. The drop-in now sets `ACCUM_ENTRY_MODE=fak` as canonical.
- **Consequence:** with the drop-in as source of truth, the `.env` value is dead; if the drop-in is ever disabled without an .env update, accumulator quietly falls back to maker without an error.
- **Disposition:** flag only. Shadow-debt does not touch `.env`.
- **Routes to:** `/custodian` (.env hygiene).

### Priority

Per STEP 4 priority rules: **AUTH-CONFLICT (#1) > SHADOW (#2) > ORPHAN (#3, #4)**. Highest-leverage next action is operator ratifying which emmanuel description is canonical, so MEMORY.md either updates or the drop-in flips back.

### Carry-over from 2026-04-23 00:30 PST scan

| Finding | Category | Verdict | Notes |
|---|---|---|---|
| `SHADOW_DEPLOY_CHECKLIST.md` | SUPERSEDED | RATIFIED | Header added; preserved for revival context. |
| `BTC5M_BOT_IMPROVEMENT_PLAN.md` | SUPERSEDED | DEFERRED | Reporting infra ambiguity unresolved; revisit after Phase 2 pwin-estimator work lands. |
| `dario_output/` | ACCUMULATOR | DEFERRED | No prune policy yet; 60 files, no operator-facing cost. |
| `AGENTS.md` + `CLAUDE.md` | (dual harness adapter) | DISMISSED | Shared contract; both are legitimate by design. |
