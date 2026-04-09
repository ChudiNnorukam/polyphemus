"""
Polyphemus Signal Feed: WebSocket connection to DB reference trader via RTDS.

Connects to Polymarket's Real-Time Data Stream (wss://ws-live-data.polymarket.com),
subscribes to all trade activity, then filters client-side for DB's wallet.

Handles:
- WebSocket reconnection with exponential backoff
- RTDS subscription and ping keep-alive
- Signal deduplication via transactionHash
- SELL signal detection against open positions
- Field name mapping from RTDS to Polyphemus format
- Session rotation for connection freshness
- Stale signal watchdog
- Mock mode for testing
"""

import asyncio
import json
import time
from collections import OrderedDict
from typing import Optional, Callable, Any

import aiohttp

from .models import (
    BACKOFF_BASE,
    BACKOFF_MAX,
    BACKOFF_MULTIPLIER,
    HEALTHY_RESET_SECS,
    SESSION_ROTATE_SECS,
    STALE_THRESHOLD_SECS,
    SEEN_TRADES_CAP,
    SEEN_TRADES_EVICT_PCT,
)
from .config import Settings, setup_logger
from .position_store import PositionStore

# DB reference trader wallet
DB_WALLET = "0xe00740bce98a594e26861838885ab310ec3b548c"

# RTDS WebSocket endpoint (NOT the CLOB WS)
RTDS_URL = "wss://ws-live-data.polymarket.com"

# Keep-alive ping interval
PING_INTERVAL = 5


