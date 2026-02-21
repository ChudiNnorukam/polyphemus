"""Accumulator Engine — time-distributed paired accumulation for updown markets.

Buys both Up and Down sides of short-duration binary markets when the combined
pair cost is below $1.00, locking in guaranteed profit at settlement.

Supports N concurrent positions, each with independent state machines.
State machine per position: SCANNING → ACCUMULATING → HEDGED → SETTLING → (removed)
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp

from .config import Settings, setup_logger
from .types import (
    GAMMA_API_URL,
    AccumulatorState,
    AccumulatorPosition,
    Position,
)
from .balance_manager import BalanceManager
from .clob_wrapper import ClobWrapper
from .position_store import PositionStore


class AccumulatorEngine:
    """Time-distributed paired accumulation engine.

    Discovers cheap updown markets, accumulates both sides over the window,
    and collects guaranteed profit at settlement when pair_cost < $1.00.

    Supports multiple concurrent positions, each tracked independently.
    """

    def __init__(
        self,
        clob: ClobWrapper,
        balance: BalanceManager,
        store: PositionStore,
        config: Settings,
    ):
        self._clob = clob
        self._balance = balance
        self._store = store
        self._config = config
        self._dry_run = config.accum_dry_run
        self._logger = setup_logger("polyphemus.accumulator")

        # State — concurrent positions keyed by slug
        self._positions: dict[str, AccumulatorPosition] = {}

        # Market discovery cache
        self._market_cache: list[dict] = []
        self._market_cache_ts: float = 0.0

        # aiohttp session (created in start())
        self._session: Optional[aiohttp.ClientSession] = None

        # Scan counter for periodic logging
        self._scan_count: int = 0
        self._last_log_ts: float = 0.0
        self._last_eval_log_ts: float = 0.0
        self._eval_block_reason: str = ""

        # Abandoned slug cooldown: slug → market_end_time (don't re-enter)
        self._abandoned_slugs: dict[str, datetime] = {}

        # Stats for dashboard
        self._orders_placed: int = 0
        self._orders_filled: int = 0
        self._orders_timed_out: int = 0
        self._best_bid_pair: float = 0.0

        # Auto-redeemer (injected by signal_bot)
        self._redeemer = None

        # Metrics collector and adaptive tuner (injected by signal_bot)
        self._metrics = None
        self._adaptive_tuner = None

        # Settlement history and stats for dashboard
        self._settlements: list[dict] = []
        self._accum_total_pnl = 0.0
        self._accum_hedged_count = 0
        self._accum_orphaned_count = 0
        self._accum_unwound_count = 0

        # Circuit breaker: stop trading on sustained losses
        self._consecutive_unwinds = 0
        self._max_consecutive_unwinds = 3
        self._daily_loss_limit = -abs(self._config.max_daily_loss)
        self._circuit_tripped = False
        self._cb_state_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "data", "circuit_breaker.json"
        )
        self._load_circuit_breaker_state()

    def _load_circuit_breaker_state(self):
        """Load persisted circuit breaker PnL from disk."""
        try:
            with open(self._cb_state_path, "r") as f:
                state = json.load(f)
            self._accum_total_pnl = state.get("total_pnl", 0.0)
            self._accum_hedged_count = state.get("hedged_count", 0)
            self._accum_unwound_count = state.get("unwound_count", 0)
            self._consecutive_unwinds = state.get("consecutive_unwinds", 0)
            if self._accum_total_pnl < self._daily_loss_limit:
                self._circuit_tripped = True
                self._logger.warning(
                    f"Circuit breaker TRIPPED on load: PnL ${self._accum_total_pnl:.2f}"
                )
            self._logger.info(
                f"Loaded circuit breaker state: pnl=${self._accum_total_pnl:.2f} "
                f"hedged={self._accum_hedged_count} unwound={self._accum_unwound_count}"
            )
        except FileNotFoundError:
            pass
        except Exception as e:
            self._logger.warning(f"Failed to load circuit breaker state: {e}")

    def _save_circuit_breaker_state(self):
        """Persist circuit breaker PnL to disk after each settle/unwind."""
        try:
            os.makedirs(os.path.dirname(self._cb_state_path), exist_ok=True)
            state = {
                "total_pnl": round(self._accum_total_pnl, 4),
                "hedged_count": self._accum_hedged_count,
                "unwound_count": self._accum_unwound_count,
                "consecutive_unwinds": self._consecutive_unwinds,
                "orphaned_count": self._accum_orphaned_count,
                "updated_at": time.time(),
            }
            tmp = self._cb_state_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f)
            os.rename(tmp, self._cb_state_path)
        except Exception as e:
            self._logger.warning(f"Failed to save circuit breaker state: {e}")

    def _get_aggregate_state(self) -> str:
        """Return aggregate state for backward compat."""
        if not self._positions:
            return "idle"
        return "active"

    @property
    def stats(self) -> dict:
        """Stats for dashboard API."""
        positions = []
        for pos in self._positions.values():
            positions.append({
                "slug": pos.slug,
                "state": pos.state.value,
                "up_qty": pos.up_qty,
                "down_qty": pos.down_qty,
                "pair_cost": round(pos.pair_cost, 4),
                "reprice_count": pos.reprice_count,
                "is_hedged": pos.is_fully_hedged,
            })
        return {
            "state": self._get_aggregate_state(),
            "active_positions": len(self._positions),
            "max_concurrent": self._config.accum_max_concurrent,
            "positions": positions,
            "scan_count": self._scan_count,
            "best_bid_pair": round(self._best_bid_pair, 4),
            "orders_placed": self._orders_placed,
            "orders_filled": self._orders_filled,
            "orders_timed_out": self._orders_timed_out,
            "total_pnl": round(self._accum_total_pnl, 2),
            "hedged_count": self._accum_hedged_count,
            "orphaned_count": self._accum_orphaned_count,
            "unwound_count": self._accum_unwound_count,
            "settlements": self._settlements[-20:],  # Last 20 for dashboard
        }

    def set_redeemer(self, redeemer):
        """Inject redeemer for non-blocking post-settlement redemption."""
        self._redeemer = redeemer

    def set_metrics(self, metrics):
        """Inject metrics collector for adaptive tuning."""
        self._metrics = metrics

    def set_adaptive_tuner(self, tuner):
        """Inject adaptive tuner for dynamic parameter overrides."""
        self._adaptive_tuner = tuner

    def _get_param(self, name: str) -> float:
        """Get config param, checking adaptive tuner overrides first."""
        if self._adaptive_tuner and name in self._adaptive_tuner.current_overrides:
            return self._adaptive_tuner.current_overrides[name]
        return getattr(self._config, name)

    # ========================================================================
    # Main Loop
    # ========================================================================

    async def start(self):
        """Main loop: advance active positions, scan for new opportunities."""
        self._session = aiohttp.ClientSession()
        self._logger.info(
            f"Accumulator started | dry_run={self._dry_run} | "
            f"assets={self._config.accum_assets} | windows={self._config.accum_window_types} | "
            f"max_pair_cost={self._get_param('accum_max_pair_cost')} | "
            f"max_concurrent={self._config.accum_max_concurrent}"
        )
        try:
            while True:
                # 1. Advance each active position based on ITS state
                for slug in list(self._positions):
                    pos = self._positions.get(slug)
                    if not pos:
                        continue
                    try:
                        if pos.state == AccumulatorState.SCANNING:
                            await self._evaluate_and_enter(pos)
                        elif pos.state == AccumulatorState.ACCUMULATING:
                            await self._accumulate_both_sides(pos)
                        elif pos.state == AccumulatorState.HEDGED:
                            await self._monitor_hedged_position(pos)
                        elif pos.state == AccumulatorState.SETTLING:
                            await self._handle_settlement(pos)
                    except Exception as e:
                        self._logger.exception(f"Error in {pos.state.value} for {slug}: {e}")
                        await self._emergency_cleanup(pos)

                # 2. If room for more positions, scan for new opportunities
                if len(self._positions) < self._config.accum_max_concurrent:
                    await self._scan_for_window()

                await asyncio.sleep(self._get_param('accum_scan_interval'))
        finally:
            if self._session and not self._session.closed:
                await self._session.close()

    # ========================================================================
    # State Handlers
    # ========================================================================

    async def _scan_for_window(self):
        """Discover active markets, check for pair cost opportunity."""
        self._scan_count += 1
        markets = await self._discover_markets()
        if not markets:
            now = time.time()
            if now - self._last_log_ts >= 60:
                self._logger.info(f"Scan #{self._scan_count}: no active markets found")
                self._last_log_ts = now
            return

        best_pair = None
        best_slug = None

        # Clean up expired abandoned slugs
        now_utc = datetime.now(tz=timezone.utc)
        self._abandoned_slugs = {
            s: t for s, t in self._abandoned_slugs.items() if t > now_utc
        }

        # Priority: BTC-5m (0) > ETH-15m (1) > BTC-15m (2) > ETH-5m (3)
        PRIORITY = {("btc", "5m"): 0, ("eth", "15m"): 1, ("btc", "15m"): 2, ("eth", "5m"): 3}
        markets.sort(key=lambda m: PRIORITY.get((m.get("asset", ""), m.get("window_type", "")), 9))

        for market in markets:
            # Skip slugs we already abandoned (cooldown until market expires)
            if market["slug"] in self._abandoned_slugs:
                continue
            # Skip slugs with active positions
            if market["slug"] in self._positions:
                continue
            # Cap 5m concurrent to 1: simultaneous 5m entries are correlated and
            # cause paired unwinds when spreads blow out in the same window
            if market.get("window_secs", 0) == 300:
                active_5m = sum(1 for p in self._positions.values() if p.window_secs == 300)
                if active_5m >= 1:
                    continue
            # Skip markets that just opened — books are thin and directional at open
            market_open = market["expires_at"] - timedelta(seconds=market["window_secs"])
            secs_since_open = (now_utc - market_open).total_seconds()
            if secs_since_open < 30:
                continue
            try:
                up_book = await self._clob.get_order_book(market["up_token_id"])
                down_book = await self._clob.get_order_book(market["down_token_id"])
            except Exception:
                continue

            up_bids = up_book.get("bids", [])
            down_bids = down_book.get("bids", [])

            if not up_bids or not down_bids:
                self._logger.debug(
                    f"Empty book for {market['slug']}: "
                    f"up_bids={len(up_bids)} down_bids={len(down_bids)}"
                )
                continue

            best_bid_up = up_bids[0]["price"]
            best_bid_down = down_bids[0]["price"]
            bid_pair_cost = best_bid_up + best_bid_down

            if best_pair is None or bid_pair_cost < best_pair:
                best_pair = bid_pair_cost
                best_slug = market["slug"]

            if self._evaluate_opportunity(best_bid_up, best_bid_down):
                pos = AccumulatorPosition(
                    slug=market["slug"],
                    window_secs=market["window_secs"],
                    state=AccumulatorState.SCANNING,
                    up_token_id=market["up_token_id"],
                    down_token_id=market["down_token_id"],
                    market_end_time=market["expires_at"],
                    entry_time=datetime.now(tz=timezone.utc),
                    condition_id=market.get("condition_id", ""),
                )
                self._positions[market["slug"]] = pos
                self._logger.info(
                    f"Opportunity found: {market['slug']} | "
                    f"up_bid={best_bid_up:.3f} down_bid={best_bid_down:.3f} "
                    f"bid_pair={bid_pair_cost:.4f} | "
                    f"active={len(self._positions)}/{self._config.accum_max_concurrent}"
                )
                return  # One new opportunity per scan cycle

        # Update stats for dashboard
        if best_pair is not None:
            self._best_bid_pair = best_pair

        # Log best bid pair cost every 60s so we know the accumulator is alive
        now = time.time()
        if now - self._last_log_ts >= 60:
            if best_pair is not None:
                self._logger.info(
                    f"Scan #{self._scan_count}: {len(markets)} markets | "
                    f"best_bid_pair=${best_pair:.4f} ({best_slug}) | "
                    f"need <${self._get_param('accum_max_pair_cost')} | "
                    f"active={len(self._positions)}/{self._config.accum_max_concurrent}"
                )
            else:
                self._logger.info(f"Scan #{self._scan_count}: {len(markets)} markets, no valid books")
            self._last_log_ts = now

    async def _evaluate_and_enter(self, pos: AccumulatorPosition):
        """SCANNING: Place both maker BUY orders simultaneously, transition to ACCUMULATING.

        Uses BID prices for pair_cost check (maker economics — we post at bid, earn the spread).
        Both orders placed at once via asyncio.gather to eliminate sequential fill race condition.
        If first leg fills but second doesn't within accum_hedge_deadline_secs, _accumulate_both_sides
        cancels the unfilled leg and unwinds the filled leg via _unwind_orphan().
        """
        secs_left = (pos.market_end_time - datetime.now(tz=timezone.utc)).total_seconds()

        if secs_left < self._config.accum_min_secs_remaining:
            self._logger.info(f"Window expired during scan: {pos.slug} ({secs_left:.0f}s left)")
            self._positions.pop(pos.slug, None)
            self._abandoned_slugs[pos.slug] = pos.market_end_time  # prevent re-scan spam
            return

        # Fetch fresh books for both sides
        try:
            up_book = await self._clob.get_order_book(pos.up_token_id)
            down_book = await self._clob.get_order_book(pos.down_token_id)
        except Exception as e:
            self._eval_block_reason = f"book_fetch_error: {e}"
            self._log_eval_heartbeat(pos, secs_left)
            return

        up_bids = up_book.get("bids", [])
        down_bids = down_book.get("bids", [])
        if not up_bids or not down_bids:
            self._eval_block_reason = f"empty_books: up_bids={len(up_bids)} down_bids={len(down_bids)}"
            self._log_eval_heartbeat(pos, secs_left)
            return

        # Maker entry uses BID prices (we post at bid, wait for fill at that price)
        up_bid = float(up_bids[0]["price"])
        down_bid = float(down_bids[0]["price"])
        bid_pair = up_bid + down_bid

        # Directional guard: reject imbalanced markets where one side is too expensive.
        # Sequential maker fills on directional markets (e.g., $0.20/$0.79) create
        # orphan risk when the expensive side moves further before our order fills.
        max_side = self._get_param('accum_max_side_price')
        if up_bid > max_side or down_bid > max_side:
            self._eval_block_reason = (
                f"directional: up_bid={up_bid:.3f} down_bid={down_bid:.3f} "
                f"max_side=${max_side:.2f}"
            )
            self._log_eval_heartbeat(pos, secs_left)
            return

        max_pair = self._get_param('accum_max_pair_cost')
        if bid_pair >= max_pair:
            self._eval_block_reason = (
                f"bid_pair_expensive: ${bid_pair:.4f} >= ${max_pair} "
                f"(up_bid={up_bid:.3f} down_bid={down_bid:.3f})"
            )
            self._log_eval_heartbeat(pos, secs_left)
            return

        # Circuit breaker check
        if not self._evaluate_opportunity(up_bid, down_bid):
            self._eval_block_reason = f"circuit_breaker or profit_too_low: bid_pair={bid_pair:.4f}"
            self._log_eval_heartbeat(pos, secs_left)
            return

        # Capital check — use bid_pair as cost per share pair
        available = await self._balance.get_available_for_accumulator()
        if available < self._config.accum_min_shares * bid_pair:
            self._eval_block_reason = f"insufficient_capital: ${available:.2f}"
            self._log_eval_heartbeat(pos, secs_left)
            return

        target_shares = min(available / bid_pair, self._config.accum_max_shares)
        target_shares = max(target_shares, self._config.accum_min_shares)
        pos.target_shares = target_shares

        self._logger.info(
            f"Maker entry attempt: {pos.slug} | "
            f"up_bid={up_bid:.3f} down_bid={down_bid:.3f} bid_pair={bid_pair:.4f} | "
            f"{target_shares:.0f}sh | ${target_shares * bid_pair:.2f} total"
        )

        # Place BOTH maker orders simultaneously — eliminates sequential fill race condition.
        # Both orders rest in the book; fills happen independently as sellers arrive.
        up_id, down_id = await asyncio.gather(
            self._place_maker_order(pos.up_token_id, up_bid, target_shares),
            self._place_maker_order(pos.down_token_id, down_bid, target_shares),
        )

        if not up_id and not down_id:
            self._eval_block_reason = "both_maker_placements_failed"
            self._log_eval_heartbeat(pos, secs_left)
            return

        now = datetime.now(tz=timezone.utc)
        pos.up_order_id = up_id
        pos.up_order_time = now if up_id else None
        pos.down_order_id = down_id
        pos.down_order_time = now if down_id else None
        self._set_side_price(pos, "UP", up_bid)
        self._set_side_price(pos, "DOWN", down_bid)
        self._orders_placed += (1 if up_id else 0) + (1 if down_id else 0)

        self._logger.info(
            f"Maker orders placed: {pos.slug} | "
            f"UP order={'ok' if up_id else 'FAILED'} "
            f"DOWN order={'ok' if down_id else 'FAILED'} | "
            f"waiting for fills (hedge deadline={self._config.accum_hedge_deadline_secs}s)"
        )
        self._transition(AccumulatorState.ACCUMULATING, pos)

    async def _accumulate_both_sides(self, pos: AccumulatorPosition):
        """ACCUMULATING: Monitor both maker orders; enforce hedge deadline; handle fills."""
        secs_left = (pos.market_end_time - datetime.now(tz=timezone.utc)).total_seconds()
        now = datetime.now(tz=timezone.utc)

        # Time guard: if < 60s left, cancel resting orders and settle
        if secs_left < 60:
            self._logger.info(f"Time guard: {pos.slug} has {secs_left:.0f}s left, settling")
            await self._cancel_resting_orders(pos)
            self._transition(AccumulatorState.SETTLING, pos)
            return

        # --- Dual-resting phase: BOTH orders placed, neither filled yet ---
        # _get_resting_order() only returns one side; we need to check both.
        if pos.up_order_id and pos.down_order_id and pos.up_qty == 0 and pos.down_qty == 0:
            up_result = await self._check_and_handle_order(
                pos, "UP", pos.up_order_id, pos.up_order_time
            )
            if up_result == "abandoned":
                await self._cancel_resting_orders(pos)
                self._abandoned_slugs[pos.slug] = pos.market_end_time
                self._positions.pop(pos.slug, None)
                return
            # Also check DOWN in same cycle (both can fill simultaneously)
            if pos.down_order_id:
                down_result = await self._check_and_handle_order(
                    pos, "DOWN", pos.down_order_id, pos.down_order_time
                )
                if down_result == "abandoned":
                    await self._cancel_resting_orders(pos)
                    self._abandoned_slugs[pos.slug] = pos.market_end_time
                    self._positions.pop(pos.slug, None)
                    return
            # Record first_fill_time the moment either leg fills
            if (pos.up_qty > 0 or pos.down_qty > 0) and pos.first_fill_time is None:
                pos.first_fill_time = now
                filled = "UP" if pos.up_qty > 0 else "DOWN"
                self._logger.info(
                    f"First fill: {filled} leg filled | {pos.slug} | "
                    f"hedge deadline in {self._config.accum_hedge_deadline_secs}s"
                )
            return

        # --- Hedge deadline: one leg filled, waiting for second ---
        # If second leg hasn't filled within accum_hedge_deadline_secs, cancel and unwind.
        one_leg_only = (pos.up_qty > 0) != (pos.down_qty > 0)  # XOR
        if pos.first_fill_time and one_leg_only:
            elapsed = (now - pos.first_fill_time).total_seconds()
            deadline = self._config.accum_hedge_deadline_secs
            if elapsed > deadline:
                filled_side = "UP" if pos.up_qty > 0 else "DOWN"
                unfilled_side = "DOWN" if filled_side == "UP" else "UP"
                await self._cancel_resting_orders(pos)
                self._logger.warning(
                    f"HEDGE DEADLINE exceeded: {pos.slug} | "
                    f"{filled_side} filled {elapsed:.0f}s ago, {unfilled_side} never filled | "
                    f"unwinding {filled_side} to recover capital"
                )
                await self._unwind_orphan(pos, filled_side)
                return

        # Check if already fully hedged (both sides filled)
        if pos.up_qty > 0 and pos.down_qty > 0:
            # Imbalance guard: partial maker fills create directional exposure.
            # Sell the excess of the larger side so both legs match before hedging.
            excess = abs(pos.up_qty - pos.down_qty)
            if excess > 1.0:
                excess_side = "DOWN" if pos.down_qty > pos.up_qty else "UP"
                excess_token = pos.down_token_id if excess_side == "DOWN" else pos.up_token_id
                self._logger.warning(
                    f"SHARE IMBALANCE: {pos.slug} | up={pos.up_qty:.0f} down={pos.down_qty:.0f} "
                    f"(excess {excess:.0f} {excess_side}) — selling excess to balance"
                )
                await self._cancel_resting_orders(pos)
                # Wait 30s for CLOB to index freshly-filled maker tokens (Bug #46 pattern)
                await asyncio.sleep(30)
                sell_result = await self._clob.place_fok_order(excess_token, excess, "SELL")
                if sell_result.success:
                    if excess_side == "DOWN":
                        pos.down_qty = pos.up_qty
                    else:
                        pos.up_qty = pos.down_qty
                    self._logger.info(f"Imbalance corrected: sold {excess:.0f} excess {excess_side} shares")
                else:
                    self._logger.error(
                        f"Excess sell failed after 30s wait ({sell_result.error}) — settling imbalanced"
                    )
                    self._transition(AccumulatorState.SETTLING, pos)
                    return

            pos.pair_cost = pos.up_avg_price + pos.down_avg_price
            if pos.pair_cost < self._get_param('accum_max_pair_cost'):
                pos.is_fully_hedged = True
                self._logger.info(
                    f"HEDGED: {pos.slug} | up={pos.up_qty:.0f}@${pos.up_avg_price:.3f} "
                    f"down={pos.down_qty:.0f}@${pos.down_avg_price:.3f} | "
                    f"pair_cost=${pos.pair_cost:.4f} | profit=${1.0 - pos.pair_cost:.4f}/share"
                )
                self._transition(AccumulatorState.HEDGED, pos)
            return

        # Check for existing resting order
        side, order_id, order_time = self._get_resting_order(pos)
        if order_id:
            result = await self._check_and_handle_order(pos, side, order_id, order_time)
            if result == "filled":
                # Check if now fully hedged
                if pos.up_qty > 0 and pos.down_qty > 0:
                    # Imbalance guard: sell excess before declaring hedged
                    excess = abs(pos.up_qty - pos.down_qty)
                    if excess > 1.0:
                        excess_side = "DOWN" if pos.down_qty > pos.up_qty else "UP"
                        excess_token = pos.down_token_id if excess_side == "DOWN" else pos.up_token_id
                        self._logger.warning(
                            f"SHARE IMBALANCE: {pos.slug} | up={pos.up_qty:.0f} down={pos.down_qty:.0f} "
                            f"(excess {excess:.0f} {excess_side}) — selling excess to balance"
                        )
                        await self._cancel_resting_orders(pos)
                        # Wait 30s for CLOB to index freshly-filled maker tokens (Bug #46 pattern)
                        await asyncio.sleep(30)
                        sell_result = await self._clob.place_fok_order(excess_token, excess, "SELL")
                        if sell_result.success:
                            if excess_side == "DOWN":
                                pos.down_qty = pos.up_qty
                            else:
                                pos.up_qty = pos.down_qty
                            self._logger.info(f"Imbalance corrected: sold {excess:.0f} excess {excess_side} shares")
                        else:
                            self._logger.error(
                                f"Excess sell failed after 30s wait ({sell_result.error}) — settling imbalanced"
                            )
                            self._transition(AccumulatorState.SETTLING, pos)
                            return

                    pos.pair_cost = pos.up_avg_price + pos.down_avg_price
                    # CRITICAL: pair_cost guard — reject guaranteed losses
                    if pos.pair_cost > 1.0:
                        self._logger.error(
                            f"PAIR COST > $1.00: {pos.slug} | pair_cost=${pos.pair_cost:.4f} "
                            f"— guaranteed loss, settling immediately"
                        )
                        self._transition(AccumulatorState.SETTLING, pos)
                        return
                    if pos.pair_cost >= self._get_param('accum_max_pair_cost'):
                        self._logger.warning(
                            f"Pair cost ${pos.pair_cost:.4f} >= limit "
                            f"${self._get_param('accum_max_pair_cost')} — settling"
                        )
                        self._transition(AccumulatorState.SETTLING, pos)
                        return
                    pos.is_fully_hedged = True
                    self._logger.info(
                        f"HEDGED: {pos.slug} | up={pos.up_qty:.0f}@${pos.up_avg_price:.3f} "
                        f"down={pos.down_qty:.0f}@${pos.down_avg_price:.3f} | "
                        f"pair_cost=${pos.pair_cost:.4f} | profit=${1.0 - pos.pair_cost:.4f}/share"
                    )
                    self._transition(AccumulatorState.HEDGED, pos)
            elif result == "abandoned":
                # Try FOK taker fallback before unwinding
                if await self._try_fok_fallback(pos):
                    return
                self._logger.warning(f"Reprice limit in ACCUMULATING, FOK not viable — unwinding")
                filled_side = "UP" if pos.up_qty > 0 else "DOWN"
                await self._unwind_orphan(pos, filled_side)
            return

        # No resting order — place order for the missing side
        if pos.up_qty > 0 and pos.down_qty == 0:
            try:
                down_book = await self._clob.get_order_book(pos.down_token_id)
            except Exception:
                return
            down_bids = down_book.get("bids", [])
            if not down_bids:
                return
            best_bid_down = float(down_bids[0]["price"])
            projected_pair = pos.up_avg_price + best_bid_down
            if projected_pair > self._get_param('accum_max_pair_cost'):
                pos.blocked_cycles += 1
                if pos.blocked_cycles >= 3:
                    # Try FOK taker fallback before unwinding
                    if await self._try_fok_fallback(pos):
                        return
                    self._logger.warning(
                        f"UNWIND: {pos.slug} | DOWN unfillable for {pos.blocked_cycles} cycles | "
                        f"projected_pair=${projected_pair:.4f} > limit ${self._get_param('accum_max_pair_cost')} | "
                        f"FOK not viable — selling UP leg to recover capital"
                    )
                    await self._unwind_orphan(pos, "UP")
                    return
                if pos.blocked_cycles == 1 or pos.blocked_cycles % 5 == 0:
                    self._logger.info(
                        f"Second leg blocked ({pos.blocked_cycles}/3): {pos.slug} | "
                        f"projected_pair=${projected_pair:.4f} > ${self._get_param('accum_max_pair_cost')} | "
                        f"up_avg=${pos.up_avg_price:.3f} best_bid_down=${best_bid_down:.3f}"
                    )
                return
            # Balance guard: use CLOB balance directly (already reflects first leg cost)
            try:
                available = await self._balance.get_balance()
            except Exception:
                available = 0
            needed = pos.up_qty * best_bid_down
            if available < needed:
                pos.blocked_cycles += 1
                if pos.blocked_cycles == 1 or pos.blocked_cycles % 10 == 0:
                    self._logger.info(
                        f"Second leg waiting for balance: ${available:.2f} < ${needed:.2f} needed | "
                        f"{pos.slug} ({pos.blocked_cycles} cycles)"
                    )
                return
            order_id = await self._place_maker_order(pos.down_token_id, best_bid_down, pos.up_qty)
            if order_id:
                pos.down_order_id = order_id
                pos.down_order_time = datetime.now(tz=timezone.utc)
                pos.target_shares = pos.up_qty
                self._set_side_price(pos, "DOWN", best_bid_down)
                self._orders_placed += 1
                pos.blocked_cycles = 0

        elif pos.down_qty > 0 and pos.up_qty == 0:
            try:
                up_book = await self._clob.get_order_book(pos.up_token_id)
            except Exception:
                return
            up_bids = up_book.get("bids", [])
            if not up_bids:
                return
            best_bid_up = float(up_bids[0]["price"])
            projected_pair = best_bid_up + pos.down_avg_price
            if projected_pair > self._get_param('accum_max_pair_cost'):
                pos.blocked_cycles += 1
                if pos.blocked_cycles >= 3:
                    # Try FOK taker fallback before unwinding
                    if await self._try_fok_fallback(pos):
                        return
                    self._logger.warning(
                        f"UNWIND: {pos.slug} | UP unfillable for {pos.blocked_cycles} cycles | "
                        f"projected_pair=${projected_pair:.4f} > limit ${self._get_param('accum_max_pair_cost')} | "
                        f"FOK not viable — selling DOWN leg to recover capital"
                    )
                    await self._unwind_orphan(pos, "DOWN")
                    return
                if pos.blocked_cycles == 1 or pos.blocked_cycles % 5 == 0:
                    self._logger.info(
                        f"Second leg blocked ({pos.blocked_cycles}/3): {pos.slug} | "
                        f"projected_pair=${projected_pair:.4f} > ${self._get_param('accum_max_pair_cost')} | "
                        f"down_avg=${pos.down_avg_price:.3f} best_bid_up=${best_bid_up:.3f}"
                    )
                return
            # Balance guard: use CLOB balance directly (already reflects first leg cost)
            try:
                available = await self._balance.get_balance()
            except Exception:
                available = 0
            needed = pos.down_qty * best_bid_up
            if available < needed:
                pos.blocked_cycles += 1
                if pos.blocked_cycles == 1 or pos.blocked_cycles % 10 == 0:
                    self._logger.info(
                        f"Second leg waiting for balance: ${available:.2f} < ${needed:.2f} needed | "
                        f"{pos.slug} ({pos.blocked_cycles} cycles)"
                    )
                return
            order_id = await self._place_maker_order(pos.up_token_id, best_bid_up, pos.down_qty)
            if order_id:
                pos.up_order_id = order_id
                pos.up_order_time = datetime.now(tz=timezone.utc)
                pos.target_shares = pos.down_qty
                self._set_side_price(pos, "UP", best_bid_up)
                self._orders_placed += 1
                pos.blocked_cycles = 0

    async def _try_fok_fallback(self, pos: AccumulatorPosition) -> bool:
        """Try FOK taker buy for the missing second leg. Returns True if hedged."""
        # If market has expired, secondary market is closed — FOK BUY will fail with the same
        # ambiguous "not enough balance/allowance" error as other CLOB rejections (Bug #47).
        secs_left = (pos.market_end_time - datetime.now(tz=timezone.utc)).total_seconds()
        if secs_left <= 0:
            self._logger.warning(
                f"FOK fallback skipped: {pos.slug} market already expired ({secs_left:.0f}s). "
                "Orphaned leg left for redeemer."
            )
            return False

        if pos.up_qty > 0 and pos.down_qty == 0:
            filled_price = pos.up_avg_price
            other_token = pos.down_token_id
            missing_side = "DOWN"
            target_qty = pos.up_qty
        elif pos.down_qty > 0 and pos.up_qty == 0:
            filled_price = pos.down_avg_price
            other_token = pos.up_token_id
            missing_side = "UP"
            target_qty = pos.down_qty
        else:
            return False

        try:
            book = await self._clob.get_order_book(other_token)
            asks = book.get("asks", [])
            if not asks:
                self._logger.info(f"FOK fallback: no asks for {missing_side} leg")
                return False
            best_ask = float(asks[0]["price"])
        except Exception as e:
            self._logger.warning(f"FOK fallback book fetch failed: {e}")
            return False

        # Estimate taker fee: fee(p) = p * (1-p) * 0.0624
        taker_fee = best_ask * (1.0 - best_ask) * 0.0624
        total_cost = filled_price + best_ask + taker_fee

        if total_cost >= 1.00:
            self._logger.info(
                f"FOK fallback not profitable: {pos.slug} | "
                f"filled=${filled_price:.3f} + ask=${best_ask:.3f} + fee=${taker_fee:.4f} "
                f"= ${total_cost:.4f} >= $1.00"
            )
            return False

        profit_per_share = 1.00 - total_cost
        dollar_amount = round(target_qty * best_ask, 2)

        self._logger.info(
            f"FOK fallback profitable: {pos.slug} | "
            f"total=${total_cost:.4f} | profit=${profit_per_share:.4f}/share | "
            f"FOK BUY {missing_side} ${dollar_amount:.2f}"
        )

        if self._dry_run:
            self._logger.info(f"[DRY RUN] Would FOK BUY {missing_side} ${dollar_amount:.2f}")
            if missing_side == "UP":
                pos.up_qty = target_qty
                pos.up_avg_price = best_ask
            else:
                pos.down_qty = target_qty
                pos.down_avg_price = best_ask
            pos.pair_cost = pos.up_avg_price + pos.down_avg_price
            pos.is_fully_hedged = True
            self._store.add(Position(
                token_id=other_token,
                slug=pos.slug,
                entry_price=best_ask,
                entry_size=target_qty,
                entry_time=datetime.now(tz=timezone.utc),
                entry_tx_hash="fok_dry",
                market_end_time=pos.market_end_time,
                metadata={"is_accumulator": True, "accum_pair_id": f"accum_{pos.slug}", "accum_side": missing_side},
            ))
            self._transition(AccumulatorState.HEDGED, pos)
            return True

        result = await self._clob.place_fok_order(other_token, dollar_amount, "BUY")
        if not result.success:
            self._logger.warning(f"FOK fallback failed: {result.error}")
            return False

        # FOK filled — record and transition to HEDGED
        # Fetch actual fill details; fall back to estimate if unavailable
        est_shares = dollar_amount / best_ask
        if result.order_id:
            details = await self._clob.get_order_details(result.order_id)
            if details and details.get("size_matched", 0) > 0:
                actual = details["size_matched"]
                self._logger.info(
                    f"FOK actual fill: {actual:.4f} shares (est {est_shares:.4f})"
                )
                est_shares = actual
        if missing_side == "UP":
            pos.up_qty = est_shares
            pos.up_avg_price = best_ask
        else:
            pos.down_qty = est_shares
            pos.down_avg_price = best_ask
        pos.pair_cost = pos.up_avg_price + pos.down_avg_price
        pos.is_fully_hedged = True
        self._orders_filled += 1
        self._store.add(Position(
            token_id=other_token,
            slug=pos.slug,
            entry_price=best_ask,
            entry_size=est_shares,
            entry_time=datetime.now(tz=timezone.utc),
            entry_tx_hash=getattr(result, "order_id", "fok"),
            market_end_time=pos.market_end_time,
            metadata={"is_accumulator": True, "accum_pair_id": f"accum_{pos.slug}", "accum_side": missing_side},
        ))

        self._logger.info(
            f"FOK HEDGED: {pos.slug} | up={pos.up_qty:.0f}@${pos.up_avg_price:.3f} "
            f"down={pos.down_qty:.0f}@${pos.down_avg_price:.3f} | "
            f"pair_cost=${pos.pair_cost:.4f} (incl taker fee ~${taker_fee:.4f})"
        )
        self._transition(AccumulatorState.HEDGED, pos)
        return True

    async def _unwind_orphan(self, pos: AccumulatorPosition, filled_side: str):
        """Sell the filled leg at market to recover capital instead of holding to expiry."""
        token_id = pos.up_token_id if filled_side == "UP" else pos.down_token_id
        qty = pos.up_qty if filled_side == "UP" else pos.down_qty

        if qty < 5:
            self._logger.warning(f"Unwind skip: {filled_side} qty={qty:.0f} below minimum")
            self._store.remove(token_id)
            self._positions.pop(pos.slug, None)
            return

        # If the market has already expired, we cannot sell on the secondary market.
        # Leave the tokens for the redeemer — expected value is ~$0.50/share (coin flip),
        # which is near our entry price. Do NOT count this as a circuit-breaker loss.
        secs_left = (pos.market_end_time - datetime.now(tz=timezone.utc)).total_seconds()
        if secs_left <= 0:
            avg_price = pos.up_avg_price if filled_side == "UP" else pos.down_avg_price
            self._logger.warning(
                f"Unwind skipped: {pos.slug} market already expired ({secs_left:.0f}s). "
                f"{filled_side} {qty:.0f} shares @ ${avg_price:.3f} left for redeemer."
            )
            self._store.remove(token_id)
            self._positions.pop(pos.slug, None)
            return

        # Get best bid to sell into
        try:
            book = await self._clob.get_order_book(token_id)
            bids = book.get("bids", [])
            if not bids:
                self._logger.error(f"Unwind failed: no bids for {filled_side} leg")
                self._store.remove(token_id)
                self._positions.pop(pos.slug, None)
                return
            sell_price = float(bids[0]["price"])
        except Exception as e:
            self._logger.error(f"Unwind book fetch failed: {e}")
            self._store.remove(token_id)
            self._positions.pop(pos.slug, None)
            return

        avg_price = pos.up_avg_price if filled_side == "UP" else pos.down_avg_price
        sell_success = False
        unwind_loss = 0.0

        if self._dry_run:
            # Simulate the unwind — spread cost only
            unwind_loss = (avg_price - sell_price) * qty
            sell_success = True
            # Credit back the sell proceeds
            self._balance.sim_credit(sell_price * qty)
            self._logger.info(
                f"[DRY RUN] UNWOUND: {pos.slug} | SOLD {filled_side} {qty:.0f} shares @ ${sell_price:.3f} | "
                f"entry=${avg_price:.3f} | loss=${unwind_loss:.2f} (spread cost)"
            )
        else:
            # Step 1: Wait for CLOB to index freshly-filled conditional tokens.
            # CLOB can take 5-30s to reflect a new fill in its balance view.
            # Poll get_share_balance() until the tokens appear, then sell that amount.
            await asyncio.sleep(5)
            sell_qty = 0.0
            for check in range(1, 7):
                clob_qty = await self._clob.get_share_balance(token_id)
                if clob_qty >= 1.0:
                    sell_qty = clob_qty
                    self._logger.info(
                        f"Unwind: CLOB confirmed {clob_qty:.0f} shares for {filled_side} (check {check}/6)"
                    )
                    break
                self._logger.warning(
                    f"Unwind: CLOB shows 0 shares for {filled_side} (check {check}/6), retrying in 5s"
                )
                if check < 6:
                    await asyncio.sleep(5)

            if sell_qty < 1.0:
                self._logger.error(
                    f"Unwind: CLOB never indexed {filled_side} tokens after 6 checks (~35s) "
                    f"for {pos.slug} — tokens left for redeemer"
                )
                unwind_loss = avg_price * qty
            else:
                # Step 2: FOK SELL with the CLOB-confirmed quantity
                for attempt in range(1, 4):
                    try:
                        result = await self._clob.place_fok_order(
                            token_id=token_id,
                            amount=sell_qty,
                            side="SELL",
                        )
                        if result.success:
                            unwind_loss = (avg_price - sell_price) * sell_qty
                            sell_success = True
                            self._logger.info(
                                f"UNWOUND (FOK): {pos.slug} | SOLD {filled_side} {sell_qty:.0f} shares | "
                                f"entry=${avg_price:.3f} est_sell=${sell_price:.3f} | loss=${unwind_loss:.2f}"
                            )
                            break
                        else:
                            self._logger.warning(
                                f"Unwind FOK SELL attempt {attempt}/3 failed: {result.error}"
                            )
                    except Exception as e:
                        self._logger.warning(f"Unwind SELL attempt {attempt}/3 exception: {e}")

                    if attempt < 3:
                        await asyncio.sleep(3)
                        # Re-check expiry: market may have closed during the sleep
                        secs_left = (pos.market_end_time - datetime.now(tz=timezone.utc)).total_seconds()
                        if secs_left <= 0:
                            self._logger.warning(
                                f"Unwind retry abort: {pos.slug} expired during retry sleep. "
                                "Tokens left for redeemer."
                            )
                            break

                if not sell_success:
                    self._logger.error(
                        f"Unwind SELL failed after 3 attempts for {pos.slug} {filled_side} "
                        f"{sell_qty:.0f} shares — tokens left for redeemer"
                    )
                    # Do NOT record loss here: the redeemer will settle these tokens.
                    # Expected value is ~entry_price (coin flip). Recording avg_price*qty
                    # as loss would overcount and false-trip the circuit breaker.
                    unwind_loss = 0.0

        if sell_success:
            # True strategy unwind: real spread cost. Count against circuit breaker.
            self._settlements.append({
                "slug": pos.slug,
                "exit_reason": "unwound",
                "matched": 0,
                "up_qty": pos.up_qty,
                "down_qty": pos.down_qty,
                "up_avg": pos.up_avg_price,
                "down_avg": pos.down_avg_price,
                "pair_cost": 0,
                "pnl": round(-unwind_loss, 2),
                "timestamp": time.time(),
            })
            if len(self._settlements) > 100:
                self._settlements = self._settlements[-100:]
            self._accum_total_pnl -= unwind_loss
            self._accum_unwound_count += 1
            self._consecutive_unwinds += 1
            self._save_circuit_breaker_state()
        else:
            # Infra unwind: SELL failed, tokens left for redeemer. NOT a strategy loss.
            # Do not increment consecutive_unwinds — this is not the strategy failing.
            self._accum_orphaned_count += 1
            self._logger.warning(
                f"Infra unwind (sell failed): {pos.slug} orphaned for redeemer. "
                "NOT counting against circuit breaker."
            )
            self._save_circuit_breaker_state()

        # Record cycle to metrics DB for adaptive tuner
        if self._metrics:
            from .accumulator_metrics import CycleRecord
            entry_ts = pos.entry_time.timestamp() if pos.entry_time else time.time() - 60
            self._metrics.record_cycle(CycleRecord(
                slug=pos.slug,
                started_at=entry_ts,
                ended_at=time.time(),
                up_qty=pos.up_qty,
                down_qty=pos.down_qty,
                up_avg_price=pos.up_avg_price,
                down_avg_price=pos.down_avg_price,
                pair_cost=0.0,
                pnl=-unwind_loss,
                exit_reason="unwound",
                reprices_used=pos.reprice_count,
                fill_time_secs=time.time() - entry_ts,
                hedge_time_secs=0.0,
                spread_at_entry=self._best_bid_pair,
            ))

        # Clean up and remove position. Force balance cache expiry so the next
        # scan sees freed capital immediately (D1: 60s TTL would otherwise delay recovery).
        self._balance._cache_time = 0
        self._store.remove(token_id)
        self._abandoned_slugs[pos.slug] = pos.market_end_time
        self._positions.pop(pos.slug, None)

    async def _monitor_hedged_position(self, pos: AccumulatorPosition):
        """HEDGED: Position locked, wait for window expiry."""
        secs_left = (pos.market_end_time - datetime.now(tz=timezone.utc)).total_seconds()
        if secs_left < 60:
            self._transition(AccumulatorState.SETTLING, pos)

    async def _handle_settlement(self, pos: AccumulatorPosition):
        """SETTLING: Cancel unfilled orders, wait for resolution, record P&L."""
        # Step 1: Cancel any resting orders
        if pos.up_order_id:
            try:
                await self._clob.cancel_order(pos.up_order_id)
            except Exception:
                pass
            pos.up_order_id = None
        if pos.down_order_id:
            try:
                await self._clob.cancel_order(pos.down_order_id)
            except Exception:
                pass
            pos.down_order_id = None

        is_hedged = pos.up_qty > 0 and pos.down_qty > 0
        is_orphaned = (pos.up_qty > 0) != (pos.down_qty > 0)

        # Step 2: Wait for market resolution
        result = await self._wait_for_settlement(pos)

        # Step 3: Calculate P&L
        if is_hedged:
            matched = min(pos.up_qty, pos.down_qty)
            total_spent = (pos.up_qty * pos.up_avg_price) + (pos.down_qty * pos.down_avg_price)
            pnl = matched * 1.00 - total_spent
            pos.exit_reason = "hedged_settlement"
            self._logger.info(
                f"HEDGED settlement: {pos.slug} | matched={matched:.0f} | "
                f"spent=${total_spent:.2f} | pnl=${pnl:.2f}"
            )

        elif is_orphaned:
            side = "UP" if pos.up_qty > 0 else "DOWN"
            qty = pos.up_qty if pos.up_qty > 0 else pos.down_qty
            avg = pos.up_avg_price if pos.up_qty > 0 else pos.down_avg_price

            if result == f"resolved_{side.lower()}":
                pnl = qty * 1.00 - qty * avg  # Won
            elif result.startswith("resolved_"):
                pnl = 0 - qty * avg  # Lost
            else:
                pnl = None  # Unknown

            pos.exit_reason = "orphaned_settlement"
            pnl_str = f"${pnl:.2f}" if pnl is not None else "UNKNOWN"
            self._logger.info(
                f"ORPHANED {side} settlement: {pos.slug} | result={result} | "
                f"qty={qty:.0f}@${avg:.3f} | pnl={pnl_str}"
            )

        else:
            pnl = 0.0
            pos.exit_reason = "empty_settlement"

        pos.pnl = pnl

        # Credit simulated balance in dry-run mode
        if self._dry_run and pnl is not None:
            if is_hedged:
                # Hedged: one side pays $1.00 per matched share
                self._balance.sim_credit(min(pos.up_qty, pos.down_qty) * 1.00)
            elif is_orphaned:
                qty = pos.up_qty if pos.up_qty > 0 else pos.down_qty
                avg = pos.up_avg_price if pos.up_qty > 0 else pos.down_avg_price
                if pnl > 0:
                    self._balance.sim_credit(qty * 1.00)  # Won: full payout

        # Record settlement for dashboard
        if pnl is not None:
            self._settlements.append({
                "slug": pos.slug,
                "exit_reason": pos.exit_reason,
                "matched": min(pos.up_qty, pos.down_qty) if is_hedged else 0,
                "up_qty": pos.up_qty,
                "down_qty": pos.down_qty,
                "up_avg": pos.up_avg_price,
                "down_avg": pos.down_avg_price,
                "pair_cost": pos.pair_cost,
                "pnl": round(pnl, 2),
                "timestamp": time.time(),
            })
            # Keep last 100
            if len(self._settlements) > 100:
                self._settlements = self._settlements[-100:]
            self._accum_total_pnl += pnl
            if pos.exit_reason == "hedged_settlement":
                self._accum_hedged_count += 1
                self._consecutive_unwinds = 0  # Reset on success
            elif pos.exit_reason == "orphaned_settlement":
                self._accum_orphaned_count += 1
            self._save_circuit_breaker_state()

        # Record cycle to metrics DB for adaptive tuner
        if self._metrics and pnl is not None:
            from .accumulator_metrics import CycleRecord
            entry_ts = pos.entry_time.timestamp() if pos.entry_time else time.time() - 60
            self._metrics.record_cycle(CycleRecord(
                slug=pos.slug,
                started_at=entry_ts,
                ended_at=time.time(),
                up_qty=pos.up_qty,
                down_qty=pos.down_qty,
                up_avg_price=pos.up_avg_price,
                down_avg_price=pos.down_avg_price,
                pair_cost=pos.pair_cost,
                pnl=pnl,
                exit_reason=pos.exit_reason or "unknown",
                reprices_used=pos.reprice_count,
                fill_time_secs=time.time() - entry_ts,
                hedge_time_secs=time.time() - entry_ts if is_hedged else 0.0,
                spread_at_entry=self._best_bid_pair,
            ))

        # === AUTO-REDEMPTION: queue winning tokens for on-chain redemption ===
        if self._redeemer and pos.exit_reason == "hedged_settlement" and pos.condition_id:
            from .types import RedemptionEvent
            event = RedemptionEvent(
                condition_id=pos.condition_id,
                slug=pos.slug,
                winning_side="up" if result == "resolved_up" else "down",
                shares=min(pos.up_qty, pos.down_qty),
                settled_at=time.time(),
            )
            self._redeemer.enqueue(event)
            self._logger.info(f"Redemption queued: {pos.slug} | {event.shares:.0f} shares | side={event.winning_side}")

        # Step 4: Remove positions from store
        for token_id in [pos.up_token_id, pos.down_token_id]:
            try:
                stored = self._store.get(token_id)
                if stored and stored.metadata.get("is_accumulator"):
                    self._store.remove(token_id)
            except Exception as e:
                self._logger.debug(f"Store cleanup for {token_id[:8]}: {e}")

        # Step 5: Remove position
        self._positions.pop(pos.slug, None)

    async def _emergency_cleanup(self, pos: AccumulatorPosition):
        """On error: cancel all orders for this position, remove it."""
        for order_id in [pos.up_order_id, pos.down_order_id]:
            if order_id:
                try:
                    await self._clob.cancel_order(order_id)
                except Exception:
                    pass
        for token_id in [pos.up_token_id, pos.down_token_id]:
            if token_id:
                self._store.remove(token_id)
        self._positions.pop(pos.slug, None)

    def _transition(self, new_state: AccumulatorState, pos: AccumulatorPosition):
        """Log state transition for a specific position."""
        old = pos.state
        pos.state = new_state
        self._logger.info(f"State: {old.value} → {new_state.value} ({pos.slug})")

    # ========================================================================
    # Market Discovery
    # ========================================================================

    async def _discover_markets(self) -> list[dict]:
        """Discover active updown markets via computed slug pattern.

        Generates slugs for configured assets × window types,
        queries Gamma API for each, returns parseable market dicts.

        Returns list of dicts with keys:
            slug, up_token_id, down_token_id, window_secs, expires_at (datetime)
        """
        now = time.time()

        # Cache check (60s TTL)
        if self._market_cache and now - self._market_cache_ts < 60:
            return self._market_cache

        if not self._session or self._session.closed:
            return []

        assets = [a.strip().lower() for a in self._config.accum_assets.split(",")]
        window_types = [w.strip() for w in self._config.accum_window_types.split(",")]

        WINDOW_SECS = {"5m": 300, "15m": 900}

        markets = []
        for asset in assets:
            for wtype in window_types:
                window = WINDOW_SECS.get(wtype)
                if not window:
                    continue

                ts_rounded = int(now // window) * window

                for offset in range(4):
                    ts = ts_rounded + (offset * window)
                    slug = f"{asset}-updown-{wtype}-{ts}"

                    try:
                        async with self._session.get(
                            f"{GAMMA_API_URL}/markets",
                            params={"slug": slug},
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as resp:
                            if resp.status != 200:
                                continue
                            data = await resp.json()

                        if not data:
                            continue

                        m = data[0]

                        if m.get("closed") or not m.get("active"):
                            continue

                        # Parse clobTokenIds (JSON string inside JSON)
                        raw_tokens = m.get("clobTokenIds", "[]")
                        if isinstance(raw_tokens, str):
                            clob_token_ids = json.loads(raw_tokens)
                        else:
                            clob_token_ids = raw_tokens

                        if len(clob_token_ids) != 2:
                            continue

                        # Parse outcomes
                        raw_outcomes = m.get("outcomes", "[]")
                        if isinstance(raw_outcomes, str):
                            outcomes = json.loads(raw_outcomes)
                        else:
                            outcomes = raw_outcomes

                        if len(outcomes) != 2:
                            continue

                        # Parse endDate → datetime
                        end_str = m.get("endDate", "")
                        if not end_str:
                            continue
                        try:
                            if end_str.endswith("Z"):
                                end_str = end_str[:-1] + "+00:00"
                            end_dt = datetime.fromisoformat(end_str)
                        except (ValueError, TypeError):
                            continue

                        # Skip markets expiring within min_secs_remaining
                        secs_left = (end_dt - datetime.now(tz=timezone.utc)).total_seconds()
                        if secs_left < self._config.accum_min_secs_remaining:
                            continue

                        # Determine UP vs DOWN token indices
                        up_idx, down_idx = 0, 1
                        for i, o in enumerate(outcomes):
                            ol = o.lower() if isinstance(o, str) else ""
                            if "up" in ol or "yes" in ol:
                                up_idx = i
                            elif "down" in ol or "no" in ol:
                                down_idx = i

                        markets.append({
                            "slug": slug,
                            "asset": asset,
                            "window_type": wtype,
                            "up_token_id": clob_token_ids[up_idx],
                            "down_token_id": clob_token_ids[down_idx],
                            "window_secs": window,
                            "expires_at": end_dt,
                            "condition_id": m.get("conditionId", ""),
                        })

                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        self._logger.debug(f"Gamma lookup failed for {slug}: {e}")
                        continue

        self._market_cache = markets
        self._market_cache_ts = now
        return markets

    # ========================================================================
    # Entry Evaluation
    # ========================================================================

    def _evaluate_opportunity(self, up_price: float, down_price: float) -> bool:
        """Should we enter this market?

        Maker mode: pair_cost (bid_pair) < max AND profit >= min.
        Directional guard is enforced in _evaluate_and_enter() via accum_max_side_price.
        """
        # Circuit breaker: stop entering new trades on sustained losses
        if self._circuit_tripped:
            return False
        if self._accum_total_pnl < self._daily_loss_limit:
            self._circuit_tripped = True
            self._logger.warning(
                f"CIRCUIT BREAKER: PnL ${self._accum_total_pnl:.2f} < ${self._daily_loss_limit:.2f} limit. "
                f"No new trades until restart."
            )
            return False
        if self._consecutive_unwinds >= self._max_consecutive_unwinds:
            self._circuit_tripped = True
            self._logger.warning(
                f"CIRCUIT BREAKER: {self._consecutive_unwinds} consecutive unwinds. "
                f"No new trades until restart."
            )
            return False

        pair_cost = up_price + down_price

        if pair_cost >= self._get_param('accum_max_pair_cost'):
            return False

        profit_per_share = 1.00 - pair_cost
        if profit_per_share < self._get_param('accum_min_profit_per_share'):
            return False

        return True

    # ========================================================================
    # Order Placement
    # ========================================================================

    async def _place_maker_order(self, token_id: str, price: float, qty: float) -> Optional[str]:
        """Place post-only maker order. Returns order_id or None."""
        if self._dry_run:
            self._logger.info(
                f"[DRY RUN] Would BUY {qty:.0f} shares @ ${price:.3f} "
                f"token={token_id[:8]}..."
            )
            return f"dry_run_{int(time.time())}"

        try:
            result = await self._clob.place_order(
                token_id=token_id,
                price=price,
                size=qty,
                side="BUY",
                post_only=True,
            )
            if result.success:
                return result.order_id
        except Exception as e:
            self._logger.warning(f"Maker order failed: {e}")

        return None

    # ========================================================================
    # Non-Blocking Order Management
    # ========================================================================

    def _log_eval_heartbeat(self, pos: AccumulatorPosition, secs_left: float):
        """Log a throttled INFO heartbeat when stuck in SCANNING (every 30s)."""
        now = time.time()
        if now - self._last_eval_log_ts >= 30:
            self._logger.info(
                f"Eval blocked: {pos.slug} | {secs_left:.0f}s left | {self._eval_block_reason}"
            )
            self._last_eval_log_ts = now

    def _get_resting_order(self, pos: AccumulatorPosition):
        """Return (side, order_id, order_time) for any resting order."""
        if pos.up_order_id:
            return ("UP", pos.up_order_id, pos.up_order_time)
        if pos.down_order_id:
            return ("DOWN", pos.down_order_id, pos.down_order_time)
        return (None, None, None)

    def _side_price(self, pos: AccumulatorPosition, side: str) -> float:
        """Get pending order price for a given side."""
        if side == "UP":
            return pos.pending_up_price if pos.pending_up_price > 0 else pos.pending_order_price
        return pos.pending_down_price if pos.pending_down_price > 0 else pos.pending_order_price

    def _set_side_price(self, pos: AccumulatorPosition, side: str, price: float):
        """Set pending order price for a given side."""
        if side == "UP":
            pos.pending_up_price = price
        else:
            pos.pending_down_price = price
        pos.pending_order_price = price  # backward compat

    def _side_reprice_count(self, pos: AccumulatorPosition, side: str) -> int:
        """Get reprice count for a given side."""
        if side == "UP":
            return pos.up_reprice_count
        return pos.down_reprice_count

    def _inc_side_reprice(self, pos: AccumulatorPosition, side: str):
        """Increment reprice count for a given side and sync to legacy field."""
        if side == "UP":
            pos.up_reprice_count += 1
        else:
            pos.down_reprice_count += 1
        pos.reprice_count = max(pos.up_reprice_count, pos.down_reprice_count)

    async def _check_and_handle_order(
        self, pos: AccumulatorPosition, side: str, order_id: str, order_time: Optional[datetime]
    ) -> str:
        """Check resting order status and handle appropriately.

        Returns: "filled", "waiting", "repriced", "abandoned", "error"
        """
        price = self._side_price(pos, side)
        if self._dry_run:
            self._apply_fill(pos, side, price, pos.target_shares, order_id)
            self._clear_order(pos, side)
            self._orders_filled += 1
            return "filled"

        try:
            status = await self._clob.get_order_status(order_id)
        except Exception:
            return "error"

        if status in ("FILLED", "MATCHED"):
            # Fetch actual fill price/size from CLOB (not the order placement price)
            details = await self._clob.get_order_details(order_id)
            if details and details.get("size_matched", 0) > 0:
                fill_price = details["price"]
                fill_qty = details["size_matched"]
            else:
                fill_price = price  # fallback to order price if details unavailable
                fill_qty = pos.target_shares
            self._apply_fill(pos, side, fill_price, fill_qty, order_id)
            self._clear_order(pos, side)
            self._orders_filled += 1
            return "filled"

        if status in ("CANCELLED", "FAILED"):
            self._logger.warning(f"Order {order_id[:8]} terminal: {status}")
            self._clear_order(pos, side)
            self._inc_side_reprice(pos, side)
            rc = self._side_reprice_count(pos, side)
            if rc >= self._get_param('accum_reprice_limit'):
                self._logger.warning(
                    f"Reprice limit ({self._get_param('accum_reprice_limit')}) reached for {pos.slug} {side}"
                )
                return "abandoned"
            return "repriced"

        # Status is LIVE — check adaptive timeout
        if order_time:
            age = (datetime.now(tz=timezone.utc) - order_time).total_seconds()
            secs_left = (pos.market_end_time - datetime.now(tz=timezone.utc)).total_seconds()
            adaptive_timeout = max(5, min(
                self._get_param('accum_order_timeout'),
                int(max(0, secs_left - 60) / max(1, self._get_param('accum_reprice_limit')))
            ))

            if age >= adaptive_timeout:
                new_order_id = await self._reprice_order(pos, side, order_id)
                if new_order_id:
                    return "repriced"
                rc = self._side_reprice_count(pos, side)
                if rc >= self._get_param('accum_reprice_limit'):
                    return "abandoned"
                return "error"

        if order_time:
            age = (datetime.now(tz=timezone.utc) - order_time).total_seconds()
            self._logger.debug(
                f"Order {order_id[:8]} LIVE | age={age:.0f}s | {side} price=${price:.3f}"
            )
        return "waiting"

    def _clear_order(self, pos: AccumulatorPosition, side: str):
        """Clear order tracking fields after fill or cancel."""
        if side == "UP":
            pos.up_order_id = None
            pos.up_order_time = None
        else:
            pos.down_order_id = None
            pos.down_order_time = None

    async def _cancel_resting_orders(self, pos: AccumulatorPosition):
        """Cancel any resting orders on the position."""
        for attr in ("up_order_id", "down_order_id"):
            oid = getattr(pos, attr)
            if oid:
                try:
                    await self._clob.cancel_order(oid)
                except Exception:
                    pass
                setattr(pos, attr, None)

    async def _reprice_order(
        self, pos: AccumulatorPosition, side: str, old_order_id: str
    ) -> Optional[str]:
        """Cancel old order, fetch fresh book, reprice ascending toward ask.

        Returns new order_id or None.
        """
        # 1. Cancel old order
        try:
            await self._clob.cancel_order(old_order_id)
        except Exception:
            pass
        self._clear_order(pos, side)
        self._orders_timed_out += 1

        # 2. Increment per-side reprice count and check limit
        self._inc_side_reprice(pos, side)
        rc = self._side_reprice_count(pos, side)
        limit_n = int(self._get_param('accum_reprice_limit'))
        if rc >= limit_n:
            self._logger.warning(f"Reprice limit ({limit_n}) reached for {pos.slug} {side}")
            return None

        # 3. Fetch FRESH order book
        token_id = pos.up_token_id if side == "UP" else pos.down_token_id
        try:
            book = await self._clob.get_order_book(token_id)
        except Exception:
            return None

        bids = book.get("bids", [])
        asks = book.get("asks", [])
        if not bids or not asks:
            return None

        best_bid = float(bids[0]["price"])
        best_ask = float(asks[0]["price"])

        # 4. Calculate ascending reprice: best_bid + (side_reprice_count * tick)
        tick = self._config.accum_maker_price_decrement
        new_price = best_bid + (rc * tick)

        # 5. Cap at best_ask - buffer (NEVER cross spread)
        ask_buffer = max(tick, 0.003)
        max_price = best_ask - ask_buffer
        if new_price >= max_price:
            new_price = max_price

        # 5a. Same-price guard
        old_price = self._side_price(pos, side)
        if abs(new_price - old_price) < 0.001:
            self._logger.warning(f"Reprice #{rc}: {side} same price ${new_price:.3f}, abandoning")
            if side == "UP":
                pos.up_reprice_count = limit_n
            else:
                pos.down_reprice_count = limit_n
            pos.reprice_count = max(pos.up_reprice_count, pos.down_reprice_count)
            return None

        # 5b. Pair-cost guard: check against filled avg OR pending price of other side
        if side == "UP":
            other_price = pos.down_avg_price if pos.down_qty > 0 else pos.pending_down_price
        else:
            other_price = pos.up_avg_price if pos.up_qty > 0 else pos.pending_up_price
        if other_price > 0:
            estimated_pair = new_price + other_price
            pair_limit = min(self._get_param('accum_max_pair_cost'), 1.0)
            if estimated_pair > pair_limit:
                self._logger.warning(
                    f"Reprice #{rc}: {side} pair_cost ${estimated_pair:.4f} "
                    f"(other=${other_price:.3f} + reprice=${new_price:.3f}) "
                    f"> limit ${pair_limit}, abandoning"
                )
                if side == "UP":
                    pos.up_reprice_count = limit_n
                else:
                    pos.down_reprice_count = limit_n
                pos.reprice_count = max(pos.up_reprice_count, pos.down_reprice_count)
                return None

        # 5c. Directional guard: abandon if reprice tracks into skewed territory
        max_side = self._config.accum_max_side_price
        min_side = 1.0 - max_side
        skewed = new_price > max_side or (side == "UP" and new_price < min_side)
        if skewed:
            self._logger.warning(
                f"Reprice #{rc}: {side} price ${new_price:.3f} outside "
                f"[{min_side:.2f}, {max_side:.2f}], abandoning"
            )
            if side == "UP":
                pos.up_reprice_count = limit_n
            else:
                pos.down_reprice_count = limit_n
            pos.reprice_count = max(pos.up_reprice_count, pos.down_reprice_count)
            return None

        if new_price <= 0 or new_price >= best_ask:
            self._logger.warning(
                f"Reprice would cross spread: ${new_price:.3f} >= ask ${best_ask:.3f}"
            )
            return None

        # 6. Place repriced order
        self._logger.info(
            f"Reprice #{rc}: {side} → ${new_price:.3f} (bid={best_bid:.3f} ask={best_ask:.3f})"
        )
        new_oid = await self._place_maker_order(token_id, new_price, pos.target_shares)
        if new_oid:
            if side == "UP":
                pos.up_order_id = new_oid
                pos.up_order_time = datetime.now(tz=timezone.utc)
            else:
                pos.down_order_id = new_oid
                pos.down_order_time = datetime.now(tz=timezone.utc)
            self._set_side_price(pos, side, new_price)
            self._orders_placed += 1
            return new_oid

        # Placement failed at ceiling — abandon
        if new_price >= max_price - 0.001:
            self._logger.info(
                f"Reprice #{rc}: {side} failed at cap ${new_price:.3f} "
                f"(ask=${best_ask:.3f}), spread too tight — abandoning"
            )
            if side == "UP":
                pos.up_reprice_count = limit_n
            else:
                pos.down_reprice_count = limit_n
            pos.reprice_count = max(pos.up_reprice_count, pos.down_reprice_count)

        return None

    def _apply_fill(self, pos: AccumulatorPosition, side: str, price: float, qty: float, order_id: str):
        """Apply a fill to the AccumulatorPosition and store it."""
        # Deduct cost from simulated balance in dry-run mode
        if self._dry_run:
            self._balance.sim_deduct(qty * price)

        if side == "UP":
            old_total = pos.up_qty * pos.up_avg_price
            new_total = qty * price
            pos.up_qty += qty
            pos.up_avg_price = (old_total + new_total) / pos.up_qty if pos.up_qty > 0 else 0
        elif side == "DOWN":
            old_total = pos.down_qty * pos.down_avg_price
            new_total = qty * price
            pos.down_qty += qty
            pos.down_avg_price = (old_total + new_total) / pos.down_qty if pos.down_qty > 0 else 0

        # Update pair cost
        if pos.up_qty > 0 and pos.down_qty > 0:
            pos.pair_cost = pos.up_avg_price + pos.down_avg_price

        # Store position in PositionStore for BalanceManager tracking
        token_id = pos.up_token_id if side == "UP" else pos.down_token_id
        self._store.add(Position(
            token_id=token_id,
            slug=pos.slug,
            entry_price=price,
            entry_size=qty,
            entry_time=datetime.now(tz=timezone.utc),
            entry_tx_hash=order_id,
            market_end_time=pos.market_end_time,
            metadata={
                "is_accumulator": True,
                "accum_pair_id": f"accum_{pos.slug}",
                "accum_side": side,
            },
        ))

        self._logger.info(
            f"Fill: {side} {qty:.0f} @ ${price:.3f} | "
            f"UP: {pos.up_qty:.0f}@${pos.up_avg_price:.3f} | "
            f"DOWN: {pos.down_qty:.0f}@${pos.down_avg_price:.3f} | "
            f"pair_cost=${pos.pair_cost:.4f}"
        )

    # ========================================================================
    # Settlement
    # ========================================================================

    async def _wait_for_settlement(self, pos: AccumulatorPosition) -> str:
        """Wait for market to resolve after window expiry.

        Returns: "resolved_up", "resolved_down", or "timeout"
        """
        deadline = datetime.now(tz=timezone.utc) + timedelta(
            seconds=self._config.accum_settle_timeout_secs
        )

        while datetime.now(tz=timezone.utc) < deadline:
            try:
                up_mid = await self._clob.get_midpoint(pos.up_token_id)
                down_mid = await self._clob.get_midpoint(pos.down_token_id)

                # Skip if either is 0.0 (no data available)
                if up_mid <= 0 or down_mid <= 0:
                    await asyncio.sleep(5)
                    continue

                if up_mid >= 0.95 and down_mid <= 0.05:
                    return "resolved_up"
                if down_mid >= 0.95 and up_mid <= 0.05:
                    return "resolved_down"
            except Exception:
                pass

            await asyncio.sleep(5)

        # Timeout — check Gamma API as fallback
        if self._session and not self._session.closed:
            try:
                async with self._session.get(
                    f"{GAMMA_API_URL}/markets",
                    params={"slug": pos.slug},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and data[0].get("closed"):
                            return "resolved_unknown"
            except Exception:
                pass

        return "timeout"
