"""Auto-resolve paper trades by checking actual outcomes.

Primary: Polymarket resolution via Gamma API (ground truth).
Fallback: Open-Meteo observed temperatures (may differ from Polymarket's
resolution source by 1-3 deg, flagged with warning).

Usage:
    python -m polyphemus.prediction_markets.weather.resolver
    python -m polyphemus.prediction_markets.weather.resolver --source open-meteo
    python -m polyphemus.prediction_markets.weather.resolver --dry-run
    python -m polyphemus.prediction_markets.weather.resolver --date 2026-04-13
"""
import asyncio
import json
import logging
import re
from datetime import date, datetime, timezone

import httpx

from .config import CITIES
from .paper_tracker import get_db, resolve_trade
from .detector import classify_question

logger = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"
ARCHIVE_BASE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_BASE = "https://api.open-meteo.com/v1/forecast"


# ---------------------------------------------------------------------------
# Polymarket resolution (primary source)
# ---------------------------------------------------------------------------

async def fetch_resolved_temperature_markets() -> dict[str, str]:
    """Fetch resolved temperature markets from Gamma API.

    Returns dict mapping condition_id -> "YES" or "NO".
    Only includes markets that have fully resolved.
    """
    resolutions: dict[str, str] = {}
    offset = 0
    limit = 100

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                resp = await client.get(
                    f"{GAMMA_BASE}/events",
                    params={
                        "tag_slug": "temperature",
                        "closed": "true",
                        "limit": limit,
                        "offset": offset,
                    },
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.error("Gamma API error fetching resolved markets: %s", exc)
                break

            events = resp.json()
            if not isinstance(events, list):
                break

            for event in events:
                for market in event.get("markets", []):
                    cid = market.get("conditionId")
                    if not cid:
                        continue

                    # Parse outcome prices to determine resolution
                    prices_raw = market.get("outcomePrices", "[]")
                    try:
                        prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                        if not prices:
                            continue
                        yes_price = float(prices[0])
                    except (json.JSONDecodeError, IndexError, ValueError, TypeError):
                        continue

                    if yes_price >= 0.99:
                        resolutions[cid] = "YES"
                    elif yes_price <= 0.01:
                        resolutions[cid] = "NO"
                    # else: market closed but not clearly resolved (e.g. voided)

            if len(events) < limit:
                break
            offset += limit

    logger.info("Fetched %d resolved temperature markets from Polymarket", len(resolutions))
    return resolutions


async def resolve_via_polymarket(
    target_date: date | None = None,
    dry_run: bool = False,
) -> dict:
    """Resolve paper trades using actual Polymarket market outcomes.

    This is the most accurate resolution method: it uses the same outcome
    that Polymarket used, regardless of which temperature source they checked.
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
        conn.close()
        return {"resolved": 0, "pnl": 0.0}

    print(f"Found {len(rows)} trades to resolve via Polymarket")

    # Fetch all resolved temperature markets
    print("Fetching resolved markets from Gamma API...")
    resolutions = await fetch_resolved_temperature_markets()
    print(f"  {len(resolutions)} resolved markets found")

    # Build set of condition_ids NOT found in batch, plus trades missing condition_id
    unmatched_cids = set()
    for r in rows:
        cid = r["condition_id"]
        if cid and cid not in resolutions:
            unmatched_cids.add(cid)
        elif not cid:
            pass  # will fall through to token_id

    # For any trade whose condition_id wasn't in the batch OR has no condition_id,
    # try individual token_id lookups as fallback
    fallback_token_ids = set()
    for r in rows:
        cid = r["condition_id"]
        in_batch = cid and cid in resolutions
        if not in_batch and r["token_id"]:
            fallback_token_ids.add(r["token_id"])

    token_to_resolution: dict[str, str] = {}
    if fallback_token_ids:
        print(f"  {len(fallback_token_ids)} trades not in batch, trying token_id lookup...")
        token_to_resolution = await _lookup_by_token_ids(fallback_token_ids)
        print(f"  Matched {len(token_to_resolution)} by token_id")

    print()

    resolved_count = 0
    total_pnl = 0.0
    wins = 0
    losses = 0
    skipped = 0

    for r in rows:
        outcome = None
        source = None

        # Try condition_id match first
        if r["condition_id"] and r["condition_id"] in resolutions:
            outcome = resolutions[r["condition_id"]]
            source = "polymarket"
        # Fall back to token_id match
        elif r["token_id"] and r["token_id"] in token_to_resolution:
            outcome = token_to_resolution[r["token_id"]]
            source = "polymarket-token"
        else:
            print(f"  #{r['id']:3d} SKIP - market not yet resolved on Polymarket")
            skipped += 1
            continue

        city_cfg = CITIES.get(r["city"], {})
        display = city_cfg.get("display", r["city"])

        if dry_run:
            pnl = _compute_pnl(r, outcome)
            symbol = "W" if pnl > 0 else "L"
            print(f"  #{r['id']:3d} [DRY] {r['direction']} {display:15s} "
                  f"thresh={r['temp']}{chr(176)}{r['unit']} -> {outcome} "
                  f"({symbol}) P&L=${pnl:+.2f} [{source}]")
            total_pnl += pnl
            if pnl > 0:
                wins += 1
            else:
                losses += 1
            resolved_count += 1
        else:
            try:
                pnl = resolve_trade(conn, r["id"], outcome)
                # Record resolution source
                conn.execute(
                    "UPDATE paper_trades SET resolution_source = ? WHERE id = ?",
                    (source, r["id"]),
                )
                conn.commit()
                symbol = "W" if pnl > 0 else "L"
                print(f"  #{r['id']:3d} {r['direction']} {display:15s} "
                      f"thresh={r['temp']}{chr(176)}{r['unit']} -> {outcome} "
                      f"({symbol}) P&L=${pnl:+.2f} [{source}]")
                total_pnl += pnl
                if pnl > 0:
                    wins += 1
                else:
                    losses += 1
                resolved_count += 1
            except Exception as exc:
                print(f"  #{r['id']:3d} ERROR: {exc}")
                skipped += 1

    conn.close()
    _print_summary(resolved_count, skipped, wins, losses, total_pnl, dry_run)
    return {
        "resolved": resolved_count,
        "skipped": skipped,
        "wins": wins,
        "losses": losses,
        "win_rate": round(100 * wins / resolved_count, 1) if resolved_count > 0 else 0,
        "pnl": round(total_pnl, 2),
        "source": "polymarket",
    }


async def _lookup_by_token_ids(token_ids: set[str]) -> dict[str, str]:
    """Look up market resolutions by CLOB token IDs via Gamma API."""
    results: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=30) as client:
        for tid in token_ids:
            try:
                resp = await client.get(
                    f"{GAMMA_BASE}/markets",
                    params={"clob_token_ids": tid, "closed": "true"},
                )
                if resp.status_code != 200:
                    continue
                markets = resp.json()
                if not markets:
                    continue
                m = markets[0] if isinstance(markets, list) else markets
                prices_raw = m.get("outcomePrices", "[]")
                prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                if prices:
                    yes_price = float(prices[0])
                    if yes_price >= 0.99:
                        results[tid] = "YES"
                    elif yes_price <= 0.01:
                        results[tid] = "NO"
            except Exception as exc:
                logger.debug("Token lookup failed for %s: %s", tid, exc)
    return results


# ---------------------------------------------------------------------------
# Open-Meteo temperature resolution (fallback)
# ---------------------------------------------------------------------------

async def fetch_actual_temp(city_key: str, target_date: date) -> dict | None:
    """Fetch actual observed max temperature for a city on a past date.

    WARNING: Open-Meteo temperature_2m_max is model-derived, not station
    observations. It can differ from Polymarket's resolution source
    (Weather Underground, KMA, Met Office, etc.) by 1-3 deg C/F.
    Use resolve_via_polymarket() for accurate paper trade scoring.

    Returns:
        {"temp_max_c": float, "temp_max_f": float, "source": str} or None.
    """
    city = CITIES.get(city_key)
    if not city:
        return None

    date_str = target_date.isoformat()

    async with httpx.AsyncClient(timeout=15) as client:
        # Try forecast API first (has actuals for recent dates within 16-day window)
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
        range_match = re.search(r"between\s+(\d+)[-\u2013](\d+)", question.lower())
        if range_match:
            low = int(range_match.group(1))
            high = int(range_match.group(2))
            rounded = round(actual)
            return "YES" if low <= rounded <= high else "NO"

        # Single bucket: YES if actual rounds to the threshold
        rounded = round(actual)
        return "YES" if rounded == temp_threshold else "NO"


async def resolve_via_open_meteo(
    target_date: date | None = None,
    dry_run: bool = False,
) -> dict:
    """Resolve paper trades using Open-Meteo temperature data (fallback).

    WARNING: Open-Meteo temperature_2m_max can differ from Polymarket's
    actual resolution source by 1-3 deg. Results scored here may not match
    actual Polymarket outcomes. Use resolve_via_polymarket() when possible.
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
        conn.close()
        return {"resolved": 0, "pnl": 0.0}

    print(f"Found {len(rows)} trades to resolve via Open-Meteo")
    print("WARNING: Open-Meteo temps may differ from Polymarket resolution by 1-3 deg")
    print()

    # Group by city to batch API calls
    city_dates: dict[str, set[str]] = {}
    for r in rows:
        city_dates.setdefault(r["city"], set()).add(r["market_date"])

    # Fetch actual temperatures
    actual_temps: dict[str, dict[str, dict]] = {}
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
                print(f"  {city_cfg.get('display', city_key)} {d}: actual max = "
                      f"{display_temp}{chr(176)}{unit} ({temp_data['source']})")
            else:
                print(f"  {city_key} {d}: MISSING - cannot resolve")

    print()

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
            print(f"  #{r['id']:3d} SKIP {city_key} {market_date} - no actual temp data")
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
        display = city_cfg.get("display", city_key)
        unit = r["unit"]
        actual = temps["temp_max_f"] if unit == "F" else temps["temp_max_c"]

        if dry_run:
            pnl = _compute_pnl(r, outcome)
            symbol = "W" if pnl > 0 else "L"
            print(f"  #{r['id']:3d} [DRY] {r['direction']} {display:15s} "
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
                conn.execute(
                    "UPDATE paper_trades SET resolution_source = ? WHERE id = ?",
                    ("open-meteo", r["id"]),
                )
                conn.commit()
                symbol = "W" if pnl > 0 else "L"
                print(f"  #{r['id']:3d} {r['direction']} {display:15s} "
                      f"thresh={r['temp']}{chr(176)}{unit} actual={actual}{chr(176)}{unit} -> {outcome} "
                      f"({symbol}) P&L=${pnl:+.2f}")
                total_pnl += pnl
                if pnl > 0:
                    wins += 1
                else:
                    losses += 1
                resolved_count += 1
            except Exception as exc:
                print(f"  #{r['id']:3d} ERROR: {exc}")
                skipped += 1

    conn.close()
    _print_summary(resolved_count, skipped, wins, losses, total_pnl, dry_run)
    return {
        "resolved": resolved_count,
        "skipped": skipped,
        "wins": wins,
        "losses": losses,
        "win_rate": round(100 * wins / resolved_count, 1) if resolved_count > 0 else 0,
        "pnl": round(total_pnl, 2),
        "source": "open-meteo",
    }


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _compute_pnl(row, outcome: str) -> float:
    """Compute P&L for a paper trade given the outcome."""
    price = row["market_price"]
    size = row["hypothetical_size"]

    if row["direction"] == "BUY":
        shares = size / price if price > 0 else 0
        fee = 0.05 * price * (1 - price) * shares
        if outcome == "YES":
            return shares * (1 - price) - fee
        else:
            return -size
    else:  # SELL (buy NO tokens)
        no_price = 1.0 - price
        shares = size / no_price if no_price > 0 else 0
        fee = 0.05 * no_price * (1 - no_price) * shares
        if outcome == "NO":
            return shares * price - fee
        else:
            return -size


def _print_summary(resolved: int, skipped: int, wins: int, losses: int,
                   pnl: float, dry_run: bool) -> None:
    wr = round(100 * wins / resolved, 1) if resolved > 0 else 0
    print(f"\n{'=' * 50}")
    print(f"RESOLUTION SUMMARY {'(DRY RUN)' if dry_run else ''}")
    print(f"{'=' * 50}")
    print(f"Resolved: {resolved} | Skipped: {skipped}")
    print(f"Wins: {wins} | Losses: {losses} | WR: {wr}%")
    print(f"Total P&L: ${pnl:+.2f}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Auto-resolve weather paper trades")
    parser.add_argument("--date", type=str, default=None,
                        help="Resolve trades for this date (YYYY-MM-DD). Default: all past dates.")
    parser.add_argument("--source", choices=["polymarket", "open-meteo"], default="polymarket",
                        help="Resolution source (default: polymarket)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show outcomes without writing to DB")
    parser.add_argument("--log-level", default="WARNING",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    target = date.fromisoformat(args.date) if args.date else None

    if args.source == "polymarket":
        asyncio.run(resolve_via_polymarket(target_date=target, dry_run=args.dry_run))
    else:
        asyncio.run(resolve_via_open_meteo(target_date=target, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
