#!/usr/bin/env python3
"""Cross-venue odds divergence detector: Polymarket vs traditional sportsbooks.

Compares Polymarket sports market prices against The Odds API sportsbook odds
to find mispricings. When Polymarket prices a sports outcome cheaper than the
consensus sportsbook implied probability, that is a potential edge.

Usage:
    python3 odds_divergence.py scan                       # Scan all active sports markets
    python3 odds_divergence.py scan --sport basketball_nba  # NBA only
    python3 odds_divergence.py scan --min-div 5           # Only show 5%+ divergences
    python3 odds_divergence.py monitor --interval 300     # Continuous monitoring every 5 min
    python3 odds_divergence.py history                    # Show historical divergences
    python3 odds_divergence.py stats                      # Show divergence accuracy stats

Requires env var ODDS_API_KEY from https://the-odds-api.com/ (free tier: 500 req/month).
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
USER_AGENT = "PolyphemusOddsDivergence/1.0"

DEFAULT_DB = Path(__file__).parent.parent / "data" / "odds_divergence.db"

# Supported sports and their Odds API keys
SPORTS = {
    "basketball_nba": "NBA",
    "basketball_euroleague": "EuroLeague",
    "soccer_epl": "EPL",
    "soccer_uefa_champions_league": "Champions League",
    "icehockey_nhl": "NHL",
    "americanfootball_nfl": "NFL",
    "baseball_mlb": "MLB",
}

# Polymarket slug sport prefixes that map to Odds API sport keys
SLUG_SPORT_MAP = {
    "nba": "basketball_nba",
    "nfl": "americanfootball_nfl",
    "nhl": "icehockey_nhl",
    "mlb": "baseball_mlb",
    "epl": "soccer_epl",
    "ucl": "soccer_uefa_champions_league",
    "euroleague": "basketball_euroleague",
}

# Sportsbook priority for consensus (Pinnacle is sharpest)
SPORTSBOOK_PRIORITY = [
    "pinnacle", "draftkings", "fanduel", "betmgm", "caesars",
    "pointsbet", "bet365", "betrivers", "barstool",
]

# ---------------------------------------------------------------------------
# Team name aliases: slug abbreviation -> list of full name variants
# ---------------------------------------------------------------------------
TEAM_ALIASES: Dict[str, List[str]] = {
    # NBA
    "gsw": ["golden state warriors", "warriors", "golden state"],
    "sac": ["sacramento kings", "kings", "sacramento"],
    "mil": ["milwaukee bucks", "bucks", "milwaukee"],
    "bkn": ["brooklyn nets", "nets", "brooklyn"],
    "lal": ["los angeles lakers", "lakers", "la lakers"],
    "bos": ["boston celtics", "celtics", "boston"],
    "mia": ["miami heat", "heat", "miami"],
    "phi": ["philadelphia 76ers", "76ers", "sixers", "philadelphia"],
    "chi": ["chicago bulls", "bulls", "chicago"],
    "atl": ["atlanta hawks", "hawks", "atlanta"],
    "tor": ["toronto raptors", "raptors", "toronto"],
    "nyk": ["new york knicks", "knicks", "new york"],
    "cle": ["cleveland cavaliers", "cavaliers", "cleveland"],
    "ind": ["indiana pacers", "pacers", "indiana"],
    "det": ["detroit pistons", "pistons", "detroit"],
    "cha": ["charlotte hornets", "hornets", "charlotte"],
    "was": ["washington wizards", "wizards", "washington"],
    "orl": ["orlando magic", "magic", "orlando"],
    "den": ["denver nuggets", "nuggets", "denver"],
    "min": ["minnesota timberwolves", "timberwolves", "minnesota"],
    "okc": ["oklahoma city thunder", "thunder", "oklahoma city"],
    "por": ["portland trail blazers", "trail blazers", "portland"],
    "uta": ["utah jazz", "jazz", "utah"],
    "phx": ["phoenix suns", "suns", "phoenix"],
    "lac": ["los angeles clippers", "clippers", "la clippers"],
    "nop": ["new orleans pelicans", "pelicans", "new orleans"],
    "dal": ["dallas mavericks", "mavericks", "dallas"],
    "hou": ["houston rockets", "rockets", "houston"],
    "mem": ["memphis grizzlies", "grizzlies", "memphis"],
    "sas": ["san antonio spurs", "spurs", "san antonio"],
    # NFL
    "ne": ["new england patriots", "patriots", "new england"],
    "buf": ["buffalo bills", "bills", "buffalo"],
    "mia": ["miami dolphins", "dolphins"],  # overrides NBA mia
    "nyj": ["new york jets", "jets"],
    "nyg": ["new york giants", "giants"],
    "phi": ["philadelphia eagles", "eagles"],  # overrides NBA phi
    "dal": ["dallas cowboys", "cowboys"],  # overrides NBA dal
    "was": ["washington commanders", "commanders"],  # overrides NBA was
    "chi": ["chicago bears", "bears"],  # overrides NBA chi
    "det": ["detroit lions", "lions"],  # overrides NBA det
    "gb": ["green bay packers", "packers", "green bay"],
    "min": ["minnesota vikings", "vikings"],  # overrides NBA min
    "sea": ["seattle seahawks", "seahawks", "seattle"],
    "sf": ["san francisco 49ers", "49ers", "san francisco"],
    "lar": ["los angeles rams", "rams", "la rams"],
    "ari": ["arizona cardinals", "cardinals", "arizona"],
    "atl": ["atlanta falcons", "falcons"],  # overrides NBA atl
    "car": ["carolina panthers", "panthers", "carolina"],
    "no": ["new orleans saints", "saints"],
    "tb": ["tampa bay buccaneers", "buccaneers", "tampa bay"],
    "bal": ["baltimore ravens", "ravens", "baltimore"],
    "pit": ["pittsburgh steelers", "steelers", "pittsburgh"],
    "cle": ["cleveland browns", "browns"],  # overrides NBA cle
    "cin": ["cincinnati bengals", "bengals", "cincinnati"],
    "ind": ["indianapolis colts", "colts"],  # overrides NBA ind
    "jax": ["jacksonville jaguars", "jaguars", "jacksonville"],
    "ten": ["tennessee titans", "titans", "tennessee"],
    "hou": ["houston texans", "texans"],  # overrides NBA hou
    "kc": ["kansas city chiefs", "chiefs", "kansas city"],
    "lv": ["las vegas raiders", "raiders", "las vegas"],
    "lac": ["los angeles chargers", "chargers", "la chargers"],  # overrides NBA lac
    "den": ["denver broncos", "broncos"],  # overrides NBA den
    # NHL
    "bos": ["boston bruins", "bruins"],  # overrides NBA bos
    "buf": ["buffalo sabres", "sabres"],  # overrides NFL buf
    "det": ["detroit red wings", "red wings"],  # overrides
    "fla": ["florida panthers", "florida"],
    "mtl": ["montreal canadiens", "canadiens", "montreal"],
    "ott": ["ottawa senators", "senators", "ottawa"],
    "tb": ["tampa bay lightning", "lightning"],  # overrides NFL tb
    "tor": ["toronto maple leafs", "maple leafs"],  # overrides NBA tor
    "car": ["carolina hurricanes", "hurricanes"],  # overrides NFL car
    "cbj": ["columbus blue jackets", "blue jackets", "columbus"],
    "nyi": ["new york islanders", "islanders"],
    "nyr": ["new york rangers", "rangers"],
    "phi": ["philadelphia flyers", "flyers"],  # overrides
    "pit": ["pittsburgh penguins", "penguins"],  # overrides NFL pit
    "was": ["washington capitals", "capitals"],  # overrides
    "chi": ["chicago blackhawks", "blackhawks"],  # overrides
    "col": ["colorado avalanche", "avalanche", "colorado"],
    "min": ["minnesota wild", "wild"],  # overrides
    "nsh": ["nashville predators", "predators", "nashville"],
    "stl": ["st. louis blues", "blues", "st louis"],
    "wpg": ["winnipeg jets", "jets", "winnipeg"],
    "ana": ["anaheim ducks", "ducks", "anaheim"],
    "cgy": ["calgary flames", "flames", "calgary"],
    "edm": ["edmonton oilers", "oilers", "edmonton"],
    "lak": ["los angeles kings", "kings"],
    "sjs": ["san jose sharks", "sharks", "san jose"],
    "sea": ["seattle kraken", "kraken"],  # overrides NFL sea
    "van": ["vancouver canucks", "canucks", "vancouver"],
    "ari": ["arizona coyotes", "coyotes"],  # overrides NFL ari -> also utah hockey club
    "nj": ["new jersey devils", "devils", "new jersey"],
    "dal": ["dallas stars", "stars"],  # overrides
    # MLB
    "bos": ["boston red sox", "red sox"],
    "nyy": ["new york yankees", "yankees"],
    "tb": ["tampa bay rays", "rays"],
    "tor": ["toronto blue jays", "blue jays"],
    "bal": ["baltimore orioles", "orioles"],
    "chi": ["chicago white sox", "white sox"],
    "cle": ["cleveland guardians", "guardians"],
    "kc": ["kansas city royals", "royals"],
    "min": ["minnesota twins", "twins"],
    "det": ["detroit tigers", "tigers"],
    "hou": ["houston astros", "astros"],
    "laa": ["los angeles angels", "angels", "la angels"],
    "sea": ["seattle mariners", "mariners"],
    "tex": ["texas rangers", "rangers"],
    "oak": ["oakland athletics", "athletics", "oakland"],
    "atl": ["atlanta braves", "braves"],
    "mia": ["miami marlins", "marlins"],
    "nym": ["new york mets", "mets"],
    "phi": ["philadelphia phillies", "phillies"],
    "was": ["washington nationals", "nationals"],
    "chc": ["chicago cubs", "cubs"],
    "cin": ["cincinnati reds", "reds"],
    "mil": ["milwaukee brewers", "brewers"],
    "pit": ["pittsburgh pirates", "pirates"],
    "stl": ["st. louis cardinals", "cardinals", "st louis"],
    "ari": ["arizona diamondbacks", "diamondbacks", "d-backs"],
    "col": ["colorado rockies", "rockies"],
    "lad": ["los angeles dodgers", "dodgers", "la dodgers"],
    "sd": ["san diego padres", "padres", "san diego"],
    "sf": ["san francisco giants", "giants"],
    # EPL soccer
    "ars": ["arsenal", "arsenal fc"],
    "che": ["chelsea", "chelsea fc"],
    "liv": ["liverpool", "liverpool fc"],
    "mci": ["manchester city", "man city"],
    "mun": ["manchester united", "man united", "man utd"],
    "tot": ["tottenham hotspur", "tottenham", "spurs"],
    "new": ["newcastle united", "newcastle"],
    "avl": ["aston villa"],
    "whu": ["west ham united", "west ham"],
    "eve": ["everton"],
    "bha": ["brighton", "brighton & hove albion"],
    "bre": ["brentford"],
    "cry": ["crystal palace"],
    "wol": ["wolverhampton wanderers", "wolves"],
    "ful": ["fulham"],
    "bou": ["bournemouth", "afc bournemouth"],
    "not": ["nottingham forest"],
    "lei": ["leicester city"],
    "sou": ["southampton"],
    "ips": ["ipswich town", "ipswich"],
    # Champions League
    "rma": ["real madrid", "madrid"],
    "bar": ["barcelona", "fc barcelona"],
    "bay": ["bayern munich", "fc bayern"],
    "psg": ["paris saint-germain", "psg"],
    "juv": ["juventus"],
    "int": ["inter milan", "internazionale", "inter"],
    "acm": ["ac milan", "milan"],
    "atm": ["atletico madrid"],
    "bvb": ["borussia dortmund", "dortmund"],
    "por": ["porto", "fc porto"],
    "ben": ["benfica", "sl benfica"],
    "ajx": ["ajax"],
    "cel": ["celtic"],
    "rng": ["rangers", "rangers fc"],
}

# Reverse lookup: full name tokens -> abbreviation (built at startup)
_NAME_TO_ABBR: Dict[str, str] = {}


def _build_reverse_alias():
    """Build reverse lookup from full name to abbreviation."""
    for abbr, names in TEAM_ALIASES.items():
        for name in names:
            _NAME_TO_ABBR[name.lower()] = abbr
            # Also index individual meaningful words (>3 chars)
            for word in name.lower().split():
                if len(word) > 3 and word not in ("city", "united", "town", "real", "new", "los", "san", "bay"):
                    if word not in _NAME_TO_ABBR:
                        _NAME_TO_ABBR[word] = abbr


_build_reverse_alias()


# ---------------------------------------------------------------------------
# Caching layer (in-memory, per-run)
# ---------------------------------------------------------------------------
_odds_cache: Dict[str, Tuple[float, list]] = {}  # sport -> (fetched_at, data)
_ODDS_CACHE_TTL = 900  # 15 minutes


def _cache_get(sport: str) -> Optional[list]:
    if sport in _odds_cache:
        fetched_at, data = _odds_cache[sport]
        if time.time() - fetched_at < _ODDS_CACHE_TTL:
            return data
    return None


def _cache_set(sport: str, data: list):
    _odds_cache[sport] = (time.time(), data)


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------
def _http_get(url: str, retries: int = 1, timeout: int = 15) -> Optional[dict]:
    """Generic HTTP GET. Returns parsed JSON or None on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 401:
                print("  [AUTH ERROR] ODDS_API_KEY is invalid or missing. Get one at https://the-odds-api.com/", file=sys.stderr)
                sys.exit(1)
            elif e.code == 422:
                # Unprocessable - sport not in season or no events
                return []
            elif e.code == 429:
                print("  [RATE LIMIT] The Odds API quota exceeded (500/month free tier).", file=sys.stderr)
                return None
            if attempt < retries:
                time.sleep(2)
            else:
                print(f"  [HTTP {e.code}] {url}", file=sys.stderr)
                return None
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
            if attempt < retries:
                time.sleep(2)
            else:
                print(f"  [API ERROR] {url}: {e}", file=sys.stderr)
                return None
    return None


