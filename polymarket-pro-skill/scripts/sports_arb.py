#!/usr/bin/env python3
"""Sports Odds Arb Bot — Compare sportsbook consensus vs Polymarket prices.

Fetches real-time NBA moneyline odds from The Odds API, compares to
Polymarket game outcome markets, and places maker orders when edge
exceeds threshold.

Usage:
    python3 sports_arb.py scan          # Show edges without trading
    python3 sports_arb.py trade         # Scan + place orders on edges
    python3 sports_arb.py history       # Show trade history
    python3 sports_arb.py pnl           # Show P&L summary
    python3 sports_arb.py resolve       # Resolve completed trades
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
ODDS_API = "https://api.the-odds-api.com/v4"
CHAIN_ID = 137

# Trading params
EDGE_THRESHOLD = float(os.environ.get("SPORTS_EDGE_THRESHOLD", "0.05"))  # 5%
KELLY_FRACTION = float(os.environ.get("SPORTS_KELLY_FRACTION", "0.25"))
MAX_POSITION_PCT = float(os.environ.get("SPORTS_MAX_POSITION_PCT", "0.05"))
MAX_TOTAL_EXPOSURE_PCT = float(os.environ.get("SPORTS_MAX_EXPOSURE_PCT", "0.20"))
MIN_ORDER_SIZE = 5
MIN_BOOKMAKERS = int(os.environ.get("SPORTS_MIN_BOOKMAKERS", "3"))  # Min books for consensus
MAKER_OFFSET = float(os.environ.get("SPORTS_MAKER_OFFSET", "0.005"))

# Sports to track (h2h = game-by-game, futures = championship winner)
SPORTS_H2H = os.environ.get("SPORTS_LEAGUES", "basketball_nba").split(",")
SPORTS_FUTURES = os.environ.get("SPORTS_FUTURES",
    "basketball_nba_championship_winner").split(",")

# DB
DB_PATH = os.environ.get("SPORTS_DB_PATH",
    os.path.join(os.path.dirname(__file__), "sports_trades.db"))

# Team name normalization for matching
TEAM_ALIASES = {
    # NBA teams — map various names to canonical short form
    "76ers": "76ers", "philadelphia 76ers": "76ers", "philly": "76ers",
    "bucks": "bucks", "milwaukee bucks": "bucks",
    "bulls": "bulls", "chicago bulls": "bulls",
    "cavaliers": "cavaliers", "cleveland cavaliers": "cavaliers", "cavs": "cavaliers",
    "celtics": "celtics", "boston celtics": "celtics",
    "clippers": "clippers", "la clippers": "clippers", "los angeles clippers": "clippers",
    "grizzlies": "grizzlies", "memphis grizzlies": "grizzlies",
    "hawks": "hawks", "atlanta hawks": "hawks",
    "heat": "heat", "miami heat": "heat",
    "hornets": "hornets", "charlotte hornets": "hornets",
    "jazz": "jazz", "utah jazz": "jazz",
    "kings": "kings", "sacramento kings": "kings",
    "knicks": "knicks", "new york knicks": "knicks",
    "lakers": "lakers", "los angeles lakers": "lakers", "la lakers": "lakers",
    "magic": "magic", "orlando magic": "magic",
    "mavericks": "mavericks", "dallas mavericks": "mavericks", "mavs": "mavericks",
    "nets": "nets", "brooklyn nets": "nets",
    "nuggets": "nuggets", "denver nuggets": "nuggets",
    "pacers": "pacers", "indiana pacers": "pacers",
    "pelicans": "pelicans", "new orleans pelicans": "pelicans",
    "pistons": "pistons", "detroit pistons": "pistons",
    "raptors": "raptors", "toronto raptors": "raptors",
    "rockets": "rockets", "houston rockets": "rockets",
    "spurs": "spurs", "san antonio spurs": "spurs",
    "suns": "suns", "phoenix suns": "suns",
    "thunder": "thunder", "oklahoma city thunder": "thunder", "okc thunder": "thunder",
    "timberwolves": "timberwolves", "minnesota timberwolves": "timberwolves", "wolves": "timberwolves",
    "trail blazers": "trail blazers", "portland trail blazers": "trail blazers", "blazers": "trail blazers",
    "warriors": "warriors", "golden state warriors": "warriors",
    "wizards": "wizards", "washington wizards": "wizards",
}


def normalize_team(name: str) -> str:
    """Normalize team name to canonical short form."""
    key = name.lower().strip()
    if key in TEAM_ALIASES:
        return TEAM_ALIASES[key]
    # Try matching just the last word (team name)
    last_word = key.split()[-1] if key.split() else key
    if last_word in TEAM_ALIASES:
        return TEAM_ALIASES[last_word]
    return key


# --- CLOB Client ---

def _load_env_file():
    for path in ["/opt/openclaw/.env", "/opt/lagbot/lagbot/.env", "/opt/weatherbot/.env"]:
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
    conn.execute("""CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        sport TEXT NOT NULL,
        game TEXT NOT NULL,
        team TEXT NOT NULL,
        consensus_prob REAL NOT NULL,
        polymarket_price REAL NOT NULL,
        edge REAL NOT NULL,
        order_price REAL NOT NULL,
        shares REAL NOT NULL,
        cost REAL NOT NULL,
        token_id TEXT NOT NULL,
        condition_id TEXT,
        order_id TEXT,
        status TEXT DEFAULT 'placed',
        pnl REAL DEFAULT 0,
        num_bookmakers INTEGER DEFAULT 0,
        game_time TEXT
    )""")
    conn.commit()
    return conn


def record_trade(conn, sport, game, team, consensus_prob, polymarket_price,
                 edge, order_price, shares, cost, token_id, condition_id,
                 order_id, num_bookmakers, game_time):
    conn.execute("""INSERT INTO trades
        (ts, sport, game, team, consensus_prob, polymarket_price, edge,
         order_price, shares, cost, token_id, condition_id, order_id,
         num_bookmakers, game_time)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (datetime.now(timezone.utc).isoformat(), sport, game, team,
         consensus_prob, polymarket_price, edge, order_price, shares, cost,
         token_id, condition_id, order_id, num_bookmakers, game_time))
    conn.commit()


