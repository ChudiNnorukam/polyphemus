#!/usr/bin/env python3
"""Polymarket Whale Monitor - track profitable wallets in real-time.

Usage:
    python3 whale_monitor.py                    # Monitor top 10 wallets, 10s interval
    python3 whale_monitor.py --interval 5       # Poll every 5 seconds
    python3 whale_monitor.py --wallets top20    # Track top 20
    python3 whale_monitor.py --wallets 0xABC,0xDEF  # Track specific wallets
    python3 whale_monitor.py --min-usdc 1000    # Only alert on trades > $1,000
    python3 whale_monitor.py --categories crypto # Only crypto markets
    python3 whale_monitor.py --snapshot         # One-shot: show current positions, then exit
    python3 whale_monitor.py --history 0xABC    # Show stored trade history for a wallet
    python3 whale_monitor.py --directionality   # Classify wallets: directional vs market maker

No authentication required. Uses Polymarket's public Data API.
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Leaderboard seed: top 20 monthly profit wallets (scraped 2026-04-07)
# ---------------------------------------------------------------------------
LEADERBOARD_WALLETS = {
    "0x492442eab586f242b53bda933fd5de859c8a3782": ("Multicolored-Self", 6_440_436),
    "0x02227b8f5a9636e895607edd3185ed6ee5598ff7": ("HorizonSplendidView", 4_016_108),
    "0xefbc5fec8d7b0acdc8911bdd9a98d6964308f9a2": ("reachingthesky", 3_742_635),
    "0xc2e7800b5af46e6093872b177b7a5e7f0563be51": ("beachboy4", 3_180_956),
    "0x019782cab5d844f02bafb71f512758be78579f3c": ("majorexploiter", 2_416_975),
    "0x2a2c53bd278c04da9962fcf96490e17f3dfb9bc1": ("anon-6", 2_026_041),
    "0x2005d16a84ceefa912d4e380cd32e7ff827875ea": ("RN1", 1_759_561),
    "0xee613b3fc183ee44f9da9c05f53e2da107e3debf": ("sovereign2013", 1_758_094),
    "0xbddf61af533ff524d27154e589d2d7a81510c684": ("Countryside", 1_616_141),
    "0xdc876e6873772d38716fda7f2452a78d426d7ab6": ("432614799197", 1_495_977),
    "0xf195721ad850377c96cd634457c70cd9e8308057": ("lo34567Taipe", 1_459_615),
    "0xb45a797faa52b0fd8adc56d30382022b7b12192c": ("bcda", 1_340_160),
    "0x2b3ff45c91540e46fae1e0c72f61f4b049453446": ("Mentallyillgambld", 1_258_877),
    "0x93abbc022ce98d6f45d4444b594791cc4b7a9723": ("gatorr", 1_213_989),
    "0x59a0744db1f39ff3afccd175f80e6e8dfc239a09": ("Blessed-Sunshine", 1_202_927),
    "0x50b1db131a24a9d9450bbd0372a95d32ea88f076": ("blindStaking", 1_170_165),
    "0x8f037a2e4fd49d11267f4ab874ab7ba745ac64d6": ("Anointed-Connect", 1_168_767),
    "0x204f72f35326db932158cba6adff0b9a1da95e14": ("swisstony", 1_071_462),
    "0xb6d6e99d3bfe055874a04279f659f009fd57be17": ("JPMorgan101", 931_640),
    "0x8c80d213c0cbad777d06ee3f58f6ca4bc03102c3": ("SecondWindCapital", 920_287),
}

DATA_API = "https://data-api.polymarket.com"
DEFAULT_DB = Path(__file__).parent.parent / "data" / "whale_monitor.db"
USER_AGENT = "PolyphemusWhaleMonitor/1.0"

# Category keywords for filtering
CATEGORY_KEYWORDS = {
    "crypto": ["btc", "bitcoin", "eth", "ethereum", "sol", "solana", "xrp", "crypto", "updown"],
    "sports": ["nba", "nfl", "mlb", "nhl", "ufc", "soccer", "tennis", "spread", "moneyline", "totals"],
    "politics": ["president", "election", "trump", "biden", "senate", "congress", "governor"],
}


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------
def _api_get(path: str, params: Optional[Dict] = None, retries: int = 2) -> Optional[list]:
    """GET from Polymarket Data API. Returns parsed JSON or None on failure."""
    url = f"{DATA_API}{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        url = f"{url}?{qs}"

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError) as e:
            if attempt < retries:
                time.sleep(1)
            else:
                print(f"  [API ERROR] {path}: {e}", file=sys.stderr)
                return None


def fetch_activity(wallet: str, limit: int = 20) -> List[dict]:
    """Fetch recent trades for a wallet. No auth required."""
    result = _api_get("/activity", {"user": wallet, "limit": str(limit)})
    return result if result else []


def fetch_positions(wallet: str, size_threshold: float = 0) -> List[dict]:
    """Fetch open positions for a wallet. No auth required."""
    result = _api_get("/positions", {
        "user": wallet,
        "limit": "100",
        "sizeThreshold": str(size_threshold),
    })
    return result if result else []


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def init_db(db_path: Path) -> sqlite3.Connection:
    """Initialize SQLite database for whale trade storage."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tracked_wallets (
            address TEXT PRIMARY KEY,
            alias TEXT,
            leaderboard_pnl REAL,
            added_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            last_active TEXT
        );
        CREATE TABLE IF NOT EXISTS whale_trades (
            tx_hash TEXT,
            wallet TEXT,
            timestamp INTEGER,
            slug TEXT,
            title TEXT,
            outcome TEXT,
            side TEXT,
            price REAL,
            size REAL,
            usdc_size REAL,
            condition_id TEXT,
            asset TEXT,
            event_slug TEXT,
            detected_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            PRIMARY KEY (tx_hash, wallet, asset)
        );
        CREATE TABLE IF NOT EXISTS whale_positions (
            wallet TEXT,
            condition_id TEXT,
            outcome TEXT,
            size REAL,
            avg_price REAL,
            current_value REAL,
            cash_pnl REAL,
            pct_pnl REAL,
            cur_price REAL,
            title TEXT,
            slug TEXT,
            end_date TEXT,
            last_seen TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            PRIMARY KEY (wallet, condition_id, outcome)
        );
        CREATE INDEX IF NOT EXISTS idx_trades_wallet ON whale_trades(wallet);
        CREATE INDEX IF NOT EXISTS idx_trades_ts ON whale_trades(timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_trades_slug ON whale_trades(slug);
    """)
    conn.commit()
    return conn


def save_wallet(conn: sqlite3.Connection, address: str, alias: str, pnl: float):
    """Upsert a tracked wallet."""
    conn.execute(
        "INSERT INTO tracked_wallets (address, alias, leaderboard_pnl) "
        "VALUES (?, ?, ?) ON CONFLICT(address) DO UPDATE SET alias=?, leaderboard_pnl=?",
        (address, alias, pnl, alias, pnl),
    )
    conn.commit()


def save_trade(conn: sqlite3.Connection, trade: dict, wallet: str) -> bool:
    """Save a trade. Returns True if it was new (not previously seen)."""
    tx_hash = trade.get("transactionHash", "")
    asset = trade.get("asset", "")
    try:
        conn.execute(
            "INSERT INTO whale_trades "
            "(tx_hash, wallet, timestamp, slug, title, outcome, side, price, size, "
            "usdc_size, condition_id, asset, event_slug) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                tx_hash, wallet, trade.get("timestamp", 0),
                trade.get("slug", ""), trade.get("title", ""),
                trade.get("outcome", ""), trade.get("side", ""),
                trade.get("price", 0), trade.get("size", 0),
                trade.get("usdcSize", 0), trade.get("conditionId", ""),
                asset, trade.get("eventSlug", ""),
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False  # Already seen


def save_position(conn: sqlite3.Connection, pos: dict, wallet: str):
    """Upsert a position snapshot."""
    conn.execute(
        "INSERT INTO whale_positions "
        "(wallet, condition_id, outcome, size, avg_price, current_value, "
        "cash_pnl, pct_pnl, cur_price, title, slug, end_date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(wallet, condition_id, outcome) DO UPDATE SET "
        "size=?, avg_price=?, current_value=?, cash_pnl=?, pct_pnl=?, "
        "cur_price=?, last_seen=strftime('%Y-%m-%dT%H:%M:%SZ', 'now')",
        (
            wallet, pos.get("conditionId", ""), pos.get("outcome", ""),
            pos.get("size", 0), pos.get("avgPrice", 0),
            pos.get("currentValue", 0), pos.get("cashPnl", 0),
            pos.get("percentPnl", 0), pos.get("curPrice", 0),
            pos.get("title", ""), pos.get("slug", ""),
            pos.get("endDate", ""),
            # ON CONFLICT update values:
            pos.get("size", 0), pos.get("avgPrice", 0),
            pos.get("currentValue", 0), pos.get("cashPnl", 0),
            pos.get("percentPnl", 0), pos.get("curPrice", 0),
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------
def _alias(wallet: str) -> str:
    """Short display name for a wallet."""
    info = LEADERBOARD_WALLETS.get(wallet.lower())
    if info:
        return info[0]
    return wallet[:10] + "..."


def _ts_str(ts: int) -> str:
    """Unix timestamp to readable string."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M:%S UTC")


