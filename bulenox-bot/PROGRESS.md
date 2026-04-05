# Bulenox Bot Progress

## Apr 4 2026 - Exit Management Overhaul + Payout Roadmap

### Current State
- **16 closed dry-run trades** (unchanged - bot has not traded since Apr 2)
- **DRY_RUN=true** / paper creds still pending (BX97517)
- **New config deployed locally** - trailing stop + lower breakeven
- **Backtest validated** on 136,811 1m bars

### What Shipped This Session

#### Root Cause Analysis: Why UP EV Was $1.71/trade
- Commission drag: $6.52/trade = 79% of gross EV ($8.23)
- Wins exiting on 15-min time stop, NOT TP - avg exit 22-40 ticks vs TP at 50 ticks
- LOSS 2 reached 11 ticks favorable before SL hit - missed breakeven (was 15t) by 3.9t
- Breakeven threshold was identical to SL (15t = 75pts) - too late to protect near-wins

#### Backtest (backtest_params.py, 136,811 1m bars, UP-only)
| Config | WR | EV/trade | Total |
|--------|-----|----------|-------|
| Baseline be=15t, no trail | 18.4% | -$5.06 | -$440 |
| be=8t, trail=OFF | 18.4% | -$4.20 | -$365 |
| **be=8t, trail=15t@20t** | **57.5%** | **+$7.90** | **+$687** |
| be=10t, trail=15t@20t | 57.5% | +$7.64 | +$665 |

Trailing stop is the dominant lever - WR jumps 18.4% -> 57.5% by locking in gains before reversal.

#### Code Changes
- `config.py`: added `breakeven_ticks=10` (default), `trailing_stop_ticks=0` (default), `trailing_activation_ticks=20`
- `bulenox_bot.py`: breakeven threshold now uses `breakeven_ticks` (not `stop_loss_ticks`); added trailing stop logic in `_on_tick` using `Position.trailing_peak`
- `bulenox_bot.py`: added `trailing_peak: float = 0.0` field to `Position` class
- `.env`: `BREAKEVEN_TICKS=8`, `TRAILING_STOP_TICKS=15`, `TRAILING_ACTIVATION_TICKS=20`
- `backtest_params.py`: NEW - parameter grid backtester on btc_1m_history.db

#### Payout Roadmap (Bulenox $50K, verified from bulenox.com)
- **Pass eval**: earn $3,000 on sim account (no time limit)
- **Consistency rule**: no single day > 40% of total profit (internal cap 35%)
- **Min 10 trading days** before first withdrawal
- **Master Account fee**: $148 one-time
- **Payouts 1-3**: min $1,000 / max $1,500 each
- **Payout 4+**: unlimited, processed Wednesdays
- **Profit split**: first $10K = 100% yours, above $10K = 90%

#### Timeline to $3,000 target (be=8t, trail=15t@20t, 1.68 signals/week)
| Contracts | Weeks to $3K |
|-----------|-------------|
| 1 | 226 wks - not viable |
| 3 | 75 wks |
| 5 | 45 wks - minimum viable |
| 7 (max) | 32 wks |

### Blockers
- Paper creds (BX97517) - still pending. Everything gates on this.
- Trailing stop not yet deployed to VPS (config only deployed locally)
- Backtest WR (57.5%) needs live confirmation before scaling contracts

### Next Steps (ordered)
1. **[P0]** Chase BX97517 with Bulenox support
2. **[P0]** Once paper creds arrive: deploy new config to VPS (predeploy.sh)
3. **[P1]** Collect n=20 dry-run trades with trailing stop active - validate WR > 34.4%
4. **[P1]** Switch DRY_RUN=false on paper account, collect n=20 paper fills (Gate 3)
5. **[P2]** If WR > 34.4% on paper fills: scale to 3 contracts
6. **[P2]** Track daily/cumulative profit ratio - keep below 35% (consistency rule)
7. **[P3]** At n=60 live trades + WR confirmed: scale to 5 contracts
8. **[P3]** Stay at 1-2c until cumulative > $300 to avoid consistency trap

