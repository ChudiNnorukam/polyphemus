"""Polyphemus — Polymarket Signal Bot entry point."""

import argparse
import asyncio
import sys

from .config import Settings, setup_logger
from .signal_bot import SignalBot


def main():
    parser = argparse.ArgumentParser(description="Polyphemus — Polymarket Signal Bot")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Run in dry-run mode (no real orders)")
    parser.add_argument("--mock-file", type=str, default=None, help="Path to mock signals JSON file for testing")
    parser.add_argument("--log-level", type=str, default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Log level")
    args = parser.parse_args()

    logger = setup_logger("polyphemus", args.log_level)

    try:
        config = Settings()
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        sys.exit(1)

    from pathlib import Path as _Path
    from .startup_check import run_check as _config_check
    _exit_code = _config_check(
        env_path=_Path(__file__).parent / ".env",
        expected_path=_Path(__file__).parent / "config_expected.json",
        halt_on_critical=True,
    )
    if _exit_code == 1:
        logger.warning("Config drift detected (non-critical). Review findings above.")

    # CLI --dry-run flag overrides config
    dry_run = args.dry_run or config.dry_run

    logger.info(f"Polyphemus starting | dry_run={dry_run} | mock={args.mock_file is not None}")

    bot = SignalBot(config=config, dry_run=dry_run, mock_file=args.mock_file)

    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        logger.info("Polyphemus stopped by user")
    except Exception as e:
        logger.error(f"Polyphemus crashed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
