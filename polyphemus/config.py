import logging
from pathlib import Path
from typing import List

from dotenv import load_dotenv
try:
    from pydantic_settings import BaseSettings
except ImportError:
    from pydantic import BaseSettings


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
    accum_max_pair_cost: float = 0.975    # max pair cost for entry (pure arbitrage)
    accum_min_profit_per_share: float = 0.02  # min $0.02 profit per share
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

    # Sharp move detector (fires on 0.2% in 15s alongside the 60s rolling window)
    enable_sharp_move: bool = False
    sharp_move_window_secs: int = 15       # rolling sub-window for sharp spike detection
    sharp_move_trigger_pct: float = 0.002  # 0.2% in 15s = sharp move
    sharp_move_shadow: bool = True         # shadow mode: log only, no execution
    sharp_move_max_entry_price: float = 0.95  # sharp moves can enter 0.90-0.95 with taker (fee <0.20%)

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

    # Window Delta mode (buy winning side at T-N seconds before 5m window close)
    enable_window_delta: bool = False
    window_delta_lead_secs: int = 10   # fire signal N seconds before window close
    window_delta_min_pct: float = 0.001  # 0.1% minimum price move to consider direction "decided"
    window_delta_assets: str = ""  # comma-separated, empty = use asset_filter
    window_delta_max_price: float = 0.95  # max entry price for delta signals (breakeven ~95% WR)

    # Resolution snipe: buy near-certain outcomes in last seconds before resolution
    enable_resolution_snipe: bool = False
    snipe_max_secs_remaining: int = 45   # enter up to 45s before close
    snipe_min_secs_remaining: int = 8    # need at least 8s for taker fill
    snipe_min_momentum_pct: float = 0.002  # 0.2% Binance move confirms direction
    snipe_min_entry_price: float = 0.90  # snipe zone floor
    snipe_max_entry_price: float = 0.985  # snipe zone ceiling (above = dust profit)
    snipe_bet_pct: float = 0.04          # 4% of balance per snipe
    snipe_max_bet: float = 50.0          # hard $ cap per snipe trade
    snipe_assets: str = ""               # comma-separated, empty = use asset_filter
    snipe_max_daily_trades: int = 100    # hard cap on snipe trades per day (0=unlimited)

    # Order entry/exit mode
    entry_mode: str = "taker"  # "taker" (cross spread) or "maker" (post-only)
    maker_offset: float = 0.005  # place maker order this much below midpoint (DARIO: aggressive pricing better for snipers)
    taker_on_5m: bool = True            # Use taker FOK on 5m markets (fee-free = no cost, instant fill)
    hold_to_resolution: bool = False     # Hold to market resolution instead of profit_target/stop_loss
    maker_exit_enabled: bool = True    # Feature flag: maker SELL for profit_target (zero fee)
    maker_exit_timeout_polls: int = 10  # Polls for maker SELL fill before taker fallback
    signature_type: int = 1  # 1=Proxy wallet, 2=EOA (MetaMask direct)

    # Circuit breaker settings
    dry_run_balance: float = 400.0       # Simulated balance for dry-run sizing ($)
    max_daily_loss: float = 40.0         # Halt new trades if daily realized loss exceeds this ($, 0=disabled)
    max_consecutive_losses: int = 5      # Halt after N consecutive losing trades (0=disabled)
    loss_cooldown_mins: int = 60         # Pause duration after consecutive loss trigger (minutes)
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

    # Data science modules (all optional, graceful degradation)
    enable_signal_logging: bool = True    # Log ALL signals to SQLite for ML training
    enable_signal_scoring: bool = True    # XGBoost signal quality scoring
    signal_score_mode: str = "shadow"     # "shadow" (log only) or "active" (filter below threshold)
    signal_score_threshold: float = 30.0  # Min score in active mode (0-100)
    enable_fill_optimizer: bool = True    # Thompson Sampling for maker offset selection
    enable_regime_detection: bool = True  # Market regime classification from Binance prices

    class Config:
        env_file = str(ENV_PATH)
        case_sensitive = False
        extra = 'ignore'

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
