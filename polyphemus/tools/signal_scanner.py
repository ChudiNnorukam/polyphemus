#!/usr/bin/env python3
"""Combined signal scanner: whale consensus + odds divergence = actionable signals.

Cross-references three independent signal sources:
  1. Odds divergence: Polymarket mispriced vs sportsbook consensus
  2. Whale activity: directional wallets buying a specific outcome
  3. Whale consensus: multiple whales converging on same market

When 2+ signals align on the same outcome, that is a high-conviction trade.

Usage:
    python3 signal_scanner.py                    # Full scan + report
    python3 signal_scanner.py --sport basketball_nba  # NBA only
    python3 signal_scanner.py --monitor 300      # Continuous every 5 min
    python3 signal_scanner.py --resolve           # Check resolution of past signals

Requires: ODDS_API_KEY env var for divergence scanning.
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

# Import from sibling modules
TOOLS_DIR = Path(__file__).parent
POLY_DIR = TOOLS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(POLY_DIR))

try:
    from odds_divergence import (
        SPORTS, cmd_scan, init_db as init_div_db, fetch_polymarket_sports,
        scan_sport, fetch_odds, _parse_slug_teams, _normalize,
    )
    from whale_monitor import (
        LEADERBOARD_WALLETS, fetch_activity, fetch_positions,
        init_db as init_whale_db, _alias, _category,
    )
except ImportError as e:
    print(f"Import error: {e}", file=sys.stderr)
    print("Run from polyphemus/tools/ or ensure odds_divergence.py and whale_monitor.py exist", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SIGNAL_DB = POLY_DIR / "data" / "signals.db"
DIV_DB = POLY_DIR / "data" / "odds_divergence.db"
WHALE_DB = POLY_DIR / "data" / "whale_monitor.db"

# Wallets classified as DIRECTIONAL (>80% one-sided) from directionality analysis
DIRECTIONAL_WALLETS = {
    "0x8c80d213c0cbad777d06ee3f58f6ca4bc03102c3": "SecondWindCapital",
    "0xc2e7800b5af46e6093872b177b7a5e7f0563be51": "beachboy4",
    "0x93abbc022ce98d6f45d4444b594791cc4b7a9723": "gatorr",
    "0x2005d16a84ceefa912d4e380cd32e7ff827875ea": "RN1",
    "0xb45a797faa52b0fd8adc56d30382022b7b12192c": "bcda",
    "0x50b1db131a24a9d9450bbd0372a95d32ea88f076": "blindStaking",
    "0xf195721ad850377c96cd634457c70cd9e8308057": "lo34567Taipe",
    "0xbddf61af533ff524d27154e589d2d7a81510c684": "Countryside",
    "0x2b3ff45c91540e46fae1e0c72f61f4b049453446": "Mentallyillgambld",
    "0xdc876e6873772d38716fda7f2452a78d426d7ab6": "432614799197",
    "0x59a0744db1f39ff3afccd175f80e6e8dfc239a09": "Blessed-Sunshine",
    "0x8f037a2e4fd49d11267f4ab874ab7ba745ac64d6": "Anointed-Connect",
    "0xb6d6e99d3bfe055874a04279f659f009fd57be17": "JPMorgan101",
}

# Signal strength thresholds
MIN_DIV_PCT = 3.0          # Minimum divergence to count as a signal
MIN_WHALE_USDC = 1000      # Minimum whale trade size
MIN_WHALE_COUNT = 1        # Minimum unique whales for consensus signal
STRONG_DIV_PCT = 7.0       # "Strong" divergence threshold
STRONG_WHALE_USDC = 10000  # "Strong" whale position size

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
RESET = "\033[0m"

USER_AGENT = "PolyphemusSignalScanner/1.0"


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def init_signal_db(db_path: Path) -> sqlite3.Connection:
    """Initialize the combined signals database."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detected_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            sport TEXT,
            event_name TEXT,
            outcome TEXT,
            polymarket_slug TEXT,
            polymarket_price REAL,
            -- Divergence signal
            has_divergence INTEGER DEFAULT 0,
            divergence_pct REAL,
            sportsbook_consensus REAL,
            best_sportsbook TEXT,
            -- Whale signal
            has_whale INTEGER DEFAULT 0,
            whale_names TEXT,
            whale_count INTEGER DEFAULT 0,
            whale_total_usdc REAL DEFAULT 0,
            -- Combined score
            signal_strength TEXT,  -- STRONG / MODERATE / WEAK
            signal_score REAL,     -- 0-100 composite score
            -- Resolution tracking
            resolved INTEGER DEFAULT 0,
            resolution_outcome TEXT,
            was_profitable INTEGER,
            resolved_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sig_detected ON signals(detected_at DESC);
        CREATE INDEX IF NOT EXISTS idx_sig_slug ON signals(polymarket_slug);
        CREATE INDEX IF NOT EXISTS idx_sig_strength ON signals(signal_strength);
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Whale activity scanner (sports-focused)
# ---------------------------------------------------------------------------
def scan_whale_sports_activity(hours: int = 6) -> Dict[str, dict]:
    """Scan directional whale wallets for recent sports market activity.

    Returns: dict keyed by (event_slug or slug) -> {
        outcome, total_usdc, whale_count, whale_names, avg_price, trades
    }
    """
    cutoff = int(time.time()) - (hours * 3600)
    sports_kw = {"nba", "nfl", "nhl", "mlb", "epl", "ucl", "ufc", "soccer",
                 "spread", "moneyline", "win on 2026", "win on 2025"}

    # Aggregate: slug+outcome -> whale activity
    activity: Dict[str, dict] = {}

    for addr, alias in DIRECTIONAL_WALLETS.items():
        try:
            trades = fetch_activity(addr, limit=50)
        except Exception:
            continue

        for t in trades:
            ts = t.get("timestamp", 0)
            if ts < cutoff:
                continue

            slug = t.get("slug", "")
            event_slug = t.get("eventSlug", slug)
            title = t.get("title", "")
            combined = (slug + " " + title + " " + event_slug).lower()

            # Filter to sports
            is_sports = any(kw in combined for kw in sports_kw)
            if not is_sports:
                continue

            side = t.get("side", "")
            if side != "BUY":
                continue

            outcome = t.get("outcome", "")
            key = f"{event_slug}::{outcome}"
            usdc = float(t.get("usdcSize", 0))

            if key not in activity:
                activity[key] = {
                    "event_slug": event_slug,
                    "slug": slug,
                    "title": title,
                    "outcome": outcome,
                    "total_usdc": 0,
                    "whale_count": 0,
                    "whale_names": set(),
                    "avg_price": 0,
                    "prices": [],
                    "trades": [],
                }

            entry = activity[key]
            entry["total_usdc"] += usdc
            entry["whale_names"].add(alias)
            entry["whale_count"] = len(entry["whale_names"])
            entry["prices"].append(float(t.get("price", 0)))
            entry["trades"].append(t)

        time.sleep(0.3)  # Rate limit between wallets

    # Compute avg price
    for entry in activity.values():
        if entry["prices"]:
            entry["avg_price"] = sum(entry["prices"]) / len(entry["prices"])
        entry["whale_names"] = sorted(entry["whale_names"])  # Convert set to list

    return activity


