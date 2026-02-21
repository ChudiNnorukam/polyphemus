"""Gabagool Tracker — real-time whale intelligence for adaptive learning.

Polls gabagool22's Polymarket trading activity via CLOB Data API,
matches paired trades, computes fill rates, pair cost distributions,
timing patterns, and estimated PnL for the adaptive tuner.
"""

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

from .config import setup_logger


@dataclass
class PairedTrade:
    """A matched pair of Up+Down buys on the same slug."""
    slug: str
    asset: str
    market_type: str          # "updown_5m" or "updown_15m"
    up_price: float
    down_price: float
    up_size: float
    down_size: float
    pair_cost: float          # up_price + down_price
    profit_per_share: float   # 1.0 - pair_cost
    up_timestamp: float
    down_timestamp: float
    fill_gap_secs: float      # time between first and second leg fill
    matched_shares: float     # min(up_size, down_size)
    estimated_pnl: float      # matched_shares * profit_per_share


@dataclass
class GabagoolInsights:
    """Rich insights from gabagool's recent trading activity."""
    # Pair cost analysis
    avg_pair_cost: float = 0.0
    median_pair_cost: float = 0.0
    min_pair_cost: float = 0.0
    max_pair_cost: float = 0.0
    pair_cost_p25: float = 0.0    # 25th percentile
    pair_cost_p75: float = 0.0    # 75th percentile
    total_pairs: int = 0

    # Fill rate and timing
    avg_fill_gap_secs: float = 0.0   # avg time between Up and Down fills
    max_fill_gap_secs: float = 0.0
    fill_rate: float = 0.0          # % of slugs where both sides filled

    # Side price distribution
    avg_side_price: float = 0.0     # avg individual side price at entry
    side_price_range: tuple = (0.0, 0.0)  # (min, max) side prices he enters at

    # Activity
    entry_trigger_price: float = 0.0
    preferred_window: str = ""
    avg_size_per_side: float = 0.0
    trades_per_hour: float = 0.0
    active_now: bool = False
    last_seen: float = 0.0
    total_tracked: int = 0
    updown_pct: float = 0.0

    # PnL estimation
    estimated_hourly_pnl: float = 0.0
    estimated_pnl_per_pair: float = 0.0
    total_estimated_pnl: float = 0.0

    # Asset breakdown
    asset_distribution: dict = field(default_factory=dict)  # {"BTC": 0.65, "ETH": 0.35}

    # Timing patterns
    avg_entry_offset_secs: float = 0.0  # how early in the window he enters


@dataclass
class TrackedTrade:
    """A single observed trade from gabagool."""
    trade_id: str
    timestamp: float
    slug: str
    asset: str
    side: str         # "BUY" or "SELL"
    outcome: str      # "Up" or "Down"
    price: float
    size: float
    market_type: str  # "updown_5m", "updown_15m", "other"


