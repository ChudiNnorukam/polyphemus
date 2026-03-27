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
        # Optional momentum feed for reversal exit (set via set_momentum_feed)
        self._momentum_feed = None
        # Dedup set for dry-run confidence exit (log once per position)
        self._confidence_exit_logged: Set[str] = set()
        # Optional Chainlink oracle feed for oracle-based reversal (set via set_chainlink_feed)
        self._chainlink = None

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

        # === ROUTE weather positions to dedicated evaluator (48h hold, fixed price target) ===
        if pos.metadata and pos.metadata.get("is_weather"):
            return self._evaluate_weather(pos, now)

        # === Hold-to-resolution positions: oracle flips and snipes ===
        # These enter near market end with cheap tokens ($0.01-$0.25) that
        # resolve to $1 if correct. ONLY exit via market_resolved.
        # stop_loss/profit_target/max_hold would all misfire on cheap tokens.
        source = (pos.metadata or {}).get("source", "")
        hold_to_resolution = source in (
            "oracle_flip", "resolution_snipe", "resolution_snipe_15m",
            "reversal_short", "tugao9_copy", "phase_gate_hedge",
            "pair_arb", "cheap_side", "lottery",
        )

        # IGOC: Dynamic hold_to_resolution based on oracle confirmation
        if source == "clob_imbalance" and self._chainlink:
            asset = pos.metadata.get("asset", "")
            oracle_dir = self._chainlink.get_direction_confirmed(asset, n=self._config.igoc_oracle_confirm_n)
            trade_dir = pos.metadata.get("direction", "")
            hold_to_resolution = (oracle_dir is not None and oracle_dir == trade_dir)

        # === TIME CHECK #1: max_hold (TIME, independent of price) ===
        if not hold_to_resolution and self._check_max_hold(pos, now):
            return ExitSignal(
                token_id=pos.token_id,
                reason=ExitReason.MAX_HOLD.value,
                exit_price=pos.current_price or pos.entry_price,
            )

        # === CHECK 1b: market past end → market_resolved (skip SELL) ===
        # After resolution, the orderbook is REMOVED.
        # Attempting a SELL on a resolved market = "orderbook does not exist" error
        # → records exit_price=$0.00 → catastrophic P&L. (Bug #35)
        # Instead, treat as market_resolved: skip SELL, let redeemer handle.
        from .types import parse_window_from_slug
        window_secs = parse_window_from_slug(pos.slug)
        if pos.market_end_time:
            secs_past_end = (now - pos.market_end_time).total_seconds()
            if secs_past_end > 30:
                self._logger.info(
                    f"Market ended {secs_past_end:.0f}s ago → market_resolved | "
                    f"slug={pos.slug} (skipping SELL, orderbook likely gone)"
                )
                return ExitSignal(
                    token_id=pos.token_id,
                    reason=ExitReason.MARKET_RESOLVED.value,
                    exit_price=None,  # No SELL needed
                )

        # === TIME CHECK #2: time_exit (TIME, independent of price) ===
        # Gated by hold_to_resolution: cheap_side/oracle_flip/snipe hold to resolution,
        # selling them before end loses the payoff. Consistent with max_hold gate above.
        if not hold_to_resolution and self._check_time_exit(pos, now):
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

        # === CHECK #3b: direction reversal exit ===
        # Detects underlying asset (Binance) reversing against entry direction.
        # NOT gated by hold_to_resolution: that flag blocks price-level exits
        # (stop_loss/profit_target) which misfire on binary tokens. This check
        # is direction-based -- if Binance reversed, selling at $0.50 is better
        # than resolution at $0.005.
        # Priority: (A) Binance entry-relative (~100ms), (B) Chainlink oracle (27s),
        #           (C) Binance rolling pct (fallback)
        # Default off (MOMENTUM_REVERSAL_EXIT=false).
        # Reversal exit is direction-based (not price-level), so snipes can use it
        # (selling at $0.50 beats resolving to $0.00). But oracle_flip/reversal_short
        # enter at cheap prices where mid-price sells destroy EV - they hold to resolution.
        cheap_hold = source in ("oracle_flip", "reversal_short")
        if (self._config.momentum_reversal_exit
                and not cheap_hold
                and pos.metadata
                and pos.metadata.get("entry_momentum_direction")):
            entry_dir = pos.metadata["entry_momentum_direction"]
            reversed_ = False
            source_used = "none"
            asset = pos.slug.split("-")[0].upper()
            window = 300 if '5m' in pos.slug else 900
            pos_age = (time.time() - pos.entry_time.timestamp()) if pos.entry_time else 0

            # Path A: Binance entry-relative reversal (~100ms detection)
            # Compares current Binance price to price at signal time
            entry_binance = pos.metadata.get("entry_binance_price", 0.0)
            if (entry_binance > 0
                    and self._momentum_feed
                    and pos_age >= self._config.binance_reversal_min_hold_secs):
                current_binance = self._momentum_feed.get_latest_price(asset)
                if current_binance and current_binance > 0:
                    change_pct = (current_binance - entry_binance) / entry_binance
                    threshold = self._config.momentum_reversal_pct
                    source_used = "binance"
                    reversed_ = (
                        (entry_dir == "up" and change_pct < -threshold)
                        or (entry_dir == "down" and change_pct > threshold)
                    )

            # Path B: Chainlink oracle (backup, 27s heartbeat)
            if (source_used == "none"
                    and self._config.oracle_reversal_exit
                    and self._chainlink
                    and self._chainlink.is_healthy(asset)
                    and (not self._config.oracle_exit_5m_only or window <= 300)):
                secs_remaining = (pos.market_end_time.timestamp() - time.time()) if pos.market_end_time else 999

                # Use wider ceiling for momentum entries (>120s before market end)
                effective_ceiling = self._config.oracle_exit_secs_remaining
                if pos.market_end_time and pos.entry_time:
                    time_at_entry = pos.market_end_time.timestamp() - pos.entry_time.timestamp()
                    if time_at_entry > 120:
                        effective_ceiling = self._config.oracle_exit_secs_remaining_momentum

                if (pos_age >= self._config.oracle_exit_min_hold_secs
                        and secs_remaining <= effective_ceiling):
                    parts = pos.slug.rsplit('-', 1)
                    if len(parts) == 2 and parts[1].isdigit():
                        epoch = int(parts[1])
                        oracle_verdict = self._chainlink.is_above_window_open(
                            epoch, window, asset
                        )
                        if oracle_verdict is not None:
                            source_used = "chainlink"
                            reversed_ = (
                                (entry_dir == "up" and not oracle_verdict)
                                or (entry_dir == "down" and oracle_verdict)
                            )

            # Path C: Binance rolling pct (fallback for when A and B miss)
            if source_used == "none" and self._momentum_feed:
                ts = pos.metadata.get("entry_momentum_ts")
                if ts:
                    secs_since = time.time() - ts
                    if 0 < secs_since < self._config.momentum_reversal_window_secs:
                        current_pct = self._momentum_feed.get_current_momentum_pct(asset)
                        if current_pct is not None:
                            source_used = "binance_rolling"
                            threshold = self._config.momentum_reversal_pct
                            reversed_ = (
                                (entry_dir == "up" and current_pct < -threshold)
                                or (entry_dir == "down" and current_pct > threshold)
                            )

            if reversed_:
                reason = "oracle_reversal" if source_used == "chainlink" else "binance_reversal"
                extra_info = ""
                if source_used == "chainlink" and self._chainlink:
                    cp = self._chainlink.get_current_price(asset)
                    extra_info = f" | oracle_price={cp:,.2f}" if cp else ""
                elif source_used == "binance" and entry_binance > 0:
                    current_b = self._momentum_feed.get_latest_price(asset) if self._momentum_feed else 0
                    change = ((current_b - entry_binance) / entry_binance * 100) if current_b and entry_binance else 0
                    extra_info = f" | binance_entry={entry_binance:,.2f} | binance_now={current_b:,.2f} | change={change:+.2f}%"

                if self._config.momentum_reversal_dry_run:
                    self._logger.info(
                        f"[DRY] {reason} WOULD fire | token={pos.token_id[:8]} | "
                        f"entry_dir={entry_dir} | source={source_used}{extra_info} | "
                        f"entry_price={pos.entry_price:.4f} | "
                        f"current_price={pos.current_price:.4f}"
                    )
                else:
                    self._logger.info(
                        f"{reason} triggered | token_id={pos.token_id} | "
                        f"entry_dir={entry_dir} | source={source_used}{extra_info}"
                    )
                    return ExitSignal(
                        token_id=pos.token_id,
                        reason=reason,
                        exit_price=pos.current_price or pos.entry_price,
                    )

        # === CHECK #3b2: trailing stop (lock in gains from peak) ===
        # Only fires after position gained trailing_stop_min_gain_pct from entry,
        # then dropped trailing_stop_pct from peak. Skips hold_to_resolution.
        if (self._config.trailing_stop_enabled
                and not hold_to_resolution
                and pos.current_price > 0
                and pos.peak_price > 0
                and pos.entry_time
                and (datetime.now(timezone.utc) - pos.entry_time).total_seconds() >= 15):
            min_peak = pos.entry_price * (1 + self._config.trailing_stop_min_gain_pct)
            if pos.peak_price >= min_peak:
                trail_stop = pos.peak_price * (1 - self._config.trailing_stop_pct)
                if pos.current_price <= trail_stop:
                    if self._config.trailing_stop_dry_run:
                        self._logger.info(
                            f"[DRY] trailing_stop WOULD fire | token={pos.token_id[:8]} | "
                            f"entry={pos.entry_price:.4f} | peak={pos.peak_price:.4f} | "
                            f"current={pos.current_price:.4f} | trail_stop={trail_stop:.4f}"
                        )
                        return None  # block lower-priority exits when trailing stop would fire
                    else:
                        self._logger.info(
                            f"trailing_stop triggered | token_id={pos.token_id} | "
                            f"entry={pos.entry_price:.4f} | peak={pos.peak_price:.4f} | "
                            f"current={pos.current_price:.4f} | trail_stop={trail_stop:.4f}"
                        )
                        return ExitSignal(
                            token_id=pos.token_id,
                            reason="trailing_stop",
                            exit_price=pos.current_price,
                        )

        # === CHECK #3b3: confidence exit (midpoint trajectory) ===
        # Research: at 60-120s into a 5m position, winners avg midpoint 0.71
        # while losers avg 0.47. If midpoint < threshold, cut early.
        # Skips hold_to_resolution sources (oracle_flip, snipe, etc.)
        if (self._config.confidence_exit_enabled
                and not hold_to_resolution
                and pos.current_price > 0
                and pos.entry_time):
            pos_age = (datetime.now(timezone.utc) - pos.entry_time).total_seconds()
            # Book depth guard: skip confidence exit if midpoint is unreliable.
            # If peak_price never exceeded threshold, the position entered cheap
            # and the low midpoint is normal, not a signal of losing.
            # Also skip if current_price is suspiciously near 0 (thin book artifact).
            peak_ok = pos.peak_price >= self._config.confidence_exit_threshold if pos.peak_price > 0 else True
            book_ok = pos.current_price > 0.02  # below 0.02 = likely no real book
            if (self._config.confidence_exit_min_hold_secs
                    <= pos_age
                    <= self._config.confidence_exit_max_hold_secs
                    and pos.current_price < self._config.confidence_exit_threshold
                    and peak_ok and book_ok):
                if self._config.confidence_exit_dry_run:
                    if pos.token_id not in self._confidence_exit_logged:
                        self._confidence_exit_logged.add(pos.token_id)
                        self._logger.info(
                            f"[DRY] confidence_exit WOULD fire | token={pos.token_id[:8]} | "
                            f"entry={pos.entry_price:.4f} | midpoint={pos.current_price:.4f} | "
                            f"threshold={self._config.confidence_exit_threshold} | "
                            f"age={pos_age:.0f}s | slug={pos.slug}"
                        )
                else:
                    self._logger.info(
                        f"confidence_exit triggered | token_id={pos.token_id} | "
                        f"entry={pos.entry_price:.4f} | midpoint={pos.current_price:.4f} | "
                        f"threshold={self._config.confidence_exit_threshold} | "
                        f"age={pos_age:.0f}s | slug={pos.slug}"
                    )
                    return ExitSignal(
                        token_id=pos.token_id,
                        reason=ExitReason.CONFIDENCE_EXIT.value,
                        exit_price=pos.current_price,
                    )

        # === CHECK #3c: mid-price stop for momentum AND high-entry snipe positions ===
        # Snipe positions are in hold_to_resolution (needed for pre_resolution_exit, max_hold bypass).
        # But high-entry snipes (>= 0.80) benefit from mid_price_stop to cap loss asymmetry.
        # This bypass enables mid_price_stop ONLY, without affecting other hold_to_resolution checks.
        snipe_with_stop = (
            source in ("resolution_snipe", "resolution_snipe_15m")
            and pos.entry_price >= 0.80
        )
        if (self._config.mid_price_stop_enabled
                and (not hold_to_resolution or snipe_with_stop)
                and pos.current_price > 0
                and pos.entry_time
                and (datetime.now(timezone.utc) - pos.entry_time).total_seconds() >= 2):
            # Use IGOC-specific stop % for clob_imbalance, else global mid_price_stop_pct
            stop_pct = (self._config.igoc_stop_pct if source == "clob_imbalance"
                        else self._config.mid_price_stop_pct)
            stop_price = pos.entry_price * (1 - stop_pct)
            if pos.current_price <= stop_price:
                self._logger.info(
                    f"mid_price_stop triggered | token_id={pos.token_id} | source={source} | "
                    f"entry={pos.entry_price:.4f} | current={pos.current_price:.4f} | "
                    f"stop_price={stop_price:.4f}"
                )
                return ExitSignal(
                    token_id=pos.token_id,
                    reason=ExitReason.MID_PRICE_STOP.value,
                    exit_price=pos.current_price,
                )

        # === CHECK #3d: pre-resolution exit ===
        # Dump LOSING positions N seconds before market ends to avoid $0.005 resolution.
        # WINNING positions (current > entry) hold to $1.00 resolution - selling at $0.96
        # loses $0.56/trade vs holding. Selling losers at $0.41 saves $5.67/trade vs $0.005.
        # Born from: Mar 21 2026 audit - 29 winning trades sold at $0.96 instead of $1.00,
        # leaving $16.24 on the table across 36 trades.
        snipe_losing = (
            hold_to_resolution
            and source in ("resolution_snipe", "resolution_snipe_15m")
            and pos.current_price > 0
            and pos.current_price < pos.entry_price
        )
        is_losing = pos.current_price < pos.entry_price
        if (self._config.pre_resolution_exit_secs > 0
                and (not hold_to_resolution or snipe_losing)
                and is_losing
                and pos.market_end_time
                and pos.current_price > 0):
            secs_remaining = (pos.market_end_time.timestamp() - time.time())
            if 0 < secs_remaining <= self._config.pre_resolution_exit_secs:
                self._logger.info(
                    f"pre_resolution_exit | token_id={pos.token_id} | "
                    f"slug={pos.slug} | secs_remaining={secs_remaining:.0f} | "
                    f"current_price={pos.current_price:.4f} | "
                    f"entry_price={pos.entry_price:.4f} | losing=True"
                )
                return ExitSignal(
                    token_id=pos.token_id,
                    reason=ExitReason.PRE_RESOLUTION_EXIT.value,
                    exit_price=pos.current_price,
                )

        # === CHECK #3e: early profit target (bypasses hold_to_resolution) ===
        # Fires when position is up profit_target_early_pp (after taker fee) with >= min_secs remaining.
        # Fee correction: taker fee at exit = p²*(1-p). At p=0.92: ~6.8%. Without this, a 7pp gross
        # gain at 0.92 is nearly breakeven after fees. Only counts net gain toward the threshold.
        if (self._config.profit_target_early_enabled
                and pos.current_price > 0
                and pos.market_end_time):
            secs_remaining = pos.market_end_time.timestamp() - time.time()
            gain_pp = pos.current_price - pos.entry_price
            p = pos.current_price
            fee_at_exit = p * p * (1.0 - p) if self._config.profit_target_early_apply_fee_correction else 0.0
            net_gain_pp = gain_pp - fee_at_exit
            if (net_gain_pp >= self._config.profit_target_early_pp
                    and secs_remaining >= self._config.profit_target_early_min_secs):
                if self._config.profit_target_early_dry_run:
                    self._logger.info(
                        f"[DRY] profit_target_early WOULD fire | token={pos.token_id[:8]} | "
                        f"entry={pos.entry_price:.4f} | current={pos.current_price:.4f} | "
                        f"gross={gain_pp:.4f}pp | fee={fee_at_exit:.4f}pp | net={net_gain_pp:.4f}pp | "
                        f"secs_remaining={secs_remaining:.0f}"
                    )
                else:
                    self._logger.info(
                        f"profit_target_early triggered | token_id={pos.token_id} | "
                        f"entry={pos.entry_price:.4f} | current={pos.current_price:.4f} | "
                        f"gross={gain_pp:.4f}pp | fee={fee_at_exit:.4f}pp | net={net_gain_pp:.4f}pp | "
                        f"secs_remaining={secs_remaining:.0f}"
                    )
                    return ExitSignal(
                        token_id=pos.token_id,
                        reason=ExitReason.PROFIT_TARGET_EARLY.value,
                        exit_price=pos.current_price,
                    )

        # === HOLD-TO-RESOLUTION: skip stop_loss and profit_target ===
        # Oracle flips/snipes always hold. Regular 5m positions hold if config says so.
        skip_price_exits = hold_to_resolution or (
            self._config.hold_to_resolution and window_secs <= 300
        )
        if skip_price_exits:
            # Only exit via market_resolved (check #3 / check 1b above)
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

    def _evaluate_weather(self, pos: Position, now: datetime) -> Optional[ExitSignal]:
        """Evaluate a weather position with long-hold logic.

        Uses hours-based max hold (not minutes) and a fixed absolute price target
        instead of a percentage profit_target. Skips max_hold_mins and time_exit_buffer.
        """
        # 1. Max hold in hours (weather positions can hold 24-48h)
        if pos.entry_time is None:
            self._logger.warning(
                f"Weather position missing entry_time | token_id={pos.token_id} | "
                f"will rely on market_resolved or profit_target to exit"
            )
        else:
            max_hold_hours = pos.metadata.get("weather_max_hold_hours", 48.0)
            hold_hours = (now - pos.entry_time).total_seconds() / 3600
            if hold_hours >= max_hold_hours:
                self._logger.debug(
                    f"weather max_hold triggered | token_id={pos.token_id} | "
                    f"hold_hours={hold_hours:.1f} | max={max_hold_hours}"
                )
                return ExitSignal(
                    token_id=pos.token_id,
                    reason=ExitReason.MAX_HOLD.value,
                    exit_price=pos.current_price or pos.entry_price,
                )

        # 2. Market resolved (redeemer handles, no SELL needed)
        if pos.is_resolved:
            return ExitSignal(
                token_id=pos.token_id,
                reason=ExitReason.MARKET_RESOLVED.value,
                exit_price=None,
            )

        # 3. Hold to resolution: skip price exit
        if pos.metadata.get("weather_hold_to_resolution"):
            return None

        # 4. Price target: sell when market corrects to weather_exit_price
        exit_price_target = pos.metadata.get("weather_exit_price", 0.45)
        if pos.current_price > 0 and pos.current_price >= exit_price_target:
            self._logger.debug(
                f"weather profit_target triggered | token_id={pos.token_id} | "
                f"current_price={pos.current_price:.4f} | target={exit_price_target:.4f}"
            )
            return ExitSignal(
                token_id=pos.token_id,
                reason=ExitReason.PROFIT_TARGET.value,
                exit_price=pos.current_price,
            )

        # 5. Time-decay exit: <2h to resolution, price still low — cut the loss
        market_end_ts = pos.metadata.get("weather_market_end_ts")
        if market_end_ts and pos.current_price > 0:
            hours_to_resolution = (market_end_ts - now.timestamp()) / 3600
            if hours_to_resolution < 2.0 and pos.current_price < 0.20:
                self._logger.info(
                    f"weather time_decay exit | token_id={pos.token_id} | "
                    f"{hours_to_resolution:.1f}h to resolution | "
                    f"price={pos.current_price:.4f} < 0.20 | cutting loss"
                )
                return ExitSignal(
                    token_id=pos.token_id,
                    reason="weather_time_decay",
                    exit_price=pos.current_price,
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
        Skips positions designed to hold to resolution (snipe, oracle_flip).

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

        # Skip time_exit for positions that should hold to resolution.
        # Oracle flips and snipes enter near market end with cheap tokens
        # that resolve to $1 if correct. Selling them early is always wrong.
        source = (pos.metadata or {}).get("source", "")
        if source in ("oracle_flip", "resolution_snipe", "resolution_snipe_15m"):
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

    def set_momentum_feed(self, feed) -> None:
        """Inject BinanceMomentumFeed for reversal exit checks."""
        self._momentum_feed = feed

    def set_chainlink_feed(self, feed) -> None:
        """Inject ChainlinkFeed for oracle-based reversal exit checks."""
        self._chainlink = feed

    def complete_exit(self, token_id: str) -> None:
        """
        Mark exit as complete (remove from pending and clear failure tracking).

        Args:
            token_id: Token ID that exited successfully
        """
        self._pending_exits.discard(token_id)
        self._exit_failures.pop(token_id, None)
        self._confidence_exit_logged.discard(token_id)
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
