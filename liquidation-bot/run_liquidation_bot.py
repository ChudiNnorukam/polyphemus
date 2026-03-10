"""
Aave V3 Liquidation Bot - Arbitrum
Monitors for liquidatable positions and executes via flash loans ($0 capital).
Starts in DRY_RUN=true (monitor-only) by default.
"""

import asyncio
import logging
import signal
import sys
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Tuple
from pathlib import Path

from web3 import Web3
from config import config
from database import LiquidationDatabase
from healthcheck import HealthStatus, TelegramNotifier, notify_ready, notify_watchdog

logging.basicConfig(
    level=getattr(logging, config.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("liquidation-bot")

# --- Minimal ABIs (only the functions we call) ---

POOL_ABI = [
    {
        "inputs": [{"name": "user", "type": "address"}],
        "name": "getUserAccountData",
        "outputs": [
            {"name": "totalCollateralBase", "type": "uint256"},
            {"name": "totalDebtBase", "type": "uint256"},
            {"name": "availableBorrowsBase", "type": "uint256"},
            {"name": "currentLiquidationThreshold", "type": "uint256"},
            {"name": "ltv", "type": "uint256"},
            {"name": "healthFactor", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

ORACLE_ABI = [
    {
        "inputs": [{"name": "asset", "type": "address"}],
        "name": "getAssetPrice",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

POOL_DATA_PROVIDER_ABI = [
    {
        "inputs": [
            {"name": "asset", "type": "address"},
            {"name": "user", "type": "address"},
        ],
        "name": "getUserReserveData",
        "outputs": [
            {"name": "currentATokenBalance", "type": "uint256"},
            {"name": "currentStableDebt", "type": "uint256"},
            {"name": "currentVariableDebt", "type": "uint256"},
            {"name": "principalStableDebt", "type": "uint256"},
            {"name": "scaledVariableDebt", "type": "uint256"},
            {"name": "stableBorrowRate", "type": "uint256"},
            {"name": "liquidityRate", "type": "uint256"},
            {"name": "usageAsCollateralEnabled", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

LIQUIDATOR_ABI = [
    {
        "inputs": [
            {"name": "collateralAsset", "type": "address"},
            {"name": "debtAsset", "type": "address"},
            {"name": "borrower", "type": "address"},
            {"name": "debtToCover", "type": "uint256"},
        ],
        "name": "liquidateWithFlashLoan",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


@dataclass
class LiquidatablePosition:
    user: str
    total_collateral_base: int  # 8 decimals (USD)
    total_debt_base: int        # 8 decimals (USD)
    health_factor: int          # 1e18 scale
    health_factor_normalized: float


class LiquidationBot:
    """Main bot: monitor Aave V3, find liquidatable positions, execute via flash loans."""

    def __init__(self):
        config.validate()
        self.running = True
        self.w3 = Web3(Web3.HTTPProvider(config.arbitrum_rpc))
        # Separate RPC for event scanning (public RPC has no block range limit)
        self.w3_events = Web3(Web3.HTTPProvider(config.arbitrum_rpc_events))

        # Contracts
        self.pool = self.w3.eth.contract(
            address=Web3.to_checksum_address(config.aave_pool), abi=POOL_ABI
        )
        self.oracle = self.w3.eth.contract(
            address=Web3.to_checksum_address(config.aave_oracle), abi=ORACLE_ABI
        )
        self.data_provider = self.w3.eth.contract(
            address=Web3.to_checksum_address(config.aave_pool_data_provider),
            abi=POOL_DATA_PROVIDER_ABI,
        )

        if config.liquidator_contract:
            self.liquidator = self.w3.eth.contract(
                address=Web3.to_checksum_address(config.liquidator_contract),
                abi=LIQUIDATOR_ABI,
            )
        else:
            self.liquidator = None

        # State
        Path("data").mkdir(exist_ok=True)
        self.db = LiquidationDatabase(config.db_path)
        self.health = HealthStatus("data")
        self.telegram = TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id)

        # Borrower cache (populated from events)
        self.known_borrowers: set = set()
        self.last_event_block = 0

        logger.info(
            f"Bot initialized: chain={config.chain_id}, "
            f"rpc={config.arbitrum_rpc[:40]}..., "
            f"dry_run={config.dry_run}, "
            f"liquidator={'SET' if config.liquidator_contract else 'NOT SET (monitor only)'}"
        )

    # --- Signal Handlers ---

    def setup_signals(self):
        def handler(sig, frame):
            logger.info(f"Signal {sig} received, shutting down...")
            self.running = False

        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

    # --- Borrower Discovery ---

    def discover_borrowers_from_events(self, lookback_blocks: int = 50000) -> int:
        """Scan recent Borrow events using public RPC (no block range limit)."""
        try:
            current_block = self.w3_events.eth.block_number
            from_block = max(0, current_block - lookback_blocks)

            if from_block <= self.last_event_block:
                from_block = self.last_event_block + 1

            if from_block >= current_block:
                return 0

            borrow_topic = Web3.keccak(text="Borrow(address,address,address,uint256,uint8,uint256,uint16)")
            topic_hex = "0x" + borrow_topic.hex()
            pool_addr = Web3.to_checksum_address(config.aave_pool)

            logs = self.w3_events.eth.get_logs({
                "fromBlock": from_block,
                "toBlock": current_block,
                "address": pool_addr,
                "topics": [topic_hex],
            })

            new_count = 0
            for log in logs:
                if len(log["topics"]) >= 3:
                    borrower = "0x" + log["topics"][2].hex()[-40:]
                    if borrower not in self.known_borrowers:
                        self.known_borrowers.add(borrower)
                        new_count += 1

            self.last_event_block = current_block
            logger.info(
                f"Event scan: block {from_block}-{current_block}, "
                f"{len(logs)} events, {new_count} new borrowers, "
                f"{len(self.known_borrowers)} total tracked"
            )
            return new_count

        except Exception as e:
            logger.error(f"Event scan error: {e}")
            return 0

    # --- Health Factor Checking ---

    def check_health_factor(self, user: str) -> Optional[LiquidatablePosition]:
        """Check if a user's position is liquidatable (HF < 1.0)."""
        try:
            user = Web3.to_checksum_address(user)
            result = self.pool.functions.getUserAccountData(user).call()

            total_collateral_base = result[0]
            total_debt_base = result[1]
            health_factor = result[5]

            # No debt = not liquidatable
            if total_debt_base == 0:
                return None

            hf_normalized = health_factor / 1e18

            # Only return if below threshold
            if hf_normalized < config.min_health_factor:
                return LiquidatablePosition(
                    user=user,
                    total_collateral_base=total_collateral_base,
                    total_debt_base=total_debt_base,
                    health_factor=health_factor,
                    health_factor_normalized=hf_normalized,
                )
            return None

        except Exception as e:
            logger.debug(f"HF check failed for {user[:10]}...: {e}")
            return None

    def scan_for_liquidations(self) -> List[LiquidatablePosition]:
        """Scan all known borrowers for liquidatable positions."""
        liquidatable = []
        checked = 0

        for user in list(self.known_borrowers):
            if not self.running:
                break

            pos = self.check_health_factor(user)
            checked += 1

            if pos:
                liquidatable.append(pos)

            # Rate limit + watchdog ping
            if checked % 20 == 0:
                time.sleep(0.1)
            if checked % 50 == 0:
                notify_watchdog()

        return liquidatable

    # --- Profit Estimation ---

    def estimate_profit(
        self, position: LiquidatablePosition
    ) -> Tuple[float, dict]:
        """Estimate profit from liquidating a position."""
        try:
            # Aave V3 base currency is USD with 8 decimals
            debt_usd = position.total_debt_base / 1e8
            collateral_usd = position.total_collateral_base / 1e8

            # Close factor: 50% when HF > 0.95, 100% when HF <= 0.95
            if position.health_factor_normalized > 0.95:
                close_factor = 0.5
            else:
                close_factor = 1.0

            liquidatable_debt_usd = debt_usd * close_factor

            # Liquidation bonus (typically 5-10% on Aave V3 Arbitrum)
            bonus_pct = 0.05  # Conservative 5%
            collateral_received_usd = liquidatable_debt_usd * (1 + bonus_pct)

            # Costs
            flash_loan_fee_usd = liquidatable_debt_usd * config.flash_loan_fee
            gas_price = self.w3.eth.gas_price
            gas_cost_eth = (800_000 * gas_price) / 1e18  # ~800k gas for liquidation
            eth_price = self.oracle.functions.getAssetPrice(
                Web3.to_checksum_address(config.weth)
            ).call() / 1e8
            gas_cost_usd = gas_cost_eth * eth_price
            slippage_usd = liquidatable_debt_usd * config.slippage_tolerance

            profit_usd = (
                collateral_received_usd
                - liquidatable_debt_usd
                - flash_loan_fee_usd
                - gas_cost_usd
                - slippage_usd
            )

            details = {
                "debt_usd": debt_usd,
                "collateral_usd": collateral_usd,
                "liquidatable_debt_usd": liquidatable_debt_usd,
                "bonus_pct": bonus_pct,
                "flash_loan_fee_usd": flash_loan_fee_usd,
                "gas_cost_usd": gas_cost_usd,
                "slippage_usd": slippage_usd,
                "close_factor": close_factor,
            }

            return profit_usd, details

        except Exception as e:
            logger.error(f"Profit estimation error: {e}")
            return 0.0, {}

    # --- Execution ---

    def execute_liquidation(self, position: LiquidatablePosition, profit_usd: float) -> bool:
        """Execute a liquidation via the FlashLiquidator contract."""
        if config.dry_run:
            logger.info(f"[DRY RUN] Would liquidate {position.user[:10]}... profit=${profit_usd:.2f}")
            return True

        if not self.liquidator:
            logger.warning("No liquidator contract set, skipping execution")
            return False

        if not config.private_key:
            logger.warning("No private key set, skipping execution")
            return False

        try:
            # Build transaction
            debt_amount_raw = int(position.total_debt_base / 2)  # 50% close factor, raw units

            tx = self.liquidator.functions.liquidateWithFlashLoan(
                Web3.to_checksum_address(config.weth),   # collateral
                Web3.to_checksum_address(config.usdc),    # debt
                Web3.to_checksum_address(position.user),
                debt_amount_raw,
            ).build_transaction({
                "from": Web3.to_checksum_address(config.wallet_address),
                "gas": 2_000_000,
                "gasPrice": int(self.w3.eth.gas_price * config.gas_buffer_multiplier),
                "nonce": self.w3.eth.get_transaction_count(
                    Web3.to_checksum_address(config.wallet_address)
                ),
                "chainId": config.chain_id,
            })

            # Sign and send
            signed = self.w3.eth.account.sign_transaction(tx, config.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            logger.info(f"TX sent: {tx_hash.hex()}")

            # Wait for confirmation
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

            if receipt["status"] == 1:
                logger.info(f"Liquidation SUCCESS: {tx_hash.hex()}")
                return True
            else:
                logger.error(f"Liquidation REVERTED: {tx_hash.hex()}")
                return False

        except Exception as e:
            logger.error(f"Execution error: {e}")
            return False

    # --- Main Loop ---

    async def run(self):
        """Main bot loop."""
        self.setup_signals()
        logger.info("=" * 60)
        logger.info("Aave V3 Liquidation Bot Starting")
        logger.info(f"  Chain: Arbitrum (ID {config.chain_id})")
        logger.info(f"  Mode: {'DRY RUN (monitor only)' if config.dry_run else 'LIVE'}")
        logger.info(f"  Min profit: ${config.min_profit_usd}")
        logger.info(f"  Check interval: {config.check_interval}s")
        logger.info("=" * 60)

        # Verify connection
        try:
            chain_id = self.w3.eth.chain_id
            block = self.w3.eth.block_number
            logger.info(f"Connected: chain={chain_id}, block={block}")
            if chain_id != config.chain_id:
                logger.error(f"Wrong chain: got {chain_id}, expected {config.chain_id}")
                return
        except Exception as e:
            logger.error(f"Cannot connect to RPC: {e}")
            return

        # Initial borrower discovery
        logger.info("Discovering borrowers from recent events...")
        self.discover_borrowers_from_events(lookback_blocks=100_000)
        logger.info(f"Tracking {len(self.known_borrowers)} borrowers")

        await self.telegram.notify_startup()
        notify_ready()

        scan_count = 0
        last_health_time = time.time()
        last_event_scan = time.time()

        while self.running:
            try:
                start = time.time()
                scan_count += 1

                # Scan for liquidatable positions
                liquidatable = self.scan_for_liquidations()
                scan_ms = (time.time() - start) * 1000
                self.health.record_scan(scan_ms, len(liquidatable))

                logger.info(
                    f"Scan #{scan_count}: {len(self.known_borrowers)} borrowers, "
                    f"{len(liquidatable)} liquidatable, {scan_ms:.0f}ms"
                )

                # Process each opportunity
                for pos in liquidatable:
                    if not self.running:
                        break

                    profit_usd, details = self.estimate_profit(pos)

                    logger.info(
                        f"  {pos.user[:10]}... HF={pos.health_factor_normalized:.4f} "
                        f"debt=${pos.total_debt_base/1e8:.2f} "
                        f"collateral=${pos.total_collateral_base/1e8:.2f} "
                        f"est_profit=${profit_usd:.2f}"
                    )

                    # Log opportunity to DB
                    self.db.log_opportunity(
                        pos.user,
                        pos.total_collateral_base / 1e8,
                        pos.total_debt_base / 1e8,
                        pos.health_factor_normalized,
                        True,
                    )

                    # Check profitability
                    if profit_usd < config.min_profit_usd:
                        logger.debug(f"  Skipping: profit ${profit_usd:.2f} < min ${config.min_profit_usd}")
                        continue

                    # Log + execute
                    liq_id = self.db.log_liquidation(
                        pos.user, config.weth, config.usdc,
                        pos.total_debt_base / 1e8, profit_usd,
                        status="attempting",
                    )

                    success = self.execute_liquidation(pos, profit_usd)

                    if success:
                        self.db.update_liquidation_result(
                            liq_id, "success",
                            actual_profit=profit_usd,
                            gas_cost=details.get("gas_cost_usd", 0),
                        )
                        self.health.record_liquidation(profit_usd)
                        await self.telegram.notify_liquidation(
                            pos.user, pos.total_debt_base / 1e8, profit_usd
                        )
                    else:
                        self.db.update_liquidation_result(liq_id, "failed", error_msg="Execution failed")
                        self.health.record_error()

                # Periodic event scan (every 5 minutes)
                if time.time() - last_event_scan > 300:
                    self.discover_borrowers_from_events(lookback_blocks=5000)
                    last_event_scan = time.time()

                # Periodic health check
                if time.time() - last_health_time > config.health_check_interval:
                    self.health.write_status_file()
                    self.health.log_status()
                    stats = self.db.get_liquidation_stats()
                    logger.info(f"Stats: {stats}")
                    notify_watchdog()
                    last_health_time = time.time()

                # Wait before next scan
                await asyncio.sleep(config.check_interval)

            except Exception as e:
                logger.error(f"Main loop error: {e}", exc_info=True)
                self.health.record_error()
                await asyncio.sleep(config.retry_delay)

        # Shutdown
        logger.info("Shutting down...")
        self.health.write_status_file()
        await self.telegram.notify_shutdown(self.health.total_profit)
        logger.info(f"Final stats: {self.db.get_liquidation_stats()}")


if __name__ == "__main__":
    bot = LiquidationBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Interrupted")
    except Exception as e:
        logger.error(f"Fatal: {e}", exc_info=True)
        sys.exit(1)