### Config Changelog (continued)
| Date | Key | Before | After | Reason |
|------|-----|--------|-------|--------|
| Apr 4 | BREAKEVEN_TICKS | 15 (implicit) | 8 | Earlier SL protection, reduces avg loss |
| Apr 4 | TRAILING_STOP_TICKS | 0 (N/A) | 15 | Trail 15t from peak after activation |
| Apr 4 | TRAILING_ACTIVATION_TICKS | 0 (N/A) | 20 | Activate trail after 20t in profit |

---

## Apr 2 2026 Quant Factory Stage 0

### What Shipped
- Added `research_store.py` with Stage 0 research tables in `data/research.db`
- Added `quant_factory_backfill.py` to initialize/backfill:
  - `feature_snapshots`
  - `execution_events`
  - baseline hypothesis `H-baseline-b0`
  - baseline run `B0`
  - `gate_verdicts`
  - `data/quant_factory/status.json`, `gates.json`, `experiments.jsonl`, `agent_runs.jsonl`
- Wired live signal snapshot and execution-event logging into `bulenox_bot.py`
- Added dashboard API endpoint: `/api/futures/quant-factory?bot=bulenox`
- Added localhost Bulenox dashboard panels:
  - `Quant Factory Stage`
  - `Experiment Queue`
  - `Active Agents & Processes`

### VPS Verification
- Deployed bot changes to `/opt/bulenox/bulenox-bot/`
- Rebuilt live research DB from VPS data:
  - `signals=128`
  - `feature_snapshots=128`
  - `execution_events=42`
- Restarted `bulenox` successfully
- Deployed dashboard API and static Bulenox page to `/opt/dashboard`
- Restarted `dashboard` successfully
- Verified localhost tunnel endpoint returns live Quant Factory payload

### Current Factory State
- `stage`: `stage_1_baseline_freeze`
- `verdict`: `collecting`
- `baseline_run_id`: `B0`
- `Stage 0 instrumentation`: `pass`
- Current blockers:
  - `sample_size_lt_30`
  - `net_after_costs_negative`
  - `down_side_weak`

## Apr 2 2026 Checkpoint

### Live Verification
- **Service**: `bulenox` active on VPS after restart at 18:56 CEST / 11:56 CDT
- **Trades**: 16 closed total, 6W / 10L, WR 37.5%, gross +211.87 pts
- **Directional split**:
  - `UP`: 5 trades, 3W / 2L, WR 60.0%, +411.43 pts
  - `DOWN`: 11 trades, 3W / 8L, WR 27.3%, -199.56 pts
- **Breakeven stop**: confirmed active in live logs on trade `DRY-f4a533f9` at 17:24:03 CDT after +75.2 pts excursion
- **Directional gate**: confirmed active in live logs, blocking `DOWN` signals at 27% WR on last 11 `DOWN` trades
- **ATR regime filter**: confirmed active in live logs, blocking fade-against-UP-trend signals with ratios from 1.56x to 3.55x

### Analytics Fix
- Fixed shadow-trade accounting bug in `bulenox_bot.py`: short-side TP shadow wins were being written with negative `pnl_pts`
- Backed up VPS DB to `/opt/bulenox/bulenox-bot/data/trades.db.bak.1775148960`
- Corrected 13 historical shadow rows on VPS
- Post-fix shadow evidence:
  - `atr_regime`: 10 samples, 50.0% WR, +509.81 pts total
  - `directional_gate`: 3 samples, 66.7% WR, +425.0 pts total
  - `trend_filter`: 2 samples, 50.0% WR, +175.0 pts total
  - `session_filter`: 89 samples, 25.8% WR, -244.88 pts total

### Interpretation
- The bot is still not ready for live trading. `DOWN` remains structurally weak and paper sample size is only **16/50**.
- The extra regime-classifier idea is still not justified yet. ATR and directional gating are already removing the worst fade-against-trend setups.
- The next useful threshold is not “go live,” it is reaching **n=30** with more post-breakeven trades, then reassessing stop behavior and shadow-filter value.