# --- The Odds API ---

def get_odds_api_key():
    _load_env_file()
    key = os.environ.get("ODDS_API_KEY", "")
    if not key:
        print("ERROR: ODDS_API_KEY not set. Sign up at https://the-odds-api.com/")
        sys.exit(1)
    return key


def fetch_odds(sport: str = "basketball_nba") -> list:
    """Fetch moneyline odds from The Odds API for upcoming games."""
    api_key = get_odds_api_key()
    url = f"{ODDS_API}/sports/{sport}/odds/"
    try:
        r = requests.get(url, params={
            "apiKey": api_key,
            "regions": "us,uk",
            "markets": "h2h",
            "oddsFormat": "american",
        }, timeout=20)
        r.raise_for_status()

        # Track API usage from headers
        remaining = r.headers.get("x-requests-remaining", "?")
        used = r.headers.get("x-requests-used", "?")
        print(f"  Odds API: {used} used / {remaining} remaining this month")

        return r.json()
    except Exception as e:
        print(f"  Odds API error: {e}")
        return []


def american_to_implied(odds: int) -> float:
    """Convert American odds to implied probability (0-1)."""
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    else:
        return 100 / (odds + 100)


def calculate_consensus(game: dict) -> dict:
    """Calculate vig-free consensus probability from all bookmakers.

    Returns dict with team names as keys and probabilities as values.
    """
    bookmakers = game.get("bookmakers", [])
    if len(bookmakers) < MIN_BOOKMAKERS:
        return {}

    # Collect implied probs per team across all bookmakers
    team_probs = {}  # team_name -> [prob1, prob2, ...]
    for book in bookmakers:
        for market in book.get("markets", []):
            if market.get("key") != "h2h":
                continue
            for outcome in market.get("outcomes", []):
                name = outcome.get("name", "")
                price = outcome.get("price", 0)
                if not name or price == 0:
                    continue
                prob = american_to_implied(price)
                team_probs.setdefault(name, []).append(prob)

    if len(team_probs) < 2:
        return {}

    # Median implied prob per team
    consensus = {}
    for team, probs in team_probs.items():
        probs.sort()
        mid = len(probs) // 2
        median = probs[mid] if len(probs) % 2 else (probs[mid-1] + probs[mid]) / 2
        consensus[team] = median

    # Remove vig: normalize so probabilities sum to 1.0
    total = sum(consensus.values())
    if total <= 0:
        return {}
    for team in consensus:
        consensus[team] /= total

    return consensus


# --- Polymarket Discovery ---

def _has_nba_tag(tags):
    """Check if event tags include NBA/Basketball/Games (tags can be dicts or strings)."""
    nba_slugs = {"nba", "basketball", "games"}
    for t in (tags if isinstance(tags, list) else []):
        if isinstance(t, dict):
            slug = t.get("slug", "").lower()
        else:
            slug = str(t).lower()
        if slug in nba_slugs:
            return True
    return False