def _category(slug: str, title: str) -> str:
    """Detect market category from slug/title."""
    combined = (slug + " " + title).lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in combined for kw in keywords):
            return cat
    return "other"


def _matches_category(slug: str, title: str, filter_cat: Optional[str]) -> bool:
    """Check if a trade matches the category filter."""
    if not filter_cat:
        return True
    return _category(slug, title) == filter_cat


def format_new_trade(trade: dict, wallet: str) -> str:
    """Format a new trade alert for terminal display."""
    alias = _alias(wallet)
    side = trade.get("side", "?")
    price = trade.get("price", 0)
    usdc = trade.get("usdcSize", 0)
    shares = trade.get("size", 0)
    title = trade.get("title", "?")
    outcome = trade.get("outcome", "?")
    ts = _ts_str(trade.get("timestamp", 0))
    cat = _category(trade.get("slug", ""), title)

    side_color = "\033[92m" if side == "BUY" else "\033[91m"  # green/red
    reset = "\033[0m"
    bold = "\033[1m"
    dim = "\033[2m"

    return (
        f"  {bold}{side_color}{side}{reset} "
        f"| {bold}{alias}{reset} "
        f"| {outcome} @ ${price:.2f} "
        f"| ${usdc:,.0f} ({shares:,.0f} shares) "
        f"| {dim}{title}{reset} "
        f"| [{cat}] {ts}"
    )


