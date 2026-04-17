"""Resolution calibration: compare Open-Meteo forecasts against Polymarket outcomes.

Measures systematic bias between our forecast source (Open-Meteo) and Polymarket's
resolution sources (Weather Underground, KMA, Met Office, etc.). A 2-4F/1-2C
mismatch was identified by DARIO research (Apr 13 2026). This script quantifies it.

The key question: when we predict "Seoul will be 23C or higher" at 78% probability,
does Polymarket's resolution source (KMA) agree with Open-Meteo's temperature? If
there's a consistent bias, our entire edge estimate is wrong.

Usage:
    # Collect calibration data for past resolved markets
    python -m polyphemus.prediction_markets.weather.calibration collect

    # Show calibration report
    python -m polyphemus.prediction_markets.weather.calibration report

    # Collect + report
    python -m polyphemus.prediction_markets.weather.calibration run

Design:
    For each resolved Polymarket weather market, we record:
    - Open-Meteo forecast (what we predicted)
    - Open-Meteo observed actual (what Open-Meteo says happened)
    - Polymarket resolution (what actually counted for payout)
    - The delta between Open-Meteo observed and Polymarket resolution

    If Open-Meteo observed and Polymarket resolution disagree on the outcome
    (e.g., Open-Meteo says 22C but Polymarket resolved YES on "23C or higher"),
    that's a resolution mismatch. High mismatch rate means our model is calibrated
    to the wrong ground truth.
"""
import argparse
import asyncio
import json
import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

from .config import CITIES
from .resolver import fetch_resolved_temperature_markets, fetch_actual_temp
from .detector import classify_question

logger = logging.getLogger(__name__)

# 10 cities covering major resolution source diversity
# Selection criteria: high market volume, different resolution sources, mix of F/C
CALIBRATION_CITIES = [
    "new-york-city",   # wunderground (F) - highest volume US city
    "los-angeles",     # wunderground (F) - West Coast
    "chicago",         # wunderground (F) - Midwest
    "london",          # metoffice (C) - highest volume EU city
    "paris",           # meteofrance (C)
    "seoul",           # kma (C) - where we have the most paper trades
    "tokyo",           # jma (C) - major Asian market
    "toronto",         # environment-canada (C)
    "tel-aviv",        # ims (C) - Middle East
    "sao-paulo",       # inmet (C) - Southern Hemisphere
]

DB_PATH = Path(__file__).parent / "data" / "calibration.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS calibration (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collected_at TEXT NOT NULL,
    city TEXT NOT NULL,
    market_date TEXT NOT NULL,
    condition_id TEXT,
    question TEXT,
    question_type TEXT,
    temp_threshold INTEGER NOT NULL,
    unit TEXT NOT NULL,
    direction TEXT,
    market_price REAL,
    polymarket_outcome TEXT NOT NULL CHECK(polymarket_outcome IN ('YES', 'NO')),
    open_meteo_temp_c REAL,
    open_meteo_temp_f REAL,
    open_meteo_source TEXT,
    open_meteo_outcome TEXT CHECK(open_meteo_outcome IN ('YES', 'NO', NULL)),
    outcome_match INTEGER,
    temp_delta_c REAL,
    forecast_temp_c REAL,
    forecast_error_c REAL,
    UNIQUE(city, market_date, condition_id)
);