def _is_moneyline_market(question: str) -> bool:
    """Check if market is a moneyline (game winner) market, not O/U or spread."""
    q = question.lower()
    # Exclude over/under, spreads, player props, handicaps
    exclude = ["o/u", "over/under", "total points", "handicap", "spread",
               "points scored", "most points", "game 1", "game 2", "game 3",
               "game 4", "game 5", "game 6", "game 7",
               "mvp", "champion", "championship", "conference", "finals",
               "playoff", "award", "rookie", "all-star", "draft"]
    if any(kw in q for kw in exclude):
        return False
    # Must look like a game winner question
    if any(kw in q for kw in ["win", "beat", "defeat", "vs", "vs."]):
        return True
    return False


def _is_futures_market(question: str) -> bool:
    """Check if market is a futures/championship winner market."""
    q = question.lower()
    return any(kw in q for kw in ["win the 2026 nba finals",
                                   "win the 2026 nhl",
                                   "nba championship"])


def _parse_market(market, event_slug="", event_end_date=""):
    """Parse a single market dict into our standard format. Returns None if invalid."""
    question = market.get("question", "")

    # Must be a moneyline market
    if not _is_moneyline_market(question):
        return None

    # Must mention a team
    teams = extract_teams_from_question(question)
    if not teams:
        return None

    outcomes = market.get("outcomes", "[]")
    if isinstance(outcomes, str):
        outcomes = json.loads(outcomes)
    prices = market.get("outcomePrices", "[]")
    if isinstance(prices, str):
        prices = json.loads(prices)
    tokens = market.get("clobTokenIds", "[]")
    if isinstance(tokens, str):
        tokens = json.loads(tokens)

    if len(outcomes) < 2 or len(prices) < 2 or len(tokens) < 2:
        return None

    # Filter out zero-liquidity markets (both sides at 0)
    p0 = float(prices[0])
    p1 = float(prices[1])
    if p0 < 0.01 and p1 < 0.01:
        return None

    return {
        "question": question,
        "slug": market.get("slug", ""),
        "event_slug": event_slug,
        "outcomes": outcomes,
        "prices": [p0, p1],
        "tokens": tokens,
        "teams": teams,
        "volume": float(market.get("volume", 0) or 0),
        "condition_id": market.get("conditionId", ""),
        "end_date": event_end_date or market.get("endDate", ""),
        "closed": market.get("closed", False),
    }


def discover_sports_markets() -> list:
    """Find active NBA game markets on Polymarket.

    NOTE: Gamma API tag= query param is broken (returns unrelated events).
    Instead we fetch top events by volume and filter by checking tags in the response.
    """
    found = []
    seen_slugs = set()

    # Approach 1: Fetch top events by volume, filter by NBA tags in response
    for offset in range(0, 200, 100):
        try:
            r = requests.get(f"{GAMMA_API}/events", params={
                "active": "true",
                "closed": "false",
                "limit": "100",
                "offset": str(offset),
                "order": "volume",
                "ascending": "false",
            }, timeout=20)
            events = r.json()
            if not events:
                break

            for event in events:
                tags = event.get("tags", [])
                if not _has_nba_tag(tags):
                    continue

                end_date = event.get("endDate", "")
                for market in event.get("markets", []):
                    slug = market.get("slug", "")
                    if slug in seen_slugs:
                        continue
                    parsed = _parse_market(market, event.get("slug", ""), end_date)
                    if parsed:
                        found.append(parsed)
                        seen_slugs.add(slug)
        except Exception as e:
            print(f"  Gamma API error (volume scan): {e}")
            break

    # Approach 2: Search markets endpoint for team names directly
    for team_kw in ["lakers", "celtics", "warriors", "thunder", "bucks",
                     "nuggets", "knicks", "cavaliers", "grizzlies", "mavericks"]:
        try:
            r = requests.get(f"{GAMMA_API}/markets", params={
                "active": "true",
                "closed": "false",
                "limit": "20",
                "slug_contains": team_kw,
            }, timeout=15)
            markets = r.json()
            for market in markets:
                slug = market.get("slug", "")
                if slug in seen_slugs:
                    continue
                parsed = _parse_market(market)
                if parsed:
                    found.append(parsed)
                    seen_slugs.add(slug)
        except Exception:
            pass

    print(f"  Discovery: found {len(found)} NBA game markets")
    return found


