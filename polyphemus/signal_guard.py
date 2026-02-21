"""SignalGuard — Combined filter + validator + metrics tracking for signals.

Applies all entry criteria, guard clauses, and dedup rules.
Tracks rejection reasons and pass rates for monitoring.
"""

import time
import logging
from datetime import datetime, timezone
from typing import Dict, List

from .types import FilterResult
from .config import Settings
from .position_store import PositionStore


class SignalGuard:
    """Filter and validate signals before execution.

    Responsibilities:
    - Apply entry price range validation
    - Check blocked assets
    - Enforce blackout hours
    - Validate signal freshness
    - Detect duplicate slugs
    - Enforce max position limits
    - Check minimum conviction size
    - Track all rejection reasons and metrics
    """

    def __init__(self, config: Settings, store: PositionStore) -> None:
        """Initialize SignalGuard with config and position store.

        Args:
            config: Settings object with price ranges, blocked assets, etc.
            store: PositionStore for dedup and position count checks
        """
        self._config = config
        self._store = store
        self._logger = logging.getLogger('polyphemus.guard')

        # Metrics
        self.signals_received: int = 0
        self.signals_passed: int = 0
        self.rejection_reasons: Dict[str, int] = {}

    def check(self, signal: dict) -> FilterResult:
        """Apply all filters and validators to a signal.

        Collects ALL rejection reasons (does not short-circuit).
        Updates metrics for signals_received, signals_passed, and rejection_reasons.

        Args:
            signal: Dict with keys: direction, outcome, price, asset,
                   timestamp, slug, usdc_size, etc.

        Returns:
            FilterResult with passed=True if all checks pass, False otherwise.
            reasons list contains human-readable rejection reasons.
        """
        reasons: List[str] = []

        # Increment received counter
        self.signals_received += 1

        # Check if this is a momentum signal (skip some filters for momentum)
        is_momentum = signal.get('source') == 'binance_momentum'
        is_window_delta = signal.get('source') == 'window_delta'

        # ====================================================================
        # FILTER 1: Direction Check (only BUY signals from DB)
        # ====================================================================
        if not is_momentum and not is_window_delta:
            direction = signal.get('direction', '').upper()
            if direction != 'BUY':
                reasons.append('not_buy_signal')

        # ====================================================================
        # FILTER 2: Outcome Check (UP or DOWN outcomes)
        # ====================================================================
        outcome = signal.get('outcome', '').lower()
        if outcome not in ('up', 'down'):
            reasons.append('invalid_outcome')

        # ====================================================================
        # FILTER 3: Price Range Check (window-aware)
        # 5m markets: wider range (0.20-0.80) — short window limits downside
        # 15m markets: tight range (config values) — more reversal risk
        # ====================================================================
        price = signal.get('price', 0)
        market_window = signal.get('market_window_secs', 900)
        min_price = self._config.min_entry_price
        max_price = self._config.max_entry_price
        # Window delta fires near expiry where prices are extreme (>0.90)
        # — exempt from standard price range, use dedicated max instead
        if is_window_delta:
            if price > self._config.window_delta_max_price:
                reasons.append('price_out_of_range')
        elif price < min_price or price > max_price:
            reasons.append('price_out_of_range')

        # Trap zone filter: reject midpoints in the 0.60-0.80 range
        # These entries have 33% WR vs 75-100% outside the zone
        trap_lo = self._config.entry_trap_low
        trap_hi = self._config.entry_trap_high
        if trap_lo > 0 and trap_hi > 0 and trap_lo < price < trap_hi:
            reasons.append('entry_trap_zone')

        # ====================================================================
        # FILTER 4a: Asset Allow-List (if configured)
        # ====================================================================
        asset = signal.get('asset', '').upper()
        allowed_assets = self._config.get_asset_filter()
        if allowed_assets and asset not in allowed_assets:
            reasons.append('asset_not_in_filter')

        # ====================================================================
        # FILTER 4b: Blocked Assets Check
        # ====================================================================
        blocked_assets_upper = [a.upper() for a in self._config.get_blocked_assets()]
        if asset in blocked_assets_upper:
            reasons.append('blocked_asset')

        # ====================================================================
        # FILTER 5: Blackout Hours Check
        # ====================================================================
        hour_utc = datetime.now(timezone.utc).hour
        blackout_hours = self._config.get_blackout_hours()
        if hour_utc in blackout_hours:
            reasons.append('blackout_hour')

        # ====================================================================
        # VALIDATOR 1: Market Expiry Check (configurable window)
        # Parse epoch from slug like "btc-updown-15m-1770598800"
        # Reject if market has < min_secs_remaining left
        # (otherwise time_exit triggers immediately after entry)
        # Window delta signals are DESIGNED to fire with <10s remaining — skip check
        # ====================================================================
        slug = signal.get('slug', '')
        from .types import parse_window_from_slug
        window = parse_window_from_slug(slug)
        if not is_window_delta:
            # Cap min_secs at 40% of window so 5m markets (300s) aren't blocked
            # 5m: min(360, 120) = 120s → 3min entry window
            # 15m: min(360, 360) = 360s → 9min entry window
            min_secs = min(self._config.min_secs_remaining, int(window * 0.4))
            parts = slug.rsplit('-', 1)
            if len(parts) == 2 and parts[1].isdigit():
                market_epoch = int(parts[1])
                market_end = market_epoch + window
                secs_left = market_end - time.time()
                if secs_left < min_secs:
                    reasons.append('market_expired')

        # ====================================================================
        # VALIDATOR 2: Dedup Check (via PositionStore)
        # ====================================================================
        slug = signal.get('slug', '')
        if self._store.get_by_slug(slug) is not None:
            reasons.append('duplicate_slug')

        # ====================================================================
        # VALIDATOR 3: Max Positions Check
        # ====================================================================
        if self._store.count_open() >= self._config.max_open_positions:
            reasons.append('max_positions')

        # ====================================================================
        # VALIDATOR 3b: Direction Limiter (max 2 same-direction positions)
        # Prevents correlated wipeouts when all positions are same direction
        # ====================================================================
        MAX_SAME_DIRECTION = 4
        signal_outcome = signal.get('outcome', '').lower()
        if signal_outcome:
            same_dir_count = sum(
                1 for pos in self._store.get_open()
                if getattr(pos, 'outcome', '').lower() == signal_outcome
            )
            if same_dir_count >= MAX_SAME_DIRECTION:
                reasons.append('direction_limit')

        # ====================================================================
        # VALIDATOR 4: Minimum Conviction Check
        # ====================================================================
        usdc_size = signal.get('usdc_size', 0)
        if not is_momentum and not is_window_delta:
            if usdc_size < self._config.min_db_signal_size:
                reasons.append('low_conviction')

        # ====================================================================
        # Record Metrics
        # ====================================================================
        passed = len(reasons) == 0
        if passed:
            self.signals_passed += 1

        for reason in reasons:
            self.rejection_reasons[reason] = self.rejection_reasons.get(reason, 0) + 1

        # Near-miss detection: would have passed if not for market_expired
        if not passed and 'market_expired' in reasons:
            other_reasons = [r for r in reasons if r != 'market_expired']
            if not other_reasons:
                # Parse time remaining for diagnostics
                slug = signal.get('slug', '')
                parts = slug.rsplit('-', 1)
                secs_left = 0
                if len(parts) == 2 and parts[1].isdigit():
                    market_end = int(parts[1]) + window
                    secs_left = market_end - time.time()
                self._logger.warning(
                    f"NEAR MISS: {slug} @ {price:.2f} "
                    f"(${usdc_size:.1f}) — only blocked by market_expired "
                    f"({secs_left:.0f}s left, need {min_secs}s)"
                )
                self.rejection_reasons['near_miss'] = self.rejection_reasons.get('near_miss', 0) + 1

        return FilterResult(passed=passed, reasons=reasons)

    def get_metrics(self) -> dict:
        """Return metrics snapshot.

        Returns:
            Dict with keys:
            - signals_received: Total signals checked
            - signals_passed: Signals that passed all checks
            - pass_rate: Percentage of signals that passed (0-100)
            - rejection_reasons: Dict of reason -> count
        """
        pass_rate = 0.0
        if self.signals_received > 0:
            pass_rate = (self.signals_passed / self.signals_received) * 100

        return {
            'signals_received': self.signals_received,
            'signals_passed': self.signals_passed,
            'pass_rate': pass_rate,
            'rejection_reasons': dict(self.rejection_reasons),
        }

    def reset_metrics(self) -> None:
        """Reset all metrics counters to 0."""
        self.signals_received = 0
        self.signals_passed = 0
        self.rejection_reasons = {}
