"""Auto-resolve paper trades by fetching actual temperatures.

Uses Open-Meteo archive API to get observed max temperatures,
then resolves each trade based on its question type.

Usage:
    python -m polyphemus.prediction_markets.weather.resolver
    python -m polyphemus.prediction_markets.weather.resolver --dry-run
    python -m polyphemus.prediction_markets.weather.resolver --date 2026-04-13
"""
import asyncio
import logging
import math
import re
from datetime import date, datetime, timezone

import httpx

from .config import CITIES
from .paper_tracker import get_db, resolve_trade
from .detector import classify_question

logger = logging.getLogger(__name__)

ARCHIVE_BASE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_BASE = "https://api.open-meteo.com/v1/forecast"


async def fetch_actual_temp(city_key: str, target_date: date) -> dict | None:
    """Fetch actual observed max temperature for a city on a past date.

    Tries archive API first (reliable for dates > 5 days ago),
    falls back to forecast API (has recent actuals within its window).

    Returns:
        {"temp_max_c": float, "temp_max_f": float, "source": str} or None.
    """
    city = CITIES.get(city_key)
    if not city:
        return None

    date_str = target_date.isoformat()

    # Try forecast API first (has actuals for recent dates within 16-day window)
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(FORECAST_BASE, params={
                "latitude": city["lat"],
                "longitude": city["lon"],
                "daily": "temperature_2m_max",
                "timezone": "auto",
                "start_date": date_str,
                "end_date": date_str,
            })
            resp.raise_for_status()
            data = resp.json()
            temps = data.get("daily", {}).get("temperature_2m_max", [])
            if temps and temps[0] is not None:
                temp_c = float(temps[0])
                return {
                    "temp_max_c": round(temp_c, 1),
                    "temp_max_f": round(temp_c * 9 / 5 + 32, 1),
                    "source": "open-meteo-forecast",
                }
        except Exception as exc:
            logger.debug("Forecast API failed for %s: %s", city_key, exc)

        # Fallback to archive API
        try:
            resp = await client.get(ARCHIVE_BASE, params={
                "latitude": city["lat"],
                "longitude": city["lon"],
                "daily": "temperature_2m_max",
                "timezone": "auto",
                "start_date": date_str,
                "end_date": date_str,
            })
            resp.raise_for_status()
            data = resp.json()
            temps = data.get("daily", {}).get("temperature_2m_max", [])
            if temps and temps[0] is not None:
                temp_c = float(temps[0])
                return {
                    "temp_max_c": round(temp_c, 1),
                    "temp_max_f": round(temp_c * 9 / 5 + 32, 1),
                    "source": "open-meteo-archive",
                }
        except Exception as exc:
            logger.warning("Archive API also failed for %s: %s", city_key, exc)

    return None


def determine_outcome(question: str, temp_threshold: int, unit: str,
                      actual_temp_c: float, actual_temp_f: float) -> str:
    """Determine YES/NO outcome based on question type and actual temperature.

    Args:
        question: Market question text.
        temp_threshold: Temperature threshold from the trade.
        unit: "C" or "F".
        actual_temp_c: Actual observed max temp in Celsius.
        actual_temp_f: Actual observed max temp in Fahrenheit.

    Returns:
        "YES" or "NO".
    """
    actual = actual_temp_f if unit == "F" else actual_temp_c
    q_type = classify_question(question)

    if q_type == "cumulative_higher":
        # "X or higher" -> YES if actual >= X
        return "YES" if actual >= temp_threshold else "NO"

    elif q_type == "cumulative_lower":
        # "X or lower" / "below X" -> YES if actual <= X
        return "YES" if actual <= temp_threshold else "NO"

    else:
        # Bucket: "exactly X" or "between X-Y"
        # Check for range pattern first: "between 48-49°F"
        range_match = re.search(r"between\s+(\d+)[-–](\d+)", question.lower())
        if range_match:
            low = int(range_match.group(1))
            high = int(range_match.group(2))
            rounded = round(actual)
            return "YES" if low <= rounded <= high else "NO"

        # Single bucket: YES if actual rounds to the threshold
        rounded = round(actual)
        return "YES" if rounded == temp_threshold else "NO"


