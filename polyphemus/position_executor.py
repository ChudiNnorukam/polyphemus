"""PositionExecutor — BUY order execution with 3-layer position sizing.

This module handles order placement, fill verification, and position creation.
All operations are async and include proper timeout handling via ClobWrapper.
"""

import asyncio
import math
import time
from datetime import datetime, timezone
from typing import Optional

from py_clob_client.order_builder.constants import BUY

from .types import (
    Position,
    ExecutionResult,
    OrderStatus,
    ORDER_POLL_INTERVAL,
    FAK_POLL_INTERVAL,
    ORDER_POLL_MAX,
    MAKER_POLL_MAX,
    TAKER_POLL_MAX,
    MIN_SHARES_FOR_SELL,
    MIN_ENTRY_PRICE,
    MAX_ENTRY_PRICE,
)
from .config import Settings, setup_logger
from .clob_wrapper import ClobWrapper
from .position_store import PositionStore


class PositionExecutor:
    """Handles BUY order execution with 3-layer position sizing and fill verification."""

    def __init__(
        self,
        clob: ClobWrapper,
        store: PositionStore,
        config: Settings,
        tuner=None,
    ):
        """Initialize executor.

        Args:
            clob: ClobWrapper instance for CLOB interactions
            store: PositionStore instance for position tracking
            config: Settings instance with sizing parameters
            tuner: Optional self-tuner for position sizing multiplier
        """
        self._clob = clob
        self._store = store
        self._config = config
        self._tuner = tuner
        self._logger = setup_logger("polyphemus.executor")
        self._fill_optimizer = None  # Set by signal_bot after init
        self._performance_db = None  # Set by signal_bot after init (Kelly sizing)
        self._tracker = None         # Set by signal_bot (flip escalation)
        self._kelly_cache: dict = {}  # (asset, bucket) -> (wr, n, fetched_at)
        self._entry_retry_stats = {
            "placement_retry_eligible": 0,
            "placement_retry_attempted": 0,
            "placement_retry_skipped": 0,
            "fill_retry_eligible": 0,
            "fill_retry_attempted": 0,
            "fill_retry_skipped": 0,
            "fill_retry_succeeded": 0,
            "fill_retry_failed": 0,
            "placement_failures": 0,
            "fill_timeouts": 0,
            "retry_recovered": 0,
            "retry_skip_reasons": {},
        }

    def get_entry_retry_stats(self) -> dict:
        """Return a shallow copy of BTC 5m entry retry counters."""
        stats = dict(self._entry_retry_stats)
        stats["retry_skip_reasons"] = dict(self._entry_retry_stats["retry_skip_reasons"])
        return stats

    def _record_entry_retry_event(self, event: str, slug: str = "", detail: str = "") -> None:
        """Track retry lifecycle events for dashboard observability."""
        if event not in self._entry_retry_stats:
            return
        self._entry_retry_stats[event] += 1
        suffix = f" | {detail}" if detail else ""
        self._logger.info(f"{event}: {slug}{suffix}")

    def _record_entry_retry_skip(self, stage: str, slug: str, reason: str) -> None:
        """Track why a bounded retry was skipped."""
        key = f"{stage}:{reason}"
        skips = self._entry_retry_stats["retry_skip_reasons"]
        skips[key] = skips.get(key, 0) + 1
        event = "placement_retry_skipped" if stage == "placement" else "fill_retry_skipped"
        self._record_entry_retry_event(event, slug, reason)

    def _is_btc5m_retry_candidate(self, signal: dict, window: int) -> bool:
        """Retry scope is intentionally narrow for the first rollout."""
        return (
            bool(self._config.btc5m_entry_retry_enabled)
            and signal.get("asset") == "BTC"
            and window <= 300
            and signal.get("source") == "binance_momentum"
        )

    def _retry_mode_active(self) -> bool:
        return str(getattr(self._config, "btc5m_entry_retry_mode", "shadow")).lower() == "active"

    def _is_transient_execution_error(self, result: ExecutionResult) -> bool:
        """Best-effort detection of a placement failure worth retrying once."""
        text = f"{result.reason} {result.error}".lower()
        needles = (
            "request exception",
            "timeout",
            "connection",
            "tempor",
            "apiexception",
            "server error",
            "status_code=none",
        )
        return any(needle in text for needle in needles)

    def _get_price_tick(self, signal: dict) -> float:
        """Use exchange-provided tick size when available."""
        meta = signal.get("metadata", {}) or {}
        raw = meta.get("price_tick_size") or signal.get("price_tick_size") or 0.01
        try:
            tick = float(raw)
        except (TypeError, ValueError):
            tick = 0.01
        return tick if tick > 0 else 0.01

    def _quantize_price(self, price: float, tick: float, mode: str) -> float:
        """Round prices to the market tick size instead of forcing 1-cent steps."""
        tick = tick if tick > 0 else 0.01
        units = price / tick
        if mode == "down":
            quantized = math.floor(units + 1e-9) * tick
        elif mode == "up":
            quantized = math.ceil(units - 1e-9) * tick
        else:
            quantized = round(units) * tick
        quantized = round(quantized, 6)
        return min(max(quantized, tick), 0.99)

    async def _build_btc5m_retry_plan(
        self,
        signal: dict,
        token_id: str,
        signal_price: float,
    ) -> tuple[float | None, str]:
        """Return a safe retry price or a skip reason."""
        secs_remaining = int(signal.get("time_remaining_secs", 0) or 0)
        min_secs = int(getattr(self._config, "btc5m_entry_retry_min_secs_remaining", 45))
        if secs_remaining < min_secs:
            return None, "too_late"

        midpoint = await self._clob.get_midpoint(token_id)
        if midpoint <= 0:
            return None, "midpoint_unavailable"

        max_entry = float(getattr(self._config, "max_entry_price", MAX_ENTRY_PRICE))
        if midpoint > max_entry:
            return None, "midpoint_above_cap"

        reprice_cents = max(0, int(getattr(self._config, "btc5m_entry_retry_reprice_cents", 1)))
        max_overpay_cents = max(0, int(getattr(self._config, "btc5m_entry_retry_max_overpay_cents", 5)))
        max_retry_price = min(
            max_entry,
            round(signal_price + (max_overpay_cents / 100.0), 2),
        )
        retry_price = round(midpoint + (reprice_cents / 100.0), 2)
        if retry_price > max_retry_price:
            return None, "overpay_cap"

        return retry_price, ""

    async def _maybe_retry_btc5m_entry(
        self,
        *,
        stage: str,
        signal: dict,
        token_id: str,
        slug: str,
        signal_price: float,
        size: float,
        attempts_allowed: int,
    ) -> ExecutionResult | None:
        """Bounded retry path for passed BTC 5m momentum entries."""
        eligible_event = "placement_retry_eligible" if stage == "placement" else "fill_retry_eligible"
        attempted_event = "placement_retry_attempted" if stage == "placement" else "fill_retry_attempted"
        self._record_entry_retry_event(eligible_event, slug)

        if attempts_allowed <= 0:
            self._record_entry_retry_skip(stage, slug, "max_retries_exhausted")
            return None

        retry_price, skip_reason = await self._build_btc5m_retry_plan(signal, token_id, signal_price)
        if retry_price is None:
            self._record_entry_retry_skip(stage, slug, skip_reason)
            return None

        if not self._retry_mode_active():
            self._record_entry_retry_skip(stage, slug, f"shadow_would_retry@{retry_price:.2f}")
            return None

        delay_ms = max(0, int(getattr(self._config, "btc5m_entry_retry_delay_ms", 500)))
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000.0)

        self._record_entry_retry_event(attempted_event, slug, f"retry_price={retry_price:.2f}")
        placement_result = await self._clob.place_order(
            token_id=token_id,
            price=retry_price,
            size=size,
            side=BUY,
        )
        if not placement_result.success:
            self._entry_retry_stats["placement_failures"] += 1
            if stage == "fill":
                self._record_entry_retry_event("fill_retry_failed", slug, placement_result.error)
            return placement_result

        order_id = placement_result.order_id
        self._logger.info(
            "BTC 5m %s retry order placed: %s | %s @ %.2f x %.2f shares",
            stage,
            order_id,
            slug,
            retry_price,
            size,
        )
        _fill_start = time.time()
        fill_result = await self._poll_for_fill(
            order_id,
            token_id,
            retry_price,
            size,
            max_polls=TAKER_POLL_MAX,
        )
        fill_result.fill_time_ms = int((time.time() - _fill_start) * 1000)
        if fill_result.success:
            self._entry_retry_stats["retry_recovered"] += 1
            self._record_entry_retry_event("fill_retry_succeeded", slug, f"retry_price={retry_price:.2f}")
            fill_result.fill_price = retry_price
            return fill_result

        self._entry_retry_stats["fill_timeouts"] += 1
        await self._clob.cancel_order(order_id)
        if stage == "fill":
            self._record_entry_retry_event("fill_retry_failed", slug, fill_result.reason or fill_result.error)
        return fill_result

    async def execute_buy(
        self,
        signal: dict,
        available_capital: float,
    ) -> ExecutionResult:
        """Execute a BUY order from a signal.

        Args:
            signal: Signal dict with keys: token_id, price, slug, market_title, usdc_size
            available_capital: Available USDC balance

        Returns:
            ExecutionResult with success status, order_id, fill details, or error
        """
        # Extract signal fields
        token_id = signal.get("token_id", "")
        price = signal.get("price", 0.0)
        slug = signal.get("slug", "")
        market_title = signal.get("market_title", "")
        usdc_size = signal.get("usdc_size", 0.0)

        if not token_id or price <= 0:
            msg = f"Invalid signal: token_id={token_id}, price={price}"
            self._logger.error(msg)
            return ExecutionResult(success=False, error=msg)

        # Extract asset for per-asset sizing
        asset = signal.get("asset", "")
        liq_conviction = signal.get("liq_conviction", 0.0)
        sig_meta = signal.get("metadata", {}) or {}

        # Weather signals use their own kelly-based sizing (not the bot's base_bet_pct)
        if sig_meta.get("is_weather"):
            kelly = signal.get("kelly_fraction", self._config.weather_base_bet_pct)
            spend = kelly * available_capital
            spend = max(
                self._config.weather_base_bet_pct * available_capital,
                min(spend, self._config.weather_max_bet_pct * available_capital),
            )
            spend = min(spend, self._config.weather_max_spend)  # hard cap — no min_bet floor (min_bet > cap)
            size = spend / price if price > 0 else 0.0
            self._logger.info(
                f"Weather sizing: kelly={kelly:.3f}, spend={spend:.2f}, "
                f"size={size:.2f} shares @ {price:.4f}"
            )
        else:
            # Calculate position size (3-layer sizing + asset multiplier + liquidation boost)
            size = self._calculate_size(price, available_capital, asset=asset, liq_conviction=liq_conviction, spread=signal.get("spread"), fear_greed=signal.get("fear_greed"), signal=signal)
        if size <= 0:
            msg = (
                f"Size calculation failed for {slug} @ {price}: "
                f"available_capital={available_capital}"
            )
            self._logger.warning(msg)
            return ExecutionResult(success=False, error=msg, reason="zero_size")

        # Determine entry mode based on config (maker saves ~1.3% taker fee)
        from .types import parse_window_from_slug
        window = parse_window_from_slug(slug)
        retry_candidate = self._is_btc5m_retry_candidate(signal, window)
        use_maker = self._config.entry_mode == "maker"
        price_tick = self._get_price_tick(signal)

        # Override: use taker on 5m markets (NOTE: 5m taker fees active since Feb 12 2026)
        if self._config.taker_on_5m and window <= 300:
            use_maker = False
            self._logger.info(
                f"5m taker override: fee-free market, using taker for instant fill | "
                f"slug={slug}"
            )

        # Resolution snipe: taker entry (no time for maker fill in last 8-45s)
        is_snipe = signal.get('source') == 'resolution_snipe' if signal else False
        use_fak = False
        if is_snipe:
            if self._config.snipe_entry_mode == "fak":
                use_fak = True
                use_maker = False
            else:
                use_maker = False

        # Oracle flip: FAK entry (cheap tokens, <44s remaining, speed is everything)
        is_oracle_flip = signal.get('source') == 'oracle_flip' if signal else False
        if is_oracle_flip:
            use_fak = True
            use_maker = False

        # Sharp move at 0.90+: taker entry (fee <0.20% at 0.90, near-zero above)
        is_sharp = signal.get('source') == 'sharp_move' if signal else False
        if is_sharp and price > 0.90:
            use_maker = False
            self._logger.info(
                f"Sharp move taker override: midpoint={price:.4f} > 0.90, "
                f"fee ~{0.25 * (price * (1 - price)) ** 2:.3%} | slug={slug}"
            )

        # Near-resolution pair arb: always taker (no time for maker in last 8-45s)
        is_near_res_pair = signal.get('near_resolution', False) if signal else False
        if is_near_res_pair:
            use_maker = False

        maker_offset_used = None  # Track for fill optimizer
        entry_mode_label = "fak" if use_fak else ("maker" if use_maker else "taker")
        self._logger.info(
            f"Entry mode: {entry_mode_label} | "
            f"window={window}s | slug={slug}"
        )

        # FAK path: instant fill of available liquidity, no polling needed
        if use_fak:
            dollar_amount = round(size * price, 2)
            # Use WS best_ask as price hint to skip SDK's calculate_market_price REST call
            ws_best_ask = signal.get('best_ask', 0.0) if signal else 0.0
            self._logger.info(
                f"FAK snipe: {slug} | ${dollar_amount:.2f} @ ~{price:.4f} "
                f"(ask={ws_best_ask or '?'}, {signal.get('time_remaining_secs', '?')}s left)"
            )
            placement_result = await self._clob.place_fak_order(
                token_id=token_id,
                amount=dollar_amount,
                side=BUY,
                price_hint=ws_best_ask or 0.0,
            )
            if not placement_result.success:
                self._logger.warning(f"FAK snipe failed for {slug}: {placement_result.error}")
                return placement_result
            order_id = placement_result.order_id
            self._logger.info(f"FAK order accepted: {order_id} | {slug}")

            # FAK fills instantly — fast poll to confirm shares settled
            _fill_start = time.time()
            fill_result = await self._poll_for_fill(
                order_id, token_id, price, size, max_polls=3,
                poll_interval=FAK_POLL_INTERVAL,
            )
            fill_result.fill_time_ms = int((time.time() - _fill_start) * 1000)
            if not fill_result.success:
                self._logger.warning(f"FAK fill not confirmed for {slug} (may be partial)")
                # Still return the placement — shares may have settled
                return placement_result
            return fill_result

        if use_maker:
            maker_override = sig_meta.get("maker_target_price")
            if maker_override:
                live_midpoint = signal.get("price", 0.0)
                buy_price = self._quantize_price(float(maker_override), price_tick, mode="down")
                self._logger.info(
                    f"Maker order override: target={float(maker_override):.4f}, "
                    f"tick={price_tick:.4f}, buy_price={buy_price:.4f}"
                )
            else:
                # Maker mode: place below midpoint to sit on the book (post-only)
                live_midpoint = await self._clob.get_midpoint(token_id)
                if live_midpoint <= 0:
                    msg = f"No midpoint for maker order: {slug}"
                    self._logger.warning(msg)
                    return ExecutionResult(success=False, error=msg)
                offset = self._config.maker_offset
                if self._fill_optimizer:
                    offset = self._fill_optimizer.select_offset()
                maker_offset_used = offset
                buy_price = self._quantize_price(live_midpoint - offset, price_tick, mode="down")
                self._logger.info(
                    f"Maker order: midpoint={live_midpoint:.4f}, "
                    f"offset={offset}, tick={price_tick:.4f}, buy_price={buy_price:.4f}"
                )
            placement_result = await self._clob.place_order(
                token_id=token_id,
                price=buy_price,
                size=size,
                side=BUY,
                post_only=True,
            )
        else:
            # Taker mode: smart slippage logic
            # Snipe: skip redundant midpoint fetch — signal.price IS the midpoint
            if is_snipe:
                live_midpoint = price
            else:
                live_midpoint = await self._clob.get_midpoint(token_id)
            base_price = max(price, live_midpoint) if live_midpoint > 0 else price
            if is_snipe:
                # Snipe: tight slippage — +$0.01 max, every cent matters at 0.90+
                buy_price = self._quantize_price(min(price + 0.01, 0.99), price_tick, mode="up")
            elif sig_meta.get("is_weather"):
                weather_slippage = max(price_tick * 2, 0.002)
                buy_price = self._quantize_price(
                    min(base_price + weather_slippage, price + weather_slippage, 0.99),
                    price_tick,
                    mode="up",
                )
            else:
                # +$0.02 on top of live price, but cap total overpay at $0.05 from signal
                buy_price = self._quantize_price(
                    min(base_price + 0.02, price + 0.05, 0.99),
                    price_tick,
                    mode="up",
                )
            self._logger.info(
                f"{'Snipe' if is_snipe else 'Smart'} slippage: signal={price}, "
                f"midpoint={live_midpoint:.4f}, tick={price_tick:.4f}, buy_price={buy_price} "
                f"(max overpay ${0.01 if is_snipe else 0.05})"
            )
            placement_result = await self._clob.place_order(
                token_id=token_id,
                price=buy_price,
                size=size,
                side=BUY,
            )

        # Update price to actual order price (not signal midpoint) for accurate P&L
        price = buy_price

        if not placement_result.success:
            if retry_candidate:
                self._entry_retry_stats["placement_failures"] += 1
            if retry_candidate and self._is_transient_execution_error(placement_result):
                retry_result = await self._maybe_retry_btc5m_entry(
                    stage="placement",
                    signal=signal,
                    token_id=token_id,
                    slug=slug,
                    signal_price=signal.get("price", price),
                    size=size,
                    attempts_allowed=int(getattr(self._config, "btc5m_entry_retry_max_placement_retries", 1)),
                )
                if retry_result is not None:
                    if retry_result.success:
                        placement_result = retry_result
                        order_id = retry_result.order_id
                        fill_result = retry_result
                        price = retry_result.fill_price or price
                        goto_share_verification = True
                    else:
                        self._logger.error(
                            f"Order placement failed for {slug}: {retry_result.error or retry_result.reason}"
                        )
                        return retry_result
                else:
                    self._logger.error(
                        f"Order placement failed for {slug}: {placement_result.error}"
                    )
                    return placement_result
            else:
                self._logger.error(
                    f"Order placement failed for {slug}: {placement_result.error}"
                )
                return placement_result

        if placement_result.success and placement_result.fill_size > 0:
            order_id = placement_result.order_id
            fill_result = placement_result
            goto_share_verification = True
        else:
            goto_share_verification = False

        if not goto_share_verification:
            self._logger.info(
                f"Order placed: {placement_result.order_id} | {slug} @ {price} x {size} shares"
            )
            order_id = placement_result.order_id

            # Poll for fill — maker gets longer timeout, taker gets standard
            poll_max = MAKER_POLL_MAX if use_maker else TAKER_POLL_MAX
            # Snipe: cap timeout at (time_remaining - 3s) to cancel before market close
            if is_snipe:
                secs_left = signal.get('time_remaining_secs', 15)
                snipe_polls = max(1, int(secs_left) - 3)  # 3s safety buffer
                poll_max = min(poll_max, snipe_polls)
                self._logger.info(f"Snipe fill timeout: {poll_max}s ({secs_left}s remaining, 3s buffer)")
            _fill_start = time.time()
            fill_result = await self._poll_for_fill(order_id, token_id, price, size, max_polls=poll_max)
            fill_result.fill_time_ms = int((time.time() - _fill_start) * 1000)

        # Record maker fill outcome to optimizer (before fallback)
        if self._fill_optimizer and maker_offset_used is not None:
            try:
                self._fill_optimizer.record_outcome(
                    offset=maker_offset_used,
                    filled=fill_result.success,
                    slug=slug,
                    asset=asset,
                    midpoint=live_midpoint,
                )
            except Exception as fo_err:
                self._logger.warning(f"Fill optimizer record failed: {fo_err}")

        if not fill_result.success:
            if retry_candidate and fill_result.reason == "timeout":
                self._entry_retry_stats["fill_timeouts"] += 1
            # Attempt cancel
            cancel_ok = await self._clob.cancel_order(order_id)
            if not cancel_ok:
                self._logger.warning(
                    f"cancel_order failed after {'snipe' if is_snipe else 'maker'} timeout "
                    f"(order_id={order_id})"
                )
            if is_snipe:
                self._logger.info(f"Snipe fill missed for {slug} — no retry near market close")
                return fill_result

            if retry_candidate and not use_maker and fill_result.reason == "timeout":
                retry_fill_result = await self._maybe_retry_btc5m_entry(
                    stage="fill",
                    signal=signal,
                    token_id=token_id,
                    slug=slug,
                    signal_price=signal.get("price", price),
                    size=size,
                    attempts_allowed=int(getattr(self._config, "btc5m_entry_retry_max_fill_retries", 1)),
                )
                if retry_fill_result is not None:
                    if retry_fill_result.success:
                        fill_result = retry_fill_result
                        order_id = retry_fill_result.order_id
                        price = retry_fill_result.fill_price or price
                        size = retry_fill_result.fill_size if retry_fill_result.fill_size > 0 else size
                    else:
                        self._logger.warning(
                            f"BTC 5m fill retry failed for {slug}: {retry_fill_result.reason or retry_fill_result.error}"
                        )
                        return retry_fill_result
                else:
                    return fill_result

            if fill_result.success:
                pass
            elif use_maker and fill_result.reason == "timeout":
                self._logger.info(
                    f"Maker timeout after {MAKER_POLL_MAX}s for {slug}, "
                    f"falling back to taker order"
                )
                live_midpoint = await self._clob.get_midpoint(token_id)
                if live_midpoint <= 0:
                    return fill_result  # Can't get midpoint, give up
                taker_price = round(min(live_midpoint + 0.02, 0.99), 2)
                fallback_max = (
                    self._config.sharp_move_max_entry_price
                    if is_sharp else self._config.max_entry_price
                )
                if taker_price > fallback_max:
                    self._logger.warning(
                        f"Taker fallback aborted: taker_price {taker_price:.4f} > "
                        f"max_entry_price {fallback_max} for {slug}"
                    )
                    return fill_result
                taker_result = await self._clob.place_order(
                    token_id=token_id,
                    price=taker_price,
                    size=size,
                    side=BUY,
                )
                if not taker_result.success:
                    self._logger.error(
                        f"Taker fallback placement failed: {taker_result.error}"
                    )
                    return taker_result
                order_id = taker_result.order_id
                self._logger.info(
                    f"Taker fallback placed: {order_id} | {slug} @ {taker_price}"
                )
                _fill_start = time.time()
                fill_result = await self._poll_for_fill(
                    order_id, token_id, taker_price, size, max_polls=TAKER_POLL_MAX
                )
                fill_result.fill_time_ms = int((time.time() - _fill_start) * 1000)
                if not fill_result.success:
                    await self._clob.cancel_order(order_id)
                    self._logger.warning(
                        f"Taker fallback also failed for {slug}: {fill_result.reason}"
                    )
                    return fill_result
                # CRITICAL: update price to actual taker fill price for position recording
                price = taker_price
            else:
                self._logger.warning(
                    f"Order {order_id} failed to fill: {fill_result.reason}"
                )
                return fill_result

        size = fill_result.fill_size if fill_result.fill_size > 0 else size

        # Verify shares settled on CLOB before adding to exit watch (Bug #41 fix)
        # After a taker BUY, CLOB takes 1-3s to reflect shares. Exit manager runs every 0.5s
        # and sees 0 shares → triggers stop_loss → SELL fails → force-closes as market_resolved.
        # Under high market stress (liquidation cascades), CLOB balance can lag 5-10s after MATCHED.
        # Initial 5s wait before polling prevents false failures during volatile periods.
        await asyncio.sleep(5)
        for _attempt in range(6):
            shares = await self._clob.get_share_balance(token_id)
            if shares >= MIN_SHARES_FOR_SELL:
                self._logger.info(
                    f"Share verification passed: {slug} {shares:.1f} shares settled on CLOB"
                )
                break
            self._logger.warning(
                f"Share settlement pending ({_attempt + 1}/6): {slug} — {shares:.1f} shares | "
                f"waiting 3s..."
            )
            await asyncio.sleep(3)
        else:
            # F5 fix: Don't add position with 0 verified shares — prevents Bug B2/B3 recurrence
            self._logger.error(
                f"Share verification FAILED for {slug} — {shares:.1f} shares after 18s "
                f"(need {MIN_SHARES_FOR_SELL}). NOT adding position."
            )
            return ExecutionResult(
                success=False,
                order_id=order_id,
                error=f"Share verification failed: {shares:.1f} shares after 18s",
                reason="shares_not_settled",
            )

        # Create position
        now_utc = datetime.now(timezone.utc)

        # Compute market_end_time — prefer signal override (for weather/non-epoch slugs)
        market_end_time = now_utc  # fallback
        if signal and signal.get("market_end_time_iso"):
            try:
                _s = str(signal["market_end_time_iso"]).replace("Z", "+00:00")
                market_end_time = datetime.fromisoformat(_s)
                if market_end_time.tzinfo is None:
                    market_end_time = market_end_time.replace(tzinfo=timezone.utc)
            except (ValueError, OSError):
                pass
        else:
            parts = slug.rsplit('-', 1)
            if len(parts) == 2 and parts[1].isdigit():
                from .types import parse_window_from_slug
                market_epoch = int(parts[1])
                market_end_time = datetime.fromtimestamp(market_epoch + parse_window_from_slug(slug), tz=timezone.utc)

        # Merge signal metadata (e.g. is_weather, weather_exit_price) into position metadata.
        # Signal-provided keys are set first so that standard fields take precedence on conflict.
        sig_meta = signal.get("metadata", {}) if signal else {}
        metadata = {
            **sig_meta,
            "market_title": market_title,
            "signal_usdc_size": usdc_size,
            "condition_id": signal.get("condition_id", "") if signal else "",
        }

        position = Position(
            token_id=token_id,
            slug=slug,
            entry_price=price,
            entry_size=size,
            entry_time=now_utc,
            entry_tx_hash=order_id,
            market_end_time=market_end_time,
            metadata=metadata,
            outcome=signal.get('outcome', '') if signal else '',
        )

        self._store.add(position)
        self._logger.info(
            f"Position created: {token_id} | {slug} @ {price} x {size} shares | "
            f"entry_time={now_utc.isoformat()}"
        )

        return ExecutionResult(
            success=True,
            order_id=order_id,
            fill_price=price,
            fill_size=size,
        )

    def _calculate_size(self, price: float, available_capital: float, asset: str = "", liq_conviction: float = 0.0, spread: Optional[float] = None, fear_greed: Optional[float] = None, signal: Optional[dict] = None) -> float:
        """Calculate position size using 3-layer sizing model + asset multiplier.

        Layer 1: Base percentage of available capital
        Layer 1b: Per-asset multiplier (ETH 1.2x, SOL 1.2x, BTC 1.0x)
        Layer 1c: Liquidation conviction boost (up to 50% when cascade aligns)
        Layer 2: Self-tuner multiplier (if enabled)
        Layer 3: Hard min/max bet bounds and deployment ratio cap

        Args:
            price: Entry price (0.0-1.0)
            available_capital: Available USDC
            asset: Asset name (e.g. "ETH", "SOL", "BTC") for per-asset sizing
            liq_conviction: Liquidation conviction 0.0-1.0 from regime detector

        Returns:
            Size in shares (or 0 if invalid)
        """
        if available_capital <= 0 or price <= 0:
            self._logger.warning(
                f"Invalid sizing inputs: available_capital={available_capital}, "
                f"price={price}"
            )
            return 0.0

        # Resolution snipe: flat sizing, skip all layers
        is_snipe = signal.get('source') == 'resolution_snipe' if signal else False
        if is_snipe:
            base_spend = available_capital * self._config.snipe_bet_pct
            base_spend = min(base_spend, self._config.snipe_max_bet)
            base_spend = max(base_spend, self._config.min_bet)
            size = base_spend / price
            self._logger.info(
                f"Snipe sizing: {self._config.snipe_bet_pct:.0%} of "
                f"${available_capital:.0f} = ${base_spend:.2f} / "
                f"{price:.4f} = {size:.1f} shares"
            )
            return max(0, size)

        # Oracle flip: dedicated max bet with auto-escalation
        is_oracle_flip = signal.get('source') == 'oracle_flip' if signal else False
        if is_oracle_flip:
            max_bet = self._config.oracle_flip_max_bet
            # Auto-escalation: raise bet after proven track record
            if (self._tracker
                    and self._config.oracle_flip_escalation_min_trades > 0):
                try:
                    stats = self._tracker.get_source_stats("oracle_flip")
                    if (stats["total"] >= self._config.oracle_flip_escalation_min_trades
                            and stats["wr"] >= self._config.oracle_flip_escalation_min_wr):
                        max_bet = self._config.oracle_flip_escalated_max_bet
                        self._logger.info(
                            f"Oracle flip ESCALATED: {stats['total']} trades, "
                            f"{stats['wr']:.0f}% WR -> max_bet=${max_bet:.0f}"
                        )
                except Exception:
                    pass  # fallback to default max_bet
            base_spend = min(available_capital, max_bet)
            if self._config.max_trade_amount > 0:
                base_spend = min(base_spend, self._config.max_trade_amount)
            if base_spend < self._config.min_bet:
                self._logger.warning(f"Oracle flip: insufficient capital ${base_spend:.2f} < min ${self._config.min_bet:.2f}")
                return 0.0
            size = base_spend / price
            multiplier = round((1.0 - price) / price, 1) if price > 0 else 0
            self._logger.info(
                f"Oracle flip sizing: ${base_spend:.2f} / "
                f"{price:.4f} = {size:.0f} shares | "
                f"multiplier={multiplier}x | potential=${size * (1.0 - price):.2f}"
            )
            return max(0, size)

        # Streak contrarian: dedicated sizing, skip all layers
        is_streak = signal.get('source') == 'streak_contrarian' if signal else False
        if is_streak:
            base_spend = available_capital * self._config.streak_contrarian_bet_pct
            base_spend = min(base_spend, self._config.streak_contrarian_max_bet)
            if self._config.max_trade_amount > 0:
                base_spend = min(base_spend, self._config.max_trade_amount)
            base_spend = max(base_spend, self._config.min_bet)
            size = base_spend / price
            self._logger.info(
                f"Streak contrarian sizing: ${base_spend:.2f} / "
                f"{price:.4f} = {size:.0f} shares"
            )
            return max(0, size)

        # Reversal short: dedicated sizing, skip all layers
        is_reversal_short = signal.get('source') == 'reversal_short' if signal else False
        if is_reversal_short:
            base_spend = min(available_capital, self._config.reversal_short_max_bet)
            if self._config.max_trade_amount > 0:
                base_spend = min(base_spend, self._config.max_trade_amount)
            base_spend = max(base_spend, self._config.min_bet)
            size = base_spend / price
            self._logger.info(
                f"Reversal short sizing: ${base_spend:.2f} / "
                f"{price:.4f} = {size:.0f} shares"
            )
            return max(0, size)

        # Near-resolution pair arb: flat sizing with hard $ cap per leg
        is_near_res = signal.get('near_resolution', False) if signal else False
        if is_near_res:
            base_spend = available_capital * self._config.pair_arb_near_res_bet_pct
            base_spend = min(base_spend, self._config.pair_arb_near_res_max_bet)
            base_spend = max(base_spend, self._config.min_bet)
            size = base_spend / price
            self._logger.info(
                f"Near-res pair arb sizing: {self._config.pair_arb_near_res_bet_pct:.0%} of "
                f"${available_capital:.0f} = ${base_spend:.2f} / "
                f"{price:.4f} = {size:.1f} shares"
            )
            return max(0, size)

        # Layer 1: Base bet as percentage of available capital
        hc_thresh = self._config.high_confidence_threshold
        hc_pct = self._config.high_confidence_bet_pct
        if hc_thresh > 0 and hc_pct > 0 and price >= hc_thresh:
            effective_pct = hc_pct
            self._logger.info(
                f"Layer 1 (high-conf): price={price:.2f}>={hc_thresh:.2f}, "
                f"using {hc_pct:.0%} instead of {self._config.base_bet_pct:.0%}"
            )
        else:
            if self._config.enable_kelly_sizing:
                kelly_f = self._compute_kelly(asset, price)
                if kelly_f is not None:
                    effective_pct = min(
                        kelly_f * self._config.kelly_fraction,
                        self._config.kelly_max_fraction,
                    )
                else:
                    effective_pct = self._config.base_bet_pct
            else:
                effective_pct = self._config.base_bet_pct
        base_spend = available_capital * effective_pct
        self._logger.debug(
            f"Layer 1 (base): available={available_capital:.2f}, "
            f"base_pct={effective_pct}, base_spend={base_spend:.2f}"
        )

        # Layer 1a: Spread scaling (wider spread = smaller position)
        if self._config.spread_size_scaling and spread is not None and spread > 0:
            if spread > self._config.spread_full_max:
                spread_mult = self._config.spread_reduced_size
                base_spend *= spread_mult
                self._logger.info(
                    f"Layer 1a (spread): spread={spread:.3f} > full_max={self._config.spread_full_max:.3f}, "
                    f"scaling to {spread_mult:.0%}: spend={base_spend:.2f}"
                )

        # Layer 1b: Per-asset multiplier
        if asset:
            asset_mult = self._config.get_asset_multiplier(asset)
            base_spend *= asset_mult
            self._logger.debug(
                f"Layer 1b (asset): asset={asset}, multiplier={asset_mult:.2f}, "
                f"after_asset={base_spend:.2f}"
            )

        # Layer 1c: Liquidation conviction boost
        # Defensive: up to 1.5x when cascade aligns (existing behavior)
        # Offensive: up to config multiplier when cascade volume exceeds threshold
        if liq_conviction > 0:
            liq_vol = signal.get("liq_volume_60s", 0.0) if signal else 0.0
            if (self._config.liq_cascade_boost_enabled
                    and liq_vol >= self._config.liq_cascade_boost_volume):
                # Offensive mode: large cascade aligned with our direction
                liq_boost = min(
                    1.0 + (liq_conviction * (self._config.liq_cascade_boost_multiplier - 1.0)),
                    self._config.liq_cascade_boost_multiplier,
                )
                self._logger.info(
                    f"Layer 1c (liq CASCADE BOOST): conviction={liq_conviction:.2f}, "
                    f"vol_60s=${liq_vol:,.0f}, boost={liq_boost:.2f}x, after_liq={base_spend * liq_boost:.2f}"
                )
            else:
                liq_boost = 1.0 + (liq_conviction * 0.5)  # max 1.5x (defensive default)
                self._logger.info(
                    f"Layer 1c (liq boost): conviction={liq_conviction:.2f}, "
                    f"boost={liq_boost:.2f}x, after_liq={base_spend * liq_boost:.2f}"
                )
            base_spend *= liq_boost

        # Layer 1d: Hour-of-day sizing multiplier (requires 200+ trades to calibrate)
        if self._config.hour_size_weights.strip():
            hour = datetime.now(timezone.utc).hour
            weights = {}
            for part in self._config.hour_size_weights.split(","):
                part = part.strip()
                if ":" in part:
                    try:
                        h, w = part.split(":", 1)
                        weights[int(h.strip())] = float(w.strip())
                    except ValueError:
                        pass
            hour_mult = weights.get(hour, 1.0)
            if hour_mult != 1.0:
                base_spend *= hour_mult
                self._logger.info(
                    f"Layer 1d (hour): hour={hour}UTC, mult={hour_mult:.2f}, "
                    f"after_hour={base_spend:.2f}"
                )

        # Layer 1e: Entry-price scaling (lower entry = bigger bet)
        if self._config.entry_price_scaling and price > 0:
            anchor = self._config.entry_price_anchor
            if anchor < 1.0:
                raw_mult = (1.0 - price) / (1.0 - anchor)
                ep_mult = max(
                    self._config.entry_price_scale_min,
                    min(raw_mult, self._config.entry_price_scale_max),
                )
                base_spend *= ep_mult
                self._logger.info(
                    f"Layer 1e (entry-price): price={price:.2f}, anchor={anchor:.2f}, "
                    f"mult={ep_mult:.2f}x, after_ep={base_spend:.2f}"
                )

        # Layer 1f: Whipsaw caution zone (reduce size in moderate chop)
        if self._config.whipsaw_max_ratio > 0:
            vol_1h = signal.get('volatility_1h') if signal else None
            trend_1h = signal.get('trend_1h') if signal else None
            if vol_1h is not None and trend_1h is not None and vol_1h >= self._config.whipsaw_min_vol:
                directionality = abs(trend_1h) / vol_1h
                if directionality < self._config.whipsaw_caution_ratio:
                    ws_mult = self._config.fg_caution_size_mult
                    base_spend *= ws_mult
                    self._logger.info(
                        f"Layer 1f (whipsaw caution): directionality={directionality:.3f} < 0.40, "
                        f"vol={vol_1h:.4f}, mult={ws_mult:.0%}, after_ws={base_spend:.2f}"
                    )

        # Layer 1g: Danger hours sizing (reduce size during loss-clustering hours)
        danger_hours = self._config.get_danger_hours()
        if danger_hours:
            hour_utc = datetime.now(timezone.utc).hour
            if hour_utc in danger_hours:
                dh_mult = self._config.danger_hours_size_mult
                base_spend *= dh_mult
                self._logger.info(
                    f"Layer 1g (danger hour): hour={hour_utc}UTC in {danger_hours}, "
                    f"mult={dh_mult:.0%}, after_dh={base_spend:.2f}"
                )

        # Layer 1h: Up direction reduction (Down is 20W/1L, Up is 19W/6L)
        if signal:
            outcome = str(signal.get("outcome", "") or signal.get("direction", ""))
            if outcome.lower() == "up" and self._config.up_direction_size_mult < 1.0:
                up_mult = self._config.up_direction_size_mult
                base_spend *= up_mult
                self._logger.info(
                    f"Layer 1h (up direction): outcome={outcome}, mult={up_mult:.0%}, "
                    f"after_up={base_spend:.2f}"
                )

        # Layer 1i: Volatility regime sizing (S1)
        # T0: calm (<0.5% vol) = 44% WR, moderate (0.5-1.0%) = 80% WR, elevated (>1.0%) = mixed
        if self._config.regime_sizing_enabled and signal:
            vol_1h = signal.get('volatility_1h')
            if vol_1h is not None and vol_1h > 0:
                if vol_1h < self._config.regime_cautious_max_vol:
                    regime_mult = self._config.regime_cautious_mult
                    regime_label = "cautious"
                elif vol_1h < self._config.regime_optimal_max_vol:
                    regime_mult = 1.0
                    regime_label = "optimal"
                else:
                    regime_mult = self._config.regime_elevated_mult
                    regime_label = "elevated"
                if regime_mult != 1.0:
                    base_spend *= regime_mult
                    self._logger.info(
                        f"Layer 1i (vol regime): vol_1h={vol_1h:.4f}, "
                        f"regime={regime_label}, mult={regime_mult:.0%}, "
                        f"after_regime={base_spend:.2f}"
                    )

        # Layer 1j: Sharp move bet multiplier (half-size during initial live phase)
        if signal and signal.get('source') == 'sharp_move':
            sm_mult = self._config.sharp_move_bet_multiplier
            if sm_mult != 1.0:
                base_spend *= sm_mult
                self._logger.info(
                    f"Layer 1j (sharp_move): mult={sm_mult:.2f}x, "
                    f"after_sm={base_spend:.2f}"
                )

        # Layer 1k: Ensemble bet sizing (BTC 5m signals only; non-BTC → neutral fallback)
        # CRITICAL: None score means non-BTC signal. Default "neutral" = 1.0x to avoid silently
        # halving ETH/SOL/XRP bets with zero justification. Only "cautious" opt-in reduces them.
        if self._config.ensemble_sizing_enabled and signal:
            ensemble_score = signal.get('ensemble_score')  # None for non-BTC signals
            if ensemble_score is None:
                ens_mult = 0.5 if self._config.ensemble_none_fallback == "cautious" else 1.0
            elif ensemble_score >= self._config.ensemble_high_threshold:
                ens_mult = self._config.ensemble_high_mult
            elif ensemble_score < self._config.ensemble_low_threshold:
                ens_mult = self._config.ensemble_low_mult
            else:
                ens_mult = 1.0  # neutral band: 0.40 <= score < 0.80
            label = "[DRY] " if self._config.ensemble_sizing_dry_run else ""
            if ens_mult != 1.0 or self._config.ensemble_sizing_dry_run:
                self._logger.info(
                    f"{label}Layer 1k (ensemble): score={ensemble_score}, "
                    f"mult={ens_mult:.2f}x, before={base_spend:.2f}, "
                    f"after={base_spend * ens_mult:.2f}"
                )
            if not self._config.ensemble_sizing_dry_run:
                base_spend *= ens_mult

        # Layer 2: Tuner multiplier
        spend = base_spend
        tuner_multiplier = 1.0
        if self._tuner is not None:
            tuner_multiplier = self._tuner.get_multiplier(price)
            tuner_multiplier = max(
                self._config.risk_multiplier_min,
                min(tuner_multiplier, self._config.risk_multiplier_max),
            )
            spend = base_spend * tuner_multiplier
            self._logger.debug(
                f"Layer 2 (tuner): multiplier={tuner_multiplier:.2f}, "
                f"after_tuner={spend:.2f}"
            )
        else:
            self._logger.debug("Layer 2 (tuner): disabled")

        # Layer 3: Hard bounds (min/max bet)
        # Auto-scale max_bet: 5% of capital, floored at config max_bet, capped at $6,500 (liquidity)
        if self._config.auto_max_bet:
            effective_max = max(self._config.max_bet, min(available_capital * self._config.auto_max_bet_pct, self._config.auto_max_bet_cap))
        else:
            effective_max = self._config.max_bet
        spend = max(self._config.min_bet, min(spend, effective_max))
        self._logger.debug(
            f"Layer 3a (hard bounds): min={self._config.min_bet:.2f}, "
            f"max={effective_max:.2f}, after_bounds={spend:.2f}"
        )

        # Layer 3 continued: Deployment ratio cap
        max_deployment = available_capital * self._config.max_deployment_ratio
        spend = min(spend, max_deployment)
        self._logger.debug(
            f"Layer 3b (deployment ratio): max_ratio={self._config.max_deployment_ratio}, "
            f"max_deployment={max_deployment:.2f}, final_spend={spend:.2f}"
        )

        # Layer 3c: Absolute $ cap per trade (hard ceiling regardless of sizing)
        if self._config.max_trade_amount > 0:
            if spend > self._config.max_trade_amount:
                self._logger.info(
                    f"Layer 3c (max_trade_amount): spend={spend:.2f} > cap={self._config.max_trade_amount:.2f}, "
                    f"capping to ${self._config.max_trade_amount:.2f}"
                )
                spend = self._config.max_trade_amount

        # Convert to shares and enforce minimum
        size = spend / price
        size = max(size, MIN_SHARES_FOR_SELL)

        self._logger.info(
            f"Size calculation complete: price={price}, spend={spend:.2f}, "
            f"size={size:.2f} shares"
        )

        return size

    def _compute_kelly(self, asset: str, price: float) -> Optional[float]:
        """Kelly fraction: f* = (p*b - (1-p)) / b

        p = WR from performance.db for (asset, price bucket)
        b = (1/price) - 1  (net odds per share)
        Returns None if fewer than kelly_min_trades in bucket (falls back to flat bet).
        """
        if not self._performance_db:
            return None
        bucket = round(price * 10) / 10
        cache_key = (asset.upper(), bucket)
        now = time.time()
        cached = self._kelly_cache.get(cache_key)
        if cached and (now - cached[2]) < 1800:
            wr, n = cached[0], cached[1]
        else:
            try:
                wr, n = self._performance_db.get_wr_for_bucket(asset, bucket)
                self._kelly_cache[cache_key] = (wr, n, now)
            except Exception as e:
                self._logger.debug(f"Kelly DB query failed: {e}")
                return None
        if n < self._config.kelly_min_trades:
            self._logger.debug(
                f"Kelly fallback to flat: n={n} < min={self._config.kelly_min_trades} "
                f"for {asset}@{bucket:.1f}"
            )
            return None
        b = (1.0 / price) - 1.0
        if b <= 0:
            return None
        f = (wr * b - (1.0 - wr)) / b
        kelly_f = max(0.0, f)
        self._logger.info(
            f"kelly_fraction={kelly_f:.3f} (WR={wr:.2f}, n={n}, entry={price:.2f})"
        )
        return kelly_f

    async def _async_confirm_fak(
        self, order_id: str, token_id: str, price: float, size: float, slug: str,
    ) -> None:
        """Background FAK fill confirmation for oracle flips (fire-and-forget)."""
        try:
            result = await self._poll_for_fill(
                order_id, token_id, price, size, max_polls=3,
                poll_interval=FAK_POLL_INTERVAL,
            )
            if result.success:
                self._logger.info(
                    f"FAK async confirm OK: {slug} | {result.fill_size:.1f} shares"
                )
            else:
                self._logger.warning(
                    f"FAK async confirm: {slug} not fully confirmed (may be partial)"
                )
        except Exception as e:
            self._logger.warning(f"FAK async confirm error for {slug}: {e}")

    async def _poll_for_fill(
        self,
        order_id: str,
        token_id: str,
        price: float,
        size: float,
        max_polls: int = TAKER_POLL_MAX,
        poll_interval: float = ORDER_POLL_INTERVAL,
    ) -> ExecutionResult:
        """Poll for order fill status, capturing partial fills on timeout.

        Args:
            order_id: Order ID from placement
            token_id: Token ID for the position
            price: Entry price
            size: Entry size
            max_polls: Maximum number of polls (default TAKER_POLL_MAX=10)
            poll_interval: Seconds between polls (default ORDER_POLL_INTERVAL=1s)

        Returns:
            ExecutionResult with success or timeout reason.
            On timeout, checks size_matched to capture partial fills.
        """
        for poll_num in range(1, max_polls + 1):
            await asyncio.sleep(poll_interval)

            details = await self._clob.get_order_details(order_id)
            if details is None:
                self._logger.debug(
                    f"Order {order_id} poll {poll_num}/{max_polls}: API error"
                )
                continue

            status = details["status"]
            size_matched = details["size_matched"]
            self._logger.debug(
                f"Order {order_id} poll {poll_num}/{max_polls}: "
                f"{status} (filled {size_matched:.1f}/{size:.1f})"
            )

            if status == OrderStatus.FILLED or status == OrderStatus.MATCHED:
                actual_size = size_matched if size_matched > 0 else size
                self._logger.info(
                    f"Order {order_id} filled (status={status}) after "
                    f"{poll_num * ORDER_POLL_INTERVAL}s | "
                    f"{actual_size:.1f} shares"
                )
                return ExecutionResult(
                    success=True,
                    order_id=order_id,
                    fill_price=price,
                    fill_size=actual_size,
                )

            # Continue polling on LIVE or ERROR
            if status not in [OrderStatus.LIVE, "ERROR"]:
                # Hit CANCELLED or FAILED — but check for partial fill first
                if size_matched >= MIN_SHARES_FOR_SELL:
                    self._logger.info(
                        f"Order {order_id} hit {status} but has partial fill: "
                        f"{size_matched:.1f}/{size:.1f} shares — recording"
                    )
                    return ExecutionResult(
                        success=True,
                        order_id=order_id,
                        fill_price=price,
                        fill_size=size_matched,
                    )
                self._logger.warning(
                    f"Order {order_id} hit terminal status: {status}"
                )
                return ExecutionResult(
                    success=False,
                    order_id=order_id,
                    error=f"Terminal status: {status}",
                    reason=status.lower(),
                )

        # Timeout — check for partial fill before giving up
        total_wait = max_polls * poll_interval
        details = await self._clob.get_order_details(order_id)
        size_matched = details.get("size_matched", 0) if details else 0

        if size_matched >= MIN_SHARES_FOR_SELL:
            # Cancel unfilled remainder, keep partial fill
            await self._clob.cancel_order(order_id)
            self._logger.info(
                f"Partial fill captured for {order_id}: "
                f"{size_matched:.1f}/{size:.1f} shares after {total_wait}s timeout"
            )
            return ExecutionResult(
                success=True,
                order_id=order_id,
                fill_price=price,
                fill_size=size_matched,
            )

        self._logger.warning(
            f"Order {order_id} failed to fill within {total_wait}s "
            f"({max_polls} polls) | size_matched={size_matched:.1f}"
        )
        return ExecutionResult(
            success=False,
            order_id=order_id,
            error=f"Fill timeout after {total_wait}s",
            reason="timeout",
        )
