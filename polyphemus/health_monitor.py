"""
Health monitoring for Polyphemus bot: systemd watchdog, JSON logs, connection monitoring, daily self-restart.
"""

import asyncio
import glob
import json
import os
import socket
import sys
import time
from datetime import datetime

from .config import setup_logger
from .types import DAILY_RESTART_HOURS, HEALTH_LOG_INTERVAL, WATCHDOG_INTERVAL


class HealthMonitor:
    """
    Monitors bot health: systemd watchdog notifications, JSON health logs, error tracking.
    Handles daily self-restart when uptime > 20h and no open positions.
    """

    def __init__(self, config, store, signal_feed=None, guard=None, redeemer=None,
                 halt_callback=None):
        """
        Initialize HealthMonitor.

        Args:
            config: Configuration object
            store: Position store for open position counts
            signal_feed: SignalFeed instance for signal freshness (optional)
            guard: SignalGuard instance for filter metrics (optional)
            redeemer: Redeemer instance for tracking redemptions (optional)
            halt_callback: Callable that halts trading when invariant violated (F3 fix)
        """
        self.config = config
        self.store = store
        self.signal_feed = signal_feed
        self.guard = guard
        self.redeemer = redeemer
        self._halt_callback = halt_callback
        self._logger = setup_logger("polyphemus.health")
        self._start_time = time.time()
        self._error_count = 0
        self._startup_balance = None  # Set during first health_log cycle
        self._last_momentum_detections = 0
        self._last_redemption_count = 0
        self._last_redemption_time = None

    def notify_ready(self):
        """Send systemd READY=1 notification."""
        try:
            addr = os.environ.get("NOTIFY_SOCKET")
            if addr:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                if addr[0] == "@":
                    addr = "\0" + addr[1:]
                sock.sendto(b"READY=1", addr)
                sock.close()
                self._logger.info("systemd: READY=1 sent")
        except Exception as e:
            self._logger.warning(f"systemd notify_ready failed: {e}")

    def notify_watchdog(self):
        """Send systemd WATCHDOG=1 heartbeat."""
        try:
            addr = os.environ.get("NOTIFY_SOCKET")
            if addr:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                if addr[0] == "@":
                    addr = "\0" + addr[1:]
                sock.sendto(b"WATCHDOG=1", addr)
                sock.close()
                self._logger.debug("systemd: WATCHDOG=1 sent")
        except Exception as e:
            self._logger.warning(f"systemd notify_watchdog failed: {e}")

    async def start_watchdog_loop(self):
        """Periodically send systemd WATCHDOG=1 heartbeat."""
        while True:
            try:
                await asyncio.sleep(WATCHDOG_INTERVAL)
                self.notify_watchdog()
            except Exception as e:
                self._logger.error(f"watchdog_loop error: {e}")

    async def start_health_log_loop(self, balance_manager=None):
        """
        Periodically log health status to JSON file.

        Args:
            balance_manager: BalanceManager instance for USDC balance (optional)
        """
        while True:
            try:
                await asyncio.sleep(HEALTH_LOG_INTERVAL)

                # Compute metrics
                uptime_hours = (time.time() - self._start_time) / 3600
                open_positions = self.store.count_open()
                signal_age_secs = None
                balance = None

                # Get signal freshness if available
                if self.signal_feed:
                    try:
                        signal_age_secs = self.signal_feed.last_signal_age()
                    except Exception:
                        signal_age_secs = None

                # Get balance if available
                if balance_manager:
                    try:
                        balance = balance_manager._cached_balance
                    except Exception:
                        balance = None

                # Build health record
                health_record = {
                    "uptime_hours": round(uptime_hours, 2),
                    "open_positions": open_positions,
                    "signal_age_secs": signal_age_secs,
                    "balance": balance,
                    "errors": self._error_count,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                }

                # Write to data/health_{timestamp}.json
                os.makedirs(self.config.lagbot_data_dir, exist_ok=True)
                timestamp_str = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                health_file = os.path.join(self.config.lagbot_data_dir, f"health_{timestamp_str}.json")

                with open(health_file, "w") as f:
                    json.dump(health_record, f, indent=2)

                # Get guard metrics if available
                guard_info = ""
                gm = None
                if self.guard:
                    try:
                        gm = self.guard.get_metrics()
                        guard_info = (
                            f", signals={gm['signals_received']}, "
                            f"passed={gm['signals_passed']}, "
                            f"rejections={gm['rejection_reasons']}"
                        )
                        health_record["guard"] = gm
                    except Exception:
                        pass

                self._logger.info(
                    f"health_log: uptime={uptime_hours:.1f}h, positions={open_positions}, "
                    f"signal_age={signal_age_secs if signal_age_secs is not None else 'N/A'}, balance={balance}, errors={self._error_count}"
                    f"{guard_info}"
                )

                # Runtime health invariants (detect silent failures within 5 minutes)
                self._check_runtime_invariants(uptime_hours, balance, gm, open_positions)

                # Clean up old health files (keep last 50)
                self._cleanup_old_health_files()

            except Exception as e:
                self._logger.error(f"health_log_loop error: {e}")

    def _check_runtime_invariants(self, uptime_hours, balance, guard_metrics, open_positions):
        """Check runtime invariants. CRITICAL violations halt trading via callback (F3 fix)."""
        if not guard_metrics:
            return

        # INVARIANT-1: Signal generation check (CRITICAL → halt)
        if (uptime_hours > 0.5 and
            guard_metrics.get('signals_received', 0) > 10 and
            guard_metrics.get('signals_passed', 0) == 0):
            msg = (
                f"INVARIANT-1 VIOLATION: {guard_metrics['signals_received']} signals received "
                f"but 0 passed — check guards/config"
            )
            self._logger.critical(msg)
            # Don't halt for this — signals being blocked is bad but not money-losing

        # INVARIANT-2: Balance drift check (CRITICAL → halt)
        if balance and self._startup_balance is None:
            self._startup_balance = balance
        if balance and self._startup_balance and open_positions == 0:
            drift_pct = ((self._startup_balance - balance) / self._startup_balance) * 100
            if drift_pct > 35:
                msg = (
                    f"INVARIANT-2 VIOLATION: balance dropped {drift_pct:.1f}% "
                    f"(${self._startup_balance:.2f} → ${balance:.2f}) "
                    f"with 0 open positions — halting trading"
                )
                self._logger.critical(msg)
                if self._halt_callback:
                    self._halt_callback(msg)

        # INVARIANT-3: Signal-to-trade conversion check (WARNING only)
        if guard_metrics.get('signals_received', 0) > 5:
            if guard_metrics.get('signals_passed', 0) == 0:
                self._logger.warning(
                    f"INVARIANT-3 WARNING: {guard_metrics['signals_received']} signals "
                    f"but 0 passed — rejections: {guard_metrics.get('rejection_reasons', {})}"
                )

        # INVARIANT-4: Redeemer sweep count check (F4 fix: use sweep_count, not _redemption_count)
        if self.redeemer:
            try:
                sweep_count = getattr(self.redeemer, 'sweep_count', 0)
                now = time.time()
                if sweep_count > self._last_redemption_count:
                    delta = sweep_count - self._last_redemption_count
                    if delta > 20:
                        self._logger.warning(
                            f"INVARIANT-4 WARNING: {delta} redemption sweeps since last check "
                            f"— possible re-redemption loop"
                        )
                self._last_redemption_count = sweep_count
            except Exception:
                pass

    def _cleanup_old_health_files(self, keep=50):
        """Remove old health log files, keeping the most recent N."""
        try:
            health_files = sorted(glob.glob(os.path.join(self.config.lagbot_data_dir, "health_*.json")), reverse=True)
            if len(health_files) > keep:
                for old_file in health_files[keep:]:
                    os.remove(old_file)
                    self._logger.debug(f"removed old health file: {old_file}")
        except Exception as e:
            self._logger.warning(f"cleanup_old_health_files error: {e}")

    async def check_daily_restart(self):
        """Perform daily self-restart if uptime > 20h and no open positions."""
        while True:
            try:
                await asyncio.sleep(600)  # Check every 10 minutes

                uptime_hours = self.get_uptime_hours()
                open_positions = self.store.count_open()

                if uptime_hours > DAILY_RESTART_HOURS and open_positions == 0:
                    self._logger.info(
                        f"Daily self-restart: uptime {uptime_hours:.1f}h > {DAILY_RESTART_HOURS}h, "
                        f"no open positions"
                    )
                    sys.exit(0)

            except Exception as e:
                self._logger.error(f"check_daily_restart error: {e}")

    def record_error(self):
        """Increment error counter."""
        self._error_count += 1

    def get_uptime_hours(self) -> float:
        """Return current uptime in hours."""
        return (time.time() - self._start_time) / 3600
