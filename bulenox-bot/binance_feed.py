import asyncio
import json
import logging
import ssl
import time
from collections import deque
from typing import Callable, Awaitable, Optional

import aiohttp
import certifi

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

    def _trim(self, now: float) -> None:
        cutoff = now - self._window_secs
        while self._prices and self._prices[0][0] < cutoff:
            self._prices.popleft()
