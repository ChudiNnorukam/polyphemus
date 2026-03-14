"""
Chainlink Oracle Feed: WebSocket + poll hybrid for BTC/ETH/SOL price oracles
on Polygon.

Provides the actual resolution price source for Polymarket 5-minute crypto markets.
Used for:
1. Snipe confirmation gate (oracle disagrees with midpoint direction -> block)
2. Oracle reversal exit (oracle says position is losing -> exit early)
3. Reversal short (after early exit, buy the opposite side)

Architecture:
- Single WS connection subscribing to AnswerUpdated events from all asset contracts
- Per-asset poll fallback via latestRoundData every 20s
- Per-asset epoch anchoring for 5m/15m window open prices
"""

import asyncio
import json
import time
from collections import deque
from typing import Dict, Optional, Tuple

import aiohttp

from .types import BACKOFF_BASE, BACKOFF_MAX, BACKOFF_MULTIPLIER
from .config import Settings, setup_logger

# Polymarket RTDS WebSocket — actual resolution price source (Chainlink Data Streams)
RTDS_WS_URL = "wss://ws-live-data.polymarket.com"
RTDS_PING_INTERVAL = 5  # send text "ping" every 5s (NOT a WS ping frame)
RTDS_SYMBOL_MAP = {
    "btc/usd": "BTC", "eth/usd": "ETH",
    "sol/usd": "SOL", "xrp/usd": "XRP",
}

# Chainlink Aggregator contracts on Polygon (all 8 decimals)
ASSET_CONTRACTS = {
    "BTC": "0xc907E116054Ad103354f2D350FD2514433D57F6f",
    "ETH": "0xF9680D99D6C9589e2a93a78A04A279e509205945",
    "SOL": "0x10C8264C0935b3B9870013e057f330Ff3e9C56dC",
    "XRP": "0x785ba89291f676b5386652eB12b30cF361020694",
}

# Reverse lookup: lowercase contract address -> asset
CONTRACT_TO_ASSET = {v.lower(): k for k, v in ASSET_CONTRACTS.items()}

# AnswerUpdated(int256 indexed current, uint256 indexed roundId, uint256 updatedAt)
ANSWER_UPDATED_TOPIC = (
    "0x0559884fd3a460db3073b7fc896cc77986f16e378210ded43186175bf646fc5f"
)

# latestRoundData() selector
LATEST_ROUND_DATA_SELECTOR = "0xfeaf968c"

# Price has 8 decimals
PRICE_DECIMALS = 1e8

# Circuit breaker
CIRCUIT_BREAKER_THRESHOLD = 3

# Poll interval for fallback RPC (seconds)
POLL_INTERVAL = 20

# Max epoch entries to keep (prune older than this many seconds)
EPOCH_MAX_AGE = 1800  # 30 minutes

# Price history buffer per asset
PRICE_BUFFER_SIZE = 120  # ~40 min at 20s intervals

# Direction cross confirmation: require N consecutive readings on the new side
# before firing the callback. Prevents single-tick noise from causing bad trades.
# At ~1 update/sec via WS, 3 readings = ~3s confirmation window.
DIRECTION_CROSS_CONFIRM_COUNT = 3


class _AssetState:
    """Per-asset price state and epoch anchoring."""

    __slots__ = (
        "asset", "contract", "current_price", "last_update_ts",
        "price_buffer", "epoch_open_prices", "prev_above_open",
        "cross_confirm_count", "direction_history",
    )

    def __init__(self, asset: str, contract: str):
        self.asset = asset
        self.contract = contract
        self.current_price: Optional[float] = None
        self.last_update_ts: float = 0.0
        self.price_buffer: deque = deque(maxlen=PRICE_BUFFER_SIZE)
        self.epoch_open_prices: Dict[int, float] = {}
        self.prev_above_open: Dict[int, Optional[bool]] = {}  # epoch -> last known direction
        self.cross_confirm_count: Dict[int, int] = {}  # epoch -> consecutive readings on new side
        self.direction_history: deque = deque(maxlen=10)  # last 10 oracle direction readings ("up" or "down")


