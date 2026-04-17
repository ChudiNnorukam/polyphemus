"""SignalGuard — Combined filter + validator + metrics tracking for signals.

Applies all entry criteria, guard clauses, and dedup rules.
Tracks rejection reasons and pass rates for monitoring.
"""

import time
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List

from .models import FilterResult, parse_window_from_slug
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

        # Outcome gate: rolling trade outcomes for regime detection
        self._recent_outcomes: deque = deque(maxlen=config.outcome_gate_window)
        self._outcome_gate_blocked: bool = False
        self._outcome_gate_blocked_since: float = 0  # timestamp when gate was blocked

        # Markov gate: sequential streak-based regime detection
        self._markov_consecutive_wins: int = 0
        self._markov_consecutive_losses: int = 0
        self._markov_blocked: bool = False
        self._markov_blocked_since: float = 0
        self._markov_total_outcomes: int = 0

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
        context: dict = {}

        # Increment received counter
        self.signals_received += 1

        # Check if this is a momentum signal (skip some filters for momentum)
        is_momentum = signal.get('source') in ('binance_momentum', 'binance_momentum_lag')
        is_window_delta = signal.get('source') == 'window_delta'
        is_pair_arb = signal.get('source') == 'pair_arb'
        is_weather = signal.get('source') == 'noaa_weather'
        is_snipe = signal.get('source') == 'resolution_snipe'
        is_sharp = signal.get('source') == 'sharp_move'
        is_oracle_flip = signal.get('source') == 'oracle_flip'
        is_streak_contrarian = signal.get('source') == 'streak_contrarian'
        is_clob_imbalance = signal.get('source') == 'clob_imbalance'
        is_flat_regime_rtds = signal.get('source') == 'flat_regime_rtds'
        is_tugao9_copy = signal.get('source') == 'tugao9_copy'
        is_phase_gate_hedge = signal.get('source') == 'phase_gate_hedge'
        is_cheap_side = signal.get('source') == 'cheap_side'
        is_lottery = signal.get('source') == 'lottery'

        # ====================================================================
        # FILTER 1: Direction Check (only BUY signals from DB)
        # ====================================================================
        if not is_momentum and not is_window_delta and not is_snipe and not is_oracle_flip and not is_streak_contrarian and not is_flat_regime_rtds and not is_tugao9_copy and not is_phase_gate_hedge and not is_cheap_side and not is_lottery:
            direction = signal.get('direction', '').upper()
            if direction != 'BUY':
                reasons.append('not_buy_signal')

        # ====================================================================
        # FILTER 2: Outcome Check (UP or DOWN outcomes)
        # ====================================================================
        outcome = signal.get('outcome', '').lower()
        if not is_weather and outcome not in ('up', 'down'):
            reasons.append('invalid_outcome')

        # ====================================================================
        # FILTER 2b: Market Window Type
        # 5m markets: always allowed for momentum
        # 15m markets: allowed if enable_15m_momentum=true AND entering late
        #   (backtest: 90% WR when entering in last 300s with midpoint 0.55-0.90)
        # ====================================================================
        slug_check = signal.get('slug', '')
        is_15m_momentum = False
        if is_momentum and slug_check:
            if '-5m-' in slug_check:
                pass  # 5m always allowed
            elif '-15m-' in slug_check and self._config.enable_15m_momentum:
                parts_15m = slug_check.rsplit('-', 1)
                if len(parts_15m) == 2 and parts_15m[1].isdigit():
                    market_epoch_15m = int(parts_15m[1])
                    market_end_15m = market_epoch_15m + 900
                    secs_left_15m = market_end_15m - time.time()
                    if secs_left_15m > self._config.momentum_15m_max_secs_remaining:
                        reasons.append('15m_too_early')
                    elif secs_left_15m < self._config.momentum_15m_min_secs_remaining:
                        reasons.append('market_expired')
                    else:
                        is_15m_momentum = True
                else:
                    reasons.append('not_5m_market')
            else:
                reasons.append('not_5m_market')

        # ====================================================================
        # FILTER 2c: Book Imbalance Alignment (momentum signals only)
        # book_imbalance = bid/(bid+ask). >0.5 = buy pressure, <0.5 = sell pressure.
        # For "Up" bet: want buy-side dominance (imbalance >= threshold)
        # For "Down" bet: want sell-side dominance (imbalance <= 1 - threshold)
        # Skip if imbalance is None (market_ws has no data yet — don't reject)
        # ====================================================================
        alignment_thresh = self._config.min_book_imbalance_alignment
        if is_momentum and alignment_thresh > 0:
            book_imbalance = signal.get("book_imbalance")
            if book_imbalance is not None:
                signal_direction = signal.get("outcome", "").lower()
                if signal_direction == "up" and book_imbalance < alignment_thresh:
                    reasons.append("book_imbalance_misaligned")
                elif signal_direction == "down" and book_imbalance > (1.0 - alignment_thresh):
                    reasons.append("book_imbalance_misaligned")

        # ====================================================================
        # FILTER 3: Price Range Check (window-aware)
        # 5m markets: wider range (0.20-0.80) — short window limits downside
        # 15m markets: tight range (config values) — more reversal risk
        # ====================================================================
        price = signal.get('price', 0)
        market_window = signal.get('market_window_secs', 900)
        asset = signal.get('asset', '').upper()
        min_price, max_price = self._config.get_entry_range(asset) if asset else (
            self._config.min_entry_price, self._config.max_entry_price
        )
        # Window delta fires near expiry where prices are extreme (>0.90)
        # -- exempt from standard price range, use dedicated max instead
        if is_window_delta:
            if price > self._config.window_delta_max_price:
                reasons.append('price_out_of_range')
        elif is_snipe:
            if price < self._config.snipe_min_entry_price or price > self._config.snipe_max_entry_price:
                reasons.append('price_out_of_range')
        elif is_oracle_flip or is_streak_contrarian or is_flat_regime_rtds or is_tugao9_copy or is_phase_gate_hedge or is_cheap_side:
            pass  # these sources use their own price gates in the scan loop
        elif is_pair_arb:
            pass  # pair_arb uses pair_cost filter in scan loop, not entry price range
        elif is_weather:
            pass  # weather uses weather_entry_max_price filter in scan loop
        elif is_sharp:
            # Sharp moves use extended ceiling (0.90-0.95 zone, near-zero taker fee)
            sharp_max = self._config.sharp_move_max_entry_price
            if price < min_price or price > sharp_max:
                reasons.append('price_out_of_range')
        elif is_clob_imbalance:
            # IGOC uses custom price range
            if price < self._config.igoc_min_price or price > self._config.igoc_max_price:
                reasons.append('price_out_of_range')
        elif price < min_price or price > max_price:
            reasons.append('price_out_of_range')

        # Decision trace context: price range diagnostics
        if price > 0:
            context['price'] = round(price, 4)
            context['min_price'] = round(min_price, 2)
            context['max_price'] = round(max_price, 2)

        # Trap zone filter: reject midpoints in the 0.60-0.80 range
        # These entries have 33% WR vs 75-100% outside the zone
        # Skip for oracle_flip/streak_contrarian — they intentionally target 0.45-0.55
        trap_lo = self._config.entry_trap_low
        trap_hi = self._config.entry_trap_high
        if trap_lo > 0 and trap_hi > 0 and trap_lo < price < trap_hi and not is_oracle_flip and not is_streak_contrarian:
            reasons.append('entry_trap_zone')

        # ====================================================================
        # FILTER 4a: Asset Allow-List (if configured)
        # ====================================================================
        asset = signal.get('asset', '').upper()
        allowed_assets = self._config.get_asset_filter()
        shadow_assets = self._config.get_shadow_assets()
        if allowed_assets and asset not in allowed_assets and asset not in shadow_assets and not is_weather:
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
        # FILTER 5b: Economic Calendar Blackout (FOMC / CPI / NFP / PCE)
        # BTC moves 1-3% on macro releases — fundamental repricing breaks arb thesis
        # ====================================================================
        if not is_weather and self._config.macro_blackout_mins > 0:
            from .economic_calendar import is_macro_blackout
            if is_macro_blackout(self._config.macro_blackout_mins):
                reasons.append('macro_blackout')

        # ====================================================================
        # FILTER 6a: Fear & Greed Regime Check (legacy, default disabled)
        # ====================================================================
        if self._config.fg_min_threshold > 0 and not is_weather:
            fg_value = signal.get('fear_greed')
            if fg_value is not None and fg_value <= self._config.fg_min_threshold:
                reasons.append('extreme_fear_regime')

        # ====================================================================
        # FILTER 6b: Whipsaw Regime Guard
        # Block when volatility is high but net direction is low (chop kills arb)
        # directionality = |trend_1h| / volatility_1h
        # ====================================================================
        if self._config.whipsaw_max_ratio > 0 and not is_weather:
            vol_1h = signal.get('volatility_1h')
            trend_1h = signal.get('trend_1h')
            if vol_1h is not None and trend_1h is not None and vol_1h >= self._config.whipsaw_min_vol:
                directionality = abs(trend_1h) / vol_1h
                if directionality < self._config.whipsaw_max_ratio:
                    reasons.append('whipsaw_regime')
                elif (directionality < self._config.whipsaw_caution_ratio
                      and self._config.eth_block_on_whipsaw_caution
                      and asset == 'ETH'):
                    reasons.append('eth_whipsaw_caution')
                # Decision trace context: whipsaw diagnostics
                context['directionality'] = round(directionality, 3)
                context['vol_1h'] = round(vol_1h, 4)
                context['trend_1h'] = round(trend_1h, 4)

        # ====================================================================
        # FILTER 6c: Flat Regime Block
        # When volatility is too low, directionality ratio is noisy/meaningless.
        # Data: flat regime = -$2.83/signal (worst per-signal P&L on both instances)
        # ====================================================================
        is_flat_regime_rtds = signal.get('source') == 'flat_regime_rtds'
        if self._config.flat_regime_block and not is_weather and not is_clob_imbalance and not is_flat_regime_rtds and not is_tugao9_copy:
            vol_1h = signal.get('volatility_1h')
            if vol_1h is not None and vol_1h < self._config.flat_regime_max_vol:
                reasons.append('flat_regime')
                context['flat_vol_1h'] = round(vol_1h, 5)

        # ====================================================================
        # FILTER 6c2: Stale Regime Data Guard
        # vol_1h=0.0 AND trend_1h=0.0 exactly = regime calculator uninitialized.
        # 7/7 trades with this signature were losses (n=24, Mar 26 2026). Data quality gate.
        # ====================================================================
        if self._config.stale_regime_skip_enabled and not is_weather:
            vol_1h = signal.get('volatility_1h')
            trend_1h = signal.get('trend_1h')
            if vol_1h is not None and trend_1h is not None and vol_1h == 0.0 and trend_1h == 0.0:
                reasons.append('stale_regime_data')
                context['vol_1h'] = 0.0
                context['trend_1h'] = 0.0

        # ====================================================================
        # FILTER 6d: Liquidation Cascade Block
        # Block entries when large cascade aligns AGAINST our signal direction.
        # Massive long liquidations on an UP signal = crowd already flushed out.
        # ====================================================================
        if self._config.liq_cascade_block_enabled and not is_weather and not is_snipe and not is_oracle_flip and not is_streak_contrarian:
            liq_vol = signal.get('liq_volume_60s', 0.0)
            liq_bias = signal.get('liq_bias', '')
            if liq_vol >= self._config.liq_cascade_min_volume:
                outcome = signal.get('outcome', '')
                # "long" bias = longs liquidated = bearish pressure = block UP
                if (liq_bias == 'long' and outcome == 'Up') or (liq_bias == 'short' and outcome == 'Down'):
                    reasons.append('liq_cascade_against')
                context['liq_volume_60s'] = round(liq_vol, 0)
                context['liq_bias'] = liq_bias

        # ====================================================================
        # FILTER 6f: Early-Epoch Entry Filter (S2)
        # Momentum entries are only net-positive in the first ~60s of epoch
        # (T0: 4-5m remaining = +$70.92, later buckets all negative)
        # ====================================================================
        if is_momentum and self._config.momentum_max_epoch_elapsed_secs > 0:
            elapsed_s2 = None
            time_remaining_s2 = signal.get('time_remaining_secs')
            window_s2 = signal.get('market_window_secs') or parse_window_from_slug(signal.get('slug', ''))
            if time_remaining_s2 is not None and window_s2:
                try:
                    elapsed_s2 = max(0.0, float(window_s2) - float(time_remaining_s2))
                except (TypeError, ValueError):
                    elapsed_s2 = None

            if elapsed_s2 is None:
                slug_check_epoch = signal.get('slug', '')
                parts_epoch = slug_check_epoch.rsplit('-', 1)
                if len(parts_epoch) == 2 and parts_epoch[1].isdigit():
                    market_epoch_s2 = int(parts_epoch[1])
                    elapsed_s2 = time.time() - market_epoch_s2
                    if not window_s2:
                        window_s2 = parse_window_from_slug(slug_check_epoch)
                    time_remaining_s2 = max(0.0, float(window_s2) - elapsed_s2) if window_s2 else None

            if elapsed_s2 is not None and elapsed_s2 > self._config.momentum_max_epoch_elapsed_secs:
                reasons.append('epoch_too_late')
                context['epoch_elapsed_secs'] = round(elapsed_s2)
                context['epoch_max_elapsed_secs'] = self._config.momentum_max_epoch_elapsed_secs
                if time_remaining_s2 is not None:
                    context['time_remaining_secs'] = int(max(0.0, float(time_remaining_s2)))

        # ====================================================================
        # FILTER 6e: Extreme Funding Rate Gate
        # Block entries when funding rate is overheated (crowded positioning).
        # Extreme positive funding = overleveraged longs = higher reversal probability.
        # ====================================================================
        if self._config.funding_extreme_block_enabled and not is_weather and not is_snipe and not is_oracle_flip and not is_streak_contrarian:
            fr = signal.get('funding_rate', 0.0)
            outcome = signal.get('outcome', '')
            # Positive funding = longs pay shorts = crowded long; block UP entries
            # Negative funding = shorts pay longs = crowded short; block DOWN entries
            if abs(fr) > self._config.funding_extreme_threshold:
                if (fr > 0 and outcome == 'Up') or (fr < 0 and outcome == 'Down'):
                    reasons.append('funding_extreme')
                context['funding_rate'] = round(fr, 6)

        # ====================================================================
        # FILTER 7: Taker CVD Confirmation
        # Block momentum when taker buy/sell delta disagrees with direction.
        # A price move UP with net taker selling = thin liquidity artifact, not real demand.
        # ====================================================================
        if self._config.cvd_confirmation_enabled and not is_weather and not is_snipe and not is_oracle_flip and not is_streak_contrarian:
            taker_delta = signal.get('taker_delta')
            if taker_delta is not None:
                outcome = signal.get('outcome', '')
                cvd_agrees = (
                    (outcome == 'Up' and taker_delta > 0) or
                    (outcome == 'Down' and taker_delta < 0)
                )
                context['taker_delta'] = round(taker_delta, 4)
                context['cvd_agrees'] = cvd_agrees
                if not cvd_agrees:
                    if self._config.cvd_confirmation_dry_run:
                        context['cvd_blocked_dry_run'] = True
                    else:
                        reasons.append('cvd_disagrees')

        # ====================================================================
        # FILTER 7b: VPIN Adverse Selection Filter (upgraded)
        # Uses volume-synchronized BVC VPIN from vpin_engine.py.
        # Three protection layers:
        #   1. Instant block: VPIN >= threshold AND flow opposes direction
        #   2. Sustained block: VPIN elevated for N consecutive volume bars
        #   3. Graduated sizing: reduce position size proportional to VPIN level
        # ====================================================================
        if self._config.vpin_block_enabled and not is_weather and not is_snipe and not is_oracle_flip and not is_streak_contrarian:
            vpin = signal.get('vpin_5m')
            vpin_sustained = signal.get('vpin_sustained_alert', False)
            if vpin is not None:
                context['vpin_5m'] = round(vpin, 3)
                context['vpin_sustained'] = vpin_sustained

                # Layer 1: Instant block when high VPIN + opposing flow
                if vpin >= self._config.vpin_block_threshold:
                    taker_delta_vpin = signal.get('taker_delta')
                    outcome_vpin = signal.get('outcome', '')
                    if taker_delta_vpin is not None:
                        flow_opposes = (
                            (outcome_vpin == 'Up' and taker_delta_vpin < 0) or
                            (outcome_vpin == 'Down' and taker_delta_vpin > 0)
                        )
                        context['vpin_flow_opposes'] = flow_opposes
                        if flow_opposes:
                            if self._config.vpin_block_dry_run:
                                context['vpin_blocked_dry_run'] = True
                            else:
                                reasons.append('vpin_adverse_selection')

                # Layer 2: Sustained alert block (regardless of direction)
                if vpin_sustained and not self._config.vpin_block_dry_run:
                    reasons.append('vpin_sustained_toxic')
                elif vpin_sustained:
                    context['vpin_sustained_blocked_dry_run'] = True

                # Layer 3: Graduated sizing (pass multiplier to position_executor)
                if self._config.vpin_sizing_enabled and vpin >= self._config.vpin_size_reduce_at:
                    reduce_range = self._config.vpin_block_threshold - self._config.vpin_size_reduce_at
                    if reduce_range > 0:
                        fraction = min((vpin - self._config.vpin_size_reduce_at) / reduce_range, 1.0)
                        vpin_mult = 1.0 - fraction * (1.0 - self._config.vpin_size_min_mult)
                    else:
                        vpin_mult = self._config.vpin_size_min_mult
                    context['vpin_size_mult'] = round(vpin_mult, 2)

        # ====================================================================
        # FILTER 7c: Coinbase Premium Confirmation
        # Block entries when Coinbase Premium strongly disagrees with direction.
        # Positive premium = US institutional buying = bullish signal.
        # Negative premium = US institutional selling = bearish signal.
        # ====================================================================
        if self._config.coinbase_premium_block_enabled and not is_weather and not is_snipe and not is_oracle_flip and not is_streak_contrarian:
            cb_premium_bps = signal.get('coinbase_premium_bps')
            if cb_premium_bps is not None and abs(cb_premium_bps) >= self._config.coinbase_premium_min_bps:
                outcome_cb = signal.get('outcome', '')
                premium_opposes = (
                    (outcome_cb == 'Up' and cb_premium_bps < -self._config.coinbase_premium_min_bps) or
                    (outcome_cb == 'Down' and cb_premium_bps > self._config.coinbase_premium_min_bps)
                )
                context['coinbase_premium_bps'] = round(cb_premium_bps, 1)
                context['cb_premium_opposes'] = premium_opposes
                if premium_opposes:
                    if self._config.coinbase_premium_block_dry_run:
                        context['cb_premium_blocked_dry_run'] = True
                    else:
                        reasons.append('coinbase_premium_opposes')

        # ====================================================================
        # FILTER 8: Outcome Gate (rolling WR regime detection)
        # Blocks all entries when recent trade outcomes indicate a losing regime.
        # Hysteresis: blocks at min_wr, resumes at resume_wr to prevent cycling.
        # ====================================================================
        if self._config.outcome_gate_enabled and not is_weather:
            n_outcomes = len(self._recent_outcomes)
            if n_outcomes >= 3:
                rolling_wr = sum(self._recent_outcomes) / n_outcomes
                context['outcome_gate_wr'] = round(rolling_wr, 3)
                context['outcome_gate_n'] = n_outcomes
                context['outcome_gate_blocked'] = self._outcome_gate_blocked
                # Auto-unblock after 30 min to prevent deadlock
                # (no trades = no new outcomes = gate stuck forever)
                if (self._outcome_gate_blocked
                        and self._outcome_gate_blocked_since > 0
                        and time.time() - self._outcome_gate_blocked_since > 1800):
                    self._outcome_gate_blocked = False
                    self._outcome_gate_blocked_since = 0
                    self._logger.info(
                        f"Outcome gate AUTO-UNBLOCKED after 30min deadlock | "
                        f"rolling_wr={rolling_wr:.1%} | allowing 1 trade to probe regime"
                    )
                    context['outcome_gate_blocked'] = False
                if self._outcome_gate_blocked:
                    if self._config.outcome_gate_dry_run:
                        context['outcome_gate_blocked_dry_run'] = True
                    else:
                        reasons.append('outcome_gate_regime')

        # ====================================================================
        # FILTER 8b: Markov Gate (sequential streak regime detection)
        # Unlike outcome gate (rolling WR), this tracks consecutive W/L streaks.
        # P(W|W)=0.59 vs P(W|L)=0.31 — a 28pp swing. Blocks after loss streaks,
        # resumes after a win. Auto-unblocks after timeout to probe regime.
        # ====================================================================
        if self._config.markov_gate_enabled and not is_weather:
            if self._markov_total_outcomes >= 1:
                context['markov_consec_w'] = self._markov_consecutive_wins
                context['markov_consec_l'] = self._markov_consecutive_losses
                context['markov_blocked'] = self._markov_blocked
                # Auto-unblock after timeout to prevent deadlock
                if (self._markov_blocked
                        and self._markov_blocked_since > 0
                        and time.time() - self._markov_blocked_since > self._config.markov_gate_timeout_secs):
                    self._markov_blocked = False
                    self._markov_blocked_since = 0
                    self._logger.info(
                        f"Markov gate AUTO-UNBLOCKED after {self._config.markov_gate_timeout_secs}s | "
                        f"consec_l={self._markov_consecutive_losses} | probing regime"
                    )
                    context['markov_blocked'] = False
                if self._markov_blocked:
                    if self._config.markov_gate_dry_run:
                        context['markov_blocked_dry_run'] = True
                    else:
                        reasons.append('markov_cold_regime')

        # ====================================================================
        # VALIDATOR 1: Market Expiry Check (configurable window)
        # Parse epoch from slug like "btc-updown-15m-1770598800"
        # Reject if market has < min_secs_remaining left
        # (otherwise time_exit triggers immediately after entry)
        # Window delta signals are DESIGNED to fire with <10s remaining — skip check
        # ====================================================================
        slug = signal.get('slug', '')
        window = parse_window_from_slug(slug)
        min_secs = 0
        secs_left = 0
        if not is_window_delta and not is_weather and not is_15m_momentum and not is_snipe and not is_oracle_flip and not is_streak_contrarian and not is_clob_imbalance:
            # Cap min_secs at 40% of window so 5m markets (300s) aren't blocked
            # 5m: min(360, 120) = 120s → 3min entry window
            # 15m momentum: skipped here — timing validated in FILTER 2b
            min_secs = min(self._config.min_secs_remaining, int(window * 0.4))
            parts = slug.rsplit('-', 1)
            if len(parts) == 2 and parts[1].isdigit():
                market_epoch = int(parts[1])
                market_end = market_epoch + window
                secs_left = market_end - time.time()
                if secs_left < min_secs:
                    reasons.append('market_expired')
        elif is_clob_imbalance:
            # IGOC has custom secs_remaining range
            parts = slug.rsplit('-', 1)
            if len(parts) == 2 and parts[1].isdigit():
                market_epoch = int(parts[1])
                market_end = market_epoch + window
                secs_left = market_end - time.time()
                if secs_left < self._config.igoc_min_secs_remaining or secs_left > self._config.igoc_max_secs_remaining:
                    reasons.append('secs_remaining_out_of_range')

        # ====================================================================
        # VALIDATOR 2: Dedup Check (via PositionStore)
        # ====================================================================
        slug = signal.get('slug', '')
        if self._store.get_by_slug(slug) is not None:
            reasons.append('duplicate_slug')

        # VALIDATOR 2b: Pair arb — block if root slug already held directionally
        # e.g. 'btc-updown-5m-1770944400:up' -> root = 'btc-updown-5m-1770944400'
        if is_pair_arb and ':' in slug:
            root_slug = slug.rsplit(':', 1)[0]
            if self._store.get_by_slug(root_slug) is not None:
                reasons.append('pair_arb_blocked_by_directional')

        # ====================================================================
        # VALIDATOR 3: Max Positions Check
        # Weather uses its own slot budget so momentum/arb positions don't crowd them out
        # ====================================================================
        if is_weather:
            weather_open = sum(
                1 for p in self._store.get_open()
                if p.metadata and p.metadata.get("is_weather")
            )
            if weather_open >= self._config.weather_max_open_positions:
                reasons.append('max_positions')
        elif self._store.count_open() >= self._config.max_open_positions:
            reasons.append('max_positions')

        # ====================================================================
        # VALIDATOR 3b: Direction Limiter (max 2 same-direction positions)
        # Prevents correlated wipeouts when all positions are same direction
        # Weather positions use "yes"/"no" (not "up"/"down") — skip this check
        # ====================================================================
        MAX_SAME_DIRECTION = 4
        signal_outcome = signal.get('outcome', '').lower()
        if signal_outcome and not is_weather:
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
        if not is_momentum and not is_window_delta and not is_weather and not is_snipe and not is_oracle_flip and not is_streak_contrarian and not is_flat_regime_rtds and not is_tugao9_copy and not is_phase_gate_hedge and not is_cheap_side and not is_lottery:
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

        return FilterResult(passed=passed, reasons=reasons, context=context)

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

    def record_outcome(self, is_win: bool) -> None:
        """Record a trade outcome for rolling WR tracking.

        Called by signal_bot after each trade exit. Updates the outcome gate
        blocked/unblocked state based on rolling WR with hysteresis.

        Args:
            is_win: True if the trade was profitable.
        """
        self._recent_outcomes.append(is_win)
        n = len(self._recent_outcomes)
        if n < 3:
            return  # need minimum sample

        rolling_wr = sum(self._recent_outcomes) / n
        was_blocked = self._outcome_gate_blocked

        if self._outcome_gate_blocked:
            if rolling_wr >= self._config.outcome_gate_resume_wr:
                self._outcome_gate_blocked = False
                self._outcome_gate_blocked_since = 0
                self._logger.info(
                    f"Outcome gate UNBLOCKED | rolling_wr={rolling_wr:.1%} "
                    f">= resume={self._config.outcome_gate_resume_wr:.0%} | n={n}"
                )
        else:
            if rolling_wr < self._config.outcome_gate_min_wr:
                self._outcome_gate_blocked = True
                self._outcome_gate_blocked_since = time.time()
                self._logger.warning(
                    f"Outcome gate BLOCKED | rolling_wr={rolling_wr:.1%} "
                    f"< threshold={self._config.outcome_gate_min_wr:.0%} | n={n}"
                )

    def seed_outcomes(self, outcomes: list) -> None:
        """Seed recent outcomes from historical data at startup.

        Args:
            outcomes: List of bools (True=win, False=loss), oldest first.
        """
        for o in outcomes:
            self._recent_outcomes.append(o)
        n = len(self._recent_outcomes)
        if n >= 3:
            rolling_wr = sum(self._recent_outcomes) / n
            if rolling_wr < self._config.outcome_gate_min_wr:
                self._outcome_gate_blocked = True
                self._outcome_gate_blocked_since = time.time()
                self._logger.warning(
                    f"Outcome gate BLOCKED at startup | rolling_wr={rolling_wr:.1%} | n={n}"
                )

    def record_markov_outcome(self, is_win: bool) -> None:
        """Record a trade outcome for Markov streak tracking.

        Updates consecutive win/loss counters and blocked state.
        """
        self._markov_total_outcomes += 1
        if is_win:
            self._markov_consecutive_wins += 1
            self._markov_consecutive_losses = 0
            was_blocked = self._markov_blocked
            if (self._markov_blocked
                    and self._markov_consecutive_wins >= self._config.markov_gate_min_wins):
                self._markov_blocked = False
                self._markov_blocked_since = 0
                self._logger.info(
                    f"Markov gate UNBLOCKED | {self._markov_consecutive_wins} consecutive wins "
                    f">= threshold={self._config.markov_gate_min_wins}"
                )
        else:
            self._markov_consecutive_losses += 1
            self._markov_consecutive_wins = 0
            if (not self._markov_blocked
                    and self._markov_consecutive_losses >= self._config.markov_gate_max_losses):
                self._markov_blocked = True
                self._markov_blocked_since = time.time()
                self._logger.warning(
                    f"Markov gate BLOCKED | {self._markov_consecutive_losses} consecutive losses "
                    f">= threshold={self._config.markov_gate_max_losses}"
                )

    def seed_markov_outcomes(self, outcomes: list) -> None:
        """Seed Markov gate from historical trade outcomes at startup.

        Args:
            outcomes: List of bools (True=win, False=loss), oldest first.
        """
        for o in outcomes:
            self._markov_total_outcomes += 1
            if o:
                self._markov_consecutive_wins += 1
                self._markov_consecutive_losses = 0
            else:
                self._markov_consecutive_losses += 1
                self._markov_consecutive_wins = 0

        if self._markov_total_outcomes > 0:
            if self._markov_consecutive_losses >= self._config.markov_gate_max_losses:
                self._markov_blocked = True
                self._markov_blocked_since = time.time()
                self._logger.warning(
                    f"Markov gate BLOCKED at startup | "
                    f"last {self._markov_consecutive_losses} trades were losses"
                )
            else:
                self._logger.info(
                    f"Markov gate seeded | consec_w={self._markov_consecutive_wins}, "
                    f"consec_l={self._markov_consecutive_losses}, total={self._markov_total_outcomes}"
                )

    def reset_metrics(self) -> None:
        """Reset all metrics counters to 0."""
        self.signals_received = 0
        self.signals_passed = 0
        self.rejection_reasons = {}
