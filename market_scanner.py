#!/usr/bin/env python3
"""
market_scanner.py - Multi-category market scanner for signal detection
Expands from crypto-only to all Polymarket categories
"""
import os
import asyncio
import aiohttp
import json
import logging
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Set, Tuple
from datetime import datetime, timedelta
from enum import Enum

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Categories to scan (configurable via env)
DEFAULT_CATEGORIES = "crypto,politics,sports,entertainment,finance"
ENABLED_CATEGORIES = set(os.getenv("ENABLED_CATEGORIES", DEFAULT_CATEGORIES).split(","))

# Polymarket APIs
MARKETS_API = "https://gamma-api.polymarket.com/markets"
EVENTS_API = "https://gamma-api.polymarket.com/events"

# Market quality thresholds
MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "1000"))  # USDC
MIN_VOLUME_24H = float(os.getenv("MIN_VOLUME_24H", "500"))  # USDC
MIN_QUALITY_SCORE = float(os.getenv("MIN_QUALITY_SCORE", "0.5"))  # 0-1

# Time windows for scoring
QUALITY_OPTIMAL_DAYS = 7  # Markets ~7 days from resolution score best
QUALITY_MIN_DAYS = 0.5  # Don't trade if <12 hours to resolution
QUALITY_MAX_DAYS = 180  # Don't trade if >6 months to resolution


class MarketCategory(Enum):
    """Market categories on Polymarket"""
    CRYPTO = "crypto"
    POLITICS = "politics"
    SPORTS = "sports"
    ENTERTAINMENT = "entertainment"
    FINANCE = "finance"
    OTHER = "other"


@dataclass
class MarketInfo:
    """Market information with trading metrics"""
    condition_id: str
    question: str
    category: str
    volume_24h: float
    liquidity: float
    end_date: datetime
    slug: str
    tokens: List[dict]
    quality_score: float  # 0-1 based on liquidity, volume, time to resolution
    created_at: datetime

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        data = asdict(self)
        data['end_date'] = self.end_date.isoformat()
        data['created_at'] = self.created_at.isoformat()
        return data