def discover_futures_markets() -> list:
    """Find open NBA Finals/Championship futures markets on Polymarket.

    Uses the markets endpoint with bulk scan + keyword filtering (events
    endpoint tag= param is broken).
    """
    found = []
    seen_slugs = set()
    offset = 0

    while offset < 3000:
        try:
            r = requests.get(f"{GAMMA_API}/markets", params={
                "active": "true",
                "closed": "false",
                "limit": "100",
                "offset": str(offset),
            }, timeout=20)
            mkts = r.json()
            if not mkts:
                break

            for market in mkts:
                slug = market.get("slug", "")
                if slug in seen_slugs:
                    continue
                question = market.get("question", "")
                if not _is_futures_market(question):
                    continue

                teams = extract_teams_from_question(question)
                if not teams:
                    continue

                outcomes = market.get("outcomes", "[]")
                if isinstance(outcomes, str):
                    outcomes = json.loads(outcomes)
                prices = market.get("outcomePrices", "[]")
                if isinstance(prices, str):
                    prices = json.loads(prices)
                tokens = market.get("clobTokenIds", "[]")
                if isinstance(tokens, str):
                    tokens = json.loads(tokens)

                if len(outcomes) < 2 or len(prices) < 2 or len(tokens) < 2:
                    continue

                p0 = float(prices[0])
                if p0 < 0.005:  # Skip near-zero markets
                    continue

                seen_slugs.add(slug)
                found.append({
                    "question": question,
                    "slug": slug,
                    "outcomes": outcomes,
                    "prices": [p0, float(prices[1])],
                    "tokens": tokens,
                    "teams": teams,
                    "volume": float(market.get("volume", 0) or 0),
                    "condition_id": market.get("conditionId", ""),
                    "end_date": market.get("endDate", ""),
                })

            if len(mkts) < 100:
                break
            offset += 100
        except Exception as e:
            print(f"  Gamma API error at offset {offset}: {e}")
            break

    print(f"  Discovery: found {len(found)} futures markets")
    return found


def fetch_futures_odds(sport: str = "basketball_nba_championship_winner") -> dict:
    """Fetch championship/futures odds from The Odds API.

    Returns dict: team_name -> {prob: float, n_books: int, odds_samples: list}
    """
    api_key = get_odds_api_key()
    url = f"{ODDS_API}/sports/{sport}/odds/"
    try:
        r = requests.get(url, params={
            "apiKey": api_key,
            "regions": "us,uk",
            "markets": "outrights",
            "oddsFormat": "american",
        }, timeout=20)
        r.raise_for_status()

        remaining = r.headers.get("x-requests-remaining", "?")
        used = r.headers.get("x-requests-used", "?")
        print(f"  Odds API (futures): {used} used / {remaining} remaining")

        data = r.json()
        if not isinstance(data, list) or not data:
            return {}

        # Collect odds per team across all bookmakers
        team_odds = {}  # team -> [implied_prob, ...]
        for event in data:
            for book in event.get("bookmakers", []):
                for market in book.get("markets", []):
                    if market.get("key") != "outrights":
                        continue
                    for outcome in market.get("outcomes", []):
                        name = outcome.get("name", "")
                        price = outcome.get("price", 0)
                        if not name or price == 0:
                            continue
                        prob = american_to_implied(price)
                        team_odds.setdefault(name, []).append(prob)

        # Calculate median and vig-adjusted probability per team
        result = {}
        all_medians = {}
        for team, probs in team_odds.items():
            probs.sort()
            mid = len(probs) // 2
            median = probs[mid] if len(probs) % 2 else (probs[mid-1] + probs[mid]) / 2
            all_medians[team] = median
            result[team] = {
                "raw_prob": median,
                "n_books": len(probs),
            }

        # Remove vig: normalize so probabilities sum to 1.0
        total = sum(v for v in all_medians.values())
        if total > 0:
            for team in result:
                result[team]["prob"] = all_medians[team] / total

        return result
    except Exception as e:
        print(f"  Odds API futures error: {e}")
        return {}