# ---------------------------------------------------------------------------
# Cross-reference: divergence + whale activity
# ---------------------------------------------------------------------------
def cross_reference(
    divergences: List[dict],
    whale_activity: Dict[str, dict],
) -> List[dict]:
    """Cross-reference divergences with whale activity to produce combined signals.

    Returns list of signal dicts sorted by signal_score descending.
    """
    signals = []

    # Index whale activity by normalized slug parts for fuzzy matching
    whale_by_slug = {}
    for key, entry in whale_activity.items():
        event_slug = entry.get("event_slug", "").lower()
        slug = entry.get("slug", "").lower()
        outcome = entry.get("outcome", "").lower()
        whale_by_slug[f"{event_slug}::{outcome}"] = entry
        whale_by_slug[f"{slug}::{outcome}"] = entry

    # Process each divergence and check for whale confirmation
    seen_slugs = set()  # Deduplicate

    for div in divergences:
        slug = div.get("polymarket_slug", "").lower()
        outcome = _normalize(div.get("polymarket_outcome", ""))
        dedup_key = f"{slug}::{outcome}"

        if dedup_key in seen_slugs:
            continue
        seen_slugs.add(dedup_key)

        # Look for whale activity on same slug+outcome
        whale_match = None
        for wkey, wentry in whale_activity.items():
            w_slug = wentry.get("event_slug", "").lower()
            w_outcome = _normalize(wentry.get("outcome", ""))

            # Match by slug overlap AND outcome
            if (w_slug in slug or slug in w_slug) and (
                w_outcome == outcome or
                w_outcome in outcome or
                outcome in w_outcome
            ):
                whale_match = wentry
                break

        # Build signal
        div_pct = div.get("divergence_pct", 0)
        is_buy_signal = div_pct < 0  # Poly cheaper than consensus = buy

        signal = {
            "sport": div.get("sport", ""),
            "event_name": div.get("event_name", ""),
            "outcome": div.get("polymarket_outcome", ""),
            "polymarket_slug": div.get("polymarket_slug", ""),
            "polymarket_price": div.get("polymarket_price", 0),
            "has_divergence": True,
            "divergence_pct": div_pct,
            "sportsbook_consensus": div.get("sportsbook_consensus_prob", 0),
            "best_sportsbook": div.get("best_sportsbook", ""),
            "has_whale": whale_match is not None,
            "whale_names": whale_match["whale_names"] if whale_match else [],
            "whale_count": whale_match["whale_count"] if whale_match else 0,
            "whale_total_usdc": whale_match["total_usdc"] if whale_match else 0,
            "match_confidence": div.get("match_confidence", 0),
            "odds_event_date": div.get("odds_event_date", ""),
        }

        # Compute signal score (0-100)
        score = 0

        # Divergence component (0-50)
        if is_buy_signal:
            abs_div = abs(div_pct)
            if abs_div >= STRONG_DIV_PCT:
                score += 40
            elif abs_div >= MIN_DIV_PCT:
                score += 20 + (abs_div - MIN_DIV_PCT) / (STRONG_DIV_PCT - MIN_DIV_PCT) * 20
        else:
            # Poly expensive: penalty signal (fade)
            score += 10  # Still informational

        # Match confidence component (0-10)
        confidence = div.get("match_confidence", 0)
        score += confidence * 10

        # Whale component (0-40)
        if whale_match:
            if whale_match["whale_count"] >= 2:
                score += 25
            else:
                score += 15
            if whale_match["total_usdc"] >= STRONG_WHALE_USDC:
                score += 15
            elif whale_match["total_usdc"] >= MIN_WHALE_USDC:
                score += 5 + (whale_match["total_usdc"] - MIN_WHALE_USDC) / (STRONG_WHALE_USDC - MIN_WHALE_USDC) * 10

        signal["signal_score"] = min(score, 100)

        # Classify strength
        if score >= 60:
            signal["signal_strength"] = "STRONG"
        elif score >= 35:
            signal["signal_strength"] = "MODERATE"
        else:
            signal["signal_strength"] = "WEAK"

        signals.append(signal)

    # Also add whale-only signals (no divergence data)
    for key, entry in whale_activity.items():
        event_slug = entry.get("event_slug", "").lower()
        outcome = _normalize(entry.get("outcome", ""))
        dedup_key = f"{event_slug}::{outcome}"

        if dedup_key in seen_slugs:
            continue
        seen_slugs.add(dedup_key)

        if entry["whale_count"] < MIN_WHALE_COUNT:
            continue
        if entry["total_usdc"] < MIN_WHALE_USDC:
            continue

        score = 0
        if entry["whale_count"] >= 2:
            score += 25
        else:
            score += 15
        if entry["total_usdc"] >= STRONG_WHALE_USDC:
            score += 15
        elif entry["total_usdc"] >= MIN_WHALE_USDC:
            score += 5

        signal = {
            "sport": "",
            "event_name": entry.get("title", ""),
            "outcome": entry.get("outcome", ""),
            "polymarket_slug": entry.get("slug", ""),
            "polymarket_price": entry.get("avg_price", 0),
            "has_divergence": False,
            "divergence_pct": 0,
            "sportsbook_consensus": 0,
            "best_sportsbook": "",
            "has_whale": True,
            "whale_names": entry["whale_names"],
            "whale_count": entry["whale_count"],
            "whale_total_usdc": entry["total_usdc"],
            "match_confidence": 0,
            "odds_event_date": "",
            "signal_score": min(score, 100),
            "signal_strength": "STRONG" if score >= 60 else "MODERATE" if score >= 35 else "WEAK",
        }
        signals.append(signal)

    # Sort by score descending
    signals.sort(key=lambda s: s["signal_score"], reverse=True)
    return signals


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------
def format_signal(sig: dict, rank: int) -> str:
    """Format a combined signal for terminal display."""
    strength = sig["signal_strength"]
    score = sig["signal_score"]

    # Color by strength
    if strength == "STRONG":
        str_color = GREEN
        badge = f"{GREEN}{BOLD}STRONG{RESET}"
    elif strength == "MODERATE":
        str_color = YELLOW
        badge = f"{YELLOW}{BOLD}MODERATE{RESET}"
    else:
        str_color = DIM
        badge = f"{DIM}WEAK{RESET}"

    # Direction
    div_pct = sig.get("divergence_pct", 0)
    if div_pct < 0:
        direction = f"{GREEN}BUY{RESET}"
        direction_detail = f"Poly {abs(div_pct):.1f}% cheaper"
    elif div_pct > 0:
        direction = f"{RED}FADE{RESET}"
        direction_detail = f"Poly {abs(div_pct):.1f}% expensive"
    else:
        direction = f"{CYAN}WHALE ONLY{RESET}"
        direction_detail = "No sportsbook comparison"

    # Event time
    event_date = sig.get("odds_event_date", "")
    if event_date:
        try:
            dt = datetime.fromisoformat(event_date.replace("Z", "+00:00"))
            date_str = dt.strftime("%b %d %H:%M UTC")
        except Exception:
            date_str = ""
    else:
        date_str = ""

    lines = [
        f"\n  {str_color}#{rank}{RESET} {badge} (score: {score:.0f}/100) {direction}",
        f"     {BOLD}{sig['event_name']}{RESET}" + (f" ({sig['sport']}, {date_str})" if date_str else ""),
        f"     Outcome: {BOLD}{sig['outcome']}{RESET} @ ${sig['polymarket_price']:.3f}",
    ]

    # Divergence detail
    if sig["has_divergence"]:
        consensus = sig["sportsbook_consensus"]
        lines.append(
            f"     {CYAN}Divergence:{RESET} {direction_detail} "
            f"(PM={sig['polymarket_price']*100:.1f}% vs SB={consensus*100:.1f}%, "
            f"best: {sig['best_sportsbook']})"
        )

    # Whale detail
    if sig["has_whale"]:
        names = ", ".join(sig["whale_names"][:5])
        lines.append(
            f"     {MAGENTA}Whales:{RESET} {sig['whale_count']} whale(s) "
            f"buying ${sig['whale_total_usdc']:,.0f} total "
            f"({names})"
        )

    if not sig["has_divergence"] and not sig["has_whale"]:
        lines.append(f"     {DIM}(insufficient data for signal){RESET}")

    # Actionable summary
    if sig["has_divergence"] and sig["has_whale"] and div_pct < 0:
        lines.append(
            f"     {GREEN}{BOLD}>>> CONFIRMED: Divergence + Whale alignment. "
            f"High-conviction BUY signal.{RESET}"
        )
    elif sig["has_divergence"] and div_pct < -STRONG_DIV_PCT:
        lines.append(
            f"     {GREEN}>>> Large divergence. Watch for whale entry to confirm.{RESET}"
        )

    return "\n".join(lines)