class MarketScanner:
    """Scanner for Polymarket markets across multiple categories"""

    def __init__(self, session: Optional[aiohttp.ClientSession] = None):
        self.session = session
        self.cached_markets: Dict[str, MarketInfo] = {}
        self.cache_ttl = 300  # 5 minutes
        self.last_scan: Optional[datetime] = None
        self.own_session = False

    async def __aenter__(self):
        if not self.session:
            self.session = aiohttp.ClientSession()
            self.own_session = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.own_session and self.session:
            await self.session.close()

    async def scan_markets(
        self,
        categories: Optional[List[str]] = None,
        force_refresh: bool = False
    ) -> List[MarketInfo]:
        """
        Scan markets across multiple categories.

        Args:
            categories: List of categories to scan. If None, uses ENABLED_CATEGORIES
            force_refresh: Force cache refresh

        Returns:
            List of MarketInfo objects sorted by quality score
        """
        if not self.session:
            raise RuntimeError("Scanner must be used with 'async with' context or session provided")

        # Check cache
        if not force_refresh and self.last_scan:
            age = (datetime.utcnow() - self.last_scan).total_seconds()
            if age < self.cache_ttl and self.cached_markets:
                logger.info(f"Using cached markets ({len(self.cached_markets)} markets)")
                return sorted(
                    self.cached_markets.values(),
                    key=lambda m: m.quality_score,
                    reverse=True
                )

        categories = categories or list(ENABLED_CATEGORIES)
        markets = []

        try:
            logger.info(f"Scanning markets in categories: {categories}")

            # Fetch all active markets
            async with self.session.get(MARKETS_API, params={"limit": 10000}) as resp:
                if resp.status != 200:
                    logger.error(f"Failed to fetch markets: {resp.status}")
                    return []

                data = await resp.json()
                all_markets = data.get("data", []) if isinstance(data, dict) else data

            logger.info(f"Fetched {len(all_markets)} total markets from API")

            # Process markets
            for market in all_markets:
                try:
                    processed = self._process_market(market, categories)
                    if processed:
                        markets.append(processed)
                except Exception as e:
                    logger.debug(f"Error processing market: {e}")
                    continue

            # Cache results
            self.cached_markets = {m.condition_id: m for m in markets}
            self.last_scan = datetime.utcnow()

            # Sort by quality score
            markets = sorted(markets, key=lambda m: m.quality_score, reverse=True)
            logger.info(f"Found {len(markets)} tradeable markets")

            return markets

        except Exception as e:
            logger.error(f"Error scanning markets: {e}")
            return list(self.cached_markets.values())

    def _process_market(self, market: dict, enabled_categories: List[str]) -> Optional[MarketInfo]:
        """
        Process a market from API response.

        Args:
            market: Raw market data from API
            enabled_categories: List of enabled category names

        Returns:
            MarketInfo if market meets criteria, None otherwise
        """
        try:
            # Extract basic fields
            condition_id = market.get("condition_id") or market.get("id")
            if not condition_id:
                return None

            question = market.get("question", "")
            slug = market.get("slug", "")

            # Extract volume and liquidity
            volume_24h = float(market.get("volume24h", 0) or market.get("volume_24h", 0) or 0)

            # Calculate liquidity from orderbook
            liquidity = self._calculate_liquidity(market)

            # Parse end date
            end_date_str = market.get("endDate") or market.get("end_date")
            if not end_date_str:
                return None

            try:
                if isinstance(end_date_str, str):
                    # Try ISO format first
                    end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                else:
                    end_date = datetime.fromtimestamp(end_date_str)
            except:
                return None

            # Parse creation date
            created_str = market.get("createdAt") or market.get("created_at")
            if created_str:
                try:
                    if isinstance(created_str, str):
                        created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                    else:
                        created_at = datetime.fromtimestamp(created_str)
                except:
                    created_at = datetime.utcnow()
            else:
                created_at = datetime.utcnow()

            # Determine category
            category = self._categorize_market(market, question)

            # Filter by enabled categories
            if category not in enabled_categories:
                return None

            # Extract tokens
            tokens = market.get("outcomes", []) or market.get("tokens", [])

            # Check trading criteria
            if not self.is_market_tradeable(volume_24h, liquidity, end_date):
                return None

            # Calculate quality score
            quality_score = self.calculate_quality_score(
                volume_24h=volume_24h,
                liquidity=liquidity,
                end_date=end_date
            )

            # Only include if quality score meets minimum
            if quality_score < MIN_QUALITY_SCORE:
                return None

            return MarketInfo(
                condition_id=condition_id,
                question=question,
                category=category,
                volume_24h=volume_24h,
                liquidity=liquidity,
                end_date=end_date,
                slug=slug,
                tokens=tokens,
                quality_score=quality_score,
                created_at=created_at
            )

        except Exception as e:
            logger.debug(f"Error processing market {market.get('id', 'unknown')}: {e}")
            return None

    def _categorize_market(self, market: dict, question: str) -> str:
        """
        Categorize market based on tags, topic, or question content.

        Args:
            market: Market data
            question: Market question text

        Returns:
            Category name
        """
        # Check tags first
        tags = set()
        if "tags" in market and market["tags"]:
            tags = set(t.lower() for t in market["tags"])

        # Check category field
        market_category = (market.get("category") or "").lower()
        topic = (market.get("topic") or "").lower()

        all_text = f"{question} {market_category} {topic}".lower()

        # Crypto keywords
        if any(kw in tags or kw in all_text for kw in ["crypto", "bitcoin", "ethereum", "ethereum", "web3", "defi", "blockchain", "nft"]):
            return MarketCategory.CRYPTO.value

        # Politics keywords
        if any(kw in tags or kw in all_text for kw in ["politics", "election", "congress", "senate", "presidential", "voting", "government"]):
            return MarketCategory.POLITICS.value

        # Sports keywords
        if any(kw in tags or kw in all_text for kw in ["sports", "nfl", "nba", "mlb", "nhl", "soccer", "football", "basketball", "baseball", "hockey"]):
            return MarketCategory.SPORTS.value

        # Entertainment keywords
        if any(kw in tags or kw in all_text for kw in ["entertainment", "oscars", "awards", "movie", "tv", "celebrity", "music", "grammy", "emmy"]):
            return MarketCategory.ENTERTAINMENT.value

        # Finance keywords
        if any(kw in tags or kw in all_text for kw in ["finance", "stock", "market", "fed", "interest", "inflation", "gdp", "unemployment"]):
            return MarketCategory.FINANCE.value

        return MarketCategory.OTHER.value

    def _calculate_liquidity(self, market: dict) -> float:
        """
        Calculate available liquidity from market data.

        Args:
            market: Market data

        Returns:
            Estimated liquidity in USDC
        """
        # Try different liquidity sources
        if "liquidity" in market:
            try:
                return float(market["liquidity"])
            except:
                pass

        # Try orderbook
        orderbook = market.get("orderbook", {})
        if orderbook:
            buy_side = sum(float(order.get("amount", 0)) for order in orderbook.get("bids", []))
            sell_side = sum(float(order.get("amount", 0)) for order in orderbook.get("asks", []))
            if buy_side or sell_side:
                return max(buy_side, sell_side)

        # Estimate from volume
        volume_24h = float(market.get("volume24h", 0) or 0)
        if volume_24h > 0:
            # Liquidity ≈ 5-10% of daily volume
            return volume_24h * 0.075

        return 0.0

    def calculate_quality_score(
        self,
        volume_24h: float,
        liquidity: float,
        end_date: datetime
    ) -> float:
        """
        Score market quality based on liquidity, volume, time to resolution.

        Higher scores = better markets for trading.

        Args:
            volume_24h: 24h trading volume in USDC
            liquidity: Available liquidity in USDC
            end_date: Market resolution date

        Returns:
            Score from 0-1
        """
        now = datetime.utcnow()
        days_to_resolution = (end_date - now).total_seconds() / 86400

        # Check hard limits
        if days_to_resolution < QUALITY_MIN_DAYS or days_to_resolution > QUALITY_MAX_DAYS:
            return 0.0

        # Scoring components (0-1 each)

        # 1. Liquidity score (higher is better, but with diminishing returns)
        # Good liquidity is 10k+ USDC
        liquidity_score = min(liquidity / 10000, 1.0) if liquidity > 0 else 0

        # 2. Volume score (higher is better)
        # Good volume is 5k+ USDC in 24h
        volume_score = min(volume_24h / 5000, 1.0) if volume_24h > 0 else 0

        # 3. Time to resolution score (bell curve, optimal around 7 days)
        # Markets very close to resolution have low signal quality
        # Markets far in future have low volume
        if days_to_resolution <= 0:
            time_score = 0.0
        elif days_to_resolution < 1:
            # Less than 1 day: ramping up from 0
            time_score = days_to_resolution / 0.5
        elif days_to_resolution <= QUALITY_OPTIMAL_DAYS:
            # 1 day to 7 days: optimal zone
            time_score = 1.0
        else:
            # Beyond 7 days: declining
            days_beyond = days_to_resolution - QUALITY_OPTIMAL_DAYS
            # Decay to 0 over remaining time window
            remaining_window = QUALITY_MAX_DAYS - QUALITY_OPTIMAL_DAYS
            time_score = max(0, 1.0 - (days_beyond / remaining_window))

        # Combined score with weights
        # Liquidity and volume are equally important (40% each)
        # Time is critical (20%)
        combined = (liquidity_score * 0.4) + (volume_score * 0.4) + (time_score * 0.2)

        return min(1.0, max(0.0, combined))

    def is_market_tradeable(
        self,
        volume_24h: float,
        liquidity: float,
        end_date: datetime
    ) -> bool:
        """
        Check if market meets minimum trading criteria.

        Args:
            volume_24h: 24h trading volume
            liquidity: Available liquidity
            end_date: Market resolution date

        Returns:
            True if market is tradeable
        """
        # Check minimum liquidity
        if liquidity < MIN_LIQUIDITY:
            return False

        # Check minimum volume
        if volume_24h < MIN_VOLUME_24H:
            return False

        # Check time to resolution
        days_to_resolution = (end_date - datetime.utcnow()).total_seconds() / 86400
        if days_to_resolution < QUALITY_MIN_DAYS or days_to_resolution > QUALITY_MAX_DAYS:
            return False

        return True

    def get_market_by_id(self, condition_id: str) -> Optional[MarketInfo]:
        """
        Get a specific market from cache by condition ID.

        Args:
            condition_id: Polymarket condition ID

        Returns:
            MarketInfo or None if not found
        """
        return self.cached_markets.get(condition_id)

    def get_markets_by_category(self, category: str) -> List[MarketInfo]:
        """
        Get all markets in a category from cache.

        Args:
            category: Category name

        Returns:
            List of MarketInfo objects
        """
        return [
            m for m in self.cached_markets.values()
            if m.category == category
        ]

    def export_markets_json(self, markets: Optional[List[MarketInfo]] = None, filepath: Optional[str] = None) -> str:
        """
        Export markets to JSON format.

        Args:
            markets: List of markets to export. If None, uses cached markets.
            filepath: Optional file path to write JSON. If None, returns string.

        Returns:
            JSON string
        """
        markets = markets or list(self.cached_markets.values())
        data = {
            "timestamp": datetime.utcnow().isoformat(),
            "count": len(markets),
            "markets": [m.to_dict() for m in markets]
        }

        json_str = json.dumps(data, indent=2)

        if filepath:
            with open(filepath, 'w') as f:
                f.write(json_str)
            logger.info(f"Exported {len(markets)} markets to {filepath}")

        return json_str


