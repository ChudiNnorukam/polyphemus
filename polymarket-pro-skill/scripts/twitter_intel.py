#!/usr/bin/env python3
"""OpenClaw Social Intelligence v2 - Polymarket + crypto market scanner.

Uses FREE data sources (no paid API keys):
  1. Polymarket Data API - leaderboard, top traders by PnL/volume
  2. CryptoCompare News API - hot crypto news for BTC/ETH/SOL
  3. CoinGecko Trending API - trending coins by search volume

Usage:
    python3 twitter_intel.py scan                # Run all sources, save digest
    python3 twitter_intel.py scan --source whales # Only Polymarket leaderboard
    python3 twitter_intel.py scan --source news   # Only crypto news
    python3 twitter_intel.py scan --source trend  # Only trending coins
    python3 twitter_intel.py digest               # Show latest digest
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# --- Config ---

DATA_DIR = os.environ.get("OPENCLAW_DATA_DIR", "/opt/openclaw/data")
INTEL_DIR = os.path.join(DATA_DIR, "intel")
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "")

UA = "Mozilla/5.0 (compatible; OpenClaw/2.0)"


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr)


def _fetch_json(url: str, timeout: int = 15) -> dict | list | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        _log(f"  HTTP {e.code} from {url}")
        return None
    except Exception as e:
        _log(f"  {e} from {url}")
        return None


# --- Polymarket Leaderboard ---

def scan_polymarket_whales() -> dict:
    """Scan Polymarket Data API for top traders."""
    results = {}

    # Daily PnL leaders
    data = _fetch_json(
        "https://data-api.polymarket.com/v1/leaderboard?timePeriod=DAY&orderBy=PNL&limit=10"
    )
    if isinstance(data, list) and data:
        leaders = []
        for entry in data:
            pnl = entry.get("pnl", 0)
            vol = entry.get("vol", 0)
            leaders.append({
                "username": entry.get("userName", "anon"),
                "pnl_24h": round(pnl, 2) if isinstance(pnl, (int, float)) else pnl,
                "volume_24h": round(vol, 2) if isinstance(vol, (int, float)) else vol,
                "rank": entry.get("rank", "?"),
            })
        results["pnl_leaders"] = leaders
        _log(f"Polymarket PnL leaderboard: {len(leaders)} entries")

    # Crypto-specific leaders
    crypto_data = _fetch_json(
        "https://data-api.polymarket.com/v1/leaderboard?category=CRYPTO&timePeriod=WEEK&orderBy=VOL&limit=10"
    )
    if isinstance(crypto_data, list) and crypto_data:
        crypto_leaders = []
        for entry in crypto_data:
            pnl = entry.get("pnl", 0)
            vol = entry.get("vol", 0)
            crypto_leaders.append({
                "username": entry.get("userName", "anon"),
                "pnl_week": round(pnl, 2) if isinstance(pnl, (int, float)) else pnl,
                "volume_week": round(vol, 2) if isinstance(vol, (int, float)) else vol,
                "rank": entry.get("rank", "?"),
            })
        results["crypto_leaders"] = crypto_leaders
        _log(f"Polymarket crypto leaderboard: {len(crypto_leaders)} entries")

    if results:
        return {"polymarket_leaderboard": results}
    return {}


# --- CryptoCompare News ---

def scan_crypto_news() -> dict:
    """Scan CryptoCompare for latest crypto news."""
    data = _fetch_json(
        "https://min-api.cryptocompare.com/data/v2/news/?lang=EN&categories=BTC,ETH,SOL"
    )
    if not data:
        return {}

    articles_raw = data.get("Data", [])
    if not articles_raw:
        return {}

    articles = []
    for post in articles_raw[:10]:
        published_ts = post.get("published_on", 0)
        published_str = datetime.fromtimestamp(published_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if published_ts else ""
        categories = post.get("categories", "")

        articles.append({
            "title": post.get("title", ""),
            "url": post.get("url", ""),
            "source": post.get("source", ""),
            "published": published_str,
            "categories": categories,
        })

    _log(f"CryptoCompare: {len(articles)} articles")
    return {
        "crypto_news": {
            "description": "Latest crypto news (BTC/ETH/SOL)",
            "articles": articles,
        }
    }


# --- CoinGecko Trending ---

def scan_trending() -> dict:
    """Scan CoinGecko for trending coins."""
    data = _fetch_json("https://api.coingecko.com/api/v3/search/trending")
    if not data:
        return {}

    coins_raw = data.get("coins", [])
    if not coins_raw:
        return {}

    coins = []
    for entry in coins_raw[:15]:
        item = entry.get("item", {})
        price_change = item.get("data", {}).get("price_change_percentage_24h", {})
        usd_change = price_change.get("usd", 0) if isinstance(price_change, dict) else 0

        coins.append({
            "name": item.get("name", ""),
            "symbol": item.get("symbol", ""),
            "market_cap_rank": item.get("market_cap_rank"),
            "price_btc": item.get("price_btc", 0),
            "price_change_24h_pct": round(usd_change, 2) if isinstance(usd_change, (int, float)) else 0,
        })

    _log(f"CoinGecko trending: {len(coins)} coins")
    return {
        "trending": {
            "description": "Trending coins by CoinGecko search volume (24h)",
            "coins": coins,
        }
    }


# --- Keyword Extraction ---

SIGNAL_KEYWORDS = {
    "bullish": ["long", "buy", "pump", "moon", "breakout", "higher", "bullish", "accumulating", "loading", "rally"],
    "bearish": ["short", "sell", "dump", "crash", "breakdown", "lower", "bearish", "dumping", "exit", "plunge"],
    "resolution": ["resolved", "resolving", "settlement", "expired", "outcome", "result"],
    "whale_move": ["position", "opened", "closed", "liquidated", "whale", "large order", "size"],
}

ASSET_KEYWORDS = ["BTC", "bitcoin", "ETH", "ethereum", "SOL", "solana", "XRP"]


def extract_signals(text: str) -> dict:
    text_lower = text.lower()
    signals = {}
    for category, keywords in SIGNAL_KEYWORDS.items():
        matches = [kw for kw in keywords if kw in text_lower]
        if matches:
            signals[category] = matches
    assets = [a for a in ASSET_KEYWORDS if a.lower() in text_lower]
    if assets:
        signals["assets"] = assets
    bull = len(signals.get("bullish", []))
    bear = len(signals.get("bearish", []))
    signals["sentiment"] = bull - bear
    return signals


# --- Digest Generation ---

def generate_digest(whale_data: dict, news_data: dict, trend_data: dict) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Social Intel Digest - {now}\n"]

    news_bullish = 0
    news_bearish = 0

    # Polymarket leaderboard
    lb = whale_data.get("polymarket_leaderboard", {})

    pnl_leaders = lb.get("pnl_leaders", [])
    if pnl_leaders:
        lines.append("## Polymarket Top Traders (24h PnL)")
        lines.append("| Rank | Trader | PnL 24h | Volume 24h |")
        lines.append("|------|--------|---------|------------|")
        for entry in pnl_leaders:
            pnl = entry.get("pnl_24h", 0)
            vol = entry.get("volume_24h", 0)
            pnl_str = f"${pnl:+,.0f}" if isinstance(pnl, (int, float)) else str(pnl)
            vol_str = f"${vol:,.0f}" if isinstance(vol, (int, float)) else str(vol)
            lines.append(f"| {entry.get('rank', '?')} | {entry.get('username', '?')} | {pnl_str} | {vol_str} |")
        lines.append("")

    crypto_leaders = lb.get("crypto_leaders", [])
    if crypto_leaders:
        lines.append("## Polymarket Crypto Traders (7d Volume)")
        lines.append("| Rank | Trader | PnL 7d | Volume 7d |")
        lines.append("|------|--------|--------|-----------|")
        for entry in crypto_leaders:
            pnl = entry.get("pnl_week", 0)
            vol = entry.get("volume_week", 0)
            pnl_str = f"${pnl:+,.0f}" if isinstance(pnl, (int, float)) else str(pnl)
            vol_str = f"${vol:,.0f}" if isinstance(vol, (int, float)) else str(vol)
            lines.append(f"| {entry.get('rank', '?')} | {entry.get('username', '?')} | {pnl_str} | {vol_str} |")
        lines.append("")

    # Crypto news
    news = news_data.get("crypto_news", {})
    articles = news.get("articles", [])
    if articles:
        lines.append("## Crypto News (BTC/ETH/SOL)")
        for a in articles:
            signals = extract_signals(a.get("title", ""))
            sentiment = signals.get("sentiment", 0)
            if sentiment > 0:
                news_bullish += 1
                tag = "[+]"
            elif sentiment < 0:
                news_bearish += 1
                tag = "[-]"
            else:
                tag = "[~]"
            cats = a.get("categories", "")
            cat_tag = f" ({cats})" if cats else ""
            lines.append(f"- {tag} {a['title']}{cat_tag}")
            lines.append(f"  _{a.get('source', '')} - {a.get('published', '')}_")
            lines.append("")

    # Trending coins
    trending = trend_data.get("trending", {})
    coins = trending.get("coins", [])
    if coins:
        lines.append("## Trending Coins (CoinGecko)")
        lines.append("| Coin | Symbol | Rank | 24h Change |")
        lines.append("|------|--------|------|------------|")
        for c in coins:
            change = c.get("price_change_24h_pct", 0)
            change_str = f"{change:+.1f}%" if isinstance(change, (int, float)) and change != 0 else "N/A"
            rank = c.get("market_cap_rank") or "?"
            lines.append(f"| {c['name']} | {c['symbol']} | #{rank} | {change_str} |")
        lines.append("")

    # Summary line
    n_articles = len(articles)
    n_trending = len(coins)
    n_leaders = len(pnl_leaders)
    summary = f"**Sources**: {n_leaders} top traders | {n_articles} news articles | {n_trending} trending coins"
    if n_articles > 0:
        summary += f" | News sentiment: +{news_bullish}/-{news_bearish}"
    lines.insert(1, summary + "\n")

    lines.append("---")
    lines.append("_Generated by OpenClaw Social Intel v2 (free sources: Polymarket Data API, CryptoCompare, CoinGecko)_")
    return "\n".join(lines)


def generate_slack_summary(whale_data: dict, news_data: dict, trend_data: dict) -> str:
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    parts = [f":globe_with_meridians: *Social Intel Scan* — {now}"]

    lb = whale_data.get("polymarket_leaderboard", {})

    # --- PnL leaderboard top 5 with volume ---
    leaders = lb.get("pnl_leaders", [])
    if leaders:
        parts.append("\n*:trophy: Polymarket Top 5 (24h PnL):*")
        for entry in leaders[:5]:
            pnl = entry.get("pnl_24h", 0)
            vol = entry.get("volume_24h", 0)
            pnl_str = f"${pnl:+,.0f}" if isinstance(pnl, (int, float)) else str(pnl)
            vol_str = f"${vol:,.0f}" if isinstance(vol, (int, float)) else "n/a"
            parts.append(f"  #{entry.get('rank','?')} {entry.get('username','?')}: {pnl_str}  _(vol {vol_str})_")

    # --- Crypto 7d leaders top 3 ---
    crypto = lb.get("crypto_leaders", [])
    if crypto:
        parts.append("\n*:chart_with_upwards_trend: Crypto Traders (7d Vol):*")
        for entry in crypto[:3]:
            pnl = entry.get("pnl_week", 0)
            vol = entry.get("volume_week", 0)
            pnl_str = f"${pnl:+,.0f}" if isinstance(pnl, (int, float)) else str(pnl)
            vol_str = f"${vol:,.0f}" if isinstance(vol, (int, float)) else "n/a"
            parts.append(f"  #{entry.get('rank','?')} {entry.get('username','?')}: {pnl_str} PnL  |  {vol_str} vol")

    # --- News with sentiment badges and clickable links ---
    articles = news_data.get("crypto_news", {}).get("articles", [])
    bull, bear = 0, 0
    if articles:
        news_lines = []
        for a in articles[:5]:
            sigs = extract_signals(a.get("title", ""))
            sentiment = sigs.get("sentiment", 0)
            if sentiment > 0:
                bull += 1
                badge = ":green_circle:"
            elif sentiment < 0:
                bear += 1
                badge = ":red_circle:"
            else:
                badge = ":white_circle:"
            url = a.get("url", "")
            title = a.get("title", "")[:100]
            source = a.get("source", "")
            pub = a.get("published", "")
            pub_short = pub[-5:] if pub else ""  # HH:MM only
            link = f"<{url}|{title}>" if url else title
            news_lines.append(f"  {badge} {link}  _{source} {pub_short}_")
        sentiment_str = f"+{bull} bullish / -{bear} bearish"
        parts.append(f"\n*:newspaper: Crypto News ({len(articles)}) — {sentiment_str}:*")
        parts.extend(news_lines)

    # --- Trending coins with 24h price change ---
    coins = trend_data.get("trending", {}).get("coins", [])
    if coins:
        trend_parts = []
        for c in coins[:7]:
            sym = c.get("symbol", "")
            change = c.get("price_change_24h_pct", 0)
            if isinstance(change, (int, float)) and change != 0:
                arrow = ":small_red_triangle:" if change > 0 else ":small_red_triangle_down:"
                trend_parts.append(f"{sym} {arrow}{change:+.1f}%")
            else:
                trend_parts.append(sym)
        parts.append(f"\n*:fire: Trending:* {',  '.join(trend_parts)}")

    return "\n".join(parts)


def post_to_slack(text: str):
    if not SLACK_BOT_TOKEN or not SLACK_CHANNEL_ID:
        return
    payload = json.dumps({"channel": SLACK_CHANNEL_ID, "text": text}).encode()
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=payload,
        headers={
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            if not result.get("ok"):
                _log(f"Slack error: {result.get('error')}")
    except Exception as e:
        _log(f"Slack failed: {e}")


# --- Commands ---

def cmd_scan(args):
    os.makedirs(INTEL_DIR, exist_ok=True)

    source = getattr(args, "source", "all")

    whale_data = {}
    news_data = {}
    trend_data = {}

    if source in ("all", "whales"):
        whale_data = scan_polymarket_whales()

    if source in ("all", "news"):
        news_data = scan_crypto_news()

    if source in ("all", "trend"):
        trend_data = scan_trending()

    # Generate digest
    digest = generate_digest(whale_data, news_data, trend_data)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    digest_path = os.path.join(INTEL_DIR, f"social_intel_{date_str}.md")
    with open(digest_path, "w") as f:
        f.write(digest)

    # Save raw data
    raw_path = os.path.join(INTEL_DIR, f"social_raw_{date_str}.json")
    with open(raw_path, "w") as f:
        json.dump({"whales": whale_data, "news": news_data, "trending": trend_data}, f, indent=2, default=str)

    _log(f"Digest: {digest_path}")

    # Slack
    slack_msg = generate_slack_summary(whale_data, news_data, trend_data)
    post_to_slack(slack_msg)

    print(digest)


def cmd_digest(args):
    intel_path = Path(INTEL_DIR)
    if not intel_path.exists():
        print("No intel directory yet.")
        return
    digests = sorted(intel_path.glob("social_intel_*.md"), reverse=True)
    if not digests:
        digests = sorted(intel_path.glob("twitter_intel_*.md"), reverse=True)
    if not digests:
        print("No digests found.")
        return
    with open(digests[0]) as f:
        print(f.read())


def main():
    parser = argparse.ArgumentParser(description="OpenClaw Social Intelligence v2")
    sub = parser.add_subparsers(dest="command")

    p_scan = sub.add_parser("scan", help="Run social intel scan")
    p_scan.add_argument("--source", choices=["all", "whales", "news", "trend"], default="all")
    p_scan.set_defaults(func=cmd_scan)

    p_digest = sub.add_parser("digest", help="Show latest digest")
    p_digest.set_defaults(func=cmd_digest)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