def format_position_line(pos: dict, wallet: str) -> str:
    """Format a position for snapshot display."""
    alias = _alias(wallet)
    pnl = pos.get("cashPnl", 0)
    pnl_pct = pos.get("percentPnl", 0)
    cur_price = pos.get("curPrice", 0)
    avg_price = pos.get("avgPrice", 0)
    value = pos.get("currentValue", 0)
    title = pos.get("title", "?")
    outcome = pos.get("outcome", "?")
    size = pos.get("size", 0)

    pnl_color = "\033[92m" if pnl >= 0 else "\033[91m"
    reset = "\033[0m"
    dim = "\033[2m"

    return (
        f"  {alias:20s} | {outcome:12s} | "
        f"avg=${avg_price:.2f} now=${cur_price:.2f} | "
        f"{size:>10,.0f} shares | ${value:>10,.0f} value | "
        f"{pnl_color}${pnl:>+10,.0f} ({pnl_pct:>+.1f}%){reset} | "
        f"{dim}{title[:50]}{reset}"
    )


# ---------------------------------------------------------------------------
# Consensus: what are whales converging on?
# ---------------------------------------------------------------------------
def compute_consensus(conn: sqlite3.Connection, hours: int = 24) -> List[dict]:
    """Find markets where multiple whales are buying the same outcome."""
    cutoff = int(time.time()) - (hours * 3600)
    rows = conn.execute(
        """
        SELECT slug, title, outcome, side,
               COUNT(DISTINCT wallet) as whale_count,
               SUM(usdc_size) as total_usdc,
               AVG(price) as avg_price,
               GROUP_CONCAT(DISTINCT wallet) as wallets
        FROM whale_trades
        WHERE timestamp > ? AND side = 'BUY'
        GROUP BY slug, outcome
        HAVING whale_count >= 2
        ORDER BY whale_count DESC, total_usdc DESC
        LIMIT 20
        """,
        (cutoff,),
    ).fetchall()

    results = []
    for row in rows:
        wallet_list = row[7].split(",") if row[7] else []
        results.append({
            "slug": row[0],
            "title": row[1],
            "outcome": row[2],
            "side": row[3],
            "whale_count": row[4],
            "total_usdc": row[5],
            "avg_price": row[6],
            "wallets": [_alias(w) for w in wallet_list],
        })
    return results


