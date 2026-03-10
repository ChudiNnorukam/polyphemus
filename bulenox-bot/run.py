import asyncio
import logging
import os
import sys

# Must be set before any protobuf import
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

from config import load_config
from bulenox_bot import BulenoxBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger(__name__)


async def main() -> None:
    cfg = load_config()

    if not cfg.rithmic_user or not cfg.rithmic_pass:
        logger.error("RITHMIC_USER and RITHMIC_PASS must be set in .env")
        sys.exit(1)

    logger.info(
        f"Config loaded: symbol={cfg.symbol} exchange={cfg.exchange} "
        f"trigger={cfg.momentum_trigger_pct:.1%} window={cfg.momentum_window_secs}s "
        f"dry_run={cfg.dry_run}"
    )

    bot = BulenoxBot(cfg)
    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("Shutting down")


if __name__ == "__main__":
    asyncio.run(main())