def match_futures_to_polymarket(futures_odds: dict, poly_markets: list) -> list:
    """Match sportsbook futures odds to Polymarket championship markets.

    Returns list of matches with edge calculations.
    """
    matches = []

    for pm in poly_markets:
        teams = pm.get("teams", [])
        if not teams:
            continue

        yes_price = pm["prices"][0]
        team_canonical = teams[0]  # First team in the question

        # Find matching sportsbook team
        best_match = None
        for book_team, data in futures_odds.items():
            if normalize_team(book_team) == team_canonical:
                best_match = (book_team, data)
                break

        if not best_match:
            continue

        book_team, data = best_match
        consensus_prob = data.get("prob", 0)
        n_books = data.get("n_books", 0)

        if n_books < MIN_BOOKMAKERS:
            continue

        edge = consensus_prob - yes_price

        matches.append({
            "game": f"NBA Finals: {book_team}",
            "team": team_canonical,
            "side": "YES",
            "consensus_prob": consensus_prob,
            "polymarket_price": yes_price,
            "edge": edge,
            "token_id": pm["tokens"][0],
            "condition_id": pm["condition_id"],
            "question": pm["question"],
            "volume": pm["volume"],
            "num_bookmakers": n_books,
            "game_time": pm.get("end_date", ""),
            "sport": "basketball_nba_championship_winner",
        })

    matches.sort(key=lambda x: x["edge"], reverse=True)
    return matches


def extract_teams_from_question(question: str) -> list:
    """Extract team names from market question.

    Examples:
        "Will the Lakers beat the Celtics?" -> ["lakers", "celtics"]
        "Lakers vs Celtics" -> ["lakers", "celtics"]
        "Will the Mavericks win against the Grizzlies?" -> ["mavericks", "grizzlies"]
    """
    q = question.lower()
    teams = []
    for alias, canonical in TEAM_ALIASES.items():
        if alias in q and canonical not in teams:
            teams.append(canonical)
    return teams


# --- Matching ---

def _game_date_matches(game_time_iso: str, pm_end_date: str) -> bool:
    """Check if the game date roughly matches the market end date.

    Game markets typically end the same day or day after the game.
    """
    if not game_time_iso or not pm_end_date:
        return True  # Can't filter, assume match
    try:
        game_dt = datetime.fromisoformat(game_time_iso.replace("Z", "+00:00"))
        pm_dt = datetime.fromisoformat(pm_end_date.replace("Z", "+00:00"))
        # Market should end within 2 days of game start
        diff = abs((pm_dt - game_dt).total_seconds())
        return diff < 86400 * 2  # Within 2 days
    except Exception:
        return True  # Can't parse, assume match


def match_odds_to_polymarket(odds_games: list, poly_markets: list) -> list:
    """Match The Odds API games to Polymarket markets by team names.

    Returns list of matched pairs with edge calculations.
    Deduplicates: picks the highest-volume market per (game, side).
    """
    raw_matches = []

    for game in odds_games:
        home = game.get("home_team", "")
        away = game.get("away_team", "")
        home_norm = normalize_team(home)
        away_norm = normalize_team(away)
        game_time = game.get("commence_time", "")

        consensus = calculate_consensus(game)
        if not consensus:
            continue

        num_books = len(game.get("bookmakers", []))

        # Find matching Polymarket market
        for pm in poly_markets:
            pm_teams = pm.get("teams", [])
            if not pm_teams:
                continue

            # Check if BOTH teams match (not just one)
            has_home = home_norm in pm_teams
            has_away = away_norm in pm_teams
            if not (has_home and has_away):
                continue

            # Check game date matches market date
            if not _game_date_matches(game_time, pm.get("end_date", "")):
                continue

            # Found a match — calculate edge for each outcome
            yes_price = pm["prices"][0]
            no_price = pm["prices"][1] if len(pm["prices"]) > 1 else 1.0 - yes_price

            # Skip markets with very low prices on both sides (no liquidity)
            if yes_price < 0.02 and no_price < 0.02:
                continue

            # Determine which team the "Yes" outcome refers to
            # Usually "Will X beat Y?" → X = Yes team
            yes_team_norm = None
            q = pm["question"].lower()
            # Find the FIRST team mentioned in the question — that's the "Yes" team
            first_pos = len(q)
            for alias, canonical in TEAM_ALIASES.items():
                pos = q.find(alias)
                if pos >= 0 and pos < first_pos:
                    first_pos = pos
                    yes_team_norm = canonical

            if not yes_team_norm:
                continue

            # Get consensus prob for the Yes team
            consensus_yes = None
            for team_name, prob in consensus.items():
                if normalize_team(team_name) == yes_team_norm:
                    consensus_yes = prob
                    break

            if consensus_yes is None:
                continue

            edge_yes = consensus_yes - yes_price
            edge_no = (1.0 - consensus_yes) - no_price

            game_label = f"{away} @ {home}"

            # Check Yes side
            if edge_yes >= EDGE_THRESHOLD:
                raw_matches.append({
                    "game": game_label,
                    "team": yes_team_norm,
                    "side": "YES",
                    "consensus_prob": consensus_yes,
                    "polymarket_price": yes_price,
                    "edge": edge_yes,
                    "token_id": pm["tokens"][0],
                    "condition_id": pm["condition_id"],
                    "question": pm["question"],
                    "volume": pm["volume"],
                    "num_bookmakers": num_books,
                    "game_time": game_time,
                    "sport": game.get("sport_key", "basketball_nba"),
                })

            # Check No side
            if edge_no >= EDGE_THRESHOLD and len(pm["tokens"]) > 1:
                no_team_norm = away_norm if yes_team_norm == home_norm else home_norm
                raw_matches.append({
                    "game": game_label,
                    "team": no_team_norm,
                    "side": "NO",
                    "consensus_prob": 1.0 - consensus_yes,
                    "polymarket_price": no_price,
                    "edge": edge_no,
                    "token_id": pm["tokens"][1],
                    "condition_id": pm["condition_id"],
                    "question": pm["question"],
                    "volume": pm["volume"],
                    "num_bookmakers": num_books,
                    "game_time": game_time,
                    "sport": game.get("sport_key", "basketball_nba"),
                })

    # Dedup: keep highest-volume market per (game, team, side)
    best = {}
    for m in raw_matches:
        key = (m["game"], m["team"], m["side"])
        if key not in best or m["volume"] > best[key]["volume"]:
            best[key] = m

    matches = sorted(best.values(), key=lambda x: x["edge"], reverse=True)
    return matches


