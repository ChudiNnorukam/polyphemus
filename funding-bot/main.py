"""
Hyperliquid Funding Rate Farmer v2
Phase 1: Monitor & identify opportunities (no execution)
Phase 2: Auto-execute delta-neutral positions (future)
"""

import asyncio
import logging
import sys
from pathlib import Path

import config
from rate_scanner import FundingRateScanner

logging.basicConfig(
    level=logging.INFO,
    format=config.LOG_FORMAT,
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("funding-farmer")


async def main():
    scanner = FundingRateScanner()
    await scanner.initialize()

    logger.info(f"Hyperliquid Funding Rate Farmer v{config.BOT_VERSION}")
    logger.info(f"Watching: {', '.join(config.WATCH_COINS)}")
    logger.info(f"Min net APR: {config.MIN_ANNUALIZED_RETURN*100:.0f}% | Min sustained: {config.MIN_SUSTAINED_HOURS}h")
    logger.info(f"DRY_RUN: {config.DRY_RUN} | Testnet: {config.USE_TESTNET}")
    logger.info(f"Scan interval: {config.SCAN_INTERVAL_SECS}s")
    logger.info(f"DB: {config.DATABASE_PATH}")

    scan_count = 0
    try:
        while True:
            scan_count += 1
            logger.info(f"--- Scan #{scan_count} ---")

            opportunities = await scanner.scan()

            if opportunities:
                table = scanner.format_table(opportunities)
                logger.info(f"\n{table}")

                # Log actionable signals
                viable = scanner.rank_opportunities()
                if viable:
                    logger.info(f"\n=== {len(viable)} FARM SIGNALS ===")
                    for opp in viable:
                        be_days = config.ROUND_TRIP_FEES / opp.avg_24h_rate / 24 if opp.avg_24h_rate > 0 else 999
                        monthly = opp.avg_24h_rate * 720 * config.CAPITAL_PER_PAIR - config.ROUND_TRIP_FEES * config.CAPITAL_PER_PAIR
                        logger.info(
                            f"  {opp.coin}: {opp.net_annualized_pct:.1f}% net APR | "
                            f"${monthly:.2f}/mo per ${config.CAPITAL_PER_PAIR} | "
                            f"breakeven: {be_days:.1f}d | "
                            f"positive for {opp.consecutive_positive_hrs}h straight"
                        )
                else:
                    logger.info("No farm signals this scan")

                # Full universe scan for outliers
                outliers = await scanner.scan_full_universe()
                if outliers:
                    logger.info(f"\n=== {len(outliers)} HIGH-APR OUTLIERS (>{config.FULL_UNIVERSE_MIN_APR}% APR, not in watch list) ===")
                    for opp in outliers[:5]:
                        oi_m = opp.open_interest * opp.mark_price / 1e6
                        logger.info(
                            f"  {opp.coin}: {opp.annualized_pct:.0f}% APR | "
                            f"rate={opp.current_rate*100:.4f}%/hr | "
                            f"OI=${oi_m:.1f}M | "
                            f"CAUTION: no history, thin liquidity"
                        )
            else:
                logger.warning("Scan returned empty results")

            await asyncio.sleep(config.SCAN_INTERVAL_SECS)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await scanner.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
