#!/usr/bin/env python3
"""Resolution Sniper — Buy near-certain markets at $0.90-0.98 for guaranteed returns.

Scans Polymarket for markets where one outcome is trading at high probability,
verifies with external data where possible, and places maker orders on the
best risk-adjusted opportunities.

Primary targets:
  1. Crypto price thresholds — auto-verified via exchange prices
  2. Short-term markets — resolving within 7 days with extreme probability
  3. High-volume long-shots — crowd consensus + time value

Usage:
    python3 resolution_sniper.py scan          # Show opportunities
    python3 resolution_sniper.py trade         # Scan + place orders
    python3 resolution_sniper.py history       # Show trade history
    python3 resolution_sniper.py pnl           # P&L summary
    python3 resolution_sniper.py resolve       # Resolve completed trades
"""

import argparse
import json
import math
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta

import requests
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    OrderArgs,
    BalanceAllowanceParams,
    AssetType,
    OrderType,
)
from py_clob_client.order_builder.constants import BUY

# --- Config ---

CLOB_HOST = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CHAIN_ID = 137

# Snipe zone: buy the near-certain side
MIN_SNIPE_PRICE = float(os.environ.get("SNIPE_MIN_PRICE", "0.90"))
MAX_SNIPE_PRICE = float(os.environ.get("SNIPE_MAX_PRICE", "0.97"))
MIN_VOLUME = float(os.environ.get("SNIPE_MIN_VOLUME", "5000"))
MIN_ANNUALIZED_RETURN = float(os.environ.get("SNIPE_MIN_ANNUAL_RETURN", "0.15"))  # 15%

# Risk params
KELLY_FRACTION = float(os.environ.get("SNIPE_KELLY_FRACTION", "0.15"))  # conservative
MAX_POSITION_PCT = float(os.environ.get("SNIPE_MAX_POSITION_PCT", "0.05"))
MAX_TOTAL_EXPOSURE_PCT = float(os.environ.get("SNIPE_MAX_EXPOSURE_PCT", "0.25"))
MIN_ORDER_SIZE = 5
MAKER_OFFSET = float(os.environ.get("SNIPE_MAKER_OFFSET", "0.003"))

# Crypto verification — daily volatility estimates (annualized_vol / sqrt(365))
CRYPTO_DAILY_VOL = {
    "bitcoin": 0.031,   # ~57% annualized
    "ethereum": 0.042,  # ~80% annualized
    "solana": 0.055,    # ~105% annualized
    "xrp": 0.050,       # ~95% annualized
    "dogecoin": 0.060,  # ~115% annualized
}

# Market scan pages
MAX_SCAN_PAGES = int(os.environ.get("SNIPE_MAX_PAGES", "10"))
PAGE_SIZE = 100

# DB
DB_PATH = os.environ.get("SNIPE_DB_PATH",
    os.path.join(os.path.dirname(__file__), "snipe_trades.db"))


# --- CLOB Client ---

def _load_env_file():
    for path in ["/opt/openclaw/.env", "/opt/lagbot/lagbot/.env"]:
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, val = line.partition("=")
                        os.environ.setdefault(key.strip(), val.strip())


def _env(polymarket_name, lagbot_name, default=""):
    return os.environ.get(polymarket_name) or os.environ.get(lagbot_name) or default


def get_client():
    _load_env_file()
    private_key = _env("POLYMARKET_PRIVATE_KEY", "PRIVATE_KEY")
    sig_type = int(_env("POLYMARKET_SIGNATURE_TYPE", "SIGNATURE_TYPE", "0"))
    if not private_key:
        print("ERROR: PRIVATE_KEY not set")
        sys.exit(1)
    client = ClobClient(CLOB_HOST, key=private_key, chain_id=CHAIN_ID,
                        signature_type=sig_type)
    client.set_api_creds(client.create_or_derive_api_creds())
    return client


def get_balance():
    client = get_client()
    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    result = client.get_balance_allowance(params)
    return float(result.get("balance", 0)) / 1e6


