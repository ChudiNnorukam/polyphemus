#!/usr/bin/env python3
"""
Bybit Funding Rate Arbitrage Bot
Delta-neutral strategy: buy spot + short perp, collect funding payments
Runs on VPS with systemd integration and health monitoring
"""
import asyncio
import logging
import signal
import sys
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Optional, List
import aiohttp

from config import (
    BYBIT_API_KEY, BYBIT_API_SECRET, PAIRS, MIN_FUNDING_RATE,
    MAX_POSITION_PCT, REBALANCE_THRESHOLD, MAX_BASIS_DIVERGENCE,
    CIRCUIT_BREAKER_LOSS, CHECK_INTERVAL, HEALTH_CHECK_INTERVAL,
    WATCHDOG_INTERVAL, ORDER_TIMEOUT, RATE_STABILITY_PERIODS,
    DRY_RUN, CLOSE_ON_SHUTDOWN, DB_PATH, LOG_LEVEL, LOG_FILE,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
)
from rate_scanner import FundingRateScanner
from position_manager import PositionManager, Position
from database import FundingBotDatabase

# Set up logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class FundingRateBot:
    """Main bot for funding rate arbitrage"""

    def __init__(self):
        """Initialize bot"""
        self.scanner = FundingRateScanner(
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET,
            dry_run=DRY_RUN
        )
        self.position_manager = PositionManager(
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET,
            dry_run=DRY_RUN
        )
        self.db = FundingBotDatabase(DB_PATH)

        # Bot state
        self.running = True
        self.start_time = datetime.now(timezone.utc)
        self.last_health_check = 0
        self.last_watchdog = 0
        self.initial_balance = 0.0
        self.peak_balance = 0.0
        self.stats = {
            "positions_entered": 0,
            "positions_exited": 0,
            "total_funding_collected": 0.0,
            "total_pnl": 0.0,
            "errors": 0
        }

        # Register signal handlers
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

        logger.info("FundingRateBot initialized")
        logger.info(f"Configuration: DRY_RUN={DRY_RUN}, PAIRS={PAIRS}")
        logger.info(f"Min funding rate: {MIN_FUNDING_RATE:.6f}")
        logger.info(f"Max position %: {MAX_POSITION_PCT:.1%}")

    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signals"""
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False

    async def initialize(self):
        """Initialize bot state"""
        try:
            balance = await self.position_manager.get_balance()
            self.initial_balance = balance
            self.peak_balance = balance
            logger.info(f"Starting balance: ${balance:.2f}")

            # Log initial status
            await self._log_health_status()

        except Exception as e:
            logger.error(f"Error during initialization: {e}")
            self.stats["errors"] += 1

    async def main_loop(self):
        """Main bot loop"""
        logger.info("Starting main loop...")

        while self.running:
            try:
                current_time = time.time()

                # Main trading logic
                await self._trading_cycle()

                # Health checks
                if current_time - self.last_health_check >= HEALTH_CHECK_INTERVAL:
                    await self._log_health_status()
                    self.last_health_check = current_time

                # Systemd watchdog
                if current_time - self.last_watchdog >= WATCHDOG_INTERVAL:
                    await self._send_watchdog_ping()
                    self.last_watchdog = current_time

                await asyncio.sleep(CHECK_INTERVAL)

            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                self.stats["errors"] += 1
                await asyncio.sleep(5)

        logger.info("Main loop stopped")

    async def _trading_cycle(self):
        """Execute one complete trading cycle"""
        try:
            # Scan for opportunities
            opportunities = await self.scanner.find_opportunities(
                pairs=PAIRS,
                min_rate=MIN_FUNDING_RATE,
                stability_periods=RATE_STABILITY_PERIODS
            )

            # Process opportunities
            for rate_data in opportunities:
                symbol = rate_data.symbol

                # Log rate data
                self.db.add_funding_rate(
                    symbol=symbol,
                    current_rate=rate_data.current_rate,
                    next_rate=rate_data.next_rate,
                    avg_7d_rate=rate_data.avg_7d_rate,
                    apy=rate_data.annualized_apy,
                    consecutive_positive=rate_data.consecutive_positive
                )

                # Check if already have position
                open_positions = await self.position_manager.get_open_positions()
                has_position = any(p.symbol == symbol for p in open_positions)

                if not has_position:
                    # Enter new position
                    await self._enter_position(
                        symbol=symbol,
                        current_price=await self._get_current_price(symbol),
                        funding_rate=rate_data.current_rate
                    )
                else:
                    # Check existing position
                    position = next(p for p in open_positions if p.symbol == symbol)
                    await self._check_position_health(position, rate_data)

            # Check existing positions for exit conditions
            open_positions = await self.position_manager.get_open_positions()
            for position in open_positions:
                # Get current rate for this symbol
                current_rate = await self.scanner.get_current_rate(position.symbol)

                if current_rate is None:
                    continue

                # Exit conditions
                if current_rate < 0:
                    # Funding rate turned negative
                    await self._exit_position(position, "rate_negative")
                elif current_rate < 0.00001:
                    # Rate too low
                    await self._exit_position(position, "rate_too_low")
                else:
                    # Check delta drift
                    await self.position_manager.check_delta(position.symbol)

        except Exception as e:
            logger.error(f"Error in trading cycle: {e}", exc_info=True)
            self.stats["errors"] += 1

    async def _enter_position(
        self,
        symbol: str,
        current_price: float,
        funding_rate: float
    ) -> bool:
        """
        Enter a new delta-neutral position

        Args:
            symbol: Trading pair
            current_price: Current spot price
            funding_rate: Current funding rate

        Returns:
            True if successful
        """
        try:
            # Calculate position size
            balance = await self.position_manager.get_balance()
            position_size_usdt = balance * MAX_POSITION_PCT

            # Sanity checks
            if position_size_usdt < 10:
                logger.warning(f"Position size ${position_size_usdt:.2f} too small, skipping")
                return False

            logger.info(
                f"Entering position: {symbol} size=${position_size_usdt:.2f} "
                f"rate={funding_rate:.6f} apy={funding_rate * 3 * 365:.1%}"
            )

            # Execute entry
            position = await self.position_manager.enter_position(
                symbol=symbol,
                size_usdt=position_size_usdt,
                current_price=current_price
            )

            if position:
                # Record in database
                self.db.add_position(
                    position_id=position.position_id,
                    symbol=symbol,
                    entry_price=position.entry_price,
                    spot_qty=position.spot_qty,
                    perp_qty=position.perp_qty,
                    entry_time=position.entry_time,
                    spot_order_id=position.spot_order_id,
                    perp_order_id=position.perp_order_id
                )

                self.stats["positions_entered"] += 1

                # Send alert
                await self._send_telegram(
                    f"✅ Position entered: {symbol}\n"
                    f"Size: ${position_size_usdt:.2f}\n"
                    f"Entry: ${position.entry_price:.2f}\n"
                    f"Funding rate: {funding_rate:.6f}"
                )

                return True
            else:
                logger.error(f"Failed to enter position for {symbol}")
                return False

        except Exception as e:
            logger.error(f"Error entering position: {e}", exc_info=True)
            self.stats["errors"] += 1
            return False

    async def _exit_position(
        self,
        position: Position,
        reason: str
    ) -> bool:
        """
        Exit a delta-neutral position

        Args:
            position: Position to exit
            reason: Exit reason

        Returns:
            True if successful
        """
        try:
            logger.info(f"Exiting position {position.position_id} {position.symbol}: {reason}")

            pnl = await self.position_manager.exit_position(
                symbol=position.symbol,
                reason=reason
            )

            if pnl is not None:
                # Update database
                self.db.update_position_exit(
                    position_id=position.position_id,
                    exit_price=position.exit_price,
                    exit_time=position.exit_time,
                    pnl=pnl,
                    fees_paid=position.fees_paid,
                    funding_collected=position.funding_collected,
                    exit_reason=reason
                )

                self.stats["positions_exited"] += 1
                self.stats["total_pnl"] += pnl
                self.stats["total_funding_collected"] += position.funding_collected

                # Send alert
                await self._send_telegram(
                    f"📊 Position closed: {position.symbol}\n"
                    f"Reason: {reason}\n"
                    f"P&L: ${pnl:.2f}\n"
                    f"Funding: ${position.funding_collected:.2f}"
                )

                return True
            else:
                logger.error(f"Failed to exit position {position.position_id}")
                return False

        except Exception as e:
            logger.error(f"Error exiting position: {e}", exc_info=True)
            self.stats["errors"] += 1
            return False

    async def _check_position_health(
        self,
        position: Position,
        rate_data
    ):
        """
        Check health of existing position

        Args:
            position: Position to check
            rate_data: Current rate data
        """
        try:
            # Check if rates still positive
            if rate_data.current_rate < 0.00001:
                await self._exit_position(position, "rate_dropped")
                return

            # Check delta divergence (simplified for dry run)
            if not DRY_RUN:
                await self.position_manager.check_delta(position.symbol)

        except Exception as e:
            logger.error(f"Error checking position health: {e}")

    async def _get_current_price(self, symbol: str) -> float:
        """
        Get current spot price for a symbol

        Args:
            symbol: Trading pair

        Returns:
            Current price
        """
        try:
            if self.position_manager.session:
                ticker = self.position_manager.session.get_tickers(
                    category="linear",
                    symbol=symbol
                )
                if ticker["retCode"] == 0:
                    return float(ticker["result"]["list"][0]["lastPrice"])

            # Return mock price for dry run
            return 100.0

        except Exception as e:
            logger.error(f"Error getting price for {symbol}: {e}")
            return 100.0

    async def _log_health_status(self):
        """Log bot health status"""
        try:
            balance = await self.position_manager.get_balance()
            open_positions = await self.position_manager.get_open_positions()
            uptime = (datetime.now(timezone.utc) - self.start_time).total_seconds()

            # Update peak balance
            if balance > self.peak_balance:
                self.peak_balance = balance

            # Calculate total P&L
            total_pnl = self.db.get_total_pnl()
            total_funding = self.db.get_total_funding_collected()

            # Create status object
            status = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "balance": round(balance, 2),
                "peak_balance": round(self.peak_balance, 2),
                "drawdown": round((self.peak_balance - balance) / self.peak_balance * 100, 2) if self.peak_balance > 0 else 0,
                "open_positions": len(open_positions),
                "symbols_with_positions": [p.symbol for p in open_positions],
                "total_pnl": round(total_pnl, 2),
                "total_funding_collected": round(total_funding, 2),
                "uptime_hours": round(uptime / 3600, 2),
                "positions_entered": self.stats["positions_entered"],
                "positions_exited": self.stats["positions_exited"],
                "errors": self.stats["errors"],
                "mode": "DRY_RUN" if DRY_RUN else "LIVE"
            }

            # Log to file
            health_file = Path("logs/health.json")
            health_file.parent.mkdir(parents=True, exist_ok=True)
            with open(health_file, "w") as f:
                json.dump(status, f, indent=2)

            # Log to database
            self.db.add_bot_status(
                balance=balance,
                equity=balance,
                open_positions=len(open_positions),
                total_pnl=total_pnl,
                total_funding_collected=total_funding,
                uptime_seconds=int(uptime)
            )

            logger.info(
                f"Health: balance=${balance:.2f} positions={len(open_positions)} "
                f"pnl=${total_pnl:.2f} funding=${total_funding:.2f} uptime={status['uptime_hours']:.1f}h"
            )

        except Exception as e:
            logger.error(f"Error logging health status: {e}")
            self.stats["errors"] += 1

    async def _send_watchdog_ping(self):
        """Send systemd watchdog ping"""
        try:
            # Send WATCHDOG=1 to systemd
            if "WATCHDOG_USEC" in __import__("os").environ:
                import os
                os.write(1, b"WATCHDOG=1\n")
                logger.debug("Watchdog ping sent")
        except Exception as e:
            logger.warning(f"Watchdog ping failed: {e}")

    async def _send_telegram(self, message: str):
        """
        Send Telegram alert

        Args:
            message: Alert message
        """
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            return

        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json={"chat_id": TELEGRAM_CHAT_ID, "text": message},
                    timeout=aiohttp.ClientTimeout(total=5)
                ):
                    pass
        except Exception as e:
            logger.warning(f"Failed to send Telegram message: {e}")

    async def shutdown(self):
        """Graceful shutdown"""
        logger.info("Shutting down bot...")

        try:
            if CLOSE_ON_SHUTDOWN:
                logger.info("Closing all positions...")
                open_positions = await self.position_manager.get_open_positions()
                for position in open_positions:
                    await self._exit_position(position, "shutdown")

            # Log final status
            await self._log_health_status()

            # Close connections
            self.scanner.close()
            self.position_manager.close()

            logger.info("Bot shutdown complete")

        except Exception as e:
            logger.error(f"Error during shutdown: {e}", exc_info=True)

    async def run(self):
        """Run the bot"""
        try:
            await self.initialize()
            await self.main_loop()
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
            self.stats["errors"] += 1
        finally:
            await self.shutdown()


async def main():
    """Main entry point"""
    bot = FundingRateBot()
    await bot.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