def fetch_odds(sport: str, api_key: str) -> List[dict]:
    """Fetch odds from The Odds API for a sport. Caches for 15 minutes."""
    cached = _cache_get(sport)
    if cached is not None:
        return cached

    url = (
        f"{ODDS_API_BASE}/sports/{sport}/odds/"
        f"?apiKey={api_key}"
        f"&regions=us,eu"
        f"&markets=h2h"
        f"&oddsFormat=american"
    )
    result = _http_get(url)
    if result is None:
        return []

    data = result if isinstance(result, list) else []
    _cache_set(sport, data)
    return data


def fetch_polymarket_sports(sport_filter: Optional[str] = None) -> List[dict]:
    """Fetch active Polymarket sports markets via Gamma API with Data API fallback."""
    markets = []

    # Try Gamma API first - it has richer metadata
    try:
        url = f"{GAMMA_API}/events?active=true&closed=false&limit=200&tag=sports"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        # Gamma API returns events; flatten to markets
        if isinstance(data, list):
            for event in data:
                for market in event.get("markets", []):
                    market["event_slug"] = event.get("slug", "")
                    market["event_title"] = event.get("title", "")
                    markets.append(market)
        elif isinstance(data, dict):
            for market in data.get("markets", []):
                markets.append(market)
    except Exception:
        pass

    # Fallback: Data API activity endpoint for sports slugs
    if not markets:
        try:
            url = f"{DATA_API}/markets?tag=sports&limit=200&active=true"
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            if isinstance(data, list):
                markets = data
        except Exception:
            pass

    # Filter to sport if requested
    if sport_filter and markets:
        # Extract the league prefix (e.g. "basketball_nba" -> "nba")
        sport_prefix = sport_filter.split("_")[-1]  # "nba", "nfl", etc.
        markets = [
            m for m in markets
            if sport_prefix in (m.get("slug", "") + " " + m.get("event_slug", "")).lower()
        ]

    return markets


