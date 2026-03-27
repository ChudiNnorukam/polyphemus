"""EpochAccumulator - ugag-style continuous buying throughout an epoch.

Instead of one entry per signal, accumulates a position with multiple
small buys every N seconds as direction confirms. Holds to resolution.

Price-gated accumulation (v2): each round only fires if the current token
price has dropped by at least accum_min_price_drop_pct from the previous
fill price. This ensures we average DOWN (cheaper/more certain outcome),
not UP (into a worsening position).

Why ugag's accumulator works:
  - He buys as the token gets CHEAPER, not on a clock
  - Each successive buy is at a lower price (higher certainty of outcome)
  - Near resolution he's buying at $0.01-$0.02 (98-99% certain)
  - We were averaging UP (round 2 fill > round 1 fill) because we bought
    on a timer regardless of price direction

Pattern observed from ugag ($19K profit in 20 days):
  - 10-27 buys per epoch, spread across 46-194 seconds
  - Median 4s between buys
  - Buys at $0.01-$0.70 (wider range as certainty increases)
  - One direction per epoch (pure directional conviction)
  - Holds to resolution (no early exit)

Usage:
    accum = EpochAccumulator(config, executor, momentum_feed, market_ws=ws)
    await accum.start_accumulation(slug, direction, token_id, epoch_end)
"""

import asyncio
import logging
import time
from typing import Optional


