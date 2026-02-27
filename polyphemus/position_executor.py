"""PositionExecutor — BUY order execution with 3-layer position sizing.

This module handles order placement, fill verification, and position creation.
All operations are async and include proper timeout handling via ClobWrapper.
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

from py_clob_client.order_builder.constants import BUY

from .types import (
    Position,
    ExecutionResult,
    OrderStatus,
    ORDER_POLL_INTERVAL,
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
        self._kelly_cache: dict = {}  # (asset, bucket) -> (wr, n, fetched_at)

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
        use_maker = self._config.entry_mode == "maker"

        # Override: use taker on 5m markets (NOTE: 5m taker fees active since Feb 12 2026)
        if self._config.taker_on_5m and window <= 300:
            use_maker = False
            self._logger.info(
                f"5m taker override: fee-free market, using taker for instant fill | "
                f"slug={slug}"
            )

        # Resolution snipe: always taker (no time for maker fill in last 8-45s)
        is_snipe = signal.get('source') == 'resolution_snipe' if signal else False
        if is_snipe:
            use_maker = False

        maker_offset_used = None  # Track for fill optimizer
        self._logger.info(
            f"Entry mode: {'maker' if use_maker else 'taker'} | "
            f"window={window}s | slug={slug}"
        )
        if use_maker:
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
            buy_price = round(live_midpoint - offset, 2)
            buy_price = max(buy_price, 0.01)  # floor
            self._logger.info(
                f"Maker order: midpoint={live_midpoint:.4f}, "
                f"offset={offset}, buy_price={buy_price}"
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
                buy_price = round(min(price + 0.01, 0.99), 2)
            else:
                # +$0.02 on top of live price, but cap total overpay at $0.05 from signal
                buy_price = round(min(base_price + 0.02, price + 0.05, 0.99), 2)
            self._logger.info(
                f"{'Snipe' if is_snipe else 'Smart'} slippage: signal={price}, "
                f"midpoint={live_midpoint:.4f}, buy_price={buy_price} "
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
            self._logger.error(
                f"Order placement failed for {slug}: {placement_result.error}"
            )
            return placement_result

        order_id = placement_result.order_id
        self._logger.info(
            f"Order placed: {order_id} | {slug} @ {price} x {size} shares"
        )

        # Poll for fill — maker gets longer timeout, taker gets standard
        poll_max = MAKER_POLL_MAX if use_maker else TAKER_POLL_MAX
        # Snipe: cap timeout at (time_remaining - 3s) to cancel before market close
        if is_snipe:
            secs_left = signal.get('time_remaining_secs', 15)
            snipe_polls = max(1, int(secs_left) - 3)  # 3s safety buffer
            poll_max = min(poll_max, snipe_polls)
            self._logger.info(f"Snipe fill timeout: {poll_max}s ({secs_left}s remaining, 3s buffer)")
        fill_result = await self._poll_for_fill(order_id, token_id, price, size, max_polls=poll_max)

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

            # Hybrid fallback: if maker timed out, retry as taker
            if use_maker and fill_result.reason == "timeout":
                self._logger.info(
                    f"Maker timeout after {MAKER_POLL_MAX}s for {slug}, "
                    f"falling back to taker order"
                )
                live_midpoint = await self._clob.get_midpoint(token_id)
                if live_midpoint <= 0:
                    return fill_result  # Can't get midpoint, give up
                if live_midpoint > self._config.max_entry_price:
                    self._logger.warning(
                        f"Taker fallback aborted: midpoint {live_midpoint:.4f} > "
                        f"max_entry_price {self._config.max_entry_price} for {slug}"
                    )
                    return fill_result
                taker_price = round(min(live_midpoint + 0.02, 0.99), 2)
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
                fill_result = await self._poll_for_fill(
                    order_id, token_id, taker_price, size, max_polls=TAKER_POLL_MAX
                )
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

        # Layer 1c: Liquidation conviction boost (up to 50% extra)
        if liq_conviction > 0:
            liq_boost = 1.0 + (liq_conviction * 0.5)  # max 1.5x
            base_spend *= liq_boost
            self._logger.info(
                f"Layer 1c (liq boost): conviction={liq_conviction:.2f}, "
                f"boost={liq_boost:.2f}x, after_liq={base_spend:.2f}"
            )

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

    async def _poll_for_fill(
        self,
        order_id: str,
        token_id: str,
        price: float,
        size: float,
        max_polls: int = TAKER_POLL_MAX,
    ) -> ExecutionResult:
        """Poll for order fill status, capturing partial fills on timeout.

        Args:
            order_id: Order ID from placement
            token_id: Token ID for the position
            price: Entry price
            size: Entry size
            max_polls: Maximum number of polls (default TAKER_POLL_MAX=10)

        Returns:
            ExecutionResult with success or timeout reason.
            On timeout, checks size_matched to capture partial fills.
        """
        for poll_num in range(1, max_polls + 1):
            await asyncio.sleep(ORDER_POLL_INTERVAL)

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
        total_wait = max_polls * ORDER_POLL_INTERVAL
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
