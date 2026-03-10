"""
Position Manager for Funding Rate Arbitrage Bot
Manages spot and perpetual positions to maintain delta neutrality
"""

import asyncio
import logging
from decimal import Decimal
from typing import Dict, Optional, Tuple, List
from dataclasses import dataclass
import time
import sqlite3

import aiohttp
import config

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Position information"""
    symbol: str
    spot_qty: Decimal
    perp_qty: Decimal
    spot_entry_price: Decimal
    perp_entry_price: Decimal
    entry_time: float
    funding_claimed: Decimal
    unrealized_pnl: Decimal
    delta: Decimal  # Delta neutrality (should be ~0)


class PositionManager:
    """Manages spot and perpetual positions"""

    def __init__(self):
        self.session = None
        self.base_url = config.PERP_API_URL
        self.spot_url = config.SPOT_API_URL
        self.positions = {}
        self.nonce = int(time.time() * 1000)

    async def initialize(self):
        """Initialize HTTP session"""
        self.session = aiohttp.ClientSession()

    async def shutdown(self):
        """Cleanup session"""
        if self.session:
            await self.session.close()

    async def open_position(
        self,
        symbol: str,
        quantity: Decimal,
        spot_price: Decimal,
        perp_price: Decimal,
        use_margin: bool = True,
    ) -> bool:
        """
        Open a delta-neutral position
        Long spot + Short perp (or vice versa)
        
        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            quantity: Amount to trade
            spot_price: Current spot price
            perp_price: Current perpetual price
            use_margin: Use margin for perp short
            
        Returns:
            True if successful
        """
        try:
            logger.info(f"Opening position for {symbol}: {quantity} units")

            # Place spot buy order
            spot_order_ok = await self._place_spot_order(
                symbol=symbol,
                side="buy",
                qty=quantity,
            )

            if not spot_order_ok:
                logger.error(f"Failed to place spot buy order for {symbol}")
                return False

            # Wait for spot order to fill
            await asyncio.sleep(2)

            # Place perp short order
            perp_order_ok = await self._place_perp_order(
                symbol=symbol,
                side="sell",
                qty=quantity,
                reduce_only=False,
            )

            if not perp_order_ok:
                logger.error(f"Failed to place perp sell order for {symbol}")
                # Close spot position
                await self._place_spot_order(symbol, "sell", quantity)
                return False

            # Record position
            self.positions[symbol] = Position(
                symbol=symbol,
                spot_qty=quantity,
                perp_qty=quantity,
                spot_entry_price=spot_price,
                perp_entry_price=perp_price,
                entry_time=time.time(),
                funding_claimed=Decimal(0),
                unrealized_pnl=Decimal(0),
                delta=Decimal(0),
            )

            logger.info(f"Position opened for {symbol}: Long spot, Short perp")
            self._record_trade(symbol, "open", quantity, spot_price, perp_price)

            return True

        except Exception as e:
            logger.error(f"Error opening position: {e}")
            return False

    async def close_position(self, symbol: str) -> bool:
        """Close a delta-neutral position"""
        try:
            if symbol not in self.positions:
                logger.warning(f"No position found for {symbol}")
                return False

            pos = self.positions[symbol]
            logger.info(f"Closing position for {symbol}: {pos.spot_qty} units")

            # Close spot (sell)
            spot_ok = await self._place_spot_order(symbol, "sell", pos.spot_qty)

            # Close perp (buy to cover short)
            perp_ok = await self._place_perp_order(symbol, "buy", pos.perp_qty, reduce_only=True)

            if spot_ok and perp_ok:
                # Claim any pending funding before closing
                funding = await self.claim_funding(symbol)

                del self.positions[symbol]
                logger.info(f"Position closed for {symbol}, funding claimed: {funding}")

                self._record_trade(symbol, "close", pos.spot_qty, Decimal(0), Decimal(0))
                return True

            return False

        except Exception as e:
            logger.error(f"Error closing position: {e}")
            return False

    async def rebalance_positions(self):
        """Rebalance all positions to maintain delta neutrality"""
        try:
            for symbol, pos in list(self.positions.items()):
                # Get current prices
                spot_price = await self._get_spot_price(symbol)
                perp_price = await self._get_perp_price(symbol)

                if not spot_price or not perp_price:
                    continue

                # Calculate current delta
                spot_value = pos.spot_qty * spot_price
                perp_value = pos.perp_qty * perp_price
                delta = abs(spot_value - perp_value)

                pos.delta = delta

                # Rebalance if delta > 5%
                if delta / spot_value > Decimal("0.05"):
                    logger.info(f"Rebalancing {symbol}: delta={delta}")
                    await self._adjust_position_size(symbol, spot_price, perp_price)

        except Exception as e:
            logger.error(f"Error rebalancing positions: {e}")

    async def claim_funding(self, symbol: str) -> Decimal:
        """Claim pending funding for a position"""
        try:
            # Funding is automatically received for perp positions
            # This is a placeholder for claiming if using more complex strategies
            funding = await self._get_pending_funding(symbol)

            if funding > 0:
                logger.info(f"Claimed {funding} funding for {symbol}")
                if symbol in self.positions:
                    self.positions[symbol].funding_claimed += funding

            return funding

        except Exception as e:
            logger.error(f"Error claiming funding: {e}")
            return Decimal(0)

    async def _place_spot_order(self, symbol: str, side: str, qty: Decimal) -> bool:
        """Place a spot market order"""
        try:
            # In production, would use actual Bybit API
            logger.debug(f"Spot order: {side} {qty} {symbol}")
            return True

        except Exception as e:
            logger.error(f"Error placing spot order: {e}")
            return False

    async def _place_perp_order(
        self,
        symbol: str,
        side: str,
        qty: Decimal,
        reduce_only: bool = False,
    ) -> bool:
        """Place a perpetual order"""
        try:
            # In production, would use actual Bybit API
            logger.debug(f"Perp order: {side} {qty} {symbol} (reduce_only={reduce_only})")
            return True

        except Exception as e:
            logger.error(f"Error placing perp order: {e}")
            return False

    async def _get_spot_price(self, symbol: str) -> Optional[Decimal]:
        """Get current spot price"""
        try:
            # Placeholder - would fetch from Bybit
            return Decimal("0")

        except Exception as e:
            logger.error(f"Error getting spot price: {e}")
            return None

    async def _get_perp_price(self, symbol: str) -> Optional[Decimal]:
        """Get current perpetual price"""
        try:
            # Placeholder - would fetch from Bybit
            return Decimal("0")

        except Exception as e:
            logger.error(f"Error getting perp price: {e}")
            return None

    async def _adjust_position_size(self, symbol: str, spot_price: Decimal, perp_price: Decimal):
        """Adjust position sizes to maintain delta neutrality"""
        if symbol not in self.positions:
            return

        # Calculate adjustment needed
        pos = self.positions[symbol]
        spot_value = pos.spot_qty * spot_price
        perp_value = pos.perp_qty * perp_price

        if spot_value > perp_value:
            # Sell spot, increase perp short
            diff = (spot_value - perp_value) / (2 * perp_price)
            await self._place_spot_order(symbol, "sell", diff)
            await self._place_perp_order(symbol, "sell", diff)

        else:
            # Buy spot, decrease perp short
            diff = (perp_value - spot_value) / (2 * spot_price)
            await self._place_spot_order(symbol, "buy", diff)
            await self._place_perp_order(symbol, "buy", diff, reduce_only=True)

    async def _get_pending_funding(self, symbol: str) -> Decimal:
        """Get pending funding payments"""
        try:
            # Placeholder - would calculate from position and rates
            return Decimal(0)

        except Exception as e:
            logger.error(f"Error getting pending funding: {e}")
            return Decimal(0)

    def _record_trade(
        self,
        symbol: str,
        action: str,
        qty: Decimal,
        spot_price: Decimal,
        perp_price: Decimal,
    ):
        """Record trade in database"""
        try:
            conn = sqlite3.connect(config.DATABASE_PATH)
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT INTO trades (symbol, action, qty, spot_price, perp_price, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (symbol, action, str(qty), str(spot_price), str(perp_price), time.time()),
            )

            conn.commit()
            conn.close()

        except Exception as e:
            logger.error(f"Error recording trade: {e}")

    def get_positions(self) -> Dict[str, Position]:
        """Get current positions"""
        return self.positions.copy()

    def get_total_funding(self) -> Decimal:
        """Get total funding claimed"""
        return sum(pos.funding_claimed for pos in self.positions.values())
