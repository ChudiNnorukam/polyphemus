"""Tugao9 Copy-Trade Watcher — signal source from tugao9's live trades.

Polls tugao9's Polymarket trading activity via CLOB Data API,
detects new BUY trades on updown-5m markets, and emits signals
into the bot's entry pipeline for copy-trading.

Tugao9 profile: 0x970e744a34cd0795ff7b4ba844018f17b7fd5c26
  - $21.7K profit on $967K volume, 5,742 predictions
  - Near-50c RTDS-style trader (entries at 0.40-0.60)
"""

import asyncio
import time
from typing import Callable, Optional, Awaitable

import aiohttp

from .config import setup_logger


class Tugao9Watcher:
    """Polls tugao9's trades and emits copy-trade signals."""

    DATA_API_URLS = [
        "https://data-api.polymarket.com/activity",
        "https://gamma-api.polymarket.com/activity",
    ]
    CLOB_TRADES_URL = "https://clob.polymarket.com/data/trades"

    def __init__(
        self,
        address: str,
        poll_interval: float = 5.0,
        min_price: float = 0.40,
        max_price: float = 0.60,
        shadow: bool = True,
        on_signal: Optional[Callable[[dict], Awaitable]] = None,
        momentum_feed=None,
        session: Optional[aiohttp.ClientSession] = None,
        allowed_assets: Optional[set] = None,
    ):
        self._address = address
        self._poll_interval = poll_interval
        self._min_price = min_price
        self._max_price = max_price
        self._shadow = shadow
        self._on_signal = on_signal
        self._momentum_feed = momentum_feed
        self._session = session
        self._allowed_assets = allowed_assets
        self._owns_session = session is None
        self._logger = setup_logger("polyphemus.tugao9")
        self._seen_ids: set = set()
        self._api_url: Optional[str] = None
        self._poll_errors: int = 0
        self._signals_emitted: int = 0
        self._last_poll: float = 0.0

    async def start(self):
        """Main polling loop."""
        if not self._session:
            self._session = aiohttp.ClientSession()
        self._logger.info(
            f"Tugao9 watcher started | addr={self._address[:10]}... | "
            f"interval={self._poll_interval}s | shadow={self._shadow} | "
            f"price={self._min_price}-{self._max_price}"
        )
        await self._discover_api()

        while True:
            try:
                await self._poll_and_signal()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._poll_errors += 1
                if self._poll_errors <= 3 or self._poll_errors % 10 == 0:
                    self._logger.warning(f"Poll error #{self._poll_errors}: {e}")
            await asyncio.sleep(self._poll_interval)

    async def _discover_api(self):
        """Try endpoints to find one that works."""
        for url in self.DATA_API_URLS:
            try:
                async with self._session.get(
                    url,
                    params={"user": self._address, "limit": "5"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if isinstance(data, list) and len(data) > 0:
                            self._api_url = url
                            self._logger.info(f"API discovered: {url}")
                            return
            except Exception:
                continue

        try:
            async with self._session.get(
                self.CLOB_TRADES_URL,
                params={"maker_address": self._address, "limit": "5"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list) and len(data) > 0:
                        self._api_url = self.CLOB_TRADES_URL
                        self._logger.info("API discovered: CLOB trades")
                        return
        except Exception:
            pass

        self._logger.warning("No working API endpoint — will retry each poll")

    async def _poll_and_signal(self):
        """Fetch recent trades, detect new BUYs, emit signals."""
        if not self._api_url:
            await self._discover_api()
            if not self._api_url:
                return

        params = {"limit": "50"}
        if self._api_url == self.CLOB_TRADES_URL:
            params["maker_address"] = self._address
        else:
            params["user"] = self._address

        try:
            async with self._session.get(
                self._api_url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    self._poll_errors += 1
                    return
                data = await resp.json()
        except Exception:
            self._poll_errors += 1
            raise

        if not isinstance(data, list):
            return

        self._last_poll = time.time()
        self._poll_errors = 0

        for raw in data:
            trade_id = str(
                raw.get("id") or raw.get("tradeId")
                or raw.get("transactionHash") or raw.get("hash")
                or hash(str(raw))
            )
            if trade_id in self._seen_ids:
                continue
            self._seen_ids.add(trade_id)

            # Trim seen_ids to prevent unbounded growth
            if len(self._seen_ids) > 2000:
                self._seen_ids = set(list(self._seen_ids)[-1000:])

            # Only BUY trades on updown-5m markets
            side = (raw.get("side") or raw.get("type") or "").upper()
            if side != "BUY":
                continue

            slug = raw.get("slug") or raw.get("market_slug") or raw.get("marketSlug") or ""
            if "updown-5m" not in slug:
                continue

            asset = slug.split("-")[0].upper() if slug else ""
            if asset not in ("BTC", "ETH", "SOL", "XRP"):
                continue

            # Skip assets not in bot's ASSET_FILTER (prevents infinite token_id retry for uncached assets)
            if self._allowed_assets and asset not in self._allowed_assets:
                continue

            price = float(raw.get("price") or raw.get("avg_price") or 0)
            outcome = (raw.get("outcome") or raw.get("direction") or "").lower()
            size = float(raw.get("size") or raw.get("amount") or raw.get("shares") or 0)

            if not outcome or price <= 0:
                continue

            # Price range filter
            if not (self._min_price <= price <= self._max_price):
                continue

            # Parse time remaining FIRST - skip expired epochs before token_id lookup
            secs_left = None
            slug_parts = slug.rsplit("-", 1)
            if len(slug_parts) == 2 and slug_parts[1].isdigit():
                epoch_ts = int(slug_parts[1])
                market_end = epoch_ts + 300  # 5m window
                secs_left = market_end - time.time()

            # Skip if epoch already expired
            if secs_left is not None and secs_left < 10:
                continue

            # Resolve token_id from momentum_feed's live market cache
            token_id = None
            market_cache = getattr(self._momentum_feed, '_market_cache', None) if self._momentum_feed else None
            if market_cache:
                cache_entry = market_cache.get(slug, {})
                if outcome == "up":
                    token_id = cache_entry.get("up_token_id")
                elif outcome == "down":
                    token_id = cache_entry.get("down_token_id")

            # If token_id not resolved, skip but DON'T mark as seen (retry next poll)
            if token_id is None:
                self._seen_ids.discard(trade_id)
                self._logger.warning(
                    f"token_id=None for {slug} {outcome} - cache not ready, will retry"
                )
                continue

            signal = {
                "source": "tugao9_copy",
                "shadow": self._shadow,
                "slug": slug,
                "token_id": token_id,
                "asset": asset,
                "outcome": outcome,
                "price": price,
                "direction": outcome.upper(),
                "tugao9_size": size,
                "time_remaining_secs": secs_left,
            }

            mode = "SHADOW" if self._shadow else "LIVE"
            secs_str = f"{secs_left:.0f}" if secs_left is not None else "N/A"
            self._logger.info(
                f"[TUGAO9_COPY {mode}] {slug} {outcome.upper()} @ {price:.3f} | "
                f"size=${size:.2f} secs={secs_str}"
            )

            if self._on_signal:
                self._signals_emitted += 1
                await self._on_signal(signal)

    @property
    def stats(self) -> dict:
        """Stats for dashboard/health checks."""
        return {
            "wallet": self._address[:10] + "...",
            "active": self._api_url is not None,
            "shadow": self._shadow,
            "signals_emitted": self._signals_emitted,
            "seen_trades": len(self._seen_ids),
            "poll_errors": self._poll_errors,
            "last_poll": self._last_poll,
            "api_url": self._api_url or "none",
        }
