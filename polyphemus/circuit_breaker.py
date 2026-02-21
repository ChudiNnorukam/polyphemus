"""Circuit breaker — kill switch, daily loss limit, consecutive loss cooldown.

Three independent safety mechanisms combined via CircuitBreaker facade.
All checks block NEW ENTRIES only. Exits are NEVER blocked.
"""

import json
import os
import time
from datetime import date, datetime, timezone
from typing import Tuple

from .config import setup_logger


class KillSwitch:
    """File-based kill switch. Touch file to halt, remove to resume."""

    def __init__(self, path: str):
        self._path = path

    def is_active(self) -> bool:
        if not self._path:
            return False
        return os.path.exists(self._path)


class DailyLossMonitor:
    """DB-backed daily realized P&L tracker.

    Queries performance.db for today's cumulative realized P&L.
    Only counts EXITED trades (unrealized positions excluded).
    """

    def __init__(self, perf_db, max_daily_loss: float):
        self._db = perf_db
        self._max_daily_loss = max_daily_loss

    def has_hit_limit(self) -> bool:
        if self._max_daily_loss <= 0:
            return False
        daily_pnl = self._db.get_daily_pnl(date.today())
        return daily_pnl <= -self._max_daily_loss

    def get_daily_pnl(self) -> float:
        return self._db.get_daily_pnl(date.today())


class StreakTracker:
    """Consecutive loss tracker with cooldown. State persists across restarts via JSON."""

    def __init__(self, max_consecutive: int, cooldown_mins: int, state_path: str):
        self._max = max_consecutive
        self._cooldown_mins = cooldown_mins
        self._state_path = state_path
        self._consecutive_losses: int = 0
        self._cooldown_until: float = 0.0
        self._load_state()

    def record_result(self, pnl: float):
        if pnl < 0:
            self._consecutive_losses += 1
            if self._max > 0 and self._consecutive_losses >= self._max:
                self._cooldown_until = time.time() + (self._cooldown_mins * 60)
        else:
            self._consecutive_losses = 0
            self._cooldown_until = 0.0
        self._save_state()

    def in_cooldown(self) -> bool:
        if self._max <= 0:
            return False
        if self._cooldown_until <= 0:
            return False
        if time.time() >= self._cooldown_until:
            self._consecutive_losses = 0
            self._cooldown_until = 0.0
            self._save_state()
            return False
        return True

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses

    def _save_state(self):
        try:
            os.makedirs(os.path.dirname(self._state_path) or '.', exist_ok=True)
            with open(self._state_path, 'w') as f:
                json.dump({
                    'consecutive_losses': self._consecutive_losses,
                    'cooldown_until': self._cooldown_until,
                }, f)
        except Exception as e:
            self._logger.warning(f"StreakTracker save failed: {e}")

    def _load_state(self):
        try:
            with open(self._state_path) as f:
                state = json.load(f)
            self._consecutive_losses = int(state.get('consecutive_losses', 0))
            self._cooldown_until = float(state.get('cooldown_until', 0.0))
        except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError, TypeError):
            self._consecutive_losses = 0
            self._cooldown_until = 0.0


class CircuitBreaker:
    """Facade combining kill switch, daily loss, and streak tracking.

    Usage in signal_bot._on_signal() BEFORE execute_buy():
        allowed, reason = self._circuit_breaker.is_trading_allowed()
        if not allowed:
            return

    Usage in signal_bot._handle_exit() AFTER P&L calculation:
        self._circuit_breaker.record_trade_result(pnl)
    """

    def __init__(self, kill_switch: KillSwitch, loss_monitor: DailyLossMonitor,
                 streak_tracker: StreakTracker, logger):
        self._ks = kill_switch
        self._lm = loss_monitor
        self._st = streak_tracker
        self._logger = logger
        self._logger.info(
            f"Circuit breaker initialized | "
            f"kill_switch={'enabled' if kill_switch._path else 'disabled'} | "
            f"daily_loss_limit=${loss_monitor._max_daily_loss:.0f} | "
            f"max_consecutive_losses={streak_tracker._max} | "
            f"cooldown={streak_tracker._cooldown_mins}min"
        )

    def is_trading_allowed(self) -> Tuple[bool, str]:
        """Check all circuit breakers. Returns (allowed, reason)."""
        if self._ks.is_active():
            return False, "kill_switch_active"
        if self._lm.has_hit_limit():
            return False, f"daily_loss_limit (${self._lm.get_daily_pnl():.2f})"
        if self._st.in_cooldown():
            remaining = max(0, self._st._cooldown_until - time.time())
            return False, f"consecutive_loss_cooldown ({remaining:.0f}s remaining)"
        return True, ""

    def record_trade_result(self, pnl: float):
        """Record trade outcome. Called after each exit."""
        self._st.record_result(pnl)
        if pnl < 0:
            self._logger.info(
                f"Circuit breaker: loss ${pnl:.2f} | "
                f"streak={self._st.consecutive_losses}/{self._st._max}"
            )
