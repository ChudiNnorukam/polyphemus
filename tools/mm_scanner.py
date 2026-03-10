#!/usr/bin/env python3
"""Market-Making Opportunity Scanner for Polymarket.

Scans active markets for wide spreads suitable for market-making.
Uses Gamma API data (bestBid, bestAsk, spread fields) for fast scanning.

Usage:
    python tools/mm_scanner.py                  # scan all active markets
    python tools/mm_scanner.py --min-spread 3   # only show 3+ cent spreads
    python tools/mm_scanner.py --new            # only new markets
    python tools/mm_scanner.py --limit 200      # scan 200 markets
    python tools/mm_scanner.py --arb            # scan for complete-set arbitrage
"""

import argparse
import json
import time
from datetime import datetime, timezone

import requests

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"


def fetch_markets(limit: int = 100, new_only: bool = False, offset: int = 0) -> list:
    """Fetch active markets from Gamma API with spread data."""
    params = {
        "active": "true",
        "closed": "false",
        "limit": str(limit),
        "offset": str(offset),
        "order": "volume24hrClob",
        "ascending": "false",
    }
    if new_only:
        params["new"] = "true"

    resp = requests.get(f"{GAMMA_API}/markets", params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def scan_spreads(markets: list, min_spread_cents: float = 2.0) -> list:
    """Scan markets for wide spreads using Gamma API data."""
    opportunities = []

    for m in markets:
        if not m.get("acceptingOrders"):
            continue

        spread_raw = m.get("spread")
        if spread_raw is None:
            continue

        spread = float(spread_raw)
        spread_cents = spread * 100

        if spread_cents < min_spread_cents:
            continue

        best_bid = float(m.get("bestBid") or 0)
        best_ask = float(m.get("bestAsk") or 0)
        if best_bid <= 0:
            continue

        midpoint = (best_bid + best_ask) / 2
        question = m.get("question", "")[:65]
        volume_24h = float(m.get("volume24hrClob") or 0)
        liquidity = float(m.get("liquidityClob") or m.get("liquidity") or 0)
        is_new = m.get("new", False)

        # Parse outcomes for context
        outcomes_raw = m.get("outcomes", "[]")
        try:
            outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
        except json.JSONDecodeError:
            outcomes = []

        prices_raw = m.get("outcomePrices", "[]")
        try:
            prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
        except json.JSONDecodeError:
            prices = []

        opportunities.append({
            "question": question,
            "spread_cents": round(spread_cents, 1),
            "midpoint": round(midpoint, 3),
            "best_bid": round(best_bid, 3),
            "best_ask": round(best_ask, 3),
            "volume_24h": round(volume_24h, 0),
            "liquidity": round(liquidity, 0),
            "new": is_new,
            "slug": m.get("slug", ""),
            "outcomes": outcomes,
            "prices": [round(float(p), 3) for p in prices] if prices else [],
            "fee_type": m.get("feeType", ""),
        })

    opportunities.sort(key=lambda x: x["spread_cents"], reverse=True)
    return opportunities


def scan_arb(markets: list) -> list:
    """Scan for complete-set arbitrage (multi-outcome markets where sum < $1.00)."""
    # Group markets by event
    events = {}
    for m in markets:
        event_list = m.get("events", [])
        if not event_list:
            continue
        event = event_list[0] if isinstance(event_list, list) else event_list
        event_slug = event.get("slug", "") if isinstance(event, dict) else ""
        if not event_slug:
            continue

        prices_raw = m.get("outcomePrices", "[]")
        try:
            prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
        except json.JSONDecodeError:
            continue

        if not prices or len(prices) < 1:
            continue

        # For binary markets, YES price is the relevant one
        yes_price = float(prices[0])

        if event_slug not in events:
            events[event_slug] = {
                "event_slug": event_slug,
                "event_title": event.get("title", "")[:60] if isinstance(event, dict) else "",
                "markets": [],
            }

        events[event_slug]["markets"].append({
            "question": m.get("question", "")[:50],
            "yes_price": yes_price,
            "slug": m.get("slug", ""),
            "volume_24h": float(m.get("volume24hrClob") or 0),
        })

    # Find events where sum of YES prices < 1.00 (complete-set arb)
    arb_opportunities = []
    for event_slug, data in events.items():
        if len(data["markets"]) < 3:  # need 3+ outcomes for arb
            continue

        total_cost = sum(m["yes_price"] for m in data["markets"])
        if 0 < total_cost < 1.0:
            profit_pct = ((1.0 - total_cost) / total_cost) * 100
            arb_opportunities.append({
                "event": data["event_title"],
                "event_slug": event_slug,
                "num_outcomes": len(data["markets"]),
                "total_cost": round(total_cost, 4),
                "profit_per_share": round(1.0 - total_cost, 4),
                "profit_pct": round(profit_pct, 2),
                "markets": data["markets"],
            })

    arb_opportunities.sort(key=lambda x: x["profit_pct"], reverse=True)
    return arb_opportunities


def print_spread_table(opps: list):
    """Print spread opportunities."""
    if not opps:
        print("No spread opportunities found.")
        return

    print(f"\n{'='*105}")
    print(f"  MM SPREAD OPPORTUNITIES ({len(opps)} found)")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*105}")
    print(f" {'Spread':>6} {'Mid':>6} {'Bid':>6} {'Ask':>6} {'Vol24h':>9} {'Liq':>8} {'New':>3}  Question")
    print(f" {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*9} {'-'*8} {'-'*3}  {'-'*50}")

    for o in opps[:30]:
        vol_str = f"${o['volume_24h']:,.0f}" if o['volume_24h'] else "$0"
        liq_str = f"${o['liquidity']:,.0f}" if o['liquidity'] else "$0"
        new_flag = " Y" if o['new'] else "  "
        print(
            f" {o['spread_cents']:>5.1f}c "
            f"${o['midpoint']:.2f} "
            f"${o['best_bid']:.2f} "
            f"${o['best_ask']:.2f} "
            f"{vol_str:>9} "
            f"{liq_str:>8} "
            f"{new_flag}  "
            f"{o['question']}"
        )

    if opps:
        print(f"\n  Top 3 opportunities for $597 capital:")
        for i, o in enumerate(opps[:3]):
            shares = min(100, int(597 * 0.1 / o['midpoint'])) if o['midpoint'] > 0 else 0
            capture = o['spread_cents'] - 1  # assume 1c positioning cost
            profit = shares * capture / 100
            print(f"  {i+1}. \"{o['question'][:40]}\" — {o['spread_cents']}c spread, ~{shares} shares, ~${profit:.2f} capture")