def fetch_clob_book(token_id: str) -> Optional[dict]:
    """Fetch order book for a Polymarket token. Returns mid price or None."""
    url = f"{CLOB_API}/book?token_id={token_id}"
    result = _http_get(url)
    if not result:
        return None
    return result


def extract_mid_price(book: dict) -> Optional[float]:
    """Extract mid price from CLOB order book."""
    bids = book.get("bids", [])
    asks = book.get("asks", [])

    best_bid = max((float(b["price"]) for b in bids if b.get("price")), default=None)
    best_ask = min((float(a["price"]) for a in asks if a.get("price")), default=None)

    if best_bid and best_ask:
        return (best_bid + best_ask) / 2
    elif best_bid:
        return best_bid
    elif best_ask:
        return best_ask
    return None


def extract_book_depth(book: dict, price: float, cents: float = 0.02) -> float:
    """Sum USDC depth within `cents` of mid price on both sides."""
    total = 0.0
    for b in book.get("bids", []):
        p = float(b.get("price", 0))
        if abs(p - price) <= cents:
            total += float(b.get("size", 0)) * p
    for a in book.get("asks", []):
        p = float(a.get("price", 0))
        if abs(p - price) <= cents:
            total += float(a.get("size", 0)) * p
    return total


# ---------------------------------------------------------------------------
# Odds conversion
# ---------------------------------------------------------------------------
def american_to_implied_prob(american_odds: float) -> float:
    """Convert American odds to implied probability (0.0 - 1.0)."""
    if american_odds > 0:
        return 100.0 / (american_odds + 100.0)
    else:
        return abs(american_odds) / (abs(american_odds) + 100.0)


