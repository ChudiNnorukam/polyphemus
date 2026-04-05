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
from typing import Optional, TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    from .performance_db import PerformanceDB

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
        self._effective_dry_run = bool(config.dry_run or config.accum_dry_run)
        if bool(config.dry_run) != bool(config.accum_dry_run):
            raise ValueError(
                "Accumulator requires DRY_RUN and ACCUM_DRY_RUN to match. "
                f"Got DRY_RUN={config.dry_run} ACCUM_DRY_RUN={config.accum_dry_run}."
            )
        self._dry_run = self._effective_dry_run
        self._logger = setup_logger("polyphemus.accumulator")
        self._perf_db: "Optional[PerformanceDB]" = None

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
        self._last_eval_block_reason: str = ""
        self._last_candidate_slug: str = ""
        self._last_candidate_bid_pair: float = 0.0
        self._candidates_seen: int = 0
        self._candidates_rejected: int = 0

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
        self._max_consecutive_unwinds = 5
        self._daily_loss_limit = -abs(self._config.max_daily_loss)
        self._circuit_tripped = False
        self._cb_state_path = os.path.join(self._config.lagbot_data_dir, "circuit_breaker.json")
        self._legacy_cb_state_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "data", "circuit_breaker.json"
        )
        self._load_circuit_breaker_state()

    def _load_circuit_breaker_state(self):
        """Load persisted circuit breaker PnL from disk."""
        loaded_from = None
        state = None
        for candidate in (self._cb_state_path, self._legacy_cb_state_path):
            try:
                with open(candidate, "r") as f:
                    state = json.load(f)
                loaded_from = candidate
                break
            except FileNotFoundError:
                continue
            except Exception as e:
                self._logger.warning(f"Failed to load circuit breaker state from {candidate}: {e}")

        if not state:
            return

        self._accum_total_pnl = state.get("total_pnl", 0.0)
        self._accum_hedged_count = state.get("hedged_count", 0)
        self._accum_unwound_count = state.get("unwound_count", 0)
        self._consecutive_unwinds = state.get("consecutive_unwinds", 0)
        self._accum_orphaned_count = state.get("orphaned_count", 0)
        if self._accum_total_pnl < self._daily_loss_limit:
            self._circuit_tripped = True
            self._logger.warning(
                f"Circuit breaker TRIPPED on load: PnL ${self._accum_total_pnl:.2f}"
            )
        self._logger.info(
            f"Loaded circuit breaker state: pnl=${self._accum_total_pnl:.2f} "
            f"hedged={self._accum_hedged_count} unwound={self._accum_unwound_count}"
        )
        if loaded_from == self._legacy_cb_state_path and self._legacy_cb_state_path != self._cb_state_path:
            self._logger.warning(
                f"Migrating legacy circuit breaker state into instance data dir: {self._cb_state_path}"
            )
            self._save_circuit_breaker_state()

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
        assets = [a.strip().upper() for a in self._config.accum_assets.split(",") if a.strip()]
        window_types = [w.strip() for w in self._config.accum_window_types.split(",") if w.strip()]
        positions = []
        for pos in self._positions.values():
            positions.append({
                "slug": pos.slug,
                "state": pos.state.value,
                "up_qty": pos.up_qty,
                "down_qty": pos.down_qty,
                "pair_cost": round(pos.pair_cost, 4),
                "up_fee_paid": round(pos.up_fee_paid, 4),
                "down_fee_paid": round(pos.down_fee_paid, 4),
                "reprice_count": pos.reprice_count,
                "is_hedged": pos.is_fully_hedged,
            })
        return {
            "state": self._get_aggregate_state(),
            "entry_mode": self._accum_entry_mode(),
            "assets": assets,
            "window_types": window_types,
            "active_positions": len(self._positions),
            "max_concurrent": self._config.accum_max_concurrent,
            "daily_loss_limit": round(self._daily_loss_limit, 2),
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
            "consecutive_unwinds": self._consecutive_unwinds,
            "circuit_tripped": self._circuit_tripped,
            "last_candidate_slug": self._last_candidate_slug,
            "last_candidate_bid_pair": round(self._last_candidate_bid_pair, 4),
            "last_eval_block_reason": self._last_eval_block_reason,
            "candidates_seen": self._candidates_seen,
            "candidates_rejected": self._candidates_rejected,
            "accum_dry_run": bool(self._config.accum_dry_run),
            "effective_accumulator_dry_run": bool(self._dry_run),
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

            max_side = self._get_param('accum_max_side_price')
            if best_bid_up > max_side or best_bid_down > max_side:
                continue

            if self._evaluate_opportunity(best_bid_up, best_bid_down):
                self._candidates_seen += 1
                self._last_candidate_slug = market["slug"]
                self._last_candidate_bid_pair = bid_pair_cost
                self._logger.info(
                    f"Candidate found: {market['slug']} | "
                    f"up_bid={best_bid_up:.3f} down_bid={best_bid_down:.3f} "
                    f"bid_pair={bid_pair_cost:.4f}"
                )
                entered = await self._start_candidate_position(market)
                if entered:
                    return  # One new active opportunity per scan cycle
                self._candidates_rejected += 1
                self._logger.info(
                    f"Candidate rejected before activation: {market['slug']} | "
                    f"reason={self._last_eval_block_reason or 'unknown'}"
                )

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

    async def _start_candidate_position(self, market: dict) -> bool:
        """Attempt to activate a discovered candidate into a live tracked position."""
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
        entered = await self._evaluate_and_enter(pos)
        if entered:
            self._positions[market["slug"]] = pos
        return entered

    def _accum_entry_mode(self) -> str:
        """Return the configured accumulator entry mode."""
        return str(getattr(self._config, "accum_entry_mode", "maker")).strip().lower()

    @staticmethod
    def _estimate_taker_fee_per_share(price: float) -> float:
        """Estimate Polymarket taker fee per share at a given fill price."""
        return price * (1.0 - price) * 0.0624

    async def _fetch_fill_details(
        self,
        order_id: str,
        fallback_price: float,
        fallback_qty: float,
    ) -> tuple[float, float]:
        """Return actual fill price and matched size for a completed order."""
        if not order_id:
            return fallback_price, fallback_qty
        try:
            details = await self._clob.get_order_details(order_id)
        except Exception:
            return fallback_price, fallback_qty
        if not details:
            return fallback_price, fallback_qty
        fill_price = float(details.get("price") or details.get("average_price") or fallback_price)
        fill_qty = float(details.get("size_matched") or fallback_qty)
        return fill_price, fill_qty

    def _register_filled_side(
        self,
        pos: AccumulatorPosition,
        side: str,
        price: float,
        qty: float,
        order_id: str,
    ):
        """Persist a filled accumulator leg into the in-memory position."""
        if qty <= 0:
            return
        fee_paid = self._estimate_taker_fee_per_share(price) * qty
        self._apply_fill(pos, side, price, qty, order_id)
        if side == "UP":
            pos.up_fee_paid += fee_paid
        else:
            pos.down_fee_paid += fee_paid

    async def _evaluate_and_enter_fak(
        self,
        pos: AccumulatorPosition,
        secs_left: float,
        up_book: dict,
        down_book: dict,
    ) -> bool:
        """Enter the pair with immediate taker fills when fee-adjusted edge remains positive."""
        up_asks = up_book.get("asks", [])
        down_asks = down_book.get("asks", [])
        if not up_asks or not down_asks:
            self._eval_block_reason = f"empty_asks: up_asks={len(up_asks)} down_asks={len(down_asks)}"
            self._last_eval_block_reason = self._eval_block_reason
            self._log_eval_heartbeat(pos, secs_left)
            return False

        up_ask = float(up_asks[0]["price"])
        down_ask = float(down_asks[0]["price"])

        max_side = self._get_param('accum_max_side_price')
        if up_ask > max_side or down_ask > max_side:
            self._eval_block_reason = (
                f"directional: up_ask={up_ask:.3f} down_ask={down_ask:.3f} "
                f"max_side=${max_side:.2f}"
            )
            self._last_eval_block_reason = self._eval_block_reason
            self._log_eval_heartbeat(pos, secs_left)
            return False

        up_fee = self._estimate_taker_fee_per_share(up_ask)
        down_fee = self._estimate_taker_fee_per_share(down_ask)
        total_pair_cost = up_ask + down_ask + up_fee + down_fee
        max_pair = self._get_param('accum_max_pair_cost')
        if total_pair_cost >= max_pair:
            self._eval_block_reason = (
                f"ask_pair_expensive: ${total_pair_cost:.4f} >= ${max_pair} "
                f"(up_ask={up_ask:.3f} down_ask={down_ask:.3f} fees=${up_fee + down_fee:.4f})"
            )
            self._last_eval_block_reason = self._eval_block_reason
            self._log_eval_heartbeat(pos, secs_left)
            return False

        profit_per_share = 1.00 - total_pair_cost
        min_profit = self._get_param('accum_min_profit_per_share')
        if profit_per_share < min_profit:
            self._eval_block_reason = (
                f"profit_too_low: ${profit_per_share:.4f} < ${min_profit:.4f} "
                f"(ask_pair=${total_pair_cost:.4f})"
            )
            self._last_eval_block_reason = self._eval_block_reason
            self._log_eval_heartbeat(pos, secs_left)
            return False

        available = await self._balance.get_available_for_accumulator()
        gross_pair_cost = up_ask + down_ask
        if available < self._config.accum_min_shares * gross_pair_cost:
            self._eval_block_reason = f"insufficient_capital: ${available:.2f}"
            self._last_eval_block_reason = self._eval_block_reason
            self._log_eval_heartbeat(pos, secs_left)
            return False

        target_shares = min(available / gross_pair_cost, self._config.accum_max_shares)
        target_shares = max(target_shares, self._config.accum_min_shares)
        pos.target_shares = target_shares

        up_amount = round(target_shares * up_ask, 2)
        down_amount = round(target_shares * down_ask, 2)
        self._logger.info(
            f"FAK entry attempt: {pos.slug} | "
            f"up_ask={up_ask:.3f} down_ask={down_ask:.3f} "
            f"fee_pair=${up_fee + down_fee:.4f} total_pair=${total_pair_cost:.4f} | "
            f"{target_shares:.0f}sh | ${up_amount + down_amount:.2f} gross"
        )

        if self._dry_run:
            self._register_filled_side(pos, "UP", up_ask, target_shares, f"dry_up_{int(time.time())}")
            self._register_filled_side(pos, "DOWN", down_ask, target_shares, f"dry_down_{int(time.time())}")
            pos.pair_cost = pos.up_avg_price + pos.down_avg_price + (
                (pos.up_fee_paid + pos.down_fee_paid) / max(target_shares, 1.0)
            )
            self._orders_placed += 2
            self._orders_filled += 2
            self._transition(AccumulatorState.ACCUMULATING, pos)
            self._last_eval_block_reason = ""
            return True

        up_result, down_result = await asyncio.gather(
            self._clob.place_fak_order(pos.up_token_id, up_amount, "BUY", price_hint=up_ask),
            self._clob.place_fak_order(pos.down_token_id, down_amount, "BUY", price_hint=down_ask),
        )
        self._orders_placed += (1 if up_result.success else 0) + (1 if down_result.success else 0)

        up_price = up_ask
        up_qty = 0.0
        if up_result.success:
            up_price, up_qty = await self._fetch_fill_details(up_result.order_id, up_ask, target_shares)
            if up_qty > 0:
                self._register_filled_side(pos, "UP", up_price, up_qty, up_result.order_id)
                self._orders_filled += 1

        down_price = down_ask
        down_qty = 0.0
        if down_result.success:
            down_price, down_qty = await self._fetch_fill_details(down_result.order_id, down_ask, target_shares)
            if down_qty > 0:
                self._register_filled_side(pos, "DOWN", down_price, down_qty, down_result.order_id)
                self._orders_filled += 1

        if up_qty <= 0 and down_qty <= 0:
            self._eval_block_reason = "both_fak_placements_failed"
            self._last_eval_block_reason = self._eval_block_reason
            self._log_eval_heartbeat(pos, secs_left)
            return False

        if pos.up_qty > 0 and pos.down_qty > 0:
            pos.pair_cost = (
                pos.up_avg_price
                + pos.down_avg_price
                + ((pos.up_fee_paid + pos.down_fee_paid) / max(min(pos.up_qty, pos.down_qty), 1.0))
            )
            self._logger.info(
                f"FAK entry filled both legs: {pos.slug} | "
                f"up={pos.up_qty:.2f}@${pos.up_avg_price:.3f} "
                f"down={pos.down_qty:.2f}@${pos.down_avg_price:.3f} | "
                f"fee_pair=${pos.up_fee_paid + pos.down_fee_paid:.4f}"
            )
            self._transition(AccumulatorState.ACCUMULATING, pos)
            self._last_eval_block_reason = ""
            return True

        filled_side = "UP" if pos.up_qty > 0 else "DOWN"
        self._logger.warning(
            f"FAK entry orphaned immediately: {pos.slug} | "
            f"{filled_side} filled, other leg empty — trying taker hedge fallback now"
        )
        pos.first_fill_time = datetime.now(tz=timezone.utc)
        if await self._try_fok_fallback(pos):
            self._last_eval_block_reason = ""
            return True

        await self._unwind_orphan(pos, filled_side)
        self._last_eval_block_reason = "fak_orphan_unwound"
        return False

    async def _evaluate_and_enter(self, pos: AccumulatorPosition) -> bool:
        """SCANNING: Enter a pair opportunity using the configured accumulator mode.

        `maker`: rest both sides at the bid, then manage repricing/hedging asynchronously.
        `fak`: cross both sides immediately at the ask and only admit trades that remain
        profitable after taker fees.
        """
        secs_left = (pos.market_end_time - datetime.now(tz=timezone.utc)).total_seconds()

        if secs_left < self._config.accum_min_secs_remaining:
            self._logger.info(f"Window expired during scan: {pos.slug} ({secs_left:.0f}s left)")
            self._abandoned_slugs[pos.slug] = pos.market_end_time
            self._last_eval_block_reason = "window_expired_during_scan"
            return False

        # Fetch fresh books for both sides
        try:
            up_book = await self._clob.get_order_book(pos.up_token_id)
            down_book = await self._clob.get_order_book(pos.down_token_id)
        except Exception as e:
            self._eval_block_reason = f"book_fetch_error: {e}"
            self._last_eval_block_reason = self._eval_block_reason
            self._log_eval_heartbeat(pos, secs_left)
            return False

        up_bids = up_book.get("bids", [])
        down_bids = down_book.get("bids", [])
        if not up_bids or not down_bids:
            self._eval_block_reason = f"empty_books: up_bids={len(up_bids)} down_bids={len(down_bids)}"
            self._last_eval_block_reason = self._eval_block_reason
            self._log_eval_heartbeat(pos, secs_left)
            return False

        # Maker entry uses BID prices (we post at bid, wait for fill at that price)
        up_bid = float(up_bids[0]["price"])
        down_bid = float(down_bids[0]["price"])
        bid_pair = up_bid + down_bid

        if self._accum_entry_mode() == "fak":
            return await self._evaluate_and_enter_fak(pos, secs_left, up_book, down_book)

        # Directional guard
        max_side = self._get_param('accum_max_side_price')
        if up_bid > max_side or down_bid > max_side:
            self._eval_block_reason = (
                f"directional: up_bid={up_bid:.3f} down_bid={down_bid:.3f} "
                f"max_side=${max_side:.2f}"
            )
            self._last_eval_block_reason = self._eval_block_reason
            self._log_eval_heartbeat(pos, secs_left)
            return False

        max_pair = self._get_param('accum_max_pair_cost')
        if bid_pair >= max_pair:
            self._eval_block_reason = (
                f"bid_pair_expensive: ${bid_pair:.4f} >= ${max_pair} "
                f"(up_bid={up_bid:.3f} down_bid={down_bid:.3f})"
            )
            self._last_eval_block_reason = self._eval_block_reason
            self._log_eval_heartbeat(pos, secs_left)
            return False

        # Circuit breaker check
        if not self._evaluate_opportunity(up_bid, down_bid):
            self._eval_block_reason = f"circuit_breaker or profit_too_low: bid_pair={bid_pair:.4f}"
            self._last_eval_block_reason = self._eval_block_reason
            self._log_eval_heartbeat(pos, secs_left)
            return False

        # Capital check
        available = await self._balance.get_available_for_accumulator()
        if available < self._config.accum_min_shares * bid_pair:
            self._eval_block_reason = f"insufficient_capital: ${available:.2f}"
            self._last_eval_block_reason = self._eval_block_reason
            self._log_eval_heartbeat(pos, secs_left)
            return False

        target_shares = min(available / bid_pair, self._config.accum_max_shares)
        target_shares = max(target_shares, self._config.accum_min_shares)
        pos.target_shares = target_shares

        self._logger.info(
            f"Maker entry attempt: {pos.slug} | "
            f"up_bid={up_bid:.3f} down_bid={down_bid:.3f} bid_pair={bid_pair:.4f} | "
            f"{target_shares:.0f}sh | ${target_shares * bid_pair:.2f} total"
        )

        # Place BOTH maker orders simultaneously
        up_id, down_id = await asyncio.gather(
            self._place_maker_order(pos.up_token_id, up_bid, target_shares),
            self._place_maker_order(pos.down_token_id, down_bid, target_shares),
        )

        if not up_id and not down_id:
            self._eval_block_reason = "both_maker_placements_failed"
            self._last_eval_block_reason = self._eval_block_reason
            self._log_eval_heartbeat(pos, secs_left)
            return False

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
        self._last_eval_block_reason = ""
        return True

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
                    f"waiting for second maker fill (deadline={self._config.accum_hedge_deadline_secs}s)"
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
                pos.up_fee_paid += taker_fee * target_qty
            else:
                pos.down_qty = target_qty
                pos.down_avg_price = best_ask
                pos.down_fee_paid += taker_fee * target_qty
            pos.pair_cost = pos.up_avg_price + pos.down_avg_price + (
                (pos.up_fee_paid + pos.down_fee_paid) / max(target_qty, 1.0)
            )
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
            pos.up_fee_paid += taker_fee * est_shares
        else:
            pos.down_qty = est_shares
            pos.down_avg_price = best_ask
            pos.down_fee_paid += taker_fee * est_shares
        pos.pair_cost = pos.up_avg_price + pos.down_avg_price + (
            (pos.up_fee_paid + pos.down_fee_paid) / max(min(pos.up_qty, pos.down_qty), 1.0)
        )
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
        """Aggressive sellback of orphaned leg. NEVER hold to resolution.

        Escalation ladder:
        1. FAK SELL at market (instant, best price)
        2. FAK SELL retry x2 with 3s gaps
        3. Fire sale: FAK SELL at $0.01 (recover pennies, avoid coin flip)
        4. Only if market expired AND can't sell: forced hold (logged CRITICAL)
        """
        token_id = pos.up_token_id if filled_side == "UP" else pos.down_token_id
        qty = pos.up_qty if filled_side == "UP" else pos.down_qty
        avg_price = pos.up_avg_price if filled_side == "UP" else pos.down_avg_price
        entry_fee_paid = pos.up_fee_paid if filled_side == "UP" else pos.down_fee_paid

        if qty < 5:
            self._logger.warning(f"Sellback skip: {filled_side} qty={qty:.0f} below minimum")
            self._store.remove(token_id)
            self._drop_position(pos, "sellback_skipped_below_min")
            return

        # If market expired, can't sell on secondary. This is the ONE hold case.
        secs_left = (pos.market_end_time - datetime.now(tz=timezone.utc)).total_seconds()
        if secs_left <= 0:
            forced_loss = avg_price * qty + entry_fee_paid
            self._logger.critical(
                f"FORCED HOLD (market expired): {pos.slug} | {filled_side} {qty:.0f}@${avg_price:.3f} | "
                f"CANNOT SELL — market closed. Worst-case loss=${forced_loss:.2f} "
                f"(includes entry fees ${entry_fee_paid:.2f})"
            )
            self._accum_total_pnl -= forced_loss
            self._consecutive_unwinds += 1
            self._accum_orphaned_count += 1
            self._save_circuit_breaker_state()
            self._drop_position(pos, "forced_hold_market_expired")
            self._record_db_trade(pos, -forced_loss, "forced_hold_expired")
            return

        self._logger.info(
            f"SELLBACK: {pos.slug} | {filled_side} {qty:.0f}@${avg_price:.3f} | "
            f"{secs_left:.0f}s remaining | attempting aggressive sell"
        )

        sell_success = False
        sell_price = 0.0
        unwind_loss = 0.0

        if self._dry_run:
            sell_price = max(avg_price - 0.02, 0.01)
            sell_fee_paid = self._estimate_taker_fee_per_share(sell_price) * qty
            unwind_loss = (avg_price - sell_price) * qty + entry_fee_paid + sell_fee_paid
            sell_success = True
            self._consecutive_unwinds += 1
            self._balance.sim_credit(sell_price * qty)
            self._logger.info(
                f"[DRY RUN] SELLBACK: {pos.slug} | {filled_side} {qty:.0f}sh | "
                f"entry=${avg_price:.3f} sold@${sell_price:.3f} | "
                f"fees=${entry_fee_paid + sell_fee_paid:.2f} | cost=${unwind_loss:.2f}"
            )
        else:
            # Wait for CLOB to index tokens (5-30s after fill)
            await asyncio.sleep(3)
            sell_qty = 0.0
            for check in range(1, 5):
                clob_qty = await self._clob.get_share_balance(token_id)
                if clob_qty >= 1.0:
                    sell_qty = clob_qty
                    self._logger.info(f"Sellback: CLOB confirmed {clob_qty:.0f}sh (check {check}/4)")
                    break
                if check < 4:
                    await asyncio.sleep(3)

            if sell_qty < 1.0:
                forced_loss = avg_price * qty + entry_fee_paid
                self._logger.critical(
                    f"SELLBACK FAILED: CLOB never indexed tokens for {pos.slug} {filled_side}. "
                    f"Forced hold — worst-case loss=${forced_loss:.2f} "
                    f"(includes entry fees ${entry_fee_paid:.2f})"
                )
                self._accum_total_pnl -= forced_loss
                self._consecutive_unwinds += 1
                self._accum_orphaned_count += 1
                self._save_circuit_breaker_state()
                self._drop_position(pos, "forced_hold_clob_unindexed")
                self._record_db_trade(pos, -forced_loss, "forced_hold_clob_unindexed")
                return

            # Escalation ladder: FAK SELL x3, then fire sale
            for attempt in range(1, 5):
                secs_left = (pos.market_end_time - datetime.now(tz=timezone.utc)).total_seconds()
                if secs_left <= 0:
                    self._logger.critical(
                        f"SELLBACK ABORTED: {pos.slug} expired during sell attempts. Forced hold."
                    )
                    break

                try:
                    result = await self._clob.place_fak_order(
                        token_id=token_id, amount=sell_qty, side="SELL", price_hint=0
                    )
                    if result.success:
                        # Get actual fill price from order details if available
                        actual_price = None
                        if result.order_id:
                            try:
                                details = await self._clob.get_order_details(result.order_id)
                                if details and details.get("average_price"):
                                    actual_price = float(details["average_price"])
                            except Exception as e:
                                self._logger.warning(f"Sellback fill details fetch failed: {e}")
                        if not actual_price:
                            # Fallback: conservative estimate from remaining book
                            try:
                                book = await self._clob.get_order_book(token_id)
                                bids = book.get("bids", [])
                                if len(bids) >= 2:
                                    actual_price = (float(bids[0]["price"]) + float(bids[1]["price"])) / 2
                                elif bids:
                                    actual_price = float(bids[0]["price"])
                                else:
                                    actual_price = max(avg_price - 0.05, 0.01)
                            except Exception as e:
                                self._logger.warning(f"Sellback book fetch failed, using estimate: {e}")
                                actual_price = max(avg_price - 0.03, 0.01)
                        sell_price = actual_price
                        sell_fee_paid = self._estimate_taker_fee_per_share(sell_price) * sell_qty
                        unwind_loss = (avg_price - sell_price) * sell_qty + entry_fee_paid + sell_fee_paid
                        sell_success = True
                        self._logger.info(
                            f"SELLBACK OK (attempt {attempt}): {pos.slug} | "
                            f"{filled_side} {sell_qty:.0f}sh | entry=${avg_price:.3f} "
                            f"sold@~${sell_price:.3f} | "
                            f"fees=${entry_fee_paid + sell_fee_paid:.2f} | cost=${unwind_loss:.2f}"
                        )
                        break
                    else:
                        self._logger.warning(
                            f"Sellback attempt {attempt}/4 failed: {result.error}"
                        )
                except Exception as e:
                    self._logger.warning(f"Sellback attempt {attempt}/4 exception: {e}")

                if attempt < 4:
                    await asyncio.sleep(2)

        # Record result
        if sell_success:
            self._settlements.append({
                "slug": pos.slug, "exit_reason": "sellback",
                "matched": 0, "up_qty": pos.up_qty, "down_qty": pos.down_qty,
                "up_avg": pos.up_avg_price, "down_avg": pos.down_avg_price,
                "pair_cost": 0, "pnl": round(-unwind_loss, 2), "timestamp": time.time(),
            })
            if len(self._settlements) > 100:
                self._settlements = self._settlements[-100:]
            self._accum_total_pnl -= unwind_loss
            self._accum_unwound_count += 1
            self._save_circuit_breaker_state()
            self._record_db_trade(pos, -unwind_loss, "sellback")
        else:
            # All sell attempts failed — forced hold (CRITICAL, should be rare)
            forced_hold_loss = avg_price * qty + entry_fee_paid
            self._logger.critical(
                f"ALL SELLBACK ATTEMPTS FAILED: {pos.slug} | {filled_side} {qty:.0f}@${avg_price:.3f} | "
                f"FORCED HOLD — redeemer must resolve. Worst-case loss=${forced_hold_loss:.2f} "
                f"(includes entry fees ${entry_fee_paid:.2f})"
            )
            self._accum_total_pnl -= forced_hold_loss
            self._consecutive_unwinds += 1
            self._record_db_trade(pos, -forced_hold_loss, "forced_hold_sell_failed")
            self._accum_orphaned_count += 1
            self._save_circuit_breaker_state()

        self._balance._cache_time = 0
        self._store.remove(token_id)
        self._abandoned_slugs[pos.slug] = pos.market_end_time
        self._drop_position(pos, f"sellback_complete:{filled_side}")

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
            total_spent = (
                (pos.up_qty * pos.up_avg_price)
                + (pos.down_qty * pos.down_avg_price)
                + pos.up_fee_paid
                + pos.down_fee_paid
            )
            pnl = matched * 1.00 - total_spent
            pos.exit_reason = "hedged_settlement"
            self._logger.info(
                f"HEDGED settlement: {pos.slug} | matched={matched:.0f} | "
                f"spent=${total_spent:.2f} (fees=${pos.up_fee_paid + pos.down_fee_paid:.2f}) | pnl=${pnl:.2f}"
            )

        elif is_orphaned:
            side = "UP" if pos.up_qty > 0 else "DOWN"
            qty = pos.up_qty if pos.up_qty > 0 else pos.down_qty
            avg = pos.up_avg_price if pos.up_qty > 0 else pos.down_avg_price
            fee_paid = pos.up_fee_paid if pos.up_qty > 0 else pos.down_fee_paid

            if result == f"resolved_{side.lower()}":
                pnl = qty * 1.00 - qty * avg - fee_paid  # Won
            elif result.startswith("resolved_"):
                pnl = 0 - qty * avg - fee_paid  # Lost
            else:
                pnl = None  # Unknown

            pos.exit_reason = "orphaned_settlement"
            pnl_str = f"${pnl:.2f}" if pnl is not None else "UNKNOWN"
            self._logger.info(
                f"ORPHANED {side} settlement: {pos.slug} | result={result} | "
                f"qty={qty:.0f}@${avg:.3f} | fees=${fee_paid:.2f} | pnl={pnl_str}"
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

        # Step 5: Record to performance DB
        if pnl is not None:
            self._record_db_trade(pos, pnl, pos.exit_reason or "unknown")

        # Step 6: Remove position
        self._drop_position(pos, f"settlement_complete:{pos.exit_reason or 'unknown'}")

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
        self._drop_position(pos, "emergency_cleanup")

    def _transition(self, new_state: AccumulatorState, pos: AccumulatorPosition):
        """Log state transition for a specific position."""
        old = pos.state
        pos.state = new_state
        self._logger.info(f"State: {old.value} → {new_state.value} ({pos.slug})")

    def _drop_position(self, pos: AccumulatorPosition, reason: str):
        """Remove a tracked position with an explicit terminal reason."""
        if pos.slug in self._positions:
            self._logger.info(f"Position removed: {pos.slug} | reason={reason}")
        self._positions.pop(pos.slug, None)

    def _record_db_trade(self, pos: AccumulatorPosition, pnl: float, exit_reason: str):
        """Record a completed pair arb cycle (entry+exit) to performance.db."""
        if not self._perf_db or pnl is None:
            return
        try:
            trade_id = f"pair_{pos.slug}"
            matched = min(pos.up_qty, pos.down_qty) if (pos.up_qty > 0 and pos.down_qty > 0) else 0
            entry_ts = pos.entry_time.timestamp() if pos.entry_time else time.time() - 300
            entry_cost = pos.pair_cost if pos.pair_cost > 0 else (pos.up_avg_price or pos.down_avg_price or 0)

            self._perf_db.record_entry(
                trade_id=trade_id,
                token_id=pos.up_token_id or pos.down_token_id,
                slug=pos.slug,
                entry_time=entry_ts,
                entry_price=entry_cost,
                entry_size=matched,
                entry_tx_hash="pair_arb",
                outcome="PAIR" if pos.is_fully_hedged else "ORPHAN",
                market_title=pos.slug,
                strategy="pair_arb",
                metadata={
                    "up_price": round(pos.up_avg_price, 4),
                    "down_price": round(pos.down_avg_price, 4),
                    "up_fee_paid": round(pos.up_fee_paid, 4),
                    "down_fee_paid": round(pos.down_fee_paid, 4),
                    "up_qty": round(pos.up_qty, 2),
                    "down_qty": round(pos.down_qty, 2),
                    "pair_cost": round(entry_cost, 4),
                    "is_hedged": pos.is_fully_hedged,
                },
            )

            cost_basis = entry_cost * matched if matched > 0 else 1.0
            self._perf_db.record_exit(
                trade_id=trade_id,
                exit_time=time.time(),
                exit_price=1.0 if exit_reason == "hedged_settlement" else 0.0,
                exit_size=matched,
                exit_reason=exit_reason,
                exit_tx_hash="settlement",
                pnl=round(pnl, 6),
                pnl_pct=round(pnl / cost_basis, 4) if cost_basis > 0 else 0.0,
            )
            self._logger.info(f"DB: pair trade {trade_id} | pnl=${pnl:.2f} | {exit_reason}")
        except Exception as e:
            self._logger.warning(f"DB pair trade write failed: {e}")

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
        self._last_eval_block_reason = self._eval_block_reason
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
                "entry_mode": self._accum_entry_mode(),
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
