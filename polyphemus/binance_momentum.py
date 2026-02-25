"""
Binance Momentum Signal Feed — Primary signal generator.

Monitors Binance spot prices in real-time via WebSocket. When a crypto asset
moves >threshold% within a rolling window, generates a BUY signal on the
corresponding Polymarket 15-min market using post-only maker orders.
"""

import asyncio
import json
import time
from collections import deque
from typing import Callable, Dict, Optional, Set

import aiohttp

from .types import (
    BACKOFF_BASE, BACKOFF_MAX, BACKOFF_MULTIPLIER,
    BINANCE_WS_URL, BINANCE_SYMBOLS, ASSET_TO_BINANCE,
    COINBASE_WS_URL, COINBASE_PRODUCTS, COINBASE_TO_SYMBOL,
    PRICE_FEED_SOURCE,
    BINANCE_FUTURES_WS_URL, BINANCE_FUNDING_URL,
    ASSET_TO_FUTURES, FUTURES_TO_ASSET,
)
from .config import Settings, setup_logger
from .clob_wrapper import ClobWrapper
from .state_store import StateStore

GAMMA_API_URL = "https://gamma-api.polymarket.com"

# Reverse map: binance symbol -> asset name
BINANCE_TO_ASSET = {v: k for k, v in ASSET_TO_BINANCE.items()}