def consensus_prob(event: dict, outcome_name: str) -> Tuple[Optional[float], Optional[str], Optional[float]]:
    """
    Compute consensus implied probability for an outcome from all bookmakers.
    Returns (consensus_prob, best_sportsbook_name, best_sportsbook_prob).
    Best = closest to Pinnacle, or highest-probability book if Pinnacle not present.
    """
    probs = []
    best_book = None
    best_prob = None
    best_priority = 999

    for bookmaker in event.get("bookmakers", []):
        book_key = bookmaker.get("key", "").lower()
        for market in bookmaker.get("markets", []):
            if market.get("key") != "h2h":
                continue
            for outcome in market.get("outcomes", []):
                if _name_matches(outcome.get("name", ""), outcome_name):
                    odds = outcome.get("price")
                    if odds is not None:
                        prob = american_to_implied_prob(float(odds))
                        probs.append(prob)

                        # Track priority sportsbook
                        priority = next(
                            (i for i, b in enumerate(SPORTSBOOK_PRIORITY) if b in book_key),
                            999,
                        )
                        if priority < best_priority:
                            best_priority = priority
                            best_book = bookmaker.get("title", book_key)
                            best_prob = prob

    if not probs:
        return None, None, None

    consensus = sum(probs) / len(probs)
    return consensus, best_book, best_prob


# ---------------------------------------------------------------------------
# Fuzzy matching
# ---------------------------------------------------------------------------
def _normalize(s: str) -> str:
    """Lowercase, strip punctuation."""
    return "".join(c for c in s.lower() if c.isalnum() or c == " ").strip()


def _name_matches(sportsbook_name: str, query: str) -> bool:
    """Check if a sportsbook outcome name matches our query (fuzzy)."""
    norm_sb = _normalize(sportsbook_name)
    norm_q = _normalize(query)

    if norm_sb == norm_q:
        return True
    if norm_q in norm_sb or norm_sb in norm_q:
        return True

    # Token-level overlap
    q_tokens = set(norm_q.split())
    sb_tokens = set(norm_sb.split())
    overlap = q_tokens & sb_tokens
    if len(overlap) >= min(2, len(q_tokens)):
        return True

    return False


