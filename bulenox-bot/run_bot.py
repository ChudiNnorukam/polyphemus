#!/usr/bin/env python3
"""Entry point for BulenoxBot with graceful shutdown."""
import asyncio
import json
import logging
import signal
import ssl
import sys
import time

import aiohttp
import certifi

from config import load_config
from bulenox_bot import BulenoxBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("bulenox")


async def preflight_feed_test():
    """Verify Coinbase feed delivers ticker data before starting the bot."""
    logger.info("PREFLIGHT: Testing Coinbase feed...")
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    try:
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect("wss://ws-feed.exchange.coinbase.com", ssl=ssl_ctx) as ws:
                await ws.send_json({"type": "subscribe", "product_ids": ["BTC-USD"], "channels": ["ticker"]})
                ticks = 0
                start = time.time()
                while time.time() - start < 10:
                    msg = await asyncio.wait_for(ws.receive(), timeout=5)
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        if data.get("type") == "ticker":
                            ticks += 1
                            if ticks == 1:
                                price = float(data["price"])
                                logger.info(f"PREFLIGHT: Feed alive. BTC=${price:,.2f}")
                            if ticks >= 3:
                                break
                if ticks >= 3:
                    logger.info(f"PREFLIGHT: PASS ({ticks} ticks in {time.time()-start:.0f}s)")
                    return True
                else:
                    logger.error(f"PREFLIGHT: FAIL (only {ticks} ticks in 10s)")
                    return False
    except Exception as e:
        logger.error(f"PREFLIGHT: FAIL ({e})")
        return False


async def heartbeat_logger(bot):
    """Log a heartbeat every 10 minutes so we can tell the bot is alive from journalctl."""
    while True:
        await asyncio.sleep(600)
        price = bot._feed.last_price
        trades = bot._store.get_total_trades()
        halted = bot._halted
        logger.info(f"HEARTBEAT: price=${price:,.2f} trades={trades} halted={halted}")


async def main():
    cfg = load_config()

    # Preflight: verify feed works before committing to full startup
    if not await preflight_feed_test():
        logger.error("Feed preflight failed. Retrying in 30s...")
        await asyncio.sleep(30)
        if not await preflight_feed_test():
            logger.error("Feed preflight failed twice. Exiting (systemd will restart).")
            sys.exit(1)

    bot = BulenoxBot(cfg)

    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _shutdown(sig):
        logger.warning(f"Received {sig.name}, shutting down...")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown, sig)

    bot_task = asyncio.create_task(bot.start())
    hb_task = asyncio.create_task(heartbeat_logger(bot))

    # Wait for either shutdown signal or bot crash
    done, _ = await asyncio.wait(
        [bot_task, asyncio.create_task(shutdown_event.wait())],
        return_when=asyncio.FIRST_COMPLETED,
    )
    hb_task.cancel()

    if shutdown_event.is_set():
        logger.info("Graceful shutdown: closing positions and logging out...")
        await bot._force_close_all("shutdown signal")
        await bot._rithmic.logout()
        await bot._ticker.logout()
        bot._save_state()
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass
    else:
        # Bot crashed
        for task in done:
            if task.exception():
                logger.error(f"Bot crashed: {task.exception()}")

    logger.info("BulenoxBot stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