def save_signal(conn: sqlite3.Connection, sig: dict) -> int:
    """Persist a signal to the database."""
    whale_names_str = ",".join(sig.get("whale_names", []))
    cursor = conn.execute(
        """INSERT INTO signals
        (sport, event_name, outcome, polymarket_slug, polymarket_price,
         has_divergence, divergence_pct, sportsbook_consensus, best_sportsbook,
         has_whale, whale_names, whale_count, whale_total_usdc,
         signal_strength, signal_score)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            sig["sport"], sig["event_name"], sig["outcome"],
            sig["polymarket_slug"], sig["polymarket_price"],
            1 if sig["has_divergence"] else 0, sig.get("divergence_pct", 0),
            sig.get("sportsbook_consensus", 0), sig.get("best_sportsbook", ""),
            1 if sig["has_whale"] else 0, whale_names_str,
            sig.get("whale_count", 0), sig.get("whale_total_usdc", 0),
            sig["signal_strength"], sig["signal_score"],
        ),
    )
    conn.commit()
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# Resolution tracker
# ---------------------------------------------------------------------------
def cmd_resolve(db_path: Path):
    """Check resolution of past signals against Polymarket outcomes."""
    conn = init_signal_db(db_path)
    bold, dim, reset, green, red = BOLD, DIM, RESET, GREEN, RED

    # Get unresolved signals
    rows = conn.execute(
        """SELECT id, detected_at, event_name, outcome, polymarket_slug,
                  polymarket_price, signal_strength, signal_score,
                  has_divergence, has_whale, divergence_pct
           FROM signals WHERE resolved = 0
           ORDER BY detected_at ASC"""
    ).fetchall()

    if not rows:
        print(f"\n  No unresolved signals to check.")
        conn.close()
        return

    print(f"\n{bold}=== SIGNAL RESOLUTION CHECK ==={reset}")
    print(f"  Checking {len(rows)} unresolved signals...\n")

    resolved_count = 0
    wins = 0
    losses = 0

    for row in rows:
        sig_id, detected, event, outcome, slug, price, strength, score, has_div, has_whale, div_pct = row

        # Try to get current price from Polymarket
        # A resolved market has price near 0 or 1
        try:
            from odds_divergence import _http_get, GAMMA_API
            url = f"{GAMMA_API}/markets?slug={slug}&limit=1"
            result = _http_get(url, timeout=5)
            if not result or not isinstance(result, list) or len(result) == 0:
                continue

            market = result[0]
            outcome_prices = market.get("outcomePrices", "")
            if isinstance(outcome_prices, str):
                try:
                    outcome_prices = json.loads(outcome_prices)
                except Exception:
                    continue

            outcomes = market.get("outcomes", "")
            if isinstance(outcomes, str):
                try:
                    outcomes = json.loads(outcomes)
                except Exception:
                    continue

            if not outcomes or not outcome_prices or len(outcomes) != len(outcome_prices):
                continue

            # Check if market is resolved (price near 0 or 1)
            prices = [float(p) for p in outcome_prices]
            max_price = max(prices)
            if max_price < 0.95:
                continue  # Not yet resolved

            # Find which outcome won
            winner_idx = prices.index(max_price)
            winner = outcomes[winner_idx] if winner_idx < len(outcomes) else "?"

            # Did our signal's outcome win?
            was_buy = (div_pct < 0) if has_div else True
            outcome_norm = _normalize(outcome)
            winner_norm = _normalize(winner)

            if outcome_norm == winner_norm or outcome_norm in winner_norm or winner_norm in outcome_norm:
                profitable = 1 if was_buy else 0
            else:
                profitable = 0 if was_buy else 1

            # Update DB
            conn.execute(
                """UPDATE signals SET resolved=1, was_profitable=?,
                   resolution_outcome=?, resolved_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
                   WHERE id=?""",
                (profitable, winner, sig_id),
            )
            conn.commit()
            resolved_count += 1

            if profitable:
                wins += 1
                color = green
                result_str = "WIN"
            else:
                losses += 1
                color = red
                result_str = "LOSS"

            print(f"  {color}{result_str}{reset} | {strength:8s} (score {score:.0f}) | "
                  f"{outcome} @ ${price:.2f} | {event[:50]} | {dim}Winner: {winner}{reset}")

        except Exception:
            continue

        time.sleep(0.3)

    # Show stats from all resolved signals
    all_resolved = conn.execute(
        """SELECT signal_strength, COUNT(*) as n,
                  SUM(CASE WHEN was_profitable=1 THEN 1 ELSE 0 END) as wins
           FROM signals WHERE resolved=1
           GROUP BY signal_strength"""
    ).fetchall()

    if all_resolved:
        print(f"\n{bold}--- Resolution Stats ---{reset}")
        total_n = 0
        total_w = 0
        for strength, n, w in all_resolved:
            wr = w / n * 100 if n > 0 else 0
            wr_color = green if wr > 50 else (YELLOW if wr > 40 else red)
            print(f"  {strength:8s}: {n:3d} resolved | {wr_color}WR = {wr:.1f}%{reset} ({w}/{n})")
            total_n += n
            total_w += w
        if total_n > 0:
            total_wr = total_w / total_n * 100
            wr_color = green if total_wr > 50 else (YELLOW if total_wr > 40 else red)
            print(f"  {'TOTAL':8s}: {total_n:3d} resolved | {wr_color}WR = {total_wr:.1f}%{reset} ({total_w}/{total_n})")
        if total_n < 50:
            print(f"\n  {dim}CAUTION: n={total_n} is too small. Need n>=50 for reliable WR. "
                  f"Low-price bets (e.g., $0.03) are +EV if mispriced but lose most individual bets.{reset}")
    elif resolved_count == 0:
        print(f"\n  {dim}No signals resolved yet (markets still active). Check back after games end.{reset}")

    conn.close()


# ---------------------------------------------------------------------------
# Main scan command
# ---------------------------------------------------------------------------
def cmd_scan(
    api_key: str,
    db_path: Path,
    sport_filter: Optional[str] = None,
    whale_hours: int = 6,
    min_score: float = 0,
):
    """Run full combined scan: divergence + whale activity."""
    conn = init_signal_db(db_path)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    sport_str = SPORTS.get(sport_filter, sport_filter) if sport_filter else "ALL SPORTS"

    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  COMBINED SIGNAL SCANNER ({ts}){RESET}")
    print(f"{BOLD}{'='*60}{RESET}")
    print(f"  Sport:       {sport_str}")
    print(f"  Whale window: last {whale_hours}h")
    print(f"  Min score:   {min_score:.0f}")
    print()

    # Step 1: Scan divergences
    print(f"  {DIM}[1/3] Scanning odds divergences...{RESET}", flush=True)
    div_conn = init_div_db(DIV_DB)
    all_poly_markets = fetch_polymarket_sports()
    sports_to_scan = [sport_filter] if sport_filter else list(SPORTS.keys())
    all_divergences = []

    for sport in sports_to_scan:
        odds_events = fetch_odds(sport, api_key)
        if not odds_events:
            continue
        divs = scan_sport(
            sport, api_key, div_conn,
            poly_markets_cache=all_poly_markets,
            min_div=MIN_DIV_PCT,
        )
        all_divergences.extend(divs)
        time.sleep(0.5)

    div_conn.close()
    print(f"  {DIM}  Found {len(all_divergences)} divergences across {len(sports_to_scan)} sports{RESET}")

    # Step 2: Scan whale activity
    print(f"  {DIM}[2/3] Scanning whale activity (last {whale_hours}h)...{RESET}", flush=True)
    whale_activity = scan_whale_sports_activity(hours=whale_hours)
    active_whales = sum(1 for v in whale_activity.values() if v["whale_count"] > 0)
    print(f"  {DIM}  Found {len(whale_activity)} whale positions across {active_whales} markets{RESET}")

    # Step 3: Cross-reference
    print(f"  {DIM}[3/3] Cross-referencing signals...{RESET}", flush=True)
    signals = cross_reference(all_divergences, whale_activity)

    # Filter by min score
    if min_score > 0:
        signals = [s for s in signals if s["signal_score"] >= min_score]

    # Save to DB
    for sig in signals:
        save_signal(conn, sig)

    conn.close()

    # Display results
    if not signals:
        print(f"\n  No combined signals found above score threshold.")
        print(f"  {DIM}Divergences: {len(all_divergences)} | Whale positions: {len(whale_activity)}{RESET}")
        print(f"  {DIM}Markets may be efficiently priced right now.{RESET}")
        return

    # Separate by strength
    strong = [s for s in signals if s["signal_strength"] == "STRONG"]
    moderate = [s for s in signals if s["signal_strength"] == "MODERATE"]
    weak = [s for s in signals if s["signal_strength"] == "WEAK"]

    confirmed = [s for s in signals if s["has_divergence"] and s["has_whale"] and s["divergence_pct"] < 0]

    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  RESULTS: {len(signals)} signal(s){RESET}")
    if confirmed:
        print(f"  {GREEN}{BOLD}  {len(confirmed)} CONFIRMED (divergence + whale alignment){RESET}")
    print(f"  {GREEN}  {len(strong)} strong{RESET} | "
          f"{YELLOW}{len(moderate)} moderate{RESET} | "
          f"{DIM}{len(weak)} weak{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")

    # Show all signals ranked
    for i, sig in enumerate(signals, 1):
        if i > 20:  # Cap display at 20
            remaining = len(signals) - 20
            print(f"\n  {DIM}... and {remaining} more (saved to {db_path}){RESET}")
            break
        print(format_signal(sig, i))

    # Summary
    print(f"\n{BOLD}--- Action Summary ---{RESET}")
    if confirmed:
        print(f"  {GREEN}{BOLD}HIGH CONVICTION BUYS (divergence + whale confirmed):{RESET}")
        for s in confirmed:
            print(f"    {GREEN}BUY{RESET} {s['outcome']} @ ${s['polymarket_price']:.3f} "
                  f"(div: {s['divergence_pct']:+.1f}%, "
                  f"whales: {', '.join(s['whale_names'][:3])})")
    else:
        print(f"  {DIM}No confirmed signals (divergence + whale alignment) right now.{RESET}")
        if strong:
            print(f"  {YELLOW}Watch these strong signals for whale entry:{RESET}")
            for s in strong[:3]:
                print(f"    {s['outcome']} @ ${s['polymarket_price']:.3f} "
                      f"(div: {s['divergence_pct']:+.1f}%)")

    print(f"\n  Signals saved to: {db_path}")
    print(f"  Run '{Path(__file__).name} --resolve' after games to track accuracy\n")


# ---------------------------------------------------------------------------
# Monitor mode
# ---------------------------------------------------------------------------
def cmd_monitor(
    api_key: str,
    db_path: Path,
    sport_filter: Optional[str],
    interval: int,
    whale_hours: int,
):
    """Continuous monitoring loop."""
    print(f"\n{BOLD}=== SIGNAL SCANNER MONITOR ==={RESET}")
    print(f"  Interval: {interval}s | Whale window: {whale_hours}h")
    print(f"  {DIM}Press Ctrl+C to stop{RESET}\n")

    cycle = 0
    try:
        while True:
            cycle += 1
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
            print(f"\n{DIM}[{ts}] Cycle {cycle}...{RESET}", flush=True)

            cmd_scan(
                api_key=api_key,
                db_path=db_path,
                sport_filter=sport_filter,
                whale_hours=whale_hours,
                min_score=25,  # Only show moderate+ in monitor mode
            )

            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n\n  Monitor stopped after {cycle} cycles.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Combined signal scanner: odds divergence + whale consensus",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--sport", choices=list(SPORTS.keys()), default=None,
        help="Limit to a specific sport",
    )
    parser.add_argument(
        "--monitor", type=int, metavar="SECONDS", default=None,
        help="Continuous monitoring with given interval (e.g., 300 = 5 min)",
    )
    parser.add_argument(
        "--resolve", action="store_true",
        help="Check resolution of past signals",
    )
    parser.add_argument(
        "--whale-hours", type=int, default=6,
        help="Hours to look back for whale activity (default: 6)",
    )
    parser.add_argument(
        "--min-score", type=float, default=0,
        help="Minimum signal score to display (default: 0 = show all)",
    )
    parser.add_argument(
        "--db", default=str(SIGNAL_DB),
        help=f"SQLite database path (default: {SIGNAL_DB})",
    )

    args = parser.parse_args()
    db_path = Path(args.db)

    if args.resolve:
        cmd_resolve(db_path)
        return

    # API key required for scan
    api_key = os.environ.get("ODDS_API_KEY", "").strip()
    if not api_key:
        print("\nError: ODDS_API_KEY environment variable not set.", file=sys.stderr)
        print("Get a free key at https://the-odds-api.com/", file=sys.stderr)
        sys.exit(1)

    if args.monitor is not None:
        cmd_monitor(
            api_key=api_key,
            db_path=db_path,
            sport_filter=args.sport,
            interval=args.monitor,
            whale_hours=args.whale_hours,
        )
    else:
        cmd_scan(
            api_key=api_key,
            db_path=db_path,
            sport_filter=args.sport,
            whale_hours=args.whale_hours,
            min_score=args.min_score,
        )


if __name__ == "__main__":
    main()