def print_consensus(conn: sqlite3.Connection, hours: int = 24):
    """Print consensus report."""
    results = compute_consensus(conn, hours)
    if not results:
        print(f"\n  No multi-whale convergence in last {hours}h")
        return

    bold = "\033[1m"
    reset = "\033[0m"
    print(f"\n{bold}=== WHALE CONSENSUS (last {hours}h) ==={reset}")
    print(f"  Markets where 2+ tracked whales are buying the same outcome:\n")
    for r in results:
        names = ", ".join(r["wallets"][:5])
        print(
            f"  [{r['whale_count']} whales] {r['outcome']} @ avg ${r['avg_price']:.2f} "
            f"| ${r['total_usdc']:,.0f} total | {r['title'][:60]}"
        )
        print(f"           Wallets: {names}")
    print()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_monitor(
    wallets: Dict[str, Tuple[str, int]],
    interval: int,
    db_path: Path,
    min_usdc: float,
    category: Optional[str],
):
    """Main monitoring loop. Polls wallets and alerts on new trades."""
    conn = init_db(db_path)
    for addr, (alias, pnl) in wallets.items():
        save_wallet(conn, addr, alias, pnl)

    bold = "\033[1m"
    dim = "\033[2m"
    reset = "\033[0m"

    print(f"\n{bold}=== POLYMARKET WHALE MONITOR ==={reset}")
    print(f"  Tracking: {len(wallets)} wallets")
    print(f"  Interval: {interval}s")
    print(f"  Min trade: ${min_usdc:,.0f}")
    print(f"  Category: {category or 'all'}")
    print(f"  Database: {db_path}")
    print(f"  {dim}Press Ctrl+C to stop{reset}\n")

    # Initial backfill: fetch recent activity for all wallets
    print(f"  Backfilling recent trades...")
    total_backfilled = 0
    for addr in wallets:
        trades = fetch_activity(addr, limit=50)
        for t in trades:
            if save_trade(conn, t, addr):
                total_backfilled += 1
    print(f"  Backfilled {total_backfilled} trades from {len(wallets)} wallets\n")

    # Show consensus from backfill
    print_consensus(conn, hours=24)

    print(f"{bold}--- Live monitoring started at {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')} ---{reset}\n")

    cycle = 0
    try:
        while True:
            cycle += 1
            new_trades = []

            for addr in wallets:
                trades = fetch_activity(addr, limit=10)
                for t in trades:
                    if save_trade(conn, t, addr):
                        usdc = t.get("usdcSize", 0)
                        slug = t.get("slug", "")
                        title = t.get("title", "")

                        if usdc < min_usdc:
                            continue
                        if not _matches_category(slug, title, category):
                            continue

                        new_trades.append((t, addr))

                # Rate limit: don't hammer the API
                time.sleep(0.2)

            if new_trades:
                ts_now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
                print(f"\n  {bold}[{ts_now}] {len(new_trades)} new trade(s):{reset}")
                for t, addr in new_trades:
                    print(format_new_trade(t, addr))

            # Periodic consensus update every 30 cycles
            if cycle % 30 == 0:
                print_consensus(conn, hours=24)

            # Heartbeat every 10 cycles
            if cycle % 10 == 0 and not new_trades:
                ts_now = datetime.now(timezone.utc).strftime("%H:%M:%S")
                print(f"  {dim}[{ts_now}] heartbeat: cycle {cycle}, {len(wallets)} wallets OK{reset}")

            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n\n  Monitor stopped after {cycle} cycles.")
        print_consensus(conn, hours=24)
        conn.close()


