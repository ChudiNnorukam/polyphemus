import asyncio
import json
import logging
import math
import ssl
import time
from collections import deque
from typing import Callable, Awaitable, Optional

import aiohttp
import certifi
import statistics

logger = logging.getLogger(__name__)

# Coinbase Exchange WS - US-accessible, high frequency, reliable
COINBASE_WS = "wss://ws-feed.exchange.coinbase.com"
HEARTBEAT_TIMEOUT = 30  # Coinbase ticker updates every few seconds


class BinanceFeed:
    """BTC price momentum feed via Coinbase Exchange ticker stream."""

    def __init__(
        self,
        symbol: str,
        window_secs: int,
        trigger_pct: float,
        on_signal: Callable[[str, float], Awaitable[None]],
        on_price: Optional[Callable[[float], Awaitable[None]]] = None,
        cooldown_secs: int = 120,
    ):
        self._symbol = symbol.lower()
        self._window_secs = window_secs
        self._trigger_pct = trigger_pct
        self._trigger_fallback = trigger_pct * 0.6  # 0.3% when primary is 0.5%
        self._on_signal = on_signal
        self._on_price = on_price
        self._cooldown_secs = cooldown_secs
        self._prices: deque = deque()  # (ts, price)
        self._last_signal_ts: float = 0.0
        self._last_any_signal_ts: float = time.monotonic()  # init to startup so 6h clock starts immediately
        self._fallback_active: bool = False
        self._FALLBACK_AFTER_SECS = 6 * 3600  # lower threshold after 6 hours of silence
        self.last_price: float = 0.0
        # ATR regime tracking: 1h rolling prices + 24 hourly range snapshots
        self._prices_1h: deque = deque()
        self._hourly_ranges: deque = deque(maxlen=24)
        self._last_hourly_ts: float = 0.0

    async def start(self) -> None:
        backoff = 5
        while True:
            try:
                ssl_ctx = ssl.create_default_context(cafile=certifi.where())
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(COINBASE_WS, ssl=ssl_ctx, heartbeat=20) as ws:
                        # Subscribe to BTC-USD ticker
                        await ws.send_json({
                            "type": "subscribe",
                            "product_ids": ["BTC-USD"],
                            "channels": ["ticker"],
                        })
                        logger.info(f"Coinbase connected: {COINBASE_WS} (BTC-USD ticker)")
                        backoff = 5
                        last_ticker_ts = time.monotonic()

                        while True:
                            try:
                                msg = await asyncio.wait_for(ws.receive(), timeout=HEARTBEAT_TIMEOUT)
                            except asyncio.TimeoutError:
                                logger.warning(f"Coinbase feed silent for {HEARTBEAT_TIMEOUT}s, reconnecting...")
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                if data.get("type") == "ticker":
                                    last_ticker_ts = time.monotonic()
                                    await self._handle_ticker(data)
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                logger.warning("Coinbase WS closed/error, reconnecting...")
                                break
                            # Watchdog: if no TICKER data for 60s despite connection alive, reconnect
                            if time.monotonic() - last_ticker_ts > 60:
                                logger.warning("Coinbase ticker watchdog: no ticker data for 60s despite active connection, reconnecting...")
                                break
            except Exception as e:
                logger.error(f"Coinbase feed error: {e}, reconnecting in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _handle_ticker(self, data: dict) -> None:
        price = float(data["price"])
        self.last_price = price
        if self._on_price:
            await self._on_price(price)
        now = time.monotonic()

        # ATR regime: maintain 1h rolling price window and take hourly range snapshots
        self._prices_1h.append((now, price))
        cutoff_1h = now - 3600
        while self._prices_1h and self._prices_1h[0][0] < cutoff_1h:
            self._prices_1h.popleft()
        if self._last_hourly_ts == 0.0:
            self._last_hourly_ts = now
        elif now - self._last_hourly_ts >= 3600:
            prices_1h = [p for _, p in self._prices_1h]
            if len(prices_1h) >= 2:
                self._hourly_ranges.append(max(prices_1h) - min(prices_1h))
                logger.debug(f"ATR snapshot: 1h_range=${self._hourly_ranges[-1]:.0f} n={len(self._hourly_ranges)}/24")
            self._last_hourly_ts = now

        self._prices.append((now, price))

        if len(self._prices) < 2:
            return

        oldest_ts, oldest_price = self._prices[0]

        if now - oldest_ts < self._window_secs:
            self._trim(now)
            return

        pct = (price - oldest_price) / oldest_price
        self._trim(now)

        # Adaptive threshold: drop to fallback after 6h of silence
        time_since_signal = now - self._last_any_signal_ts if self._last_any_signal_ts > 0 else 0
        active_thresh = self._trigger_pct
        if time_since_signal > self._FALLBACK_AFTER_SECS and self._last_any_signal_ts > 0:
            if not self._fallback_active:
                self._fallback_active = True
                logger.info(f"Adaptive threshold: lowering from {self._trigger_pct*100:.1f}% to {self._trigger_fallback*100:.1f}% after {time_since_signal/3600:.1f}h silence")
            active_thresh = self._trigger_fallback

        if abs(pct) < active_thresh:
            return

        if now - self._last_signal_ts < self._cooldown_secs:
            return

        raw_direction = "UP" if pct > 0 else "DOWN"
        # FADE mode: bet AGAINST the move (mean reversion)
        direction = "DOWN" if raw_direction == "UP" else "UP"
        self._last_signal_ts = now
        self._last_any_signal_ts = now
        # Reset fallback when a signal fires at primary threshold
        if abs(pct) >= self._trigger_pct and self._fallback_active:
            self._fallback_active = False
            logger.info(f"Adaptive threshold: restored to {self._trigger_pct*100:.1f}% (primary signal fired)")
        thresh_label = "fallback" if abs(pct) < self._trigger_pct else "primary"
        logger.info(f"Signal: raw={raw_direction} {pct:.4%} -> FADE entry={direction} [{thresh_label}] in {self._window_secs}s")
        await self._on_signal(direction, pct)

    def get_atr_ratio(self) -> float:
        """Return 1h ATR / median 24h hourly ATR. Returns 0.0 if < 4h of baseline data."""
        if len(self._hourly_ranges) < 4 or len(self._prices_1h) < 2:
            return 0.0
        prices = [p for _, p in self._prices_1h]
        atr_1h = max(prices) - min(prices)
        median_atr = statistics.median(self._hourly_ranges)
        if median_atr <= 0:
            return 0.0
        return atr_1h / median_atr

    def get_trend_direction(self) -> str:
        """Return 1h price trend: 'UP', 'DOWN', or 'NEUTRAL' (< ±0.5% change)."""
        if len(self._prices_1h) < 2:
            return "NEUTRAL"
        prices = [p for _, p in self._prices_1h]
        pct = (prices[-1] - prices[0]) / prices[0]
        if pct < -0.005:
            return "DOWN"
        if pct > 0.005:
            return "UP"
        return "NEUTRAL"

    def get_feature_snapshot(self, tick_size: float) -> dict:
        prices_1h = [p for _, p in self._prices_1h]
        atr_1h = (max(prices_1h) - min(prices_1h)) if len(prices_1h) >= 2 else None
        atr_24h_median = statistics.median(self._hourly_ranges) if self._hourly_ranges else None
        prices_15m = [p for ts, p in self._prices_1h if ts >= time.monotonic() - 900]
        atr_5m = None
        prices_5m = [p for ts, p in self._prices_1h if ts >= time.monotonic() - 300]
        if len(prices_5m) >= 2:
            atr_5m = max(prices_5m) - min(prices_5m)

        def realized_vol(prices: list[float]) -> Optional[float]:
            if len(prices) < 3:
                return None
            returns = []
            for prev, curr in zip(prices, prices[1:]):
                if prev > 0 and curr > 0:
                    returns.append(math.log(curr / prev))
            if len(returns) < 2:
                return None
            return statistics.pstdev(returns)

        return {
            "atr_5m_ticks": (atr_5m / tick_size) if atr_5m is not None and tick_size else None,
            "atr_1h_ticks": (atr_1h / tick_size) if atr_1h is not None and tick_size else None,
            "atr_24h_median_ticks": (atr_24h_median / tick_size) if atr_24h_median is not None and tick_size else None,
            "atr_ratio_1h_24h": self.get_atr_ratio(),
            "realized_vol_15m": realized_vol(prices_15m),
            "realized_vol_1h": realized_vol(prices_1h),
            "hurst_48h": None,
            "ou_half_life_min": None,
            "trend_direction": self.get_trend_direction(),
        }

    def _trim(self, now: float) -> None:
        cutoff = now - self._window_secs
        while self._prices and self._prices[0][0] < cutoff:
            self._prices.popleft()