CREATE INDEX IF NOT EXISTS idx_calibration_city ON calibration(city);
CREATE INDEX IF NOT EXISTS idx_calibration_date ON calibration(market_date);
"""

GAMMA_BASE = "https://gamma-api.polymarket.com"


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


async def _fetch_temperature_events(
    client: httpx.AsyncClient,
    closed: bool = True,
) -> list[dict]:
    """Fetch temperature events from Gamma API."""
    events = []
    offset = 0
    limit = 100

    while True:
        try:
            resp = await client.get(
                f"{GAMMA_BASE}/events",
                params={
                    "tag_slug": "temperature",
                    "closed": str(closed).lower(),
                    "limit": limit,
                    "offset": offset,
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("Gamma API error: %s", exc)
            break

        batch = resp.json()
        if not isinstance(batch, list):
            break

        events.extend(batch)
        if len(batch) < limit:
            break
        offset += limit

    return events


def _parse_market_info(market: dict, event: dict) -> dict | None:
    """Extract city, date, threshold, unit, question type from a market."""
    question = market.get("question", "") or event.get("title", "")
    if not question:
        return None

    cid = market.get("conditionId")
    if not cid:
        return None

    # Parse resolution from outcome prices
    prices_raw = market.get("outcomePrices", "[]")
    try:
        prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
        if not prices:
            return None
        yes_price = float(prices[0])
    except (json.JSONDecodeError, IndexError, ValueError, TypeError):
        return None

    if yes_price >= 0.99:
        outcome = "YES"
    elif yes_price <= 0.01:
        outcome = "NO"
    else:
        return None  # not clearly resolved

    # Parse city from event title or question
    title = (event.get("title") or "").lower()
    q_lower = question.lower()
    city_key = None
    # Common abbreviations that appear in Polymarket titles
    _ALIASES = {"nyc": "new-york-city", "la": "los-angeles", "sf": "san-francisco"}
    for alias, mapped_key in _ALIASES.items():
        if alias in title.split() or alias in q_lower.split():
            city_key = mapped_key
            break
    if not city_key:
        for key, cfg in CITIES.items():
            display_lower = cfg["display"].lower()
            if display_lower in title or display_lower in q_lower or key in title:
                city_key = key
                break

    if not city_key or city_key not in CALIBRATION_CITIES:
        return None

    # Parse temperature threshold from question
    import re
    temp_match = re.search(r"(\d+)\s*[°]?\s*([CF])", question)
    if not temp_match:
        return None

    threshold = int(temp_match.group(1))
    unit = temp_match.group(2)

    # Parse date from event
    end_date_str = event.get("endDate") or market.get("endDate")
    if not end_date_str:
        return None

    try:
        # Gamma dates can be ISO format
        end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        return None

    q_type = classify_question(question)

    return {
        "city": city_key,
        "market_date": end_date.isoformat(),
        "condition_id": cid,
        "question": question,
        "question_type": q_type,
        "temp_threshold": threshold,
        "unit": unit,
        "outcome": outcome,
        "market_price": yes_price,
    }


async def collect(days_back: int = 14) -> dict:
    """Collect calibration data from resolved Polymarket temperature markets.

    For each resolved market in calibration cities:
    1. Record the Polymarket outcome (ground truth for payouts)
    2. Fetch Open-Meteo observed temperature for that city/date
    3. Compute what Open-Meteo would have resolved
    4. Record whether they match

    Args:
        days_back: How far back to look for resolved markets.

    Returns:
        Summary dict with counts.
    """
    conn = get_db()
    now_iso = datetime.now(timezone.utc).isoformat()

    print(f"Collecting calibration data (last {days_back} days)...")
    print(f"Cities: {', '.join(CITIES[c]['display'] for c in CALIBRATION_CITIES)}")
    print()

    async with httpx.AsyncClient(timeout=30) as client:
        events = await _fetch_temperature_events(client, closed=True)

    print(f"Fetched {len(events)} resolved temperature events from Gamma API")

    # Parse all markets
    parsed = []
    for event in events:
        for market in event.get("markets", []):
            info = _parse_market_info(market, event)
            if info:
                parsed.append(info)

    print(f"Parsed {len(parsed)} markets in calibration cities")

    # Filter to recent dates
    cutoff = (date.today() - timedelta(days=days_back)).isoformat()
    recent = [p for p in parsed if p["market_date"] >= cutoff]
    print(f"  {len(recent)} within last {days_back} days")

    # Deduplicate against existing data
    existing = set()
    for row in conn.execute("SELECT city, market_date, condition_id FROM calibration"):
        existing.add((row["city"], row["market_date"], row["condition_id"]))

    new_markets = [p for p in recent if (p["city"], p["market_date"], p["condition_id"]) not in existing]
    print(f"  {len(new_markets)} new (not already in DB)")
    print()

    if not new_markets:
        print("No new calibration data to collect.")
        conn.close()
        return {"collected": 0, "mismatches": 0}

    # Fetch Open-Meteo actuals for each unique city/date
    city_dates = {}
    for m in new_markets:
        key = (m["city"], m["market_date"])
        if key not in city_dates:
            city_dates[key] = None

    print(f"Fetching Open-Meteo actuals for {len(city_dates)} city-date pairs...")
    for (city_key, date_str) in city_dates:
        td = date.fromisoformat(date_str)
        temp_data = await fetch_actual_temp(city_key, td)
        city_dates[(city_key, date_str)] = temp_data
        if temp_data:
            city_cfg = CITIES[city_key]
            unit = city_cfg["unit"]
            display_temp = temp_data["temp_max_f"] if unit == "F" else temp_data["temp_max_c"]
            print(f"  {city_cfg['display']:15s} {date_str}: {display_temp}{chr(176)}{unit} ({temp_data['source']})", flush=True)
        else:
            print(f"  {CITIES[city_key]['display']:15s} {date_str}: NO DATA", flush=True)

    print()

    # Insert calibration records
    collected = 0
    mismatches = 0

    for m in new_markets:
        temp_data = city_dates.get((m["city"], m["market_date"]))

        om_temp_c = temp_data["temp_max_c"] if temp_data else None
        om_temp_f = temp_data["temp_max_f"] if temp_data else None
        om_source = temp_data["source"] if temp_data else None

        # Compute what Open-Meteo would resolve
        om_outcome = None
        if temp_data:
            from .resolver import determine_outcome
            om_outcome = determine_outcome(
                question=m["question"],
                temp_threshold=m["temp_threshold"],
                unit=m["unit"],
                actual_temp_c=om_temp_c,
                actual_temp_f=om_temp_f,
            )

        outcome_match = None
        if om_outcome is not None:
            outcome_match = 1 if om_outcome == m["outcome"] else 0
            if outcome_match == 0:
                mismatches += 1

        # Temperature delta (Open-Meteo vs threshold, in Celsius for uniformity)
        temp_delta_c = None
        if om_temp_c is not None:
            if m["unit"] == "F":
                threshold_c = (m["temp_threshold"] - 32) * 5 / 9
            else:
                threshold_c = float(m["temp_threshold"])
            temp_delta_c = round(om_temp_c - threshold_c, 2)

        conn.execute(
            """INSERT OR IGNORE INTO calibration (
                collected_at, city, market_date, condition_id, question,
                question_type, temp_threshold, unit, direction, market_price,
                polymarket_outcome, open_meteo_temp_c, open_meteo_temp_f,
                open_meteo_source, open_meteo_outcome, outcome_match,
                temp_delta_c, forecast_temp_c, forecast_error_c
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                now_iso, m["city"], m["market_date"], m["condition_id"],
                m["question"], m["question_type"], m["temp_threshold"],
                m["unit"], None, m["market_price"],
                m["outcome"], om_temp_c, om_temp_f, om_source,
                om_outcome, outcome_match, temp_delta_c, None, None,
            ),
        )
        collected += 1

    conn.commit()
    conn.close()

    print(f"Collected {collected} calibration records ({mismatches} outcome mismatches)")
    return {"collected": collected, "mismatches": mismatches}