def print_arb_table(arbs: list):
    """Print complete-set arbitrage opportunities."""
    if not arbs:
        print("\nNo complete-set arbitrage opportunities found (sum of all YES prices >= $1.00).")
        return

    print(f"\n{'='*90}")
    print(f"  COMPLETE-SET ARBITRAGE ({len(arbs)} found)")
    print(f"  Buy all outcomes → guaranteed $1.00 at settlement")
    print(f"{'='*90}")

    for a in arbs[:10]:
        print(f"\n  Event: {a['event']}")
        print(f"  Outcomes: {a['num_outcomes']} | Cost: ${a['total_cost']:.4f} | Profit: ${a['profit_per_share']:.4f}/share ({a['profit_pct']:.2f}%)")
        for mk in a['markets'][:6]:
            print(f"    ${mk['yes_price']:.3f}  {mk['question']}")
        if len(a['markets']) > 6:
            print(f"    ... and {len(a['markets'])-6} more")

        # Calculate with $97 allocation
        shares = int(97 / a['total_cost'])
        profit = shares * a['profit_per_share']
        print(f"  At $97: buy {shares} complete sets → ${profit:.2f} profit ({a['profit_pct']:.2f}%)")


def main():
    parser = argparse.ArgumentParser(description="Polymarket MM & Arb Scanner")
    parser.add_argument("--min-spread", type=float, default=2.0, help="Min spread in cents (default: 2)")
    parser.add_argument("--new", action="store_true", help="Only show new markets")
    parser.add_argument("--limit", type=int, default=100, help="Max markets to fetch (default: 100)")
    parser.add_argument("--arb", action="store_true", help="Scan for complete-set arbitrage")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--all", action="store_true", help="Run both spread and arb scans")
    args = parser.parse_args()

    print(f"Fetching markets from Polymarket (limit={args.limit})...")
    markets = fetch_markets(limit=args.limit, new_only=args.new)
    print(f"  Got {len(markets)} markets")

    if args.arb or args.all:
        arbs = scan_arb(markets)
        if args.json:
            print(json.dumps(arbs, indent=2))
        else:
            print_arb_table(arbs)

    if not args.arb or args.all:
        opps = scan_spreads(markets, min_spread_cents=args.min_spread)
        if args.json:
            print(json.dumps(opps, indent=2))
        else:
            print_spread_table(opps)


if __name__ == "__main__":
    main()