class ChainlinkFeed:
    """Multi-asset WebSocket + poll hybrid feed for Chainlink oracles on Polygon."""

    def __init__(self, config: Settings):
        self._config = config
        self._logger = setup_logger("polyphemus.chainlink")
        self._startup_time = time.time()

        api_key = config.oracle_alchemy_api_key
        if not api_key:
            self._logger.warning(
                "ChainlinkFeed: no ORACLE_ALCHEMY_API_KEY, feed will not connect"
            )
        self._ws_url = f"wss://polygon-mainnet.g.alchemy.com/v2/{api_key}"
        self._rpc_url = f"https://polygon-mainnet.g.alchemy.com/v2/{api_key}"

        # Per-asset state
        self._assets: Dict[str, _AssetState] = {}
        for asset, contract in ASSET_CONTRACTS.items():
            self._assets[asset] = _AssetState(asset, contract)

        # RTDS price trajectory buffer: asset -> list of (timestamp, price) tuples
        # Max 120 entries per asset = ~60s of history at 0.5s update frequency
        self._rtds_history: Dict[str, list] = {asset: [] for asset in ASSET_CONTRACTS.keys()}

        # Connection state
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._poll_session: Optional[aiohttp.ClientSession] = None
        self._consecutive_failures = 0
        self._circuit_open = False
        self._ws_connected = False
        self._rtds_connected = False

        # Direction-cross callback: called when oracle direction flips for an epoch
        self._on_direction_cross = None

    def set_on_direction_cross(self, callback) -> None:
        """Set callback for oracle direction crossover events.

        Callback signature: async callback(asset, epoch, window, is_above_open, price, open_price)
        """
        self._on_direction_cross = callback

    def get_current_price(self, asset: str = "BTC") -> Optional[float]:
        """Latest price from Chainlink oracle. None if feed down."""
        state = self._assets.get(asset.upper())
        if state is None:
            return None
        return state.current_price

    def get_window_open_price(self, epoch: int, window_secs: int,
                              asset: str = "BTC") -> Optional[float]:
        """Chainlink price recorded at or just after the start of this epoch."""
        state = self._assets.get(asset.upper())
        if state is None:
            return None
        return state.epoch_open_prices.get(epoch)

    def get_epoch_delta_pct(self, asset: str, epoch: int,
                            window_secs: int = 300) -> Optional[float]:
        """Percentage change from epoch open to current oracle price.

        Returns None if no epoch open price or current price available.
        Positive = price rose (UP direction), negative = price fell (DOWN).
        """
        open_price = self.get_window_open_price(epoch, window_secs, asset)
        current = self.get_current_price(asset)
        if open_price is None or current is None or open_price <= 0:
            return None
        return (current - open_price) / open_price

    def is_above_window_open(self, epoch: int, window_secs: int,
                             asset: str = "BTC") -> Optional[bool]:
        """True = current price at or above window open (UP winning). None if no data."""
        state = self._assets.get(asset.upper())
        if state is None:
            return None
        if state.current_price is None or not self.is_healthy(asset):
            return None
        open_price = state.epoch_open_prices.get(epoch)
        if open_price is None:
            return None
        return state.current_price >= open_price

    def is_healthy(self, asset: str = "BTC") -> bool:
        """True if last update was within stale threshold (default 60s)."""
        state = self._assets.get(asset.upper())
        if state is None or state.current_price is None:
            return False
        age = time.time() - state.last_update_ts
        return age < self._config.oracle_stale_threshold_secs

    def get_direction_confirmed(self, asset: str, n: int = 3) -> Optional[str]:
        """Returns direction only if last n oracle readings agree.

        Returns "up" or "down" if confirmed, None if mixed or insufficient data.
        Used by IGOC strategy to confirm oracle direction before entry.
        """
        state = self._assets.get(asset.upper())
        if state is None:
            return None

        history = list(state.direction_history)
        if len(history) < n:
            return None

        recent = history[-n:]
        if all(d == "up" for d in recent):
            return "up"
        if all(d == "down" for d in recent):
            return "down"
        return None

    def get_price_trajectory(self, asset: str, window_secs: float = 60.0) -> list:
        """Return RTDS price trajectory for the last window_secs seconds.

        Returns: list of (timestamp, price) tuples, most recent last.
                 Returns empty list if no history or asset unknown.
        """
        asset = asset.upper()
        history = self._rtds_history.get(asset, [])
        if not history:
            return []

        cutoff_ts = time.time() - window_secs
        return [(ts, p) for ts, p in history if ts >= cutoff_ts]

    def get_trajectory_direction(self, asset: str, window_secs: float = 30.0) -> Optional[str]:
        """Determine direction based on price trajectory over window_secs.

        Returns "up", "down", or None if:
        - Less than 2 points in trajectory
        - Price change < 0.5%
        """
        trajectory = self.get_price_trajectory(asset, window_secs)
        if len(trajectory) < 2:
            return None

        start_price = trajectory[0][1]
        end_price = trajectory[-1][1]
        if start_price <= 0:
            return None

        pct_change = (end_price - start_price) / start_price
        if abs(pct_change) < 0.005:  # 0.5%
            return None

        return "up" if pct_change > 0 else "down"

    @property
    def staleness_secs(self) -> float:
        """Seconds since last update (best across all assets)."""
        best = float("inf")
        for state in self._assets.values():
            if state.last_update_ts > 0:
                age = time.time() - state.last_update_ts
                best = min(best, age)
        return best

    @property
    def circuit_open(self) -> bool:
        return self._circuit_open

    async def start(self) -> None:
        """Start WS subscription, poll fallback, and RTDS loops."""
        tasks = []

        assets_str = ",".join(sorted(self._assets.keys()))
        self._logger.info(
            f"ChainlinkFeed starting | assets={assets_str} | "
            f"stale_threshold={self._config.oracle_stale_threshold_secs}s | "
            f"rtds={'ON' if self._config.rtds_enabled else 'OFF'}"
        )

        if self._config.oracle_alchemy_api_key:
            tasks.append(self._ws_subscribe_loop())
        else:
            self._logger.warning("ChainlinkFeed: no API key, Alchemy WS disabled")

        tasks.append(self._poll_fallback_loop())

        if self._config.rtds_enabled:
            tasks.append(self._rtds_ws_loop())

        await asyncio.gather(*tasks)

    async def _ws_subscribe_loop(self) -> None:
        """Subscribe to AnswerUpdated events for all assets via single Alchemy WS."""
        attempt = 0
        contracts = [s.contract for s in self._assets.values()]

        while True:
            try:
                self._session = aiohttp.ClientSession()
                self._ws = await self._session.ws_connect(
                    self._ws_url, timeout=15
                )

                # Subscribe to logs for AnswerUpdated across all contracts
                subscribe_msg = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_subscribe",
                    "params": [
                        "logs",
                        {
                            "address": contracts,
                            "topics": [ANSWER_UPDATED_TOPIC],
                        },
                    ],
                }
                await self._ws.send_json(subscribe_msg)
                assets_str = ",".join(sorted(self._assets.keys()))
                self._logger.info(
                    f"Chainlink WS connected, subscribed to AnswerUpdated "
                    f"for {assets_str}"
                )

                attempt = 0
                self._consecutive_failures = 0
                self._circuit_open = False
                self._ws_connected = True

                await self._ws_read_loop()

            except asyncio.CancelledError:
                self._logger.info("Chainlink WS cancelled")
                raise

            except Exception as e:
                self._consecutive_failures += 1
                self._ws_connected = False
                if self._consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
                    if not self._circuit_open:
                        self._logger.error(
                            f"Chainlink circuit breaker OPEN after "
                            f"{self._consecutive_failures} failures"
                        )
                    self._circuit_open = True

                delay = min(
                    BACKOFF_BASE * (BACKOFF_MULTIPLIER ** attempt),
                    BACKOFF_MAX,
                )
                self._logger.warning(
                    f"Chainlink WS disconnected: {e}, retry in {delay}s"
                )
                attempt += 1
                await asyncio.sleep(delay)

            finally:
                self._ws_connected = False
                await self._close_ws()

    async def _ws_read_loop(self) -> None:
        """Read AnswerUpdated events and route to correct asset state."""
        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    # Subscription confirmation
                    if "result" in data and "id" in data:
                        self._logger.debug(
                            f"Chainlink subscription confirmed: {data['result']}"
                        )
                        continue

                    # Subscription event
                    params = data.get("params", {})
                    result = params.get("result", {})
                    topics = result.get("topics", [])
                    log_address = result.get("address", "").lower()

                    if len(topics) >= 2:
                        # Identify which asset this event belongs to
                        asset = CONTRACT_TO_ASSET.get(log_address)
                        if asset is None:
                            continue

                        # AnswerUpdated: topics[1] = int256 current (indexed)
                        price_raw = int(topics[1], 16)
                        if price_raw >= 2**255:
                            price_raw -= 2**256
                        price = price_raw / PRICE_DECIMALS
                        now = time.time()

                        if price > 0:
                            self._update_price(asset, price, now)
                            self._logger.debug(
                                f"Chainlink WS: {asset}/USD ${price:,.2f}"
                            )

                except (json.JSONDecodeError, KeyError, IndexError, ValueError) as e:
                    self._logger.debug(f"Chainlink WS parse error: {e}")

            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                self._logger.info("Chainlink WS closed/error")
                break

    async def _poll_fallback_loop(self) -> None:
        """Poll latestRoundData for all assets. Adaptive interval: 5s when WS down, 20s when up."""
        self._poll_session = aiohttp.ClientSession()
        try:
            while True:
                try:
                    any_ws = self._ws_connected or self._rtds_connected
                    interval = 5 if not any_ws else POLL_INTERVAL
                    await asyncio.sleep(interval)
                    for asset, state in self._assets.items():
                        price = await self._poll_latest_round_data(state.contract)
                        if price is not None and price > 0:
                            now = time.time()
                            self._update_price(asset, price, now)

                    # Reset circuit breaker on successful poll
                    if self._circuit_open and not self._ws_connected:
                        self._consecutive_failures = 0
                        self._circuit_open = False

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self._logger.debug(f"Chainlink poll error: {e}")
        finally:
            if self._poll_session and not self._poll_session.closed:
                await self._poll_session.close()
            self._poll_session = None

    async def _poll_latest_round_data(self, contract: str) -> Optional[float]:
        """Call latestRoundData() via HTTP RPC for a specific contract."""
        try:
            session = self._poll_session
            if session is None or session.closed:
                self._poll_session = aiohttp.ClientSession()
                session = self._poll_session
            payload = {
                "jsonrpc": "2.0",
                "method": "eth_call",
                "params": [
                    {
                        "to": contract,
                        "data": LATEST_ROUND_DATA_SELECTOR,
                    },
                    "latest",
                ],
                "id": 1,
            }
            async with session.post(
                self._rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                result = await resp.json()
                hex_data = result.get("result", "0x")
                if hex_data == "0x" or len(hex_data) < 130:
                    return None

                answer_hex = hex_data[2 + 64:2 + 128]
                answer_raw = int(answer_hex, 16)
                if answer_raw >= 2**255:
                    answer_raw -= 2**256
                return answer_raw / PRICE_DECIMALS

        except Exception as e:
            self._logger.debug(f"Chainlink latestRoundData error: {e}")
            return None

    def _update_price(self, asset: str, price: float, now: float) -> None:
        """Update per-asset price state, anchor epochs, detect direction crossovers."""
        state = self._assets.get(asset)
        if state is None:
            return

        state.current_price = price
        state.last_update_ts = now
        state.price_buffer.append((now, price))

        # Track RTDS price trajectory (max 120 entries per asset)
        if asset in self._rtds_history:
            self._rtds_history[asset].append((now, price))
            if len(self._rtds_history[asset]) > 120:
                self._rtds_history[asset].pop(0)

        # Anchor epoch open prices for both 5m and 15m windows
        for window in (300, 900):
            epoch = int(now // window) * window
            if epoch not in state.epoch_open_prices:
                state.epoch_open_prices[epoch] = price
                self._logger.debug(
                    f"Chainlink epoch anchor: {asset} window={window}s "
                    f"epoch={epoch} price=${price:,.2f}"
                )

            # Detect direction crossover for this epoch (with confirmation)
            # Key by (epoch, window) to prevent 5m/15m counter collision
            # at 15-minute boundaries where both compute the same epoch value.
            if self._on_direction_cross and epoch in state.epoch_open_prices:
                open_price = state.epoch_open_prices[epoch]
                is_above = price >= open_price
                ew_key = (epoch, window)

                # Track direction in history for get_direction_confirmed()
                direction_str = "up" if is_above else "down"
                state.direction_history.append(direction_str)

                prev = state.prev_above_open.get(ew_key)
                if prev is not None and is_above != prev:
                    # Direction crossed - increment confirmation counter
                    count = state.cross_confirm_count.get(ew_key, 0) + 1
                    state.cross_confirm_count[ew_key] = count
                    if count >= DIRECTION_CROSS_CONFIRM_COUNT:
                        # Confirmed crossover - fire callback
                        state.prev_above_open[ew_key] = is_above
                        state.cross_confirm_count[ew_key] = 0
                        try:
                            asyncio.ensure_future(
                                self._on_direction_cross(
                                    asset, epoch, window, is_above, price, open_price
                                )
                            )
                        except Exception as e:
                            self._logger.warning(
                                f"Direction cross callback error: {asset} epoch={epoch}: {e}"
                            )
                else:
                    # Same direction as before or first reading - reset counter
                    state.cross_confirm_count[ew_key] = 0
                    if prev is None:
                        state.prev_above_open[ew_key] = is_above

        # Prune old epochs
        cutoff = now - EPOCH_MAX_AGE
        stale_keys = [k for k in state.epoch_open_prices if k < cutoff]
        for k in stale_keys:
            del state.epoch_open_prices[k]
        stale_prev = [k for k in state.prev_above_open if k[0] < cutoff]
        for k in stale_prev:
            del state.prev_above_open[k]
        stale_confirm = [k for k in state.cross_confirm_count if k[0] < cutoff]
        for k in stale_confirm:
            del state.cross_confirm_count[k]

    async def _rtds_ws_loop(self) -> None:
        """Subscribe to Polymarket RTDS for resolution-aligned Chainlink Data Streams prices."""
        attempt = 0
        while True:
            rtds_session = None
            rtds_ws = None
            try:
                rtds_session = aiohttp.ClientSession()
                rtds_ws = await rtds_session.ws_connect(
                    RTDS_WS_URL, timeout=15
                )

                # Subscribe to all chainlink symbols
                subscribe_msg = {
                    "action": "subscribe",
                    "subscriptions": [{
                        "topic": "crypto_prices_chainlink",
                        "type": "*",
                        "filters": "",
                    }],
                }
                await rtds_ws.send_json(subscribe_msg)
                self._logger.info("RTDS connected, subscribed to crypto_prices_chainlink")

                attempt = 0
                self._rtds_connected = True

                # Send initial ping
                await rtds_ws.send_str("ping")
                last_ping = time.time()

                while True:
                    # Read with timeout for ping scheduling
                    try:
                        msg = await asyncio.wait_for(
                            rtds_ws.receive(), timeout=RTDS_PING_INTERVAL
                        )
                    except asyncio.TimeoutError:
                        await rtds_ws.send_str("ping")
                        last_ping = time.time()
                        continue

                    if msg.type == aiohttp.WSMsgType.TEXT:
                        # Send ping if interval elapsed
                        now_t = time.time()
                        if now_t - last_ping >= RTDS_PING_INTERVAL:
                            await rtds_ws.send_str("ping")
                            last_ping = now_t

                        raw = msg.data
                        if "payload" not in raw:
                            continue

                        try:
                            data = json.loads(raw)
                            payload = data.get("payload", {})
                            symbol = payload.get("symbol", "")
                            value = payload.get("value")
                            asset = RTDS_SYMBOL_MAP.get(symbol)
                            if asset and value and value > 0:
                                self._update_price(asset, float(value), time.time())
                        except (json.JSONDecodeError, TypeError, ValueError):
                            pass

                    elif msg.type in (
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.ERROR,
                        aiohttp.WSMsgType.CLOSING,
                    ):
                        self._logger.info("RTDS WS closed/error")
                        break

            except asyncio.CancelledError:
                self._logger.info("RTDS WS cancelled")
                raise

            except Exception as e:
                delay = min(
                    BACKOFF_BASE * (BACKOFF_MULTIPLIER ** attempt),
                    BACKOFF_MAX,
                )
                self._logger.warning(f"RTDS disconnected: {e}, retry in {delay}s")
                attempt += 1
                await asyncio.sleep(delay)

            finally:
                self._rtds_connected = False
                try:
                    if rtds_ws and not rtds_ws.closed:
                        await rtds_ws.close()
                except Exception:
                    pass
                try:
                    if rtds_session and not rtds_session.closed:
                        await rtds_session.close()
                except Exception:
                    pass

    async def _close_ws(self) -> None:
        """Close WebSocket and session safely."""
        try:
            if self._ws and not self._ws.closed:
                await self._ws.close()
        except Exception:
            pass
        try:
            if self._session and not self._session.closed:
                await self._session.close()
        except Exception:
            pass
        self._ws = None
        self._session = None

    async def close(self) -> None:
        """Public shutdown method."""
        await self._close_ws()
        if self._poll_session and not self._poll_session.closed:
            await self._poll_session.close()
        self._poll_session = None

    def stats(self) -> dict:
        """Return feed stats for dashboard/monitoring."""
        asset_stats = {}
        for asset, state in self._assets.items():
            age = (time.time() - state.last_update_ts) if state.last_update_ts > 0 else float("inf")
            asset_stats[asset] = {
                "price": state.current_price,
                "staleness": round(age, 1),
                "healthy": self.is_healthy(asset),
                "epochs": len(state.epoch_open_prices),
            }
        return {
            "assets": asset_stats,
            "ws_connected": self._ws_connected,
            "rtds_connected": self._rtds_connected,
            "circuit_open": self._circuit_open,
            "consecutive_failures": self._consecutive_failures,
        }