def report() -> None:
    """Print calibration report from collected data."""
    conn = get_db()

    total = conn.execute("SELECT COUNT(*) FROM calibration").fetchone()[0]
    if total == 0:
        print("No calibration data. Run 'collect' first.")
        conn.close()
        return

    print("=" * 70)
    print("RESOLUTION CALIBRATION REPORT")
    print("=" * 70)

    # Overall stats
    with_om = conn.execute(
        "SELECT COUNT(*) FROM calibration WHERE open_meteo_outcome IS NOT NULL"
    ).fetchone()[0]
    matches = conn.execute(
        "SELECT COUNT(*) FROM calibration WHERE outcome_match = 1"
    ).fetchone()[0]
    mismatches = conn.execute(
        "SELECT COUNT(*) FROM calibration WHERE outcome_match = 0"
    ).fetchone()[0]

    match_rate = round(100 * matches / with_om, 1) if with_om > 0 else 0
    mismatch_rate = round(100 * mismatches / with_om, 1) if with_om > 0 else 0

    print(f"\nTotal records:     {total}")
    print(f"With Open-Meteo:   {with_om}")
    print(f"Outcome match:     {matches} ({match_rate}%)")
    print(f"Outcome mismatch:  {mismatches} ({mismatch_rate}%)")

    if mismatch_rate > 10:
        print(f"\n** WARNING: {mismatch_rate}% mismatch rate. Our model is calibrated")
        print("   to a different ground truth than Polymarket uses for resolution.")
        print("   Edge estimates may be INFLATED by this amount.")

    # By city
    print(f"\n{'City':20s} {'n':>5s} {'Match':>6s} {'Miss':>6s} {'Rate':>7s} {'Avg Delta C':>12s}")
    print("-" * 60)

    rows = conn.execute("""
        SELECT city,
               COUNT(*) as n,
               SUM(CASE WHEN outcome_match = 1 THEN 1 ELSE 0 END) as matches,
               SUM(CASE WHEN outcome_match = 0 THEN 1 ELSE 0 END) as mismatches,
               ROUND(AVG(temp_delta_c), 2) as avg_delta
        FROM calibration
        WHERE open_meteo_outcome IS NOT NULL
        GROUP BY city
        ORDER BY mismatches DESC, n DESC
    """).fetchall()

    for r in rows:
        rate = round(100 * r["matches"] / r["n"], 1) if r["n"] > 0 else 0
        city_display = CITIES.get(r["city"], {}).get("display", r["city"])
        delta_str = f"{r['avg_delta']:+.2f}" if r["avg_delta"] is not None else "N/A"
        print(f"{city_display:20s} {r['n']:5d} {r['matches']:6d} {r['mismatches']:6d} {rate:6.1f}% {delta_str:>12s}")

    # By question type
    print(f"\n{'Question Type':20s} {'n':>5s} {'Match':>6s} {'Miss':>6s} {'Rate':>7s}")
    print("-" * 45)

    rows = conn.execute("""
        SELECT question_type,
               COUNT(*) as n,
               SUM(CASE WHEN outcome_match = 1 THEN 1 ELSE 0 END) as matches,
               SUM(CASE WHEN outcome_match = 0 THEN 1 ELSE 0 END) as mismatches
        FROM calibration
        WHERE open_meteo_outcome IS NOT NULL
        GROUP BY question_type
        ORDER BY n DESC
    """).fetchall()

    for r in rows:
        rate = round(100 * r["matches"] / r["n"], 1) if r["n"] > 0 else 0
        qt = r["question_type"] or "unknown"
        print(f"{qt:20s} {r['n']:5d} {r['matches']:6d} {r['mismatches']:6d} {rate:6.1f}%")

    # Date range
    date_range = conn.execute(
        "SELECT MIN(market_date), MAX(market_date) FROM calibration"
    ).fetchone()
    print(f"\nDate range: {date_range[0]} to {date_range[1]}")

    # Mismatch details (show up to 10)
    mismatched = conn.execute("""
        SELECT city, market_date, question, temp_threshold, unit,
               polymarket_outcome, open_meteo_outcome,
               open_meteo_temp_c, open_meteo_temp_f
        FROM calibration
        WHERE outcome_match = 0
        ORDER BY market_date DESC
        LIMIT 10
    """).fetchall()

    if mismatched:
        print(f"\nRecent mismatches (showing up to 10):")
        print("-" * 70)
        for r in mismatched:
            city_display = CITIES.get(r["city"], {}).get("display", r["city"])
            actual = r["open_meteo_temp_f"] if r["unit"] == "F" else r["open_meteo_temp_c"]
            print(f"  {city_display} {r['market_date']}: "
                  f"thresh={r['temp_threshold']}{chr(176)}{r['unit']} "
                  f"actual={actual}{chr(176)}{r['unit']} "
                  f"Poly={r['polymarket_outcome']} OM={r['open_meteo_outcome']}")

    # Strategic implications
    print(f"\n{'=' * 70}")
    print("STRATEGIC IMPLICATIONS")
    print(f"{'=' * 70}")
    if with_om > 0:
        if mismatch_rate <= 5:
            print("LOW RISK: Open-Meteo and Polymarket resolution sources largely agree.")
            print("Our forecast model is calibrated to the correct ground truth.")
        elif mismatch_rate <= 15:
            print("MODERATE RISK: Some resolution source disagreement detected.")
            print("Consider city-specific bias adjustments for worst offenders.")
            print("Edge estimates should be haircut by the mismatch rate.")
        else:
            print("HIGH RISK: Significant resolution source disagreement.")
            print("Our model may be predicting the WRONG temperature.")
            print("Recommend: focus trades on cities with <5% mismatch rate only.")
    else:
        print("Insufficient data. Run 'collect' with more days_back.")

    conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Resolution calibration: Open-Meteo vs Polymarket outcomes"
    )
    sub = parser.add_subparsers(dest="command")

    collect_p = sub.add_parser("collect", help="Collect calibration data from resolved markets")
    collect_p.add_argument("--days", type=int, default=14,
                           help="How far back to look (default: 14)")

    sub.add_parser("report", help="Show calibration report")

    run_p = sub.add_parser("run", help="Collect + report")
    run_p.add_argument("--days", type=int, default=14,
                       help="How far back to look (default: 14)")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.command == "collect":
        asyncio.run(collect(days_back=args.days))
    elif args.command == "report":
        report()
    elif args.command == "run":
        asyncio.run(collect(days_back=args.days))
        print()
        report()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