class SignalFeed:
    """WebSocket-based signal feed from DB reference trader via RTDS."""

    def __init__(
        self,
        config: Settings,
        store: PositionStore,
        on_signal: Callable[[dict], Any],
        mock_file: Optional[str] = None,
    ):
        """
        Initialize signal feed.

        Args:
            config: Settings object with wallet_address and other config
            store: PositionStore for tracking open positions
            on_signal: Async callback(signal_dict) for new signals
            mock_file: Optional path to JSON mock file for testing
        """
        self.config = config
        self.store = store
        self.on_signal = on_signal
        self.mock_file = mock_file

        self._seen_trades = OrderedDict()
        self._ws = None
        self._session = None
        self._last_signal_time = time.time()
        self._connected_since = 0
        self._ping_task = None

        self._logger = setup_logger("polyphemus.feed")

    async def start(self) -> None:
        """Start the signal feed (mock or live)."""
        if self.mock_file:
            await self._replay_mock()
        else:
            await self._connect_loop()

    async def _connect_loop(self) -> None:
        """Exponential backoff reconnection loop."""
        attempt = 0

        while True:
            try:
                self._session = aiohttp.ClientSession()
                self._ws = await self._session.ws_connect(RTDS_URL, timeout=10)
                self._connected_since = time.time()
                attempt = 0

                self._logger.info("RTDS WebSocket connected")

                # Send subscription message
                subscribe_msg = {
                    "action": "subscribe",
                    "subscriptions": [{"topic": "activity", "type": "trades"}],
                }
                await self._ws.send_json(subscribe_msg)
                self._logger.info("Subscribed to RTDS trades activity")

                # Start ping keep-alive
                self._ping_task = asyncio.create_task(self._ping_loop())

                try:
                    await self._read_loop()
                finally:
                    if self._ping_task:
                        self._ping_task.cancel()
                        self._ping_task = None

            except Exception as e:
                delay = min(
                    BACKOFF_BASE * (BACKOFF_MULTIPLIER ** attempt),
                    BACKOFF_MAX,
                )
                self._logger.error(
                    f"WS disconnected: {e}, retry in {delay}s"
                )
                attempt += 1
                await asyncio.sleep(delay)

            finally:
                await self._close_session()

    async def _ping_loop(self) -> None:
        """Send periodic pings to keep RTDS connection alive."""
        try:
            while self._ws and not self._ws.closed:
                await asyncio.sleep(PING_INTERVAL)
                if self._ws and not self._ws.closed:
                    await self._ws.ping()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._logger.debug(f"Ping error: {e}")

    def _map_rtds_to_signal(self, trade: dict) -> dict:
        """Map RTDS trade fields to Polyphemus's expected signal format.

        RTDS fields -> Polyphemus fields:
            transactionHash -> tx_hash
            side            -> direction
            outcome         -> outcome
            usdcSize        -> usdc_size
            price           -> price
            asset           -> token_id (also kept as asset)
            title           -> market_title
            slug/eventSlug  -> slug
            proxyWallet     -> (filtered, not passed)
        """
        slug = trade.get("slug") or trade.get("eventSlug", "")

        # Extract asset name from title (e.g. "Solana Up or Down..." -> "SOL")
        title = trade.get("title", "")
        asset_name = ""
        title_lower = title.lower()
        if "bitcoin" in title_lower or "btc" in title_lower:
            asset_name = "BTC"
        elif "ethereum" in title_lower or "eth" in title_lower:
            asset_name = "ETH"
        elif "solana" in title_lower or "sol" in title_lower:
            asset_name = "SOL"
        elif "xrp" in title_lower or "ripple" in title_lower:
            asset_name = "XRP"

        # Capture trade's own timestamp for latency measurement
        trade_ts = trade.get("timestamp") or trade.get("createdAt") or trade.get("timeStamp")
        trade_epoch = None
        if trade_ts:
            try:
                trade_epoch = float(trade_ts)
                if trade_epoch > 1e12:
                    trade_epoch = trade_epoch / 1000.0
            except (ValueError, TypeError):
                trade_epoch = None

        return {
            "tx_hash": trade.get("transactionHash", ""),
            "direction": trade.get("side", "BUY").upper(),
            "outcome": trade.get("outcome", "").lower(),
            "usdc_size": float(trade.get("usdcSize") or trade.get("size") or 0),
            "price": float(trade.get("price", 0)),
            "token_id": trade.get("asset", ""),
            "asset": asset_name,
            "market_title": title,
            "slug": slug,
            "timestamp": time.time(),
            "trade_timestamp": trade_epoch,
        }

    async def _process_trade(self, trade: dict) -> None:
        """Process a single RTDS trade from DB's wallet."""
        # Filter: only DB's wallet
        wallet = trade.get("proxyWallet", "").lower()
        if wallet != DB_WALLET.lower():
            return

        # Map to Polyphemus format
        signal = self._map_rtds_to_signal(trade)

        # Deduplication
        tx_hash = signal.get("tx_hash")
        if tx_hash and tx_hash in self._seen_trades:
            return

        # Add to dedup cache
        if tx_hash:
            self._seen_trades[tx_hash] = True

            # Evict oldest entries if over capacity
            if len(self._seen_trades) > SEEN_TRADES_CAP:
                evict_count = int(SEEN_TRADES_CAP * SEEN_TRADES_EVICT_PCT)
                for _ in range(evict_count):
                    self._seen_trades.popitem(last=False)

        # Log with latency measurement
        latency_str = ""
        trade_ts = signal.get("trade_timestamp")
        if trade_ts:
            latency = time.time() - trade_ts
            latency_str = f" | lag={latency:.1f}s"

        slug = signal.get("slug", "")
        secs_left_str = ""
        parts = slug.rsplit('-', 1) if slug else []
        if len(parts) == 2 and parts[1].isdigit():
            market_end = int(parts[1]) + 900
            secs_left = market_end - time.time()
            secs_left_str = f" | {secs_left:.0f}s left"

        self._logger.info(
            f"DB trade: {signal['direction']} {signal['outcome']} "
            f"{signal['asset']} @ {signal['price']:.4f} "
            f"(${signal['usdc_size']:.2f}) | {signal['slug']}"
            f"{latency_str}{secs_left_str}"
        )

        # Check for SELL signals against open positions
        if signal["direction"] == "SELL":
            slug = signal.get("slug")
            if slug:
                position = self.store.get_by_slug(slug)
                if position:
                    position.metadata['sell_signal_received'] = True
                    self._logger.info(f"SELL signal matched open position: {slug}")

        # Route signal to callback
        await self.on_signal(signal)

    async def _read_loop(self) -> None:
        """Read and process messages from RTDS WebSocket."""
        msg_count = 0
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    # Track connection liveness on ANY message
                    self._last_signal_time = time.time()
                    msg_count += 1

                    try:
                        data = json.loads(msg.data)

                        # Log first 3 messages for diagnostics
                        if msg_count <= 3:
                            keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
                            self._logger.info(f"RTDS msg #{msg_count}: keys={keys}")

                        payload = data.get("payload", data)

                        # RTDS payload can be a list of trades or a single trade
                        if isinstance(payload, list):
                            for trade in payload:
                                await self._process_trade(trade)
                        elif isinstance(payload, dict):
                            await self._process_trade(payload)

                    except json.JSONDecodeError:
                        continue
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        self._logger.debug(f"Message processing error: {e}")

                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    self._logger.info("WebSocket closed by server")
                    break

                elif msg.type == aiohttp.WSMsgType.ERROR:
                    self._logger.error("WebSocket error")
                    break

                # Session rotation: close and reconnect with fresh session
                if (
                    time.time() - self._connected_since
                    > SESSION_ROTATE_SECS
                ):
                    self._logger.info(
                        "Session rotation: closing for fresh connection"
                    )
                    break

        except asyncio.CancelledError:
            self._logger.info("Read loop cancelled")
            raise

    async def _close_session(self) -> None:
        """Close WebSocket and session safely."""
        try:
            if self._ws:
                await self._ws.close()
        except Exception as e:
            self._logger.debug(f"Error closing WebSocket: {e}")

        try:
            if self._session:
                await self._session.close()
        except Exception as e:
            self._logger.debug(f"Error closing session: {e}")

    async def start_stale_watchdog(self) -> None:
        """Background task: monitor signal staleness and trigger reconnect."""
        try:
            while True:
                await asyncio.sleep(30)

                age = self.last_signal_age
                if age > STALE_THRESHOLD_SECS:
                    self._logger.warning(
                        f"Signal stale {age:.0f}s, forcing reconnect"
                    )
                    if self._ws:
                        await self._ws.close()

        except asyncio.CancelledError:
            pass

    async def _replay_mock(self) -> None:
        """Replay signals from mock JSON file for testing."""
        if not self.mock_file:
            return

        try:
            with open(self.mock_file, "r") as f:
                signals = json.load(f)

            if not isinstance(signals, list):
                self._logger.error("Mock file must contain a JSON array")
                return

            self._logger.info(f"Replaying {len(signals)} mock signals")

            for signal in signals:
                # Dedup
                tx_hash = signal.get("tx_hash")
                if tx_hash and tx_hash in self._seen_trades:
                    continue

                if tx_hash:
                    self._seen_trades[tx_hash] = True

                # Check for SELL signals
                direction = signal.get("direction", "").upper()
                if direction == "SELL":
                    slug = signal.get("slug")
                    if slug:
                        position = self.store.get_by_slug(slug)
                        if position:
                            position.metadata['sell_signal_received'] = True

                # Route to callback
                await self.on_signal(signal)
                await asyncio.sleep(0.5)

            self._logger.info("Mock replay complete")

        except Exception as e:
            self._logger.error(f"Mock replay failed: {e}")
            raise

    @property
    def last_signal_age(self) -> float:
        """Return seconds since last signal received."""
        return time.time() - self._last_signal_time