class BinanceMomentumFeed:
    """Real-time Binance price momentum detector → Polymarket signal generator."""

    def __init__(self, config: Settings, clob: ClobWrapper, on_signal: Callable):
        self._config = config
        self._clob = clob
        self._on_signal = on_signal  # async callback
        self._logger = setup_logger("polyphemus.momentum")

        # State persistence (prevents duplicate signals on restart)
        self._state_store = StateStore(data_dir=config.lagbot_data_dir, default_ttl_secs=3600)
        self._signaled_slugs: Set[str] = self._state_store.load("signaled_slugs", ttl_secs=3600)

        # Per-symbol rolling price buffer: symbol -> deque of (timestamp, price)
        self._price_buffers: Dict[str, deque] = {
            symbol: deque(maxlen=600)  # ~10 min of 1s updates
            for symbol in BINANCE_SYMBOLS
        }

        # Per-asset momentum cooldown: asset -> last_signal_time
        self._momentum_cooldown: Dict[str, float] = self._state_store.load("momentum_cooldown", ttl_secs=300)
        self._last_prune_time: float = 0.0

        # Market info cache: slug -> {"up_token_id", "down_token_id", "market_title"}
        self._market_cache: Dict[str, Optional[dict]] = {}

        # Window Delta: track open prices per (asset, window_epoch)
        self._window_open_prices: Dict[tuple, float] = {}
        self._delta_fired: Set[str] = set()  # slugs already fired for delta

        # Connection state
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._consecutive_failures = 0

        # Stats
        self.signals_generated = 0
        self.momentum_detections = 0
        self._regime_detector = None  # Set by signal_bot after init

        # Entry stagger: cooldown between entries to prevent correlated cluster wipeouts
        self._last_signal_time: dict = {}  # per-asset: {asset: timestamp}

        # Fee gate: if ANY 5m market has fees, halt all signal generation
        self._fee_gate_active: bool = False

        # Persistent Gamma API session (reused across _discover_market calls)
        self._gamma_session: Optional[aiohttp.ClientSession] = None

        # Market pre-cache background task + MarketWS reference (set by signal_bot)
        self._market_ws = None  # type: Optional[MarketWS]
        self._prefetch_task: Optional[asyncio.Task] = None

        # Liquidation tracking: asset -> deque of (timestamp, side, usd_value)
        self._liq_buffers: Dict[str, deque] = {}
        self._liq_ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._liq_session: Optional[aiohttp.ClientSession] = None
        self.liquidation_events = 0

        # Pair arb: active concurrent position counter (managed by signal_bot)
        self._active_pair_arb_count: int = 0

    def set_market_ws(self, ws) -> None:
        """Inject MarketWS reference for real-time midpoints."""
        self._market_ws = ws

    async def start(self) -> None:
        """Start the Binance momentum feed + liquidation feed + funding poller."""
        self._logger.info(
            f"Starting Binance momentum feed | "
            f"trigger={self._config.momentum_trigger_pct*100:.1f}% | "
            f"window={self._config.momentum_window_secs}s | "
            f"entry_mode={self._config.entry_mode}"
        )
        # Create persistent Gamma session
        self._gamma_session = aiohttp.ClientSession()

        await asyncio.gather(
            self._connect_loop(),
            self._liquidation_connect_loop(),
            self._funding_rate_poll_loop(),
            self._market_prefetch_loop(),
        )

    async def start_stale_watchdog(self) -> None:
        """Placeholder for API compatibility with signal feeds."""
        # Momentum feed has its own reconnect logic, no separate watchdog needed
        await asyncio.Event().wait()

    async def _connect_loop(self) -> None:
        """Exponential backoff reconnection loop."""
        use_coinbase = PRICE_FEED_SOURCE == "coinbase"
        attempt = 0
        while True:
            try:
                self._session = aiohttp.ClientSession()
                if use_coinbase:
                    self._ws = await self._session.ws_connect(
                        COINBASE_WS_URL, timeout=10
                    )
                    # Subscribe to ticker channel
                    await self._ws.send_json({
                        "type": "subscribe",
                        "product_ids": COINBASE_PRODUCTS,
                        "channel": "ticker",
                    })
                    self._logger.info("Coinbase momentum WS connected")
                else:
                    streams = "/".join(
                        f"{symbol}@kline_1s" for symbol in BINANCE_SYMBOLS
                    )
                    url = f"{BINANCE_WS_URL}?streams={streams}"
                    self._ws = await self._session.ws_connect(url, timeout=10)
                    self._logger.info("Binance momentum WS connected")
                attempt = 0
                self._consecutive_failures = 0
                await self._read_loop()
            except asyncio.CancelledError:
                self._logger.info("Momentum feed cancelled")
                raise
            except Exception as e:
                self._consecutive_failures += 1
                delay = min(BACKOFF_BASE * (BACKOFF_MULTIPLIER ** attempt), BACKOFF_MAX)
                source = "Coinbase" if use_coinbase else "Binance"
                self._logger.warning(
                    f"{source} momentum WS disconnected: {e}, retry in {delay}s"
                )
                attempt += 1
                await asyncio.sleep(delay)
            finally:
                await self._close_session()

    async def _read_loop(self) -> None:
        """Read price messages and detect momentum."""
        use_coinbase = PRICE_FEED_SOURCE == "coinbase"
        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    if use_coinbase:
                        await self._process_coinbase_update(data)
                    else:
                        await self._process_binance_update(data)
                except json.JSONDecodeError:
                    continue
                except Exception as e:
                    self._logger.warning(f"Momentum msg error: {e}", exc_info=True)
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break

    async def _process_coinbase_update(self, data: dict) -> None:
        """Process a Coinbase ticker update."""
        if data.get("channel") != "ticker":
            return
        for event in data.get("events", []):
            for ticker in event.get("tickers", []):
                product_id = ticker.get("product_id", "")
                symbol = COINBASE_TO_SYMBOL.get(product_id)
                if not symbol or symbol not in self._price_buffers:
                    continue
                price = float(ticker.get("price", 0))
                if price <= 0:
                    continue
                now = time.time()
                self._price_buffers[symbol].append((now, price))
                if self._regime_detector:
                    asset = BINANCE_TO_ASSET.get(symbol)
                    if asset:
                        self._regime_detector.update(asset, price, now)
                await self._check_momentum(symbol, now, price)
                if self._config.enable_window_delta:
                    await self._check_window_delta(symbol, now, price)

    async def _process_binance_update(self, data: dict) -> None:
        """Process a Binance kline update."""
        kline_data = data.get("data", {})
        k = kline_data.get("k", {})

        symbol = kline_data.get("s", "").lower()
        if symbol not in self._price_buffers:
            return

        price = float(k.get("c", 0))
        if price <= 0:
            return

        now = time.time()
        self._price_buffers[symbol].append((now, price))

        # Feed regime detector with every price tick
        if self._regime_detector:
            asset = BINANCE_TO_ASSET.get(symbol)
            if asset:
                self._regime_detector.update(asset, price, now)

        # Check momentum
        await self._check_momentum(symbol, now, price)

        # Check window delta (T-10 late entry)
        if self._config.enable_window_delta:
            await self._check_window_delta(symbol, now, price)

    async def _check_momentum(self, symbol: str, now: float, current_price: float) -> None:
        """Check if price has moved enough within the window to trigger a signal."""
        buffer = self._price_buffers[symbol]
        window = self._config.momentum_window_secs
        threshold = self._config.momentum_trigger_pct

        # Find oldest price within window
        cutoff = now - window
        oldest_price = None
        for ts, price in buffer:
            if ts >= cutoff:
                oldest_price = price
                break

        if oldest_price is None or oldest_price <= 0:
            return

        # Calculate momentum
        pct_change = (current_price - oldest_price) / oldest_price

        if abs(pct_change) < threshold:
            return

        # Flash crash guard: reject extreme momentum (data glitch or black swan)
        max_pct = self._config.momentum_max_pct
        if max_pct > 0 and abs(pct_change) > max_pct:
            asset = BINANCE_TO_ASSET.get(symbol, symbol)
            self._logger.warning(
                f"Flash crash guard: {asset} {pct_change:+.3%} exceeds "
                f"{max_pct:.1%} cap in {window}s — skipping"
            )
            return

        # Momentum detected!
        direction = "UP" if pct_change > 0 else "DOWN"
        asset = BINANCE_TO_ASSET.get(symbol)
        if not asset:
            return

        # Companion assets don't fire independently — they follow the lead asset's trigger
        if asset.upper() in self._config.get_companion_assets():
            return

        # Per-asset cooldown: suppress duplicate detections for 5s
        cooldown_key = f"{asset}_{direction}"
        last_fire = self._momentum_cooldown.get(cooldown_key, 0.0)
        if now - last_fire < 5.0:
            return
        self._momentum_cooldown[cooldown_key] = now
        self._state_store.save("momentum_cooldown", self._momentum_cooldown)

        self.momentum_detections += 1
        self._logger.info(
            f"Momentum detected: {asset} {direction} {pct_change:+.3%} "
            f"in {window}s ({oldest_price:.4f} -> {current_price:.4f})"
        )

        # Periodic prune of stale slugs (every 5 min)
        if now - self._last_prune_time > 300:
            self.prune_stale()
            self._last_prune_time = now

        # Generate signal
        await self._generate_signal(asset, direction, momentum_pct=pct_change)

    async def _generate_signal(self, asset: str, direction: str, momentum_pct: float = 0.0) -> None:
        """Generate trading signals for ALL applicable windows for this asset."""
        # Early filter: skip assets not in allow-list or shadow-list
        allowed = self._config.get_asset_filter()
        shadow = self._config.get_shadow_assets()
        is_shadow = asset.upper() in shadow

        # Entry cooldown: prevent correlated cluster entries (skip for shadow)
        cooldown = self._config.entry_cooldown_secs
        if not is_shadow and cooldown > 0 and self._last_signal_time.get(asset.upper(), 0) > 0:
            elapsed = time.time() - self._last_signal_time.get(asset.upper(), 0)
            if elapsed < cooldown:
                self._logger.debug(
                    f"Entry cooldown: {asset} {direction} skipped "
                    f"({cooldown - elapsed:.0f}s remaining)"
                )
                return
        if allowed and asset.upper() not in allowed and not is_shadow:
            self._logger.debug(f"Skipping {asset} momentum (not in asset_filter or shadow)")
            return

        # Direction filter: e.g. "Up" = only buy Up tokens (skip Down momentum)
        dir_filter = self._config.direction_filter.strip()
        if dir_filter:
            outcome = "Up" if direction == "UP" else "Down"
            if outcome != dir_filter:
                self._logger.debug(
                    f"Skipping {asset} {direction} (direction_filter={dir_filter})"
                )
                return

        # Generate signal for each window (e.g., BTC → [300, 900] for dual-window)
        for window in self._config.get_market_windows(asset):
            await self._generate_signal_for_window(asset, direction, momentum_pct, window, shadow=is_shadow)

        # Companion assets: fire in the same direction as this (lead) asset
        if not is_shadow:
            for companion in self._config.get_companion_assets():
                if allowed and companion not in allowed:
                    continue  # companion must also be in ASSET_FILTER
                if cooldown > 0 and self._last_signal_time.get(companion, 0) > 0:
                    elapsed = time.time() - self._last_signal_time.get(companion, 0)
                    if elapsed < cooldown:
                        self._logger.debug(
                            f"Companion cooldown: {companion} skipped "
                            f"({cooldown - elapsed:.0f}s remaining)"
                        )
                        continue
                self._logger.info(
                    f"Companion signal: {companion} follows {asset} {direction} {momentum_pct:+.3%}"
                )
                for window in self._config.get_market_windows(companion):
                    await self._generate_signal_for_window(companion, direction, momentum_pct, window, shadow=False)

    async def _generate_signal_for_window(self, asset: str, direction: str,
                                           momentum_pct: float, window: int,
                                           shadow: bool = False) -> None:
        """Generate a trading signal for a specific market window."""
        # Fee gate: hard block if 5m fees detected
        if self._fee_gate_active:
            return

        # Compute current market slug for THIS window
        epoch = int(time.time() // window) * window
        label = f"{window // 60}m"
        slug = f"{asset.lower()}-updown-{label}-{epoch}"

        # Debounce: max 1 attempt per slug per window
        if slug in self._signaled_slugs:
            return
        self._signaled_slugs.add(slug)  # Mark immediately to prevent spam
        self._state_store.save("signaled_slugs", self._signaled_slugs)

        # Check time remaining (per-window threshold)
        market_end = epoch + window
        secs_left = market_end - time.time()
        min_secs = self._config.get_min_secs_remaining(window)
        if secs_left < min_secs:
            self._logger.info(
                f"Skipping {slug}: only {secs_left:.0f}s left "
                f"(need {min_secs}s)"
            )
            return

        # Discover market on Polymarket
        market_info = await self._discover_market(slug)
        if not market_info:
            self._logger.warning(f"Market not found on Polymarket: {slug}")
            return

        # Determine token based on direction
        outcome = "Up" if direction == "UP" else "Down"
        token_id = (
            market_info["up_token_id"] if outcome == "Up"
            else market_info["down_token_id"]
        )

        # Get midpoint price — prefer WS (0ms) over REST (~68ms)
        midpoint = 0.0
        midpoint_source = "rest"
        if self._market_ws:
            midpoint = self._market_ws.get_midpoint(token_id)
            if midpoint > 0:
                midpoint_source = "ws"
        if midpoint <= 0:
            midpoint = await self._clob.get_midpoint(token_id)
            midpoint_source = "rest"
        if midpoint <= 0:
            self._logger.warning(f"No midpoint for {slug} {outcome}")
            return

        # Spread check: reject if bid-ask spread too wide (maker orders won't fill)
        # Skip check if MarketWS has no data yet (-1.0 sentinel) — newly subscribed tokens
        max_spread = self._config.max_entry_spread
        if max_spread > 0 and self._market_ws:
            spread = self._market_ws.get_spread(token_id)
            if spread > 0 and spread > max_spread:
                self._logger.info(
                    f"Spread too wide: {slug} {outcome} spread=${spread:.3f} "
                    f"> ${max_spread:.3f} — skipping"
                )
                return

        self.signals_generated += 1

        # Get liquidation conviction from regime detector
        liq_conviction = 0.0
        if self._regime_detector:
            liq_conviction = self._regime_detector.get_liquidation_conviction(
                asset, "UP" if outcome == "Up" else "DOWN"
            )

        # Build signal dict (compatible with existing pipeline)
        signal = {
            "token_id": token_id,
            "price": midpoint,
            "slug": slug,
            "market_title": market_info.get("market_title", slug),
            "usdc_size": 999.0,  # synthetic — bypass conviction filter
            "direction": "BUY",
            "outcome": outcome,
            "asset": asset,
            "tx_hash": f"momentum-{slug}-{int(time.time())}",
            "timestamp": time.time(),
            "source": "binance_momentum",
            "momentum_pct": momentum_pct,
            "time_remaining_secs": int(secs_left),
            "market_window_secs": window,
            "liq_conviction": liq_conviction,
            "shadow": shadow,
            "condition_id": market_info.get("condition_id", ""),
        }

        shadow_tag = " [SHADOW]" if shadow else ""
        self._logger.info(
            f"Signal generated{shadow_tag}: {slug} {outcome} @ {midpoint:.4f} "
            f"({secs_left:.0f}s remaining, mid_src={midpoint_source})"
        )

        # Update cooldown timer BEFORE sending signal (not for shadow)
        if not shadow:
            self._last_signal_time[asset.upper()] = time.time()

        await self._on_signal(signal)

    async def _discover_market(self, slug: str) -> Optional[dict]:
        """Query Gamma API for market token IDs. Caches results per slug."""
        if slug in self._market_cache:
            return self._market_cache[slug]

        try:
            url = f"{GAMMA_API_URL}/markets?slug={slug}"
            session = self._gamma_session or aiohttp.ClientSession()
            close_session = self._gamma_session is None
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        self._logger.warning(f"Gamma API {resp.status} for {slug}")
                        return None
                    data = await resp.json()
            finally:
                if close_session:
                    await session.close()

            if not data:
                self._market_cache[slug] = None
                return None

            market = data[0] if isinstance(data, list) else data

            # CRITICAL: clobTokenIds and outcomes are JSON STRINGS, not lists
            token_ids = json.loads(market["clobTokenIds"])
            outcomes = json.loads(market["outcomes"])

            # Fee gate: only halt if using taker entry on 5m (maker orders are fee-free)
            fee_rate = float(market.get("fee_rate_bps", 0) or 0)
            if fee_rate > 0 and "5m" in slug and self._config.taker_on_5m:
                self._logger.critical(
                    f"FEE GATE: 5m market {slug} has fee_rate_bps={fee_rate} "
                    f"and taker_on_5m=True — taker fees would eat edge. "
                    f"HALTING entries. Set TAKER_ON_5M=false to use fee-free maker."
                )
                self._fee_gate_active = True
                return None
            elif fee_rate > 0 and "5m" in slug:
                self._logger.info(
                    f"5m market {slug} has fee_rate_bps={fee_rate} — "
                    f"using maker entry (fee-free), continuing"
                )

            # outcomes = ["Up", "Down"], token_ids[0] = Up, token_ids[1] = Down
            info = {
                "up_token_id": token_ids[0],
                "down_token_id": token_ids[1],
                "market_title": market.get("question", slug),
                "condition_id": market.get("conditionId", ""),
            }
            self._market_cache[slug] = info

            # Auto-subscribe to MarketWS for real-time midpoints
            if self._market_ws:
                await self._market_ws.subscribe([token_ids[0], token_ids[1]])

            return info

        except Exception as e:
            self._logger.warning(f"Gamma API error for {slug}: {e}")
            return None

    # ========================================================================
    # Window Delta: Buy winning side at T-N seconds before 5m window close
    # ========================================================================

    async def _check_window_delta(self, symbol: str, now: float, current_price: float) -> None:
        """At T-lead_secs before each 5m window close, buy the likely winner."""
        asset = BINANCE_TO_ASSET.get(symbol)
        if not asset:
            return

        # Check asset filter
        delta_assets = self._config.window_delta_assets.strip()
        if delta_assets:
            allowed = [a.strip().upper() for a in delta_assets.split(',') if a.strip()]
        else:
            allowed = self._config.get_asset_filter()
        if allowed and asset.upper() not in allowed:
            return

        window = 300  # 5m only
        current_epoch = (int(now) // window) * window
        window_end = current_epoch + window
        secs_to_end = window_end - now

        # Record open price at start of each window (first tick after epoch boundary)
        key = (asset, current_epoch)
        if key not in self._window_open_prices:
            self._window_open_prices[key] = current_price
            return

        # Check if we're in the firing window
        lead = self._config.window_delta_lead_secs
        if secs_to_end > lead or secs_to_end < 2:
            return

        # Already fired for this window?
        delta_slug = f"{asset.lower()}-updown-5m-{current_epoch}"
        if delta_slug in self._delta_fired:
            return

        # Check price direction
        open_price = self._window_open_prices[key]
        if open_price <= 0:
            return
        pct_change = (current_price - open_price) / open_price

        if abs(pct_change) < self._config.window_delta_min_pct:
            return

        direction = "UP" if pct_change > 0 else "DOWN"
        self._delta_fired.add(delta_slug)

        self._logger.info(
            f"Window delta trigger: {asset} {direction} {pct_change:+.3%} "
            f"(open={open_price:.4f} now={current_price:.4f}, {secs_to_end:.0f}s left)"
        )

        await self._generate_delta_signal(asset, direction, delta_slug, pct_change, secs_to_end)

    async def _generate_delta_signal(self, asset: str, direction: str, slug: str,
                                      pct_change: float, secs_left: float) -> None:
        """Generate a window delta trading signal."""
        market_info = await self._discover_market(slug)
        if not market_info:
            self._logger.warning(f"Window delta: market not found: {slug}")
            return

        outcome = "Up" if direction == "UP" else "Down"
        token_id = (
            market_info["up_token_id"] if outcome == "Up"
            else market_info["down_token_id"]
        )

        # Prefer WS midpoint over REST
        midpoint = 0.0
        if self._market_ws:
            midpoint = self._market_ws.get_midpoint(token_id)
        if midpoint <= 0:
            midpoint = await self._clob.get_midpoint(token_id)
        if midpoint <= 0:
            self._logger.warning(f"Window delta: no midpoint for {slug} {outcome}")
            return

        if midpoint > self._config.window_delta_max_price:
            self._logger.debug(
                f"Window delta: {slug} {outcome} midpoint {midpoint:.4f} > "
                f"window_delta_max_price {self._config.window_delta_max_price} — skip"
            )
            return

        self.signals_generated += 1

        signal = {
            "token_id": token_id,
            "price": midpoint,
            "slug": slug,
            "market_title": market_info.get("market_title", slug),
            "usdc_size": 999.0,
            "direction": "BUY",
            "outcome": outcome,
            "asset": asset,
            "tx_hash": f"window-delta-{slug}-{int(time.time())}",
            "timestamp": time.time(),
            "source": "window_delta",
            "momentum_pct": pct_change,
            "time_remaining_secs": int(secs_left),
            "market_window_secs": 300,
        }

        self._logger.info(
            f"Window delta signal: {slug} {outcome} @ {midpoint:.4f} "
            f"({secs_left:.0f}s left, delta={pct_change:+.3%})"
        )

        await self._on_signal(signal)

    def prune_stale(self) -> None:
        """Prune old signaled slugs and market cache. Call periodically.

        Parses window from each slug to correctly handle mixed 5m/15m markets.
        A slug is stale if market_end + one_window < now (buffer for late processing).
        """
        from .types import parse_window_from_slug
        now = time.time()
        stale_slugs = set()
        for slug in self._signaled_slugs:
            parts = slug.rsplit('-', 1)
            if len(parts) == 2 and parts[1].isdigit():
                epoch = int(parts[1])
                window = parse_window_from_slug(slug)
                market_end = epoch + window
                if market_end + window < now:
                    stale_slugs.add(slug)
        for s in stale_slugs:
            self._signaled_slugs.discard(s)
            self._delta_fired.discard(s)
            self._market_cache.pop(s, None)

        # Persist pruned slugs to disk
        self._state_store.save("signaled_slugs", self._signaled_slugs)

        # Prune old window open prices (keep only current + previous window)
        now = time.time()
        stale_keys = [
            k for k in self._window_open_prices
            if k[1] + 600 < now  # older than 2 windows
        ]
        for k in stale_keys:
            del self._window_open_prices[k]

    # ========================================================================
    # Liquidation WebSocket Feed (shadow mode — log + regime updates only)
    # ========================================================================

    async def _liquidation_connect_loop(self) -> None:
        """Connect to Binance Futures liquidation stream with backoff."""
        attempt = 0
        while True:
            try:
                self._liq_session = aiohttp.ClientSession()
                url = f"{BINANCE_FUTURES_WS_URL}/!forceOrder@arr"
                self._liq_ws = await self._liq_session.ws_connect(url, timeout=10)
                self._logger.info("Binance liquidation WS connected")
                attempt = 0
                await self._read_liquidation_loop()
            except asyncio.CancelledError:
                self._logger.info("Liquidation feed cancelled")
                raise
            except Exception as e:
                delay = min(BACKOFF_BASE * (BACKOFF_MULTIPLIER ** attempt), BACKOFF_MAX)
                self._logger.warning(f"Liquidation WS error: {e}, retry in {delay}s")
                attempt += 1
                await asyncio.sleep(delay)
            finally:
                await self._close_liq_session()

    async def _read_liquidation_loop(self) -> None:
        """Read liquidation events from Binance Futures WS."""
        async for msg in self._liq_ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    self._process_liquidation_event(data)
                except (json.JSONDecodeError, Exception) as e:
                    if not isinstance(e, json.JSONDecodeError):
                        self._logger.debug(f"Liquidation msg error: {e}")
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break

    def _process_liquidation_event(self, data: dict) -> None:
        """Process a single Binance forced liquidation event.

        Event format: {"e":"forceOrder","E":1234,"o":{
            "s":"BTCUSDT","S":"SELL","o":"LIMIT","f":"IOC",
            "q":"0.014","p":"97000.00","ap":"96950.00","X":"FILLED","l":"0.014","z":"0.014","T":1234
        }}
        """
        order = data.get("o", {})
        symbol = order.get("s", "")  # e.g. "BTCUSDT"
        asset = FUTURES_TO_ASSET.get(symbol)
        if not asset:
            return

        side = order.get("S", "")  # "SELL" = long liquidated, "BUY" = short liquidated
        qty = float(order.get("q", 0))
        avg_price = float(order.get("ap", 0))
        usd_value = qty * avg_price

        if usd_value <= 0:
            return

        now = time.time()
        self.liquidation_events += 1

        # Buffer for rolling window
        if asset not in self._liq_buffers:
            self._liq_buffers[asset] = deque(maxlen=600)
        self._liq_buffers[asset].append((now, side, usd_value))

        # Calculate rolling 60s stats
        cutoff = now - 60
        total_volume = 0.0
        long_volume = 0.0
        short_volume = 0.0
        for ts, s, val in self._liq_buffers[asset]:
            if ts >= cutoff:
                total_volume += val
                if s == "SELL":  # long liquidated
                    long_volume += val
                else:
                    short_volume += val

        bias = ""
        if long_volume > short_volume * 2:
            bias = "long"
        elif short_volume > long_volume * 2:
            bias = "short"

        # Update regime detector
        if self._regime_detector:
            self._regime_detector.update_liquidation(asset, total_volume, bias)

        # Log significant liquidations (>$100K in 60s or single >$50K)
        if usd_value > 50_000 or total_volume > 100_000:
            liq_type = "LONG" if side == "SELL" else "SHORT"
            self._logger.info(
                f"Liquidation: {asset} {liq_type} ${usd_value:,.0f} "
                f"(60s total: ${total_volume:,.0f}, bias={bias or 'neutral'})"
            )

    async def _close_liq_session(self) -> None:
        """Close liquidation WebSocket and session safely."""
        try:
            if self._liq_ws:
                await self._liq_ws.close()
        except Exception:
            pass
        try:
            if self._liq_session:
                await self._liq_session.close()
        except Exception:
            pass
        self._liq_ws = None
        self._liq_session = None

    # ========================================================================
    # Funding Rate Polling (hourly, updates regime detector)
    # ========================================================================

    async def _funding_rate_poll_loop(self) -> None:
        """Poll Binance funding rates every hour. Updates regime detector."""
        while True:
            try:
                await self._fetch_funding_rates()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._logger.warning(f"Funding rate poll error: {e}")
            await asyncio.sleep(3600)  # 1 hour

    async def _fetch_funding_rates(self) -> None:
        """Fetch latest funding rates from Binance Futures REST API."""
        if not self._regime_detector:
            return

        async with aiohttp.ClientSession() as session:
            for asset, futures_symbol in ASSET_TO_FUTURES.items():
                try:
                    url = f"{BINANCE_FUNDING_URL}?symbol={futures_symbol}&limit=1"
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.json()
                    if data and isinstance(data, list):
                        rate = float(data[0].get("fundingRate", 0))
                        self._regime_detector.update_funding(asset, rate)
                except Exception as e:
                    self._logger.debug(f"Funding rate error for {asset}: {e}")

    async def _market_prefetch_loop(self) -> None:
        """Background task: pre-fetch upcoming markets from Gamma API.

        Epoch-aligned: wakes up 2s before each new 5m boundary to discover
        and subscribe to the upcoming window's tokens on MarketWS. This ensures
        we have real-time book data BEFORE momentum fires, eliminating the
        Gamma API + REST midpoint latency from the critical path.
        """
        allowed = self._config.get_asset_filter()
        shadow = self._config.get_shadow_assets()
        all_assets = set(a.upper() for a in allowed) | set(shadow)
        assets = [a.lower() for a in all_assets] if all_assets else ["btc"]

        while True:
            try:
                now = time.time()
                new_discoveries = 0
                for asset in assets:
                    for window in self._config.get_market_windows(asset.upper()):
                        epoch_base = int(now // window) * window
                        # Pre-fetch current + next 3 windows
                        for offset in (0, window, window * 2, window * 3):
                            epoch = epoch_base + offset
                            label = f"{window // 60}m"
                            slug = f"{asset}-updown-{label}-{epoch}"
                            if slug not in self._market_cache:
                                info = await self._discover_market(slug)
                                if info:
                                    new_discoveries += 1
                if new_discoveries:
                    prefetched = sum(1 for v in self._market_cache.values() if v is not None)
                    self._logger.info(f"Prefetch: +{new_discoveries} new, {prefetched} cached total")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._logger.warning(f"Market prefetch error: {e}")

            # Epoch-aligned sleep: wake 2s before next 5m boundary, max 15s
            now = time.time()
            next_boundary = (int(now // 300) + 1) * 300
            secs_to_boundary = next_boundary - now
            sleep_time = min(max(secs_to_boundary - 2, 1), 15)
            await asyncio.sleep(sleep_time)

    # -----------------------------------------------------------------------
    # Pair arb scan (Phase 2) — wired via signal_bot._safe_task(), not gather()
    # -----------------------------------------------------------------------

    def increment_pair_arb_count(self) -> None:
        self._active_pair_arb_count += 1

    def decrement_pair_arb_count(self) -> None:
        self._active_pair_arb_count = max(0, self._active_pair_arb_count - 1)

    async def pair_arb_scan_loop(self) -> None:
        """Scan every 30s for 5m markets with pair_cost below threshold.
        Wired via signal_bot._safe_task() — crash here never kills the price feed.
        Maker-only: taker fees at ~3.12% destroy the margin at median pair_cost.
        """
        while True:
            try:
                await self._do_pair_arb_scan()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._logger.warning(f"pair_arb_scan error (non-fatal): {e}")
            await asyncio.sleep(30)

    async def _do_pair_arb_scan(self) -> None:
        """Check current 5m epoch for each asset. Log or emit pair arb signals."""
        allowed = self._config.get_asset_filter() or ["BTC"]
        for asset in allowed:
            epoch = int(time.time() // 300) * 300
            slug = f"{asset.lower()}-updown-5m-{epoch}"

            market_info = self._market_cache.get(slug)
            if not market_info:
                self._logger.debug(f"pair_arb: {slug} not in market_cache (prefetch pending)")
                continue

            up_token_id = market_info.get("up_token_id")
            down_token_id = market_info.get("down_token_id")
            if not up_token_id or not down_token_id:
                continue

            if not self._market_ws:
                continue

            # Ensure tokens are subscribed — idempotent, no-op if already tracked
            await self._market_ws.subscribe([up_token_id, down_token_id])

            ask_up = self._market_ws.get_best_ask(up_token_id)
            ask_down = self._market_ws.get_best_ask(down_token_id)

            # Zero-guard: skip if MarketWS has no data yet for either side
            if ask_up <= 0.0 or ask_down <= 0.0:
                self._logger.debug(
                    f"pair_arb: {slug} skipped (no MarketWS data: up={ask_up}, down={ask_down})"
                )
                continue

            pair_cost = ask_up + ask_down
            threshold = self._config.pair_arb_max_pair_cost
            triggered = pair_cost < threshold

            self._logger.info(
                f"pair_arb_scan: {asset} pair_cost={pair_cost:.4f} "
                f"threshold={threshold:.4f} {'TRIGGER' if triggered else 'skip'}"
            )

            if not triggered:
                continue

            if self._active_pair_arb_count >= self._config.pair_arb_max_concurrent:
                self._logger.debug(
                    f"pair_arb: {slug} skipped (max concurrent={self._config.pair_arb_max_concurrent})"
                )
                continue

            if self._config.pair_arb_dry_run:
                self._logger.info(
                    f"pair_arb DRY: would enter {asset} "
                    f"pair_cost={pair_cost:.4f} (up={ask_up:.4f} down={ask_down:.4f})"
                )
                continue

            # Emit both legs
            up_signal = self._build_pair_arb_signal(
                asset, "Up", up_token_id, slug, ask_up, pair_cost, market_info, epoch
            )
            down_signal = self._build_pair_arb_signal(
                asset, "Down", down_token_id, slug, ask_down, pair_cost, market_info, epoch
            )
            await self._on_signal(up_signal)
            await self._on_signal(down_signal)

    def _build_pair_arb_signal(
        self,
        asset: str,
        outcome: str,
        token_id: str,
        slug: str,
        price: float,
        pair_cost: float,
        market_info: dict,
        epoch: int,
    ) -> dict:
        """Build a pair arb signal dict. Slug uses ':up'/':down' suffix to bypass dedup."""
        end_epoch = epoch + 300
        time_remaining = max(0, int(end_epoch - time.time()))
        return {
            "token_id": token_id,
            "price": price,
            "slug": f"{slug}:{outcome.lower()}",  # ':up' / ':down' suffix bypasses dedup
            "source": "pair_arb",
            "outcome": outcome,
            "direction": "BUY",
            "asset": asset,
            "pair_cost": pair_cost,
            "strategy": "maker",
            "market_title": market_info.get("market_title", slug),
            "condition_id": market_info.get("condition_id", ""),
            "timestamp": time.time(),
            "usdc_size": 999.0,   # bypass scorer minimum conviction check
            "time_remaining_secs": time_remaining,
            "market_window_secs": 300,
        }

    async def close(self) -> None:
        """Clean up persistent sessions."""
        try:
            if self._gamma_session:
                await self._gamma_session.close()
                self._gamma_session = None
        except Exception:
            pass

    async def _close_session(self) -> None:
        """Close WebSocket and session safely."""
        try:
            if self._ws:
                await self._ws.close()
        except Exception:
            pass
        try:
            if self._session:
                await self._session.close()
        except Exception:
            pass
        self._ws = None
        self._session = None
