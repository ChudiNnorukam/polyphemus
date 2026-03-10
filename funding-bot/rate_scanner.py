"""
Hyperliquid Funding Rate Scanner
Monitors funding rates across all perps, identifies farming opportunities.
"""

import asyncio
import logging
import time
import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from pathlib import Path

import aiohttp
import config

logger = logging.getLogger(__name__)


@dataclass
class FundingOpportunity:
    coin: str
    current_rate: float          # hourly rate (e.g., 0.0001 = 0.01%/hr)
    avg_24h_rate: float          # average over last 24h
    annualized_pct: float        # APR after fees
    net_annualized_pct: float    # APR after round-trip fees
    consecutive_positive_hrs: int
    mark_price: float
    open_interest: float
    funding_velocity: float      # rate of change (accelerating/decelerating)
    timestamp: float


class FundingRateScanner:
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._api_url = config.HL_TESTNET_URL if config.USE_TESTNET else config.HL_API_URL
        self._info_url = f"{self._api_url}/info"
        self._cache: Dict[str, FundingOpportunity] = {}
        self._db: Optional[sqlite3.Connection] = None

    async def initialize(self):
        self._session = aiohttp.ClientSession()
        Path(config.DATA_DIR).mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(config.DATABASE_PATH)
        self._init_db()
        logger.info(f"Scanner initialized | API: {self._api_url} | watching {len(config.WATCH_COINS)} coins")

    def _init_db(self):
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS funding_snapshots (
                id INTEGER PRIMARY KEY,
                coin TEXT NOT NULL,
                rate REAL NOT NULL,
                avg_24h REAL,
                annualized REAL,
                net_annualized REAL,
                consecutive_positive_hrs INTEGER,
                mark_price REAL,
                open_interest REAL,
                timestamp REAL NOT NULL
            )
        """)
        self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_snapshots_coin_ts
            ON funding_snapshots(coin, timestamp)
        """)
        self._db.commit()

    async def _post_info(self, payload: dict) -> Optional[dict]:
        try:
            async with self._session.post(
                self._info_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.error(f"HL API error: {resp.status}")
                    return None
                return await resp.json()
        except Exception as e:
            logger.error(f"HL API request failed: {e}")
            return None

    async def get_all_mids(self) -> Dict[str, float]:
        data = await self._post_info({"type": "allMids"})
        if not data:
            return {}
        return {k: float(v) for k, v in data.items()}

    async def get_meta_and_contexts(self) -> Optional[dict]:
        """Get universe metadata + current funding rates + open interest."""
        data = await self._post_info({"type": "metaAndAssetCtxs"})
        return data

    async def get_funding_history(self, coin: str, start_time_ms: int) -> List[dict]:
        data = await self._post_info({
            "type": "fundingHistory",
            "coin": coin,
            "startTime": start_time_ms,
        })
        return data if data else []

    async def scan(self) -> Dict[str, FundingOpportunity]:
        """Full scan: get current rates + history for watched coins."""
        meta = await self.get_meta_and_contexts()
        if not meta or len(meta) < 2:
            logger.error("Failed to fetch meta+contexts")
            return {}

        universe = meta[0].get("universe", [])
        contexts = meta[1]

        # Build coin -> index map
        coin_map = {}
        for i, asset in enumerate(universe):
            coin_map[asset["name"]] = i

        opportunities = {}
        now = time.time()
        start_ms = int((now - config.RATE_LOOKBACK_HOURS * 3600) * 1000)

        # Fetch histories in parallel for watched coins
        history_tasks = {}
        for coin in config.WATCH_COINS:
            if coin in coin_map:
                history_tasks[coin] = self.get_funding_history(coin, start_ms)

        histories = {}
        if history_tasks:
            results = await asyncio.gather(*history_tasks.values(), return_exceptions=True)
            for coin, result in zip(history_tasks.keys(), results):
                if isinstance(result, Exception):
                    logger.error(f"History fetch failed for {coin}: {result}")
                    histories[coin] = []
                else:
                    histories[coin] = result

        for coin in config.WATCH_COINS:
            idx = coin_map.get(coin)
            if idx is None or idx >= len(contexts):
                continue

            ctx = contexts[idx]
            current_rate = float(ctx.get("funding", "0"))
            mark_price = float(ctx.get("markPx", "0"))
            open_interest = float(ctx.get("openInterest", "0"))

            # Process history
            history = histories.get(coin, [])
            rates = [float(h.get("fundingRate", "0")) for h in history]

            avg_24h = sum(rates) / len(rates) if rates else current_rate

            # Count consecutive positive hours (from most recent)
            consecutive = 0
            for r in rates:
                if r > 0:
                    consecutive += 1
                else:
                    break

            # Funding velocity (is rate increasing or decreasing?)
            if len(rates) >= 4:
                recent_avg = sum(rates[:4]) / 4
                older_avg = sum(rates[4:8]) / 4 if len(rates) >= 8 else avg_24h
                velocity = recent_avg - older_avg
            else:
                velocity = 0.0

            # Annualized return (hourly rate * 8760 hours/year)
            annualized = avg_24h * config.HOURS_PER_YEAR

            # Net after round-trip fees (one-time cost amortized over hold period)
            # Fees are paid once at entry+exit, NOT recurring.
            # Breakeven = round_trip_fees / avg_hourly_rate (in hours)
            # Net APR assumes a 30-day hold (720h) which is realistic for farming.
            hold_hours = 720  # 30-day assumed hold
            if avg_24h > 0:
                breakeven_hours = config.ROUND_TRIP_FEES / avg_24h
                total_funding_30d = avg_24h * hold_hours
                net_30d_return = total_funding_30d - config.ROUND_TRIP_FEES
                net_annualized = (net_30d_return / (hold_hours / config.HOURS_PER_YEAR))
            else:
                breakeven_hours = float('inf')
                net_annualized = annualized

            opp = FundingOpportunity(
                coin=coin,
                current_rate=current_rate,
                avg_24h_rate=avg_24h,
                annualized_pct=annualized * 100,
                net_annualized_pct=net_annualized * 100,
                consecutive_positive_hrs=consecutive,
                mark_price=mark_price,
                open_interest=open_interest,
                funding_velocity=velocity,
                timestamp=now,
            )

            opportunities[coin] = opp

            # Persist to DB
            self._db.execute(
                "INSERT INTO funding_snapshots (coin, rate, avg_24h, annualized, net_annualized, consecutive_positive_hrs, mark_price, open_interest, timestamp) VALUES (?,?,?,?,?,?,?,?,?)",
                (coin, current_rate, avg_24h, annualized * 100, net_annualized * 100, consecutive, mark_price, open_interest, now),
            )

        self._db.commit()
        self._cache = opportunities
        return opportunities

    def rank_opportunities(self, min_net_apr: float = None) -> List[FundingOpportunity]:
        """Rank cached opportunities by net annualized return."""
        if min_net_apr is None:
            min_net_apr = config.MIN_ANNUALIZED_RETURN * 100

        viable = [
            opp for opp in self._cache.values()
            if opp.net_annualized_pct >= min_net_apr
            and opp.consecutive_positive_hrs >= config.MIN_SUSTAINED_HOURS
            and opp.current_rate > 0
        ]

        return sorted(viable, key=lambda x: x.net_annualized_pct, reverse=True)

    def format_table(self, opportunities: Dict[str, FundingOpportunity] = None) -> str:
        """Format opportunities as a readable table."""
        if opportunities is None:
            opportunities = self._cache

        if not opportunities:
            return "No data"

        sorted_opps = sorted(opportunities.values(), key=lambda x: x.net_annualized_pct, reverse=True)

        lines = [
            f"{'Coin':<6} {'Rate/hr':>9} {'Avg24h':>9} {'APR%':>7} {'NetAPR%':>7} {'BE(d)':>6} {'$/mo':>7} {'Pos.h':>5} {'OI($M)':>8} {'Signal':>6}",
            "-" * 82,
        ]

        for opp in sorted_opps[:config.LOG_TOP_N]:
            signal = "FARM" if (
                opp.net_annualized_pct >= config.MIN_ANNUALIZED_RETURN * 100
                and opp.consecutive_positive_hrs >= config.MIN_SUSTAINED_HOURS
                and opp.current_rate > 0
            ) else "WAIT" if opp.current_rate > 0 else "SKIP"

            oi_millions = opp.open_interest * opp.mark_price / 1e6

            # Breakeven in days
            if opp.avg_24h_rate > 0:
                be_days = (config.ROUND_TRIP_FEES / opp.avg_24h_rate) / 24
            else:
                be_days = 999

            # Monthly profit per $500 deployed
            monthly_profit = opp.avg_24h_rate * 720 * config.CAPITAL_PER_PAIR - config.ROUND_TRIP_FEES * config.CAPITAL_PER_PAIR

            lines.append(
                f"{opp.coin:<6} "
                f"{opp.current_rate*100:>8.4f}% "
                f"{opp.avg_24h_rate*100:>8.4f}% "
                f"{opp.annualized_pct:>6.1f}% "
                f"{opp.net_annualized_pct:>6.1f}% "
                f"{be_days:>5.1f}d "
                f"${monthly_profit:>5.2f} "
                f"{opp.consecutive_positive_hrs:>5d} "
                f"{oi_millions:>7.1f}M "
                f"{signal:>6}"
            )

        return "\n".join(lines)

    async def scan_full_universe(self) -> List[FundingOpportunity]:
        """Scan ALL coins for extreme funding rates (high APR outliers)."""
        if not config.FULL_UNIVERSE_SCAN:
            return []

        meta = await self.get_meta_and_contexts()
        if not meta or len(meta) < 2:
            return []

        universe = meta[0].get("universe", [])
        contexts = meta[1]
        now = time.time()
        outliers = []

        for i, asset in enumerate(universe):
            if i >= len(contexts):
                break
            coin = asset["name"]
            if coin in config.WATCH_COINS:
                continue  # Already in main scan

            ctx = contexts[i]
            rate = float(ctx.get("funding", "0"))
            mark = float(ctx.get("markPx", "0"))
            oi = float(ctx.get("openInterest", "0"))
            oi_usd = oi * mark

            if oi_usd < config.FULL_UNIVERSE_MIN_OI_USD:
                continue
            if rate <= 0:
                continue

            apr = rate * config.HOURS_PER_YEAR * 100
            if apr < config.FULL_UNIVERSE_MIN_APR:
                continue

            # This is an outlier worth tracking
            hold_hours = 720
            net_apr = ((rate * hold_hours - config.ROUND_TRIP_FEES) / (hold_hours / config.HOURS_PER_YEAR)) * 100

            opp = FundingOpportunity(
                coin=coin,
                current_rate=rate,
                avg_24h_rate=rate,  # No history fetched for full scan (too many coins)
                annualized_pct=apr,
                net_annualized_pct=net_apr,
                consecutive_positive_hrs=0,  # Unknown without history
                mark_price=mark,
                open_interest=oi,
                funding_velocity=0,
                timestamp=now,
            )
            outliers.append(opp)

            self._db.execute(
                "INSERT INTO funding_snapshots (coin, rate, avg_24h, annualized, net_annualized, consecutive_positive_hrs, mark_price, open_interest, timestamp) VALUES (?,?,?,?,?,?,?,?,?)",
                (coin, rate, rate, apr, net_apr, 0, mark, oi, now),
            )

        self._db.commit()
        outliers.sort(key=lambda x: x.annualized_pct, reverse=True)
        return outliers

    async def shutdown(self):
        if self._session:
            await self._session.close()
        if self._db:
            self._db.close()