# --- Position Sizing ---

def kelly_size(edge, market_price, bankroll):
    if edge <= 0 or market_price <= 0 or market_price >= 1:
        return 0
    kelly_f = edge / (1 - market_price)
    quarter_kelly = kelly_f * KELLY_FRACTION
    max_bet = bankroll * MAX_POSITION_PCT
    bet = min(bankroll * quarter_kelly, max_bet)
    shares = bet / market_price
    return max(0, shares)


# --- Commands ---

def _display_matches(matches, label=""):
    """Display matched arb opportunities."""
    if not matches:
        return
    if label:
        print(f"\n  --- {label} ---")
    print(f"\n  {'Market':>35}  {'Team':>12}  {'Cons%':>6}  {'PM$':>6}  {'Edge':>7}  {'Books':>5}  {'Signal':>8}")
    print(f"  {'-'*35}  {'-'*12}  {'-'*6}  {'-'*6}  {'-'*7}  {'-'*5}  {'-'*8}")

    for m in matches:
        signal = ">>> BUY" if m["edge"] >= EDGE_THRESHOLD else ""
        print(f"  {m['game'][:35]:>35}  {m['team']:>12}  "
              f"{m['consensus_prob']:>5.1%}  "
              f"${m['polymarket_price']:>.3f}  "
              f"{m['edge']:>+6.1%}  "
              f"{m['num_bookmakers']:>5}  {signal}")

    tradeable = sum(1 for m in matches if m["edge"] >= EDGE_THRESHOLD)
    print(f"\n  Tradeable signals: {tradeable}")


def cmd_scan(args):
    """Scan for sports odds arb opportunities (games + futures)."""
    all_matches = []

    # --- Game-by-game markets ---
    for sport in SPORTS_H2H:
        print(f"\n{'='*60}")
        print(f"  Scanning games: {sport}")
        print(f"{'='*60}")

        print("  Fetching sportsbook odds...")
        odds_games = fetch_odds(sport)
        if not odds_games:
            print("  No games found from The Odds API")
        else:
            print(f"  Found {len(odds_games)} upcoming games")

            print("  Discovering Polymarket game markets...")
            poly_markets = discover_sports_markets()
            if not poly_markets:
                print("  No game-by-game markets on Polymarket (discontinued since Nov 2025)")
            else:
                matches = match_odds_to_polymarket(odds_games, poly_markets)
                all_matches.extend(matches)
                _display_matches(matches, "Game Matches")

            # Show what games are available
            if odds_games and not poly_markets:
                print(f"\n  Odds API has {len(odds_games)} upcoming games but no matching Polymarket markets:")
                for g in odds_games[:5]:
                    consensus = calculate_consensus(g)
                    if consensus:
                        probs = ", ".join(f"{k}: {v:.1%}" for k, v in consensus.items())
                        print(f"    {g['away_team']} @ {g['home_team']} | {probs}")

    # --- Futures/Championship markets ---
    for sport in SPORTS_FUTURES:
        print(f"\n{'='*60}")
        print(f"  Scanning futures: {sport}")
        print(f"{'='*60}")

        print("  Fetching sportsbook futures odds...")
        futures_odds = fetch_futures_odds(sport)
        if not futures_odds:
            print("  No futures odds found")
            continue
        print(f"  Found odds for {len(futures_odds)} teams")

        print("  Discovering Polymarket futures markets...")
        poly_futures = discover_futures_markets()
        if not poly_futures:
            print("  No matching futures markets on Polymarket")
            continue
        print(f"  Found {len(poly_futures)} Polymarket futures markets")

        matches = match_futures_to_polymarket(futures_odds, poly_futures)
        all_matches.extend(matches)
        _display_matches(matches, "Futures Matches")

    if not all_matches:
        print("\n  No arb opportunities found across any market type.")


