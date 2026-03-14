"""ExitHandler — SELL order execution for position exits.

This module executes SELL orders to exit positions. It follows the execute-then-record
pattern: execute SELL, verify fill, return result. Does NOT record to DB.

All operations are async and include proper timeout handling via ClobWrapper.
"""

import asyncio
import logging
from typing import Optional

from py_clob_client.order_builder.constants import SELL

from .types import (
    Position,
    ExitSignal,
    ExitReason,
    ExecutionResult,
    OrderStatus,
    ORDER_POLL_INTERVAL,
    TAKER_POLL_MAX,
    MIN_SHARES_FOR_SELL,
)
from .config import setup_logger
from .clob_wrapper import ClobWrapper


class ExitHandler:
    """Handles SELL order execution for position exits with fill verification."""

    def __init__(self, clob: ClobWrapper, config=None):
        """Initialize exit handler.

        Args:
            clob: ClobWrapper instance for CLOB interactions
            config: Optional Settings for maker exit feature flag
        """
        self._clob = clob
        self._config = config
        self._logger = setup_logger("polyphemus.exit_handler")

    async def execute_exit(
        self,
        pos: Position,
        exit_signal: ExitSignal,
    ) -> ExecutionResult:
        """Execute a SELL order to exit a position.

        Follows execute-then-record pattern:
        1. Check exit reason (market_resolved skips SELL)
        2. Verify share balance
        3. Place SELL order
        4. Poll for fill
        5. Return result (does NOT record to DB)

        Args:
            pos: Position to exit
            exit_signal: ExitSignal with reason and optional exit_price

        Returns:
            ExecutionResult with success status, order_id, fill details, or error
        """
        slug = pos.slug
        token_id = pos.token_id

        # =====================================================================
        # Step a: Check if market is resolved
        # =====================================================================
        if exit_signal.reason == ExitReason.MARKET_RESOLVED.value:
            self._logger.info(
                f"Market resolved for {slug}: skipping SELL (shares redeemed "
                f"via poly-web3)"
            )
            # Return last known price (not 0.0!) for P&L recording (Bug #35 fix)
            resolved_price = pos.current_price if pos.current_price > 0 else pos.entry_price
            return ExecutionResult(
                success=True,
                fill_price=resolved_price,
                fill_size=pos.entry_size,
                reason="market_resolved",
            )

        # =====================================================================
        # Step b: Check share balance
        # =====================================================================
        shares = await self._clob.get_share_balance(token_id)
        if shares < MIN_SHARES_FOR_SELL:
            msg = f"Insufficient shares for SELL: {slug} has {shares:.2f} shares (min: {MIN_SHARES_FOR_SELL})"
            self._logger.warning(msg)
            return ExecutionResult(
                success=False,
                error="insufficient_shares",
                reason=f"only {shares:.2f} shares",
            )

        # =====================================================================
        # Step c: Determine exit price
        # =====================================================================
        exit_price = exit_signal.exit_price
        if exit_price is None or exit_price <= 0:
            # Fall back to current price, then entry price
            if pos.current_price > 0:
                exit_price = pos.current_price
            else:
                exit_price = pos.entry_price

        # Check time remaining for urgency-based pricing
        from datetime import datetime, timezone
        is_time_exit = exit_signal.reason in (
            ExitReason.TIME_EXIT.value,
            ExitReason.MAX_HOLD.value,
        )
        near_expiry = False
        if pos.market_end_time:
            secs_left = (pos.market_end_time - datetime.now(timezone.utc)).total_seconds()
            near_expiry = secs_left < 420  # < 7 min left
        # Aggressive pricing ONLY for time-based exits (time_exit, max_hold)
        # Stop_loss and profit_target use normal $0.02 discount regardless of expiry
        urgent = is_time_exit and near_expiry
        discount = 0.05 if urgent else 0.02
        sell_price = max(0.01, exit_price - discount)
        self._logger.debug(
            f"Exit price strategy: signal={exit_signal.exit_price}, "
            f"current={pos.current_price}, entry={pos.entry_price}, "
            f"using_exit_price={exit_price:.4f}, sell_price={sell_price:.4f}, "
            f"discount={discount} ({'urgent' if urgent else 'normal'})"
        )

        # =====================================================================
        # Step c2: Try maker exit for profit_target (zero fee + rebates)
        # =====================================================================
        use_maker = (
            exit_signal.reason == ExitReason.PROFIT_TARGET.value
            and not near_expiry  # Skip maker when < 7 min left — no time to wait
            and self._config is not None
            and getattr(self._config, 'maker_exit_enabled', False)
            and getattr(self._config, 'maker_exit_timeout_polls', 0) > 0
        )

        if use_maker:
            # Maker SELL: undercut ask by placing below midpoint
            # This becomes best ask, attracting taker buyers → zero fee
            maker_price = max(0.01, exit_price - 0.005)
            self._logger.info(
                f"Trying maker SELL for {slug}: {maker_price:.4f} "
                f"(midpoint={exit_price:.4f})"
            )
            maker_result = await self._clob.place_order(
                token_id=token_id,
                price=maker_price,
                size=shares,
                side=SELL,
                post_only=True,
            )
            if maker_result.success and maker_result.order_id:
                maker_fill = await self._poll_for_fill(
                    order_id=maker_result.order_id,
                    slug=slug,
                    sell_price=maker_price,
                    shares=shares,
                    exit_reason=exit_signal.reason,
                    max_polls=self._config.maker_exit_timeout_polls,
                )
                if maker_fill.success:
                    self._logger.info(
                        f"Maker SELL filled for {slug} @ {maker_price:.4f} (zero fee!)"
                    )
                    return maker_fill
                # Maker didn't fill — cancel and fall through to taker
                await self._clob.cancel_order(maker_result.order_id)
                self._logger.info(
                    f"Maker SELL timeout for {slug}, falling back to taker"
                )
            elif maker_result.success:
                self._logger.warning(f"Maker SELL placed but no order_id for {slug}")
            else:
                self._logger.debug(
                    f"Maker SELL rejected for {slug}: {maker_result.error} "
                    f"(falling back to taker)"
                )

        # =====================================================================
        # Step d: Place SELL order (taker — guaranteed fill)
        # =====================================================================
        placement_result = await self._clob.place_order(
            token_id=token_id,
            price=sell_price,
            size=shares,
            side=SELL,
        )

        if not placement_result.success:
            error_msg = placement_result.error
            # Check if market no longer exists (resolved or cancelled)
            if "does not exist" in error_msg.lower() or "orderbook" in error_msg.lower():
                self._logger.info(
                    f"Market appears resolved: {slug} | error: {error_msg}"
                )
                # Return last known price (not 0.0!) for P&L recording (Bug #35 fix)
                resolved_price = pos.current_price if pos.current_price > 0 else pos.entry_price
                return ExecutionResult(
                    success=True,
                    fill_price=resolved_price,
                    fill_size=pos.entry_size,
                    reason="market_resolved",
                )
            else:
                self._logger.error(
                    f"SELL order placement failed for {slug}: {error_msg}"
                )
                return ExecutionResult(success=False, error=error_msg)

        order_id = placement_result.order_id
        self._logger.info(
            f"SELL order placed: {order_id} | {slug} @ {sell_price:.4f} x {shares:.2f} shares"
        )

        # =====================================================================
        # Step e: Poll for fill
        # =====================================================================
        fill_result = await self._poll_for_fill(
            order_id=order_id,
            slug=slug,
            sell_price=sell_price,
            shares=shares,
            exit_reason=exit_signal.reason,
        )

        if fill_result.success:
            return fill_result

        # =====================================================================
        # Step f: SELL retry — if first attempt timed out, retry with deeper cut
        # =====================================================================
        if fill_result.reason == "timeout":
            # Cancel the stale order (already done in _poll_for_fill)
            # Retry with much more aggressive pricing
            retry_price = max(0.01, exit_price - 0.10)  # 10-cent discount
            self._logger.info(
                f"SELL retry for {slug}: first attempt timed out, "
                f"retrying at {retry_price:.4f} (was {sell_price:.4f})"
            )
            retry_result = await self._clob.place_order(
                token_id=token_id,
                price=retry_price,
                size=shares,
                side=SELL,
            )
            if not retry_result.success:
                error_msg = retry_result.error
                if "does not exist" in error_msg.lower() or "orderbook" in error_msg.lower():
                    self._logger.info(
                        f"Market resolved during SELL retry: {slug}"
                    )
                    resolved_price = pos.current_price if pos.current_price > 0 else pos.entry_price
                    return ExecutionResult(
                        success=True,
                        fill_price=resolved_price,
                        fill_size=pos.entry_size,
                        reason="market_resolved",
                    )
                self._logger.error(f"SELL retry placement failed: {error_msg}")
                return ExecutionResult(success=False, error=error_msg)

            retry_order_id = retry_result.order_id
            retry_fill = await self._poll_for_fill(
                order_id=retry_order_id,
                slug=slug,
                sell_price=retry_price,
                shares=shares,
                exit_reason=exit_signal.reason,
            )
            return retry_fill

        return fill_result

    async def _poll_for_fill(
        self,
        order_id: str,
        slug: str,
        sell_price: float,
        shares: float,
        exit_reason: str,
        max_polls: int = TAKER_POLL_MAX,
    ) -> ExecutionResult:
        """Poll for SELL order fill status, capturing partial fills on timeout.

        Args:
            order_id: Order ID from placement
            slug: Market slug for logging
            sell_price: SELL price
            shares: Size in shares
            exit_reason: Exit reason from signal
            max_polls: Max poll attempts before timeout (default TAKER_POLL_MAX)

        Returns:
            ExecutionResult with success or timeout reason.
            On timeout, checks size_matched to capture partial fills.
        """
        for poll_num in range(1, max_polls + 1):
            await asyncio.sleep(ORDER_POLL_INTERVAL)

            details = await self._clob.get_order_details(order_id)
            if details is None:
                self._logger.debug(
                    f"SELL order {order_id} ({slug}) poll {poll_num}/{max_polls}: API error"
                )
                continue

            status = details["status"]
            size_matched = details["size_matched"]
            self._logger.debug(
                f"SELL order {order_id} ({slug}) poll {poll_num}/{max_polls}: "
                f"{status} (filled {size_matched:.1f}/{shares:.1f})"
            )

            if status == OrderStatus.FILLED or status == OrderStatus.MATCHED:
                actual_size = size_matched if size_matched > 0 else shares
                self._logger.info(
                    f"SELL order {order_id} ({slug}) filled (status={status}) after "
                    f"{poll_num * ORDER_POLL_INTERVAL}s | {actual_size:.2f} @ {sell_price:.4f}"
                )
                return ExecutionResult(
                    success=True,
                    order_id=order_id,
                    fill_price=sell_price,
                    fill_size=actual_size,
                    reason=exit_reason,
                )

            # Continue polling on LIVE or ERROR
            if status not in [OrderStatus.LIVE, "ERROR"]:
                # Hit CANCELLED or FAILED — check for partial fill
                if size_matched >= MIN_SHARES_FOR_SELL:
                    self._logger.info(
                        f"SELL order {order_id} ({slug}) hit {status} but has partial fill: "
                        f"{size_matched:.1f}/{shares:.1f} shares — recording"
                    )
                    return ExecutionResult(
                        success=True,
                        order_id=order_id,
                        fill_price=sell_price,
                        fill_size=size_matched,
                        reason=exit_reason,
                    )
                self._logger.warning(
                    f"SELL order {order_id} ({slug}) hit terminal status: {status}"
                )
                return ExecutionResult(
                    success=False,
                    order_id=order_id,
                    error=f"Terminal status: {status}",
                    reason=status.lower(),
                )

        # =====================================================================
        # Timeout — check for partial fill before giving up
        # =====================================================================
        total_wait = max_polls * ORDER_POLL_INTERVAL
        details = await self._clob.get_order_details(order_id)
        size_matched = details["size_matched"] if details else 0

        if size_matched >= MIN_SHARES_FOR_SELL:
            # Cancel unfilled remainder, keep partial fill
            await self._clob.cancel_order(order_id)
            self._logger.info(
                f"SELL partial fill captured for {order_id} ({slug}): "
                f"{size_matched:.1f}/{shares:.1f} shares after {total_wait}s timeout"
            )
            return ExecutionResult(
                success=True,
                order_id=order_id,
                fill_price=sell_price,
                fill_size=size_matched,
                reason=exit_reason,
            )

        self._logger.error(
            f"SELL order {order_id} ({slug}) failed to fill within {total_wait}s "
            f"({max_polls} polls) | size_matched={size_matched:.1f} | Attempting cancel..."
        )

        cancel_ok = await self._clob.cancel_order(order_id)
        if cancel_ok:
            self._logger.info(f"SELL order {order_id} cancelled successfully")
        else:
            self._logger.warning(f"SELL order {order_id} cancel failed")

        return ExecutionResult(
            success=False,
            order_id=order_id,
            error=f"Fill timeout after {total_wait}s",
            reason="timeout",
        )