class EpochAccumulator:
    """Accumulates a position throughout an epoch with multiple small buys.

    Rounds are price-gated: each buy only fires if the token midpoint has
    dropped by at least accum_min_price_drop_pct from the previous fill.
    If market_ws is unavailable, falls back to time-only behavior.
    """

    def __init__(self, config, executor, momentum_feed=None, tracker=None, market_ws=None):
        self._config = config
        self._executor = executor
        self._momentum_feed = momentum_feed
        self._tracker = tracker
        self._market_ws = market_ws
        self._logger = logging.getLogger("polyphemus.accumulator")
        # Track active accumulations: slug -> task
        self._active: dict = {}

    async def start_accumulation(
        self,
        slug: str,
        direction: str,
        token_id: str,
        asset: str,
        epoch_end: float,
        initial_price: float,
        market_window: int = 300,
    ) -> None:
        """Start accumulating a position for this epoch.

        Args:
            slug: Market slug (e.g., btc-updown-5m-1774373400)
            direction: "up" or "down"
            token_id: Polymarket token ID for the chosen side
            asset: "BTC", "ETH", etc.
            epoch_end: Unix timestamp when epoch resolves
            initial_price: Midpoint at first signal
            market_window: Epoch duration in seconds (300 or 900)
        """
        if slug in self._active:
            return  # already accumulating this epoch

        bet_per_round = getattr(self._config, 'accum_bet_per_round', 1.0)
        interval = getattr(self._config, 'accum_interval_secs', 15)
        max_rounds = getattr(self._config, 'accum_max_rounds', 10)
        min_secs_before_end = getattr(self._config, 'accum_stop_before_end_secs', 30)
        reversal_pct = getattr(self._config, 'accum_reversal_pct', 0.002)
        # Minimum price drop required to trigger each successive round.
        # 0.01 = token must be 1% cheaper than previous fill to buy again.
        # Set to 0.0 to disable price gate (time-only, old behavior).
        min_price_drop_pct = getattr(self._config, 'accum_min_price_drop_pct', 0.01)

        self._logger.info(
            f"ACCUM START | {slug} {direction} | "
            f"bet=${bet_per_round}/round | interval={interval}s | "
            f"max={max_rounds} rounds | min_drop={min_price_drop_pct:.1%} | "
            f"token={token_id[:10]}..."
        )

        task = asyncio.create_task(
            self._accumulation_loop(
                slug, direction, token_id, asset, epoch_end,
                initial_price, bet_per_round, interval,
                max_rounds, min_secs_before_end, reversal_pct,
                min_price_drop_pct,
            )
        )
        self._active[slug] = task

        # Cleanup when done
        task.add_done_callback(lambda t: self._active.pop(slug, None))

    def _get_token_midpoint(self, token_id: str) -> Optional[float]:
        """Get current token midpoint from market_ws. Returns None if unavailable."""
        if not self._market_ws:
            return None
        try:
            mid = self._market_ws.get_midpoint(token_id)
            return float(mid) if mid and mid > 0 else None
        except Exception:
            return None

    async def _accumulation_loop(
        self,
        slug: str,
        direction: str,
        token_id: str,
        asset: str,
        epoch_end: float,
        initial_price: float,
        bet_per_round: float,
        interval: int,
        max_rounds: int,
        min_secs_before_end: int,
        reversal_pct: float = 0.002,
        min_price_drop_pct: float = 0.01,
    ) -> None:
        """Core accumulation loop. Price-gated: only buys when token is cheaper."""
        rounds = 0
        total_spent = 0.0
        total_shares = 0.0
        last_fill_price: Optional[float] = None  # price gate reference

        for round_num in range(1, max_rounds + 1):
            secs_left = epoch_end - time.time()

            # Stop if too close to epoch end
            if secs_left < min_secs_before_end:
                self._logger.info(
                    f"ACCUM STOP | {slug} | {secs_left:.0f}s left | "
                    f"{rounds} rounds, ${total_spent:.2f} spent, {total_shares:.1f} shares"
                )
                break

            # Check if direction still holds (Binance price)
            if self._momentum_feed and round_num > 1:
                binance_price = self._momentum_feed.get_latest_price(asset)
                entry_binance = getattr(self, f'_entry_binance_{slug}', 0)
                if binance_price and entry_binance and entry_binance > 0:
                    move = (binance_price - entry_binance) / entry_binance
                    reversed_ = (
                        (direction.lower() == "up" and move < -reversal_pct) or
                        (direction.lower() == "down" and move > reversal_pct)
                    )
                    if reversed_:
                        self._logger.warning(
                            f"ACCUM REVERSED | {slug} {direction} | "
                            f"binance moved {move:+.3%} against us | "
                            f"stopping after {rounds} rounds"
                        )
                        break

            # Price gate: rounds 2+ only fire if token is cheaper than last fill
            current_token_price = self._get_token_midpoint(token_id)
            if round_num > 1 and last_fill_price is not None and min_price_drop_pct > 0:
                if current_token_price is not None:
                    price_threshold = last_fill_price * (1 - min_price_drop_pct)
                    if current_token_price >= price_threshold:
                        self._logger.debug(
                            f"ACCUM SKIP r{round_num} | {slug} | "
                            f"token={current_token_price:.3f} >= threshold={price_threshold:.3f} "
                            f"(last_fill={last_fill_price:.3f}, need -{min_price_drop_pct:.1%}) | "
                            f"waiting for cheaper entry"
                        )
                        await asyncio.sleep(interval)
                        continue
                    else:
                        self._logger.info(
                            f"ACCUM PRICE GATE PASS r{round_num} | {slug} | "
                            f"token dropped {(current_token_price - last_fill_price) / last_fill_price:+.1%} "
                            f"to {current_token_price:.3f} (was {last_fill_price:.3f})"
                        )

            # Record entry Binance price on first round
            if round_num == 1 and self._momentum_feed:
                bp = self._momentum_feed.get_latest_price(asset)
                if bp:
                    setattr(self, f'_entry_binance_{slug}', bp)

            # Use current token price for order (not stale initial_price)
            order_price = current_token_price if current_token_price else initial_price

            # Guard: if price moved above max_price since signal, abort entire accumulation
            max_price = getattr(self._config, 'cheap_side_max_price', 0.50)
            if order_price > max_price:
                self._logger.warning(
                    f"ACCUM ABORT | {slug} | order_price={order_price:.3f} > max={max_price:.2f} "
                    f"(token moved after signal) | stopping"
                )
                break

            # Place a small maker order
            try:
                signal = {
                    "slug": slug,
                    "token_id": token_id,
                    "asset": asset,
                    "outcome": "Up" if direction.lower() == "up" else "Down",
                    "price": order_price,
                    "source": "epoch_accumulator",
                    "time_remaining_secs": secs_left,
                    "market_window_secs": 300,
                    "entry_mode_override": "fak",
                }

                signal["override_bet_size"] = bet_per_round
                result = await self._executor.execute_buy(signal, bet_per_round * 2)

                if result and result.success:
                    rounds += 1
                    fill_price = result.fill_price or order_price
                    fill_size = result.fill_size or (bet_per_round / fill_price)
                    total_spent += fill_price * fill_size
                    total_shares += fill_size
                    last_fill_price = fill_price  # update price gate reference
                    self._logger.info(
                        f"ACCUM BUY #{rounds} | {slug} {direction} | "
                        f"${fill_price:.3f} x {fill_size:.1f}sh | "
                        f"total: ${total_spent:.2f}, {total_shares:.1f}sh | "
                        f"{secs_left:.0f}s left"
                    )
                    # Record to performance DB
                    if self._tracker:
                        try:
                            trade_id = result.order_id or f"accum_{slug}_{rounds}_{int(time.time())}"
                            outcome = "Up" if direction.lower() == "up" else "Down"
                            await self._tracker.record_entry(
                                trade_id=trade_id,
                                token_id=token_id,
                                slug=slug,
                                entry_price=fill_price,
                                entry_size=fill_size,
                                entry_tx_hash=trade_id,
                                outcome=outcome,
                                market_title=slug,
                                entry_time=time.time(),
                                metadata={
                                    "source": "epoch_accumulator",
                                    "asset": asset,
                                    "direction": direction,
                                    "round": rounds,
                                    "total_rounds": max_rounds,
                                },
                            )
                        except Exception as db_err:
                            self._logger.warning(f"ACCUM DB record failed: {db_err}")
                else:
                    reason = result.reason if result else "unknown"
                    self._logger.debug(
                        f"ACCUM buy failed round {round_num} | {slug} | {reason}"
                    )
            except Exception as e:
                self._logger.warning(f"ACCUM buy error round {round_num} | {slug} | {e}")

            # Wait for next round
            await asyncio.sleep(interval)

        self._logger.info(
            f"ACCUM COMPLETE | {slug} {direction} | "
            f"{rounds}/{max_rounds} rounds | "
            f"${total_spent:.2f} spent | {total_shares:.1f} shares | "
            f"hold to resolution"
        )

    def is_accumulating(self, slug: str) -> bool:
        """Check if currently accumulating for this slug."""
        return slug in self._active

    def get_active_count(self) -> int:
        """Number of active accumulation loops."""
        return len(self._active)

    async def stop_all(self):
        """Cancel all active accumulations."""
        for slug, task in list(self._active.items()):
            task.cancel()
            self._logger.info(f"ACCUM cancelled: {slug}")
        self._active.clear()