## Apr 2 2026 Quant Factory Experiments E1 / E5 / E2

### Authoritative Data Source
- Local Bulenox DB copies were stale and did not match the handoff state.
- Quant conclusions in this checkpoint use the authoritative VPS state from:
  - `/opt/bulenox/bulenox-bot/data/trades.db`
  - `/opt/bulenox/bulenox-bot/data/research.db`
  - `/opt/bulenox/bulenox-bot/data/quant_factory/status.json`

### Frozen Results
- Report artifact written:
  - `dario_output/bulenox_quant_factory_e1_e5_e2_20260402.md`
- Next experiment scaffolded:
  - `.omc/experiments/btc-up-only-fade-baseline-v1/hypothesis.md`
  - `.omc/experiments/btc-up-only-fade-baseline-v1/evidence_log.md`
  - `.omc/experiments/btc-up-only-fade-baseline-v1/promotion_review.md`
  - `.omc/experiments/btc-up-only-fade-baseline-v1/meta.json`

### E1 Directional Asymmetry
- Baseline directional split from VPS:
  - `UP`: `5` trades, `3W / 2L`, `60.0%` WR, gross `+$41.14`, net `+$8.54`
  - `DOWN`: `11` trades, `3W / 8L`, `27.3%` WR, gross `-$19.96`, net `-$91.68`
- Provisional verdict:
  - `DOWN` should be structurally gated off

### E5 Cost Stress
- Baseline broad system fails all tested cost assumptions:
  - baseline cost model: net `-$83.13`
  - `+1 tick` stress: net `-$99.13`
  - `+2 ticks` stress: net `-$115.13`
- `UP` survives baseline and `+1 tick`, but fails at `+2 ticks`
- Broad baseline thesis is rejected on economics

### E2 Regime Pause Counterfactual
- Tested pause-style rejects:
  - `atr_regime`
  - `trend_filter`
  - `whipsaw`
- If those paused trades had been taken, net would worsen from `-$83.13` to `-$130.49`
- Regime pause is helpful damage control, but not enough to rescue the broad strategy

### Linkage Quality Note
- `14 / 16` closed trades link to feature snapshots
- `2 / 16` unlinked trades are early `2026-03-23` `DOWN` trades that predate the earliest retained signal rows
- Current read: historical artifact, not evidence of an ongoing linkage bug

### New Provisional Thesis
- Stop treating the broad Bulenox fade system as the candidate thesis
- The only surviving narrow candidate is:
  - `BTC`
  - `UP-only`
  - current protective filters kept on
- This slice is still anecdotal (`n=5`) and remains `NO-GO` until it reaches at least `n >= 30` with positive expectancy after costs

### Next Highest-Leverage Step
1. Continue collecting evidence for `.omc/experiments/btc-up-only-fade-baseline-v1`
2. Keep `DOWN` effectively gated off in research interpretation
3. Re-run expectancy after costs once `UP-only` reaches `n >= 30`
4. Do not broaden to live/paper promotion without a clean cost-surviving sample

### Local/VPS Reconciliation
- Created local backups before overwrite in:
  - `data/reconcile_backup_20260402_164514/`
- Reconciled local Bulenox research state to match VPS truth:
  - `data/research.db`
  - `data/trades.db`
  - `data/quant_factory/status.json`
  - `data/quant_factory/gates.json`
  - `data/quant_factory/experiments.jsonl`
  - `data/quant_factory/agent_runs.jsonl`
  - root-level `research.db` replaced with a copy of reconciled `data/research.db`
- Verified reconciled local state now matches VPS baseline:
  - `feature_snapshots=128`
  - `execution_events=42`
  - `closed_trades=16`
  - `signals=128`
  - `B0 net=-$83.13`
  - `UP WR=60.0%`
  - `DOWN WR=27.3%`
- This removes the stale-local-state mismatch that originally blocked trustworthy local quant reads

### One-Command Refresh Path
- Added local refresh command:
  - `python3 refresh_up_only_experiment.py --print-json`
