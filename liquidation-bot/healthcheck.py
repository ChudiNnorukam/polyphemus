"""
Health check and monitoring utilities.
Includes systemd watchdog notification and periodic status reporting.
"""

import os
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict

logger = logging.getLogger(__name__)


def notify_ready() -> None:
    """Notify systemd that service is ready."""
    notify_socket = os.getenv("NOTIFY_SOCKET")
    if not notify_socket:
        return

    try:
        if notify_socket.startswith("@"):
            # Abstract socket
            notify_socket = "\0" + notify_socket[1:]

        sock = __import__("socket").socket(
            __import__("socket").AF_UNIX, __import__("socket").SOCK_DGRAM
        )
        sock.sendto(b"READY=1", notify_socket)
        sock.close()
        logger.debug("Notified systemd: READY=1")
    except Exception as e:
        logger.debug(f"Could not notify systemd: {e}")


def notify_watchdog() -> None:
    """Notify systemd watchdog that service is alive."""
    notify_socket = os.getenv("NOTIFY_SOCKET")
    if not notify_socket:
        return

    try:
        if notify_socket.startswith("@"):
            notify_socket = "\0" + notify_socket[1:]

        sock = __import__("socket").socket(
            __import__("socket").AF_UNIX, __import__("socket").SOCK_DGRAM
        )
        sock.sendto(b"WATCHDOG=1", notify_socket)
        sock.close()
        logger.debug("Notified systemd: WATCHDOG=1")
    except Exception as e:
        logger.debug(f"Could not notify systemd watchdog: {e}")


class HealthStatus:
    """Tracks health status and generates reports."""

    def __init__(self, health_log_dir: str = "data"):
        """Initialize health status tracker."""
        self.health_log_dir = Path(health_log_dir)
        self.health_log_dir.mkdir(parents=True, exist_ok=True)

        self.start_time = datetime.utcnow()
        self.total_scans = 0
        self.total_liquidatable = 0
        self.total_liquidations = 0
        self.total_profit = 0.0
        self.error_count = 0
        self.last_scan_time = None
        self.last_scan_duration = 0.0
        self.current_balance = 0.0

    def record_scan(self, duration_ms: float, liquidatable_count: int) -> None:
        """Record a scan operation."""
        self.total_scans += 1
        self.total_liquidatable += liquidatable_count
        self.last_scan_time = datetime.utcnow()
        self.last_scan_duration = duration_ms

    def record_liquidation(self, profit: float) -> None:
        """Record a successful liquidation."""
        self.total_liquidations += 1
        self.total_profit += profit

    def record_error(self) -> None:
        """Record an error event."""
        self.error_count += 1

    def set_balance(self, balance_usdc: float) -> None:
        """Update current balance."""
        self.current_balance = balance_usdc

    @property
    def uptime_seconds(self) -> int:
        """Get uptime in seconds."""
        return int((datetime.utcnow() - self.start_time).total_seconds())

    @property
    def uptime_human(self) -> str:
        """Get human-readable uptime."""
        seconds = self.uptime_seconds
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        return f"{hours}h {minutes}m {secs}s"

    def get_status_dict(self) -> Dict:
        """Get current status as dictionary."""
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "uptime": self.uptime_human,
            "uptime_seconds": self.uptime_seconds,
            "total_scans": self.total_scans,
            "total_liquidatable": self.total_liquidatable,
            "total_liquidations": self.total_liquidations,
            "total_profit": round(self.total_profit, 2),
            "current_balance_usdc": round(self.current_balance, 2),
            "error_count": self.error_count,
            "last_scan": self.last_scan_time.isoformat() if self.last_scan_time else None,
            "last_scan_duration_ms": round(self.last_scan_duration, 2),
        }

    def write_status_file(self) -> None:
        """Write status to JSON file."""
        try:
            status_file = self.health_log_dir / "health_status.json"
            with open(status_file, "w") as f:
                json.dump(self.get_status_dict(), f, indent=2)
            logger.debug(f"Wrote health status to {status_file}")
        except Exception as e:
            logger.error(f"Error writing health status: {e}")

    def log_status(self) -> None:
        """Log current status."""
        status = self.get_status_dict()
        logger.info(
            f"Health: uptime={status['uptime']}, "
            f"scans={status['total_scans']}, "
            f"liquidations={status['total_liquidations']}, "
            f"profit=${status['total_profit']:.2f}, "
            f"balance=${status['current_balance_usdc']:.2f}, "
            f"errors={status['error_count']}"
        )


class TelegramNotifier:
    """Sends notifications via Telegram."""

    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        """Initialize Telegram notifier."""
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)

        if not self.enabled:
            logger.debug("Telegram notifications disabled (missing credentials)")

    async def send_message(self, message: str) -> bool:
        """Send message via Telegram."""
        if not self.enabled:
            return False

        try:
            import aiohttp

            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            data = {"chat_id": self.chat_id, "text": message, "parse_mode": "HTML"}

            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data, timeout=10) as resp:
                    if resp.status == 200:
                        logger.debug(f"Telegram message sent")
                        return True
                    else:
                        logger.warning(f"Telegram error: {resp.status}")
                        return False

        except Exception as e:
            logger.error(f"Error sending Telegram message: {e}")
            return False

    async def notify_liquidation(
        self,
        user: str,
        debt_amount: float,
        estimated_profit: float,
        tx_hash: Optional[str] = None,
    ) -> None:
        """Send liquidation notification."""
        if not self.enabled:
            return

        message = (
            f"💰 <b>Liquidation Executed</b>\n"
            f"User: <code>{user}</code>\n"
            f"Debt: ${debt_amount:.2f}\n"
            f"Est. Profit: ${estimated_profit:.2f}\n"
        )

        if tx_hash:
            message += f"TX: <a href='https://arbiscan.io/tx/{tx_hash}'>View</a>\n"

        await self.send_message(message)

    async def notify_error(self, error_msg: str) -> None:
        """Send error notification."""
        if not self.enabled:
            return

        message = f"⚠️ <b>Bot Error</b>\n<code>{error_msg[:200]}</code>"
        await self.send_message(message)

    async def notify_startup(self) -> None:
        """Send startup notification."""
        if not self.enabled:
            return

        message = "✅ <b>Liquidation Bot Started</b>\n🤖 Scanning for opportunities..."
        await self.send_message(message)

    async def notify_shutdown(self, profit: float) -> None:
        """Send shutdown notification."""
        if not self.enabled:
            return

        message = f"🛑 <b>Liquidation Bot Stopped</b>\n💵 Total Profit: ${profit:.2f}"
        await self.send_message(message)
