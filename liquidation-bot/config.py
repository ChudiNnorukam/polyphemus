"""
Configuration for Aave V3 Liquidation Bot on Arbitrum.
Provides both module-level constants and a config object.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # Network
    chain_id: int = 42161
    arbitrum_rpc: str = field(default_factory=lambda: os.getenv("ARBITRUM_RPC", "https://arb1.arbitrum.io/rpc"))
    arbitrum_rpc_events: str = field(default_factory=lambda: os.getenv("ARBITRUM_RPC_EVENTS", "https://arb1.arbitrum.io/rpc"))

    # Wallet
    wallet_address: str = field(default_factory=lambda: os.getenv("WALLET_ADDRESS", ""))
    private_key: str = field(default_factory=lambda: os.getenv("PRIVATE_KEY", ""))

    # Aave V3 Arbitrum addresses
    aave_pool: str = "0x794a61358D6845594F94dc1DB02A252b5b4814aD"
    aave_pool_data_provider: str = "0x69FA688f1Dc47d4B5d8029D5a35FB7a548310654"
    aave_oracle: str = "0xb56c2F0B653B2e0b10C9b928C8580Ac5Df02C7C7"

    # Tokens (Arbitrum)
    usdc: str = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
    weth: str = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
    wbtc: str = "0x2f2a2543B6B1d0A8b6464f64C164F94aFfBd2b0F"
    arb: str = "0x912CE59144191C1204E64559FE8253a0e49E6548"

    # Liquidator contract (deployed by user)
    liquidator_contract: str = field(default_factory=lambda: os.getenv("LIQUIDATOR_CONTRACT", ""))

    # DEX
    uniswap_v3_router: str = "0xE592427A0AEce92De3Edee1F18E0157C05861564"
    uniswap_v3_quoter: str = "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6"

    # Thresholds
    min_profit_usd: float = 5.0
    min_health_factor: float = 1.0
    flash_loan_fee: float = 0.0005  # 0.05%
    slippage_tolerance: float = 0.005  # 0.5%
    gas_buffer_multiplier: float = 1.3

    # Monitoring
    check_interval: int = field(default_factory=lambda: int(os.getenv("CHECK_INTERVAL", "12")))
    batch_size: int = 50
    health_check_interval: int = 300  # 5 minutes
    retry_delay: int = 30

    # Database
    db_path: str = "data/liquidations.db"

    # Logging
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    # Telegram (optional)
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))

    # Mode
    dry_run: bool = field(default_factory=lambda: os.getenv("DRY_RUN", "true").lower() == "true")

    def validate(self):
        if not self.wallet_address or not self.private_key:
            if not self.dry_run:
                raise ValueError("WALLET_ADDRESS and PRIVATE_KEY required for live mode")


config = Config()
