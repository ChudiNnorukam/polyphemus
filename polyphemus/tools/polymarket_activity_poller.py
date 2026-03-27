"""PolymarketActivityPoller — shared base class for all trader analysis tools.

Extracts the common API discovery, polling, and trade parsing logic from
tugao9_watcher.py and gabagool_tracker.py into a reusable base.

Usage:
    class MyAnalyzer(PolymarketActivityPoller):
        async def on_trade(self, trade: dict) -> None:
            # process each new trade
            ...
"""

import asyncio
import time
from typing import Optional

import aiohttp


class PolymarketActivityPoller:
    """Base class for polling Polymarket trader activity via Data API / CLOB."""

    DATA_API_URLS = [
        "https://data-api.polymarket.com/activity",
        "https://gamma-api.polymarket.com/activity",
    ]
    CLOB_TRADES_URL = "https://clob.polymarket.com/data/trades"

    def __init__(
        self,
        address: str,
        poll_interval: float = 5.0,
        session: Optional[aiohttp.ClientSession] = None,
        logger=None,
    ):
        self._address = address
        self._poll_interval = poll_interval
        self._session = session
        self._owns_session = session is None
        self._logger = logger
        self._seen_ids: set[str] = set()
        self._api_url: Optional[str] = None
        self._poll_errors: int = 0
        self._last_poll: float = 0.0

    async def _ensure_session(self):
        if not self._session:
            # Avoid brotli encoding (aiohttp may lack brotli decoder)
            headers = {"Accept-Encoding": "gzip, deflate"}
            self._session = aiohttp.ClientSession(headers=headers)

    async def _discover_api(self) -> Optional[str]:
        """Try Data API endpoints, then CLOB trades. Returns working URL or None."""
        await self._ensure_session()

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
                            if self._logger:
                                self._logger.info(f"API discovered: {url}")
                            return url
            except Exception:
                continue

        # Fallback: CLOB trades endpoint
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
                        if self._logger:
                            self._logger.info("API discovered: CLOB trades")
                        return self.CLOB_TRADES_URL
        except Exception:
            pass

        if self._logger:
            self._logger.warning("No working API endpoint — will retry each poll")
        return None

    async def _fetch_page(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """Fetch one page of trades from the discovered API endpoint."""
        if not self._api_url:
            await self._discover_api()
            if not self._api_url:
                return []

        params = {"limit": str(limit)}
        if offset > 0:
            params["offset"] = str(offset)

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
                    return []
                data = await resp.json()
        except Exception:
            self._poll_errors += 1
            raise

        if not isinstance(data, list):
            return []

        self._last_poll = time.time()
        self._poll_errors = 0
        return data

    def _extract_trade_id(self, raw: dict) -> str:
        """Extract a unique trade ID from a raw API response."""
        return str(
            raw.get("id")
            or raw.get("tradeId")
            or raw.get("transactionHash")
            or raw.get("hash")
            or hash(str(raw))
        )

    def _parse_trade(self, raw: dict) -> dict:
        """Parse raw API response into normalized trade dict.

        Returns dict with keys: trade_id, timestamp, slug, asset, side,
        outcome, price, size, market_type, window.
        """
        trade_id = self._extract_trade_id(raw)

        # Timestamp parsing
        ts = raw.get("timestamp") or raw.get("createdAt") or raw.get("time")
        if isinstance(ts, str):
            from datetime import datetime
            try:
                if ts.endswith("Z"):
                    ts = ts[:-1] + "+00:00"
                ts = datetime.fromisoformat(ts).timestamp()
            except ValueError:
                ts = time.time()
        elif ts is None:
            ts = time.time()

        slug = raw.get("slug") or raw.get("market_slug") or raw.get("marketSlug") or ""

        # Market type classification
        market_type = "other"
        window = None
        if "updown-5m" in slug or "5-minute" in slug:
            market_type = "updown_5m"
            window = "5m"
        elif "updown-15m" in slug or "15-minute" in slug:
            market_type = "updown_15m"
            window = "15m"

        # Asset extraction from slug
        asset = ""
        if slug and "-" in slug:
            slug_asset = slug.split("-")[0].upper()
            if slug_asset in ("BTC", "ETH", "SOL", "XRP", "DOGE", "MATIC"):
                asset = slug_asset

        side = (raw.get("side") or raw.get("type") or "BUY").upper()
        outcome = raw.get("outcome") or raw.get("direction") or ""
        price = float(raw.get("price") or raw.get("avg_price") or 0)
        size = float(raw.get("size") or raw.get("amount") or raw.get("shares") or 0)

        # maker_address: ground-truth maker/taker classification.
        # Available from CLOB trades endpoint, absent from Activity API.
        maker_address = raw.get("maker_address") or raw.get("makerAddress") or ""

        # size_unit: 'shares' from CLOB trades, 'unknown' from Activity API.
        # Activity API 'size' semantics are ambiguous (may be dollars or shares).
        # CLOB trades 'size' is always share count.
        size_unit = "shares" if self._api_url == self.CLOB_TRADES_URL else "unknown"

        return {
            "trade_id": trade_id,
            "timestamp": float(ts),
            "slug": slug,
            "asset": asset,
            "side": side,
            "outcome": outcome,
            "price": price,
            "size": size,
            "size_unit": size_unit,
            "market_type": market_type,
            "window": window,
            "maker_address": maker_address,
        }

    def _is_new(self, trade_id: str) -> bool:
        """Check if trade_id is new and mark it as seen."""
        if trade_id in self._seen_ids:
            return False
        self._seen_ids.add(trade_id)
        # Trim to prevent unbounded growth
        if len(self._seen_ids) > 2000:
            self._seen_ids = set(list(self._seen_ids)[-1000:])
        return True

    async def ingest_all(self, max_trades: int = 10000, page_size: int = 500) -> list[dict]:
        """Paginate through all trades up to max_trades. Returns list of parsed trades."""
        await self._ensure_session()
        if not self._api_url:
            await self._discover_api()

        all_trades = []
        offset = 0
        while offset < max_trades:
            page = await self._fetch_page(limit=page_size, offset=offset)
            if not page:
                break
            for raw in page:
                parsed = self._parse_trade(raw)
                if self._is_new(parsed["trade_id"]):
                    all_trades.append(parsed)
            if len(page) < page_size:
                break  # Last page
            offset += page_size
            await asyncio.sleep(0.2)  # Rate limit courtesy

        if self._logger:
            self._logger.info(f"Ingested {len(all_trades)} trades for {self._address[:10]}...")
        return all_trades

    async def close(self):
        """Close session if we own it."""
        if self._owns_session and self._session:
            await self._session.close()
            self._session = None

    @property
    def stats(self) -> dict:
        return {
            "wallet": self._address[:10] + "...",
            "active": self._api_url is not None,
            "seen_trades": len(self._seen_ids),
            "poll_errors": self._poll_errors,
            "last_poll": self._last_poll,
            "api_url": self._api_url or "none",
        }
