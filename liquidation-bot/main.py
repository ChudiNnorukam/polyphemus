"""
Aave V3 Liquidation Bot - Main Entry Point
Arbitrum flash loan liquidation bot with 0 capital required
"""

import asyncio
import logging
import sys
import sqlite3
from pathlib import Path
from datetime import datetime

from web3 import Web3
import config
from monitor import AaveV3Monitor
from executor import FlashLoanExecutor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format=config.LOG_FORMAT,
    handlers=[
        logging.FileHandler(f"{config.LOG_DIR}/bot.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


class LiquidationBot:
    """Main liquidation bot orchestrator"""

    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(config.RPC_URL))
        
        if not self.w3.is_connected():
            raise ConnectionError("Failed to connect to Arbitrum RPC")

        logger.info(f"Connected to Arbitrum (chain: {self.w3.eth.chain_id})")

        self.monitor = AaveV3Monitor(self.w3)
        self.executor = FlashLoanExecutor(
            self.w3,
            config.PRIVATE_KEY,
            config.WALLET_ADDRESS,
        )
        self.running = False

    async def initialize_database(self):
        """Initialize SQLite databases"""
        try:
            # Ensure directories exist
            Path(config.LOG_DIR).mkdir(parents=True, exist_ok=True)
            Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)

            # Create trades database
            conn = sqlite3.connect(config.TRADES_DB_PATH)
            cursor = conn.cursor()

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY,
                    tx_hash TEXT UNIQUE,
                    block_number INTEGER,
                    status TEXT,
                    gas_used INTEGER,
                    timestamp REAL
                )
                """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS opportunities (
                    id INTEGER PRIMARY KEY,
                    borrower TEXT,
                    debt_asset TEXT,
                    collateral_asset TEXT,
                    health_factor REAL,
                    profit_potential REAL,
                    timestamp REAL
                )
                """
            )

            conn.commit()
            conn.close()

            logger.info("Databases initialized")

        except Exception as e:
            logger.error(f"Error initializing database: {e}")
            raise

    async def run(self):
        """Main bot loop"""
        try:
            await self.initialize_database()

            logger.info(f"Starting Liquidation Bot v{config.BOT_VERSION}")
            logger.info(f"Wallet: {config.WALLET_ADDRESS}")
            logger.info(f"Network: {config.NETWORK} (chain {config.CHAIN_ID})")
            logger.info(f"Min profit threshold: ${config.MIN_PROFIT_USDC}")
            logger.info(f"DRY RUN: {config.DRY_RUN}")

            self.running = True

            # Run monitor and executor concurrently
            tasks = [
                asyncio.create_task(self.monitor.monitor_loop()),
                asyncio.create_task(self.executor.executor_loop()),
                asyncio.create_task(self.health_check_loop()),
            ]

            await asyncio.gather(*tasks)

        except KeyboardInterrupt:
            logger.info("Bot interrupted by user")
            await self.shutdown()

        except Exception as e:
            logger.error(f"Fatal error: {e}")
            await self.shutdown()
            raise

    async def health_check_loop(self):
        """Periodic health checks"""
        logger.info("Starting health check loop")

        while self.running:
            try:
                # Check RPC connection
                block = self.w3.eth.block_number
                logger.debug(f"Current block: {block}")

                # Check wallet balance
                balance = self.w3.eth.get_balance(self.executor.wallet_address)
                balance_eth = self.w3.from_wei(balance, "ether")

                if balance_eth < 0.01:
                    logger.warning(f"Low ETH balance: {balance_eth:.4f} ETH")

                # Check pending transactions
                pending_count = len(self.executor.pending_txns)
                if pending_count > 0:
                    logger.info(f"Pending transactions: {pending_count}")

                await asyncio.sleep(config.HEALTH_CHECK_INTERVAL)

            except Exception as e:
                logger.error(f"Health check error: {e}")
                await asyncio.sleep(config.ERROR_COOLDOWN)

    async def shutdown(self):
        """Graceful shutdown"""
        logger.info("Shutting down bot...")
        self.running = False

        # Wait for pending transactions
        if self.executor.pending_txns:
            logger.info(f"Waiting for {len(self.executor.pending_txns)} pending transactions...")
            timeout = 300  # 5 minutes timeout

            start = asyncio.get_event_loop().time()
            while self.executor.pending_txns:
                if asyncio.get_event_loop().time() - start > timeout:
                    logger.warning("Timeout waiting for pending transactions")
                    break
                await asyncio.sleep(5)

        logger.info("Bot shutdown complete")


async def main():
    """Entry point"""
    try:
        bot = LiquidationBot()
        await bot.run()

    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
