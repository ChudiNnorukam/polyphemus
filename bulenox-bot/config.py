import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class BulenoxConfig:
    # Rithmic
    rithmic_uri: str = "wss://rituz00100.rithmic.com:443"
    rithmic_system: str = "Rithmic Test"
    rithmic_user: str = ""
    rithmic_pass: str = ""
    rithmic_app_name: str = "BulenoxBot"
    rithmic_app_version: str = "1.0"

    # Instrument
    symbol: str = "MBT"
    exchange: str = "CME"
    auto_rollover: bool = True  # auto-detect front month contract symbol

    # Strategy
    binance_symbol: str = "BTCUSDT"
    momentum_window_secs: int = 60
    momentum_trigger_pct: float = 0.003
    entry_cooldown_secs: int = 120

    # Risk
    contracts: int = 1
    max_open_positions: int = 1
    take_profit_ticks: int = 4
    stop_loss_ticks: int = 2
    max_hold_secs: int = 120
    dry_run: bool = True

    # Persistence
    data_dir: str = "data"
    tick_size: float = 0.25

    # Safety — Bulenox $50K account (verified from dashboard 2026-03-16)
    kill_switch_path: str = ""          # touch this file to halt new entries
    max_daily_loss_usd: float = 900.0   # $50K EOD daily limit = $1,100, use $900 buffer
    max_trailing_drawdown: float = 2500.0  # $50K trailing drawdown limit (ACCOUNT TERMINATION if breached)
    profit_target: float = 3000.0       # $50K qualification target
    max_daily_profit_ratio: float = 0.35  # Bulenox consistency rule: no single day > 35% of total profit
    max_contracts: int = 7              # $50K account limit (start with 1, scale after proven WR)
    point_value: float = 0.1            # MBT: 0.1 BTC per contract, $0.50 per tick ($5 index points)
    force_close_ct: str = "15:55"       # Force close all positions by 15:55 CT (4 min buffer before 15:59 rule)
    fade_start_ct: str = "09:00"       # FADE window start CT (widened from 10:00 for signal volume)
    fade_end_ct: str = "16:00"         # FADE window end CT (widened from 15:00 for signal volume)
    max_basis_pct: float = 0.02        # Skip signal if |MBT - spot| / spot > 2% (v2.0 audit: basis divergence risk)
    directional_gate_wr: float = 0.30  # Skip direction if rolling WR < 30% on n>=10 (Gap #5)
    extreme_move_pct: float = 0.03     # 3% move in 1h triggers cooldown (Gap #11)
    extreme_cooldown_secs: int = 1800  # 30 min cooldown after extreme move
    atr_regime_threshold: float = 1.5  # Skip fade-against-trend if 1h ATR / 24h median ATR > threshold
    breakeven_ticks: int = 10           # Move SL to entry after N ticks favorable (was stop_loss_ticks=15)
    trailing_stop_ticks: int = 0        # Trail SL by N ticks from peak (0 = disabled)
    trailing_activation_ticks: int = 20 # Ticks in profit required before trailing activates


def load_config() -> BulenoxConfig:
    return BulenoxConfig(
        rithmic_uri=os.getenv("RITHMIC_URI", "wss://rituz00100.rithmic.com:443"),
        rithmic_system=os.getenv("RITHMIC_SYSTEM", "Rithmic Test"),
        rithmic_user=os.getenv("RITHMIC_USER", ""),
        rithmic_pass=os.getenv("RITHMIC_PASS", ""),
        rithmic_app_name=os.getenv("RITHMIC_APP_NAME", "BulenoxBot"),
        rithmic_app_version=os.getenv("RITHMIC_APP_VERSION", "1.0"),
        symbol=os.getenv("SYMBOL", "MBT"),
        exchange=os.getenv("EXCHANGE", "CME"),
        binance_symbol=os.getenv("BINANCE_SYMBOL", "BTCUSDT"),
        momentum_window_secs=int(os.getenv("MOMENTUM_WINDOW_SECS", "60")),
        momentum_trigger_pct=float(os.getenv("MOMENTUM_TRIGGER_PCT", "0.003")),
        entry_cooldown_secs=int(os.getenv("ENTRY_COOLDOWN_SECS", "120")),
        contracts=int(os.getenv("CONTRACTS", "1")),
        max_open_positions=int(os.getenv("MAX_OPEN_POSITIONS", "1")),
        take_profit_ticks=int(os.getenv("TAKE_PROFIT_TICKS", "4")),
        stop_loss_ticks=int(os.getenv("STOP_LOSS_TICKS", "2")),
        max_hold_secs=int(os.getenv("MAX_HOLD_SECS", "120")),
        dry_run=os.getenv("DRY_RUN", "true").lower() in ("1", "true", "yes"),
        data_dir=os.getenv("DATA_DIR", "data"),
        tick_size=float(os.getenv("TICK_SIZE", "0.25")),
        kill_switch_path=os.getenv("KILL_SWITCH_PATH", ""),
        max_daily_loss_usd=float(os.getenv("MAX_DAILY_LOSS_USD", "900.0")),
        max_trailing_drawdown=float(os.getenv("MAX_TRAILING_DRAWDOWN", "2500.0")),
        profit_target=float(os.getenv("PROFIT_TARGET", "3000.0")),
        max_daily_profit_ratio=float(os.getenv("MAX_DAILY_PROFIT_RATIO", "0.35")),
        max_contracts=int(os.getenv("MAX_CONTRACTS", "7")),
        point_value=float(os.getenv("POINT_VALUE", "0.1")),
        force_close_ct=os.getenv("FORCE_CLOSE_CT", "15:55"),
        fade_start_ct=os.getenv("FADE_START_CT", "09:00"),
        fade_end_ct=os.getenv("FADE_END_CT", "16:00"),
        max_basis_pct=float(os.getenv("MAX_BASIS_PCT", "0.02")),
        directional_gate_wr=float(os.getenv("DIRECTIONAL_GATE_WR", "0.30")),
        extreme_move_pct=float(os.getenv("EXTREME_MOVE_PCT", "0.03")),
        extreme_cooldown_secs=int(os.getenv("EXTREME_COOLDOWN_SECS", "1800")),
        atr_regime_threshold=float(os.getenv("ATR_REGIME_THRESHOLD", "1.5")),
        breakeven_ticks=int(os.getenv("BREAKEVEN_TICKS", "10")),
        trailing_stop_ticks=int(os.getenv("TRAILING_STOP_TICKS", "0")),
        trailing_activation_ticks=int(os.getenv("TRAILING_ACTIVATION_TICKS", "20")),
    )