# --- Database ---

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS snipe_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        question TEXT NOT NULL,
        slug TEXT NOT NULL,
        side TEXT NOT NULL,
        category TEXT NOT NULL,
        market_price REAL NOT NULL,
        order_price REAL NOT NULL,
        shares REAL NOT NULL,
        cost REAL NOT NULL,
        potential_profit REAL NOT NULL,
        annualized_return REAL NOT NULL,
        days_to_resolution REAL NOT NULL,
        token_id TEXT NOT NULL,
        order_id TEXT,
        status TEXT DEFAULT 'placed',
        pnl REAL DEFAULT 0,
        end_date TEXT NOT NULL,
        verified_by TEXT DEFAULT '',
        confidence REAL DEFAULT 0
    )""")
    conn.commit()
    return conn


def record_trade(conn, **kwargs):
    conn.execute("""INSERT INTO snipe_trades
        (ts, question, slug, side, category, market_price, order_price,
         shares, cost, potential_profit, annualized_return, days_to_resolution,
         token_id, order_id, end_date, verified_by, confidence)
        VALUES (:ts, :question, :slug, :side, :category, :market_price,
         :order_price, :shares, :cost, :potential_profit, :annualized_return,
         :days_to_resolution, :token_id, :order_id, :end_date, :verified_by,
         :confidence)""", kwargs)
    conn.commit()


# --- Crypto Price Verification ---

def get_crypto_prices():
    """Get current prices from CoinGecko."""
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": "bitcoin,ethereum,solana,ripple,dogecoin",
                "vs_currencies": "usd",
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        return {
            "bitcoin": data.get("bitcoin", {}).get("usd"),
            "ethereum": data.get("ethereum", {}).get("usd"),
            "solana": data.get("solana", {}).get("usd"),
            "xrp": data.get("ripple", {}).get("usd"),
            "dogecoin": data.get("dogecoin", {}).get("usd"),
        }
    except Exception as e:
        print(f"  CoinGecko error: {e}")
        # Fallback to Binance
        try:
            prices = {}
            for symbol, name in [("BTCUSDT", "bitcoin"), ("ETHUSDT", "ethereum"),
                                  ("SOLUSDT", "solana"), ("XRPUSDT", "xrp")]:
                r = requests.get(f"https://api.binance.com/api/v3/ticker/price",
                                 params={"symbol": symbol}, timeout=10)
                prices[name] = float(r.json()["price"])
            return prices
        except Exception as e2:
            print(f"  Binance fallback error: {e2}")
            return {}


def parse_crypto_market(question):
    """Parse crypto price threshold from market question.

    Returns (asset, threshold, direction) or (None, None, None).
    Examples:
        "Will the price of Bitcoin be above $100,000 on Feb 28?" → ("bitcoin", 100000, "above")
        "Will Bitcoin be less than $60,000 on Feb 17?" → ("bitcoin", 60000, "below")
        "Will ETH hit $5,000 before March?" → ("ethereum", 5000, "above")
    """
    q = question.lower()

    # Asset detection
    asset = None
    for name, keywords in [
        ("bitcoin", ["bitcoin", " btc "]),
        ("ethereum", ["ethereum", " eth "]),
        ("solana", ["solana", " sol "]),
        ("xrp", [" xrp ", "ripple"]),
        ("dogecoin", ["dogecoin", " doge "]),
    ]:
        if any(kw in f" {q} " for kw in keywords):
            asset = name
            break

    if not asset:
        return None, None, None

    # Price threshold detection
    # Match patterns like $100,000 or $60000 or $5,000.50
    price_match = re.search(r'\$[\d,]+(?:\.\d+)?', q)
    if not price_match:
        return None, None, None
    threshold = float(price_match.group().replace("$", "").replace(",", ""))

    # Direction detection
    if any(w in q for w in ["above", "more than", "higher than", "over", "exceed",
                             "hit", "reach", "surpass"]):
        direction = "above"
    elif any(w in q for w in ["below", "less than", "lower than", "under", "drop"]):
        direction = "below"
    else:
        direction = "above"  # default for "Will BTC be at $X?"

    return asset, threshold, direction


def crypto_snipe_probability(asset, threshold, direction, current_price, days):
    """Estimate probability that crypto stays above/below threshold.

    Uses log-normal model with estimated daily volatility.
    """
    if current_price is None or current_price <= 0 or days <= 0:
        return None

    daily_vol = CRYPTO_DAILY_VOL.get(asset, 0.04)
    period_vol = daily_vol * math.sqrt(days)

    if direction == "above":
        if current_price <= threshold:
            return None  # currently below — not a snipe
        margin = math.log(current_price / threshold)
        # Probability of NOT dropping below threshold
        # Using log-normal: P(S_T > K) = Φ((ln(S/K) + (r - σ²/2)T) / (σ√T))
        # Simplified with r=0: P ≈ Φ(margin / period_vol)
        z = margin / period_vol if period_vol > 0 else 10
        prob = _normal_cdf(z)
    elif direction == "below":
        if current_price >= threshold:
            return None  # currently above — not a snipe for "below"
        margin = math.log(threshold / current_price)
        z = margin / period_vol if period_vol > 0 else 10
        prob = _normal_cdf(z)
    else:
        return None

    return prob


def _normal_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


# --- Market Discovery ---

def discover_markets():
    """Scan Gamma API for all active markets with near-certain outcomes."""
    all_markets = []
    seen_slugs = set()

    for offset in range(0, MAX_SCAN_PAGES * PAGE_SIZE, PAGE_SIZE):
        try:
            r = requests.get(f"{GAMMA_API}/markets", params={
                "closed": "false",
                "active": "true",
                "limit": str(PAGE_SIZE),
                "offset": str(offset),
            }, timeout=20)
            r.raise_for_status()
            markets = r.json()
        except Exception as e:
            print(f"  Gamma API error at offset {offset}: {e}")
            break

        if not markets:
            break

        for m in markets:
            slug = m.get("slug", "")
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)

            prices = m.get("outcomePrices", "[]")
            if isinstance(prices, str):
                prices = json.loads(prices)
            tokens = m.get("clobTokenIds", "[]")
            if isinstance(tokens, str):
                tokens = json.loads(tokens)

            if not prices or not tokens or len(prices) < 2 or len(tokens) < 2:
                continue

            yes_price = float(prices[0])
            no_price = float(prices[1])
            volume = float(m.get("volume", 0) or 0)
            end_date = m.get("endDate", "")

            if volume < MIN_VOLUME:
                continue

            # Check if either side is in the snipe zone
            for side_name, price, token_idx in [("YES", yes_price, 0), ("NO", no_price, 1)]:
                if MIN_SNIPE_PRICE <= price <= MAX_SNIPE_PRICE:
                    all_markets.append({
                        "question": m.get("question", ""),
                        "slug": slug,
                        "side": side_name,
                        "price": price,
                        "volume": volume,
                        "end_date": end_date,
                        "token_id": tokens[token_idx],
                        "outcomes": m.get("outcomes", ""),
                    })

        # Rate limiting
        if offset > 0:
            time.sleep(0.2)

    return all_markets


def categorize_market(question):
    """Categorize market for risk assessment."""
    q = question.lower()
    # Use word boundary matching to avoid substring false positives
    words = set(re.findall(r'\b\w+\b', q))

    crypto_assets = {"bitcoin", "btc", "ethereum", "eth", "solana", "crypto",
                     "xrp", "dogecoin", "doge"}
    if words & crypto_assets:
        price_words = {"price", "above", "below", "hit", "reach"}
        if words & price_words or "less than" in q or "more than" in q:
            return "crypto_price"
        return "crypto_event"

    sports_words = {"nba", "nfl", "mlb", "nhl", "fifa", "finals", "stanley",
                    "super bowl", "tournament", "championship"}
    sports_phrases = ["premier league", "champions league", "world cup",
                      "stanley cup", "super bowl", "nba finals"]
    if (words & sports_words) or any(p in q for p in sports_phrases):
        return "sports"

    politics_words = {"president", "presidential", "election", "nominee",
                      "nomination", "democratic", "republican", "governor",
                      "senate", "senator", "congress", "primary"}
    if words & politics_words:
        return "politics"

    weather_words = {"temperature", "weather"}
    weather_phrases = ["highest temp", "lowest temp", "°f", "°c"]
    if (words & weather_words) or any(p in q for p in weather_phrases):
        return "weather"

    return "other"


# --- Edge & Sizing ---

def calculate_annualized_return(price, days_to_resolution):
    """Calculate annualized return from buying at price and getting $1 back."""
    if price >= 1.0 or price <= 0 or days_to_resolution <= 0:
        return 0
    raw_return = (1.0 / price) - 1.0
    annualized = (1 + raw_return) ** (365.0 / days_to_resolution) - 1
    return min(annualized, 50.0)  # cap at 5000% to avoid display noise


def kelly_size(estimated_prob, price, bankroll):
    """Conservative Kelly sizing for near-certain bets."""
    if estimated_prob is None or estimated_prob <= price:
        return 0
    edge = estimated_prob - price
    kelly_f = edge / (1 - price)
    bet = bankroll * kelly_f * KELLY_FRACTION
    max_bet = bankroll * MAX_POSITION_PCT
    bet = min(bet, max_bet)
    shares = bet / price
    return max(0, shares)


# --- Opportunity Scoring ---

def score_opportunity(opp):
    """Score a snipe opportunity for ranking.

    Factors:
      - Annualized return (higher = better)
      - Confidence / verification level
      - Time to resolution (shorter = better for capital efficiency)
      - Volume (higher = better liquidity)
    """
    ar = opp.get("annualized_return", 0)
    conf = opp.get("confidence", 0.5)
    days = opp.get("days_to_resolution", 365)
    vol = opp.get("volume", 0)

    # Penalize very long lockups
    time_factor = min(1.0, 90 / max(days, 1))

    # Volume factor (log scale, normalized)
    vol_factor = min(1.0, math.log10(max(vol, 1)) / 7)  # $10M = 1.0

    score = ar * conf * time_factor * vol_factor
    return score


def analyze_opportunities(markets, crypto_prices):
    """Analyze and score all discovered markets."""
    now = datetime.now(timezone.utc)
    opportunities = []

    for m in markets:
        question = m["question"]
        category = categorize_market(question)
        price = m["price"]
        raw_return = (1.0 / price) - 1.0

        # Parse end date — skip expired markets
        end_date = m.get("end_date", "")
        if end_date:
            try:
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                days = (end_dt - now).days
                if days < 1:
                    continue  # expired or resolving today — skip
            except (ValueError, TypeError):
                days = 365
        else:
            days = 365

        annualized = calculate_annualized_return(price, days)
        confidence = 0.5  # default
        verified_by = ""
        estimated_prob = None

        # Category-specific analysis
        if category == "crypto_price":
            asset, threshold, direction = parse_crypto_market(question)
            if asset and threshold and crypto_prices.get(asset):
                current = crypto_prices[asset]
                prob = crypto_snipe_probability(asset, threshold, direction,
                                                current, days)
                if prob is not None and prob > price:
                    estimated_prob = prob
                    confidence = min(0.95, prob)
                    margin_pct = abs(current - threshold) / threshold * 100
                    verified_by = f"{asset}=${current:,.0f} vs ${threshold:,.0f} ({margin_pct:.0f}% margin)"
                else:
                    continue  # not a valid snipe
            else:
                confidence = 0.3  # can't verify

        elif category == "sports":
            # Sports markets: rely on market consensus + volume as proxy for accuracy
            # Higher volume = more informed pricing
            if m["volume"] > 1_000_000:
                confidence = 0.7
            elif m["volume"] > 100_000:
                confidence = 0.6
            else:
                confidence = 0.5
            verified_by = f"market_consensus (vol=${m['volume']:,.0f})"

        elif category == "politics":
            # Political markets: very long duration, high uncertainty
            if days > 365:
                confidence = 0.4  # too far out
            else:
                confidence = 0.5
            verified_by = "market_consensus"

        else:
            confidence = 0.5
            verified_by = "unverified"

        if estimated_prob is None:
            # For non-crypto: estimate prob as slightly above market price
            # (the market is usually right, our edge is small)
            estimated_prob = min(0.99, price + 0.02)

        opp = {
            "question": question[:100],
            "slug": m["slug"],
            "side": m["side"],
            "price": price,
            "raw_return": raw_return,
            "annualized_return": annualized,
            "days_to_resolution": days,
            "volume": m["volume"],
            "category": category,
            "confidence": confidence,
            "estimated_prob": estimated_prob,
            "verified_by": verified_by,
            "token_id": m["token_id"],
            "end_date": end_date[:10] if end_date else "",
        }
        opp["score"] = score_opportunity(opp)
        opportunities.append(opp)

    # Filter by minimum annualized return
    opportunities = [o for o in opportunities if o["annualized_return"] >= MIN_ANNUALIZED_RETURN]

    # Sort by score descending
    opportunities.sort(key=lambda x: x["score"], reverse=True)
    return opportunities


# --- Commands ---

def cmd_scan(args):
    """Scan for snipe opportunities."""
    print(f"\n  Resolution Sniper — Scanning Polymarket")
    print(f"  Snipe zone: ${MIN_SNIPE_PRICE:.2f} - ${MAX_SNIPE_PRICE:.2f}")
    print(f"  Min volume: ${MIN_VOLUME:,.0f}")
    print(f"  Min annualized return: {MIN_ANNUALIZED_RETURN:.0%}")

    # Get crypto prices
    print(f"\n  Fetching crypto prices...")
    crypto_prices = get_crypto_prices()
    for asset, price in crypto_prices.items():
        if price:
            print(f"    {asset:>10}: ${price:,.2f}")

    # Discover markets
    print(f"\n  Scanning markets (up to {MAX_SCAN_PAGES} pages)...")
    markets = discover_markets()
    print(f"  Found {len(markets)} markets in snipe zone")

    # Analyze
    opportunities = analyze_opportunities(markets, crypto_prices)
    print(f"  Tradeable opportunities: {len(opportunities)}")

    if not opportunities:
        print("\n  No opportunities meet criteria.")
        return

    # Display
    categories = {}
    for o in opportunities:
        cat = o["category"]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(o)

    for cat_name in ["crypto_price", "sports", "politics", "crypto_event", "weather", "other"]:
        cat_opps = categories.get(cat_name, [])
        if not cat_opps:
            continue

        print(f"\n  === {cat_name.upper().replace('_', ' ')} ({len(cat_opps)}) ===")
        print(f"  {'Side':>4}  {'Price':>6}  {'Return':>7}  {'Annual':>7}  {'Days':>5}  "
              f"{'Conf':>5}  {'Score':>6}  {'Question'}")
        print(f"  {'-'*4}  {'-'*6}  {'-'*7}  {'-'*7}  {'-'*5}  {'-'*5}  {'-'*6}  {'-'*50}")

        for o in cat_opps[:args.limit]:
            print(f"  {o['side']:>4}  ${o['price']:.3f}  "
                  f"{o['raw_return']:>+6.1%}  "
                  f"{o['annualized_return']:>+6.0%}  "
                  f"{o['days_to_resolution']:>5.0f}  "
                  f"{o['confidence']:>4.0%}  "
                  f"{o['score']:>5.2f}  "
                  f"{o['question'][:50]}")
            if o.get("verified_by"):
                print(f"         verified: {o['verified_by'][:70]}")

    # Summary
    print(f"\n  Top 5 by score:")
    for i, o in enumerate(opportunities[:5], 1):
        print(f"  {i}. [{o['category']}] {o['side']} ${o['price']:.3f} "
              f"(+{o['raw_return']:.1%}, {o['annualized_return']:+.0%} ann.) "
              f"— {o['question'][:60]}")


def cmd_trade(args):
    """Scan and place orders on best opportunities."""
    print(f"\n  Resolution Sniper — Trading Mode")

    bankroll = get_balance()
    print(f"  Bankroll: ${bankroll:,.2f}")
    if bankroll < 10:
        print("  ERROR: Bankroll too low (< $10)")
        return

    conn = init_db()
    dry_run = args.dry_run
    client = None if dry_run else get_client()

    # Check existing exposure
    existing_cost = conn.execute(
        "SELECT COALESCE(SUM(cost), 0) FROM snipe_trades WHERE status='placed'"
    ).fetchone()[0]
    total_exposure = float(existing_cost)
    max_exposure = bankroll * MAX_TOTAL_EXPOSURE_PCT
    print(f"  Existing exposure: ${total_exposure:,.2f} / ${max_exposure:,.2f} max")

    # Existing slugs (dedup)
    existing_slugs = set(
        r[0] for r in conn.execute(
            "SELECT slug FROM snipe_trades WHERE status='placed'"
        ).fetchall()
    )

    # Get prices & scan
    crypto_prices = get_crypto_prices()
    markets = discover_markets()
    opportunities = analyze_opportunities(markets, crypto_prices)

    # Only trade crypto_price (auto-verified) and high-confidence others
    tradeable = [o for o in opportunities
                 if (o["category"] == "crypto_price" and o["confidence"] >= 0.85)
                 or (o["confidence"] >= 0.70 and o["annualized_return"] >= 0.25)]

    trades_placed = 0
    for o in tradeable:
        if o["slug"] in existing_slugs:
            print(f"  SKIP (already open): {o['question'][:60]}")
            continue

        if total_exposure >= max_exposure:
            print(f"  STOP: Max exposure reached (${total_exposure:,.2f})")
            break

        shares = kelly_size(o["estimated_prob"], o["price"], bankroll)
        if shares < MIN_ORDER_SIZE:
            continue

        shares = round(shares)
        order_price = round(o["price"] - MAKER_OFFSET, 3)
        order_price = max(0.01, min(0.99, order_price))
        cost = shares * order_price
        potential_profit = shares * (1.0 - order_price)

        print(f"\n  >>> SNIPE: {o['side']} {o['question'][:70]}")
        print(f"      Price: ${o['price']:.3f} → Order: ${order_price:.3f}")
        print(f"      Shares: {shares} | Cost: ${cost:.2f} | Potential profit: ${potential_profit:.2f}")
        print(f"      Return: +{o['raw_return']:.1%} | Annualized: {o['annualized_return']:+.0%}")
        print(f"      Confidence: {o['confidence']:.0%} | Verified: {o.get('verified_by','none')[:50]}")

        order_id = None
        if not dry_run:
            try:
                order_args = OrderArgs(
                    token_id=o["token_id"],
                    price=order_price,
                    size=shares,
                    side=BUY,
                )
                signed = client.create_order(order_args)
                result = client.post_order(signed, OrderType.GTC, post_only=True)
                order_id = result.get("orderID", "")
                status = result.get("status", "UNKNOWN")
                print(f"      Placed: {order_id} [{status}]")
                trades_placed += 1
            except Exception as ex:
                print(f"      ORDER FAILED: {ex}")
                continue
        else:
            print(f"      [DRY RUN — not placed]")
            trades_placed += 1

        record_trade(conn,
            ts=datetime.now(timezone.utc).isoformat(),
            question=o["question"][:200],
            slug=o["slug"],
            side=o["side"],
            category=o["category"],
            market_price=o["price"],
            order_price=order_price,
            shares=shares,
            cost=cost,
            potential_profit=potential_profit,
            annualized_return=o["annualized_return"],
            days_to_resolution=o["days_to_resolution"],
            token_id=o["token_id"],
            order_id=order_id or "DRY_RUN",
            end_date=o["end_date"],
            verified_by=o.get("verified_by", ""),
            confidence=o["confidence"],
        )
        total_exposure += cost
        existing_slugs.add(o["slug"])

    conn.close()
    mode = "DRY RUN" if dry_run else "LIVE"
    print(f"\n  Done. {trades_placed} snipes placed [{mode}].")


def cmd_history(args):
    """Show trade history."""
    conn = init_db()
    limit = args.limit or 20
    rows = conn.execute(
        "SELECT ts, side, category, market_price, order_price, shares, cost, "
        "potential_profit, annualized_return, days_to_resolution, status, pnl, "
        "question FROM snipe_trades ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()

    if not rows:
        print("No snipe trades recorded.")
        return

    print(f"\n  {'Time':>19}  {'Side':>4}  {'Cat':>8}  {'Mkt$':>5}  {'Ord$':>5}  "
          f"{'Shares':>6}  {'Cost':>7}  {'Ann%':>6}  {'Days':>5}  {'Status':>8}  {'PnL':>7}")
    print(f"  {'-'*19}  {'-'*4}  {'-'*8}  {'-'*5}  {'-'*5}  "
          f"{'-'*6}  {'-'*7}  {'-'*6}  {'-'*5}  {'-'*8}  {'-'*7}")

    for r in rows:
        ts, side, cat, mp, op, sh, cost, pp, ar, days, status, pnl, q = r
        print(f"  {ts[:19]}  {side:>4}  {cat[:8]:>8}  ${mp:.3f}  ${op:.3f}  "
              f"{sh:>6.0f}  ${cost:>6.2f}  {ar:>+5.0%}  {days:>5.0f}  {status:>8}  ${pnl:>6.2f}")
        print(f"    {q[:70]}")


def cmd_pnl(args):
    """Show P&L summary."""
    conn = init_db()
    rows = conn.execute(
        "SELECT COUNT(*), SUM(cost), SUM(pnl), SUM(potential_profit), "
        "SUM(CASE WHEN status='won' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN status='lost' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN status='placed' THEN 1 ELSE 0 END), "
        "AVG(annualized_return), AVG(confidence) "
        "FROM snipe_trades"
    ).fetchone()
    conn.close()

    total, cost, pnl, pot_profit, won, lost, pending, avg_ar, avg_conf = rows
    if not total:
        print("No snipe trades recorded.")
        return

    cost = cost or 0
    pnl = pnl or 0
    pot_profit = pot_profit or 0
    won = int(won or 0)
    lost = int(lost or 0)
    pending = int(pending or 0)
    resolved = won + lost

    print(f"\n  === Resolution Sniper P&L ===")
    print(f"  Total trades: {total}")
    print(f"  Total invested: ${cost:,.2f}")
    print(f"  Potential profit (if all win): ${pot_profit:,.2f}")
    print(f"  Resolved: {resolved} (Won: {won}, Lost: {lost})")
    print(f"  Pending: {pending}")
    if resolved > 0:
        wr = won / resolved * 100
        print(f"  Win rate: {wr:.1f}%")
    print(f"  Realized PnL: ${pnl:,.2f}")
    print(f"  Avg annualized return at entry: {(avg_ar or 0):+.0%}")
    print(f"  Avg confidence at entry: {(avg_conf or 0):.0%}")


def cmd_resolve(args):
    """Check and resolve completed trades."""
    conn = init_db()
    pending = conn.execute(
        "SELECT id, slug, side, order_price, shares, token_id, end_date, question "
        "FROM snipe_trades WHERE status='placed'"
    ).fetchall()

    if not pending:
        print("No pending snipe trades to resolve.")
        conn.close()
        return

    now = datetime.now(timezone.utc)
    print(f"  Checking {len(pending)} pending snipes...\n")
    resolved = 0

    for row in pending:
        trade_id, slug, side, order_price, shares, token_id, end_date, question = row

        # Check if past end date
        if end_date:
            try:
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                if end_dt > now:
                    continue  # not yet
            except (ValueError, TypeError):
                continue

        # Look up market by slug
        try:
            r = requests.get(f"{GAMMA_API}/markets", params={
                "slug": slug, "limit": "1",
            }, timeout=15)
            markets = r.json()
            if not markets:
                continue

            market = markets[0]
            if not market.get("closed"):
                continue

            prices = market.get("outcomePrices", "[]")
            if isinstance(prices, str):
                prices = json.loads(prices)
            if not prices or len(prices) < 2:
                continue

            # Determine if our side won
            side_idx = 0 if side == "YES" else 1
            final_price = float(prices[side_idx])

            if final_price >= 0.95:  # Our side won
                pnl = shares * (1.0 - order_price)
                conn.execute("UPDATE snipe_trades SET status='won', pnl=? WHERE id=?",
                             (pnl, trade_id))
                print(f"  WON: {question[:60]} | PnL: +${pnl:.2f}")
            else:  # Our side lost
                pnl = -(shares * order_price)
                conn.execute("UPDATE snipe_trades SET status='lost', pnl=? WHERE id=?",
                             (pnl, trade_id))
                print(f"  LOST: {question[:60]} | PnL: -${abs(pnl):.2f}")
            resolved += 1

        except Exception as e:
            print(f"  Error resolving {slug[:40]}: {e}")

    conn.commit()
    conn.close()
    print(f"\n  Resolved {resolved} snipe trades.")


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="Resolution Sniper")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("scan", help="Scan for snipe opportunities")
    p.add_argument("--limit", type=int, default=10, help="Max results per category")

    p = sub.add_parser("trade", help="Scan and place orders")
    p.add_argument("--dry-run", action="store_true", help="Don't place real orders")

    p = sub.add_parser("history", help="Trade history")
    p.add_argument("--limit", type=int, default=20)

    sub.add_parser("pnl", help="P&L summary")
    sub.add_parser("resolve", help="Resolve completed trades")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    cmds = {
        "scan": cmd_scan,
        "trade": cmd_trade,
        "history": cmd_history,
        "pnl": cmd_pnl,
        "resolve": cmd_resolve,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
