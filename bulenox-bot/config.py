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

    # Safety
    kill_switch_path: str = ""          # touch this file to halt new entries
    max_daily_loss_usd: float = 400.0   # Bulenox 25K account: $500/day limit, use $400 buffer
    max_daily_profit_ratio: float = 0.35  # Bulenox 40% consistency rule: block at 35%
    point_value: float = 0.1            # MBT: 0.1 BTC per contract = $0.10 per $1 price move


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
        max_daily_loss_usd=float(os.getenv("MAX_DAILY_LOSS_USD", "400.0")),
        max_daily_profit_ratio=float(os.getenv("MAX_DAILY_PROFIT_RATIO", "0.35")),
        point_value=float(os.getenv("POINT_VALUE", "0.1")),
    )
