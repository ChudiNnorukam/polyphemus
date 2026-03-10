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

    def __init__(self, config: Settings, clob: ClobWrapper, on_signal: Callable,
                 signal_logger=None):
        self._config = config
        self._clob = clob
        self._on_signal = on_signal  # async callback
        self._signal_logger = signal_logger  # for dry-run signal tracking
        self._logger = setup_logger("polyphemus.momentum")

        # State persistence (prevents duplicate signals on restart)
        self._state_store = StateStore(data_dir=config.lagbot_data_dir, default_ttl_secs=3600)
        self._signaled_slugs: Set[str] = self._state_store.load("signaled_slugs", ttl_secs=3600)

        # Per-symbol rolling price buffer: symbol -> deque of (timestamp, price)
        self._price_buffers: Dict[str, deque] = {
            symbol: deque(maxlen=600)  # ~10 min of 1s updates
            for symbol in BINANCE_SYMBOLS
        }

        # Sharp move detector: separate short-window buffer
        self._sharp_buffers: Dict[str, deque] = {
            symbol: deque(maxlen=30)  # ~30s of 1s updates
            for symbol in BINANCE_SYMBOLS
        }
        self._sharp_cooldown: Dict[str, float] = {}  # asset_dir -> last_fire_time

        # Per-asset momentum cooldown: asset -> last_signal_time
        self._momentum_cooldown: Dict[str, float] = self._state_store.load("momentum_cooldown", ttl_secs=300)
        self._last_prune_time: float = 0.0

        # Market info cache: slug -> {"up_token_id", "down_token_id", "market_title"}
        self._market_cache: Dict[str, Optional[dict]] = {}

        # Window Delta: track open prices per (asset, window_epoch)
        self._window_open_prices: Dict[tuple, float] = {}
        self._delta_fired: Set[str] = set()  # slugs already fired for delta
        self._snipe_fired: Set[str] = set()  # slugs already fired for resolution snipe
        self._near_res_fired: Set[str] = set()  # epochs already fired for near-res pair arb

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
        self._fee_gate_set_at: float = 0.0

        # Persistent Gamma API session (reused across _discover_market calls)
        self._gamma_session: Optional[aiohttp.ClientSession] = None

        # Market pre-cache background task + MarketWS reference (set by signal_bot)
        self._market_ws = None  # type: Optional[MarketWS]
        self._prefetch_task: Optional[asyncio.Task] = None

        # Taker CVD buffers: symbol -> deque of (timestamp, signed_qty)
        # positive = taker buy, negative = taker sell
        self._taker_buffers: Dict[str, deque] = {
            symbol: deque(maxlen=1200)  # ~20 min of trade data
            for symbol in BINANCE_SYMBOLS
        }

        # Coinbase Premium: concurrent price feed for cross-exchange divergence
        self._coinbase_prices: Dict[str, float] = {}  # symbol -> latest price
        self._coinbase_session: Optional[aiohttp.ClientSession] = None
        self._coinbase_ws: Optional[aiohttp.ClientWebSocketResponse] = None

        # Liquidation tracking: asset -> deque of (timestamp, side, usd_value)
        self._liq_buffers: Dict[str, deque] = {}
        self._liq_ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._liq_session: Optional[aiohttp.ClientSession] = None
        self.liquidation_events = 0

        # Pair arb: active concurrent position counter (managed by signal_bot)
        self._active_pair_arb_count: int = 0

        # Snipe dry-run resolution tracking: list of (epoch_end_ts, signal_id, token_id, entry_price)
        self._snipe_pending: list = []

        # Optional Chainlink oracle feed for snipe confirmation (set by signal_bot)
        self._chainlink = None

        # S4: Streak tracking - per-asset list of recent epoch outcomes ("up"/"down")
        self._epoch_outcomes: Dict[str, list] = {}  # asset -> [(epoch, "up"/"down"), ...]
        self._streak_last_checked_epoch: Dict[str, int] = {}  # asset -> last epoch we checked

    def set_market_ws(self, ws) -> None:
        """Inject MarketWS reference for real-time midpoints."""
        self._market_ws = ws

    def set_chainlink_feed(self, feed) -> None:
        """Inject ChainlinkFeed for snipe confirmation gate."""
        self._chainlink = feed
        # Wire event-driven oracle flip: fires on direction crossover
        if feed and self._config.oracle_flip_enabled:
            feed.set_on_direction_cross(self._on_oracle_direction_cross)

    def _record_epoch_outcome(self, asset: str, epoch: int, window: int) -> None:
        """Record the resolved outcome of a past epoch using Binance price data.

        Called at the start of each new epoch to record the previous epoch's result.
        Uses Binance price buffer (same data as momentum detection) to determine
        whether price was above or below epoch open at epoch end.
        """
        if not self._config.streak_tracking_enabled:
            return
        # Only track 5m epochs for now
        if window != 300:
            return
        # Skip if already recorded
        last = self._streak_last_checked_epoch.get(asset, 0)
        if epoch <= last:
            return
        self._streak_last_checked_epoch[asset] = epoch

        # Use Chainlink/RTDS for authoritative outcome (it IS the resolution source)
        if self._chainlink and self._chainlink.is_healthy(asset):
            verdict = self._chainlink.is_above_window_open(epoch, window, asset)
            if verdict is not None:
                outcome = "up" if verdict else "down"
                if asset not in self._epoch_outcomes:
                    self._epoch_outcomes[asset] = []
                self._epoch_outcomes[asset].append((epoch, outcome))
                # Keep last 20 epochs max
                if len(self._epoch_outcomes[asset]) > 20:
                    self._epoch_outcomes[asset] = self._epoch_outcomes[asset][-20:]
                self._logger.debug(
                    f"Streak tracker: {asset} epoch {epoch} resolved {outcome} | "
                    f"history={len(self._epoch_outcomes[asset])}"
                )

    def _get_current_streak(self, asset: str) -> tuple:
        """Return (streak_length, streak_direction) for an asset.

        Returns (0, "") if no streak or insufficient data.
        A streak of 3 means the last 3 epochs all resolved in the same direction.
        """
        history = self._epoch_outcomes.get(asset, [])
        if len(history) < self._config.streak_min_length:
            return (0, "")
        # Count consecutive same-direction from the end
        last_dir = history[-1][1]
        streak = 1
        for i in range(len(history) - 2, -1, -1):
            if history[i][1] == last_dir:
                streak += 1
            else:
                break
        if streak >= self._config.streak_min_length:
            return (streak, last_dir)
        return (0, "")

    async def _on_oracle_direction_cross(
        self, asset: str, epoch: int, window: int,
        is_above_open: bool, price: float, open_price: float,
    ) -> None:
        """Event-driven oracle flip: fired directly by ChainlinkFeed on direction cross.

        Bypasses snipe loop polling for ~25ms faster response.
        """
        if not self._config.oracle_flip_enabled:
            return

        now = time.time()
        window_end = epoch + window
        secs_left = window_end - now
        if secs_left > self._config.oracle_flip_max_secs_remaining or secs_left < 8:
            return

        oracle_delta_pct = abs(price - open_price) / open_price if open_price > 0 else 0.0
        if oracle_delta_pct < self._config.oracle_flip_min_delta_pct:
            return

        slug = f"{asset.lower()}-updown-{window // 60}m-{epoch}"
        if slug in self._signaled_slugs:
            return

        market_info = self._market_cache.get(slug)
        if not market_info:
            return

        # Determine which side the oracle says will win
        flip_outcome = "Up" if is_above_open else "Down"
        flip_key = "up_token_id" if is_above_open else "down_token_id"
        flip_token_id = market_info[flip_key]

        flip_mid = 0.0
        if self._market_ws:
            flip_mid = self._market_ws.get_midpoint(flip_token_id)
        if flip_mid <= 0:
            try:
                flip_mid = await self._clob.get_midpoint(flip_token_id)
            except Exception:
                pass

        if (flip_mid <= 0
                or flip_mid > self._config.oracle_flip_max_opposite_price
                or flip_mid < self._config.oracle_flip_min_opposite_price):
            return

        if self._config.oracle_flip_dry_run:
            potential_shares = self._config.oracle_flip_max_bet / flip_mid
            # NOTE: actual P&L depends on resolution, NOT assumed win.
            # Log IF-WIN profit for reference, but track real outcome via signal_logger.
            if_win_profit = potential_shares * (1.0 - flip_mid)
            self._logger.info(
                f"[ORACLE_FLIP_EVENT DRY] {slug} FLIP to {flip_outcome} "
                f"@ {flip_mid:.4f} | oracle={price:,.2f} open={open_price:,.2f} | "
                f"shares={potential_shares:.0f} if_win=${if_win_profit:.2f} | "
                f"OUTCOME PENDING (check resolution)"
            )
            # Still emit signal for signal_logger to track real outcome
            flip_signal = {
                "slug": slug,
                "token_id": flip_token_id,
                "outcome": flip_outcome,
                "midpoint": flip_mid,
                "price": flip_mid,
                "source": "oracle_flip",
                "asset": asset,
                "market_window_secs": window,
                "time_remaining_secs": int(secs_left),
                "metadata": {
                    "source": "oracle_flip",
                    "event_driven": True,
                    "dry_run": True,
                    "oracle_price": price,
                    "window_open_price": open_price,
                },
            }
            self._signaled_slugs.add(slug)
            if self._signal_logger:
                self._signal_logger.log_signal(flip_signal, guard_passed=True)
            return

        self._logger.info(
            f"[ORACLE_FLIP_EVENT] {slug} FLIPPING to {flip_outcome} "
            f"@ {flip_mid:.4f} | oracle={price:,.2f} open={open_price:,.2f} | "
            f"event-driven (0ms loop latency)"
        )
        flip_best_ask = self._market_ws.get_best_ask(flip_token_id) if self._market_ws else 0.0
        flip_signal = {
            "slug": slug,
            "token_id": flip_token_id,
            "outcome": flip_outcome,
            "midpoint": flip_mid,
            "price": flip_mid,
            "best_ask": flip_best_ask,
            "source": "oracle_flip",
            "asset": asset,
            "market_window_secs": window,
            "time_remaining_secs": int(secs_left),
            "condition_id": market_info.get("condition_id", ""),
            "metadata": {
                "source": "oracle_flip",
                "event_driven": True,
                "oracle_price": price,
                "window_open_price": open_price,
                "flip_multiplier": round((1.0 - flip_mid) / flip_mid, 1),
            },
        }
        self._signaled_slugs.add(slug)
        await self._on_signal(flip_signal)
        self._state_store.save("signaled_slugs", self._signaled_slugs)

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

        tasks = [
            self._connect_loop(),
            self._liquidation_connect_loop(),
            self._funding_rate_poll_loop(),
            self._market_prefetch_loop(),
            self._watch_new_markets(),
        ]
        if self._config.coinbase_premium_enabled:
            tasks.append(self._coinbase_premium_loop())
        if self._config.enable_resolution_snipe:
            tasks.append(self._snipe_loop())
            if self._config.snipe_dry_run and self._signal_logger:
                tasks.append(self._snipe_resolution_loop())
        await asyncio.gather(*tasks)

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
                    kline_streams = [f"{symbol}@kline_1s" for symbol in BINANCE_SYMBOLS]
                    agg_streams = [f"{symbol}@aggTrade" for symbol in BINANCE_SYMBOLS]
                    streams = "/".join(kline_streams + agg_streams)
                    url = f"{BINANCE_WS_URL}?streams={streams}"
                    self._ws = await self._session.ws_connect(url, timeout=10)
                    self._logger.info("Binance momentum+aggTrade WS connected")
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
                self._sharp_buffers[symbol].append((now, price))
                if self._regime_detector:
                    asset = BINANCE_TO_ASSET.get(symbol)
                    if asset:
                        self._regime_detector.update(asset, price, now)
                await self._check_momentum(symbol, now, price)
                if self._config.enable_sharp_move:
                    await self._check_sharp_move(symbol, now, price)
                if self._config.enable_window_delta:
                    await self._check_window_delta(symbol, now, price)

    async def _process_binance_update(self, data: dict) -> None:
        """Process a Binance kline or aggTrade update."""
        inner = data.get("data", {})
        event_type = inner.get("e", "")

        # Route aggTrade events to taker buffer
        if event_type == "aggTrade":
            self._process_aggtrade(inner)
            return

        # Kline processing (existing logic)
        k = inner.get("k", {})
        symbol = inner.get("s", "").lower()
        if symbol not in self._price_buffers:
            return

        price = float(k.get("c", 0))
        if price <= 0:
            return

        now = time.time()
        self._price_buffers[symbol].append((now, price))
        self._sharp_buffers[symbol].append((now, price))

        # Feed regime detector with every price tick
        if self._regime_detector:
            asset = BINANCE_TO_ASSET.get(symbol)
            if asset:
                self._regime_detector.update(asset, price, now)

        # Check momentum
        await self._check_momentum(symbol, now, price)

        # Check sharp move (15s sub-window)
        if self._config.enable_sharp_move:
            await self._check_sharp_move(symbol, now, price)

        # Check window delta (T-10 late entry)
        if self._config.enable_window_delta:
            await self._check_window_delta(symbol, now, price)

    def _process_aggtrade(self, data: dict) -> None:
        """Accumulate taker buy/sell volume from aggTrade events."""
        symbol = data.get("s", "").lower()
        if symbol not in self._taker_buffers:
            return
        qty = float(data.get("q", 0))
        is_buyer_maker = data.get("m", False)
        # m=True means buyer is maker, so TAKER is seller (negative delta)
        # m=False means taker is buyer (positive delta)
        signed_qty = -qty if is_buyer_maker else qty
        ts = float(data.get("T", 0)) / 1000.0  # trade time in ms -> seconds
        if ts <= 0:
            ts = time.time()
        self._taker_buffers[symbol].append((ts, signed_qty))

    def get_taker_delta(self, symbol: str, window_secs: float) -> Optional[float]:
        """Compute net taker delta over the last window_secs seconds."""
        buffer = self._taker_buffers.get(symbol)
        if not buffer:
            return None
        cutoff = time.time() - window_secs
        delta = sum(qty for ts, qty in buffer if ts >= cutoff)
        return delta

    def get_vpin(self, symbol: str, window_secs: float = 300, n_buckets: int = 10) -> Optional[float]:
        """Compute VPIN (Volume-Synchronized Probability of Informed Trading).

        Splits recent volume into n_buckets time buckets, classifies each as
        buy-dominated or sell-dominated, then computes the average imbalance.
        Returns 0.0 (balanced/noise) to 1.0 (fully informed/one-sided).
        """
        buffer = self._taker_buffers.get(symbol)
        if not buffer:
            return None
        now = time.time()
        cutoff = now - window_secs
        trades = [(ts, qty) for ts, qty in buffer if ts >= cutoff]
        if len(trades) < 20:
            return None  # insufficient data

        bucket_size = window_secs / n_buckets
        total_imbalance = 0.0
        total_volume = 0.0
        for i in range(n_buckets):
            bucket_start = cutoff + i * bucket_size
            bucket_end = bucket_start + bucket_size
            buy_vol = sum(abs(q) for t, q in trades if bucket_start <= t < bucket_end and q > 0)
            sell_vol = sum(abs(q) for t, q in trades if bucket_start <= t < bucket_end and q < 0)
            bucket_vol = buy_vol + sell_vol
            if bucket_vol > 0:
                total_imbalance += abs(buy_vol - sell_vol)
                total_volume += bucket_vol

        if total_volume <= 0:
            return None
        return total_imbalance / total_volume

    def get_coinbase_premium(self, asset: str) -> Optional[float]:
        """Compute Coinbase Premium in basis points.

        Premium = (Coinbase_price - Binance_price) / Binance_price * 10000
        Positive = US institutional buying (bullish).
        Negative = US institutional selling (bearish).
        Returns None if either price feed is unavailable.
        """
        binance_symbol = ASSET_TO_BINANCE.get(asset)
        if not binance_symbol:
            return None

        # Get latest Binance price from momentum buffer
        buf = self._price_buffers.get(binance_symbol)
        if not buf:
            return None
        binance_price = buf[-1][1]  # (timestamp, price)

        # Get latest Coinbase price
        coinbase_price = self._coinbase_prices.get(binance_symbol)
        if not coinbase_price or binance_price <= 0:
            return None

        return ((coinbase_price - binance_price) / binance_price) * 10_000

    async def _coinbase_premium_loop(self) -> None:
        """Concurrent Coinbase WS feed for cross-exchange premium calculation.

        Runs alongside the primary Binance feed. Does NOT generate signals --
        only maintains _coinbase_prices for premium computation.
        """
        attempt = 0
        while True:
            try:
                self._coinbase_session = aiohttp.ClientSession()
                self._coinbase_ws = await self._coinbase_session.ws_connect(
                    COINBASE_WS_URL, timeout=10
                )
                await self._coinbase_ws.send_json({
                    "type": "subscribe",
                    "product_ids": COINBASE_PRODUCTS,
                    "channel": "ticker",
                })
                self._logger.info("Coinbase Premium WS connected (parallel feed)")
                attempt = 0

                async for msg in self._coinbase_ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            data = json.loads(msg.data)
                            if data.get("type") == "ticker" and "price" in data:
                                product_id = data.get("product_id", "")
                                symbol = COINBASE_TO_SYMBOL.get(product_id)
                                if symbol:
                                    self._coinbase_prices[symbol] = float(data["price"])
                        except (json.JSONDecodeError, ValueError):
                            continue
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break

            except asyncio.CancelledError:
                self._logger.info("Coinbase Premium feed cancelled")
                raise
            except Exception as e:
                delay = min(BACKOFF_BASE * (BACKOFF_MULTIPLIER ** attempt), BACKOFF_MAX)
                self._logger.warning(f"Coinbase Premium WS error: {e}, retry in {delay}s")
                attempt += 1
                await asyncio.sleep(delay)
            finally:
                try:
                    if self._coinbase_ws and not self._coinbase_ws.closed:
                        await self._coinbase_ws.close()
                    if self._coinbase_session and not self._coinbase_session.closed:
                        await self._coinbase_session.close()
                except Exception:
                    pass

    async def _check_momentum(self, symbol: str, now: float, current_price: float) -> None:
        """Check if price has moved enough within the window to trigger a signal."""
        buffer = self._price_buffers[symbol]
        window = self._config.momentum_window_secs
        threshold = self._config.momentum_trigger_pct

        # Buffer fullness guard: require data spanning at least 80% of window
        # After reconnect, buffer has only seconds of data — small moves in 5s
        # can exceed 0.3% threshold and trigger false signals
        if len(buffer) < 2:
            return
        buffer_span = buffer[-1][0] - buffer[0][0]
        if buffer_span < window * 0.8:
            return

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

    async def _check_sharp_move(self, symbol: str, now: float, current_price: float) -> None:
        """Detect sharp price spikes (e.g. 0.2% in 15s) that create brief MM lag."""
        buffer = self._sharp_buffers[symbol]
        window = self._config.sharp_move_window_secs
        threshold = self._config.sharp_move_trigger_pct

        # Buffer fullness guard (same as momentum): require 80% of window span
        if len(buffer) < 2:
            return
        buffer_span = buffer[-1][0] - buffer[0][0]
        if buffer_span < window * 0.8:
            return

        cutoff = now - window
        oldest_price = None
        for ts, price in buffer:
            if ts >= cutoff:
                oldest_price = price
                break

        if oldest_price is None or oldest_price <= 0:
            return

        pct_change = (current_price - oldest_price) / oldest_price
        if abs(pct_change) < threshold:
            return

        direction = "UP" if pct_change > 0 else "DOWN"
        asset = BINANCE_TO_ASSET.get(symbol)
        if not asset:
            return

        # Per-asset cooldown: suppress duplicate sharp detections for 30s
        cooldown_key = f"sharp_{asset}_{direction}"
        last_fire = self._sharp_cooldown.get(cooldown_key, 0.0)
        if now - last_fire < 30.0:
            return
        self._sharp_cooldown[cooldown_key] = now

        is_shadow = self._config.sharp_move_shadow
        tag = " [SHADOW]" if is_shadow else ""
        self._logger.info(
            f"Sharp move{tag}: {asset} {direction} {pct_change:+.3%} "
            f"in {window}s ({oldest_price:.4f} -> {current_price:.4f})"
        )

        # Generate signal - shadow mode logs without executing
        await self._generate_signal(
            asset, direction, momentum_pct=pct_change,
            sharp_shadow=is_shadow, source_override="sharp_move"
        )

    async def _generate_signal(self, asset: str, direction: str, momentum_pct: float = 0.0,
                                sharp_shadow: bool = False, source_override: str = "") -> None:
        """Generate trading signals for ALL applicable windows for this asset."""
        # Early filter: skip assets not in allow-list or shadow-list
        allowed = self._config.get_asset_filter()
        shadow = self._config.get_shadow_assets()
        is_shadow = asset.upper() in shadow or sharp_shadow

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
            await self._generate_signal_for_window(
                asset, direction, momentum_pct, window,
                shadow=is_shadow, source_override=source_override
            )

        # Lag signals: fire companion assets with configurable delay after BTC fires
        # Prereq: ETH/SOL WR >= 60% with n >= 30 in backtest before enabling
        if (not is_shadow
                and self._config.enable_lag_signals
                and self._config.lag_assets.strip()):
            direction_outcome = "Up" if direction == "UP" else "Down"
            for lag_spec in self._config.lag_assets.split(","):
                lag_spec = lag_spec.strip()
                if ":" not in lag_spec:
                    continue
                lag_asset_raw, lag_secs_str = lag_spec.split(":", 1)
                try:
                    lag_secs = int(lag_secs_str.strip())
                except ValueError:
                    continue
                lag_asset_upper = lag_asset_raw.strip().upper()
                if allowed and lag_asset_upper not in allowed:
                    continue  # lag asset must be in ASSET_FILTER
                asyncio.create_task(
                    self._fire_lag_signal(lag_asset_upper.lower(), lag_secs, direction_outcome)
                )

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
                                           shadow: bool = False,
                                           source_override: str = "") -> None:
        """Generate a trading signal for a specific market window."""
        # Fee gate: hard block if 5m fees detected (5-min TTL auto-expires)
        if self._fee_gate_active:
            if time.time() - self._fee_gate_set_at > 300:
                self._logger.info("Fee gate expired after 5 min, resuming signal generation")
                self._fee_gate_active = False
            else:
                self._logger.warning(f"Fee gate active, blocking {asset}-{window}s signal")
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
        # 15m momentum uses its own late-entry window (validated in signal_guard FILTER 2b)
        market_end = epoch + window
        secs_left = market_end - time.time()
        is_15m = window == 900
        if is_15m and self._config.enable_15m_momentum:
            min_secs = self._config.momentum_15m_min_secs_remaining
        else:
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
        spread_val = None
        best_bid_val = None
        best_ask_val = None
        book_imbalance_val = None
        if self._market_ws:
            raw_spread = self._market_ws.get_spread(token_id)
            if raw_spread > 0:
                spread_val = raw_spread
            if max_spread > 0 and raw_spread > 0 and raw_spread > max_spread:
                self._logger.info(
                    f"Spread too wide: {slug} {outcome} spread=${raw_spread:.3f} "
                    f"> ${max_spread:.3f} — skipping"
                )
                return
            best_bid_val = self._market_ws.get_best_bid(token_id) or None
            best_ask_val = self._market_ws.get_best_ask(token_id) or None
            depth = self._market_ws.get_book_depth(token_id)
            book_imbalance_val = depth["imbalance"] if depth else None

        # Oracle confirmation gate: block momentum if Chainlink disagrees with direction
        # Only enter when BOTH Binance momentum AND oracle agree on direction
        if (not shadow
                and self._chainlink
                and self._chainlink.is_healthy(asset)
                and self._config.oracle_snipe_confirm
                and not self._config.oracle_snipe_confirm_dry_run):
            epoch_val = epoch
            oracle_verdict = self._chainlink.is_above_window_open(
                epoch_val, window, asset
            )
            if oracle_verdict is not None:
                oracle_says_up = oracle_verdict
                signal_says_up = (outcome == "Up")
                if oracle_says_up != signal_says_up:
                    oracle_price = self._chainlink.get_current_price(asset) or 0.0
                    op = self._chainlink.get_window_open_price(epoch_val, window, asset) or 0.0
                    self._logger.info(
                        f"[ORACLE_MOMENTUM_GATE] {slug} {outcome} BLOCKED | "
                        f"oracle disagrees (says {'UP' if oracle_says_up else 'DOWN'}) | "
                        f"oracle={oracle_price:,.2f} open={op:,.2f}"
                    )
                    return

        self.signals_generated += 1

        # Get liquidation and funding data from regime detector
        liq_conviction = 0.0
        liq_volume_60s = 0.0
        liq_bias = ""
        funding_rate = 0.0
        if self._regime_detector:
            liq_conviction = self._regime_detector.get_liquidation_conviction(
                asset, "UP" if outcome == "Up" else "DOWN"
            )
            regime_state = self._regime_detector.get_regime(asset)
            if regime_state:
                liq_volume_60s = regime_state.liq_volume_60s
                liq_bias = regime_state.liq_bias
                funding_rate = regime_state.funding_rate

        # Compute pair_cost: our side + opposite side ask
        pair_cost_val = None
        opp_token_id = market_info["down_token_id"] if outcome == "Up" else market_info["up_token_id"]
        opp_mid = 0.0
        if self._market_ws:
            opp_mid = self._market_ws.get_midpoint(opp_token_id)
        if opp_mid <= 0:
            try:
                opp_mid = await self._clob.get_midpoint(opp_token_id)
            except Exception:
                pass
        if opp_mid > 0 and midpoint > 0:
            pair_cost_val = midpoint + opp_mid

        # Compute taker CVD delta over momentum window + VPIN + Coinbase Premium
        binance_symbol = ASSET_TO_BINANCE.get(asset)
        taker_delta = None
        vpin_5m = None
        coinbase_premium_bps = None
        if binance_symbol:
            taker_delta = self.get_taker_delta(
                binance_symbol, self._config.momentum_window_secs
            )
            vpin_5m = self.get_vpin(binance_symbol, window_secs=300, n_buckets=10)
        if self._config.coinbase_premium_enabled:
            coinbase_premium_bps = self.get_coinbase_premium(asset)

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
            "source": source_override or "binance_momentum",
            "momentum_pct": momentum_pct,
            "time_remaining_secs": int(secs_left),
            "market_window_secs": window,
            "liq_conviction": liq_conviction,
            "shadow": shadow,
            "condition_id": market_info.get("condition_id", ""),
            "spread": spread_val,
            "best_bid": best_bid_val,
            "best_ask": best_ask_val,
            "book_imbalance": book_imbalance_val,
            "pair_cost": pair_cost_val,
            "taker_delta": taker_delta,
            "binance_price": self.get_latest_price(asset) or 0.0,
            "liq_volume_60s": liq_volume_60s,
            "liq_bias": liq_bias,
            "funding_rate": funding_rate,
            "vpin_5m": vpin_5m,
            "coinbase_premium_bps": coinbase_premium_bps,
            "oracle_epoch_delta": self._get_oracle_epoch_delta(asset, epoch, window),
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
                self._fee_gate_set_at = time.time()
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

            # Pre-warm SDK caches (neg_risk, tick_size, fee_rate) so FAK orders
            # skip REST lookups at execution time (~300ms saved per order)
            asyncio.create_task(self._prewarm_sdk_cache(token_ids[0]))
            asyncio.create_task(self._prewarm_sdk_cache(token_ids[1]))

            return info

        except Exception as e:
            self._logger.warning(f"Gamma API error for {slug}: {e}")
            return None

    async def _prewarm_sdk_cache(self, token_id: str) -> None:
        """Pre-warm py_clob_client internal caches for a token.

        Calls get_neg_risk, get_tick_size, get_fee_rate_bps in background so
        they're cached when FAK order fires. Saves ~300ms per order.
        """
        try:
            await self._clob.prewarm_market(token_id)
        except Exception:
            pass  # Non-critical, SDK will fetch on demand if needed

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

        is_shadow = self._config.window_delta_shadow
        tag = " [SHADOW]" if is_shadow else ""
        self._logger.info(
            f"Window delta trigger{tag}: {asset} {direction} {pct_change:+.3%} "
            f"(open={open_price:.4f} now={current_price:.4f}, {secs_to_end:.0f}s left)"
        )

        await self._generate_delta_signal(asset, direction, delta_slug, pct_change, secs_to_end, shadow=is_shadow)

    async def _generate_delta_signal(self, asset: str, direction: str, slug: str,
                                      pct_change: float, secs_left: float,
                                      shadow: bool = False) -> None:
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
            "shadow": shadow,
        }

        shadow_tag = " [SHADOW]" if shadow else ""
        self._logger.info(
            f"Window delta signal{shadow_tag}: {slug} {outcome} @ {midpoint:.4f} "
            f"({secs_left:.0f}s left, delta={pct_change:+.3%})"
        )

        await self._on_signal(signal)

    # ========================================================================
    # Resolution Snipe: Buy near-certain winner in last seconds before close
    # ========================================================================

    async def _snipe_loop(self) -> None:
        """Independent 1-second timer for resolution snipe.

        Runs on its own clock so we never miss a window due to quiet Binance ticks.
        Pre-caches market info at epoch boundaries to eliminate Gamma API latency at snipe time.
        """
        # Build snipe asset list
        snipe_assets_str = self._config.snipe_assets.strip()
        if snipe_assets_str:
            allowed = [a.strip().upper() for a in snipe_assets_str.split(',') if a.strip()]
        else:
            allowed = self._config.get_asset_filter() or []
        shadow = self._config.get_shadow_assets() or []
        # All assets we check (allowed + shadow that aren't already in allowed)
        all_assets = list(allowed) + [a for a in shadow if a not in allowed]

        precached: Set[str] = set()
        daily_snipe_count = 0
        daily_snipe_day = time.strftime("%Y-%m-%d")
        epoch_live_counts: dict = {}  # per-epoch live fire count (prevents correlated losses)

        self._logger.info(
            f"Snipe loop started | assets={all_assets} | "
            f"5m window=[{self._config.snipe_min_secs_remaining}-"
            f"{self._config.snipe_max_secs_remaining}s] | "
            f"5m price=[{self._config.snipe_min_entry_price}-"
            f"{self._config.snipe_max_entry_price}] | "
            f"max_daily={self._config.snipe_max_daily_trades}"
        )
        if self._config.snipe_15m_enabled:
            self._logger.info(
                f"15m snipe ENABLED (dry_run={self._config.snipe_15m_dry_run}) | "
                f"window=[{self._config.snipe_15m_min_secs_remaining}-"
                f"{self._config.snipe_15m_max_secs_remaining}s] | "
                f"price=[{self._config.snipe_15m_min_entry_price}-"
                f"{self._config.snipe_15m_max_entry_price}]"
            )

        while True:
            try:
                # Event-driven: wake instantly on WS price update, fallback 0.05s
                if self._market_ws:
                    await self._market_ws.wait_for_update(timeout=0.05)
                else:
                    await asyncio.sleep(0.05)
                now = time.time()

                # Reset daily counter at midnight
                today = time.strftime("%Y-%m-%d")
                if today != daily_snipe_day:
                    self._logger.info(f"Snipe daily reset: {daily_snipe_count} trades yesterday")
                    daily_snipe_count = 0
                    daily_snipe_day = today

                # Daily cap check
                cap = self._config.snipe_max_daily_trades
                if cap > 0 and daily_snipe_count >= cap:
                    continue

                for asset in all_assets:
                    is_shadow = asset in shadow

                    windows = self._config.get_market_windows(asset)
                    # Ensure 15m window is checked when 15m snipe is on
                    if self._config.snipe_15m_enabled and 900 not in windows:
                        windows = windows + [900]
                    for window in windows:
                        is_15m = window > 300
                        if is_15m and not self._config.snipe_15m_enabled:
                            continue
                        current_epoch = (int(now) // window) * window
                        window_end = current_epoch + window
                        secs_to_end = window_end - now
                        slug = f"{asset.lower()}-updown-{window // 60}m-{current_epoch}"

                        # Pre-cache: discover market early (>60s remaining)
                        cache_key = f"pc-{slug}"
                        if secs_to_end > 60 and cache_key not in precached:
                            precached.add(cache_key)
                            asyncio.create_task(self._discover_market(slug))
                            # S4: Record previous epoch outcome for streak tracking
                            if not is_15m:
                                prev_epoch = current_epoch - window
                                self._record_epoch_outcome(asset, prev_epoch, window)
                                # Epoch coverage: log new epoch + update previous
                                self._log_epoch_coverage(asset, current_epoch, prev_epoch, window)

                        # S4: Contrarian streak signal — fire early in epoch, opposite of streak
                        if (not is_15m
                                and self._config.streak_tracking_enabled
                                and 240 < secs_to_end < 290):
                            streak_key = f"streak-{asset.lower()}-{current_epoch}"
                            if streak_key not in self._snipe_fired:
                                streak_len, streak_dir = self._get_current_streak(asset)
                                if streak_len >= self._config.streak_min_length:
                                    contrarian_dir = "Down" if streak_dir == "up" else "Up"
                                    asyncio.create_task(self._generate_contrarian_signal(
                                        asset, slug, contrarian_dir, streak_len,
                                        streak_dir, secs_to_end, is_shadow
                                    ))
                                    self._snipe_fired.add(streak_key)

                        # Only fire in the snipe window (15m has separate timing)
                        if is_15m:
                            max_secs = self._config.snipe_15m_max_secs_remaining
                            min_secs = self._config.snipe_15m_min_secs_remaining
                        else:
                            max_secs = self._config.snipe_max_secs_remaining
                            min_secs = self._config.snipe_min_secs_remaining
                        if secs_to_end > max_secs:
                            continue
                        if secs_to_end < min_secs:
                            continue

                        # Blackout zone: skip 11-30s danger zone (reversals cluster here)
                        if not is_15m:
                            bl_min = self._config.snipe_blackout_min_secs
                            bl_max = self._config.snipe_blackout_max_secs
                            if bl_min > 0 and bl_max > 0 and bl_min <= round(secs_to_end) <= bl_max:
                                continue

                        # Dedup
                        snipe_key = f"snipe-{asset.lower()}-{window // 60}m-{current_epoch}"
                        if snipe_key in self._snipe_fired:
                            continue

                        # Skip if momentum already fired
                        if slug in self._signaled_slugs:
                            continue

                        # Per-epoch cap: prevent correlated multi-asset losses
                        effective_shadow = is_shadow
                        would_be_live = not is_shadow and not (
                            self._config.snipe_dry_run if not is_15m
                            else self._config.snipe_15m_dry_run)
                        epoch_cap = self._config.snipe_max_per_epoch
                        if (would_be_live and epoch_cap > 0
                                and epoch_live_counts.get(current_epoch, 0) >= epoch_cap):
                            effective_shadow = True

                        # Try to snipe — only mark fired if signal actually emitted
                        fired = await self._generate_snipe_signal(
                            asset, slug, secs_to_end, window, effective_shadow
                        )
                        if fired:
                            self._snipe_fired.add(snipe_key)
                            if would_be_live and not effective_shadow:
                                epoch_live_counts[current_epoch] = epoch_live_counts.get(current_epoch, 0) + 1
                            if not effective_shadow and not (asset in shadow):
                                daily_snipe_count += 1
                            elif effective_shadow and would_be_live:
                                self._logger.info(
                                    f"Snipe CAPPED ({epoch_cap}/epoch): {slug} "
                                    f"downgraded to shadow"
                                )

                # Prune old precache keys
                if len(precached) > 200:
                    precached.clear()

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"Snipe loop error: {e}")
                await asyncio.sleep(5)

    async def _generate_snipe_signal(self, asset: str, slug: str,
                                      secs_left: float,
                                      window: int, shadow: bool) -> bool:
        """Check both sides' midpoints and buy whichever is in snipe range.
        Returns True if a signal was emitted (or shadow-logged)."""
        market_info = await self._discover_market(slug)
        if not market_info:
            return False

        is_15m = window > 300
        if is_15m:
            min_price = self._config.snipe_15m_min_entry_price
            max_price = self._config.snipe_15m_max_entry_price
            force_dry = self._config.snipe_15m_dry_run
        else:
            min_price = self._config.snipe_min_entry_price
            max_price = self._config.snipe_max_entry_price
            force_dry = False

        source_tag = "resolution_snipe_15m" if is_15m else "resolution_snipe"
        label = "15m " if is_15m else ""
        oracle_blocked_count = 0  # track oracle blocks to dedup log spam

        # Check BOTH sides - buy whichever the market has priced as the winner
        blocked = set()
        if is_15m and self._config.snipe_15m_block_directions:
            for pair in self._config.snipe_15m_block_directions.split(","):
                pair = pair.strip()
                if ":" in pair:
                    blocked.add(pair.upper())

        for outcome, token_key in [("Up", "up_token_id"), ("Down", "down_token_id")]:
            if f"{asset.upper()}:{outcome.upper()}" in blocked:
                continue
            token_id = market_info[token_key]

            midpoint = 0.0
            ws_best_ask = 0.0
            if self._market_ws:
                midpoint = self._market_ws.get_midpoint(token_id)
                ws_best_ask = self._market_ws.get_best_ask(token_id)
            if midpoint <= 0:
                midpoint = await self._clob.get_midpoint(token_id)
            if midpoint <= 0:
                continue

            if midpoint < min_price:
                continue
            if midpoint > max_price:
                continue

            # Oracle snipe confirmation gate: block if Chainlink disagrees
            if (self._chainlink
                    and self._chainlink.is_healthy(asset)
                    and self._config.oracle_snipe_confirm):
                epoch_val = int(slug.rsplit("-", 1)[-1]) if "-" in slug else 0
                if epoch_val > 0:
                    oracle_verdict = self._chainlink.is_above_window_open(
                        epoch_val, window, asset
                    )
                    if oracle_verdict is not None:
                        oracle_says_up = oracle_verdict
                        signal_says_up = (outcome == "Up")
                        if oracle_says_up != signal_says_up:
                            oracle_price = self._chainlink.get_current_price(asset) or 0.0
                            op = self._chainlink.get_window_open_price(epoch_val, window, asset) or 0.0
                            if self._config.oracle_snipe_confirm_dry_run:
                                self._logger.info(
                                    f"[ORACLE_SNIPE_GATE DRY] {slug} {outcome} "
                                    f"@ {midpoint:.4f} | oracle disagrees "
                                    f"(says {'UP' if oracle_says_up else 'DOWN'}) | "
                                    f"oracle_price={oracle_price:,.2f} "
                                    f"window_open={op:,.2f} | not blocking (dry)"
                                )
                            else:
                                # Oracle flip: buy the cheap opposite token oracle says wins
                                # Skip if oracle delta is noise (< min threshold)
                                oracle_delta_pct = abs(oracle_price - op) / op if op > 0 else 0.0
                                if self._config.oracle_flip_enabled and oracle_delta_pct >= self._config.oracle_flip_min_delta_pct:
                                    flip_outcome = "Up" if oracle_says_up else "Down"
                                    flip_key = "up_token_id" if oracle_says_up else "down_token_id"
                                    flip_token_id = market_info[flip_key]
                                    flip_mid = 0.0
                                    if self._market_ws:
                                        flip_mid = self._market_ws.get_midpoint(flip_token_id)
                                    if flip_mid <= 0:
                                        try:
                                            flip_mid = await self._clob.get_midpoint(flip_token_id)
                                        except Exception:
                                            pass
                                    if (flip_mid > 0
                                            and flip_mid <= self._config.oracle_flip_max_opposite_price
                                            and flip_mid >= self._config.oracle_flip_min_opposite_price
                                            and secs_left <= self._config.oracle_flip_max_secs_remaining):
                                        potential_shares = self._config.oracle_flip_max_bet / flip_mid
                                        if_win_profit = potential_shares * (1.0 - flip_mid)
                                        if self._config.oracle_flip_dry_run:
                                            self._logger.info(
                                                f"[ORACLE_FLIP DRY] {slug} FLIP to {flip_outcome} "
                                                f"@ {flip_mid:.4f} | original={outcome} @ {midpoint:.4f} | "
                                                f"shares={potential_shares:.0f} | "
                                                f"if_win=${if_win_profit:.2f} | "
                                                f"oracle={oracle_price:,.2f} open={op:,.2f} | "
                                                f"OUTCOME PENDING"
                                            )
                                            # Log to signal_logger for real outcome tracking
                                            if self._signal_logger:
                                                dry_flip_signal = {
                                                    "slug": slug, "asset": asset,
                                                    "outcome": flip_outcome,
                                                    "midpoint": flip_mid,
                                                    "source": "oracle_flip",
                                                    "time_remaining_secs": int(secs_left),
                                                    "metadata": {"source": "oracle_flip", "dry_run": True},
                                                }
                                                self._signal_logger.log_signal(dry_flip_signal, guard_passed=True)
                                        else:
                                            self._logger.info(
                                                f"[ORACLE_FLIP] {slug} FLIPPING to {flip_outcome} "
                                                f"@ {flip_mid:.4f} | original={outcome} blocked | "
                                                f"oracle={oracle_price:,.2f} open={op:,.2f}"
                                            )
                                            flip_best_ask = self._market_ws.get_best_ask(flip_token_id) if self._market_ws else 0.0
                                            flip_signal = {
                                                "slug": slug,
                                                "token_id": flip_token_id,
                                                "outcome": flip_outcome,
                                                "midpoint": flip_mid,
                                                "price": flip_mid,
                                                "best_ask": flip_best_ask,
                                                "source": "oracle_flip",
                                                "asset": asset,
                                                "market_window_secs": window,
                                                "time_remaining_secs": int(secs_left),
                                                "condition_id": market_info.get("condition_id", ""),
                                                "metadata": {
                                                    "source": "oracle_flip",
                                                    "original_direction": outcome,
                                                    "original_price": midpoint,
                                                    "oracle_price": oracle_price,
                                                    "window_open_price": op,
                                                    "flip_multiplier": round((1.0 - flip_mid) / flip_mid, 1),
                                                },
                                            }
                                            self._signaled_slugs.add(slug)
                                            await self._on_signal(flip_signal)
                                            self._state_store.save("signaled_slugs", self._signaled_slugs)
                                            return True
                                    else:
                                        skip_reason = []
                                        if flip_mid <= 0:
                                            skip_reason.append("no_price")
                                        elif flip_mid > self._config.oracle_flip_max_opposite_price:
                                            skip_reason.append(f"too_expensive({flip_mid:.4f})")
                                        elif flip_mid < self._config.oracle_flip_min_opposite_price:
                                            skip_reason.append(f"too_cheap({flip_mid:.4f})")
                                        if secs_left > self._config.oracle_flip_max_secs_remaining:
                                            skip_reason.append(f"too_early({secs_left:.0f}s)")
                                        self._logger.info(
                                            f"[ORACLE_FLIP SKIP] {slug} {flip_outcome} | "
                                            f"reason={','.join(skip_reason)} | "
                                            f"oracle={oracle_price:,.2f}"
                                        )

                                elif self._config.oracle_flip_enabled and oracle_delta_pct < self._config.oracle_flip_min_delta_pct:
                                    _skip_key = f"flip_delta_{slug}"
                                    if _skip_key not in self._signaled_slugs:
                                        self._signaled_slugs.add(_skip_key)
                                        self._logger.info(
                                            f"[ORACLE_FLIP SKIP] {slug} | "
                                            f"reason=delta_too_small({oracle_delta_pct:.4%}) "
                                            f"min={self._config.oracle_flip_min_delta_pct:.3%} | "
                                            f"oracle={oracle_price:,.2f} open={op:,.2f}"
                                        )

                                self._logger.info(
                                    f"[ORACLE_SNIPE_GATE] {slug} {outcome} "
                                    f"BLOCKED | oracle disagrees | "
                                    f"oracle_price={oracle_price:,.2f} "
                                    f"window_open={op:,.2f}"
                                )
                                oracle_blocked_count += 1
                                continue

            # Compute pair_cost: our side + opposite side midpoint
            pair_cost_val = None
            opp_key = "down_token_id" if outcome == "Up" else "up_token_id"
            opp_token_id = market_info[opp_key]
            opp_mid = 0.0
            if self._market_ws:
                opp_mid = self._market_ws.get_midpoint(opp_token_id)
            if opp_mid <= 0:
                try:
                    opp_mid = await self._clob.get_midpoint(opp_token_id)
                except Exception:
                    pass
            if opp_mid > 0 and midpoint > 0:
                pair_cost_val = midpoint + opp_mid

            # Found a side in snipe range
            # 15m dry_run forces DB logging path even for shadow assets
            if shadow and not force_dry:
                self._logger.info(
                    f"[SHADOW] {label}Snipe: {slug} {outcome} @ {midpoint:.4f} "
                    f"({secs_left:.0f}s left)"
                )
                return True

            if self._config.snipe_dry_run or force_dry:
                self._logger.info(
                    f"{label}Snipe DRY: {slug} {outcome} @ {midpoint:.4f} "
                    f"({secs_left:.0f}s left)"
                )
                if self._signal_logger:
                    epoch_val = int(slug.rsplit("-", 1)[-1])
                    signal_id = self._signal_logger.log_signal({
                        "slug": slug,
                        "asset": asset,
                        "direction": outcome,
                        "token_id": token_id,
                        "midpoint": midpoint,
                        "source": source_tag,
                        "outcome": "snipe_dry_run",
                        "time_remaining_secs": int(secs_left),
                        "market_window_secs": window,
                        "entry_price": midpoint,
                        "dry_run": 1,
                        "pair_cost": pair_cost_val,
                    })
                    if signal_id > 0:
                        epoch_end = epoch_val + window
                        self._snipe_pending.append(
                            (epoch_end, signal_id, token_id, midpoint)
                        )
                return True

            self._signaled_slugs.add(slug)
            self._state_store.save("signaled_slugs", self._signaled_slugs)
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
                "tx_hash": f"snipe-{slug}-{int(time.time())}",
                "timestamp": time.time(),
                "source": source_tag,
                "time_remaining_secs": int(secs_left),
                "market_window_secs": window,
                "condition_id": market_info.get("condition_id", ""),
                "pair_cost": pair_cost_val,
                "best_ask": ws_best_ask if ws_best_ask > 0 else None,
                "binance_price": self.get_latest_price(asset) or 0.0,
                "vpin_5m": self.get_vpin(ASSET_TO_BINANCE.get(asset, ""), window_secs=300, n_buckets=10),
            }

            self._logger.info(
                f"{label}Snipe signal: {slug} {outcome} @ {midpoint:.4f} "
                f"({secs_left:.0f}s left)"
            )

            await self._on_signal(signal)
            return True

        # If oracle blocked both directions, mark as fired to prevent log spam
        if oracle_blocked_count >= 2:
            return True  # treat as "handled" so snipe_fired dedup kicks in

        return False

    async def _generate_contrarian_signal(
        self, asset: str, slug: str, contrarian_dir: str,
        streak_len: int, streak_dir: str, secs_left: float,
        shadow: bool,
    ) -> None:
        """Generate a contrarian streak signal (S4).

        Buys the opposite side of a 3+ epoch streak early in the new epoch,
        when mean reversion is most likely (academic: gambler's fallacy bias).
        """
        market_info = await self._discover_market(slug)
        if not market_info:
            return

        token_key = "up_token_id" if contrarian_dir == "Up" else "down_token_id"
        token_id = market_info[token_key]

        midpoint = 0.0
        ws_best_ask = 0.0
        if self._market_ws:
            midpoint = self._market_ws.get_midpoint(token_id)
            ws_best_ask = self._market_ws.get_best_ask(token_id)
        if midpoint <= 0:
            midpoint = await self._clob.get_midpoint(token_id)
        if midpoint <= 0:
            return

        min_p = self._config.streak_contrarian_min_price
        max_p = self._config.streak_contrarian_max_price
        if midpoint < min_p or midpoint > max_p:
            self._logger.debug(
                f"Streak contrarian skip: {asset} {contrarian_dir} @ {midpoint:.4f} "
                f"outside [{min_p}-{max_p}]"
            )
            return

        is_dry = shadow or self._config.streak_contrarian_dry_run
        label = "[DRY] " if is_dry else ""
        self._logger.info(
            f"{label}STREAK CONTRARIAN: {slug} {contrarian_dir} @ {midpoint:.4f} | "
            f"streak={streak_len}x {streak_dir} | {secs_left:.0f}s left"
        )

        signal = {
            "token_id": token_id,
            "price": midpoint,
            "best_ask": ws_best_ask,
            "slug": slug,
            "market_title": market_info.get("title", slug),
            "condition_id": market_info.get("condition_id", ""),
            "outcome": contrarian_dir,
            "direction": "BUY",
            "asset": asset,
            "source": "streak_contrarian",
            "time_remaining_secs": int(secs_left),
            "market_window_secs": 300,
            "momentum_pct": 0.0,
            "metadata": {
                "source": "streak_contrarian",
                "streak_length": streak_len,
                "streak_direction": streak_dir,
                "contrarian_direction": contrarian_dir,
                "dry_run": is_dry,
            },
        }

        if self._signal_logger:
            self._signal_logger.log_signal(signal, guard_passed=True)

        if not is_dry:
            await self._on_signal(signal)

    async def _snipe_resolution_loop(self) -> None:
        """Check resolved outcomes for dry-run snipe signals.

        After each epoch ends (+90s buffer for settlement), check the midpoint
        of the token we would have bought. If it resolved to ~1.00, we won.
        """
        self._logger.info("Snipe resolution checker started")
        while True:
            try:
                await asyncio.sleep(15)
                now = time.time()
                still_pending = []

                for epoch_end, signal_id, token_id, entry_price in self._snipe_pending:
                    # Wait 90s after epoch end for settlement
                    if now < epoch_end + 90:
                        still_pending.append((epoch_end, signal_id, token_id, entry_price))
                        continue

                    # Check resolved price
                    resolved_price = 0.0
                    if self._market_ws:
                        resolved_price = self._market_ws.get_midpoint(token_id)
                    if resolved_price <= 0:
                        try:
                            resolved_price = await self._clob.get_midpoint(token_id)
                        except Exception:
                            pass

                    if resolved_price <= 0:
                        # Can't determine outcome, skip
                        self._logger.debug(
                            f"Snipe resolution: no price for signal_id={signal_id}"
                        )
                        continue

                    # Win if resolved >= 0.95 (market settled to our side)
                    is_win = 1 if resolved_price >= 0.95 else 0
                    sim_pnl = (1.0 - entry_price) if is_win else -entry_price

                    self._signal_logger.update_signal(signal_id, {
                        "exit_price": resolved_price,
                        "exit_reason": "market_resolved",
                        "pnl": round(sim_pnl, 4),
                        "pnl_pct": round(sim_pnl / entry_price * 100, 2) if entry_price > 0 else 0,
                        "is_win": is_win,
                    })
                    result = "WIN" if is_win else "LOSS"
                    self._logger.info(
                        f"Snipe DRY resolved: signal_id={signal_id} "
                        f"entry={entry_price:.4f} exit={resolved_price:.4f} "
                        f"pnl={sim_pnl:+.4f} {result}"
                    )

                self._snipe_pending = still_pending

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"Snipe resolution loop error: {e}")
                await asyncio.sleep(30)

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

        # Prune snipe and near-res dedup (keys are per-epoch, safe to clear on prune cycle)
        self._snipe_fired.clear()
        self._near_res_fired.clear()

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
    # Oracle epoch delta helper
    # -----------------------------------------------------------------------

    def _get_oracle_epoch_delta(self, asset: str, epoch: int, window: int) -> Optional[float]:
        """Get oracle price delta from epoch open for signal logging."""
        if not self._chainlink:
            return None
        return self._chainlink.get_epoch_delta_pct(asset, epoch, window)

    def _log_epoch_coverage(self, asset: str, current_epoch: int, prev_epoch: int, window: int) -> None:
        """Log epoch start and update previous epoch outcome for coverage analysis."""
        if not self._signal_logger:
            return
        try:
            # Log new epoch with open prices
            oracle_open = None
            if self._chainlink:
                oracle_open = self._chainlink.get_window_open_price(current_epoch, window, asset)
            binance_symbol = ASSET_TO_BINANCE.get(asset)
            binance_open = None
            if binance_symbol and binance_symbol in self._window_open_prices:
                key = (asset, current_epoch)
                binance_open = self._window_open_prices.get(key)

            self._signal_logger.log_epoch(
                epoch=current_epoch, asset=asset, window_secs=window,
                oracle_open=oracle_open, binance_open=binance_open,
            )

            # Update previous epoch with close data and outcome
            oracle_close = None
            oracle_delta = None
            oracle_dir = None
            if self._chainlink and self._chainlink.is_healthy(asset):
                oracle_close = self._chainlink.get_current_price(asset)
                oracle_delta = self._chainlink.get_epoch_delta_pct(asset, prev_epoch, window)
                verdict = self._chainlink.is_above_window_open(prev_epoch, window, asset)
                if verdict is not None:
                    oracle_dir = "up" if verdict else "down"

            binance_close = self.get_latest_price(asset)
            binance_delta = None
            prev_key = (asset, prev_epoch)
            if binance_close and prev_key in self._window_open_prices:
                b_open = self._window_open_prices[prev_key]
                if b_open and b_open > 0:
                    binance_delta = (binance_close - b_open) / b_open

            self._signal_logger.update_epoch_outcome(
                epoch=prev_epoch, asset=asset, window_secs=window,
                oracle_close=oracle_close, oracle_delta_pct=oracle_delta,
                oracle_direction=oracle_dir, binance_close=binance_close,
                binance_delta_pct=binance_delta,
                resolved_outcome=oracle_dir,
            )
        except Exception as e:
            self._logger.debug(f"Epoch coverage log failed (non-fatal): {e}")

    # -----------------------------------------------------------------------
    # Build 6: Lag signal execution
    # -----------------------------------------------------------------------

    async def _fire_lag_signal(self, asset: str, delay_secs: int, outcome: str) -> None:
        """Fire a companion lag signal after delay_secs.

        Skips if: market has too little time left, companion midpoint already
        moved more than lag_neutral_band from 0.50 (market already repriced).
        Signal source = 'binance_momentum_lag' so guard/logger treat it like momentum.
        """
        await asyncio.sleep(delay_secs)

        now = time.time()
        epoch = int(now // 300) * 300
        slug = f"{asset}-updown-5m-{epoch}"

        secs_left = (epoch + 300) - now
        min_secs = self._config.get_min_secs_remaining(300)
        if secs_left < min_secs:
            self._logger.debug(
                f"Lag signal skip: {slug} {outcome} — only {secs_left:.0f}s left"
            )
            return

        market_info = await self._discover_market(slug)
        if not market_info:
            return

        token_id = (
            market_info["up_token_id"] if outcome == "Up"
            else market_info["down_token_id"]
        )

        midpoint = 0.0
        if self._market_ws:
            midpoint = self._market_ws.get_midpoint(token_id)
        if midpoint <= 0:
            midpoint = await self._clob.get_midpoint(token_id)
        if midpoint <= 0:
            return

        # Skip if companion market has already repriced more than neutral_band from 0.50
        band = self._config.lag_neutral_band
        if outcome == "Up" and midpoint > 0.50 + band:
            self._logger.info(
                f"Lag signal skip: {slug} {outcome} already repriced to {midpoint:.4f}"
            )
            return
        if outcome == "Down" and midpoint < 0.50 - band:
            self._logger.info(
                f"Lag signal skip: {slug} {outcome} already repriced to {midpoint:.4f}"
            )
            return

        spread_val = None
        book_imbalance_val = None
        if self._market_ws:
            raw_spread = self._market_ws.get_spread(token_id)
            if raw_spread > 0:
                spread_val = raw_spread
            depth = self._market_ws.get_book_depth(token_id)
            book_imbalance_val = depth["imbalance"] if depth else None

        signal = {
            "token_id": token_id,
            "price": midpoint,
            "slug": slug,
            "market_title": market_info.get("market_title", slug),
            "usdc_size": 999.0,
            "direction": "BUY",
            "outcome": outcome,
            "asset": asset.upper(),
            "tx_hash": f"lag-{slug}-{int(now)}",
            "timestamp": now,
            "source": "binance_momentum_lag",
            "momentum_pct": 0.0,
            "time_remaining_secs": int(secs_left),
            "market_window_secs": 300,
            "spread": spread_val,
            "book_imbalance": book_imbalance_val,
        }

        self._logger.info(
            f"Lag signal: {asset.upper()} {outcome} @ {midpoint:.4f} "
            f"({secs_left:.0f}s left, delay={delay_secs}s)"
        )
        self.signals_generated += 1
        await self._on_signal(signal)

    # -----------------------------------------------------------------------
    # Build 5: New market subscription watcher
    # Polls Gamma API every 30s for active 5m slugs we haven't cached yet.
    # Catches markets that epoch-math prefetch might miss on first run.
    # -----------------------------------------------------------------------

    async def _watch_new_markets(self) -> None:
        """Poll Gamma API every 30s for currently-active 5m markets.

        Complements _market_prefetch_loop (epoch-computed slugs) by fetching
        actual live slugs from the API — catches markets we couldn't predict.
        On new slug: calls _discover_market() which caches + subscribes MarketWS.
        """
        await asyncio.sleep(10)  # brief startup delay — let prefetch run first
        while True:
            try:
                url = f"{GAMMA_API_URL}/markets?active=true&closed=false&limit=100"
                session = self._gamma_session or aiohttp.ClientSession()
                close_session = self._gamma_session is None
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status != 200:
                            await asyncio.sleep(30)
                            continue
                        data = await resp.json()
                finally:
                    if close_session:
                        await session.close()

                new_count = 0
                for market in data if isinstance(data, list) else []:
                    slug = market.get("slug", "")
                    if not slug:
                        continue
                    if "-5m-" not in slug and not (
                        "-15m-" in slug and self._config.enable_15m_momentum
                    ):
                        continue
                    if slug in self._market_cache:
                        continue
                    info = await self._discover_market(slug)
                    if info:
                        new_count += 1
                        self._logger.info(f"New market watcher: subscribed {slug}")

                if new_count:
                    self._logger.info(f"Market watcher: +{new_count} new markets discovered")

            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._logger.debug(f"Market watcher error (non-fatal): {e}")

            await asyncio.sleep(30)

    # -----------------------------------------------------------------------
    # Pair arb scan (Phase 2) — wired via signal_bot._safe_task(), not gather()
    # -----------------------------------------------------------------------

    def get_current_momentum_pct(self, asset: str) -> Optional[float]:
        """Return current momentum pct for asset within the configured window.

        Used by ExitManager reversal check. Returns None if data unavailable.
        """
        symbol = ASSET_TO_BINANCE.get(asset.upper())
        if not symbol:
            return None
        buffer = self._price_buffers.get(symbol)
        if not buffer or len(buffer) < 2:
            return None
        now = time.time()
        cutoff = now - self._config.momentum_window_secs
        oldest_price = None
        current_price = buffer[-1][1]
        for ts, price in buffer:
            if ts >= cutoff:
                oldest_price = price
                break
        if oldest_price is None or oldest_price <= 0:
            return None
        return (current_price - oldest_price) / oldest_price

    def get_latest_price(self, asset: str) -> Optional[float]:
        """Return latest Binance price for asset. Used by ExitManager."""
        symbol = ASSET_TO_BINANCE.get(asset.upper())
        if not symbol:
            return None
        buffer = self._price_buffers.get(symbol)
        if not buffer:
            return None
        return buffer[-1][1]

    def get_epoch_price_context(self, asset: str, window: int) -> Optional[dict]:
        """Return current Binance price, epoch open, and pct change for stale quote detection.

        Used by MarketMaker to detect when one side of a market hasn't repriced
        after a Binance move. Returns None if data unavailable.
        """
        current_price = self.get_latest_price(asset)
        if current_price is None:
            return None

        now = time.time()
        current_epoch = (int(now) // window) * window
        key = (asset.upper(), current_epoch)
        open_price = self._window_open_prices.get(key)
        if not open_price or open_price <= 0:
            return None

        pct_change = (current_price - open_price) / open_price
        return {
            "current_price": current_price,
            "open_price": open_price,
            "pct_change": pct_change,
            "direction": "Up" if pct_change > 0 else "Down",
            "epoch": current_epoch,
        }

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

            # Count active pair arb trades from signaled slugs (each trade = 2 legs)
            active_pairs = sum(1 for s in self._signaled_slugs if ':up' in s or ':down' in s) // 2
            if active_pairs >= self._config.pair_arb_max_concurrent:
                self._logger.debug(
                    f"pair_arb: {slug} skipped (active={active_pairs}, max={self._config.pair_arb_max_concurrent})"
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

    async def near_res_pair_arb_loop(self) -> None:
        """Scan every few seconds for near-resolution pair arb opportunities.
        Focuses on the last N seconds of each 5m epoch where MMs pull liquidity.
        Uses taker execution (no time for maker fills).
        """
        interval = self._config.pair_arb_near_res_scan_interval
        while True:
            try:
                await self._do_near_res_pair_arb_scan()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._logger.warning(f"near_res_pair_arb error (non-fatal): {e}")
            await asyncio.sleep(interval)

    async def _do_near_res_pair_arb_scan(self) -> None:
        """Check if we're in the near-resolution window and scan for pair arb."""
        now = time.time()
        epoch = int(now // 300) * 300
        epoch_end = epoch + 300
        secs_remaining = epoch_end - now

        max_secs = self._config.pair_arb_near_res_max_secs
        min_secs = self._config.pair_arb_near_res_min_secs

        if secs_remaining > max_secs or secs_remaining < min_secs:
            return

        allowed = self._config.get_asset_filter() or ["BTC"]
        for asset in allowed:
            slug = f"{asset.lower()}-updown-5m-{epoch}"
            dedup_key = f"nr-{asset}-{epoch}"

            if dedup_key in self._near_res_fired:
                continue

            market_info = self._market_cache.get(slug)
            if not market_info:
                continue

            up_token_id = market_info.get("up_token_id")
            down_token_id = market_info.get("down_token_id")
            if not up_token_id or not down_token_id:
                continue

            if not self._market_ws:
                continue

            await self._market_ws.subscribe([up_token_id, down_token_id])

            ask_up = self._market_ws.get_best_ask(up_token_id)
            ask_down = self._market_ws.get_best_ask(down_token_id)

            if ask_up <= 0.0 or ask_down <= 0.0:
                continue

            # Fee-aware pair cost: include taker fees for both sides
            fee_up = 0.25 * (ask_up * (1 - ask_up)) ** 2
            fee_down = 0.25 * (ask_down * (1 - ask_down)) ** 2
            raw_pair_cost = ask_up + ask_down
            effective_pair_cost = raw_pair_cost + fee_up + fee_down

            threshold = self._config.pair_arb_near_res_max_pair_cost
            triggered = effective_pair_cost < threshold
            margin_pct = (1.0 - effective_pair_cost) * 100

            if triggered or margin_pct > -5:  # log when close to threshold or triggered
                self._logger.info(
                    f"near_res_arb: {asset} raw={raw_pair_cost:.4f} "
                    f"fees={fee_up + fee_down:.4f} effective={effective_pair_cost:.4f} "
                    f"threshold={threshold} {secs_remaining:.0f}s left "
                    f"{'TRIGGER' if triggered else 'skip'} margin={margin_pct:.1f}%"
                )

            if not triggered:
                continue

            # Concurrent cap (shared with regular pair arb)
            active_pairs = sum(1 for s in self._signaled_slugs if ':up' in s or ':down' in s) // 2
            if active_pairs >= self._config.pair_arb_max_concurrent:
                self._logger.debug(f"near_res_arb: {slug} skipped (concurrent cap)")
                continue

            self._near_res_fired.add(dedup_key)

            if self._config.pair_arb_dry_run:
                self._logger.info(
                    f"near_res_arb DRY: {asset} pair_cost={raw_pair_cost:.4f} "
                    f"effective={effective_pair_cost:.4f} margin={margin_pct:.1f}% "
                    f"(up={ask_up:.4f} down={ask_down:.4f}) {secs_remaining:.0f}s left"
                )
                continue

            # Build signals with near_resolution flag and taker strategy
            up_signal = self._build_pair_arb_signal(
                asset, "Up", up_token_id, slug, ask_up, raw_pair_cost, market_info, epoch
            )
            down_signal = self._build_pair_arb_signal(
                asset, "Down", down_token_id, slug, ask_down, raw_pair_cost, market_info, epoch
            )
            # Override for near-resolution: taker execution, fee-aware metadata
            for sig in (up_signal, down_signal):
                sig["strategy"] = "taker"
                sig["near_resolution"] = True
                sig["effective_pair_cost"] = effective_pair_cost
                sig["taker_fee"] = fee_up + fee_down

            await self._on_signal(up_signal)
            await self._on_signal(down_signal)

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
