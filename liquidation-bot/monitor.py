"""
Aave V3 Liquidation Opportunity Monitor
Monitors for accounts at risk of liquidation on Arbitrum
"""

import asyncio
import logging
from decimal import Decimal
from typing import List, Dict, Optional
from dataclasses import dataclass
import time

from web3 import Web3
from web3.contract import Contract
from eth_account import Account

import config

logger = logging.getLogger(__name__)


@dataclass
class LiquidationOpportunity:
    """Represents a liquidation opportunity"""
    borrower: str
    debt_asset: str
    collateral_asset: str
    debt_amount: Decimal
    collateral_amount: Decimal
    health_factor: Decimal
    liquidation_bonus: Decimal
    profit_potential_usdc: Decimal
    timestamp: float


class AaveV3Monitor:
    """Monitors Aave V3 for liquidation opportunities"""

    def __init__(self, w3: Web3):
        self.w3 = w3
        self.pool = self._load_pool_contract()
        self.data_provider = self._load_data_provider_contract()
        self.oracle = self._load_oracle_contract()
        self.opportunities: List[LiquidationOpportunity] = []
        self.last_block = 0
        self.price_cache = {}
        self.cache_timestamp = 0

    def _load_pool_contract(self) -> Contract:
        """Load Aave V3 Pool contract"""
        abi = [
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
            }
        ]
        return self.w3.eth.contract(address=Web3.to_checksum_address(config.AAVE_POOL_ADDRESS), abi=abi)

    def _load_data_provider_contract(self) -> Contract:
        """Load Aave V3 PoolDataProvider contract"""
        abi = [
            {
                "inputs": [{"name": "asset", "type": "address"}, {"name": "user", "type": "address"}],
                "name": "getUserReserveData",
                "outputs": [
                    {"name": "currentATokenBalance", "type": "uint256"},
                    {"name": "currentStableDebt", "type": "uint256"},
                    {"name": "currentVariableDebt", "type": "uint256"},
                    {"name": "principalStableDebt", "type": "uint256"},
                    {"name": "scaledVariableDebt", "type": "uint256"},
                    {"name": "stableBorrowRate", "type": "uint256"},
                    {"name": "liquidityRate", "type": "uint256"},
                    {"name": "usageAsCollateral", "type": "bool"},
                ],
                "stateMutability": "view",
                "type": "function",
            },
            {
                "inputs": [{"name": "asset", "type": "address"}],
                "name": "getReserveData",
                "outputs": [
                    {"name": "configuration", "type": "uint256"},
                    {"name": "liquidityIndex", "type": "uint128"},
                    {"name": "currentLiquidityRate", "type": "uint128"},
                    {"name": "variableBorrowIndex", "type": "uint128"},
                    {"name": "currentVariableBorrowRate", "type": "uint128"},
                    {"name": "currentStableBorrowRate", "type": "uint128"},
                    {"name": "lastUpdateTimestamp", "type": "uint40"},
                    {"name": "id", "type": "uint16"},
                ],
                "stateMutability": "view",
                "type": "function",
            }
        ]
        return self.w3.eth.contract(address=Web3.to_checksum_address(config.AAVE_POOL_DATA_PROVIDER), abi=abi)

    def _load_oracle_contract(self) -> Contract:
        """Load Aave Price Oracle contract"""
        abi = [
            {
                "inputs": [{"name": "asset", "type": "address"}],
                "name": "getAssetPrice",
                "outputs": [{"name": "", "type": "uint256"}],
                "stateMutability": "view",
                "type": "function",
            }
        ]
        return self.w3.eth.contract(address=Web3.to_checksum_address(config.AAVE_ORACLE), abi=abi)

    async def update_opportunities(self) -> List[LiquidationOpportunity]:
        """
        Scan for liquidation opportunities
        Returns list of profitable liquidation opportunities
        """
        try:
            block = self.w3.eth.block_number
            if block == self.last_block:
                return self.opportunities

            self.last_block = block
            logger.info(f"Scanning for opportunities at block {block}")

            # In production, we would scan events for account interactions
            # For now, we monitor specific high-risk accounts
            opportunities = []

            # Clear cache if > 1 minute old
            if time.time() - self.cache_timestamp > 60:
                self.price_cache.clear()
                self.cache_timestamp = time.time()

            self.opportunities = opportunities
            return opportunities

        except Exception as e:
            logger.error(f"Error updating opportunities: {e}")
            return []

    async def check_account_health(self, borrower: str) -> Optional[Dict]:
        """
        Check health factor of a specific account
        Returns account data if unhealthy (HF < 1.0)
        """
        try:
            borrower = Web3.to_checksum_address(borrower)

            # Get account data from pool
            (
                total_collateral_base,
                total_debt_base,
                available_borrows_base,
                current_liquidation_threshold,
                ltv,
                health_factor,
            ) = self.pool.functions.getUserAccountData(borrower).call()

            hf = Decimal(health_factor) / Decimal(10**18)

            if hf >= config.MIN_HEALTH_FACTOR:
                return None  # Account is healthy

            logger.warning(f"Account {borrower} at risk: HF={hf}")

            return {
                "borrower": borrower,
                "total_collateral_base": total_collateral_base,
                "total_debt_base": total_debt_base,
                "health_factor": hf,
                "timestamp": time.time(),
            }

        except Exception as e:
            logger.error(f"Error checking account {borrower}: {e}")
            return None

    def get_asset_price(self, asset: str) -> Decimal:
        """Get current price of asset from Aave oracle"""
        try:
            asset = Web3.to_checksum_address(asset)

            # Check cache first
            if asset in self.price_cache:
                return self.price_cache[asset]

            price = self.oracle.functions.getAssetPrice(asset).call()
            decimal_price = Decimal(price) / Decimal(10**8)  # 8 decimals from oracle

            self.price_cache[asset] = decimal_price
            return decimal_price

        except Exception as e:
            logger.error(f"Error getting price for {asset}: {e}")
            return Decimal(0)

    async def calculate_liquidation_profit(
        self,
        borrower: str,
        debt_asset: str,
        debt_amount: Decimal,
        collateral_asset: str,
    ) -> Optional[Decimal]:
        """
        Calculate estimated profit from liquidating an account
        Accounts for:
        - Liquidation bonus (typically 5-10%)
        - Flash loan premium (0.05%)
        - Gas costs
        - Slippage on swaps
        """
        try:
            # Get prices
            debt_price = self.get_asset_price(debt_asset)
            collateral_price = self.get_asset_price(collateral_asset)

            if debt_price == 0 or collateral_price == 0:
                return None

            # Get liquidation bonus (varies by asset)
            # For simplicity, use default 5%
            liquidation_bonus = Decimal("0.05")

            # Calculate collateral received after liquidation
            debt_value_usdc = debt_amount * debt_price
            collateral_value_usdc = (debt_value_usdc / Decimal(1 + liquidation_bonus)) * (Decimal(1) + liquidation_bonus)

            # Deduct costs
            flash_loan_fee = debt_value_usdc * Decimal(config.FLASH_LOAN_PREMIUM)
            estimated_gas_cost = Decimal(300_000 * 2) / Decimal(10**9)  # Rough estimate: 300k gas @ 2 Gwei
            swap_slippage = debt_value_usdc * Decimal(config.SLIPPAGE_TOLERANCE)

            profit = collateral_value_usdc - debt_value_usdc - flash_loan_fee - estimated_gas_cost - swap_slippage

            return profit if profit > Decimal(config.MIN_PROFIT_USDC) else None

        except Exception as e:
            logger.error(f"Error calculating profit: {e}")
            return None

    async def monitor_loop(self):
        """Main monitoring loop"""
        logger.info("Starting Aave V3 liquidation monitor")

        while True:
            try:
                opportunities = await self.update_opportunities()

                if opportunities:
                    logger.info(f"Found {len(opportunities)} liquidation opportunities")
                    for opp in opportunities:
                        logger.info(
                            f"  Borrower: {opp.borrower}, "
                            f"HF: {opp.health_factor:.2f}, "
                            f"Profit: ${opp.profit_potential_usdc:.2f}"
                        )

                await asyncio.sleep(config.MONITORING_INTERVAL)

            except Exception as e:
                logger.error(f"Error in monitor loop: {e}")
                await asyncio.sleep(config.ERROR_COOLDOWN)