- What it does:
  - creates a fresh local backup under `data/reconcile_backup_<timestamp>/`
  - syncs authoritative VPS artifacts into local Bulenox state
  - refreshes `.omc/experiments/btc-up-only-fade-baseline-v1/evidence_log.md`
  - refreshes `.omc/experiments/btc-up-only-fade-baseline-v1/promotion_review.md`
  - writes `.omc/experiments/btc-up-only-fade-baseline-v1/current_status.json`
- Verified run succeeded at:
  - `2026-04-03T00:16:17Z`
- Verified refreshed UP-only read remained:
  - `n=5`
  - `WR=60.0%`
  - `baseline=+$8.54`
  - `+1 tick=+$3.54`
  - `+2 ticks=-$1.46`

### Smoke-Test Note
- Pre-existing pytest collection blocker remains in this repo:
  - `test_login_v2.py`
  - `test_order_lifecycle.py`
- Cause:
  - protobuf generated files are incompatible with the current local Python/protobuf runtime
- This blocker predated the refresh command work and was not changed here

## Current State (Mar 27 2026)

### Bot Status
- **Running on VPS** (82.24.19.114, systemd service `bulenox`)
- **Config**: FADE 0.3% / TP=50 / SL=15 / 15min hold / FADE window 09:00-16:00 CT
- **Symbol**: MBTJ6 (April contract, auto-rolled)
- **Trades**: 10 total (3W/7L), WR 30.0%, net P&L: -$82.64
- **Dry run**: DRY_RUN=true
- **Drawdown room**: $2,500 / $2,500 (safe)
- **BTC price**: ~$65,800 (down from $71,500 on Mar 23)

### Directional Bias (CRITICAL FINDING)
- UP entries: 3W/0L (100% WR) - every fade-down (enter long) wins
- DOWN entries: 0W/7L (0% WR) - every fade-up (enter short) loses
- BTC has fallen $5,700 in 4 days. DOWN entries fight the trend.
- Directional gate will block DOWN at n>=10 if WR stays < 30% (needs 3 more DOWN trades)

### What Was Built This Session (Mar 25-27)

#### /dario DEEP Research (10 domains, 28 searches, 34 sources)
- 4 parallel research agents: microstructure, seasonality, basis risk, half-life, adaptive params, correlated RoR, qualification math, rollover, circuit breakers, Rithmic API
- Report: `dario_output/dario_bulenox_quant_expertise_gaps_20260325.md`

#### /bulenox-quant Skill v1.0 -> v2.1
- 602 lines, canonical XML format (all 7 standard tags)
- 22 gotchas, 31 cross-metric permutations, 9 operations, 10 best practices
- New sections: microstructure, seasonality, basis risk, half-life, adaptive params, correlated RoR, override rules, crowded trades, regime transitions, loss psychology, counterfactual tracking, MBT order book

#### Bot Code Improvements (all deployed to VPS)
- Session time filter (09:00-16:00 CT) - blocks off-hours signals
- Basis monitoring (MBT vs Coinbase, 2% threshold)
- Strengthened trend filter (4/5 from 3/3)
- Auto-rollover DRY_RUN fix
- Counterfactual shadow trades (records what rejected signals WOULD have done)
- Directional gate (blocks direction if WR < 30% on n>=10)
- Extreme event cooldown (3% move = 30 min pause)
- Whipsaw guard (opposite-direction signals within 5 min = skip)
- Threshold lowered from 0.5% to 0.3% (matches best backtest economics)

#### New Scripts
- `research_metrics.py` - Hurst exponent, OU half-life, rolling stability (CV), ATR-adaptive TP/SL, Monte Carlo Markov qualification sim

#### Research Findings
- Hurst at 5m: 0.50 (borderline random walk)
- Half-life stability: CV=0.87 (UNRELIABLE - varies 22-155 min across windows)
- ATR says SL should be 25 (current 15 is too tight, avg MAE=-18 confirms)
- Monte Carlo at current WR: 0% qualification, 100% bust (expected at 30% WR)
- Prop firm pass rate: 5-15% industry-wide