# REST API polling endpoint (proven reliable in V1)
ACTIVITY_API = "https://data-api.polymarket.com/activity"


class PollingSignalFeed:
    """REST API polling signal feed from DB reference trader.

    Polls https://data-api.polymarket.com/activity for DB's trades.
    Drop-in replacement for SignalFeed (WebSocket) with identical interface.
    """

    def __init__(
        self,
        config: Settings,
        store: PositionStore,
        on_signal: Callable[[dict], Any],
        mock_file: Optional[str] = None,
    ):
        self.config = config
        self.store = store
        self.on_signal = on_signal
        self.mock_file = mock_file

        self._seen_trades = OrderedDict()
        self._last_signal_time = time.time()
        self._session = None

        self._logger = setup_logger("polyphemus.poll_feed")

    async def start(self) -> None:
        """Start the signal feed (mock or live polling)."""
        if self.mock_file:
            await self._replay_mock()
        else:
            await self._poll_loop()

    async def _poll_loop(self) -> None:
        """Main polling loop with session management."""
        self._logger.info(
            f"Starting REST polling feed (interval={self.config.poll_interval}s)"
        )

        # Seed seen_trades with recent activity to avoid replaying old trades
        await self._seed_seen_trades()

        session_created = time.time()
        self._session = aiohttp.ClientSession()

        try:
            while True:
                try:
                    trades = await self._fetch_activity()
                    new_count = 0

                    for trade in trades:
                        tx_hash = trade.get("transactionHash")
                        if not tx_hash or tx_hash in self._seen_trades:
                            continue

                        # Process this new trade
                        await self._process_trade(trade)
                        new_count += 1

                    if new_count > 0:
                        self._logger.info(f"Processed {new_count} new DB trades")

                except aiohttp.ClientError as e:
                    self._logger.warning(f"Poll request failed: {e}")
                except Exception as e:
                    self._logger.error(f"Poll loop error: {e}")

                # Rotate session every 30 minutes
                if time.time() - session_created > SESSION_ROTATE_SECS:
                    await self._close_session()
                    self._session = aiohttp.ClientSession()
                    session_created = time.time()
                    self._logger.info("Session rotated")

                await asyncio.sleep(self.config.poll_interval)

        finally:
            await self._close_session()

    async def _seed_seen_trades(self) -> None:
        """Fetch recent trades and mark as seen (don't process old ones)."""
        try:
            async with aiohttp.ClientSession() as session:
                params = {"user": DB_WALLET, "limit": 50}
                async with session.get(
                    ACTIVITY_API, params=params, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        trades = await resp.json()
                        for trade in trades:
                            tx_hash = trade.get("transactionHash")
                            if tx_hash:
                                self._seen_trades[tx_hash] = True
                        self._logger.info(
                            f"Seeded {len(self._seen_trades)} existing trades (will not process)"
                        )
                    else:
                        self._logger.warning(
                            f"Seed fetch returned {resp.status}"
                        )
        except Exception as e:
            self._logger.error(f"Seed fetch failed: {e}")

    async def _fetch_activity(self) -> list:
        """Fetch recent trades from DB's wallet via REST API."""
        if not self._session:
            return []

        params = {"user": DB_WALLET, "limit": 20}
        try:
            async with self._session.get(
                ACTIVITY_API, params=params, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    self._last_signal_time = time.time()
                    return await resp.json()
                else:
                    self._logger.warning(f"Activity API returned {resp.status}")
                    return []
        except asyncio.TimeoutError:
            self._logger.warning("Activity API timeout")
            return []

    async def _process_trade(self, trade: dict) -> None:
        """Process a single trade from DB's wallet (already filtered by API)."""
        # Map to Polyphemus format (same mapping as RTDS)
        signal = self._map_trade_to_signal(trade)

        tx_hash = signal.get("tx_hash")

        # Add to dedup cache
        if tx_hash:
            self._seen_trades[tx_hash] = True
            if len(self._seen_trades) > SEEN_TRADES_CAP:
                evict_count = int(SEEN_TRADES_CAP * SEEN_TRADES_EVICT_PCT)
                for _ in range(evict_count):
                    self._seen_trades.popitem(last=False)

        # Log with latency measurement
        latency_str = ""
        trade_ts = signal.get("trade_timestamp")
        if trade_ts:
            latency = time.time() - trade_ts
            latency_str = f" | lag={latency:.1f}s"

        # Calculate time remaining in market window for diagnostics
        slug = signal.get("slug", "")
        secs_left_str = ""
        parts = slug.rsplit('-', 1) if slug else []
        if len(parts) == 2 and parts[1].isdigit():
            market_end = int(parts[1]) + 900
            secs_left = market_end - time.time()
            secs_left_str = f" | {secs_left:.0f}s left"

        self._logger.info(
            f"DB trade: {signal['direction']} {signal['outcome']} "
            f"{signal['asset']} @ {signal['price']:.4f} "
            f"(${signal['usdc_size']:.2f}) | {signal['slug']}"
            f"{latency_str}{secs_left_str}"
        )

        # Check for SELL signals against open positions
        if signal["direction"] == "SELL":
            slug = signal.get("slug")
            if slug:
                position = self.store.get_by_slug(slug)
                if position:
                    position.metadata['sell_signal_received'] = True
                    self._logger.info(f"SELL signal matched open position: {slug}")

        # Route signal to callback
        await self.on_signal(signal)

    def _map_trade_to_signal(self, trade: dict) -> dict:
        """Map Data API trade fields to Polyphemus's expected signal format.

        Data API fields are identical to RTDS fields:
            transactionHash, side, outcome, usdcSize, price, asset, title,
            slug/eventSlug
        """
        slug = trade.get("slug") or trade.get("eventSlug", "")

        title = trade.get("title", "")
        asset_name = ""
        title_lower = title.lower()
        if "bitcoin" in title_lower or "btc" in title_lower:
            asset_name = "BTC"
        elif "ethereum" in title_lower or "eth" in title_lower:
            asset_name = "ETH"
        elif "solana" in title_lower or "sol" in title_lower:
            asset_name = "SOL"
        elif "xrp" in title_lower or "ripple" in title_lower:
            asset_name = "XRP"

        # Capture trade's own timestamp for latency measurement
        trade_ts = trade.get("timestamp") or trade.get("createdAt") or trade.get("timeStamp")
        trade_epoch = None
        if trade_ts:
            try:
                trade_epoch = float(trade_ts)
                # If it looks like milliseconds, convert to seconds
                if trade_epoch > 1e12:
                    trade_epoch = trade_epoch / 1000.0
            except (ValueError, TypeError):
                trade_epoch = None

        return {
            "tx_hash": trade.get("transactionHash", ""),
            "direction": trade.get("side", "BUY").upper(),
            "outcome": trade.get("outcome", "").lower(),
            "usdc_size": float(trade.get("usdcSize") or trade.get("size") or 0),
            "price": float(trade.get("price", 0)),
            "token_id": trade.get("asset", ""),
            "asset": asset_name,
            "market_title": title,
            "slug": slug,
            "timestamp": time.time(),
            "trade_timestamp": trade_epoch,
        }

    async def _close_session(self) -> None:
        """Close aiohttp session safely."""
        try:
            if self._session:
                await self._session.close()
                self._session = None
        except Exception as e:
            self._logger.debug(f"Error closing session: {e}")

    async def start_stale_watchdog(self) -> None:
        """Monitor polling staleness (same interface as SignalFeed)."""
        try:
            while True:
                await asyncio.sleep(30)
                age = self.last_signal_age
                if age > STALE_THRESHOLD_SECS:
                    self._logger.warning(
                        f"Polling stale {age:.0f}s — API may be down"
                    )
        except asyncio.CancelledError:
            pass

    async def _replay_mock(self) -> None:
        """Replay signals from mock JSON file for testing."""
        if not self.mock_file:
            return
        try:
            with open(self.mock_file, "r") as f:
                signals = json.load(f)
            if not isinstance(signals, list):
                self._logger.error("Mock file must contain a JSON array")
                return
            self._logger.info(f"Replaying {len(signals)} mock signals")
            for signal in signals:
                tx_hash = signal.get("tx_hash")
                if tx_hash and tx_hash in self._seen_trades:
                    continue
                if tx_hash:
                    self._seen_trades[tx_hash] = True
                direction = signal.get("direction", "").upper()
                if direction == "SELL":
                    slug = signal.get("slug")
                    if slug:
                        position = self.store.get_by_slug(slug)
                        if position:
                            position.metadata['sell_signal_received'] = True
                await self.on_signal(signal)
                await asyncio.sleep(0.5)
            self._logger.info("Mock replay complete")
        except Exception as e:
            self._logger.error(f"Mock replay failed: {e}")
            raise

    @property
    def last_signal_age(self) -> float:
        """Return seconds since last signal received."""
        return time.time() - self._last_signal_time