def cmd_trade(args):
    """Scan and place orders on arb opportunities."""
    dry_run = args.dry_run

    bankroll = get_balance()
    print(f"  Bankroll: ${bankroll:,.2f}")
    if bankroll < 10:
        print("  ERROR: Bankroll too low to trade (< $10)")
        return

    conn = init_db()
    client = get_client()
    trades_placed = 0
    total_exposure = 0.0
    max_exposure = bankroll * MAX_TOTAL_EXPOSURE_PCT

    # Count existing exposure
    existing_cost = conn.execute(
        "SELECT COALESCE(SUM(cost), 0) FROM trades WHERE status='placed'"
    ).fetchone()[0]
    total_exposure = float(existing_cost)
    print(f"  Existing exposure: ${total_exposure:,.2f} / ${max_exposure:,.2f} max")

    # Dedup: existing open positions
    existing = set()
    try:
        existing = {
            r[0] for r in
            conn.execute("SELECT token_id FROM trades WHERE status='placed'").fetchall()
        }
    except Exception:
        pass

    # Collect all matches from both game and futures markets
    all_matches = []

    # Game-by-game
    for sport in SPORTS_H2H:
        odds_games = fetch_odds(sport)
        if odds_games:
            poly_markets = discover_sports_markets()
            if poly_markets:
                all_matches.extend(match_odds_to_polymarket(odds_games, poly_markets))

    # Futures
    for sport in SPORTS_FUTURES:
        futures_odds = fetch_futures_odds(sport)
        if futures_odds:
            poly_futures = discover_futures_markets()
            if poly_futures:
                all_matches.extend(match_futures_to_polymarket(futures_odds, poly_futures))

    if not all_matches:
        print("  No arb opportunities found")
        conn.close()
        return

    # Sort by edge descending
    all_matches.sort(key=lambda x: x["edge"], reverse=True)
    print(f"  Found {len(all_matches)} potential matches")

    matches = all_matches

    for m in matches:
        if m["edge"] < EDGE_THRESHOLD:
            continue

        if m["token_id"] in existing:
            print(f"  SKIP {m['team']} ({m['game']}): already have open order")
            continue

        if total_exposure >= max_exposure:
            print(f"  STOP: Max exposure reached (${total_exposure:,.2f} >= ${max_exposure:,.2f})")
            break

        shares = kelly_size(m["edge"], m["polymarket_price"], bankroll)
        if shares < MIN_ORDER_SIZE:
            print(f"  SKIP {m['team']}: kelly size {shares:.1f} < {MIN_ORDER_SIZE} min")
            continue

        shares = round(shares)
        order_price = round(m["polymarket_price"] - MAKER_OFFSET, 3)
        order_price = max(0.01, min(0.99, order_price))
        cost = shares * order_price

        print(f"\n  >>> TRADE: {m['game']} — {m['team']} ({m['side']})")
        print(f"      Consensus: {m['consensus_prob']:.1%} vs Polymarket: ${m['polymarket_price']:.3f} = {m['edge']:+.1%} edge")
        print(f"      Books: {m['num_bookmakers']} | Game: {m['game_time'][:16] if m['game_time'] else '?'}")
        print(f"      Order: BUY {shares} shares @ ${order_price:.3f} (${cost:.2f})")

        order_id = None
        if not dry_run:
            try:
                order_args = OrderArgs(
                    token_id=m["token_id"],
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

        record_trade(conn, m.get("sport", "unknown"), m["game"], m["team"],
                     m["consensus_prob"], m["polymarket_price"],
                     m["edge"], order_price, shares, cost,
                     m["token_id"], m["condition_id"],
                     order_id or "DRY_RUN", m["num_bookmakers"],
                     m["game_time"])
        total_exposure += cost
        existing.add(m["token_id"])

    conn.close()
    mode = "DRY RUN" if dry_run else "LIVE"
    print(f"\n  Done. {trades_placed} trades placed [{mode}].")


def cmd_history(args):
    """Show trade history."""
    conn = init_db()
    limit = args.limit or 20
    rows = conn.execute(
        "SELECT ts, sport, game, team, consensus_prob, polymarket_price, "
        "edge, shares, order_price, cost, status, pnl, num_bookmakers "
        "FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()

    if not rows:
        print("No trades recorded.")
        return

    print(f"\n  {'Time':>19}  {'Game':>25}  {'Team':>10}  {'Cons%':>5}  "
          f"{'PM$':>5}  {'Edge':>6}  {'Shares':>6}  {'Cost':>7}  {'Status':>8}  {'PnL':>7}")

    for r in rows:
        ts, sport, game, team, cp, pp, edge, sh, op, cost, status, pnl, nb = r
        print(f"  {ts[:19]}  {game[:25]:>25}  {team:>10}  {cp:>4.0%}  "
              f"${pp:>.3f}  {edge:>+5.1%}  {sh:>6.0f}  ${cost:>6.2f}  {status:>8}  ${pnl:>6.2f}")


def cmd_pnl(args):
    """Show P&L summary."""
    conn = init_db()
    rows = conn.execute(
        "SELECT COUNT(*), SUM(cost), SUM(pnl), "
        "SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN status = 'lost' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN status = 'placed' THEN 1 ELSE 0 END), "
        "AVG(edge), AVG(num_bookmakers) "
        "FROM trades").fetchone()
    conn.close()

    total, cost, pnl, won, lost, pending, avg_edge, avg_books = rows
    if not total:
        print("No trades recorded.")
        return

    cost = cost or 0
    pnl = pnl or 0
    won = won or 0
    lost = lost or 0
    pending = pending or 0
    resolved = won + lost

    print(f"\n  === Sports Arb P&L ===")
    print(f"  Total trades: {total}")
    print(f"  Total invested: ${cost:,.2f}")
    print(f"  Resolved: {resolved} (Won: {won}, Lost: {lost})")
    print(f"  Pending: {pending}")
    if resolved > 0:
        wr = won / resolved * 100
        print(f"  Win rate: {wr:.1f}%")
    print(f"  Total PnL: ${pnl:,.2f}")
    print(f"  Avg edge at entry: {(avg_edge or 0):+.1%}")
    print(f"  Avg bookmakers: {(avg_books or 0):.1f}")


def cmd_resolve(args):
    """Check and resolve completed trades."""
    conn = init_db()
    pending = conn.execute(
        "SELECT id, token_id, team, game, order_price, shares, condition_id, game_time "
        "FROM trades WHERE status = 'placed'").fetchall()

    if not pending:
        print("No pending trades to resolve.")
        conn.close()
        return

    print(f"  Checking {len(pending)} pending trades...\n")
    resolved = 0

    for row in pending:
        trade_id, token_id, team, game, order_price, shares, condition_id, game_time = row

        # Check if game has ended (game_time + 4 hours for safety)
        if game_time:
            try:
                gt = datetime.fromisoformat(game_time.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) < gt + timedelta(hours=4):
                    continue  # Game not yet finished
            except Exception:
                pass

        # Check market resolution via Gamma API
        if not condition_id:
            continue

        try:
            r = requests.get(f"{GAMMA_API}/markets", params={
                "conditionId": condition_id,
                "limit": "1",
            }, timeout=15)
            data = r.json()
            if not data:
                continue

            market = data[0] if isinstance(data, list) else data
            if not market.get("closed"):
                continue

            prices = market.get("outcomePrices", "[]")
            if isinstance(prices, str):
                prices = json.loads(prices)
            yes_final = float(prices[0]) if prices else 0

            if yes_final >= 0.95:  # Won
                pnl = shares * (1.0 - order_price)
                conn.execute("UPDATE trades SET status='won', pnl=? WHERE id=?",
                             (pnl, trade_id))
                print(f"  WON: {team} ({game}) | PnL: +${pnl:.2f}")
            else:  # Lost
                pnl = -(shares * order_price)
                conn.execute("UPDATE trades SET status='lost', pnl=? WHERE id=?",
                             (pnl, trade_id))
                print(f"  LOST: {team} ({game}) | PnL: -${abs(pnl):.2f}")
            resolved += 1
        except Exception as e:
            print(f"  Error resolving {team} ({game}): {e}")

    conn.commit()
    conn.close()
    print(f"\n  Resolved {resolved} trades.")


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="Sports Odds Arb Bot")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("scan", help="Scan for sports arb opportunities")

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
