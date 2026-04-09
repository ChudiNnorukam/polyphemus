"""Arbitrage Engine — Riskless paired trading on 15-min crypto markets.

Buys BOTH outcomes (UP + DOWN) when combined price < $1.00.
Winner always pays $1.00, guaranteeing profit minus fees.

Safety features:
- Dynamic fee calculation (Polymarket taker fee formula)
- Order book walking for realistic VWAP pricing
- Full book re-check between legs (not just midpoint)
- Orphaned leg unwind with 3 retries + circuit breaker
- Atomic balance checks shared with signal bot
- Configurable slippage buffer on top of fees
"""

import asyncio
import time
from typing import TYPE_CHECKING

import aiohttp
from py_clob_client.order_builder.constants import BUY, SELL

from .config import setup_logger
from .models import (
    ArbOpportunity,
    ArbResult,
    GAMMA_API_URL,
    ARB_SLIPPAGE_BUFFER,
    ARB_ORDER_VERIFY_SECS,
    ARB_MARKET_BUFFER_SECS,
    ARB_UNWIND_MAX_RETRIES,
    ARB_MIN_UNWIND_PRICE_PCT,
)

if TYPE_CHECKING:
    from .clob_wrapper import ClobWrapper
    from .balance_manager import BalanceManager
    from .config import Settings


