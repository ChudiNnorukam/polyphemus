"""
Binance Momentum Feed: WebSocket connection to Binance public kline streams.

Buffers closed 1-minute candles for BTC, ETH, SOL and computes short-term
momentum direction. Used as a confirmation layer before executing trades.

Handles:
- Combined WebSocket stream for multiple symbols
- Candle buffering (closed candles only)
- Momentum calculation from cached data (zero network in hot path)
- Exponential backoff reconnection
- Circuit breaker after repeated failures
- Startup grace period (fail-open)
"""

import asyncio
import json
import math
import time
from collections import deque
from typing import Dict, Optional

import aiohttp

from .types import (
    MomentumResult,
    BACKOFF_BASE,
    BACKOFF_MAX,
    BACKOFF_MULTIPLIER,
    BINANCE_WS_URL,
    BINANCE_SYMBOLS,
    BINANCE_KLINE_INTERVAL,
    BINANCE_BUFFER_SIZE,
    ASSET_TO_BINANCE,
)
from .config import Settings, setup_logger


# Confidence scaling: 0.5% move = full confidence
CONFIDENCE_FULL_MOVE = 0.005

# Circuit breaker: open after this many consecutive reconnect failures
CIRCUIT_BREAKER_THRESHOLD = 3


class BinanceFeed:
    """WebSocket-based Binance kline feed for momentum confirmation."""

    def __init__(self, config: Settings):
        self._config = config
        self._logger = setup_logger("polyphemus.binance")
        self._startup_time = time.time()

        # Per-symbol candle buffers: symbol -> deque of {"close": float, "time_ms": int}
        self._buffers: Dict[str, deque] = {
            symbol: deque(maxlen=BINANCE_BUFFER_SIZE)
            for symbol in BINANCE_SYMBOLS
        }

        # Connection state
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._consecutive_failures = 0
        self._circuit_open = False

    @property
    def circuit_open(self) -> bool:
        """True if circuit breaker is open (too many reconnect failures)."""
        return self._circuit_open

    def in_grace_period(self) -> bool:
        """True if still within startup grace period."""
        elapsed = time.time() - self._startup_time
        return elapsed < self._config.binance_startup_grace_secs

    async def start(self) -> None:
        """Start the Binance WebSocket connection loop."""
        self._logger.info("Starting Binance momentum feed")
        await self._connect_loop()

    async def _connect_loop(self) -> None:
        """Exponential backoff reconnection loop."""
        attempt = 0

        while True:
            try:
                self._session = aiohttp.ClientSession()

                # Build combined stream URL
                streams = "/".join(
                    f"{symbol}@kline_{BINANCE_KLINE_INTERVAL}"
                    for symbol in BINANCE_SYMBOLS
                )
                url = f"{BINANCE_WS_URL}?streams={streams}"

                self._ws = await self._session.ws_connect(url, timeout=10)
                self._logger.info("Binance WebSocket connected")

                # Reset on successful connect
                attempt = 0
                self._consecutive_failures = 0
                self._circuit_open = False

                await self._read_loop()

            except asyncio.CancelledError:
                self._logger.info("Binance feed cancelled")
                raise

            except Exception as e:
                self._consecutive_failures += 1
                if self._consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
                    if not self._circuit_open:
                        self._logger.error(
                            f"Binance circuit breaker OPEN after "
                            f"{self._consecutive_failures} failures"
                        )
                    self._circuit_open = True

                delay = min(
                    BACKOFF_BASE * (BACKOFF_MULTIPLIER ** attempt),
                    BACKOFF_MAX,
                )
                self._logger.warning(
                    f"Binance WS disconnected: {e}, retry in {delay}s "
                    f"(failures={self._consecutive_failures})"
                )
                attempt += 1
                await asyncio.sleep(delay)

            finally:
                await self._close_session()

    async def _read_loop(self) -> None:
        """Read and process messages from Binance combined stream."""
        msg_count = 0
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    msg_count += 1
                    try:
                        data = json.loads(msg.data)

                        if msg_count <= 2:
                            self._logger.debug(
                                f"Binance msg #{msg_count}: "
                                f"stream={data.get('stream', '?')}"
                            )

                        self._process_kline(data)

                    except json.JSONDecodeError:
                        continue
                    except Exception as e:
                        self._logger.debug(f"Binance msg error: {e}")

                elif msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    self._logger.info("Binance WebSocket closed/error")
                    break

        except asyncio.CancelledError:
            self._logger.info("Binance read loop cancelled")
            raise

    def _process_kline(self, data: dict) -> None:
        """Process a kline message from Binance combined stream.

        Combined stream format:
            {"stream": "btcusdt@kline_1m", "data": {"e": "kline", "s": "BTCUSDT", "k": {...}}}

        Kline fields (k):
            "c": close price (string)
            "T": kline close time (ms)
            "x": is this kline closed? (bool)
        """
        kline_data = data.get("data", {})
        k = kline_data.get("k", {})

        # Only buffer CLOSED candles
        if not k.get("x", False):
            return

        symbol = kline_data.get("s", "").lower()
        if symbol not in self._buffers:
            return

        candle = {
            "close": float(k.get("c", 0)),
            "time_ms": int(k.get("T", 0)),
        }

        self._buffers[symbol].append(candle)
        self._logger.debug(
            f"Candle buffered: {symbol} close={candle['close']:.2f} "
            f"(buffer={len(self._buffers[symbol])})"
        )

    def get_momentum(
        self, asset: str, lookback: int = None, threshold: float = None
    ) -> MomentumResult:
        """Get current momentum for an asset. Non-blocking, reads cache only.

        Args:
            asset: Asset name from RTDS (e.g. "BTC", "ETH", "SOL")
            lookback: Override number of candles (default: config.momentum_candles)
            threshold: Override min momentum threshold (default: config.min_momentum_pct)

        Returns:
            MomentumResult with direction, momentum_pct, confidence, age_secs
        """
        # Map asset to Binance symbol
        symbol = ASSET_TO_BINANCE.get(asset)
        if symbol is None:
            return MomentumResult(
                direction="UNKNOWN", momentum_pct=0.0, confidence=0.0, age_secs=0.0
            )

        n_candles = lookback or self._config.momentum_candles
        candles = self._buffers.get(symbol)
        if candles is None or len(candles) < n_candles:
            return MomentumResult(
                direction="UNKNOWN", momentum_pct=0.0, confidence=0.0, age_secs=0.0
            )

        # Get the lookback slice
        candle_list = list(candles)
        recent = candle_list[-n_candles:]

        # Calculate momentum
        start_price = recent[0]["close"]
        end_price = recent[-1]["close"]

        if start_price == 0:
            return MomentumResult(
                direction="UNKNOWN", momentum_pct=0.0, confidence=0.0, age_secs=0.0
            )

        momentum_pct = (end_price - start_price) / start_price

        # Direction classification with adaptive threshold
        min_pct = threshold or self._config.min_momentum_pct
        if momentum_pct > min_pct:
            direction = "UP"
        elif momentum_pct < -min_pct:
            direction = "DOWN"
        else:
            direction = "NEUTRAL"

        # Confidence: tanh scaling (smooth saturation, no hard cap)
        confidence = math.tanh(abs(momentum_pct) / 0.003)

        # Age: seconds since last candle closed
        last_time_ms = candle_list[-1]["time_ms"]
        age_secs = (time.time() * 1000 - last_time_ms) / 1000

        return MomentumResult(
            direction=direction,
            momentum_pct=momentum_pct,
            confidence=confidence,
            age_secs=age_secs,
        )

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
