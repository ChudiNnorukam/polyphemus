#!/usr/bin/env python3
"""Cross-platform arbitrage scanner: Polymarket vs Kalshi.

Detects price divergences on equivalent markets across platforms.

Usage:
    python -m prediction_markets.arbitrage.scanner
    python -m prediction_markets.arbitrage.scanner --min-spread 0.02
"""
import asyncio
import argparse
import json
from datetime import datetime, timezone

import httpx

from .matcher import match_markets
from ..shared.fees import polymarket_fee, kalshi_taker_fee, arb_break_even_spread

GAMMA_BASE = "https://gamma-api.polymarket.com"
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

async def fetch_polymarket_markets(tag_slug: str = None, limit: int = 500) -> list:
    """Fetch active Polymarket markets."""
    async with httpx.AsyncClient(timeout=30) as client:
        all_markets = []
        offset = 0
        while offset < limit:
            params = {
                "active": "true",
                "closed": "false",
                "limit": min(100, limit - offset),
                "offset": offset,
            }
            if tag_slug:
                params["tag_slug"] = tag_slug

            resp = await client.get(f"{GAMMA_BASE}/markets", params=params)
            resp.raise_for_status()
            batch = resp.json()
            all_markets.extend(batch)
            if len(batch) < 100:
                break
            offset += 100
        return all_markets

async def fetch_kalshi_markets(limit: int = 2000) -> list:
    """Fetch active Kalshi markets (public, no auth)."""
    async with httpx.AsyncClient(timeout=30) as client:
        all_markets = []
        cursor = None
        while len(all_markets) < limit:
            params = {"status": "open", "limit": 1000}
            if cursor:
                params["cursor"] = cursor

            resp = await client.get(f"{KALSHI_BASE}/markets", params=params)
            resp.raise_for_status()
            data = resp.json()
            markets = data.get("markets", [])
            all_markets.extend(markets)
            cursor = data.get("cursor")
            if not cursor or not markets:
                break
        return all_markets

def compute_arb_opportunity(poly_market: dict, kalshi_market: dict) -> dict:
    """Compute arbitrage opportunity between matched markets.

    Two directions:
    1. Buy YES on Poly + Buy NO on Kalshi
    2. Buy NO on Poly + Buy YES on Kalshi

    Returns the better direction with net profit.
    """
    # Polymarket prices
    outcome_prices = json.loads(poly_market.get("outcomePrices", "[]"))
    if len(outcome_prices) < 2:
        return None
    poly_yes = float(outcome_prices[0])
    poly_no = float(outcome_prices[1])

    # Kalshi prices (bid/ask)
    kalshi_yes_bid = kalshi_market.get("yes_bid")
    kalshi_yes_ask = kalshi_market.get("yes_ask")
    kalshi_no_bid = kalshi_market.get("no_bid")
    kalshi_no_ask = kalshi_market.get("no_ask")

    # Handle missing prices
    if kalshi_yes_bid is None and kalshi_yes_ask is None:
        return None

    # Convert Kalshi prices (may be in cents or dollar strings)
    def parse_kalshi_price(p):
        if p is None:
            return None
        if isinstance(p, str):
            return float(p)
        if isinstance(p, (int, float)):
            return float(p) / 100 if p > 1 else float(p)
        return None

    ky_bid = parse_kalshi_price(kalshi_yes_bid)
    ky_ask = parse_kalshi_price(kalshi_yes_ask)

    opportunities = []

    # Direction 1: Buy YES on Poly (at poly_yes) + Buy NO on Kalshi (at 1-ky_bid if selling YES)
    if ky_bid and poly_yes:
        kalshi_no_price = 1 - ky_bid
        combined = poly_yes + kalshi_no_price

        # Fees
        poly_fee = polymarket_fee(poly_yes, "sports")
        kalshi_fee = kalshi_taker_fee(kalshi_no_price)

        net = 1.0 - combined - poly_fee - kalshi_fee

        if net > 0:
            opportunities.append({
                "direction": "BUY_YES_POLY + BUY_NO_KALSHI",
                "poly_price": poly_yes,
                "kalshi_price": kalshi_no_price,
                "combined_cost": round(combined, 4),
                "poly_fee": round(poly_fee, 4),
                "kalshi_fee": round(kalshi_fee, 4),
                "net_profit": round(net, 4),
                "net_pct": round(net / combined * 100, 2),
            })

    # Direction 2: Buy NO on Poly (at poly_no) + Buy YES on Kalshi (at ky_ask)
    if ky_ask and poly_no:
        combined = poly_no + ky_ask

        poly_fee = polymarket_fee(poly_no, "sports")
        kalshi_fee = kalshi_taker_fee(ky_ask)

        net = 1.0 - combined - poly_fee - kalshi_fee

        if net > 0:
            opportunities.append({
                "direction": "BUY_NO_POLY + BUY_YES_KALSHI",
                "poly_price": poly_no,
                "kalshi_price": ky_ask,
                "combined_cost": round(combined, 4),
                "poly_fee": round(poly_fee, 4),
                "kalshi_fee": round(kalshi_fee, 4),
                "net_profit": round(net, 4),
                "net_pct": round(net / combined * 100, 2),
            })

    if opportunities:
        return max(opportunities, key=lambda x: x["net_profit"])
    return None