### Key Discoveries (new this session)
1. Standard RoR underestimates by 3-5x with loss clustering (ACF=0.401)
2. 15-min hold may be in the right ballpark but half-life is too unstable to optimize from
3. Session filter correctly blocking off-hours signals (verified in logs)
4. DOWN entries 0/7 = BTC downtrend makes fade-up entries systematically lose
5. ATR regime filter was NEVER IMPLEMENTED in bot code (knowledge-base only)
6. Whipsaw pattern (opposite signals 64s apart) caused 2 losses in one minute

### Blockers
- Bulenox Paper Trading creds (BX97517) - helpdesk ticket still open
- ATR regime filter not implemented (biggest remaining code gap)

### Files Changed (since Mar 25)
| File | Changes |
|------|---------|
| `bulenox_bot.py` | +session filter, +basis check, +trend 4/5, +rollover fix, +counterfactual, +directional gate, +extreme event, +whipsaw guard |
| `config.py` | +9 new .env fields (fade window, basis, directional gate, extreme event) |
| `trade_store.py` | +shadow_trades table, +get_directional_wr(), +get_shadow_stats() |
| `research_metrics.py` | NEW - all research metrics |
| `SKILL.md` | v1.0 -> v2.1 (602 lines, canonical format) |

### What Was Added Mar 27 (this session)

#### ATR Regime Filter (P0 gap closed)
- `binance_feed.py`: 1h rolling price deque, 24-bucket hourly range deque, `get_atr_ratio()`, `get_trend_direction()`
- `bulenox_bot.py`: directional ATR check after basis check — blocks fades AGAINST the 1h trend only
- `config.py`: `ATR_REGIME_THRESHOLD=1.5` (env-configurable)
- Logic: `atr_1h / median(hourly_ranges[-24])`. Activates after 4h of baseline data.
- Directional (surgical): UP fade in DOWN trend = blocked. DOWN fade in DOWN trend = allowed (fading against a dip = with trend = wins).
- Simulation confirmed: 3.54x ratio on current BTC data, trend=DOWN, blocks UP fade correctly.

### Next Session: What to Do

#### P0 - Immediate
1. **Check Bulenox helpdesk** for Paper Trading credential response
2. **Check counterfactual data** - run `SELECT * FROM shadow_trades` to see if filters are proving their value
3. **Deploy to VPS** - ATR regime filter needs to be deployed: `./predeploy.sh --deploy bulenox` (or manual scp)

#### P1 - After n=15
4. **Check directional gate** - at n=10 DOWN trades, gate should auto-block if WR < 30%
5. **Run research_metrics.py** on VPS - Monte Carlo unlocks at n=10 (already there)
6. **Evaluate SL width** - ATR says 25, we're at 15, avg MAE confirms stops too tight. But don't change during loss streak (best practice #1). Wait for n=30.

#### P2 - After n=30
7. Run `/proof-of-edge:check` hypothesis tests
8. Compute reliable Markov transition matrix
9. Monte Carlo qualification probability (gate: must be > 60%)
10. If WR recovers to 50%+: consider SL widening to 20-22

#### P3 - After n=50
11. Run `/proof-of-edge:verdict` for GO/NO-GO on continuing
12. Session window sweep (validate 09-16 vs other windows)
13. Threshold sweep (0.3% vs 0.4% vs 0.5%)

### Config Changelog
| Date | Key | Before | After | Reason |
|------|-----|--------|-------|--------|
| Mar 26 | MOMENTUM_TRIGGER_PCT | 0.005 | 0.003 | Best backtest economics (Kelly +4.8%) |
| Mar 26 | FADE_START_CT (default) | 10:00 | 09:00 | Signal volume, user approved |
| Mar 26 | FADE_END_CT (default) | 15:00 | 16:00 | Signal volume, user approved |
| Mar 27 | config.py load_config | 10:00/15:00 fallback | 09:00/16:00 fallback | Bug fix (defaults didn't match) |
| Mar 27 | whipsaw guard | N/A | 300s | Prevent opposite-direction chop losses |