async def resolve_pending(target_date: date | None = None, dry_run: bool = False) -> dict:
    """Resolve all pending paper trades for a given date (or all past dates).

    Returns summary dict with counts and P&L.
    """
    conn = get_db()
    today = datetime.now(timezone.utc).date()

    if target_date:
        rows = conn.execute(
            "SELECT * FROM paper_trades WHERE resolved = 0 AND market_date = ?",
            (target_date.isoformat(),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM paper_trades WHERE resolved = 0 AND market_date <= ?",
            (today.isoformat(),),
        ).fetchall()

    if not rows:
        print("No trades to resolve.")
        return {"resolved": 0, "pnl": 0.0}

    print(f"Found {len(rows)} trades to resolve")
    print()

    # Group by city to batch API calls
    city_dates: dict[str, set[str]] = {}
    for r in rows:
        city_dates.setdefault(r["city"], set()).add(r["market_date"])

    # Fetch actual temperatures
    actual_temps: dict[str, dict[str, dict]] = {}  # city -> date -> temp_data
    for city_key, dates in city_dates.items():
        actual_temps[city_key] = {}
        for d in dates:
            td = date.fromisoformat(d)
            temp_data = await fetch_actual_temp(city_key, td)
            if temp_data:
                actual_temps[city_key][d] = temp_data
                city_cfg = CITIES.get(city_key, {})
                unit = city_cfg.get("unit", "C")
                display_temp = temp_data["temp_max_f"] if unit == "F" else temp_data["temp_max_c"]
                print(f"  {city_cfg.get('display', city_key)} {d}: actual max = {display_temp}{chr(176)}{unit} ({temp_data['source']})")
            else:
                print(f"  {city_key} {d}: MISSING - cannot resolve (API unavailable)")

    print()

    # Resolve trades
    resolved_count = 0
    total_pnl = 0.0
    wins = 0
    losses = 0
    skipped = 0

    for r in rows:
        city_key = r["city"]
        market_date = r["market_date"]

        temps = actual_temps.get(city_key, {}).get(market_date)
        if not temps:
            print(f"  #{r['id']:2d} SKIP {city_key} {market_date} - no actual temp data")
            skipped += 1
            continue

        outcome = determine_outcome(
            question=r["question"] or "",
            temp_threshold=r["temp"],
            unit=r["unit"],
            actual_temp_c=temps["temp_max_c"],
            actual_temp_f=temps["temp_max_f"],
        )

        city_cfg = CITIES.get(city_key, {})
        unit = r["unit"]
        actual = temps["temp_max_f"] if unit == "F" else temps["temp_max_c"]

        if dry_run:
            # Compute P&L without writing
            price = r["market_price"]
            size = r["hypothetical_size"]
            shares = size / price if price > 0 else 0
            fee = 0.05 * price * (1 - price) * shares
            if r["direction"] == "BUY":
                pnl = (shares * (1 - price) - fee) if outcome == "YES" else -size
            else:
                pnl = (shares * price - fee) if outcome == "NO" else -size

            symbol = "W" if pnl > 0 else "L"
            print(f"  #{r['id']:2d} [DRY] {r['direction']} {city_cfg.get('display', city_key):15s} "
                  f"thresh={r['temp']}{chr(176)}{unit} actual={actual}{chr(176)}{unit} -> {outcome} "
                  f"({symbol}) P&L=${pnl:+.2f}")
            total_pnl += pnl
            if pnl > 0:
                wins += 1
            else:
                losses += 1
            resolved_count += 1
        else:
            try:
                pnl = resolve_trade(conn, r["id"], outcome)
                symbol = "W" if pnl > 0 else "L"
                print(f"  #{r['id']:2d} {r['direction']} {city_cfg.get('display', city_key):15s} "
                      f"thresh={r['temp']}{chr(176)}{unit} actual={actual}{chr(176)}{unit} -> {outcome} "
                      f"({symbol}) P&L=${pnl:+.2f}")
                total_pnl += pnl
                if pnl > 0:
                    wins += 1
                else:
                    losses += 1
                resolved_count += 1
            except Exception as exc:
                print(f"  #{r['id']:2d} ERROR: {exc}")
                skipped += 1

    conn.close()

    wr = round(100 * wins / resolved_count, 1) if resolved_count > 0 else 0
    print(f"\n{'=' * 50}")
    print(f"RESOLUTION SUMMARY {'(DRY RUN)' if dry_run else ''}")
    print(f"{'=' * 50}")
    print(f"Resolved: {resolved_count} | Skipped: {skipped}")
    print(f"Wins: {wins} | Losses: {losses} | WR: {wr}%")
    print(f"Total P&L: ${total_pnl:+.2f}")

    return {
        "resolved": resolved_count,
        "skipped": skipped,
        "wins": wins,
        "losses": losses,
        "win_rate": wr,
        "pnl": round(total_pnl, 2),
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Auto-resolve weather paper trades")
    parser.add_argument("--date", type=str, default=None,
                        help="Resolve trades for this date (YYYY-MM-DD). Default: all past dates.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show outcomes without writing to DB")
    parser.add_argument("--log-level", default="WARNING",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    target = date.fromisoformat(args.date) if args.date else None
    asyncio.run(resolve_pending(target_date=target, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
