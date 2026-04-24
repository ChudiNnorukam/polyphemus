import hashlib
import json
import logging
from pathlib import Path
from typing import List

from dotenv import load_dotenv
try:
    from pydantic_settings import BaseSettings
except ImportError:
    from pydantic import BaseSettings
from pydantic import field_validator


ENV_PATH = Path(__file__).parent / '.env'
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)


class Settings(BaseSettings):
    private_key: str
    wallet_address: str
    clob_api_key: str
    clob_secret: str
    clob_passphrase: str
    builder_api_key: str
    builder_secret: str
    builder_passphrase: str

    # Relayer API Key auth (simpler than builder HMAC, preferred when available)
    relayer_api_key: str = ""
    relayer_api_key_address: str = ""

    polygon_chain_id: int = 137
    polygon_rpc_url: str

    min_entry_price: float = 0.72
    max_entry_price: float = 0.85
    entry_trap_low: float = 0.0   # Trap zone: skip midpoints between trap_low and trap_high
    entry_trap_high: float = 0.0  # Set both >0 to enable (e.g. 0.60/0.80)
    asset_filter: str = ""  # Allow-list: only trade these assets (comma-separated, e.g. "ETH")
    shadow_assets: str = ""  # Log signals but don't execute (comma-separated, e.g. "ETH,SOL,XRP")
    companion_assets: str = ""  # Follow lead asset direction only (e.g. "ETH" follows BTC trigger, no independent firing)
    blocked_assets: str = ""
    blackout_hours: str = ""
    max_open_positions: int = 3
    min_db_signal_size: float = 15.0

    profit_target_pct: float = 0.20
    time_exit_buffer_mins: int = 5
    max_hold_mins: int = 12
    stop_loss_pct: float = 0.15
    enable_stop_loss: bool = False      # Data: 0% WR, -$240 P&L across 39 trades
    enable_sell_signal_exit: bool = False  # Data: 40.5% WR, -$116 P&L across 153 trades

    base_bet_pct: float = 0.10
    high_confidence_threshold: float = 0.0   # price >= this uses high_confidence_bet_pct; 0=disabled
    high_confidence_bet_pct: float = 0.0     # 0=disabled (falls back to base_bet_pct)

    # Kelly-adaptive sizing
    enable_kelly_sizing: bool = False
    kelly_fraction: float = 0.25        # quarter-Kelly (conservative)
    kelly_min_trades: int = 20          # fallback to flat bet if fewer trades in bucket
    kelly_max_fraction: float = 0.20    # hard cap: never bet >20% of capital

    min_bet: float = 5.0
    max_bet: float = 50.0
    auto_max_bet: bool = False
    auto_max_bet_pct: float = 0.05       # 5% of available capital
    auto_max_bet_cap: float = 6500.0     # liquidity ceiling (12.5% of $52K window vol)
    risk_multiplier_min: float = 0.5
    risk_multiplier_max: float = 1.5

    # Per-asset bet sizing multipliers
    asset_multiplier_btc: float = 1.0
    asset_multiplier_eth: float = 1.2
    asset_multiplier_sol: float = 1.2
    asset_multiplier_xrp: float = 0.8  # Conservative — less liquidity than ETH/SOL

    entry_price_scaling: bool = False
    entry_price_anchor: float = 0.80
    entry_price_scale_min: float = 0.5
    entry_price_scale_max: float = 2.0

    low_balance_threshold: float = 10.0
    max_deployment_ratio: float = 0.50

    dashboard_port: int = 8080
    dashboard_host: str = "127.0.0.1"

    enable_binance_confirmation: bool = True
    min_momentum_pct: float = 0.001       # 0.1% threshold for UP/DOWN vs NEUTRAL
    momentum_candles: int = 3             # lookback = 3 closed 1m candles (early entries)
    momentum_candles_late: int = 5        # lookback = 5 closed 1m candles (late entries 6+ min)
    momentum_threshold_early: float = 0.0015   # 0.15% for early entries (0-3 min)
    momentum_threshold_mid: float = 0.001      # 0.10% for mid entries (3-6 min)
    momentum_threshold_late: float = 0.0005    # 0.05% for late entries (6+ min)
    binance_startup_grace_secs: int = 180  # fail-open for first 3 min after start

    signal_feed_mode: str = "polling"  # "polling" (REST API) or "rtds" (WebSocket)
    poll_interval: float = 1.0  # seconds between polls (polling mode only)

    enable_sell_signals: bool = True
    enable_self_tuning: bool = False
    enable_auto_redemption: bool = True
    dry_run: bool = True

    # Arbitrage mode
    enable_arb: bool = False
    arb_assets: str = "BTC,ETH,SOL"
    arb_max_pair_cost: float = 0.980
    arb_min_net_profit_pct: float = 0.005
    arb_max_shares: int = 150
    arb_capital_pct: float = 0.40
    arb_scan_interval: int = 15
    arb_dry_run: bool = True

    # Accumulator mode (time-distributed paired accumulation)
    enable_accumulator: bool = False
    accum_capital_pct: float = 0.40       # % of balance reserved for accumulator
    accum_assets: str = "BTC"             # comma-separated assets
    accum_window_types: str = "5m"        # comma-separated: "5m", "15m", "5m,15m"
    accum_dry_run: bool = True
    accum_entry_mode: str = "maker"       # explicit rollout: default stays legacy maker unless .env opts into fak
    accum_max_pair_cost: float = 0.995    # max pair cost for entry (pure arbitrage)
    accum_min_profit_per_share: float = 0.005  # min $0.005 profit per share
    accum_min_shares: float = 5.0         # minimum order size
    accum_max_shares: float = 500.0       # maximum order size per side
    accum_max_deployed_pct: float = 0.80  # max % of accum capital deployed
    accum_scan_interval: int = 5          # seconds between scan cycles
    accum_min_secs_remaining: int = 180   # skip markets expiring within 3 min
    accum_settle_timeout_secs: int = 120  # max wait for settlement detection
    accum_maker_max_retries: int = 3      # retries per maker order (legacy, unused)
    accum_maker_retry_delay: float = 3.0  # seconds between retries (legacy, unused)
    accum_maker_price_decrement: float = 0.005  # price tick for repricing ($0.5c)
    accum_max_single_side_pct: float = 0.70     # max % exposure on one side
    accum_order_timeout: int = 30         # max seconds before repricing a resting order
    accum_reprice_limit: int = 5          # max reprice attempts before abandoning
    accum_hedge_deadline_secs: int = 120  # cancel unfilled leg N secs after first leg fills
    accum_max_side_price: float = 0.55    # reject markets where either side bid > this (directional guard)
    accum_max_concurrent: int = 1        # max simultaneous accumulator positions
    accum_daily_loss_limit: float = -50.0  # stop accumulator if session PnL drops below this

    # Cross-asset lag signals (fire ETH/SOL signal N secs after BTC momentum)
    enable_lag_signals: bool = False   # default off — enable after Build 1 confirms ETH/SOL WR >= 60%
    lag_assets: str = ""               # format: "ETH:40,SOL:60" — asset:delay_secs pairs
    lag_neutral_band: float = 0.05     # skip if companion midpoint already moved >5c from 0.50

    # Hour-of-day sizing multiplier (requires 200+ trades to calibrate)
    hour_size_weights: str = ""  # CSV: "13:1.2,14:1.2,0:0.8" — only set after WR-by-hour analysis

    # Pair arb (Phase 2 — maker-only pair cost arbitrage on 5m markets)
    enable_pair_arb: bool = False
    pair_arb_dry_run: bool = True              # log only, no orders placed
    pair_arb_max_pair_cost: float = 0.975     # 2.5% margin floor (maker, 0% fee)
    pair_arb_max_concurrent: int = 2           # max simultaneous pair arb slugs
    pair_arb_fill_deadline_secs: int = 120    # cancel unfilled leg after this

    # Pair arb near-resolution: taker-based pair arb in last N seconds before epoch end
    pair_arb_near_res_enabled: bool = False
    pair_arb_near_res_max_secs: int = 45         # start scanning N seconds before epoch end
    pair_arb_near_res_min_secs: int = 8          # stop scanning (need time for taker fill)
    pair_arb_near_res_scan_interval: int = 3     # scan every N seconds in the window
    pair_arb_near_res_max_pair_cost: float = 0.985  # fee-aware threshold (tighter than maker)
    pair_arb_near_res_bet_pct: float = 0.04      # 4% of balance per leg
    pair_arb_near_res_max_bet: float = 50.0      # hard $ cap per leg

    # NOAA Weather arb (buy underpriced temperature bucket markets using forecast edge)
    weather_entry_max_price: float = 0.15     # only enter if market price <= this
    weather_exit_min_price: float = 0.45      # sell when price corrects to >= this
    weather_noaa_min_prob: float = 0.70       # min NOAA forecast probability to enter
    weather_min_edge: float = 0.08            # min edge (noaa_prob - market_price) to enter
    weather_allow_complement: bool = False    # allow buying NO when the complement leg is mispriced
    weather_cities: str = "NYC,Chicago,Seattle,Atlanta,Dallas,Miami"
    weather_base_bet_pct: float = 0.015       # base position size (1.5% of balance)
    weather_max_bet_pct: float = 0.02         # max position size (2% of balance)
    weather_max_spend: float = 2.00           # hard cap: never spend more than $2 per weather position
    weather_scan_interval: int = 120          # seconds between market scans
    weather_dry_run: bool = True              # log only, no real orders
    weather_hold_to_resolution: bool = False  # True = hold to $0/$1, False = take profit at exit_min_price
    weather_max_hold_hours: float = 48.0      # force-exit after this many hours
    weather_max_open_positions: int = 5       # max simultaneous weather positions

    # Binance Momentum mode (primary signal source)
    signal_mode: str = "copy_trade"  # "copy_trade" or "binance_momentum"
    market_window_secs: int = 900  # 900=15min, 300=5min — controls slug, timing, guards
    momentum_trigger_pct: float = 0.003  # 0.3% move triggers signal (backtest: 80% WR, 20 signals/12h)
    momentum_window_secs: int = 60  # rolling window seconds
    min_secs_remaining: int = 360  # min seconds left in market to enter (6min = 9min entry window)
    market_window_15m_assets: str = ""  # Comma-separated assets using 15m window (e.g., "ETH,SOL")
    direction_filter: str = ""  # "Up" = only buy Up tokens, "Down" = only Down, "" = both
    entry_cooldown_secs: int = 120  # Cooldown between entries to prevent correlated positions
    dual_window_assets: str = ""  # Assets on BOTH default AND 15m windows (e.g., "BTC")
    momentum_max_pct: float = 0.02  # 2% cap — reject flash crashes / data glitches

    # S2: Early-epoch entry filter (T0: 4-5m remaining = only net-positive bucket)
    momentum_max_epoch_elapsed_secs: int = 0  # 0=disabled. Block momentum entries after N secs into epoch

    # Sharp move detector (fires on 0.2% in 15s alongside the 60s rolling window)
    enable_sharp_move: bool = False
    sharp_move_window_secs: int = 15       # rolling sub-window for sharp spike detection
    sharp_move_trigger_pct: float = 0.002  # 0.2% in 15s = sharp move
    sharp_move_shadow: bool = True         # shadow mode: log only, no execution
    sharp_move_max_entry_price: float = 0.95  # sharp moves can enter 0.90-0.95 with taker (fee <0.20%)
    sharp_move_min_entry_price: float = 0.20  # floor — rejects deep-OTM entries where market has already resolved against direction
    sharp_move_bet_multiplier: float = 1.0  # sizing multiplier for sharp_move (0.5 = half-size for first 30 trades)

    # Per-asset entry price ranges (0=use global min/max_entry_price)
    asset_min_entry_btc: float = 0.0
    asset_max_entry_btc: float = 0.0
    asset_min_entry_eth: float = 0.0
    asset_max_entry_eth: float = 0.0
    asset_min_entry_sol: float = 0.0
    asset_max_entry_sol: float = 0.0
    asset_min_entry_xrp: float = 0.0
    asset_max_entry_xrp: float = 0.0
    max_entry_spread: float = 0.04  # $0.04 max bid-ask spread for entry (wider = unfilled maker)
    min_book_imbalance_alignment: float = 0.0  # 0=disabled. E.g. 0.53: Up signals need bid/(bid+ask)>=0.53, Down signals need <=0.47
    macro_blackout_mins: int = 45              # blackout window around FOMC/CPI/NFP events (0=disabled)
    spread_size_scaling: bool = False   # Scale position size down when spread is wide
    spread_full_max: float = 0.02       # Spreads <= this get full position size
    spread_reduced_size: float = 0.75   # Multiplier when spread > spread_full_max (0.75 = 75% of full size)

    # 15m momentum trading (late-entry only — backtest: 90% WR in last 300s, 0.55-0.90 range)
    enable_15m_momentum: bool = False       # default off — enable after backtest validation
    momentum_15m_max_secs_remaining: int = 300  # only enter 15m markets with <= this many secs left
    momentum_15m_min_secs_remaining: int = 60   # need at least this many secs to get filled

    # Binance reversal exit (default off — enable after validating reversal rate)
    momentum_reversal_exit: bool = False       # enable reversal exit check
    momentum_reversal_pct: float = 0.002       # 0.2% reversal from entry triggers exit
    momentum_reversal_window_secs: int = 180   # only check within first N secs of entry
    binance_reversal_min_hold_secs: int = 15   # min secs before Binance reversal exit can fire
    momentum_reversal_dry_run: bool = True     # log reversal signals without exiting
    momentum_entry_dry_run: bool = True       # log momentum entries without placing orders

    # Chainlink Oracle Feed (BTC/USD on Polygon — actual resolution source)
    oracle_enabled: bool = False                    # master switch
    oracle_alchemy_api_key: str = ""                # ORACLE_ALCHEMY_API_KEY in .env
    oracle_contract: str = "0xc907E116054Ad103354f2D350FD2514433D57F6f"
    oracle_stale_threshold_secs: int = 60           # treat as unhealthy if no update within this
    rtds_enabled: bool = True                       # Polymarket RTDS WS for resolution-aligned Chainlink prices

    # Oracle snipe confirmation gate (block snipes when oracle disagrees with direction)
    oracle_snipe_confirm: bool = False
    oracle_snipe_confirm_dry_run: bool = True       # log disagreements without blocking

    # Oracle reversal exit (use Chainlink instead of Binance for reversal detection)
    oracle_reversal_exit: bool = False              # reuses momentum_reversal_dry_run
    oracle_exit_min_hold_secs: int = 45            # wait N secs after entry before oracle can exit
    oracle_exit_secs_remaining: int = 90
    oracle_exit_secs_remaining_momentum: int = 240  # wider ceiling for momentum positions (entered >120s before market end)           # only check oracle when < N secs remain
    oracle_exit_5m_only: bool = True               # restrict oracle exit to 5m windows (15m formula wrong)

    # Oracle epoch delta assist: use oracle delta from epoch open as supplementary momentum signal
    oracle_epoch_delta_assist: bool = False         # enable oracle delta as additional momentum source
    oracle_epoch_delta_min_pct: float = 0.0015      # min |delta| to consider (0.15%)
    oracle_epoch_delta_shadow: bool = True           # shadow mode: log only, no live trades

    # Oracle flip: when snipe gate blocks, buy the OPPOSITE (cheap) token oracle says wins
    oracle_flip_enabled: bool = False
    oracle_flip_dry_run: bool = True              # log flips without placing orders
    oracle_flip_max_bet: float = 100.0            # dedicated max bet (can be higher, oracle is certain)
    oracle_flip_max_opposite_price: float = 0.15  # only flip if cheap side <= this
    oracle_flip_min_opposite_price: float = 0.01  # skip if too cheap (no liquidity)
    oracle_flip_max_secs_remaining: int = 30      # only flip with <= 30s left (highest certainty)
    oracle_flip_min_delta_pct: float = 0.001      # min oracle vs open price delta (0.1%) to flip — skip noise

    # Oracle flip auto-escalation: raise max_bet after proven track record
    oracle_flip_escalation_min_trades: int = 30   # min completed flip trades before escalating (R8: n<30 is ANECDOTAL)
    oracle_flip_escalation_min_wr: float = 70.0   # min WR% to escalate
    oracle_flip_escalated_max_bet: float = 100.0  # escalated max bet after gate

    # Reversal short: exit losing side + enter winning side on oracle reversal
    reversal_short_enabled: bool = False
    reversal_short_dry_run: bool = True
    reversal_short_min_secs_remaining: int = 45     # only flip if >= this many secs left
    reversal_short_max_down_price: float = 0.35     # only flip if opposite side <= this
    reversal_short_min_down_price: float = 0.10     # skip if opposite side too cheap (edge gone)
    reversal_short_max_bet: float = 25.0             # hard cap on flip bet size

    # Trailing stop: exit when price drops X% from peak (locks in gains)
    trailing_stop_enabled: bool = False
    trailing_stop_pct: float = 0.12       # 12% drop from peak triggers exit
    trailing_stop_min_gain_pct: float = 0.05  # only activate after 5% gain from entry
    trailing_stop_dry_run: bool = True    # log only, no actual exits

    # Confidence exit: cut positions early when midpoint trajectory signals likely loser.
    # Research (Mar 23, n=264): winners avg 0.71 midpoint at T-210s, losers avg 0.47.
    # Exit if midpoint < threshold after min_hold_secs. Walk-forward validated (+$44 OOS).
    confidence_exit_enabled: bool = False
    confidence_exit_dry_run: bool = True   # log only, no actual exits
    confidence_exit_threshold: float = 0.60  # exit if our token midpoint below this
    confidence_exit_min_hold_secs: int = 60  # don't check before 60s held
    confidence_exit_max_hold_secs: int = 120  # stop checking after 120s (later exits are net negative)

    # Outcome gate: pause trading when rolling WR drops (losing regime detection)
    # Research (Mar 23, n=329): after-loss WR=29%, after-win WR=85%, ACF=0.401.
    # Losses cluster. Pausing after N consecutive losses avoids regime drawdowns.
    outcome_gate_enabled: bool = False
    outcome_gate_dry_run: bool = True     # log only, no actual blocking
    outcome_gate_window: int = 10         # track last N trade outcomes
    outcome_gate_min_wr: float = 0.40     # block if rolling WR < 40%
    outcome_gate_resume_wr: float = 0.55  # resume when rolling WR recovers to 55%

    # Markov gate: sequential state-space regime detection
    # Research (Apr 9, n=1193): P(W|W)=0.59, P(L|L)=0.69, P(W|LL)=0.27, P(W|WW)=0.65.
    # Losses cluster into streaks (max=82). After W + cheap entry (0.35-0.50) = only profitable cohort.
    # Gate blocks entries after consecutive losses, resumes after a win.
    markov_gate_enabled: bool = False
    markov_gate_dry_run: bool = True       # log only, no actual blocking
    markov_gate_max_losses: int = 1        # block after N consecutive losses (1 = block after any L)
    markov_gate_min_wins: int = 1          # resume after N consecutive wins (1 = resume after any W)
    markov_gate_timeout_secs: int = 1800   # auto-unblock after 30min to probe regime

    # Markov-Kelly position sizing: condition bet size on Markov regime state
    # Uses sequential win probability P(W|streak) instead of rolling WR from DB.
    # Research (Apr 9, n=1193): After W + cheap entry = only profitable cohort.
    # Kelly formula for binary: f* = (p*b - q) / b, b = (1 - entry - fee) / (entry + fee)
    # Quarter-Kelly recommended for small bankrolls (survival > growth).
    markov_kelly_enabled: bool = False
    markov_kelly_haircut: float = 0.15       # backtest-to-live degradation (15% conservative)
    markov_kelly_max_bet_pct: float = 0.10   # hard cap: never bet >10% of capital per trade
    markov_kelly_pw_w: float = 0.588         # P(W|W) after 1 consecutive win
    markov_kelly_pw_ww: float = 0.650        # P(W|WW) after 2 consecutive wins
    markov_kelly_pw_www: float = 0.690       # P(W|WWW+) after 3+ consecutive wins
    markov_kelly_pw_lw: float = 0.310        # P(W|LW) after loss->win recovery (eighth-Kelly)

    # Mid-price stop-loss for momentum positions (bypasses hold_to_resolution)
    mid_price_stop_enabled: bool = False
    mid_price_stop_pct: float = 0.08   # exit when mid-price drops 8% below entry

    # Pre-resolution exit: force sell momentum positions N seconds before market end
    pre_resolution_exit_secs: int = 0   # 0 = disabled. e.g. 15 = sell 15s before resolution

    # Window Delta mode (buy winning side at T-N seconds before 5m window close)
    enable_window_delta: bool = False
    window_delta_shadow: bool = True   # shadow mode: log signal without executing
    window_delta_lead_secs: int = 10   # fire signal N seconds before window close
    window_delta_min_pct: float = 0.001  # 0.1% minimum price move to consider direction "decided"
    window_delta_assets: str = ""  # comma-separated, empty = use asset_filter
    window_delta_max_price: float = 0.95  # max entry price for delta signals (breakeven ~95% WR)

    # Resolution snipe: buy near-certain outcomes in last seconds before resolution
    enable_resolution_snipe: bool = False
    snipe_dry_run: bool = True            # log only, no real orders
    snipe_max_secs_remaining: int = 45   # enter up to 45s before close
    snipe_min_secs_remaining: int = 8    # need at least 8s for taker fill
    snipe_min_momentum_pct: float = 0.002  # 0.2% Binance move confirms direction
    snipe_min_entry_price: float = 0.90  # snipe zone floor
    snipe_max_entry_price: float = 0.985  # snipe zone ceiling (above = dust profit)
    snipe_bet_pct: float = 0.04          # 4% of balance per snipe
    snipe_max_bet: float = 50.0          # hard $ cap per snipe trade
    snipe_assets: str = ""               # comma-separated, empty = use asset_filter
    snipe_max_daily_trades: int = 100    # hard cap on snipe trades per day (0=unlimited)
    snipe_max_per_epoch: int = 0         # max live snipes per epoch, 0=unlimited (prevents correlated losses)
    snipe_entry_mode: str = "maker"      # "maker" (GTC post_only, 0% fee) or "fak" (instant fill, ~0.1% fee)
    snipe_blackout_min_secs: int = 0     # skip signals in this range (0=disabled). E.g. 11 = skip 11-30s
    snipe_blackout_max_secs: int = 0     # upper bound of blackout. E.g. 30 = skip 11-30s danger zone

    # 15m Resolution snipe (shadow testing — same strategy, longer window)
    snipe_15m_enabled: bool = False       # enable 15m snipe scanning
    snipe_15m_dry_run: bool = True        # force dry_run for 15m (shadow data collection)
    snipe_15m_min_entry_price: float = 0.93
    snipe_15m_max_entry_price: float = 0.985
    snipe_15m_min_secs_remaining: int = 8
    snipe_15m_max_secs_remaining: int = 45
    snipe_15m_block_directions: str = ""  # comma-sep "BTC:Up,ETH:Down" to skip specific asset+direction combos on 15m

    # S4: Contrarian streak entry (bet against 3+ consecutive same-direction outcomes)
    streak_tracking_enabled: bool = False    # track epoch outcomes for streak detection
    streak_min_length: int = 3              # minimum consecutive same-direction outcomes to trigger
    streak_contrarian_dry_run: bool = True  # log only, no real orders
    streak_contrarian_bet_pct: float = 0.03  # 3% of balance per contrarian entry
    streak_contrarian_max_bet: float = 30.0  # hard $ cap per contrarian trade
    streak_contrarian_min_price: float = 0.40  # only enter if opposite side >= this (meaningful edge)
    streak_contrarian_max_price: float = 0.60  # only enter if opposite side <= this (avoid chasing)

    # Market maker (risk-free pair-cost arbitrage: buy BOTH sides when pair_cost < $1.00)
    enable_market_maker: bool = False
    mm_dry_run: bool = True               # log only, no real orders
    mm_assets: str = ""                    # comma-separated, empty = use asset_filter
    mm_scan_5m: bool = True               # scan 5m markets
    mm_scan_15m: bool = True              # scan 15m markets
    mm_max_pair_cost: float = 0.995       # max fee-inclusive pair cost (profit = 1.00 - this)
    mm_bet_pct: float = 0.05             # 5% of balance per leg
    mm_max_bet: float = 50.0             # hard $ cap per leg
    mm_scan_interval: float = 0.05       # seconds between scans (50ms for sub-second reaction)
    mm_obs_flush_interval: float = 5.0   # seconds between batched observation DB flushes
    mm_balance_cache_ttl: float = 30.0   # seconds to cache get_balance() result
    mm_max_secs_remaining: int = 60      # start scanning N seconds before epoch end
    mm_min_secs_remaining: int = 8       # stop scanning (need time for FOK fill)

    # Stale quote sniping (directional: buy underpriced side when Binance shows clear direction)
    mm_stale_enabled: bool = False       # enable stale quote detection (respects mm_dry_run)
    mm_stale_min_move_pct: float = 0.002 # 0.2% min Binance move to consider direction clear
    mm_stale_max_fair_discount: float = 0.08  # buy if ask is >=8% below implied fair value
    mm_stale_min_ask: float = 0.45       # don't buy below this (too risky, high fees)
    mm_stale_max_ask: float = 0.92       # don't buy above this (not enough upside)
    mm_stale_bet_pct: float = 0.05       # 5% of balance per stale quote trade
    mm_stale_max_bet: float = 50.0       # hard $ cap per stale quote trade
    mm_stale_max_secs_remaining: int = 180  # stale quotes scan up to 3min before epoch end (wider than pair arb)
    mm_stale_min_secs_remaining: int = 8    # stop scanning (need time for FOK fill)

    # Limit-order pair-cost arbitrage (maker bids on BOTH sides, wait for fills, profit from resolution)
    # Inspired by 0x8dxd: post GTC limit bids at target prices, poll for fills, hold to resolution.
    # Maker fee = $0. Profit = $1.00 - (bid_up + bid_down) per share if both fill.
    mm_limit_enabled: bool = False          # master switch for limit-order MM
    mm_limit_dry_run: bool = True           # log only, no real orders
    mm_limit_bid_price: float = 0.48        # bid price per side (pair_cost = 2 * this = $0.96)
    mm_limit_min_bid: float = 0.40          # floor bid price (below = too risky, won't fill)
    mm_limit_max_bid: float = 0.52          # ceiling bid price (above = pair_cost too high)
    mm_limit_bet_pct: float = 0.03          # 3% of balance per leg
    mm_limit_max_bet: float = 5.0           # hard $ cap per leg (start small: $5)
    mm_limit_min_secs_post: int = 180       # post orders when >= N secs remain in epoch
    mm_limit_max_secs_post: int = 280       # post orders when <= N secs remain (avoid start-of-epoch)
    mm_limit_cancel_at_secs: int = 30       # cancel unfilled orders when < N secs remain
    mm_limit_poll_interval: float = 5.0     # seconds between fill-check polls
    mm_limit_max_pairs: int = 2             # max simultaneous pair positions across all assets
    mm_limit_orphan_hold: bool = True       # if only one leg fills, hold to resolution (directional bet at 0.48)

    # Rebate MM: continuous two-sided quoting with VPIN protection for maker rebate farming
    # Posts GTC bids AND asks near midpoint. Earns Polymarket quadratic liquidity rewards.
    # VPIN dynamically widens spread or pulls quotes when informed flow detected.
    mm_rebate_enabled: bool = False         # master switch for rebate market making
    mm_rebate_dry_run: bool = True          # log only, no real orders
    mm_rebate_base_spread: float = 0.04    # base bid-ask spread width ($0.04 = 2c each side)
    mm_rebate_min_spread: float = 0.02     # tightest allowed spread (aggressive for rewards)
    mm_rebate_max_spread: float = 0.10     # widest spread before pulling quotes entirely
    mm_rebate_size_pct: float = 0.03       # 3% of balance per side per quote
    mm_rebate_max_size: float = 10.0       # hard $ cap per side (start small)
    mm_rebate_min_mid: float = 0.20        # skip quoting when mid < this (too far from 50/50)
    mm_rebate_max_mid: float = 0.80        # skip quoting when mid > this
    mm_rebate_refresh_secs: float = 5.0    # cancel+replace quotes every N seconds
    mm_rebate_min_secs: int = 60           # stop quoting when < N secs remain (resolution risk)
    mm_rebate_max_secs: int = 280          # start quoting when <= N secs remain in epoch
    mm_rebate_max_positions: int = 2       # max simultaneous rebate-quoted markets
    mm_rebate_inventory_warn: float = 0.65 # inventory ratio threshold for aggressive skew (0.5=balanced)

    # VPIN thresholds for rebate MM (calibrate to market: start with these, tighten after data)
    mm_rebate_vpin_safe: float = 0.40      # below = normal, quote at base spread
    mm_rebate_vpin_elevated: float = 0.55  # above = widen 30%
    mm_rebate_vpin_high: float = 0.65      # above = widen 80%
    mm_rebate_vpin_kill: float = 0.75      # above = pull all quotes immediately
    mm_rebate_vpin_bucket_vol: float = 1000.0  # VPIN bucket volume in USDC
    mm_rebate_vpin_n_buckets: int = 30     # VPIN rolling window (30 for fast 5m markets)
    mm_rebate_lob_levels: int = 5          # LOB imbalance depth levels

    # IGOC: Imbalance-Gated Oracle Confirm strategy (book depth + oracle direction alignment)
    igoc_enabled: bool = False
    igoc_shadow_only: bool = True           # True = shadow mode (log only, no entry)
    igoc_imbalance_threshold: float = 0.72  # bid/(bid+ask) must exceed this
    igoc_min_price: float = 0.35
    igoc_max_price: float = 0.65
    igoc_min_secs_remaining: int = 130
    igoc_max_secs_remaining: int = 200
    igoc_bet_size: float = 5.0
    igoc_stop_pct: float = 0.01             # 1% mid-price stop
    igoc_target_pct: float = 0.015          # 1.5% profit target
    igoc_oracle_confirm_n: int = 3          # consecutive oracle readings needed
    igoc_max_daily_trades: int = 20
    igoc_max_daily_loss: float = 25.0

    # Order entry/exit mode
    entry_mode: str = "taker"  # "taker" (cross spread) or "maker" (post-only)
    maker_offset: float = 0.005  # place maker order this much below midpoint (DARIO: aggressive pricing better for snipers)
    taker_on_5m: bool = True            # Use taker FOK on 5m markets (fee-free = no cost, instant fill)
    hold_to_resolution: bool = False     # Hold to market resolution instead of profit_target/stop_loss

    # Epoch accumulator: ugag-style continuous buying throughout an epoch
    accum_mode_enabled: bool = False
    accum_bet_per_round: float = 1.0     # $ per buy round
    accum_interval_secs: int = 15        # seconds between buys
    accum_max_rounds: int = 10           # max buys per epoch
    accum_stop_before_end_secs: int = 30 # stop buying N secs before resolution
    accum_reversal_pct: float = 0.002    # stop accumulating if Binance reverses > this % against direction
    cheap_side_min_imbalance: float = 0.0  # min book imbalance to confirm direction (0=disabled, 0.3=strong)
    lottery_enabled: bool = False          # near-resolution lottery tickets ($0.01-$0.05, last 30s)
    lottery_max_price: float = 0.05        # max token price for lottery entries
    lottery_min_secs: int = 5              # minimum secs before resolution
    lottery_max_secs: int = 45             # maximum secs before resolution (entry window)
    lottery_bet: float = 0.50              # bet size per lottery ticket
    conviction_dry_run: bool = True        # log conviction scaling signals without acting
    mode2_dry_run: bool = True             # log Mode 2 momentum signals without acting

    # Cheap side signal: buy whichever token is cheaper (ugag's core strategy)
    cheap_side_enabled: bool = False
    cheap_side_max_price: float = 0.45   # only buy if cheaper side <= this
    cheap_side_min_price: float = 0.15   # skip extremely cheap (likely resolved)
    cheap_side_scan_interval: float = 30   # seconds between scans (supports sub-second)
    cheap_side_min_secs: int = 60        # min secs remaining to enter (signal generation)
    cheap_side_max_secs: int = 240       # max secs remaining (wait for some price discovery)

    # Pre-entry adverse selection filter: reject entries when Binance is moving against trade
    adverse_precheck_enabled: bool = True   # enable/disable the pre-entry check
    adverse_precheck_secs: int = 5          # lookback window in seconds
    adverse_precheck_threshold: float = 0.0001  # min adverse move to reject (0.01%)

    # Epoch time gate: minimum time remaining at execution (accounts for order lifecycle ~30s)
    min_execution_secs_remaining: int = 120  # reject signals with < this many secs remaining
    cheap_side_windows: str = "300"      # comma-separated window sizes in seconds (300=5m, 900=15m)
    cheap_side_active_hours: str = ""    # comma-separated UTC hours when cheap side is active (empty=always)
    maker_exit_enabled: bool = True    # Feature flag: maker SELL for profit_target (zero fee)
    profit_target_early_enabled: bool = False   # Exit early when up enough with time remaining
    profit_target_early_pp: float = 0.07        # pp gain above entry that triggers early exit (0.07 = 7pp)
    profit_target_early_min_secs: int = 30      # must have >= this many secs remaining to fire
    profit_target_early_dry_run: bool = True    # log only, no actual exit
    profit_target_early_apply_fee_correction: bool = True  # subtract taker fee (p²*(1-p)) before threshold check
    maker_exit_timeout_polls: int = 10  # Polls for maker SELL fill before taker fallback
    signature_type: int = 1  # 1=Proxy wallet, 2=EOA (MetaMask direct)

    # Circuit breaker settings
    dry_run_balance: float = 400.0       # Simulated balance for dry-run sizing ($)
    max_daily_loss: float = 40.0         # Halt new trades if daily realized loss exceeds this ($, 0=disabled)
    max_consecutive_losses: int = 5      # Halt after N consecutive losing trades (0=disabled)
    loss_cooldown_mins: int = 60         # Pause duration after consecutive loss trigger (minutes)
    post_loss_cooldown_mins: int = 0     # Pause after ANY single loss (0=disabled). T0: 29% WR after loss vs 85% after win (n=240).
    kill_switch_path: str = ""           # File path for kill switch (empty=disabled)
    lagbot_data_dir: str = "data"        # Per-instance data directory (set via LAGBOT_DATA_DIR)

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_approval_timeout_secs: int = 300

    slack_webhook_url: str = ""
    slack_bot_token: str = ""
    slack_channel_id: str = ""

    market_context_path: str = "/opt/openclaw/data/lagbot_context.json"

    # Fear & Greed regime filter (watchdog handles start/stop; hard block disabled by default)
    fg_min_threshold: int = 0             # hard block entries when F&G <= this (0=disabled)
    fg_caution_threshold: int = 40        # reduce sizing when F&G between min and this
    fg_caution_size_mult: float = 0.5     # sizing multiplier in caution zone (50%)
    max_trade_amount: float = 0.0         # absolute $ cap per trade (0=disabled, e.g. 100.0)

    # Whipsaw regime guard (blocks trades when volatility is high but net direction is low)
    whipsaw_max_ratio: float = 0.20       # block when directionality < this (0=disabled)
    whipsaw_min_vol: float = 0.005        # only apply when volatility_1h > 0.5% (skip calm/noisy)
    whipsaw_caution_ratio: float = 0.40   # caution zone: directionality between max_ratio and this
    eth_block_on_whipsaw_caution: bool = True  # block ETH when in whipsaw caution zone (ETH 6/9 losses)

    # Danger hours sizing (UTC hours where losses cluster — reduce size, don't block)
    danger_hours: str = ""                 # CSV of UTC hours, e.g. "1,2,3" (5-7pm PST)
    danger_hours_size_mult: float = 0.5    # sizing multiplier during danger hours
    up_direction_size_mult: float = 0.5    # sizing multiplier for Up direction trades (Down=1.0)

    # Flat regime blocking (low vol = directionality ratio is noisy, WR drops)
    flat_regime_block: bool = False         # block trading when volatility_1h < flat_regime_max_vol
    flat_regime_max_vol: float = 0.003     # vol below this = flat regime (default 0.3%)

    # Stale regime data guard (vol_1h=0 AND trend_1h=0 exactly = regime calculator uninitialized)
    # 7/7 trades with this pattern were losses (n=24, 24h window Mar 26 2026). Data quality gate.
    stale_regime_skip_enabled: bool = False  # off by default; enable after VPS deploy + log verification

    # Flat regime RTDS continuation signal
    # Fires when market is flat (vol < flat_regime_max_vol) but RTDS clearly shows direction vs strike
    flat_regime_rtds_enabled: bool = False       # master switch
    flat_regime_rtds_shadow: bool = True         # shadow-only until validated
    flat_regime_rtds_min_gap: float = 0.05       # min token mispricing vs RTDS direction (5pp)
    flat_regime_rtds_min_secs: int = 45          # min secs remaining to enter
    flat_regime_rtds_max_secs: int = 180         # max secs remaining to enter
    flat_regime_rtds_min_price: float = 0.30     # min token entry price
    flat_regime_rtds_max_price: float = 0.80     # max token entry price (above 0.80 = already priced in)
    flat_regime_rtds_imbalance_threshold: float = 0.55  # CLOB imbalance confirmation gate
    flat_regime_rtds_rtds_min_pct: float = 0.005  # min RTDS deviation from strike (0.5%)
    flat_regime_rtds_blackout_hours: str = ""     # comma-separated UTC hours to skip (e.g. "4,17,18,19,21,23")
    rtds_down_sizing_mult: float = 1.0           # sizing multiplier for RTDS Down entries (0.5 = half-size cold-start guard)

    # Momentum-size proportional sizing (bigger BTC move = bigger bet)
    # Data: 0.3-0.5% = 71% dir WR, 0.5-1.0% = 76%, 1.0%+ = 90%+
    momentum_size_scaling_enabled: bool = True
    momentum_size_tier1_pct: float = 0.005   # 0.5% move threshold for tier 1
    momentum_size_tier1_mult: float = 1.5    # 1.5x bet at 0.5%+ moves
    momentum_size_tier2_pct: float = 0.01    # 1.0% move threshold for tier 2
    momentum_size_tier2_mult: float = 2.0    # 2x bet at 1.0%+ moves

    # Dynamic Kelly sizing (Thorp: recalculate optimal bet from rolling WR)
    dynamic_kelly_enabled: bool = False       # off by default, enable after 50+ trades at new config
    dynamic_kelly_lookback: int = 50          # rolling window for WR calculation

    # Dynamic maker/taker entry (Telonex: top snipers are 75-82% taker)
    # Fill rate data: 0.60-0.74 = 5-12% maker fill, 0.75+ = 23%+
    dynamic_entry_mode_enabled: bool = False  # off by default
    dynamic_entry_taker_below: float = 0.75   # use taker when midpoint < this
    entry_delay_secs: float = 0.0             # stagger delay before order placement (prevent CLOB collision between instances)

    # Phase Gate hedge leg (opposite side cheap buy after RTDS main leg fires)
    phase_gate_hedge_enabled: bool = False
    phase_gate_hedge_max_price: float = 0.08    # max price for opposite side token
    phase_gate_hedge_max_bet: float = 5.0       # max dollar size for hedge leg
    phase_gate_hedge_size_pct: float = 0.15     # hedge size as % of main bet

    # Tugao9 copy-trade watcher
    tugao9_watcher_enabled: bool = False
    tugao9_poll_interval: float = 5.0
    tugao9_address: str = "0x970e744a34cd0795ff7b4ba844018f17b7fd5c26"
    tugao9_min_price: float = 0.40
    tugao9_max_price: float = 0.60
    tugao9_shadow: bool = True

    # S1: Regime-adaptive sizing (T0: calm=44% WR, moderate=80% WR, elevated=mixed)
    regime_sizing_enabled: bool = False     # enable 4-tier vol regime sizing
    regime_cautious_max_vol: float = 0.005  # vol below this = cautious regime (reduced size)
    regime_optimal_max_vol: float = 0.010   # vol below this = optimal regime (full size), above = elevated
    regime_cautious_mult: float = 0.50      # sizing multiplier for cautious regime
    regime_elevated_mult: float = 0.75      # sizing multiplier for elevated regime (big tails)

    # Liquidation cascade block (block entries when large cascade opposes direction)
    liq_cascade_block_enabled: bool = False  # block when liq cascade > threshold against our direction
    liq_cascade_min_volume: float = 10_000_000  # $10M in 60s to trigger block

    # Extreme funding rate gate (block entries when crowded positioning)
    funding_extreme_block_enabled: bool = False  # block when funding rate is extreme
    funding_extreme_threshold: float = 0.0005    # 0.05% = overheated (matches regime_detector)

    # Taker CVD confirmation (filter momentum by taker buy/sell agreement)
    cvd_confirmation_enabled: bool = False  # require taker delta agrees with momentum direction
    cvd_confirmation_dry_run: bool = True   # log CVD mismatches but don't block

    # VPIN adverse selection filter (block entries when informed traders oppose our direction)
    vpin_block_enabled: bool = False         # block when VPIN indicates adverse selection
    vpin_block_threshold: float = 0.65       # VPIN above this = high informed trading activity
    vpin_block_dry_run: bool = True          # log only, no actual blocking
    vpin_sustained_bars: int = 5             # consecutive high-VPIN bars to trigger sustained alert
    vpin_sustained_threshold: float = 0.60   # threshold for sustained alert (lower than instant)
    vpin_sizing_enabled: bool = False        # graduated position sizing based on VPIN level
    vpin_size_reduce_at: float = 0.50        # start reducing size at this VPIN
    vpin_size_min_mult: float = 0.50         # minimum size multiplier at vpin_block_threshold

    # Coinbase Premium confirmation (cross-exchange divergence signal)
    coinbase_premium_enabled: bool = False    # enable concurrent Coinbase feed for premium calc
    coinbase_premium_block_enabled: bool = False  # block when premium strongly opposes direction
    coinbase_premium_min_bps: float = 10.0   # min absolute premium (bps) to consider significant
    coinbase_premium_block_dry_run: bool = True   # log only, no actual blocking

    # Liquidation cascade offensive boost (increase sizing during aligned cascades)
    liq_cascade_boost_enabled: bool = False   # boost sizing when cascade aligns with direction
    liq_cascade_boost_volume: float = 5_000_000  # $5M in 60s to trigger offensive boost
    liq_cascade_boost_multiplier: float = 2.0    # max sizing multiplier during aligned cascade

    # Data science modules (all optional, graceful degradation)
    enable_signal_logging: bool = True    # Log ALL signals to SQLite for ML training
    instance_name: str = ""
    config_label: str = ""
    enable_btc5m_ensemble_shadow: bool = False  # Compare a ranked BTC 5m shadow strategy against the live path
    btc5m_ensemble_mode: str = "shadow"   # "shadow" only in first pass
    btc5m_ensemble_admission_enabled: bool = False  # Narrow future live gate: only trade BTC 5m momentum when ensemble-selected
    btc5m_ensemble_admission_mode: str = "shadow"   # "shadow" reserved for report-only planning, "active" blocks execution
    enable_btc5m_evidence_verdicts: bool = False  # Read-only BTC 5m cohort verdict logging
    btc5m_evidence_mode: str = "shadow"   # Reserved for future active gating, first pass is log-only
    btc5m_evidence_min_samples: int = 30  # Minimum comparable executed trades for non-anecdotal verdicts
    btc5m_entry_retry_enabled: bool = False  # Bounded entry recovery for passed BTC 5m momentum signals
    btc5m_entry_retry_mode: str = "shadow"   # "shadow" logs retry decisions, "active" places the retry
    btc5m_entry_retry_max_placement_retries: int = 1  # Retry transient placement failures once
    btc5m_entry_retry_max_fill_retries: int = 1  # Retry one zero-fill timeout after cancel
    btc5m_entry_retry_delay_ms: int = 500  # Delay before retrying a passed BTC 5m entry
    # Layer 1k: Ensemble bet sizing
    ensemble_sizing_enabled: bool = False          # Gate: enable ensemble score → size multiplier
    ensemble_sizing_dry_run: bool = True           # Log only, no actual size adjustment
    ensemble_none_fallback: str = "neutral"        # "neutral" (1.0x) or "cautious" (0.5x) for non-BTC signals
    ensemble_high_threshold: float = 0.80          # Score >= this → high multiplier
    ensemble_high_mult: float = 1.25               # Size multiplier for high-scoring signals
    ensemble_low_threshold: float = 0.40           # Score < this → low multiplier
    ensemble_low_mult: float = 0.50                # Size multiplier for low-scoring signals
    btc5m_entry_retry_reprice_cents: int = 1  # Extra cents over fresh midpoint on retry
    btc5m_entry_retry_min_secs_remaining: int = 45  # Skip retries too close to market close
    btc5m_entry_retry_max_overpay_cents: int = 5  # Hard cap over original signal price on retry
    enable_signal_scoring: bool = True    # XGBoost signal quality scoring
    signal_score_mode: str = "shadow"     # "shadow" (log only) or "active" (filter below threshold)
    signal_score_threshold: float = 30.0  # Min score in active mode (0-100)
    enable_fill_optimizer: bool = True    # Thompson Sampling for maker offset selection
    enable_regime_detection: bool = True  # Market regime classification from Binance prices

    class Config:
        env_file = str(ENV_PATH)
        case_sensitive = False
        extra = 'ignore'

    @field_validator(
        'mid_price_stop_pct', 'igoc_stop_pct', 'trailing_stop_pct',
        'stop_loss_pct', 'trailing_stop_min_gain_pct',
        mode='before',
    )
    @classmethod
    def validate_stop_pct_range(cls, v, info):
        v = float(v)
        if v < 0 or v > 1.0:
            raise ValueError(
                f"{info.field_name} must be between 0 and 1.0, got {v}"
            )
        return v

    @field_validator('accum_entry_mode', mode='before')
    @classmethod
    def validate_accum_entry_mode(cls, v):
        mode = str(v).strip().lower()
        if mode not in {"maker", "fak"}:
            raise ValueError(f"accum_entry_mode must be 'maker' or 'fak', got {v}")
        return mode

    def get_asset_filter(self) -> List[str]:
        """Return allow-list of assets. Empty list means all assets allowed."""
        if not self.asset_filter.strip():
            return []
        return [a.strip().upper() for a in self.asset_filter.split(',') if a.strip()]

    def get_shadow_assets(self) -> List[str]:
        """Return shadow-mode assets (log only, no execution)."""
        if not self.shadow_assets.strip():
            return []
        return [a.strip().upper() for a in self.shadow_assets.split(',') if a.strip()]

    def get_companion_assets(self) -> List[str]:
        """Return companion assets (trade only when lead asset fires, same direction)."""
        if not self.companion_assets.strip():
            return []
        return [a.strip().upper() for a in self.companion_assets.split(',') if a.strip()]

    def get_blocked_assets(self) -> List[str]:
        if not self.blocked_assets.strip():
            return []
        return [a.strip().upper() for a in self.blocked_assets.split(',') if a.strip()]

    def get_blackout_hours(self) -> List[int]:
        return [int(h.strip()) for h in self.blackout_hours.split(',') if h.strip()]

    def get_danger_hours(self) -> List[int]:
        if not self.danger_hours.strip():
            return []
        return [int(h.strip()) for h in self.danger_hours.split(',') if h.strip()]

    def get_market_window(self, asset: str) -> int:
        """Return market window in seconds for a given asset.

        Assets listed in market_window_15m_assets use 900s (15m).
        All others use the default market_window_secs.
        """
        if self.market_window_15m_assets.strip():
            assets_15m = [a.strip().upper() for a in self.market_window_15m_assets.split(',') if a.strip()]
            if asset.upper() in assets_15m:
                return 900
        return self.market_window_secs

    def get_market_window_label(self, asset: str) -> str:
        """Return slug label like '15m' or '5m' for a given asset."""
        return f"{self.get_market_window(asset) // 60}m"

    def get_asset_multiplier(self, asset: str) -> float:
        """Return bet sizing multiplier for a given asset. Default 1.0."""
        key = f"asset_multiplier_{asset.lower()}"
        return getattr(self, key, 1.0)

    def get_entry_range(self, asset: str) -> tuple:
        """Return (min_price, max_price) for an asset. Falls back to global."""
        a = asset.lower()
        lo = getattr(self, f"asset_min_entry_{a}", 0.0)
        hi = getattr(self, f"asset_max_entry_{a}", 0.0)
        return (
            lo if lo > 0 else self.min_entry_price,
            hi if hi > 0 else self.max_entry_price,
        )

    def get_market_windows(self, asset: str) -> List[int]:
        """Return list of market windows for an asset.

        Assets in dual_window_assets get BOTH their default window AND the other
        (5m if default is 15m, 15m if default is 5m).
        """
        default = self.get_market_window(asset)
        windows = [default]
        if self.dual_window_assets.strip():
            dual = [a.strip().upper() for a in self.dual_window_assets.split(',') if a.strip()]
            if asset.upper() in dual:
                other = 900 if default == self.market_window_secs else self.market_window_secs
                if other not in windows:
                    windows.append(other)
        return windows

    def get_min_secs_remaining(self, window_secs: int) -> int:
        """Return min seconds remaining per window size.

        Caps at 40% of window to match signal_guard logic.
        5m (300s) → min(config, 120). 15m (900s) → min(config, 360).
        """
        cap = int(window_secs * 0.4)
        return min(self.min_secs_remaining, cap)

    def get_arb_assets(self) -> List[str]:
        return [a.strip() for a in self.arb_assets.split(',')]

    def get_instance_name(self) -> str:
        """Return an instance label for logging/reporting."""
        if self.instance_name.strip():
            return self.instance_name.strip()
        data_dir = Path(self.lagbot_data_dir)
        if data_dir.name and data_dir.name != "data":
            return data_dir.name
        return "default"

    def get_config_era_tag(self) -> str:
        """Return a stable hash of strategy-relevant settings for later replay."""
        payload = {
            "asset_filter": self.asset_filter,
            "shadow_assets": self.shadow_assets,
            "market_window_secs": self.market_window_secs,
            "min_entry_price": self.min_entry_price,
            "max_entry_price": self.max_entry_price,
            "momentum_trigger_pct": self.momentum_trigger_pct,
            "momentum_window_secs": self.momentum_window_secs,
            "momentum_max_epoch_elapsed_secs": self.momentum_max_epoch_elapsed_secs,
            "whipsaw_max_ratio": self.whipsaw_max_ratio,
            "entry_mode": self.entry_mode,
            "accum_entry_mode": self.accum_entry_mode,
            "base_bet_pct": self.base_bet_pct,
            "max_bet": self.max_bet,
            "max_trade_amount": self.max_trade_amount,
            "signal_mode": self.signal_mode,
            "enable_window_delta": self.enable_window_delta,
            "window_delta_shadow": self.window_delta_shadow,
            "window_delta_max_price": self.window_delta_max_price,
            "enable_resolution_snipe": self.enable_resolution_snipe,
            "snipe_dry_run": self.snipe_dry_run,
            "enable_btc5m_evidence_verdicts": self.enable_btc5m_evidence_verdicts,
            "btc5m_evidence_mode": self.btc5m_evidence_mode,
            "enable_btc5m_ensemble_shadow": self.enable_btc5m_ensemble_shadow,
            "btc5m_ensemble_mode": self.btc5m_ensemble_mode,
            "btc5m_ensemble_admission_enabled": self.btc5m_ensemble_admission_enabled,
            "btc5m_ensemble_admission_mode": self.btc5m_ensemble_admission_mode,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return digest[:12]



def setup_logger(name: str, level: str = 'INFO') -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            fmt='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    # Prevent duplicate log lines from propagation to parent "polyphemus" logger
    if '.' in name:
        logger.propagate = False

    return logger


def assert_metric_matches_db(metric_name: str, displayed_value: float, db_value: float, tolerance: float = 0.01) -> bool:
    logger = logging.getLogger('polyphemus.config')
    diff = abs(displayed_value - db_value)
    if diff > tolerance:
        logger.warning(
            f'Metric mismatch: {metric_name} | displayed={displayed_value:.6f} | db={db_value:.6f} | diff={diff:.6f}'
        )
        return False
    return True


def assert_wallet_reconciliation(wallet_balance: float, position_notional: float, deployed_capital: float, tolerance: float = 5.0) -> bool:
    logger = logging.getLogger('polyphemus.config')
    expected = wallet_balance + position_notional
    diff = abs(expected - deployed_capital)
    if diff > tolerance:
        logger.warning(
            f'Wallet reconciliation mismatch | wallet_balance={wallet_balance:.2f} | position_notional={position_notional:.2f} | deployed_capital={deployed_capital:.2f} | diff={diff:.2f}'
        )
        return False
    return True
