"""CheapSideSignal - Buy the cheaper token on every epoch.

ugag's strategy decoded: 86% of buys are the side below $0.50.
No momentum required. Discovers markets independently via Gamma API,
gets live midpoints from CLOB order book, fires every scan cycle.

Designed for sub-ms latency VPS: scans every 5 seconds by default,
discovers markets at epoch start, tracks midpoints via WebSocket.
"""

import asyncio
import json
import logging
import time
from typing import Optional, Callable, Awaitable

import aiohttp


GAMMA_API_URL = "https://gamma-api.polymarket.com"


class CheapSideSignal:
    """Scans epochs for cheap-side entry opportunities."""

    def __init__(self, config, clob=None, market_ws=None,
                 on_signal: Optional[Callable] = None):
        self._config = config
        self._clob = clob
        self._market_ws = market_ws
        self._on_signal = on_signal
        self._logger = logging.getLogger("polyphemus.cheap_side")
        self._signaled_slugs: set = set()
        self._market_cache: dict = {}  # slug -> {up_token_id, down_token_id}
        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False

    async def scan_loop(self):
        """Main loop: discover markets and check for cheap sides."""
        self._running = True
        self._session = aiohttp.ClientSession()
        interval = getattr(self._config, 'cheap_side_scan_interval', 5)
        self._logger.info(
            f"CheapSideSignal started | "
            f"max_price={getattr(self._config, 'cheap_side_max_price', 0.45)} | "
            f"scan_interval={interval}s"
        )

        # Parse active hours (empty = always active)
        hours_str = getattr(self._config, 'cheap_side_active_hours', '')
        active_hours = set()
        if hours_str:
            active_hours = {int(h.strip()) for h in hours_str.split(',') if h.strip()}
            self._logger.info(f"Cheap side active hours (UTC): {sorted(active_hours)}")

        while self._running:
            try:
                if active_hours:
                    from datetime import datetime, timezone
                    current_hour = datetime.now(timezone.utc).hour
                    if current_hour not in active_hours:
                        await asyncio.sleep(5)  # sleep longer when outside active hours
                        continue
                await self._scan_current_epochs()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.warning(f"Scan error: {e}")
            await asyncio.sleep(interval)

        if self._session:
            await self._session.close()

    async def _discover_market(self, slug: str) -> Optional[dict]:
        """Discover market token IDs from Gamma API. Caches per slug."""
        if slug in self._market_cache:
            return self._market_cache[slug]

        try:
            url = f"{GAMMA_API_URL}/markets?slug={slug}"
            async with self._session.get(
                url, timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

            if not data:
                self._market_cache[slug] = None
                return None

            market = data[0] if isinstance(data, list) else data
            token_ids = json.loads(market.get("clobTokenIds", "[]"))
            outcomes = json.loads(market.get("outcomes", "[]"))

            if len(token_ids) < 2 or len(outcomes) < 2:
                self._market_cache[slug] = None
                return None

            # Map outcomes to token IDs
            up_idx = outcomes.index("Up") if "Up" in outcomes else 0
            down_idx = 1 - up_idx

            result = {
                "up_token_id": token_ids[up_idx],
                "down_token_id": token_ids[down_idx],
                "title": market.get("question", slug),
            }
            self._market_cache[slug] = result
            return result

        except Exception as e:
            self._logger.debug(f"Market discovery failed for {slug}: {e}")
            return None

    async def _get_midpoint(self, token_id: str) -> float:
        """Get live midpoint for a token. Tries WebSocket first, then REST."""
        # Try WebSocket (sub-ms)
        if self._market_ws:
            mid = self._market_ws.get_midpoint(token_id)
            if mid and mid > 0:
                return mid

        # Fallback: REST order book query
        if self._clob:
            try:
                book = await asyncio.wait_for(
                    self._clob.get_order_book(token_id),
                    timeout=2.0
                )
                if book:
                    bids = book.get("bids", [])
                    asks = book.get("asks", [])
                    if bids and asks:
                        best_bid = float(bids[0].get("price", 0))
                        best_ask = float(asks[0].get("price", 0))
                        if best_bid > 0 and best_ask > 0:
                            return (best_bid + best_ask) / 2
            except Exception:
                pass

        return 0

    async def _scan_current_epochs(self):
        """Check current 5m and 15m BTC epochs for cheap sides.
        Also pre-discovers the NEXT epoch so there's zero cold-start delay.
        """
        now = time.time()
        max_price = getattr(self._config, 'cheap_side_max_price', 0.45)
        min_price = getattr(self._config, 'cheap_side_min_price', 0.15)
        min_secs = getattr(self._config, 'cheap_side_min_secs', 60)
        max_secs = getattr(self._config, 'cheap_side_max_secs', 240)
        # Get assets from config (default BTC,SOL)
        asset_str = getattr(self._config, 'asset_filter', 'BTC')
        assets = [a.strip().upper() for a in asset_str.split(',') if a.strip()]
        if not assets:
            assets = ["BTC"]

        # Parse windows from config (default 5m only)
        windows_str = getattr(self._config, 'cheap_side_windows', '300')
        windows = [int(w.strip()) for w in windows_str.split(',') if w.strip()]
        if not windows:
            windows = [300]

        for asset in assets:
            for window in windows:
                # Pre-discover NEXT epoch (cache tokens before it starts)
                next_epoch = (int(now // window) + 1) * window
                next_label = f"{window // 60}m"
                next_slug = f"{asset.lower()}-updown-{next_label}-{next_epoch}"
                if next_slug not in self._market_cache:
                    await self._discover_market(next_slug)
                epoch = int(now // window) * window
                epoch_end = epoch + window
                secs_left = epoch_end - now
                label = f"{window // 60}m"
                slug = f"{asset.lower()}-updown-{label}-{epoch}"

                if slug in self._signaled_slugs:
                    continue

                if secs_left < min_secs or secs_left > max_secs:
                    continue

                # Discover market (cached after first call)
                market = await self._discover_market(slug)
                if not market:
                    continue

                # Get live midpoints
                up_mid = await self._get_midpoint(market["up_token_id"])
                down_mid = await self._get_midpoint(market["down_token_id"])

                if up_mid <= 0 and down_mid <= 0:
                    continue

                # If we only have one side, infer the other
                if up_mid > 0 and down_mid <= 0:
                    down_mid = max(0.01, 1.0 - up_mid)
                elif down_mid > 0 and up_mid <= 0:
                    up_mid = max(0.01, 1.0 - down_mid)

                # Pick the cheaper side
                outcome = None
                price = 0
                token_id = ""

                if up_mid <= down_mid and min_price <= up_mid <= max_price:
                    outcome = "Up"
                    price = up_mid
                    token_id = market["up_token_id"]
                elif min_price <= down_mid <= max_price:
                    outcome = "Down"
                    price = down_mid
                    token_id = market["down_token_id"]

                # Mode 2: Momentum-confirmed mid-range (ugag's expensive side plays)
                # When momentum is strong AND token is $0.60-0.80, buy the trending side
                # Gated by mode2_dry_run - only executes when explicitly enabled live
                if not outcome and self._momentum_feed and not getattr(self._config, 'mode2_dry_run', True):
                    mode2_min = 0.60
                    mode2_max = 0.80
                    move = self._momentum_feed.get_current_momentum_pct(asset)
                    if move is not None:
                        # Strong move: >0.07% over momentum_window_secs
                        if move > 0.0007 and mode2_min <= up_mid <= mode2_max:
                            outcome = "Up"
                            price = up_mid
                            token_id = market["up_token_id"]
                        elif move < -0.0007 and mode2_min <= down_mid <= mode2_max:
                            outcome = "Down"
                            price = down_mid
                            token_id = market["down_token_id"]

                if not outcome:
                    continue

                # Book imbalance confirmation (skip if book doesn't confirm direction)
                min_imbalance = getattr(self._config, 'cheap_side_min_imbalance', 0.0)
                if min_imbalance > 0 and self._clob:
                    try:
                        book = await asyncio.wait_for(
                            self._clob.get_order_book(token_id),
                            timeout=1.0
                        )
                        if book:
                            bids = book.get("bids", [])
                            asks = book.get("asks", [])
                            bid_depth = sum(float(b.get("size", 0)) for b in bids[:5])
                            ask_depth = sum(float(a.get("size", 0)) for a in asks[:5])
                            total = bid_depth + ask_depth
                            imbalance = bid_depth / total if total > 0 else 0.5
                            if imbalance < min_imbalance:
                                self._logger.debug(
                                    f"CHEAP SIDE SKIP | {slug} {outcome} | "
                                    f"imbalance={imbalance:.2f} < {min_imbalance} | "
                                    f"bid={bid_depth:.0f} ask={ask_depth:.0f}"
                                )
                                continue
                    except Exception:
                        pass  # proceed without imbalance check on error

                self._signaled_slugs.add(slug)

                # Prune old slugs
                if len(self._signaled_slugs) > 500:
                    cutoff = int(now) - 3600
                    self._signaled_slugs = {
                        s for s in self._signaled_slugs
                        if s.rsplit('-', 1)[-1].isdigit() and int(s.rsplit('-', 1)[-1]) > cutoff
                    }

                other_mid = down_mid if outcome == "Up" else up_mid
                self._logger.info(
                    f"CHEAP SIDE SIGNAL | {slug} {outcome} @ ${price:.3f} | "
                    f"other=${other_mid:.3f} | {secs_left:.0f}s left"
                )

                if self._on_signal:
                    signal = {
                        "slug": slug,
                        "asset": asset,
                        "outcome": outcome,
                        "direction": outcome,
                        "price": price,
                        "token_id": token_id,
                        "source": "cheap_side",
                        "time_remaining_secs": secs_left,
                        "market_window_secs": window,
                        "momentum_pct": 0,
                        "entry_mode_override": "fak",
                    }
                    await self._on_signal(signal)

                    # DRY RUN: conviction scaling log (would we add more?)
                    if getattr(self._config, 'conviction_dry_run', True):
                        if self._momentum_feed:
                            bp = self._momentum_feed.get_latest_price(asset)
                            if bp and hasattr(self, '_last_bp') and self._last_bp.get(asset, 0) > 0:
                                move = (bp - self._last_bp[asset]) / self._last_bp[asset]
                                confirms = (
                                    (outcome == "Up" and move > 0.0003) or
                                    (outcome == "Down" and move < -0.0003)
                                )
                                self._logger.info(
                                    f"[DRY] CONVICTION | {slug} {outcome} | "
                                    f"binance={move:+.3%} | confirms={confirms} | "
                                    f"would_scale={'5x' if confirms else '1x'}"
                                )

        # === LOTTERY TICKET SCAN (last 5-45s of epoch) ===
        lottery_enabled = getattr(self._config, 'lottery_enabled', False)
        if lottery_enabled:
            lottery_max_price = getattr(self._config, 'lottery_max_price', 0.05)
            lottery_min_secs = getattr(self._config, 'lottery_min_secs', 5)
            lottery_max_secs = getattr(self._config, 'lottery_max_secs', 45)
            lottery_bet = getattr(self._config, 'lottery_bet', 0.50)

            for asset in assets:
                for window in windows:
                    epoch = int(now // window) * window
                    epoch_end = epoch + window
                    secs_left = epoch_end - now
                    label = f"{window // 60}m"
                    slug = f"{asset.lower()}-updown-{label}-{epoch}"
                    lottery_slug = f"lottery_{slug}"

                    if lottery_slug in self._signaled_slugs:
                        continue
                    if secs_left < lottery_min_secs or secs_left > lottery_max_secs:
                        continue

                    market = await self._discover_market(slug)
                    if not market:
                        continue

                    up_mid = await self._get_midpoint(market["up_token_id"])
                    down_mid = await self._get_midpoint(market["down_token_id"])
                    if up_mid <= 0 and down_mid <= 0:
                        continue
                    if up_mid > 0 and down_mid <= 0:
                        down_mid = max(0.01, 1.0 - up_mid)
                    elif down_mid > 0 and up_mid <= 0:
                        up_mid = max(0.01, 1.0 - down_mid)

                    # Pick the cheaper side if it's ultra-cheap
                    outcome = None
                    price = 0
                    token_id = ""

                    if up_mid <= down_mid and up_mid <= lottery_max_price:
                        outcome = "Up"
                        price = up_mid
                        token_id = market["up_token_id"]
                    elif down_mid <= lottery_max_price:
                        outcome = "Down"
                        price = down_mid
                        token_id = market["down_token_id"]

                    if not outcome:
                        continue

                    self._signaled_slugs.add(lottery_slug)
                    payoff = (1.0 - price) / price if price > 0 else 0

                    self._logger.info(
                        f"LOTTERY SIGNAL | {slug} {outcome} @ ${price:.3f} | "
                        f"payoff={payoff:.0f}x | {secs_left:.0f}s left"
                    )

                    if self._on_signal:
                        signal = {
                            "slug": slug,
                            "asset": asset,
                            "outcome": outcome,
                            "direction": outcome,
                            "price": price,
                            "token_id": token_id,
                            "source": "lottery",
                            "time_remaining_secs": secs_left,
                            "market_window_secs": window,
                            "momentum_pct": 0,
                            "entry_mode_override": "fak",
                            "override_bet_size": lottery_bet,
                        }
                        await self._on_signal(signal)

        # === DRY RUN: Mode 2 momentum logging ===
        if getattr(self._config, 'mode2_dry_run', True) and self._momentum_feed:
            for asset in assets:
                for window in windows:
                    epoch = int(now // window) * window
                    secs_left = (epoch + window) - now
                    if secs_left < 60 or secs_left > 240:
                        continue
                    label = f"{window // 60}m"
                    slug = f"{asset.lower()}-updown-{label}-{epoch}"
                    mode2_slug = f"mode2_{slug}"
                    if mode2_slug in self._signaled_slugs:
                        continue

                    market = await self._discover_market(slug)
                    if not market:
                        continue
                    up_mid = await self._get_midpoint(market["up_token_id"])
                    down_mid = await self._get_midpoint(market["down_token_id"])

                    move = self._momentum_feed.get_current_momentum_pct(asset)
                    if move is None:
                        continue

                    if abs(move) > 0.0007:  # >0.07% over momentum_window_secs
                        mode2_dir = "Up" if move > 0 else "Down"
                        mode2_price = up_mid if mode2_dir == "Up" else down_mid
                        if 0.55 <= mode2_price <= 0.75:
                            self._signaled_slugs.add(mode2_slug)
                            self._logger.info(
                                f"[DRY] MODE 2 | {slug} {mode2_dir} @ ${mode2_price:.3f} | "
                                f"momentum={move:+.3%} | {secs_left:.0f}s left | "
                                f"WOULD ENTER if enabled"
                            )

    def stop(self):
        self._running = False