def cmd_snapshot(wallets: Dict[str, Tuple[str, int]], db_path: Path, min_usdc: float):
    """One-shot: show current positions for all tracked wallets."""
    conn = init_db(db_path)
    bold = "\033[1m"
    dim = "\033[2m"
    reset = "\033[0m"

    print(f"\n{bold}=== WHALE POSITION SNAPSHOT ==={reset}")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")

    total_positions = 0
    for addr, (alias, lb_pnl) in wallets.items():
        positions = fetch_positions(addr)
        if not positions:
            continue

        # Filter to meaningful positions
        positions = [p for p in positions if abs(p.get("currentValue", 0)) >= min_usdc]
        if not positions:
            continue

        total_value = sum(p.get("currentValue", 0) for p in positions)
        total_pnl = sum(p.get("cashPnl", 0) for p in positions)
        pnl_color = "\033[92m" if total_pnl >= 0 else "\033[91m"

        print(f"  {bold}{alias}{reset} ({addr[:10]}...) "
              f"| LB P&L: ${lb_pnl:,} "
              f"| Open: {len(positions)} positions "
              f"| Value: ${total_value:,.0f} "
              f"| {pnl_color}P&L: ${total_pnl:+,.0f}{reset}")

        # Sort by absolute value descending
        positions.sort(key=lambda p: abs(p.get("currentValue", 0)), reverse=True)
        for pos in positions[:10]:  # Top 10 per wallet
            save_position(conn, pos, addr)
            print(format_position_line(pos, addr))
            total_positions += 1

        print()
        time.sleep(0.3)  # Rate limit

    print(f"  {dim}Total: {total_positions} positions across {len(wallets)} wallets{reset}\n")

    # Also show consensus from stored trades
    print_consensus(conn, hours=24)
    conn.close()


def cmd_history(wallet: str, db_path: Path, limit: int = 50):
    """Show stored trade history for a wallet."""
    conn = init_db(db_path)
    bold = "\033[1m"
    reset = "\033[0m"
    dim = "\033[2m"

    alias = _alias(wallet)
    print(f"\n{bold}=== TRADE HISTORY: {alias} ==={reset}\n")

    rows = conn.execute(
        "SELECT timestamp, side, outcome, price, usdc_size, size, title, slug "
        "FROM whale_trades WHERE wallet = ? ORDER BY timestamp DESC LIMIT ?",
        (wallet.lower(), limit),
    ).fetchall()

    if not rows:
        print(f"  No stored trades for {wallet}")
        conn.close()
        return

    # Stats
    buys = [r for r in rows if r[1] == "BUY"]
    sells = [r for r in rows if r[1] == "SELL"]
    total_buy_usdc = sum(r[4] for r in buys)
    total_sell_usdc = sum(r[4] for r in sells)

    print(f"  Trades: {len(rows)} ({len(buys)} buys, {len(sells)} sells)")
    print(f"  Buy volume:  ${total_buy_usdc:,.0f}")
    print(f"  Sell volume: ${total_sell_usdc:,.0f}")

    # Category breakdown
    cats = {}
    for r in rows:
        cat = _category(r[7], r[6])
        cats[cat] = cats.get(cat, 0) + 1
    cat_str = ", ".join(f"{k}: {v}" for k, v in sorted(cats.items(), key=lambda x: -x[1]))
    print(f"  Categories: {cat_str}\n")

    for r in rows:
        ts_str = _ts_str(r[0])
        side_color = "\033[92m" if r[1] == "BUY" else "\033[91m"
        print(
            f"  {side_color}{r[1]:4s}{reset} | {r[2]:12s} @ ${r[3]:.2f} "
            f"| ${r[4]:>10,.0f} ({r[5]:>10,.0f} sh) "
            f"| {dim}{r[6][:50]}{reset} | {ts_str}"
        )

    conn.close()


def cmd_consensus(db_path: Path, hours: int = 24):
    """Show consensus report from stored data."""
    conn = init_db(db_path)
    print_consensus(conn, hours)
    conn.close()