async def run(min_spread: float = 0.01, min_similarity: float = 0.65):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"{'='*70}")
    print(f"CROSS-PLATFORM ARB SCANNER | {now}")
    print(f"Min spread: {min_spread} | Min match similarity: {min_similarity}")
    print(f"{'='*70}")

    # Fetch markets from both platforms
    print("\nFetching markets...")
    poly_markets, kalshi_markets = await asyncio.gather(
        fetch_polymarket_markets(limit=500),
        fetch_kalshi_markets(limit=2000),
    )
    print(f"  Polymarket: {len(poly_markets)} markets")
    print(f"  Kalshi: {len(kalshi_markets)} markets")

    # Match equivalent markets
    print("\nMatching equivalent markets...")
    matches = match_markets(poly_markets, kalshi_markets, min_similarity=min_similarity)
    print(f"  Found {len(matches)} matched pairs")

    # Compute arbitrage opportunities
    arb_opportunities = []
    for match in matches:
        opp = compute_arb_opportunity(match["poly_market"], match["kalshi_market"])
        if opp and opp["net_profit"] >= min_spread:
            opp["match_type"] = match["match_type"]
            opp["confidence"] = match["confidence"]
            opp["resolution_risk"] = match["resolution_risk"]
            opp["poly_title"] = match["poly_market"].get("question", "")[:60]
            opp["kalshi_title"] = match["kalshi_market"].get("title", "")[:60]
            arb_opportunities.append(opp)

    arb_opportunities.sort(key=lambda x: x["net_profit"], reverse=True)

    if not arb_opportunities:
        print(f"\nNo arbitrage opportunities above {min_spread} spread found.")
        print("This is expected - most opportunities last 2-3 seconds.")
        return

    print(f"\n{'='*70}")
    print(f"ARBITRAGE OPPORTUNITIES ({len(arb_opportunities)} found)")
    print(f"{'='*70}")

    for opp in arb_opportunities[:15]:
        print(f"\n  {opp['direction']}")
        print(f"    Poly: {opp['poly_title']}")
        print(f"    Kalshi: {opp['kalshi_title']}")
        print(f"    Match: {opp['match_type']} (confidence: {opp['confidence']:.0%})")
        print(f"    Combined: ${opp['combined_cost']:.4f} | Fees: ${opp['poly_fee'] + opp['kalshi_fee']:.4f}")
        print(f"    Net profit: ${opp['net_profit']:.4f} ({opp['net_pct']:.2f}%)")
        print(f"    Resolution risk: {opp['resolution_risk']}")

def main():
    parser = argparse.ArgumentParser(description="Cross-platform arbitrage scanner")
    parser.add_argument("--min-spread", type=float, default=0.01, help="Min net profit per contract")
    parser.add_argument("--min-similarity", type=float, default=0.65, help="Min text match similarity")
    args = parser.parse_args()
    asyncio.run(run(min_spread=args.min_spread, min_similarity=args.min_similarity))

if __name__ == "__main__":
    main()