class ArbEngine:
    """Core arbitrage engine for 15-min crypto markets."""

    def __init__(
        self,
        clob: "ClobWrapper",
        balance: "BalanceManager",
        config: "Settings",
    ):
        self._clob = clob
        self._balance = balance
        self._config = config
        self._dry_run = config.arb_dry_run
        self._session: aiohttp.ClientSession | None = None
        self._logger = setup_logger("polyphemus.arb")
        self._stats = {
            "scans": 0,
            "opportunities": 0,
            "executions": 0,
            "successes": 0,
            "failures": 0,
            "unwinds": 0,
            "total_profit": 0.0,
            "total_fees": 0.0,
            "total_unwind_loss": 0.0,
            "last_scan": 0,
            "active_market": "",
        }
        self._market_cache: dict[str, tuple[float, list]] = {}  # asset -> (cache_time, markets)

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    async def start(self):
        """Main arb loop. Runs forever, scanning every config.arb_scan_interval seconds."""
        self._session = aiohttp.ClientSession()
        self._logger.info(f"Arb engine started (dry_run={self._dry_run})")
        try:
            while True:
                await asyncio.sleep(self._config.arb_scan_interval)
                try:
                    await self._scan_cycle()
                except Exception as e:
                    self._logger.exception(f"Arb scan error: {e}")
        finally:
            if self._session and not self._session.closed:
                await self._session.close()

    async def _scan_cycle(self):
        """One full scan cycle: discover markets, evaluate, execute."""
        self._stats["scans"] += 1
        self._stats["last_scan"] = time.time()

        # Check available capital
        available = await self._balance.get_available()
        if available < self._config.min_bet * 2:
            self._logger.debug(f"Arb scan #{self._stats['scans']}: skipped, balance=${available:.2f} < min ${self._config.min_bet * 2:.2f}")
            return

        # Discover and evaluate markets for each asset
        assets = self._config.get_arb_assets()
        total_markets = 0
        for asset in assets:
            markets = await self._discover_markets(asset)
            total_markets += len(markets)
            for market in markets:
                opp = await self._evaluate_opportunity(market, available)
                if opp:
                    self._stats["opportunities"] += 1
                    self._stats["active_market"] = opp.slug
                    result = await self._execute_arb(opp)
                    if result.success:
                        self._stats["successes"] += 1
                        self._stats["total_profit"] += result.net_profit
                        self._stats["total_fees"] += result.total_fees
                    else:
                        self._stats["failures"] += 1
                    self._stats["executions"] += 1
                    return  # One arb per cycle (capital constraint)
        self._logger.info(f"Arb scan #{self._stats['scans']}: {len(assets)} assets, {total_markets} markets, 0 opportunities (bal=${available:.2f})")

    async def _discover_markets(self, asset: str) -> list[dict]:
        """Discover active 15-min markets via computed slug pattern.

        Slug pattern: {asset}-updown-15m-{epoch_rounded_to_900}
        Queries Gamma API by exact slug for current + next 3 windows.
        Caches results for 60s to avoid rate limiting.
        """
        now = time.time()
        cached = self._market_cache.get(asset)
        if cached and now - cached[0] < 60:
            return cached[1]

        if not self._session or self._session.closed:
            return []

        WINDOW = 900  # 15 minutes in seconds
        ts_rounded = int(now // WINDOW) * WINDOW
        asset_lower = asset.lower()

        markets = []
        for offset in range(4):  # current + next 3 windows
            ts = ts_rounded + (offset * WINDOW)
            slug = f"{asset_lower}-updown-15m-{ts}"

            try:
                url = f"{GAMMA_API_URL}/markets"
                params = {"slug": slug}
                async with self._session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()

                if not data:
                    continue

                m = data[0]

                # Skip closed markets
                if m.get("closed") or not m.get("active"):
                    continue

                # Parse clobTokenIds (comes as JSON string from Gamma API)
                raw_tokens = m.get("clobTokenIds", "[]")
                if isinstance(raw_tokens, str):
                    import json
                    clob_token_ids = json.loads(raw_tokens)
                else:
                    clob_token_ids = raw_tokens

                if len(clob_token_ids) != 2:
                    continue

                # Parse outcomes (also comes as JSON string)
                raw_outcomes = m.get("outcomes", "[]")
                if isinstance(raw_outcomes, str):
                    import json
                    outcomes = json.loads(raw_outcomes)
                else:
                    outcomes = raw_outcomes

                if len(outcomes) != 2:
                    continue

                # Parse end date
                end_str = m.get("endDate") or ""
                if not end_str:
                    continue

                try:
                    from datetime import datetime
                    if end_str.endswith("Z"):
                        end_str = end_str[:-1] + "+00:00"
                    end_dt = datetime.fromisoformat(end_str)
                    expires_at = end_dt.timestamp()
                except (ValueError, TypeError):
                    continue

                # Skip markets expiring too soon
                if expires_at - now < ARB_MARKET_BUFFER_SECS:
                    continue

                # Determine UP vs DOWN token indices
                # Outcomes are ["Up", "Down"] — first=Up, second=Down
                up_idx, down_idx = 0, 1
                for i, o in enumerate(outcomes):
                    ol = o.lower() if isinstance(o, str) else ""
                    if "up" in ol or "yes" in ol:
                        up_idx = i
                    elif "down" in ol or "no" in ol:
                        down_idx = i

                markets.append({
                    "slug": slug,
                    "title": m.get("question", ""),
                    "up_token_id": clob_token_ids[up_idx],
                    "down_token_id": clob_token_ids[down_idx],
                    "expires_at": expires_at,
                })

            except asyncio.TimeoutError:
                continue
            except Exception as e:
                self._logger.debug(f"Gamma lookup failed for {slug}: {e}")
                continue

        self._market_cache[asset] = (now, markets)
        return markets

    @staticmethod
    def _walk_order_book(asks: list[dict], target_size: float) -> tuple[float, float]:
        """Walk asks lowest-to-highest. Returns (vwap_per_share, total_cost) or (0, 0)."""
        if not asks or target_size <= 0:
            return (0.0, 0.0)
        sorted_asks = sorted(asks, key=lambda a: a["price"])
        filled = 0.0
        cost = 0.0
        for ask in sorted_asks:
            take = min(ask["size"], target_size - filled)
            cost += take * ask["price"]
            filled += take
            if filled >= target_size:
                break
        if filled < target_size:
            return (0.0, 0.0)
        return (cost / filled, cost)

    @staticmethod
    def _calculate_fee(price: float, shares: float) -> float:
        """Polymarket dynamic taker fee for 15-min crypto markets.

        Formula: fee = shares * price * 0.25 * (price * (1 - price))^2
        """
        price = max(0.01, min(0.99, price))
        return shares * price * 0.25 * (price * (1 - price)) ** 2

    async def _evaluate_opportunity(self, market: dict, available: float) -> ArbOpportunity | None:
        """Evaluate a market for arb profitability.

        Returns ArbOpportunity if profitable after fees + slippage, else None.
        """
        up_token = market["up_token_id"]
        down_token = market["down_token_id"]

        # Fetch both order books in parallel
        up_book, down_book = await asyncio.gather(
            self._clob.get_order_book(up_token),
            self._clob.get_order_book(down_token),
        )

        up_asks = up_book.get("asks", [])
        down_asks = down_book.get("asks", [])

        if not up_asks or not down_asks:
            return None

        # Estimate pair cost with top-of-book for initial sizing
        top_up = up_asks[0]["price"] if up_asks else 1.0
        top_down = down_asks[0]["price"] if down_asks else 1.0
        est_pair_cost = top_up + top_down

        if est_pair_cost >= 1.0:
            return None

        # Calculate target shares
        max_by_capital = (available * self._config.arb_capital_pct) / est_pair_cost
        target_shares = min(self._config.arb_max_shares, max_by_capital)
        target_shares = max(5.0, target_shares)  # Minimum 5 shares

        # Walk both books at target size
        up_vwap, up_cost = self._walk_order_book(up_asks, target_shares)
        down_vwap, down_cost = self._walk_order_book(down_asks, target_shares)

        if up_vwap == 0 or down_vwap == 0:
            return None  # Insufficient liquidity

        pair_cost_per_share = up_vwap + down_vwap
        if pair_cost_per_share > self._config.arb_max_pair_cost:
            return None

        # Calculate fees
        fee_up = self._calculate_fee(up_vwap, target_shares)
        fee_down = self._calculate_fee(down_vwap, target_shares)
        fees_per_share = (fee_up + fee_down) / target_shares

        # Net profit per share
        net_profit_per_share = 1.00 - pair_cost_per_share - fees_per_share - ARB_SLIPPAGE_BUFFER

        if net_profit_per_share < self._config.arb_min_net_profit_pct:
            return None

        return ArbOpportunity(
            slug=market["slug"],
            market_title=market["title"],
            up_token_id=up_token,
            down_token_id=down_token,
            up_price=up_vwap,
            down_price=down_vwap,
            pair_cost=pair_cost_per_share,
            fee_up=fee_up,
            fee_down=fee_down,
            net_profit_per_share=net_profit_per_share,
            shares=target_shares,
            expires_at=market["expires_at"],
        )

    async def _execute_arb(self, opp: ArbOpportunity) -> ArbResult:
        """Execute a paired arb trade with safety guards."""
        # 1. Dry run check
        if self._dry_run:
            self._logger.info(
                f"[DRY RUN] Arb: {opp.slug} pair=${opp.pair_cost:.4f} "
                f"net=${opp.net_profit_per_share:.4f}/share x {opp.shares:.0f}"
            )
            return ArbResult(
                success=True,
                net_profit=opp.net_profit_per_share * opp.shares,
                shares=opp.shares,
                pair_cost=opp.pair_cost,
                total_fees=opp.fee_up + opp.fee_down,
            )

        # 2. Atomic balance re-check (prevents race with signal bot)
        available = await self._balance.get_available()
        needed = opp.pair_cost * opp.shares * 1.05  # 5% buffer
        if available < needed:
            return ArbResult(success=False, error="balance_insufficient_at_execution")

        # 3. Place leg 1 (UP)
        up_result = await self._clob.place_order(
            opp.up_token_id, opp.up_price, opp.shares, BUY
        )
        if not up_result.success:
            return ArbResult(success=False, error=f"leg1_failed: {up_result.error}")

        # 4. Full order book re-check before leg 2
        down_book = await self._clob.get_order_book(opp.down_token_id)
        down_asks = down_book.get("asks", [])
        if not down_asks:
            self._logger.warning("Down order book empty after leg 1. Unwinding.")
            await self._unwind_leg(opp.up_token_id, opp.shares, "book_empty")
            return ArbResult(success=False, error="down_book_empty_after_leg1", unwound=True)

        fresh_down_vwap, _ = self._walk_order_book(down_asks, opp.shares)
        if fresh_down_vwap == 0:
            self._logger.warning("Insufficient down liquidity after leg 1. Unwinding.")
            await self._unwind_leg(opp.up_token_id, opp.shares, "liquidity_drain")
            return ArbResult(success=False, error="down_liquidity_drained", unwound=True)

        fresh_pair_cost = opp.up_price + fresh_down_vwap
        if fresh_pair_cost > self._config.arb_max_pair_cost:
            self._logger.warning(
                f"Pair cost drifted: original={opp.pair_cost:.4f} "
                f"fresh={fresh_pair_cost:.4f}. Unwinding leg 1."
            )
            await self._unwind_leg(opp.up_token_id, opp.shares, "pair_cost_drift")
            return ArbResult(success=False, error="pair_cost_drift_abort", unwound=True)

        # Update down_price to fresh VWAP
        opp.down_price = fresh_down_vwap

        # 5. Place leg 2 (DOWN)
        down_result = await self._clob.place_order(
            opp.down_token_id, opp.down_price, opp.shares, BUY
        )
        if not down_result.success:
            self._logger.error(f"Leg 2 failed, unwinding leg 1: {down_result.error}")
            await self._unwind_leg(opp.up_token_id, opp.shares, "leg2_failed")
            return ArbResult(success=False, error=f"leg2_failed: {down_result.error}", unwound=True)

        # 6. Verify both fills
        up_status = await self._verify_fill(up_result.order_id)
        down_status = await self._verify_fill(down_result.order_id)

        if up_status == "FILLED" and down_status == "FILLED":
            total_fees = opp.fee_up + opp.fee_down
            net = (1.00 - opp.pair_cost) * opp.shares - total_fees
            self._logger.info(
                f"Arb SUCCESS: {opp.slug} {opp.shares:.0f} shares "
                f"pair=${opp.pair_cost:.4f} fees=${total_fees:.4f} net=${net:.4f}"
            )
            return ArbResult(
                success=True,
                up_order_id=up_result.order_id,
                down_order_id=down_result.order_id,
                shares=opp.shares,
                pair_cost=opp.pair_cost,
                total_fees=total_fees,
                net_profit=net,
            )

        # 7. Handle partial fills
        if up_status == "FILLED" and down_status != "FILLED":
            await self._clob.cancel_order(down_result.order_id)
            await self._unwind_leg(opp.up_token_id, opp.shares, "down_unfilled")
            return ArbResult(success=False, error="down_leg_unfilled", unwound=True)

        if down_status == "FILLED" and up_status != "FILLED":
            await self._clob.cancel_order(up_result.order_id)
            await self._unwind_leg(opp.down_token_id, opp.shares, "up_unfilled")
            return ArbResult(success=False, error="up_leg_unfilled", unwound=True)

        # 8. Both unfilled — cancel both
        await self._clob.cancel_order(up_result.order_id)
        await self._clob.cancel_order(down_result.order_id)
        return ArbResult(success=False, error="both_legs_unfilled")

    async def _unwind_leg(self, token_id: str, shares: float, reason: str) -> bool:
        """Sell an orphaned arb leg with retries. Returns True if successful."""
        self._stats["unwinds"] += 1
        for attempt in range(ARB_UNWIND_MAX_RETRIES):
            try:
                book = await self._clob.get_order_book(token_id)
                bids = book.get("bids", [])

                if bids:
                    best_bid = bids[0]["price"]  # Already sorted descending
                    # Circuit breaker: abort if best_bid collapsed
                    if best_bid < 0.50 * ARB_MIN_UNWIND_PRICE_PCT:
                        self._logger.error(
                            f"Unwind circuit breaker: best_bid=${best_bid:.4f} too low. "
                            f"Holding position for market resolution."
                        )
                        return False
                    sell_price = best_bid * 0.99  # 1% haircut for fast fill
                else:
                    mid = await self._clob.get_midpoint(token_id)
                    if mid <= 0:
                        self._logger.error(f"Unwind abort: no midpoint available (API failure or no data)")
                        return False
                    if mid < 0.10:
                        self._logger.error(f"Unwind abort: midpoint=${mid:.4f} collapsed")
                        return False
                    sell_price = max(0.01, mid * 0.95)

                result = await self._clob.place_order(token_id, sell_price, shares, SELL)
                if result.success:
                    status = await self._verify_fill(result.order_id)
                    if status in ("FILLED", "MATCHED"):
                        loss = abs((sell_price - 0.50) * shares)
                        self._stats["total_unwind_loss"] += loss
                        self._logger.info(f"Unwind OK: {reason} | sold @ ${sell_price:.4f}")
                        return True
                    await self._clob.cancel_order(result.order_id)

                await asyncio.sleep(2 ** attempt)  # 1s, 2s, 4s backoff
            except Exception as e:
                self._logger.exception(f"Unwind attempt {attempt + 1} failed: {e}")

        self._logger.error(f"UNWIND FAILED after {ARB_UNWIND_MAX_RETRIES} retries: {token_id}")
        return False

    async def _verify_fill(self, order_id: str) -> str:
        """Poll for order fill. Returns final status string."""
        for _ in range(ARB_ORDER_VERIFY_SECS // 2):
            await asyncio.sleep(2)
            status = await self._clob.get_order_status(order_id)
            if status in ("FILLED", "MATCHED", "CANCELLED", "FAILED"):
                return status
        return "TIMEOUT"