def _parse_slug_teams(slug: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Parse a Polymarket slug to extract sport, team1, team2.
    Slug format: {sport}-{team1}-vs-{team2}-{date} or {sport}-{team1}-{team2}-{date}
    Returns (sport_key, team1_abbr, team2_abbr).
    """
    slug_lower = slug.lower()

    # Identify sport from prefix
    sport_key = None
    for prefix, key in SLUG_SPORT_MAP.items():
        if slug_lower.startswith(prefix + "-"):
            sport_key = key
            remaining = slug_lower[len(prefix) + 1:]
            break

    if not sport_key:
        return None, None, None

    # Remove trailing date-like segments (YYYY-MM-DD or digits at end)
    parts = remaining.split("-")
    # Strip trailing date parts (4-digit year, 2-digit month, 2-digit day)
    while parts and (parts[-1].isdigit() and len(parts[-1]) in (2, 4)):
        parts = parts[:-1]

    if len(parts) < 2:
        return sport_key, None, None

    # Try "at" or "vs" separator
    if "vs" in parts:
        idx = parts.index("vs")
        team1_parts = parts[:idx]
        team2_parts = parts[idx + 1:]
    elif "at" in parts:
        idx = parts.index("at")
        team1_parts = parts[:idx]
        team2_parts = parts[idx + 1:]
    else:
        # Split in half
        mid = len(parts) // 2
        team1_parts = parts[:mid]
        team2_parts = parts[mid:]

    team1_str = "-".join(team1_parts)
    team2_str = "-".join(team2_parts)

    # Try direct alias lookup on joined string
    t1 = TEAM_ALIASES.get(team1_str) and team1_str
    t2 = TEAM_ALIASES.get(team2_str) and team2_str

    # Fall back to first token as abbreviation
    if not t1 and team1_parts:
        t1 = team1_parts[0]
    if not t2 and team2_parts:
        t2 = team2_parts[0]

    return sport_key, t1, t2


def _team_full_names(abbr: str) -> List[str]:
    """Get all known full names for a team abbreviation."""
    return TEAM_ALIASES.get(abbr, [abbr])


def _odds_team_score(odds_team: str, poly_team: str) -> float:
    """
    Score how well an Odds API team name matches a Polymarket team token.
    Returns 0.0-1.0.
    """
    odds_norm = _normalize(odds_team)
    poly_norm = _normalize(poly_team)

    # Direct match on abbreviation
    if poly_norm == odds_norm:
        return 1.0

    # Check alias list
    full_names = _team_full_names(poly_norm)
    for fn in full_names:
        if _normalize(fn) == odds_norm:
            return 1.0
        if _normalize(fn) in odds_norm or odds_norm in _normalize(fn):
            return 0.9

    # Word overlap
    odds_tokens = set(odds_norm.split())
    aliases_text = " ".join(full_names).lower()
    alias_tokens = set(aliases_text.split())
    overlap = odds_tokens & alias_tokens
    if overlap:
        return 0.5 + 0.4 * (len(overlap) / max(len(odds_tokens), len(alias_tokens)))

    return 0.0


def match_polymarket_to_odds(
    poly_slug: str,
    poly_title: str,
    odds_events: List[dict],
) -> Tuple[Optional[dict], float]:
    """
    Find the best matching Odds API event for a Polymarket market.
    Returns (best_event, confidence).
    """
    sport_key, team1_abbr, team2_abbr = _parse_slug_teams(poly_slug)

    if not sport_key or not team1_abbr or not team2_abbr:
        return None, 0.0

    best_event = None
    best_score = 0.0

    for event in odds_events:
        home = event.get("home_team", "")
        away = event.get("away_team", "")

        # Score both orderings (home/away can be in either order in slug)
        score_fwd = (
            _odds_team_score(home, team1_abbr) + _odds_team_score(away, team2_abbr)
        ) / 2
        score_rev = (
            _odds_team_score(home, team2_abbr) + _odds_team_score(away, team1_abbr)
        ) / 2

        score = max(score_fwd, score_rev)

        if score > best_score:
            best_score = score
            best_event = event

    return best_event, best_score


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def init_db(db_path: Path) -> sqlite3.Connection:
    """Initialize SQLite database."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS divergences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detected_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            sport TEXT,
            event_name TEXT,
            polymarket_slug TEXT,
            polymarket_outcome TEXT,
            polymarket_price REAL,
            polymarket_implied_prob REAL,
            sportsbook_consensus_prob REAL,
            best_sportsbook TEXT,
            best_sportsbook_prob REAL,
            divergence_pct REAL,
            polymarket_condition_id TEXT,
            polymarket_token_id TEXT,
            resolved INTEGER DEFAULT 0,
            resolution_outcome TEXT,
            was_profitable INTEGER
        );

        CREATE TABLE IF NOT EXISTS event_matches (
            polymarket_slug TEXT,
            odds_api_event_id TEXT,
            sport TEXT,
            match_confidence REAL,
            matched_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            PRIMARY KEY (polymarket_slug, odds_api_event_id)
        );

        CREATE INDEX IF NOT EXISTS idx_div_sport ON divergences(sport);
        CREATE INDEX IF NOT EXISTS idx_div_detected ON divergences(detected_at DESC);
        CREATE INDEX IF NOT EXISTS idx_div_slug ON divergences(polymarket_slug);
    """)
    conn.commit()
    return conn


def save_divergence(conn: sqlite3.Connection, div: dict) -> int:
    """Insert a divergence record. Returns the new row id."""
    cursor = conn.execute(
        """
        INSERT INTO divergences
        (sport, event_name, polymarket_slug, polymarket_outcome, polymarket_price,
         polymarket_implied_prob, sportsbook_consensus_prob, best_sportsbook,
         best_sportsbook_prob, divergence_pct, polymarket_condition_id, polymarket_token_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            div["sport"], div["event_name"], div["polymarket_slug"],
            div["polymarket_outcome"], div["polymarket_price"],
            div["polymarket_implied_prob"], div["sportsbook_consensus_prob"],
            div["best_sportsbook"], div["best_sportsbook_prob"],
            div["divergence_pct"], div.get("condition_id", ""),
            div.get("token_id", ""),
        ),
    )
    conn.commit()
    return cursor.lastrowid


