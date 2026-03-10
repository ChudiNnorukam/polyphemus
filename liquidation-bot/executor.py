"""
Flash Loan Executor for Aave V3 Liquidation
Executes liquidations using flash loans (0 capital required)
"""

import asyncio
import logging
from decimal import Decimal
from typing import Optional, Tuple
import time

from web3 import Web3
from web3.contract import Contract
from web3.types import TxParams
import sqlite3

import config

logger = logging.getLogger(__name__)


class FlashLoanExecutor:
    """Executes liquidations via Aave V3 flash loans"""

    def __init__(self, w3: Web3, private_key: str, wallet_address: str):
        self.w3 = w3
        self.private_key = private_key
        self.wallet_address = Web3.to_checksum_address(wallet_address)
        self.pool = self._load_pool_contract()
        self.executor_contract = None
        self.pending_txns = {}
        self.nonce = self.w3.eth.get_transaction_count(self.wallet_address)

    def _load_pool_contract(self) -> Contract:
        """Load Aave V3 Pool contract"""
        abi = [
            {
                "inputs": [
                    {"name": "receiverAddress", "type": "address"},
                    {"name": "token", "type": "address"},
                    {"name": "amount", "type": "uint256"},
                    {"name": "params", "type": "bytes"},
                ],
                "name": "flashLoanSimple",
                "outputs": [{"name": "", "type": "bool"}],
                "stateMutability": "nonpayable",
                "type": "function",
            },
            {
                "inputs": [
                    {"name": "receiver", "type": "address"},
                    {"name": "tokens", "type": "address[]"},
                    {"name": "amounts", "type": "uint256[]"},
                    {"name": "modes", "type": "uint256[]"},
                    {"name": "onBehalfOf", "type": "address"},
                    {"name": "params", "type": "bytes"},
                    {"name": "referralCode", "type": "uint16"},
                ],
                "name": "flashLoan",
                "outputs": [{"name": "", "type": "bool"}],
                "stateMutability": "nonpayable",
                "type": "function",
            }
        ]
        return self.w3.eth.contract(address=Web3.to_checksum_address(config.AAVE_POOL_ADDRESS), abi=abi)

    async def execute_liquidation(
        self,
        borrower: str,
        debt_asset: str,
        debt_amount: int,
        collateral_asset: str,
        expected_profit: Decimal,
    ) -> Optional[str]:
        """
        Execute liquidation using flash loan
        
        Args:
            borrower: Address of the borrower to liquidate
            debt_asset: Asset address to repay
            debt_amount: Amount of debt to repay (wei)
            collateral_asset: Collateral asset to receive
            expected_profit: Expected profit in USDC
            
        Returns:
            Transaction hash if successful, None otherwise
        """
        try:
            borrower = Web3.to_checksum_address(borrower)
            debt_asset = Web3.to_checksum_address(debt_asset)
            collateral_asset = Web3.to_checksum_address(collateral_asset)

            logger.info(f"Executing liquidation for {borrower}")
            logger.info(f"  Debt: {debt_amount / 10**6:.2f} (asset: {debt_asset})")
            logger.info(f"  Collateral: {collateral_asset}")
            logger.info(f"  Expected profit: ${expected_profit:.2f}")

            # Calculate flash loan premium
            premium = debt_amount * config.FLASH_LOAN_PREMIUM
            total_owed = debt_amount + int(premium)

            # Check if profitable after premium
            if expected_profit < Decimal(config.MIN_PROFIT_USDC):
                logger.warning(f"Liquidation not profitable: ${expected_profit:.2f} < ${config.MIN_PROFIT_USDC}")
                return None

            # Prepare flash loan parameters
            params = self._encode_liquidation_params(
                borrower=borrower,
                debt_asset=debt_asset,
                collateral_asset=collateral_asset,
            )

            # Build transaction
            tx = await self._build_flash_loan_tx(
                token=debt_asset,
                amount=debt_amount,
                params=params,
            )

            # Estimate gas
            try:
                gas_estimate = self.w3.eth.estimate_gas(tx)
                tx["gas"] = int(gas_estimate * 1.2)  # Add 20% buffer
            except Exception as e:
                logger.warning(f"Gas estimate failed, using default: {e}")
                tx["gas"] = 2_000_000

            # Sign and send
            tx_hash = await self._send_transaction(tx)
            if tx_hash:
                self.pending_txns[tx_hash] = {
                    "borrower": borrower,
                    "debt_amount": debt_amount,
                    "expected_profit": expected_profit,
                    "timestamp": time.time(),
                }
                logger.info(f"Transaction sent: {tx_hash}")
                return tx_hash
            else:
                return None

        except Exception as e:
            logger.error(f"Error executing liquidation: {e}")
            return None

    def _encode_liquidation_params(
        self,
        borrower: str,
        debt_asset: str,
        collateral_asset: str,
    ) -> bytes:
        """Encode parameters for flash loan callback"""
        # In production, this would encode the liquidation logic
        # For now, we return simple encoding
        return Web3.keccak(
            text=f"{borrower}{debt_asset}{collateral_asset}"
        )

    async def _build_flash_loan_tx(self, token: str, amount: int, params: bytes) -> TxParams:
        """Build flash loan transaction"""
        tx = {
            "from": self.wallet_address,
            "to": self.pool.address,
            "data": self.pool.encodeABI(
                fn_name="flashLoanSimple",
                args=[self.wallet_address, token, amount, params],
            ),
            "value": 0,
            "nonce": self.nonce,
            "chainId": config.CHAIN_ID,
        }

        # Get gas price
        gas_price = self.w3.eth.gas_price
        tx["gasPrice"] = int(gas_price * config.GAS_PRICE_MULTIPLIER)

        self.nonce += 1
        return tx

    async def _send_transaction(self, tx: TxParams) -> Optional[str]:
        """Sign and send transaction"""
        try:
            from eth_account import Account

            account = Account.from_key(self.private_key)
            signed_tx = self.w3.eth.account.sign_transaction(tx, self.private_key)

            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            return tx_hash.hex()

        except Exception as e:
            logger.error(f"Error sending transaction: {e}")
            return None

    async def wait_for_confirmation(self, tx_hash: str, timeout: int = 600) -> bool:
        """Wait for transaction confirmation"""
        try:
            start_time = time.time()

            while time.time() - start_time < timeout:
                receipt = self.w3.eth.get_transaction_receipt(tx_hash)

                if receipt:
                    if receipt["status"] == 1:
                        logger.info(f"Transaction confirmed: {tx_hash}")
                        self._record_trade(tx_hash, receipt, success=True)
                        return True
                    else:
                        logger.error(f"Transaction failed: {tx_hash}")
                        self._record_trade(tx_hash, receipt, success=False)
                        return False

                await asyncio.sleep(5)

            logger.error(f"Transaction timeout: {tx_hash}")
            return False

        except Exception as e:
            logger.error(f"Error waiting for confirmation: {e}")
            return False

    def _record_trade(self, tx_hash: str, receipt: dict, success: bool):
        """Record executed trade in database"""
        try:
            conn = sqlite3.connect(config.TRADES_DB_PATH)
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT INTO trades (tx_hash, block_number, status, gas_used, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    tx_hash,
                    receipt.get("blockNumber"),
                    "success" if success else "failed",
                    receipt.get("gasUsed"),
                    time.time(),
                ),
            )
            conn.commit()
            conn.close()

        except Exception as e:
            logger.error(f"Error recording trade: {e}")

    async def executor_loop(self):
        """Main executor loop for managing pending transactions"""
        logger.info("Starting flash loan executor")

        while True:
            try:
                # Check pending transactions
                pending = list(self.pending_txns.keys())

                for tx_hash in pending:
                    confirmed = await self.wait_for_confirmation(tx_hash, timeout=60)

                    if confirmed or time.time() - self.pending_txns[tx_hash]["timestamp"] > config.TX_TIMEOUT:
                        del self.pending_txns[tx_hash]

                await asyncio.sleep(10)

            except Exception as e:
                logger.error(f"Error in executor loop: {e}")
                await asyncio.sleep(config.ERROR_COOLDOWN)
