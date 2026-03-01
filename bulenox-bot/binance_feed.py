import asyncio
import json
import logging
import time
from collections import deque
from typing import Callable, Awaitable

import aiohttp

logger = logging.getLogger(__name__)

KRAKEN_WS = "wss://ws.kraken.com"
HEARTBEAT_TIMEOUT = 30  # seconds — XBT/USD trades constantly; silence means dead feed


class BinanceFeed:
    """BTC price momentum feed — uses Kraken XBT/USD ticker (US-accessible, always liquid)."""

    def __init__(
        self,
        symbol: str,
        window_secs: int,
        trigger_pct: float,
        on_signal: Callable[[str, float], Awaitable[None]],
        cooldown_secs: int = 120,
    ):
        self._symbol = symbol.lower()
        self._window_secs = window_secs
        self._trigger_pct = trigger_pct
        self._on_signal = on_signal
        self._cooldown_secs = cooldown_secs
        self._prices: deque = deque()  # (ts, price)
        self._last_signal_ts: float = 0.0
        self.last_price: float = 0.0

    async def start(self) -> None:
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(KRAKEN_WS) as ws:
                        logger.info(f"Kraken connected: {KRAKEN_WS}")
                        # Subscribe to ticker
                        await ws.send_json({
                            "event": "subscribe",
                            "pair": ["XBT/USD"],
                            "subscription": {"name": "ticker"},
                        })
                        while True:
                            try:
                                msg = await asyncio.wait_for(ws.receive(), timeout=HEARTBEAT_TIMEOUT)
                            except asyncio.TimeoutError:
                                logger.warning(f"Kraken feed silent for {HEARTBEAT_TIMEOUT}s, reconnecting...")
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await self._handle(msg.data)
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                logger.warning("Kraken WS closed/error, reconnecting...")
                                break
            except Exception as e:
                logger.error(f"Kraken feed error: {e}, reconnecting in 5s")
                await asyncio.sleep(5)

    async def _handle(self, raw: str) -> None:
        data = json.loads(raw)
        # Ticker updates are arrays: [channelID, {ticker_data}, "ticker", "XBT/USD"]
        if not isinstance(data, list) or len(data) < 4 or data[2] != "ticker":
            return
        price = float(data[1]["c"][0])  # "c" = [last_trade_price, lot_volume]
        self.last_price = price
        now = time.monotonic()

        self._prices.append((now, price))

        if len(self._prices) < 2:
            return

        oldest_ts, oldest_price = self._prices[0]

        # Not enough history yet — trim and wait
        if now - oldest_ts < self._window_secs:
            self._trim(now)
            return

        pct = (price - oldest_price) / oldest_price
        self._trim(now)  # trim after reading oldest

        if abs(pct) < self._trigger_pct:
            return

        if now - self._last_signal_ts < self._cooldown_secs:
            return

        direction = "UP" if pct > 0 else "DOWN"
        self._last_signal_ts = now
        logger.info(f"Signal: {direction} {pct:.4%} in {self._window_secs}s")
        await self._on_signal(direction, pct)

    def _trim(self, now: float) -> None:
        cutoff = now - self._window_secs
        while self._prices and self._prices[0][0] < cutoff:
            self._prices.popleft()