class GabagoolTracker:
    """Polls and analyzes gabagool22's Polymarket trades with deep pair analysis."""

    WALLET = "0x6031b6eed1c97e853c6e0f03ad3ce3529351f96d"
    POLL_INTERVAL = 30  # seconds
    BUFFER_SIZE = 500   # trades in ring buffer

    DATA_API_URLS = [
        "https://data-api.polymarket.com/activity",
        "https://gamma-api.polymarket.com/activity",
    ]
    CLOB_TRADES_URL = "https://clob.polymarket.com/data/trades"

    def __init__(self, session: Optional[aiohttp.ClientSession] = None):
        self._session = session
        self._owns_session = session is None
        self._logger = setup_logger("polyphemus.gabagool")
        self._trades: deque[TrackedTrade] = deque(maxlen=self.BUFFER_SIZE)
        self._seen_ids: set[str] = set()
        self._last_poll: float = 0.0
        self._api_url: Optional[str] = None
        self._poll_errors: int = 0

        # Paired trade history (persists across buffer evictions)
        self._paired_trades: deque[PairedTrade] = deque(maxlen=200)
        self._last_insights_ts: float = 0.0
        self._cached_insights: Optional[GabagoolInsights] = None

    async def start(self):
        """Main polling loop."""
        if not self._session:
            self._session = aiohttp.ClientSession()
        self._logger.info(f"Gabagool tracker started | wallet={self.WALLET[:10]}...")
        await self._discover_api()

        while True:
            try:
                new_count = await self._poll()
                if new_count > 0:
                    self._match_pairs()
            except Exception as e:
                self._poll_errors += 1
                if self._poll_errors <= 3 or self._poll_errors % 10 == 0:
                    self._logger.warning(f"Poll error #{self._poll_errors}: {e}")
            await asyncio.sleep(self.POLL_INTERVAL)

    async def _discover_api(self):
        """Try endpoints to find one that works."""
        for url in self.DATA_API_URLS:
            try:
                async with self._session.get(
                    url,
                    params={"user": self.WALLET, "limit": "5"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if isinstance(data, list) and len(data) > 0:
                            self._api_url = url
                            self._logger.info(f"API discovered: {url} ({len(data)} trades)")
                            return
            except Exception:
                continue

        try:
            async with self._session.get(
                self.CLOB_TRADES_URL,
                params={"maker_address": self.WALLET, "limit": "5"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list) and len(data) > 0:
                        self._api_url = self.CLOB_TRADES_URL
                        self._logger.info(f"API discovered: CLOB trades ({len(data)} trades)")
                        return
        except Exception:
            pass

        self._logger.warning("No working API endpoint found — tracker will retry each poll")

    async def _poll(self) -> int:
        """Fetch recent trades and add new ones to buffer. Returns new trade count."""
        if not self._api_url:
            await self._discover_api()
            if not self._api_url:
                return 0

        try:
            params = {"limit": "50"}
            if self._api_url == self.CLOB_TRADES_URL:
                params["maker_address"] = self.WALLET
            else:
                params["user"] = self.WALLET

            async with self._session.get(
                self._api_url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    self._poll_errors += 1
                    return 0
                data = await resp.json()
        except Exception:
            self._poll_errors += 1
            raise

        if not isinstance(data, list):
            return 0

        new_count = 0
        for raw in data:
            trade = self._parse_trade(raw)
            if trade and trade.trade_id not in self._seen_ids:
                self._trades.append(trade)
                self._seen_ids.add(trade.trade_id)
                new_count += 1
                if len(self._seen_ids) > self.BUFFER_SIZE * 2:
                    active_ids = {t.trade_id for t in self._trades}
                    self._seen_ids = active_ids

        if new_count > 0:
            self._logger.info(f"Gabagool: +{new_count} new trades (buffer: {len(self._trades)})")
        self._last_poll = time.time()
        self._poll_errors = 0
        self._cached_insights = None  # Invalidate cache
        return new_count

    def _parse_trade(self, raw: dict) -> Optional[TrackedTrade]:
        """Parse a raw API response into TrackedTrade."""
        try:
            trade_id = (
                raw.get("id")
                or raw.get("tradeId")
                or raw.get("transactionHash")
                or raw.get("hash")
                or str(hash(str(raw)))
            )

            ts = raw.get("timestamp") or raw.get("createdAt") or raw.get("time")
            if isinstance(ts, str):
                from datetime import datetime, timezone
                try:
                    if ts.endswith("Z"):
                        ts = ts[:-1] + "+00:00"
                    ts = datetime.fromisoformat(ts).timestamp()
                except ValueError:
                    ts = time.time()
            elif ts is None:
                ts = time.time()

            slug = raw.get("slug") or raw.get("market_slug") or raw.get("marketSlug") or ""
            market_type = "other"
            if "updown-5m" in slug:
                market_type = "updown_5m"
            elif "updown-15m" in slug:
                market_type = "updown_15m"

            asset = raw.get("asset") or ""
            # Data API returns token IDs as "asset" — prefer slug-based extraction
            if slug and "-" in slug:
                slug_asset = slug.split("-")[0].upper()
                if slug_asset in ("BTC", "ETH", "SOL", "XRP", "DOGE", "MATIC"):
                    asset = slug_asset
            if not asset and slug:
                asset = slug.split("-")[0].upper() if slug else ""

            side = (raw.get("side") or raw.get("type") or "BUY").upper()
            outcome = raw.get("outcome") or raw.get("direction") or ""
            price = float(raw.get("price") or raw.get("avg_price") or 0)
            size = float(raw.get("size") or raw.get("amount") or raw.get("shares") or 0)

            return TrackedTrade(
                trade_id=str(trade_id),
                timestamp=float(ts),
                slug=slug,
                asset=asset,
                side=side,
                outcome=outcome,
                price=price,
                size=size,
                market_type=market_type,
            )
        except Exception as e:
            self._logger.debug(f"Parse failed: {e}")
            return None

    def _match_pairs(self):
        """Match Up+Down BUY trades on the same slug into PairedTrades."""
        trades = list(self._trades)
        updown_buys = [t for t in trades if t.side == "BUY" and t.market_type.startswith("updown")]

        # Group by slug
        slug_buys: dict[str, list[TrackedTrade]] = {}
        for t in updown_buys:
            slug_buys.setdefault(t.slug, []).append(t)

        # Track which slugs we've already paired
        paired_slugs = {p.slug for p in self._paired_trades}

        new_pairs = 0
        for slug, buys in slug_buys.items():
            if slug in paired_slugs:
                continue

            up_trades = [b for b in buys if b.outcome.lower() == "up"]
            down_trades = [b for b in buys if b.outcome.lower() == "down"]

            if not up_trades or not down_trades:
                continue

            # Use first Up and first Down trade for the pair
            up = up_trades[0]
            down = down_trades[0]
            pair_cost = up.price + down.price
            # Sanity: skip pairs with unrealistic pair cost (data errors, non-arb trades)
            if pair_cost > 1.05 or pair_cost < 0.80:
                continue
            profit = 1.0 - pair_cost
            matched = min(up.size, down.size)
            fill_gap = abs(up.timestamp - down.timestamp)

            pair = PairedTrade(
                slug=slug,
                asset=up.asset,
                market_type=up.market_type,
                up_price=up.price,
                down_price=down.price,
                up_size=up.size,
                down_size=down.size,
                pair_cost=pair_cost,
                profit_per_share=profit,
                up_timestamp=up.timestamp,
                down_timestamp=down.timestamp,
                fill_gap_secs=fill_gap,
                matched_shares=matched,
                estimated_pnl=matched * profit,
            )
            self._paired_trades.append(pair)
            new_pairs += 1

        if new_pairs > 0:
            self._logger.info(
                f"Gabagool pairs: +{new_pairs} new (total: {len(self._paired_trades)}) | "
                f"latest pair_cost=${self._paired_trades[-1].pair_cost:.4f}"
            )

    def get_insights(self) -> GabagoolInsights:
        """Compute rich insights from buffered trades and paired history."""
        now = time.time()

        # Cache for 10 seconds to avoid recomputing every call
        if self._cached_insights and now - self._last_insights_ts < 10:
            return self._cached_insights

        if not self._trades:
            return GabagoolInsights()

        trades = list(self._trades)
        pairs = list(self._paired_trades)

        # === PAIR COST ANALYSIS ===
        pair_costs = sorted(p.pair_cost for p in pairs) if pairs else []
        n = len(pair_costs)

        avg_pair = sum(pair_costs) / n if n else 0.0
        median_pair = pair_costs[n // 2] if n else 0.0
        p25 = pair_costs[n // 4] if n >= 4 else (pair_costs[0] if n else 0.0)
        p75 = pair_costs[3 * n // 4] if n >= 4 else (pair_costs[-1] if n else 0.0)

        # === FILL RATE ===
        updown_buys = [t for t in trades if t.side == "BUY" and t.market_type.startswith("updown")]
        slug_buys: dict[str, set] = {}
        for t in updown_buys:
            slug_buys.setdefault(t.slug, set()).add(t.outcome.lower())
        total_slugs = len(slug_buys)
        paired_slugs = sum(1 for outcomes in slug_buys.values() if "up" in outcomes and "down" in outcomes)
        fill_rate = paired_slugs / total_slugs if total_slugs > 0 else 0.0

        # === FILL TIMING ===
        fill_gaps = [p.fill_gap_secs for p in pairs]
        avg_fill_gap = sum(fill_gaps) / len(fill_gaps) if fill_gaps else 0.0
        max_fill_gap = max(fill_gaps) if fill_gaps else 0.0

        # === SIDE PRICE DISTRIBUTION ===
        side_prices = [p.up_price for p in pairs] + [p.down_price for p in pairs]
        avg_side = sum(side_prices) / len(side_prices) if side_prices else 0.0
        side_range = (min(side_prices), max(side_prices)) if side_prices else (0.0, 0.0)

        # === ENTRY TRIGGER (individual buy prices) ===
        buy_trades = [t for t in updown_buys if t.price > 0]
        entry_prices = [t.price for t in buy_trades]
        entry_trigger = sum(entry_prices) / len(entry_prices) if entry_prices else 0.0

        # === WINDOW PREFERENCE ===
        w5m = sum(1 for t in updown_buys if t.market_type == "updown_5m")
        w15m = sum(1 for t in updown_buys if t.market_type == "updown_15m")
        preferred = "5m" if w5m > w15m else ("15m" if w15m > w5m else "both")

        # === ACTIVITY RATE ===
        sorted_ts = sorted(t.timestamp for t in trades)
        if len(sorted_ts) >= 2:
            time_span = sorted_ts[-1] - sorted_ts[0]
            if time_span > 300:
                trades_per_hour = len(trades) / (time_span / 3600)
            elif time_span > 60:
                trades_per_hour = len(trades) / (time_span / 3600)
            else:
                recent = [t for t in sorted_ts if now - t < 300]
                trades_per_hour = len(recent) * 12
        else:
            trades_per_hour = 0.0

        last_ts = max(t.timestamp for t in trades) if trades else 0
        active_now = (now - last_ts) < 300

        # === PnL ESTIMATION ===
        total_pnl = sum(p.estimated_pnl for p in pairs)
        pnl_per_pair = total_pnl / len(pairs) if pairs else 0.0
        # Estimate hourly PnL from pair rate
        pair_ts = sorted(p.up_timestamp for p in pairs) if pairs else []
        if len(pair_ts) >= 2:
            pair_span = pair_ts[-1] - pair_ts[0]
            if pair_span > 300:
                pairs_per_hour = len(pairs) / (pair_span / 3600)
                hourly_pnl = pnl_per_pair * pairs_per_hour
            else:
                hourly_pnl = 0.0
        else:
            hourly_pnl = 0.0

        # === ASSET DISTRIBUTION ===
        asset_counts: dict[str, int] = {}
        for t in updown_buys:
            asset_counts[t.asset] = asset_counts.get(t.asset, 0) + 1
        total_asset = sum(asset_counts.values())
        asset_dist = {a: c / total_asset for a, c in asset_counts.items()} if total_asset > 0 else {}

        # === ENTRY TIMING (how early in window) ===
        entry_offsets = []
        for p in pairs:
            first_ts = min(p.up_timestamp, p.down_timestamp)
            # Parse window epoch from slug: "btc-updown-5m-1771079700" → 1771079700
            parts = p.slug.rsplit("-", 1)
            if len(parts) == 2:
                try:
                    window_start = int(parts[1])
                    offset = first_ts - window_start
                    if 0 <= offset <= 900:  # Sanity: within 15 min
                        entry_offsets.append(offset)
                except (ValueError, TypeError):
                    pass
        avg_offset = sum(entry_offsets) / len(entry_offsets) if entry_offsets else 0.0

        # === SIZE ===
        avg_size = sum(t.size for t in buy_trades) / len(buy_trades) if buy_trades else 0.0

        updown_trades = [t for t in trades if t.market_type.startswith("updown")]

        insights = GabagoolInsights(
            avg_pair_cost=avg_pair,
            median_pair_cost=median_pair,
            min_pair_cost=pair_costs[0] if pair_costs else 0.0,
            max_pair_cost=pair_costs[-1] if pair_costs else 0.0,
            pair_cost_p25=p25,
            pair_cost_p75=p75,
            total_pairs=len(pairs),
            avg_fill_gap_secs=avg_fill_gap,
            max_fill_gap_secs=max_fill_gap,
            fill_rate=fill_rate,
            avg_side_price=avg_side,
            side_price_range=side_range,
            entry_trigger_price=entry_trigger,
            preferred_window=preferred,
            avg_size_per_side=avg_size,
            trades_per_hour=trades_per_hour,
            active_now=active_now,
            last_seen=last_ts,
            total_tracked=len(trades),
            updown_pct=len(updown_trades) / len(trades) if trades else 0.0,
            estimated_hourly_pnl=hourly_pnl,
            estimated_pnl_per_pair=pnl_per_pair,
            total_estimated_pnl=total_pnl,
            asset_distribution=asset_dist,
            avg_entry_offset_secs=avg_offset,
        )

        self._cached_insights = insights
        self._last_insights_ts = now
        return insights

    @property
    def stats(self) -> dict:
        """Stats for dashboard API."""
        i = self.get_insights()
        return {
            "wallet": self.WALLET[:10] + "...",
            "total_tracked": i.total_tracked,
            "total_pairs": i.total_pairs,
            "active_now": i.active_now,
            "last_seen": i.last_seen,
            "pair_cost": {
                "avg": round(i.avg_pair_cost, 4),
                "median": round(i.median_pair_cost, 4),
                "min": round(i.min_pair_cost, 4),
                "max": round(i.max_pair_cost, 4),
                "p25": round(i.pair_cost_p25, 4),
                "p75": round(i.pair_cost_p75, 4),
            },
            "fill_rate": round(i.fill_rate * 100, 1),
            "avg_fill_gap_secs": round(i.avg_fill_gap_secs, 1),
            "max_fill_gap_secs": round(i.max_fill_gap_secs, 1),
            "side_price_range": [round(i.side_price_range[0], 3), round(i.side_price_range[1], 3)],
            "avg_side_price": round(i.avg_side_price, 3),
            "entry_trigger_price": round(i.entry_trigger_price, 3),
            "preferred_window": i.preferred_window,
            "avg_size_per_side": round(i.avg_size_per_side, 1),
            "trades_per_hour": round(i.trades_per_hour, 1),
            "updown_pct": round(i.updown_pct * 100, 1),
            "pnl": {
                "per_pair": round(i.estimated_pnl_per_pair, 3),
                "hourly": round(i.estimated_hourly_pnl, 2),
                "total": round(i.total_estimated_pnl, 2),
            },
            "asset_distribution": {k: round(v * 100, 1) for k, v in i.asset_distribution.items()},
            "avg_entry_offset_secs": round(i.avg_entry_offset_secs, 0),
            "poll_errors": self._poll_errors,
            "api_url": self._api_url or "none",
        }