# ---------------------------------------------------------------------------
# Directionality analysis: directional bettors vs market makers
# ---------------------------------------------------------------------------
def _analyze_wallet_directionality(wallet: str, alias: str) -> dict:
    """
    Fetch all open positions for a wallet and classify by directionality.

    Returns a dict with:
      alias, wallet, total_positions, total_events,
      one_sided_events, hedged_events, one_sided_pct,
      classification (DIRECTIONAL / MIXED / MARKET MAKER),
      biggest_position (dict or None),
      hedged_examples (list of dicts)
    """
    positions = fetch_positions(wallet, size_threshold=0)

    if not positions:
        return {
            "alias": alias,
            "wallet": wallet,
            "total_positions": 0,
            "total_events": 0,
            "one_sided_events": 0,
            "hedged_events": 0,
            "one_sided_pct": None,
            "classification": "NO DATA",
            "biggest_position": None,
            "hedged_examples": [],
        }

    # Group by eventSlug (fall back to conditionId when eventSlug absent)
    events: Dict[str, List[dict]] = {}
    for pos in positions:
        key = pos.get("eventSlug") or pos.get("conditionId", "unknown")
        events.setdefault(key, []).append(pos)

    one_sided_events = 0
    hedged_events = 0
    hedged_examples = []

    for event_key, event_positions in events.items():
        outcomes_held = {p.get("outcome", "").upper() for p in event_positions}
        # Hedged = wallet holds both YES and NO (or any two distinct outcomes)
        if len(outcomes_held) >= 2:
            hedged_events += 1
            # Build a human-readable example
            parts = []
            for p in event_positions:
                outcome = p.get("outcome", "?")
                value = p.get("currentValue", 0)
                parts.append(f"{outcome} ${value:,.0f}")
            hedged_examples.append({
                "title": event_positions[0].get("title", event_key)[:60],
                "legs": parts,
            })
        else:
            one_sided_events += 1

    total_events = one_sided_events + hedged_events
    one_sided_pct = (one_sided_events / total_events * 100) if total_events > 0 else None

    if one_sided_pct is None:
        classification = "NO DATA"
    elif one_sided_pct > 80:
        classification = "DIRECTIONAL"
    elif one_sided_pct >= 50:
        classification = "MIXED"
    else:
        classification = "MARKET MAKER"

    # Biggest position by currentValue
    biggest = max(positions, key=lambda p: abs(p.get("currentValue", 0)), default=None)
    biggest_info = None
    if biggest:
        biggest_info = {
            "outcome": biggest.get("outcome", "?"),
            "value": biggest.get("currentValue", 0),
            "title": biggest.get("title", "?")[:60],
        }

    return {
        "alias": alias,
        "wallet": wallet,
        "total_positions": len(positions),
        "total_events": total_events,
        "one_sided_events": one_sided_events,
        "hedged_events": hedged_events,
        "one_sided_pct": one_sided_pct,
        "classification": classification,
        "biggest_position": biggest_info,
        "hedged_examples": hedged_examples[:3],  # cap at 3 examples
    }