def save_event_match(conn: sqlite3.Connection, poly_slug: str, odds_id: str, sport: str, confidence: float):
    conn.execute(
        """
        INSERT INTO event_matches (polymarket_slug, odds_api_event_id, sport, match_confidence)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(polymarket_slug, odds_api_event_id) DO UPDATE SET
        match_confidence=?, matched_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
        """,
        (poly_slug, odds_id, sport, confidence, confidence),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Core scan logic
# ---------------------------------------------------------------------------
def scan_sport(
    sport: str,
    api_key: str,
    conn: sqlite3.Connection,
    min_div: float = 3.0,
    min_confidence: float = 0.5,
    verbose: bool = False,
) -> List[dict]:
    """
    Scan one sport for divergences. Returns list of divergence dicts.
    """
    sport_label = SPORTS.get(sport, sport)

    if verbose:
        print(f"  Fetching {sport_label} odds from The Odds API...", flush=True)

    odds_events = fetch_odds(sport, api_key)
    if not odds_events:
        if verbose:
            print(f"  No {sport_label} events available (off-season or quota hit)")
        return []

    if verbose:
        print(f"  {len(odds_events)} {sport_label} events from sportsbooks")

    # Fetch Polymarket sports markets
    poly_markets = fetch_polymarket_sports(sport)
    if not poly_markets:
        if verbose:
            print(f"  No Polymarket {sport_label} markets found")
        return []

    if verbose:
        print(f"  {len(poly_markets)} Polymarket {sport_label} markets")

    divergences = []

    for market in poly_markets:
        slug = market.get("slug", market.get("conditionId", ""))
        title = market.get("question", market.get("title", market.get("event_title", "")))

        if not slug:
            continue

        # Match this Polymarket market to an Odds API event
        odds_event, confidence = match_polymarket_to_odds(slug, title, odds_events)

        if confidence < min_confidence:
            if verbose:
                print(f"  SKIP (low confidence {confidence:.2f}): {slug}")
            continue

        if verbose:
            print(f"  MATCHED ({confidence:.2f}): {slug} -> {odds_event.get('home_team')} vs {odds_event.get('away_team')}")

        # Save the match
        save_event_match(conn, slug, odds_event.get("id", ""), sport, confidence)

        # Get Polymarket outcomes and prices
        outcomes = market.get("outcomes", "")
        outcome_prices = market.get("outcomePrices", "")

        # Parse if stringified JSON
        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
            except Exception:
                outcomes = []
        if isinstance(outcome_prices, str):
            try:
                outcome_prices = json.loads(outcome_prices)
            except Exception:
                outcome_prices = []

        # Also try tokens structure
        tokens = market.get("tokens", [])

        if tokens:
            outcome_list = [(t.get("outcome", ""), t.get("price", 0), t.get("token_id", "")) for t in tokens]
        elif outcomes and outcome_prices and len(outcomes) == len(outcome_prices):
            outcome_list = [(o, p, "") for o, p in zip(outcomes, outcome_prices)]
        else:
            # Try to fetch from CLOB if we have a condition_id
            condition_id = market.get("conditionId", market.get("condition_id", ""))
            if condition_id:
                time.sleep(0.5)
                book = fetch_clob_book(condition_id)
                if book:
                    mid = extract_mid_price(book)
                    if mid:
                        outcome_list = [("Yes", mid, condition_id), ("No", 1 - mid, "")]
                    else:
                        continue
                else:
                    continue
            else:
                continue

        for (outcome_name, poly_price_raw, token_id) in outcome_list:
            try:
                poly_price = float(poly_price_raw)
            except (ValueError, TypeError):
                continue

            if poly_price <= 0 or poly_price >= 1:
                continue

            # Get sportsbook consensus for this outcome
            consensus, best_book, best_prob = consensus_prob(odds_event, outcome_name)

            if consensus is None:
                continue

            # Divergence: positive = Poly is MORE expensive than consensus
            div_pct = (poly_price - consensus) * 100

            if abs(div_pct) < min_div:
                continue

            div = {
                "sport": sport_label,
                "event_name": f"{odds_event.get('away_team', '?')} vs {odds_event.get('home_team', '?')}",
                "polymarket_slug": slug,
                "polymarket_outcome": outcome_name,
                "polymarket_price": poly_price,
                "polymarket_implied_prob": poly_price,
                "sportsbook_consensus_prob": consensus,
                "best_sportsbook": best_book or "unknown",
                "best_sportsbook_prob": best_prob or consensus,
                "divergence_pct": div_pct,
                "condition_id": market.get("conditionId", market.get("condition_id", "")),
                "token_id": token_id,
                "odds_event_date": odds_event.get("commence_time", ""),
                "match_confidence": confidence,
            }
            divergences.append(div)
            save_divergence(conn, div)

        # Polite rate limit
        time.sleep(0.5)

    return divergences


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"


def format_divergence(div: dict) -> str:
    """Format a single divergence for terminal display."""
    pct = div["divergence_pct"]
    direction = "Poly cheaper" if pct < 0 else "Poly expensive"
    direction_color = GREEN if pct < 0 else RED  # cheaper = potential buy edge

    pct_str = f"{direction_color}{pct:+.1f}%{RESET}"
    action = f"BUY {div['polymarket_outcome']} on Polymarket" if pct < 0 else f"AVOID or fade {div['polymarket_outcome']} on Polymarket"
    action_color = GREEN if pct < 0 else YELLOW

    event_date = div.get("odds_event_date", "")
    if event_date:
        try:
            dt = datetime.fromisoformat(event_date.replace("Z", "+00:00"))
            date_str = dt.strftime("%b %d %H:%M UTC")
        except Exception:
            date_str = event_date[:10]
    else:
        date_str = "?"

    confidence = div.get("match_confidence", 0)
    conf_str = f"{DIM}(match confidence: {confidence:.0%}){RESET}" if confidence < 0.8 else ""

    lines = [
        f"\n{BOLD}DIVERGENCE: {pct_str} ({direction}){RESET}",
        f"  Event:     {div['event_name']} ({div['sport']}, {date_str}) {conf_str}",
        f"  Outcome:   {div['polymarket_outcome']}",
        f"  Polymarket: ${div['polymarket_price']:.3f} ({div['polymarket_implied_prob']*100:.1f}%)",
        f"  Consensus:  {div['sportsbook_consensus_prob']*100:.1f}% | Best book: {div['best_sportsbook']} {div['best_sportsbook_prob']*100:.1f}%",
        f"  Gap:        Polymarket is {abs(pct):.1f}% {'cheaper' if pct < 0 else 'more expensive'} than consensus",
        f"  Slug:       {div['polymarket_slug']}",
        f"  {action_color}Action: {action}{RESET}",
    ]
    return "\n".join(lines)


def print_scan_header(sport_filter: Optional[str], min_div: float):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    sport_str = SPORTS.get(sport_filter, sport_filter) if sport_filter else "ALL SPORTS"
    print(f"\n{BOLD}=== ODDS DIVERGENCE SCAN ({ts}) ==={RESET}")
    print(f"  Sport:    {sport_str}")
    print(f"  Min div:  {min_div:.1f}%")
    print()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_scan(
    api_key: str,
    db_path: Path,
    sport_filter: Optional[str] = None,
    min_div: float = 3.0,
    verbose: bool = False,
):
    """Scan sports markets for price divergences."""
    conn = init_db(db_path)
    print_scan_header(sport_filter, min_div)

    sports_to_scan = [sport_filter] if sport_filter else list(SPORTS.keys())
    all_divergences = []

    for sport in sports_to_scan:
        print(f"  {DIM}Scanning {SPORTS.get(sport, sport)}...{RESET}", flush=True)
        divs = scan_sport(sport, api_key, conn, min_div=min_div, verbose=verbose)
        all_divergences.extend(divs)
        # Brief pause between sports to be polite to both APIs
        if len(sports_to_scan) > 1:
            time.sleep(1)

    conn.close()

    # Sort by absolute divergence descending
    all_divergences.sort(key=lambda d: abs(d["divergence_pct"]), reverse=True)

    if not all_divergences:
        print(f"\n  No divergences found above {min_div:.1f}% threshold.\n")
        print(f"  {DIM}This may mean:{RESET}")
        print(f"  - No active sports markets match sportsbook events right now")
        print(f"  - Markets are efficiently priced within the threshold")
        print(f"  - Slug matching failed (run with --verbose to debug)")
        return

    print(f"\n  Found {BOLD}{len(all_divergences)}{RESET} divergence(s):\n")
    for div in all_divergences:
        print(format_divergence(div))

    # Summary
    cheap = [d for d in all_divergences if d["divergence_pct"] < 0]
    expensive = [d for d in all_divergences if d["divergence_pct"] > 0]
    print(f"\n{BOLD}--- Summary ---{RESET}")
    print(f"  Poly cheaper (potential buys): {len(cheap)}")
    print(f"  Poly expensive (fade/avoid):   {len(expensive)}")
    print(f"  Saved to: {db_path}\n")


def cmd_monitor(
    api_key: str,
    db_path: Path,
    sport_filter: Optional[str],
    min_div: float,
    interval: int,
):
    """Continuous monitoring loop."""
    conn = init_db(db_path)
    conn.close()  # Will reopen each cycle

    bold = BOLD
    dim = DIM
    reset = RESET

    print(f"\n{bold}=== ODDS DIVERGENCE MONITOR ==={reset}")
    print(f"  Sport:    {SPORTS.get(sport_filter, sport_filter) if sport_filter else 'ALL'}")
    print(f"  Interval: {interval}s")
    print(f"  Min div:  {min_div:.1f}%")
    print(f"  {dim}Press Ctrl+C to stop{reset}\n")

    cycle = 0
    try:
        while True:
            cycle += 1
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
            print(f"  {dim}[{ts}] Cycle {cycle}...{reset}", flush=True)

            conn = init_db(db_path)
            sports_to_scan = [sport_filter] if sport_filter else list(SPORTS.keys())
            all_divs = []

            for sport in sports_to_scan:
                divs = scan_sport(sport, api_key, conn, min_div=min_div)
                all_divs.extend(divs)

            conn.close()

            if all_divs:
                all_divs.sort(key=lambda d: abs(d["divergence_pct"]), reverse=True)
                print(f"\n{bold}[{ts}] {len(all_divs)} divergence(s) found:{reset}")
                for div in all_divs:
                    print(format_divergence(div))
            else:
                print(f"  {dim}[{ts}] No divergences above {min_div:.1f}% this cycle{reset}")

            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n\n  Monitor stopped after {cycle} cycles.")


def cmd_history(db_path: Path, limit: int = 50, sport_filter: Optional[str] = None):
    """Show historical divergences."""
    conn = init_db(db_path)
    bold = BOLD
    dim = DIM
    reset = RESET

    sport_clause = ""
    params: list = [limit]
    if sport_filter:
        sport_label = SPORTS.get(sport_filter, sport_filter)
        sport_clause = f"WHERE sport = ?"
        params = [sport_label, limit]

    rows = conn.execute(
        f"""
        SELECT detected_at, sport, event_name, polymarket_outcome,
               polymarket_price, sportsbook_consensus_prob,
               divergence_pct, resolved, was_profitable
        FROM divergences
        {sport_clause}
        ORDER BY detected_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()

    if not rows:
        print(f"\n  No historical divergences found in {db_path}")
        conn.close()
        return

    print(f"\n{bold}=== DIVERGENCE HISTORY (last {len(rows)}){reset}")
    if sport_filter:
        print(f"  Sport: {SPORTS.get(sport_filter, sport_filter)}")
    print()

    for r in rows:
        detected, sport, event, outcome, poly_price, consensus, div_pct, resolved, profitable = r
        div_color = GREEN if div_pct < 0 else RED
        ts = detected[:16] if detected else "?"
        resolved_str = ""
        if resolved:
            result = "WIN" if profitable else ("LOSS" if profitable == 0 else "?")
            res_color = GREEN if profitable else RED
            resolved_str = f" | {res_color}[{result}]{reset}"

        print(
            f"  {ts} | {sport:15s} | {event:35s} | {outcome:12s} | "
            f"PM={poly_price:.2f} SB={consensus:.2f} | "
            f"{div_color}{div_pct:+.1f}%{reset}{resolved_str}"
        )

    # Stats if any resolved
    resolved_rows = [r for r in rows if r[7]]
    if resolved_rows:
        wins = sum(1 for r in resolved_rows if r[8] == 1)
        total = len(resolved_rows)
        print(f"\n  {bold}Resolved: {total} | Wins: {wins} | WR: {wins/total*100:.1f}%{reset}")

    conn.close()


def cmd_stats(db_path: Path):
    """Show divergence accuracy statistics."""
    conn = init_db(db_path)
    bold = BOLD
    dim = DIM
    reset = RESET
    green = GREEN
    red = RED

    print(f"\n{bold}=== DIVERGENCE STATS ==={reset}\n")

    # Overall counts
    total_row = conn.execute("SELECT COUNT(*) FROM divergences").fetchone()
    resolved_row = conn.execute("SELECT COUNT(*) FROM divergences WHERE resolved=1").fetchone()
    total = total_row[0] if total_row else 0
    resolved = resolved_row[0] if resolved_row else 0

    print(f"  Total divergences detected: {total}")
    print(f"  Resolved:                   {resolved}")
    print(f"  Pending resolution:         {total - resolved}\n")

    if resolved == 0:
        print(f"  {dim}No resolved divergences yet. Run 'scan' regularly and check back.{reset}")
        conn.close()
        return

    # Win rate by direction
    for direction, label, color in [(-1, "Poly cheaper (bought)", green), (1, "Poly expensive (faded)", red)]:
        where = "divergence_pct < 0" if direction < 0 else "divergence_pct > 0"
        rows = conn.execute(
            f"SELECT was_profitable FROM divergences WHERE resolved=1 AND {where}"
        ).fetchall()
        if rows:
            wins = sum(1 for r in rows if r[0] == 1)
            n = len(rows)
            wr = wins / n * 100
            print(f"  {label}: {n} resolved | {color}WR={wr:.1f}%{reset}")

    # By sport
    print(f"\n  {bold}By sport:{reset}")
    sport_rows = conn.execute(
        """
        SELECT sport,
               COUNT(*) as total,
               SUM(CASE WHEN resolved=1 THEN 1 ELSE 0 END) as resolved_count,
               SUM(CASE WHEN resolved=1 AND was_profitable=1 THEN 1 ELSE 0 END) as wins,
               AVG(ABS(divergence_pct)) as avg_div
        FROM divergences
        GROUP BY sport
        ORDER BY total DESC
        """
    ).fetchall()

    for row in sport_rows:
        sport, total_s, res_s, wins_s, avg_div = row
        wr_str = f"{wins_s/res_s*100:.1f}%" if res_s > 0 else "n/a"
        print(
            f"    {sport:20s} | total={total_s:3d} resolved={res_s:3d} WR={wr_str:6s} avg_div={avg_div:.1f}%"
        )

    # Most recent
    print(f"\n  {bold}Most recent divergences:{reset}")
    recent = conn.execute(
        """
        SELECT detected_at, sport, event_name, polymarket_outcome, divergence_pct
        FROM divergences ORDER BY detected_at DESC LIMIT 5
        """
    ).fetchall()
    for r in recent:
        ts, sport, event, outcome, div = r
        color = GREEN if div < 0 else RED
        print(f"    {ts[:16]} | {sport:12s} | {event[:30]:30s} | {outcome:10s} | {color}{div:+.1f}%{reset}")

    conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Cross-venue odds divergence detector: Polymarket vs sportsbooks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # scan
    scan_parser = subparsers.add_parser("scan", help="Scan for active divergences")
    scan_parser.add_argument(
        "--sport", choices=list(SPORTS.keys()), default=None,
        help="Limit to a specific sport (default: all)",
    )
    scan_parser.add_argument(
        "--min-div", type=float, default=3.0,
        help="Minimum divergence percentage to report (default: 3.0)",
    )
    scan_parser.add_argument(
        "--verbose", action="store_true",
        help="Show matching details for each market",
    )

    # monitor
    monitor_parser = subparsers.add_parser("monitor", help="Continuous monitoring loop")
    monitor_parser.add_argument(
        "--interval", type=int, default=300,
        help="Seconds between scans (default: 300 = 5 minutes)",
    )
    monitor_parser.add_argument(
        "--sport", choices=list(SPORTS.keys()), default=None,
        help="Limit to a specific sport",
    )
    monitor_parser.add_argument(
        "--min-div", type=float, default=3.0,
        help="Minimum divergence percentage to report (default: 3.0)",
    )

    # history
    history_parser = subparsers.add_parser("history", help="Show historical divergences")
    history_parser.add_argument(
        "--limit", type=int, default=50,
        help="Number of records to show (default: 50)",
    )
    history_parser.add_argument(
        "--sport", choices=list(SPORTS.keys()), default=None,
        help="Filter to a specific sport",
    )

    # stats
    subparsers.add_parser("stats", help="Show divergence accuracy statistics")

    # Global options
    for p in [scan_parser, monitor_parser, history_parser]:
        p.add_argument(
            "--db", default=str(DEFAULT_DB),
            help=f"SQLite database path (default: {DEFAULT_DB})",
        )

    stats_p = subparsers.choices["stats"]
    stats_p.add_argument(
        "--db", default=str(DEFAULT_DB),
        help=f"SQLite database path (default: {DEFAULT_DB})",
    )

    args = parser.parse_args()
    db_path = Path(args.db)

    # Commands that don't need the API key
    if args.command == "history":
        cmd_history(db_path, limit=args.limit, sport_filter=getattr(args, "sport", None))
        return
    if args.command == "stats":
        cmd_stats(db_path)
        return

    # API key required for scan and monitor
    api_key = os.environ.get("ODDS_API_KEY", "").strip()
    if not api_key:
        print("\nError: ODDS_API_KEY environment variable not set.", file=sys.stderr)
        print("Get a free key (500 requests/month) at https://the-odds-api.com/", file=sys.stderr)
        print("\nExport it before running:", file=sys.stderr)
        print("  export ODDS_API_KEY=your_key_here", file=sys.stderr)
        sys.exit(1)

    if args.command == "scan":
        cmd_scan(
            api_key=api_key,
            db_path=db_path,
            sport_filter=getattr(args, "sport", None),
            min_div=args.min_div,
            verbose=args.verbose,
        )
    elif args.command == "monitor":
        cmd_monitor(
            api_key=api_key,
            db_path=db_path,
            sport_filter=getattr(args, "sport", None),
            min_div=args.min_div,
            interval=args.interval,
        )


if __name__ == "__main__":
    main()
