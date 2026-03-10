#!/usr/bin/env python3
"""
signal_executor.py - Real order execution for signal-based trading
Integrates with paper_trader to execute limit orders via CLOB API
"""
import os
import asyncio
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, ApiCreds
from py_clob_client.order_builder.constants import BUY, SELL
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
from web3 import Web3

from signal_types import FilteredSignal, Signal
from signal_config import (
    CLOB_HOST, MAX_POSITION_SIZE, OFFSET_HIGH_PRICE,
    OFFSET_LOW_PRICE, OFFSET_DEFAULT
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Price thresholds for offset calculation
PRICE_HIGH_THRESHOLD = 0.70
PRICE_LOW_THRESHOLD = 0.30

# Position sizing constants
COMPOUNDING_BASELINE = 384.0  # Balance at which compounding multiplier = 1.0 (Phase 2)
MAX_POSITION_SPEND = 200.0    # Hard cap per trade regardless of multiplier


@dataclass
class ExecutorConfig:
    """Configuration for order executor"""
    private_key: str
    wallet_address: str
    clob_api_key: str = ''
    clob_secret: str = ''
    clob_passphrase: str = ''
    chain_id: int = 137
    dry_run: bool = True  # Default to paper mode

    @classmethod
    def from_env(cls) -> 'ExecutorConfig':
        return cls(
            private_key=os.getenv('PRIVATE_KEY', ''),
            wallet_address=os.getenv('WALLET_ADDRESS', ''),
            clob_api_key=os.getenv('CLOB_API_KEY', ''),
            clob_secret=os.getenv('CLOB_SECRET', ''),
            clob_passphrase=os.getenv('CLOB_PASSPHRASE', ''),
            chain_id=int(os.getenv('POLYGON_CHAIN_ID', '137')),
            dry_run=os.getenv('DRY_RUN', 'true').lower() == 'true'
        )


class SignalExecutor:
    """
    Execute limit orders based on filtered signals.
    Supports both paper (dry run) and live trading.
    """

    def __init__(self, config: Optional[ExecutorConfig] = None):
        self.config = config or ExecutorConfig.from_env()
        self.client: Optional[ClobClient] = None
        self.orders_placed = 0
        self.orders_filled = 0
        self.total_volume = 0.0
        self.self_tuner = None  # Set by SignalBot after init

        if not self.config.dry_run:
            self._init_client()

    def _init_client(self) -> None:
        """Initialize CLOB client with API credentials"""
        if not self.config.private_key:
            raise ValueError("PRIVATE_KEY required for live trading")

        self.client = ClobClient(
            host=CLOB_HOST,
            key=self.config.private_key,
            chain_id=self.config.chain_id,
            signature_type=1,
            funder=self.config.wallet_address,
        )

        # Set API credentials
        if self.config.clob_api_key and self.config.clob_secret:
            creds = ApiCreds(
                api_key=self.config.clob_api_key,
                api_secret=self.config.clob_secret,
                api_passphrase=self.config.clob_passphrase,
            )
            self.client.set_api_creds(creds)
            logger.info("CLOB client initialized with API creds")
        else:
            # Derive credentials if not provided
            creds = self.client.create_or_derive_api_creds()
            self.client.set_api_creds(creds)
            logger.info("CLOB client initialized with derived creds")

    def calculate_limit_price(self, db_price: float, direction: str = "BUY") -> float:
        """
        Calculate limit price with dynamic offset based on price level and direction.
        BUY: offset above DB price (aggressive fill for scalp entry)
        SELL: offset below DB price (aggressive fill for scalp exit)
        """
        if db_price > PRICE_HIGH_THRESHOLD:
            offset = OFFSET_HIGH_PRICE
        elif db_price < PRICE_LOW_THRESHOLD:
            offset = OFFSET_LOW_PRICE
        else:
            offset = OFFSET_DEFAULT

        if direction == "SELL":
            # SELL: accept slightly less for faster exit
            limit_price = max(0.01, db_price - offset)
        else:
            # BUY: pay slightly more for faster fill (scalp copy needs speed)
            limit_price = min(0.99, db_price + offset)
        return round(limit_price, 2)

    def calculate_position_size(self, price: float, available_capital: float, score: float = 0.5, cold_streak: bool = False) -> float:
        """
        Calculate position size based on entry price confidence (DB strategy match).

        DB wallet analysis shows asymmetric sizing:
        - High entry price (>=0.80) = high confidence = BIG bet
        - Medium entry (0.40-0.70) = moderate confidence = medium bet
        - Low entry (<0.40) = low confidence lottery = small bet

        Phase 1: Concentrated sizing on high-confidence entries.
        Phase 2 (future): Compounding multiplier via COMPOUNDING_BASELINE.
        """
        # Wallet-adjusted sizing by entry price (2026-02-05)
        # Scales with balance: percentage-based with floor/ceiling
        # At $400: best=$28, strong=$20. At $800: best=$50, strong=$40.
        if price >= 0.90:
            max_spend = 5.00   # 0.90+: coin-flip zone, absolute minimum only
        elif price >= 0.80:
            # 0.80-0.90: 49.5% WR = coin flip, skip entirely
            logger.info(f"SKIP: entry price {price:.2f} in 0.80-0.90 dead zone")
            return 0
        elif price >= 0.70:
            max_spend = max(5.0, min(available_capital * 0.05, 40.0))    # Strong zone (88% WR)
        elif price >= 0.65:
            max_spend = max(5.0, min(available_capital * 0.07, 50.0))    # Best zone (92% WR)
        elif price >= 0.60:
            max_spend = max(5.0, min(available_capital * 0.025, 15.0))   # Limited data
        elif price >= 0.40:
            max_spend = max(5.0, min(available_capital * 0.02, 10.0))    # Medium
        elif price >= 0.20:
            max_spend = 5.00   # Low confidence
        else:
            max_spend = 3.00   # Lottery ticket

        # Boost by filter score (secondary factor, +/- 20%)
        if score >= 0.70:
            max_spend *= 1.20
        elif score < 0.40:
            max_spend *= 0.80

        # Cold streak guard: halve position sizes when DB is on a losing streak
        if cold_streak:
            max_spend *= 0.50
            # Safety: if halved size can't buy 5 shares (Polymarket minimum), skip trade
            min_usd_for_5_shares = 5.0 * price
            if max_spend < min_usd_for_5_shares:
                logger.warning(f"COLD STREAK SKIP: ${max_spend:.2f} < ${min_usd_for_5_shares:.2f} needed for 5 shares at {price:.2f}")
                return 0  # Caller should skip this trade

        # Hard cap per trade
        max_spend = min(max_spend, MAX_POSITION_SPEND)

        # Cap to available capital
        max_spend = min(max_spend, available_capital * 0.30)
        # Self-tuning multiplier (safe: returns 1.0 on any error)
        if hasattr(self, 'self_tuner') and self.self_tuner:
            _tuner_mult = self.self_tuner.get_multiplier(price)
            if abs(_tuner_mult - 1.0) > 0.001:
                _pre_tune = max_spend
                max_spend = max_spend * _tuner_mult
                logger.info(f"TUNER: ${_pre_tune:.2f} x {_tuner_mult:.3f} = ${max_spend:.2f}")

        # Calculate shares
        shares = max_spend / price if price > 0 else 0
        shares = max(shares, 5.0)  # Polymarket minimum 5 shares
        return round(shares, 2)

    async def execute_signal(
        self,
        signal: dict,
        filtered_result: dict,
        available_capital: float = 50.0,
        cold_streak: bool = False,
        deployment_ratio: float = 0.0
    ) -> Optional[Dict[str, Any]]:
        """
        Execute a limit order for a signal.

        Args:
            signal: Raw signal dict with price, direction, etc.
            filtered_result: Filter result with passed, score, reasons
            available_capital: Available capital for position sizing
            cold_streak: Whether DB is on a cold streak (halves sizes)
            deployment_ratio: Portfolio deployment ratio (0.0-1.0). If >0.85, BUY is skipped.

        Returns order details if successful, None if failed.
        """

        # Deployment ratio safety brake: skip if >85% of portfolio deployed
        if deployment_ratio > 0.85 and signal.get("direction", "") == "BUY":
            logger.warning(f"SKIPPING BUY: deployment ratio {deployment_ratio:.1%} > 85% limit")
            return {'status': 'SKIPPED', 'reason': f'deployment_ratio={deployment_ratio:.1%}'}

        # Calculate order parameters
        limit_price = self.calculate_limit_price(signal.get("price", 0.0), signal.get("direction", "BUY"))
        score = filtered_result.get("score", 0.5) if isinstance(filtered_result, dict) else 0.5
        # Use size_override for SELL exits, otherwise calculate
        if "size_override" in signal:
            size = signal["size_override"]
        else:
            size = self.calculate_position_size(limit_price, available_capital, score, cold_streak)
            if size <= 0:
                logger.warning(f"SKIPPING: position size is 0 (cold streak minimum violation)")
                return {'status': 'SKIPPED', 'reason': 'cold_streak_min_order'}

        # Determine side (we mirror DB's action)
        side = BUY if signal.get("direction", "") == 'BUY' else SELL

        order_info = {
            'timestamp': datetime.now().isoformat(),
            'signal_tx': signal.get("tx_hash", "")[:16] + '...',
            'market': signal.get("market_title", "")[:40],
            'slug': signal.get("slug", ""),
            'db_price': signal.get("price", 0.0),
            'limit_price': limit_price,
            'size': size,
            'side': 'BUY' if side == BUY else 'SELL',
            'outcome': signal.get("outcome", "Unknown"),
            'filter_score': filtered_result.get("score", 0.0) if isinstance(filtered_result, dict) else 0.0,
            'dry_run': self.config.dry_run
        }

        if self.config.dry_run:
            logger.info(f"[DRY RUN] Would place: {side} {size} @ {limit_price}")
            logger.info(f"  Market: {signal.get("market_title", "")[:50]}")
            logger.info(f"  DB price: {signal.get("price", 0.0)}, Our limit: {limit_price}")
            order_info['status'] = 'DRY_RUN'
            self.orders_placed += 1
            return order_info

        # Live order execution
        try:
            if not self.client:
                self._init_client()

            # For SELL orders, get CLOB balance and cap size
            if side == SELL:
                try:
                    token_id = signal.get("asset", "")
                    # Get CLOB's view of our balance for this token
                    params = BalanceAllowanceParams(
                        asset_type=AssetType.CONDITIONAL,
                        token_id=token_id
                    )
                    ba_resp = self.client.get_balance_allowance(params)
                    if isinstance(ba_resp, dict):
                        clob_balance = float(ba_resp.get('balance', 0)) / 1e6
                    else:
                        clob_balance = 0.0
                    logger.info(f"CLOB balance for {token_id[-20:]}: {clob_balance:.4f} (need {size:.2f})")
                    if clob_balance <= 0:
                        # Try update first, then re-check
                        self.client.update_balance_allowance(params)
                        ba_resp = self.client.get_balance_allowance(params)
                        if isinstance(ba_resp, dict):
                            clob_balance = float(ba_resp.get('balance', 0)) / 1e6
                        logger.info(f"After update, CLOB balance: {clob_balance:.4f}")
                    if clob_balance <= 0:
                        logger.warning(f"No CLOB balance for token {token_id[-20:]}, skipping SELL")
                        order_info['status'] = 'FAILED'
                        order_info['error'] = 'No CLOB balance'
                        return order_info
                    # Use CLOB balance, floor to 2 decimals with margin
                    import math
                    safe_balance = math.floor(clob_balance * 100) / 100 - 0.01
                    if safe_balance < 5.0:
                        logger.warning(f"CLOB balance too low for SELL: {safe_balance:.2f} < 5.0 minimum")
                        order_info['status'] = 'FAILED'
                        order_info['error'] = f'Balance {safe_balance:.2f} below minimum 5'
                        return order_info
                    if size > safe_balance:
                        logger.info(f"SELL size capped: {size:.2f} -> {safe_balance:.2f}")
                        size = safe_balance
                except Exception as allow_err:
                    logger.warning(f"SELL pre-check failed: {allow_err}")
                    # Fall back to 90% of requested size
                    size = round(size * 0.90, 2)
                    if size < 5.0:
                        logger.warning(f"Fallback SELL size {size:.2f} below minimum, skipping")
                        order_info['status'] = 'FAILED'
                        order_info['error'] = f'Fallback size {size:.2f} below minimum 5'
                        return order_info
            # Build order args (AFTER size cap for SELL)
            order_args = OrderArgs(
                token_id=signal.get("asset", ""),
                price=limit_price,
                size=size,
                side=side,
            )
            # Place limit order
            logger.info(f"Submitting order to CLOB: {side} {size} @ {limit_price}")

            # Run in executor to avoid blocking
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.client.create_and_post_order(order_args)
            )

            order_id = response.get("orderID", "") or response.get("id", "")
            order_info["order_id"] = order_id or "unknown"
            order_info["status"] = "PLACED"
            self.orders_placed += 1
            self.total_volume += size * limit_price

            logger.info(f"Order submitted to exchange: {response}")

            # Verify fill status after brief wait for matching engine
            if order_id:
                await asyncio.sleep(2)
                try:
                    check_id = order_id
                    order_status = await loop.run_in_executor(
                        None,
                        lambda: self.client.get_order(check_id)
                    )
                    fill_status = order_status.get("status", "UNKNOWN") if isinstance(order_status, dict) else "UNKNOWN"
                    order_info["fill_status"] = fill_status
                    if fill_status in ("MATCHED", "FILLED"):
                        order_info["status"] = "FILLED"
                        self.orders_filled += 1
                        logger.info(f"Order {order_id} confirmed: {fill_status}")
                    else:
                        logger.warning(f"Order {order_id} status: {fill_status}")
                        # Cancel unfilled orders to avoid stale limit orders on book
                        if fill_status in ("LIVE",):
                            try:
                                await loop.run_in_executor(
                                    None,
                                    lambda: self.client.cancel(order_id=check_id)
                                )
                                logger.info(f"Cancelled unfilled order {order_id}")
                                order_info["status"] = "CANCELLED"
                            except Exception as cancel_err:
                                logger.warning(f"Could not cancel order {order_id}: {cancel_err}")
                except Exception as e:
                    logger.warning(f"Could not verify order {order_id}: {e}")

            return order_info

        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            order_info['status'] = 'FAILED'
            order_info['error'] = str(e)
            return order_info

    def get_stats(self) -> Dict[str, Any]:
        """Return execution statistics"""
        return {
            'orders_placed': self.orders_placed,
            'orders_filled': self.orders_filled,
            'total_volume': self.total_volume,
            'dry_run': self.config.dry_run
        }


# Convenience function for quick testing
async def test_executor():
    """Test executor with mock signal"""
    from signal_types import Signal, FilteredSignal, FilterBreakdown

    executor = SignalExecutor()  # Defaults to dry_run=True

    # Create mock signal
    signal = Signal(
        tx_hash='0xtest123',
        timestamp=datetime.now().isoformat(),
        direction='BUY',
        outcome='NO',
        asset='token_123',
        price=0.25,
        usdc_size=10.0,
        market_title='BTC Up or Down Test',
        slug='btc-updown-15m-test'
    )

    breakdown = FilterBreakdown(base=0.2, direction_down=0.2, optimal_entry=0.25)
    filtered = FilteredSignal(passed=True, score=0.75, signal=signal, breakdown=breakdown)

    result = await executor.execute_signal(filtered, available_capital=50.0)
    print(f"Result: {result}")
    print(f"Stats: {executor.get_stats()}")


if __name__ == '__main__':
    asyncio.run(test_executor())