# Export convenience function for signal_tracker integration
async def scan_markets(
    categories: Optional[List[str]] = None,
    session: Optional[aiohttp.ClientSession] = None,
    min_quality_score: Optional[float] = None
) -> List[MarketInfo]:
    """
    Convenience function for scanning markets.

    Can be called from signal_tracker to get all active markets.

    Args:
        categories: Categories to scan. If None, uses ENABLED_CATEGORIES
        session: Optional aiohttp session. Creates one if not provided.
        min_quality_score: Override minimum quality score threshold

    Returns:
        List of MarketInfo objects sorted by quality
    """
    async with MarketScanner(session) as scanner:
        markets = await scanner.scan_markets(categories=categories, force_refresh=False)

        if min_quality_score is not None:
            markets = [m for m in markets if m.quality_score >= min_quality_score]

        return markets


# CLI interface for testing
async def main():
    """Test the market scanner"""
    import sys

    # Parse command line args
    categories = None
    if len(sys.argv) > 1:
        categories = sys.argv[1].split(",")

    async with MarketScanner() as scanner:
        print("Starting market scan...")
        markets = await scanner.scan_markets(categories=categories)

        print(f"\nFound {len(markets)} markets")

        # Group by category
        by_category = {}
        for market in markets:
            if market.category not in by_category:
                by_category[market.category] = []
            by_category[market.category].append(market)

        print("\nMarkets by category:")
        for cat in sorted(by_category.keys()):
            markets_in_cat = by_category[cat]
            print(f"\n{cat.upper()}: {len(markets_in_cat)} markets")

            # Show top 3 by quality
            top_3 = sorted(markets_in_cat, key=lambda m: m.quality_score, reverse=True)[:3]
            for i, market in enumerate(top_3, 1):
                days_to_end = (market.end_date - datetime.utcnow()).total_seconds() / 86400
                print(f"  {i}. {market.question[:60]}...")
                print(f"     Quality: {market.quality_score:.2f} | Volume: ${market.volume_24h:.0f} | Liquidity: ${market.liquidity:.0f}")
                print(f"     Resolution: {days_to_end:.1f} days")

        # Export to JSON
        scanner.export_markets_json(filepath="markets_scan.json")
        print("\nExported to markets_scan.json")


if __name__ == "__main__":
    asyncio.run(main())
