#!/usr/bin/env python3
"""Kalshi market scanner - discovers and categorizes available markets.

Usage:
    python -m prediction_markets.kalshi.scanner
    python -m prediction_markets.kalshi.scanner --category sports
"""
import asyncio
import argparse
from collections import Counter

from .client import KalshiClient

CATEGORY_KEYWORDS = {
    "sports": ["nfl", "nba", "mlb", "nhl", "mls", "ufc", "tennis", "golf", "game", "match", "score"],
    "crypto": ["bitcoin", "btc", "ethereum", "eth", "crypto", "price above", "price below"],
    "politics": ["election", "president", "congress", "senate", "governor", "vote", "nominee"],
    "economics": ["cpi", "gdp", "unemployment", "fed", "fomc", "rate", "inflation", "jobs"],
    "weather": ["temperature", "hurricane", "storm", "weather", "rainfall", "snowfall"],
}


def categorize_market(market: dict) -> str:
    """Categorize a market based on title keywords."""
    title = (market.get("title") or "").lower()
    ticker = (market.get("ticker") or "").lower()
    combined = f"{title} {ticker}"

    scores = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in combined)
        if score > 0:
            scores[category] = score

    if scores:
        return max(scores, key=scores.get)
    return "other"


async def scan_markets(category_filter: str = None):
    """Scan all open Kalshi markets and report summary."""
    async with KalshiClient(demo=False) as client:
        print("Fetching all open markets...")
        markets = await client.get_all_markets(status="open")
        print(f"  Found {len(markets)} open markets")

        # Categorize
        categorized = {}
        for m in markets:
            cat = categorize_market(m)
            if cat not in categorized:
                categorized[cat] = []
            categorized[cat].append(m)

        print(f"\nCategories:")
        for cat, ms in sorted(categorized.items(), key=lambda x: -len(x[1])):
            print(f"  {cat}: {len(ms)} markets")

        # Filter if requested
        display_markets = markets
        if category_filter:
            display_markets = categorized.get(category_filter, [])
            print(f"\nFiltered to {category_filter}: {len(display_markets)} markets")

        # Show top markets by volume
        display_markets.sort(key=lambda m: m.get("volume", 0) or 0, reverse=True)

        print(f"\nTop 20 markets by volume:")
        print(f"{'Ticker':<35} {'Title':<50} {'Yes Bid':>8} {'Yes Ask':>8} {'Vol':>8}")
        print("-" * 115)

        for m in display_markets[:20]:
            ticker = m.get("ticker", "")[:34]
            title = m.get("title", "")[:49]
            yes_bid = m.get("yes_bid", "")
            yes_ask = m.get("yes_ask", "")
            vol = m.get("volume", 0)
            print(f"{ticker:<35} {title:<50} {yes_bid:>8} {yes_ask:>8} {vol:>8}")


def main():
    parser = argparse.ArgumentParser(description="Kalshi market scanner")
    parser.add_argument("--category", type=str, help="Filter by category")
    args = parser.parse_args()
    asyncio.run(scan_markets(category_filter=args.category))


if __name__ == "__main__":
    main()