def cmd_directionality(wallets: Dict[str, Tuple[str, int]]):
    """Classify each tracked wallet as directional, mixed, or market maker."""
    bold = "\033[1m"
    dim = "\033[2m"
    reset = "\033[0m"
    green = "\033[92m"
    yellow = "\033[93m"
    red = "\033[91m"

    CLASS_COLOR = {
        "DIRECTIONAL": green,
        "MIXED": yellow,
        "MARKET MAKER": red,
        "NO DATA": dim,
    }

    print(f"\n{bold}=== WHALE DIRECTIONALITY ANALYSIS ==={reset}")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Checking {len(wallets)} wallets for hedged vs one-sided positioning...\n")

    results = []
    for addr, (alias, _lb_pnl) in wallets.items():
        print(f"  {dim}Fetching positions for {alias}...{reset}", end="\r")
        info = _analyze_wallet_directionality(addr, alias)
        results.append(info)
        time.sleep(0.3)  # rate limit

    print(" " * 60, end="\r")  # clear the progress line

    for info in results:
        cls = info["classification"]
        color = CLASS_COLOR.get(cls, reset)
        pct_str = f"{info['one_sided_pct']:.0f}% one-sided" if info["one_sided_pct"] is not None else "no data"
        short_wallet = info["wallet"][:10] + "..."

        print(f"{bold}{info['alias']}{reset} ({short_wallet})")
        print(f"  Type: {color}{bold}{cls}{reset} ({pct_str})")

        if info["total_positions"] > 0:
            print(
                f"  Positions: {info['total_positions']} total across "
                f"{info['total_events']} events"
            )
            print(
                f"  One-sided: {info['one_sided_events']} | "
                f"Hedged: {info['hedged_events']}"
            )

        if info["biggest_position"]:
            bp = info["biggest_position"]
            # Flag whether a hedge was detected on the biggest event
            hedge_note = ""
            for ex in info["hedged_examples"]:
                if ex["title"][:40] in bp["title"] or bp["title"][:40] in ex["title"]:
                    hedge_note = " (hedge detected)"
                    break
            print(
                f"  Biggest position: {bp['outcome']} ${bp['value']:,.0f}"
                f"{hedge_note} - {dim}{bp['title']}{reset}"
            )

        for ex in info["hedged_examples"]:
            legs_str = " + ".join(ex["legs"])
            print(f"  {yellow}Hedged:{reset} {dim}{ex['title']}{reset} ({legs_str})")

        print()

    # Summary buckets
    directional = [r["alias"] for r in results if r["classification"] == "DIRECTIONAL"]
    mixed = [r["alias"] for r in results if r["classification"] == "MIXED"]
    market_makers = [r["alias"] for r in results if r["classification"] == "MARKET MAKER"]
    no_data = [r["alias"] for r in results if r["classification"] == "NO DATA"]

    print(f"{bold}SUMMARY:{reset}")
    if directional:
        print(f"  {green}Directional (safe to follow):{reset} {', '.join(directional)}")
    if mixed:
        print(f"  {yellow}Mixed (follow with caution):{reset} {', '.join(mixed)}")
    if market_makers:
        print(f"  {red}Market Maker (DO NOT copy):{reset} {', '.join(market_makers)}")
    if no_data:
        print(f"  {dim}No open positions:{reset} {', '.join(no_data)}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_wallets(spec: str) -> Dict[str, Tuple[str, int]]:
    """Parse wallet specification: 'top10', 'top20', or comma-separated addresses."""
    if spec.startswith("top"):
        n = int(spec[3:]) if len(spec) > 3 else 10
        items = list(LEADERBOARD_WALLETS.items())[:n]
        return dict(items)
    elif "0x" in spec:
        addrs = [a.strip().lower() for a in spec.split(",")]
        result = {}
        for addr in addrs:
            if addr in LEADERBOARD_WALLETS:
                result[addr] = LEADERBOARD_WALLETS[addr]
            else:
                result[addr] = (addr[:10] + "...", 0)
        return result
    else:
        # Default top 10
        items = list(LEADERBOARD_WALLETS.items())[:10]
        return dict(items)


def main():
    parser = argparse.ArgumentParser(
        description="Polymarket Whale Monitor - track profitable wallets",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--wallets", default="top10",
        help="'top10', 'top20', or comma-separated 0x addresses (default: top10)",
    )
    parser.add_argument(
        "--interval", type=int, default=10,
        help="Poll interval in seconds (default: 10)",
    )
    parser.add_argument(
        "--min-usdc", type=float, default=500,
        help="Minimum trade size in USDC to alert on (default: 500)",
    )
    parser.add_argument(
        "--categories", default=None,
        choices=["crypto", "sports", "politics", "other"],
        help="Filter to specific market category",
    )
    parser.add_argument(
        "--db", default=str(DEFAULT_DB),
        help=f"SQLite database path (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--snapshot", action="store_true",
        help="One-shot: show current positions for all wallets, then exit",
    )
    parser.add_argument(
        "--history", metavar="WALLET",
        help="Show stored trade history for a specific wallet address",
    )
    parser.add_argument(
        "--consensus", action="store_true",
        help="Show whale consensus report from stored data",
    )
    parser.add_argument(
        "--consensus-hours", type=int, default=24,
        help="Hours to look back for consensus (default: 24)",
    )
    parser.add_argument(
        "--directionality", action="store_true",
        help=(
            "Classify each tracked wallet as DIRECTIONAL (>80%% one-sided), "
            "MIXED (50-80%%), or MARKET MAKER (<50%%). "
            "Market makers hedge on external books - DO NOT copy them blindly."
        ),
    )

    args = parser.parse_args()
    wallets = parse_wallets(args.wallets)
    db_path = Path(args.db)

    if args.history:
        cmd_history(args.history.lower(), db_path)
    elif args.consensus:
        cmd_consensus(db_path, args.consensus_hours)
    elif args.directionality:
        cmd_directionality(wallets)
    elif args.snapshot:
        cmd_snapshot(wallets, db_path, args.min_usdc)
    else:
        cmd_monitor(wallets, args.interval, db_path, args.min_usdc, args.categories)


if __name__ == "__main__":
    main()
