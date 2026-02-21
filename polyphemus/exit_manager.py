"""
ExitManager — Exit signal detection and management for Polyphemus Polymarket bot.

This module evaluates open positions against 6 exit criteria:
1. max_hold (TIME — checked FIRST, independent of price)
2. time_exit (TIME — checked SECOND, independent of price)
3. market_resolved (no SELL needed — redeemer handles)
4. stop_loss (price-dependent, checked before profit_target)
5. profit_target (price-dependent)
6. sell_signal (set externally by signal_feed)

Time checks run FIRST per Principle #6. Idempotent: skips positions
already in _pending_exits to avoid duplicate exit submissions.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Set, Tuple

from .types import Position, ExitSignal, ExitReason
from .config import Settings, setup_logger
from .position_store import PositionStore

# Backoff constants for failed SELL attempts
_BACKOFF_BASE = 5  # seconds
_BACKOFF_MAX = 60  # seconds cap


class ExitManager:
    """Evaluates open positions and generates exit signals."""

    def __init__(self, store: PositionStore, config: Settings) -> None:
        """
        Initialize ExitManager with dependencies.

        Args:
            store: PositionStore for accessing open positions
            config: Settings for exit thresholds and parameters
        """
        self._store = store
        self._config = config
        self._logger = setup_logger("polyphemus.exit_manager")
        self._pending_exits: Set[str] = set()
        # Track failed exit attempts: token_id -> (attempt_count, last_attempt_time)
        self._exit_failures: Dict[str, Tuple[int, float]] = {}

    def check_all(self, current_time: datetime) -> list[ExitSignal]:
        """
        Check all open positions for exit conditions.

        Returns list of ExitSignals to execute. Idempotent: skips positions
        already in _pending_exits.

        Args:
            current_time: Current UTC time (timezone-aware)

        Returns:
            List of ExitSignal objects ready for execution
        """
        exits = []
        now_ts = time.time()
        for pos in self._store.get_open():
            # Skip if exit already in-flight
            if pos.token_id in self._pending_exits:
                continue

            # Skip if in backoff from a previous failed SELL
            if pos.token_id in self._exit_failures:
                attempts, last_ts = self._exit_failures[pos.token_id]
                backoff_secs = min(_BACKOFF_MAX, _BACKOFF_BASE * (2 ** (attempts - 1)))
                if now_ts - last_ts < backoff_secs:
                    self._logger.debug(
                        f"Exit backoff | token_id={pos.token_id} | "
                        f"attempt={attempts} | backoff={backoff_secs}s"
                    )
                    continue

            # Evaluate this position
            signal = self._evaluate(pos, current_time)
            if signal:
                exits.append(signal)
                self._pending_exits.add(pos.token_id)
                self._logger.info(
                    f"Exit signal generated | token_id={pos.token_id} | "
                    f"reason={signal.reason} | exit_price={signal.exit_price}"
                )

        return exits

    def _evaluate(self, pos: Position, now: datetime) -> Optional[ExitSignal]:
        """
        Evaluate a position against all 6 exit criteria.

        TIME CHECKS FIRST (Principle #6):
        1. max_hold (independent of price)
        2. time_exit (independent of price)
        3. market_resolved (no SELL needed)
        4. stop_loss (price-dependent, checked before profit_target)
        5. profit_target (price-dependent)
        6. sell_signal (externally set)

        Args:
            pos: Position to evaluate
            now: Current UTC time (timezone-aware)

        Returns:
            ExitSignal if any criterion triggered, None otherwise
        """

        # === SKIP accumulator positions — they are hedged pairs managed by AccumulatorEngine ===
        if pos.metadata and pos.metadata.get("is_accumulator"):
            return None

        # === TIME CHECK #1: max_hold (TIME, independent of price) ===
        if self._check_max_hold(pos, now):
            return ExitSignal(
                token_id=pos.token_id,
                reason=ExitReason.MAX_HOLD.value,
                exit_price=pos.current_price or pos.entry_price,
            )

        # === CHECK 1b: 5-min market past end → market_resolved (skip SELL) ===
        # For 5-min markets, the orderbook is REMOVED after resolution.
        # Attempting a SELL on a resolved market = "orderbook does not exist" error
        # → records exit_price=$0.00 → catastrophic P&L. (Bug #35)
        # Instead, treat as market_resolved: skip SELL, let redeemer handle.
        from .types import parse_window_from_slug
        window_secs = parse_window_from_slug(pos.slug)
        if window_secs <= 300 and pos.market_end_time:
            secs_past_end = (now - pos.market_end_time).total_seconds()
            if secs_past_end > 30:
                self._logger.info(
                    f"5-min market ended {secs_past_end:.0f}s ago → market_resolved | "
                    f"slug={pos.slug} (skipping SELL, orderbook likely gone)"
                )
                return ExitSignal(
                    token_id=pos.token_id,
                    reason=ExitReason.MARKET_RESOLVED.value,
                    exit_price=None,  # No SELL needed
                )

        # === TIME CHECK #2: time_exit (TIME, independent of price) ===
        if self._check_time_exit(pos, now):
            return ExitSignal(
                token_id=pos.token_id,
                reason=ExitReason.TIME_EXIT.value,
                exit_price=pos.current_price or pos.entry_price,
            )

        # === CHECK #3: market_resolved (no SELL needed) ===
        if pos.is_resolved:
            return ExitSignal(
                token_id=pos.token_id,
                reason=ExitReason.MARKET_RESOLVED.value,
                exit_price=None,  # Redeemer handles, no SELL needed
            )

        # === HOLD-TO-RESOLUTION: skip stop_loss and profit_target on 5m markets ===
        if self._config.hold_to_resolution and window_secs <= 300:
            # Let 5m positions ride to resolution ($0 or $1.00)
            # Only exit via market_resolved, max_hold, or time_exit above
            pass
        else:
            # === CHECK #4: stop_loss (price-dependent, checked before profit_target) ===
            if self._config.enable_stop_loss and self._check_stop_loss(pos):
                return ExitSignal(
                    token_id=pos.token_id,
                    reason=ExitReason.STOP_LOSS.value,
                    exit_price=pos.current_price,
                )

            # === CHECK #5: profit_target (price-dependent) ===
            if self._check_profit_target(pos):
                return ExitSignal(
                    token_id=pos.token_id,
                    reason=ExitReason.PROFIT_TARGET.value,
                    exit_price=pos.current_price,
                )

        # === CHECK #6: sell_signal (set externally by signal_feed) ===
        if self._config.enable_sell_signal_exit and self._check_sell_signal(pos):
            return ExitSignal(
                token_id=pos.token_id,
                reason=ExitReason.SELL_SIGNAL.value,
                exit_price=pos.current_price or pos.entry_price,
            )

        return None

    def _check_max_hold(self, pos: Position, now: datetime) -> bool:
        """
        Check if position has exceeded max hold duration.

        Args:
            pos: Position to check
            now: Current UTC time

        Returns:
            True if held >= config.max_hold_mins
        """
        if pos.entry_time is None:
            return False

        hold_mins = (now - pos.entry_time).total_seconds() / 60
        exceeded = hold_mins >= self._config.max_hold_mins

        if exceeded:
            self._logger.debug(
                f"max_hold triggered | token_id={pos.token_id} | "
                f"hold_mins={hold_mins:.1f} | threshold={self._config.max_hold_mins}"
            )

        return exceeded

    def _check_time_exit(self, pos: Position, now: datetime) -> bool:
        """
        Check if position is within time_exit buffer of market end.

        Does not trigger if market is already resolved (checked in _evaluate).

        Args:
            pos: Position to check
            now: Current UTC time

        Returns:
            True if mins_to_market_end <= config.time_exit_buffer_mins
        """
        if pos.market_end_time is None or pos.is_resolved:
            return False

        # 5-min markets handled by market_resolved check in _evaluate (Bug #35 fix)
        from .types import parse_window_from_slug
        window_secs = parse_window_from_slug(pos.slug)
        if window_secs <= 300:
            return False

        mins_to_end = (pos.market_end_time - now).total_seconds() / 60
        triggered = mins_to_end <= self._config.time_exit_buffer_mins

        if triggered:
            self._logger.debug(
                f"time_exit triggered | token_id={pos.token_id} | "
                f"mins_to_end={mins_to_end:.1f} | buffer={self._config.time_exit_buffer_mins}"
            )

        return triggered

    def _check_profit_target(self, pos: Position) -> bool:
        """
        Check if position has hit profit target.

        Only triggers if current_price > 0 (price data available).

        Args:
            pos: Position to check

        Returns:
            True if current_price >= entry_price * (1 + profit_target_pct)
        """
        if pos.current_price <= 0:
            return False

        target_price = pos.entry_price * (1 + self._config.profit_target_pct)
        triggered = pos.current_price >= target_price

        if triggered:
            self._logger.debug(
                f"profit_target triggered | token_id={pos.token_id} | "
                f"current_price={pos.current_price:.4f} | "
                f"target_price={target_price:.4f}"
            )

        return triggered

    def _check_stop_loss(self, pos: Position) -> bool:
        """
        Check if position has hit stop loss threshold.

        Only triggers if current_price > 0 (price data available).
        Skips check within grace period after entry — taker fills eat the
        ask, temporarily dropping the midpoint below entry price. Without
        grace period, the spread cost triggers an instant false stop_loss.

        Args:
            pos: Position to check

        Returns:
            True if current_price <= entry_price * (1 - stop_loss_pct)
        """
        if pos.current_price <= 0:
            return False

        # Grace period: skip stop_loss within first 15s of entry
        # Taker orders eat the ask → midpoint drops → false stop_loss trigger
        if pos.entry_time:
            age_secs = (datetime.now(timezone.utc) - pos.entry_time).total_seconds()
            if age_secs < 15:
                return False

        stop_price = pos.entry_price * (1 - self._config.stop_loss_pct)
        triggered = pos.current_price <= stop_price

        if triggered:
            self._logger.debug(
                f"stop_loss triggered | token_id={pos.token_id} | "
                f"current_price={pos.current_price:.4f} | "
                f"stop_price={stop_price:.4f}"
            )

        return triggered

    def _check_sell_signal(self, pos: Position) -> bool:
        """
        Check if external sell signal was received.

        Signal set externally by signal_feed via pos.metadata['sell_signal_received'].

        Args:
            pos: Position to check

        Returns:
            True if sell_signal_received is set in metadata
        """
        triggered = pos.metadata.get("sell_signal_received", False)

        if triggered:
            self._logger.debug(
                f"sell_signal triggered | token_id={pos.token_id}"
            )

        return triggered

    def complete_exit(self, token_id: str) -> None:
        """
        Mark exit as complete (remove from pending and clear failure tracking).

        Args:
            token_id: Token ID that exited successfully
        """
        self._pending_exits.discard(token_id)
        self._exit_failures.pop(token_id, None)
        self._logger.debug(f"Exit completed | token_id={token_id}")

    def fail_exit(self, token_id: str) -> None:
        """
        Mark exit as failed with exponential backoff tracking.

        Backoff schedule: 5s, 10s, 20s, 40s, 60s (capped).
        Removes from pending so backoff check in check_all() handles retry timing.

        Args:
            token_id: Token ID that failed to exit
        """
        self._pending_exits.discard(token_id)
        attempts, _ = self._exit_failures.get(token_id, (0, 0))
        attempts += 1
        self._exit_failures[token_id] = (attempts, time.time())
        backoff_secs = min(_BACKOFF_MAX, _BACKOFF_BASE * (2 ** (attempts - 1)))
        self._logger.warning(
            f"Exit failed | token_id={token_id} | "
            f"attempt={attempts} | next_retry_in={backoff_secs}s"
        )

    def is_pending(self, token_id: str) -> bool:
        """
        Check if exit is currently in-flight for this position.

        Args:
            token_id: Token ID to check

        Returns:
            True if exit is pending (submitted but not yet complete/failed)
        """
        return token_id in self._pending_exits
