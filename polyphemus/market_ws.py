"""Real-time midpoint feed via Polymarket CLOB WebSocket.

Subscribes to book updates for active market tokens and maintains
a live cache of best bid/ask/midpoint. Eliminates REST polling latency
from the critical signal-to-order path.
"""

import asyncio
import json
import time
from typing import Dict, Optional, Set

import aiohttp

from .config import setup_logger

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
PING_INTERVAL = 10  # seconds
RECONNECT_BASE = 1
RECONNECT_MAX = 30
RECONNECT_MULTIPLIER = 2


class MarketWS:
    """Real-time midpoint feed via CLOB WebSocket."""

    MIDPOINT_MAX_AGE_SECS = 5.0  # Reject midpoints older than this

    def __init__(self):
        self._logger = setup_logger("polyphemus.market_ws")
        self._midpoints: Dict[str, float] = {}  # token_id -> midpoint
        self._best_bids: Dict[str, float] = {}
        self._best_asks: Dict[str, float] = {}
        self._bid_sizes: Dict[str, float] = {}   # top-of-book bid quantity
        self._ask_sizes: Dict[str, float] = {}   # top-of-book ask quantity
        self._last_update: Dict[str, float] = {}  # token_id -> epoch of last update
        self._subscribed: Set[str] = set()
        self._pending_subscribe: Set[str] = set()  # tokens to sub on (re)connect
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._connected = False
        self._update_count = 0

    async def start(self) -> None:
        """Connect and run the message loop with auto-reconnect."""
        await asyncio.gather(
            self._connect_loop(),
            self._ping_loop(),
        )

    async def subscribe(self, token_ids: list[str]) -> None:
        """Subscribe to book updates for given token IDs."""
        new_ids = [t for t in token_ids if t not in self._subscribed]
        if not new_ids:
            return

        for t in new_ids:
            self._subscribed.add(t)
            self._pending_subscribe.add(t)

        if self._connected and self._ws:
            await self._send_subscribe(new_ids)
            for t in new_ids:
                self._pending_subscribe.discard(t)

    async def unsubscribe(self, token_ids: list[str]) -> None:
        """Unsubscribe from token IDs."""
        for t in token_ids:
            self._subscribed.discard(t)
            self._pending_subscribe.discard(t)
            self._midpoints.pop(t, None)
            self._best_bids.pop(t, None)
            self._best_asks.pop(t, None)
            self._bid_sizes.pop(t, None)
            self._ask_sizes.pop(t, None)
            self._last_update.pop(t, None)

    def get_midpoint(self, token_id: str) -> float:
        """Return cached midpoint (0.0 if unknown/unsubscribed/stale)."""
        mid = self._midpoints.get(token_id, 0.0)
        if mid <= 0:
            return 0.0
        age = time.time() - self._last_update.get(token_id, 0)
        if age > self.MIDPOINT_MAX_AGE_SECS:
            return 0.0  # Force REST fallback
        return mid

    def get_midpoint_age(self, token_id: str) -> float:
        """Return seconds since last update for token (inf if never updated)."""
        last = self._last_update.get(token_id, 0)
        if last <= 0:
            return float('inf')
        return time.time() - last

    def get_best_bid(self, token_id: str) -> float:
        """Return cached best bid (0.0 if unknown)."""
        return self._best_bids.get(token_id, 0.0)

    def get_best_ask(self, token_id: str) -> float:
        """Return cached best ask (0.0 if unknown)."""
        return self._best_asks.get(token_id, 0.0)

    def get_spread(self, token_id: str) -> float:
        """Return bid-ask spread (ask - bid). Returns -1.0 if no data available."""
        bid = self._best_bids.get(token_id, 0.0)
        ask = self._best_asks.get(token_id, 0.0)
        if bid <= 0 or ask <= 0:
            return -1.0  # No data — caller should skip spread check
        return ask - bid

    def get_book_depth(self, token_id: str) -> Optional[dict]:
        """Return top-of-book qty imbalance. None if no size data yet.

        imbalance = bid_qty / (bid_qty + ask_qty)
        > 0.5 = buy pressure, < 0.5 = sell pressure.
        Only populated from 'book' snapshot messages (not price_change).
        """
        bid_q = self._bid_sizes.get(token_id, 0.0)
        ask_q = self._ask_sizes.get(token_id, 0.0)
        if bid_q <= 0 or ask_q <= 0:
            return None
        imbalance = bid_q / (bid_q + ask_q)
        return {"bid_qty": bid_q, "ask_qty": ask_q, "imbalance": round(imbalance, 4)}

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def close(self) -> None:
        """Clean shutdown."""
        self._connected = False
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

    # -- internal --

    async def _connect_loop(self) -> None:
        attempt = 0
        while True:
            try:
                self._session = aiohttp.ClientSession()
                self._ws = await self._session.ws_connect(WS_URL, timeout=10)
                self._connected = True
                attempt = 0
                self._logger.info(
                    f"MarketWS connected | {len(self._subscribed)} tokens tracked"
                )

                # Re-subscribe all tokens on reconnect
                if self._subscribed:
                    await self._send_subscribe(list(self._subscribed))
                    self._pending_subscribe.clear()

                await self._message_loop()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._connected = False
                delay = min(RECONNECT_BASE * (RECONNECT_MULTIPLIER ** attempt), RECONNECT_MAX)
                self._logger.warning(f"MarketWS disconnected: {e}, retry in {delay}s")
                attempt += 1
                await asyncio.sleep(delay)
            finally:
                self._connected = False
                await self._close_ws()

    async def _message_loop(self) -> None:
        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    self._process_message(data)
                except json.JSONDecodeError:
                    continue
                except Exception as e:
                    self._logger.debug(f"MarketWS msg error: {e}")
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break

    def _process_message(self, data: dict) -> None:
        event_type = data.get("event_type", "")

        if event_type == "book":
            self._handle_book(data)
        elif event_type == "price_change":
            self._handle_price_change(data)
        elif event_type:
            self._logger.debug(f"MarketWS unhandled event: {event_type}")

    def _handle_book(self, data: dict) -> None:
        """Full book snapshot — extract best bid/ask and top-of-book quantities.

        CLOB WS sends 'buys' (bids) and 'sells' (asks), each sorted
        best-first: buys[0] = highest bid, sells[0] = lowest ask.
        Each entry has both 'price' and 'size' keys.
        """
        asset_id = data.get("asset_id", "")
        if not asset_id or asset_id not in self._subscribed:
            return

        buys = data.get("buys", [])
        sells = data.get("sells", [])

        best_bid = float(buys[0]["price"]) if buys else 0.0
        best_ask = float(sells[0]["price"]) if sells else 0.0

        # Store top-of-book quantities for true imbalance calculation
        if buys:
            self._bid_sizes[asset_id] = float(buys[0].get("size", 0))
        if sells:
            self._ask_sizes[asset_id] = float(sells[0].get("size", 0))

        self._update_prices(asset_id, best_bid, best_ask)

    def _handle_price_change(self, data: dict) -> None:
        """Incremental update with best_bid/best_ask per asset."""
        for change in data.get("price_changes", []):
            asset_id = change.get("asset_id", "")
            if not asset_id or asset_id not in self._subscribed:
                continue

            best_bid = float(change.get("best_bid", 0))
            best_ask = float(change.get("best_ask", 0))

            if best_bid > 0 and best_ask > 0:
                self._update_prices(asset_id, best_bid, best_ask)

    def _update_prices(self, token_id: str, best_bid: float, best_ask: float) -> None:
        if best_bid <= 0 or best_ask <= 0:
            return
        is_first = token_id not in self._midpoints
        self._best_bids[token_id] = best_bid
        self._best_asks[token_id] = best_ask
        self._midpoints[token_id] = (best_bid + best_ask) / 2
        self._last_update[token_id] = time.time()
        self._update_count += 1
        if is_first:
            self._logger.info(
                f"MarketWS first price: {token_id[:16]}... "
                f"bid={best_bid:.4f} ask={best_ask:.4f} mid={self._midpoints[token_id]:.4f}"
            )

    async def _send_subscribe(self, token_ids: list[str]) -> None:
        if not self._ws or not self._connected:
            return
        msg = {
            "assets_ids": token_ids,
            "type": "market",
            "initial_dump": True,
        }
        try:
            await self._ws.send_json(msg)
            self._logger.debug(f"MarketWS subscribed to {len(token_ids)} tokens")
        except Exception as e:
            self._logger.warning(f"MarketWS subscribe failed: {e}")

    async def _ping_loop(self) -> None:
        while True:
            await asyncio.sleep(PING_INTERVAL)
            if self._connected and self._ws:
                try:
                    await self._ws.send_str("PING")
                except Exception:
                    pass

    async def _close_ws(self) -> None:
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
