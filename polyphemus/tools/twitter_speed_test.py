#!/usr/bin/env python3
"""Twitter/X Sports News Speed Tester - measures repricing lag on Polymarket sports markets.

Monitors NBA beat reporters for injury/lineup news, records tweet timestamps,
and measures how quickly Polymarket prices move in response.

Usage:
    python3 twitter_speed_test.py manual                    # Process manual feed file
    python3 twitter_speed_test.py monitor                   # API polling mode (needs TWITTER_BEARER_TOKEN)
    python3 twitter_speed_test.py monitor --accounts nba    # NBA reporters only
    python3 twitter_speed_test.py report                    # Show speed test results
    python3 twitter_speed_test.py add "1712345678 ShamsCharania Giannis ruled out tonight vs Nets, knee soreness"

Manual feed format (data/twitter_feed.log):
    {unix_timestamp} {account} {tweet_text}
    Example: 1712345678 ShamsCharania Giannis ruled out tonight vs Nets, knee soreness

No external dependencies. Twitter API Bearer token optional (env: TWITTER_BEARER_TOKEN).
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Beat reporter seed list
# ---------------------------------------------------------------------------
BEAT_REPORTERS: Dict[str, dict] = {
    # NBA national
    "ShamsCharania": {"name": "Shams Charania", "sport": "nba", "priority": 1},
    "wojespn": {"name": "Adrian Wojnarowski", "sport": "nba", "priority": 1},
    "ChrisBHaynes": {"name": "Chris Haynes", "sport": "nba", "priority": 1},
    "KeithSmithNBA": {"name": "Keith Smith", "sport": "nba", "priority": 2},
    "JakeLFischer": {"name": "Jake Fischer", "sport": "nba", "priority": 2},
    "MarcJSpears": {"name": "Marc J. Spears", "sport": "nba", "priority": 2},
    # NBA team-specific
    "MikeAScotto": {"name": "Mike Scotto", "sport": "nba", "priority": 2},
    "TheSteinLine": {"name": "Marc Stein", "sport": "nba", "priority": 1},
    "TimBontemps": {"name": "Tim Bontemps", "sport": "nba", "priority": 2},
    "WindhorstESPN": {"name": "Brian Windhorst", "sport": "nba", "priority": 2},
    # NFL
    "AdamSchefter": {"name": "Adam Schefter", "sport": "nfl", "priority": 1},
    "RapSheet": {"name": "Ian Rapoport", "sport": "nfl", "priority": 1},
    "TomPelissero": {"name": "Tom Pelissero", "sport": "nfl", "priority": 2},
    # Soccer
    "FabrizioRomano": {"name": "Fabrizio Romano", "sport": "soccer", "priority": 1},
    "David_Ornstein": {"name": "David Ornstein", "sport": "soccer", "priority": 1},
}

# ---------------------------------------------------------------------------
# Keyword detection
# ---------------------------------------------------------------------------
INJURY_KEYWORDS = [
    "out tonight", "will not play", "ruled out", "doubtful", "questionable",
    "day-to-day", "injury report", "dnp", "will miss", "sidelined",
    "knee", "ankle", "hamstring", "concussion", "illness", "rest",
    "upgraded to", "downgraded to", "available", "will play",
    "starting lineup", "will start", "benched", "load management",
    "scratched", "inactive", "game-time decision",
]

# Keywords that indicate a player is returning (positive direction)
POSITIVE_KEYWORDS = [
    "upgraded to", "available", "will play", "will start", "starting lineup",
    "off injury report", "cleared",
]

# Keywords that indicate a player is out (negative direction)
NEGATIVE_KEYWORDS = [
    "out tonight", "will not play", "ruled out", "doubtful", "questionable",
    "day-to-day", "dnp", "will miss", "sidelined", "scratched", "inactive",
    "benched", "concussion", "illness",
]

# Injury severity classification
SEVERITY_MAP = {
    "out": ["ruled out", "out tonight", "will not play", "dnp", "scratched", "inactive"],
    "questionable": ["questionable", "game-time decision", "doubtful", "day-to-day"],
    "available": ["available", "will play", "upgraded to", "will start", "cleared"],
    "rest": ["rest", "load management", "benched"],
    "injury": ["knee", "ankle", "hamstring", "concussion", "illness", "sidelined", "will miss"],
}

# ---------------------------------------------------------------------------
# Team aliases for Polymarket market matching
# ---------------------------------------------------------------------------
TEAM_ALIASES: Dict[str, List[str]] = {
    "lakers": ["lakers", "los angeles lakers", "la lakers"],
    "celtics": ["celtics", "boston celtics"],
    "warriors": ["warriors", "golden state warriors", "gsw"],
    "bucks": ["bucks", "milwaukee bucks"],
    "heat": ["heat", "miami heat"],
    "76ers": ["sixers", "76ers", "philadelphia 76ers", "philly"],
    "nuggets": ["nuggets", "denver nuggets"],
    "suns": ["suns", "phoenix suns"],
    "clippers": ["clippers", "la clippers", "los angeles clippers"],
    "nets": ["nets", "brooklyn nets"],
    "knicks": ["knicks", "new york knicks"],
    "bulls": ["bulls", "chicago bulls"],
    "hawks": ["hawks", "atlanta hawks"],
    "cavaliers": ["cavaliers", "cavs", "cleveland cavaliers"],
    "pistons": ["pistons", "detroit pistons"],
    "pacers": ["pacers", "indiana pacers"],
    "raptors": ["raptors", "toronto raptors"],
    "wizards": ["wizards", "washington wizards"],
    "hornets": ["hornets", "charlotte hornets"],
    "magic": ["magic", "orlando magic"],
    "thunder": ["thunder", "oklahoma city thunder", "okc"],
    "jazz": ["jazz", "utah jazz"],
    "grizzlies": ["grizzlies", "memphis grizzlies"],
    "pelicans": ["pelicans", "new orleans pelicans"],
    "spurs": ["spurs", "san antonio spurs"],
    "mavericks": ["mavericks", "mavs", "dallas mavericks"],
    "rockets": ["rockets", "houston rockets"],
    "kings": ["kings", "sacramento kings"],
    "trailblazers": ["trail blazers", "blazers", "portland trail blazers"],
    "timberwolves": ["timberwolves", "wolves", "minnesota timberwolves"],
    # NFL
    "chiefs": ["chiefs", "kansas city chiefs"],
    "eagles": ["eagles", "philadelphia eagles"],
    "49ers": ["49ers", "san francisco 49ers", "niners"],
    "cowboys": ["cowboys", "dallas cowboys"],
    "bills": ["bills", "buffalo bills"],
    "ravens": ["ravens", "baltimore ravens"],
    "bengals": ["bengals", "cincinnati bengals"],
    "packers": ["packers", "green bay packers"],
}

# High-profile players for entity extraction
PLAYER_NAMES = [
    "lebron", "steph curry", "kevin durant", "giannis", "luka doncic",
    "jayson tatum", "joel embiid", "nikola jokic", "damian lillard",
    "anthony davis", "kawhi leonard", "paul george", "james harden",
    "kyrie irving", "devin booker", "trae young", "zion williamson",
    "victor wembanyama", "shai gilgeous-alexander", "sga",
    "patrick mahomes", "josh allen", "lamar jackson", "joe burrow",
    "jalen hurts", "dak prescott", "brock purdy",
]

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------
DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
TWITTER_API = "https://api.twitter.com/2"
USER_AGENT = "PolyphemusSpeedTest/1.0"

DEFAULT_DB = Path(__file__).parent.parent / "data" / "twitter_speed_test.db"
DEFAULT_FEED = Path(__file__).parent.parent / "data" / "twitter_feed.log"

# Price check intervals in seconds after tweet
PRICE_CHECK_INTERVALS = [120, 300, 600, 1800]  # 2, 5, 10, 30 minutes

# How long to wait before marking repricing "complete" (seconds)
MAX_REPRICE_WAIT = 3600  # 1 hour


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------
def _http_get(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    retries: int = 2,
    timeout: int = 10,
) -> Optional[dict]:
    """GET request returning parsed JSON or None on failure."""
    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError) as exc:
            if attempt < retries:
                time.sleep(1)
            else:
                print(f"  [HTTP ERROR] {url}: {exc}", file=sys.stderr)
                return None
    return None


def fetch_polymarket_markets(query: str, limit: int = 10) -> List[dict]:
    """Search Polymarket for active sports markets matching a query."""
    encoded = urllib.request.quote(query)
    url = f"{GAMMA_API}/markets?_q={encoded}&active=true&closed=false&limit={limit}"
    result = _http_get(url)
    if isinstance(result, list):
        return result
    if isinstance(result, dict) and "data" in result:
        return result["data"]
    return []


def fetch_polymarket_price(condition_id: str) -> Optional[float]:
    """Fetch current best YES price for a Polymarket market by condition ID."""
    url = f"{DATA_API}/prices-history?market={condition_id}&interval=1m&fidelity=1"
    result = _http_get(url)
    if result and isinstance(result, dict) and "history" in result:
        history = result["history"]
        if history:
            return float(history[-1].get("p", 0))
    # Fallback: try positions endpoint for price
    url2 = f"{DATA_API}/markets?condition_id={condition_id}"
    result2 = _http_get(url2)
    if result2 and isinstance(result2, list) and result2:
        market = result2[0]
        tokens = market.get("tokens", [])
        for tok in tokens:
            if tok.get("outcome", "").lower() == "yes":
                return float(tok.get("price", 0))
    return None


def fetch_polymarket_price_by_slug(slug: str) -> Optional[Tuple[str, float, str]]:
    """Fetch YES price for a market by slug. Returns (slug, price, outcome) or None."""
    url = f"{GAMMA_API}/markets?slug={urllib.request.quote(slug)}"
    result = _http_get(url)
    markets = result if isinstance(result, list) else (result.get("data", []) if result else [])
    for market in markets:
        tokens = market.get("tokens", [])
        cond_id = market.get("conditionId", "")
        for tok in tokens:
            if tok.get("outcome", "").lower() == "yes":
                price = float(tok.get("price", 0))
                return (slug, price, "Yes")
    return None


# ---------------------------------------------------------------------------
# Twitter API (Mode 2)
# ---------------------------------------------------------------------------
def get_twitter_headers() -> Optional[Dict[str, str]]:
    """Build Twitter API v2 auth headers from env. Returns None if not configured."""
    token = os.environ.get("TWITTER_BEARER_TOKEN", "").strip()
    if not token:
        return None
    return {"Authorization": f"Bearer {token}"}


def resolve_twitter_user_id(handle: str, headers: Dict[str, str]) -> Optional[str]:
    """Resolve a Twitter handle to a numeric user ID."""
    url = f"{TWITTER_API}/users/by/username/{handle}"
    result = _http_get(url, headers=headers)
    if result and "data" in result:
        return result["data"].get("id")
    return None


def fetch_recent_tweets(user_id: str, headers: Dict[str, str], since_id: Optional[str] = None) -> List[dict]:
    """Fetch recent tweets for a user via Twitter API v2."""
    params = f"max_results=10&tweet.fields=created_at,text&exclude=retweets,replies"
    if since_id:
        params += f"&since_id={since_id}"
    url = f"{TWITTER_API}/users/{user_id}/tweets?{params}"
    result = _http_get(url, headers=headers)
    if result and "data" in result:
        return result["data"]
    return []


# ---------------------------------------------------------------------------
# Text analysis
# ---------------------------------------------------------------------------
def detect_injury_keywords(text: str) -> bool:
    """Return True if tweet text contains injury/lineup keywords."""
    lower = text.lower()
    return any(kw in lower for kw in INJURY_KEYWORDS)


def classify_severity(text: str) -> str:
    """Classify the injury type from tweet text."""
    lower = text.lower()
    for severity, keywords in SEVERITY_MAP.items():
        if any(kw in lower for kw in keywords):
            return severity
    return "injury"


def classify_direction(text: str) -> str:
    """Classify impact direction: 'negative' (player out) or 'positive' (player returning)."""
    lower = text.lower()
    if any(kw in lower for kw in POSITIVE_KEYWORDS):
        return "positive"
    if any(kw in lower for kw in NEGATIVE_KEYWORDS):
        return "negative"
    return "unknown"


def extract_player(text: str) -> Optional[str]:
    """Attempt to extract the primary player name from tweet text."""
    lower = text.lower()
    for player in PLAYER_NAMES:
        if player in lower:
            # Capitalize properly
            idx = lower.find(player)
            return text[idx: idx + len(player)].title()
    # Fallback: first capitalized word sequence after common openers
    match = re.search(r'\b([A-Z][a-z]+ [A-Z][a-z]+)\b', text)
    if match:
        return match.group(1)
    return None


def extract_team(text: str) -> Optional[str]:
    """Attempt to extract a team name from tweet text."""
    lower = text.lower()
    for team_key, aliases in TEAM_ALIASES.items():
        if any(alias in lower for alias in aliases):
            return team_key
    return None


def find_matching_market(player: Optional[str], team: Optional[str]) -> Optional[Tuple[str, str, str]]:
    """
    Search Polymarket for a market matching the player/team.
    Returns (slug, condition_id, outcome) or None.
    """
    queries = []
    if player:
        queries.append(player)
    if team:
        queries.append(team + " win")
    if not queries:
        return None

    for q in queries:
        markets = fetch_polymarket_markets(q)
        for market in markets:
            # Prefer NBA/NFL game markets
            title = market.get("question", market.get("title", "")).lower()
            slug = market.get("slug", market.get("marketSlug", ""))
            cond_id = market.get("conditionId", "")
            if not slug or not cond_id:
                continue
            # Look for moneyline/winner markets
            if any(kw in title for kw in ["win", "winner", "moneyline", "spread", "cover"]):
                return (slug, cond_id, "Yes")
    return None


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def init_db(db_path: Path) -> sqlite3.Connection:
    """Initialize SQLite database for speed test storage."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS injury_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detected_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            tweet_timestamp INTEGER,
            account TEXT,
            tweet_text TEXT,
            player_name TEXT,
            team TEXT,
            sport TEXT,
            injury_type TEXT,
            impact_direction TEXT,
            polymarket_slug TEXT,
            polymarket_condition_id TEXT,
            polymarket_outcome TEXT,
            pre_news_price REAL,
            price_2min REAL,
            price_5min REAL,
            price_10min REAL,
            price_30min REAL,
            price_move_1c_secs INTEGER,
            price_move_3c_secs INTEGER,
            max_price_move REAL,
            repricing_lag_secs INTEGER,
            source TEXT DEFAULT 'manual'
        );

        CREATE TABLE IF NOT EXISTS price_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER,
            checked_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            secs_after_tweet INTEGER,
            price REAL,
            FOREIGN KEY (event_id) REFERENCES injury_events(id)
        );

        CREATE TABLE IF NOT EXISTS twitter_user_ids (
            handle TEXT PRIMARY KEY,
            user_id TEXT,
            resolved_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );

        CREATE TABLE IF NOT EXISTS api_poll_state (
            handle TEXT PRIMARY KEY,
            last_tweet_id TEXT,
            last_polled TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_events_account ON injury_events(account);
        CREATE INDEX IF NOT EXISTS idx_events_ts ON injury_events(tweet_timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_checks_event ON price_checks(event_id);
    """)
    conn.commit()
    return conn


def insert_event(conn: sqlite3.Connection, event: dict) -> int:
    """Insert a new injury event. Returns the new row ID."""
    cur = conn.execute(
        """INSERT INTO injury_events
           (tweet_timestamp, account, tweet_text, player_name, team, sport,
            injury_type, impact_direction, polymarket_slug, polymarket_condition_id,
            polymarket_outcome, pre_news_price, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event.get("tweet_timestamp"),
            event.get("account"),
            event.get("tweet_text"),
            event.get("player_name"),
            event.get("team"),
            event.get("sport"),
            event.get("injury_type"),
            event.get("impact_direction"),
            event.get("polymarket_slug"),
            event.get("polymarket_condition_id"),
            event.get("polymarket_outcome"),
            event.get("pre_news_price"),
            event.get("source", "manual"),
        ),
    )
    conn.commit()
    return cur.lastrowid


def insert_price_check(conn: sqlite3.Connection, event_id: int, secs_after: int, price: float):
    """Record a price check for a given event."""
    conn.execute(
        "INSERT INTO price_checks (event_id, secs_after_tweet, price) VALUES (?, ?, ?)",
        (event_id, secs_after, price),
    )
    conn.commit()


def update_event_prices(conn: sqlite3.Connection, event_id: int, updates: dict):
    """Update price columns and computed lag fields for an event."""
    set_clauses = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [event_id]
    conn.execute(f"UPDATE injury_events SET {set_clauses} WHERE id = ?", values)
    conn.commit()


def load_pending_events(conn: sqlite3.Connection) -> List[dict]:
    """Load events that still need price check follow-ups."""
    now_ts = int(time.time())
    # Events within the last hour with incomplete price checks
    cutoff = now_ts - MAX_REPRICE_WAIT
    rows = conn.execute(
        """SELECT id, tweet_timestamp, polymarket_condition_id, pre_news_price,
                  price_2min, price_5min, price_10min, price_30min
           FROM injury_events
           WHERE polymarket_condition_id IS NOT NULL
             AND tweet_timestamp > ?
             AND (price_30min IS NULL OR price_10min IS NULL OR price_5min IS NULL OR price_2min IS NULL)
           ORDER BY tweet_timestamp DESC""",
        (cutoff,),
    ).fetchall()
    keys = ["id", "tweet_timestamp", "condition_id", "pre_news_price",
            "price_2min", "price_5min", "price_10min", "price_30min"]
    return [dict(zip(keys, row)) for row in rows]


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------
def process_tweet(
    conn: sqlite3.Connection,
    tweet_ts: int,
    account: str,
    tweet_text: str,
    source: str = "manual",
) -> Optional[int]:
    """
    Process a single tweet. Detects injury keywords, finds Polymarket market,
    fetches pre-news price, stores event. Returns event ID or None if not relevant.
    """
    if not detect_injury_keywords(tweet_text):
        return None

    sport = BEAT_REPORTERS.get(account, {}).get("sport", "nba")
    injury_type = classify_severity(tweet_text)
    direction = classify_direction(tweet_text)
    player = extract_player(tweet_text)
    team = extract_team(tweet_text)

    # Try to find a Polymarket market
    slug = None
    condition_id = None
    outcome = None
    pre_price = None

    market_info = find_matching_market(player, team)
    if market_info:
        slug, condition_id, outcome = market_info
        pre_price = fetch_polymarket_price(condition_id)

    event = {
        "tweet_timestamp": tweet_ts,
        "account": account,
        "tweet_text": tweet_text,
        "player_name": player,
        "team": team,
        "sport": sport,
        "injury_type": injury_type,
        "impact_direction": direction,
        "polymarket_slug": slug,
        "polymarket_condition_id": condition_id,
        "polymarket_outcome": outcome,
        "pre_news_price": pre_price,
        "source": source,
    }

    event_id = insert_event(conn, event)

    # If we have a price, record it as time-zero check
    if pre_price is not None:
        insert_price_check(conn, event_id, 0, pre_price)

    return event_id


def run_price_followups(conn: sqlite3.Connection, verbose: bool = False):
    """
    For all pending events, check if it is time for any of the scheduled
    price check intervals and fetch/store prices accordingly.
    """
    pending = load_pending_events(conn)
    if not pending:
        return

    now_ts = int(time.time())
    interval_col = {120: "price_2min", 300: "price_5min", 600: "price_10min", 1800: "price_30min"}

    for ev in pending:
        tweet_ts = ev["tweet_timestamp"]
        cond_id = ev["condition_id"]
        updates = {}
        pre_price = ev["pre_news_price"]

        for secs, col in interval_col.items():
            if ev[col] is not None:
                continue  # Already have this check
            if now_ts < tweet_ts + secs:
                continue  # Not time yet

            price = fetch_polymarket_price(cond_id)
            if price is None:
                continue

            updates[col] = price
            insert_price_check(conn, ev["id"], secs, price)
            if verbose:
                print(f"  [PRICE CHECK] event {ev['id']} @ +{secs}s: {price:.4f}")

        if updates and pre_price is not None:
            # Compute move metrics from all available prices
            all_prices = {}
            for secs, col in interval_col.items():
                p = updates.get(col) or ev.get(col)
                if p is not None:
                    all_prices[secs] = p

            if all_prices:
                moves = {s: abs(p - pre_price) for s, p in all_prices.items()}
                max_move = max(moves.values()) if moves else 0
                updates["max_price_move"] = round(max_move, 4)

                # First interval where price moved > 1 cent
                move_1c = next((s for s, m in sorted(moves.items()) if m >= 0.01), None)
                move_3c = next((s for s, m in sorted(moves.items()) if m >= 0.03), None)
                if move_1c is not None:
                    updates["price_move_1c_secs"] = move_1c
                if move_3c is not None:
                    updates["price_move_3c_secs"] = move_3c

                # repricing_lag: time until market moved >3c (or last checked interval if not yet)
                if move_3c is not None:
                    updates["repricing_lag_secs"] = move_3c
                elif all_prices:
                    updates["repricing_lag_secs"] = max(all_prices.keys())

            update_event_prices(conn, ev["id"], updates)

        time.sleep(0.1)  # Rate limit


# ---------------------------------------------------------------------------
# Manual feed parser
# ---------------------------------------------------------------------------
def parse_feed_line(line: str) -> Optional[Tuple[int, str, str]]:
    """
    Parse a manual feed line: '{unix_ts} {account} {tweet_text}'
    Returns (timestamp, account, text) or None if invalid.
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    parts = line.split(" ", 2)
    if len(parts) < 3:
        return None
    ts_str, account, text = parts
    try:
        ts = int(ts_str)
    except ValueError:
        return None
    return (ts, account, text)


def cmd_manual(db_path: Path, feed_path: Path, verbose: bool = False):
    """Process manual feed file and correlate with Polymarket prices."""
    conn = init_db(db_path)

    if not feed_path.exists():
        # Create an example feed file
        feed_path.parent.mkdir(parents=True, exist_ok=True)
        feed_path.write_text(
            "# Twitter Speed Test - Manual Feed\n"
            "# Format: {unix_timestamp} {account} {tweet_text}\n"
            "# Example:\n"
            "# 1712345678 ShamsCharania Giannis ruled out tonight vs Nets, knee soreness\n"
            "# 1712345700 wojespn LeBron James listed as questionable with ankle issue\n"
        )
        print(f"  Created example feed at {feed_path}")
        print(f"  Add tweets in format: {{unix_ts}} {{account}} {{tweet_text}}")
        print(f"  Then run: python3 twitter_speed_test.py manual")
        conn.close()
        return

    lines = feed_path.read_text().splitlines()
    processed = 0
    matched = 0
    skipped = 0

    bold = "\033[1m"
    dim = "\033[2m"
    green = "\033[92m"
    reset = "\033[0m"

    print(f"\n{bold}=== PROCESSING MANUAL FEED ==={reset}")
    print(f"  Feed: {feed_path}")
    print(f"  Lines: {len(lines)}\n")

    for line in lines:
        parsed = parse_feed_line(line)
        if not parsed:
            continue

        ts, account, text = parsed
        processed += 1

        if verbose:
            ts_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            print(f"  [{ts_str}] @{account}: {text[:80]}")

        event_id = process_tweet(conn, ts, account, text, source="manual")
        if event_id:
            matched += 1
            player = extract_player(text)
            team = extract_team(text)
            if verbose:
                print(f"    {green}-> Event #{event_id} | player={player} | team={team}{reset}")
        else:
            skipped += 1
            if verbose:
                print(f"    {dim}-> No injury keywords detected, skipped{reset}")

    print(f"  Processed: {processed} tweets")
    print(f"  Injury events: {matched}")
    print(f"  Skipped (no keywords): {skipped}")

    # Run any due price checks
    print(f"\n  Running price follow-ups...")
    run_price_followups(conn, verbose=verbose)

    conn.close()
    print(f"\n  Run 'python3 twitter_speed_test.py report' to see results.")


# ---------------------------------------------------------------------------
# API polling mode (Mode 2)
# ---------------------------------------------------------------------------
def cmd_monitor(
    db_path: Path,
    sport_filter: Optional[str] = None,
    poll_interval: int = 30,
):
    """API polling mode. Requires TWITTER_BEARER_TOKEN env var."""
    headers = get_twitter_headers()
    if not headers:
        print("ERROR: TWITTER_BEARER_TOKEN environment variable not set.", file=sys.stderr)
        print("  Set it with: export TWITTER_BEARER_TOKEN=your_token_here", file=sys.stderr)
        print("  Or use manual mode: python3 twitter_speed_test.py manual", file=sys.stderr)
        sys.exit(1)

    conn = init_db(db_path)

    bold = "\033[1m"
    dim = "\033[2m"
    green = "\033[92m"
    reset = "\033[0m"

    # Filter reporters by sport
    reporters = BEAT_REPORTERS
    if sport_filter:
        reporters = {k: v for k, v in BEAT_REPORTERS.items() if v["sport"] == sport_filter}

    print(f"\n{bold}=== TWITTER SPEED TEST - API MONITOR ==={reset}")
    print(f"  Reporters: {len(reporters)} accounts")
    print(f"  Sport filter: {sport_filter or 'all'}")
    print(f"  Poll interval: {poll_interval}s")
    print(f"  Database: {db_path}")
    print(f"  {dim}Press Ctrl+C to stop{reset}\n")

    # Resolve user IDs (cache in DB)
    print("  Resolving Twitter user IDs...")
    user_ids: Dict[str, str] = {}
    for handle in reporters:
        # Check cache first
        row = conn.execute(
            "SELECT user_id FROM twitter_user_ids WHERE handle = ?", (handle,)
        ).fetchone()
        if row:
            user_ids[handle] = row[0]
            continue
        uid = resolve_twitter_user_id(handle, headers)
        if uid:
            user_ids[handle] = uid
            conn.execute(
                "INSERT OR REPLACE INTO twitter_user_ids (handle, user_id) VALUES (?, ?)",
                (handle, uid),
            )
            conn.commit()
            print(f"    @{handle} -> {uid}")
        else:
            print(f"    @{handle} -> FAILED to resolve", file=sys.stderr)
        time.sleep(1)  # Rate limit ID resolution

    print(f"  Resolved {len(user_ids)} of {len(reporters)} accounts\n")

    # Load last seen tweet IDs
    since_ids: Dict[str, Optional[str]] = {}
    for handle in user_ids:
        row = conn.execute(
            "SELECT last_tweet_id FROM api_poll_state WHERE handle = ?", (handle,)
        ).fetchone()
        since_ids[handle] = row[0] if row else None

    print(f"  Starting poll loop (every {poll_interval}s per account)...\n")
    cycle = 0

    try:
        while True:
            cycle += 1
            new_events = 0

            for handle, uid in user_ids.items():
                since_id = since_ids.get(handle)
                tweets = fetch_recent_tweets(uid, headers, since_id)

                for tweet in tweets:
                    tweet_id = tweet.get("id", "")
                    tweet_text = tweet.get("text", "")
                    created_at = tweet.get("created_at", "")

                    # Parse ISO timestamp
                    try:
                        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        tweet_ts = int(dt.timestamp())
                    except (ValueError, AttributeError):
                        tweet_ts = int(time.time())

                    event_id = process_tweet(conn, tweet_ts, handle, tweet_text, source="api")
                    if event_id:
                        new_events += 1
                        player = extract_player(tweet_text)
                        team = extract_team(tweet_text)
                        ts_str = datetime.fromtimestamp(tweet_ts, tz=timezone.utc).strftime("%H:%M:%S UTC")
                        print(
                            f"  {green}[ALERT]{reset} @{handle} | {ts_str}"
                            f"\n    {tweet_text[:100]}"
                            f"\n    player={player} team={team} event=#{event_id}\n"
                        )

                    # Track newest tweet ID
                    if tweet_id:
                        prev = since_ids.get(handle)
                        if prev is None or int(tweet_id) > int(prev):
                            since_ids[handle] = tweet_id
                            conn.execute(
                                "INSERT OR REPLACE INTO api_poll_state (handle, last_tweet_id, last_polled) "
                                "VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))",
                                (handle, tweet_id),
                            )
                            conn.commit()

                # Rate limit: ~1 req per account per poll_interval
                time.sleep(poll_interval / max(len(user_ids), 1))

            # Run price follow-ups every cycle
            run_price_followups(conn, verbose=False)

            if cycle % 10 == 0:
                ts_now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
                print(f"  {dim}[{ts_now}] heartbeat: cycle {cycle}, {new_events} new events{reset}")

    except KeyboardInterrupt:
        print(f"\n  Monitor stopped after {cycle} cycles.")
        conn.close()


# ---------------------------------------------------------------------------
# Add command (single tweet ingestion)
# ---------------------------------------------------------------------------
def cmd_add(db_path: Path, raw: str):
    """Add a single tweet string directly (bypasses feed file)."""
    conn = init_db(db_path)
    parsed = parse_feed_line(raw)
    if not parsed:
        print(f"ERROR: Could not parse input. Expected: '{{unix_ts}} {{account}} {{tweet_text}}'", file=sys.stderr)
        sys.exit(1)

    ts, account, text = parsed

    green = "\033[92m"
    reset = "\033[0m"
    dim = "\033[2m"

    ts_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\n  Tweet: [{ts_str}] @{account}: {text}")

    event_id = process_tweet(conn, ts, account, text, source="manual")
    if event_id:
        player = extract_player(text)
        team = extract_team(text)
        row = conn.execute(
            "SELECT polymarket_slug, pre_news_price, injury_type, impact_direction "
            "FROM injury_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        slug, price, inj_type, direction = row if row else (None, None, None, None)

        print(f"  {green}Recorded event #{event_id}{reset}")
        print(f"    Player: {player or 'unknown'}")
        print(f"    Team: {team or 'unknown'}")
        print(f"    Injury type: {inj_type}")
        print(f"    Direction: {direction}")
        if slug:
            print(f"    Market: {slug}")
            print(f"    Pre-news price: {price:.4f}" if price else "    Pre-news price: N/A")
        else:
            print(f"    {dim}No matching Polymarket market found{reset}")
        print(f"\n  Price checks scheduled at: 2min, 5min, 10min, 30min")
        print(f"  Run 'python3 twitter_speed_test.py manual' again to collect follow-up prices.")
    else:
        print(f"  No injury keywords detected - event not recorded.")
    conn.close()


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def _fmt_secs(secs: Optional[float]) -> str:
    """Format seconds into 'Xm Ys' string."""
    if secs is None:
        return "N/A"
    secs = int(secs)
    m, s = divmod(secs, 60)
    if m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def cmd_report(db_path: Path):
    """Print speed test results report."""
    conn = init_db(db_path)

    bold = "\033[1m"
    green = "\033[92m"
    yellow = "\033[93m"
    red = "\033[91m"
    dim = "\033[2m"
    reset = "\033[0m"

    # Overall stats
    total = conn.execute("SELECT COUNT(*) FROM injury_events").fetchone()[0]
    matched = conn.execute(
        "SELECT COUNT(*) FROM injury_events WHERE polymarket_slug IS NOT NULL"
    ).fetchone()[0]
    with_price = conn.execute(
        "SELECT COUNT(*) FROM injury_events WHERE pre_news_price IS NOT NULL"
    ).fetchone()[0]
    with_lag = conn.execute(
        "SELECT COUNT(*) FROM injury_events WHERE repricing_lag_secs IS NOT NULL"
    ).fetchone()[0]

    print(f"\n{bold}=== TWITTER SPEED TEST REPORT ==={reset}")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")

    print(f"  Events tracked:           {total}")
    print(f"  With matching market:     {matched}")
    print(f"  With pre-news price:      {with_price}")
    print(f"  With repricing data:      {with_lag}")

    if with_lag == 0:
        print(f"\n  {yellow}No repricing data yet. Events need 30+ minutes of follow-up prices.{reset}")
        print(f"  Re-run 'python3 twitter_speed_test.py manual' after 30 minutes to collect prices.")
        _show_recent_events(conn, bold, dim, green, reset)
        conn.close()
        return

    # Overall lag stats
    lag_rows = conn.execute(
        "SELECT repricing_lag_secs, max_price_move, price_move_1c_secs, price_move_3c_secs "
        "FROM injury_events WHERE repricing_lag_secs IS NOT NULL"
    ).fetchall()
    lags = [r[0] for r in lag_rows if r[0]]
    moves = [r[1] for r in lag_rows if r[1]]
    move1c = [r[2] for r in lag_rows if r[2]]

    if lags:
        avg_lag = sum(lags) / len(lags)
        sorted_lags = sorted(lags)
        med_lag = sorted_lags[len(sorted_lags) // 2]
        avg_move = sum(moves) / len(moves) if moves else 0
        avg_1c = sum(move1c) / len(move1c) if move1c else None

        print(f"\n  Avg repricing lag:        {_fmt_secs(avg_lag)}")
        print(f"  Median repricing lag:     {_fmt_secs(med_lag)}")
        print(f"  Avg max price move:       {avg_move:.2f}c")
        if avg_1c:
            print(f"  Avg time to 1c move:      {_fmt_secs(avg_1c)}")

    # By reporter
    print(f"\n  {bold}BY REPORTER:{reset}")
    reporter_rows = conn.execute(
        """SELECT account,
                  COUNT(*) as n,
                  AVG(repricing_lag_secs) as avg_lag,
                  AVG(max_price_move) as avg_move
           FROM injury_events
           WHERE repricing_lag_secs IS NOT NULL
           GROUP BY account
           ORDER BY n DESC, avg_lag ASC"""
    ).fetchall()
    if reporter_rows:
        print(f"  {'Reporter':20s} | {'Events':>6} | {'Avg Lag':>10} | {'Avg Move':>10}")
        print(f"  {'-'*20}-+-{'-'*6}-+-{'-'*10}-+-{'-'*10}")
        for row in reporter_rows:
            account, n, avg_lag_r, avg_move_r = row
            move_str = f"{avg_move_r*100:.1f}c" if avg_move_r else "N/A"
            print(
                f"  {account:20s} | {n:>6} | {_fmt_secs(avg_lag_r):>10} | {move_str:>10}"
            )
    else:
        print(f"  {dim}No data yet{reset}")

    # By impact type
    print(f"\n  {bold}BY IMPACT TYPE:{reset}")
    type_rows = conn.execute(
        """SELECT injury_type,
                  COUNT(*) as n,
                  AVG(repricing_lag_secs) as avg_lag,
                  AVG(max_price_move) as avg_move
           FROM injury_events
           WHERE repricing_lag_secs IS NOT NULL
           GROUP BY injury_type
           ORDER BY n DESC"""
    ).fetchall()
    if type_rows:
        print(f"  {'Type':20s} | {'Events':>6} | {'Avg Lag':>10} | {'Avg Move':>10}")
        print(f"  {'-'*20}-+-{'-'*6}-+-{'-'*10}-+-{'-'*10}")
        for row in type_rows:
            inj_type, n, avg_lag_r, avg_move_r = row
            move_str = f"{avg_move_r*100:.1f}c" if avg_move_r else "N/A"
            label = (inj_type or "unknown").title()
            print(
                f"  {label:20s} | {n:>6} | {_fmt_secs(avg_lag_r):>10} | {move_str:>10}"
            )
    else:
        print(f"  {dim}No data yet{reset}")

    # By direction
    print(f"\n  {bold}BY DIRECTION:{reset}")
    dir_rows = conn.execute(
        """SELECT impact_direction,
                  COUNT(*) as n,
                  AVG(repricing_lag_secs) as avg_lag,
                  AVG(max_price_move) as avg_move
           FROM injury_events
           WHERE repricing_lag_secs IS NOT NULL
           GROUP BY impact_direction
           ORDER BY n DESC"""
    ).fetchall()
    if dir_rows:
        for row in dir_rows:
            direction, n, avg_lag_r, avg_move_r = row
            move_str = f"{avg_move_r*100:.1f}c" if avg_move_r else "N/A"
            label = {"negative": "Player ruled out", "positive": "Player available", "unknown": "Unknown"}.get(
                direction or "unknown", direction or "unknown"
            )
            print(f"  {label:20s} | {n:>6} events | avg lag {_fmt_secs(avg_lag_r)} | avg move {move_str}")

    # Verdict
    if lags:
        min_n = 20
        print(f"\n  {bold}VERDICT:{reset}")
        edge_threshold = 120  # 2 minutes in seconds
        avg_lag_val = sum(lags) / len(lags)
        n_events = len(lags)

        edge = avg_lag_val > edge_threshold
        sufficient = n_events >= min_n

        lag_color = green if edge else red
        n_color = green if sufficient else yellow

        print(f"    Avg lag > 2 minutes:      {lag_color}{'YES' if edge else 'NO'} ({_fmt_secs(avg_lag_val)}){reset}")
        print(f"    Sufficient sample (n>=20): {n_color}{'YES' if sufficient else 'NO'} (n={n_events}){reset}")

        if edge and sufficient:
            print(f"\n    {bold}{green}Recommendation: PROCEED to live testing{reset}")
        elif edge and not sufficient:
            print(f"\n    {yellow}Recommendation: COLLECT MORE DATA (need n={min_n}, have n={n_events}){reset}")
        else:
            print(f"\n    {red}Recommendation: EDGE DOES NOT EXIST at this threshold{reset}")

    _show_recent_events(conn, bold, dim, green, reset)
    conn.close()


def _show_recent_events(conn: sqlite3.Connection, bold, dim, green, reset, limit: int = 5):
    """Show most recent injury events."""
    rows = conn.execute(
        """SELECT id, tweet_timestamp, account, tweet_text, player_name, team,
                  injury_type, polymarket_slug, pre_news_price, repricing_lag_secs
           FROM injury_events
           ORDER BY tweet_timestamp DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()

    if not rows:
        return

    print(f"\n  {bold}RECENT EVENTS:{reset}")
    for row in rows:
        (eid, ts, account, text, player, team, inj_type, slug, pre_price, lag) = row
        ts_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d %H:%M UTC") if ts else "?"
        price_str = f"${pre_price:.3f}" if pre_price else "no market"
        lag_str = _fmt_secs(lag) if lag else "pending"
        print(
            f"  #{eid:3d} [{ts_str}] @{account}: {(text or '')[:60]}"
            f"\n       {dim}player={player or '?'} team={team or '?'} type={inj_type or '?'}"
            f" market={slug or 'none'} price={price_str} lag={lag_str}{reset}"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Twitter/X Sports News Speed Tester for Polymarket repricing lag",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # manual
    p_manual = subparsers.add_parser("manual", help="Process manual feed file")
    p_manual.add_argument(
        "--feed", default=str(DEFAULT_FEED),
        help=f"Path to manual feed log (default: {DEFAULT_FEED})",
    )
    p_manual.add_argument(
        "--db", default=str(DEFAULT_DB),
        help=f"SQLite database path (default: {DEFAULT_DB})",
    )
    p_manual.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print each tweet as it is processed",
    )

    # monitor
    p_monitor = subparsers.add_parser("monitor", help="API polling mode (requires TWITTER_BEARER_TOKEN)")
    p_monitor.add_argument(
        "--accounts", default=None,
        choices=["nba", "nfl", "soccer"],
        help="Filter to reporters for a specific sport",
    )
    p_monitor.add_argument(
        "--interval", type=int, default=30,
        help="Poll interval in seconds per reporter (default: 30)",
    )
    p_monitor.add_argument(
        "--db", default=str(DEFAULT_DB),
        help=f"SQLite database path (default: {DEFAULT_DB})",
    )

    # add
    p_add = subparsers.add_parser("add", help="Add a single tweet directly")
    p_add.add_argument(
        "tweet", nargs="+",
        help="Tweet in format: '{unix_ts} {account} {tweet_text}'",
    )
    p_add.add_argument(
        "--db", default=str(DEFAULT_DB),
        help=f"SQLite database path (default: {DEFAULT_DB})",
    )

    # report
    p_report = subparsers.add_parser("report", help="Show speed test results")
    p_report.add_argument(
        "--db", default=str(DEFAULT_DB),
        help=f"SQLite database path (default: {DEFAULT_DB})",
    )

    args = parser.parse_args()

    if args.command == "manual":
        cmd_manual(Path(args.db), Path(args.feed), verbose=args.verbose)
    elif args.command == "monitor":
        cmd_monitor(Path(args.db), sport_filter=args.accounts, poll_interval=args.interval)
    elif args.command == "add":
        raw = " ".join(args.tweet)
        cmd_add(Path(args.db), raw)
    elif args.command == "report":
        cmd_report(Path(args.db))


if __name__ == "__main__":
    main()
