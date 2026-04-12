#!/usr/bin/env python3
"""Polymarket Weather Divergence Scanner.

Scans all active temperature markets, compares against Open-Meteo forecasts,
and reports mispricings above the configured threshold.

Usage:
    python -m prediction_markets.weather.main
    python -m prediction_markets.weather.main --threshold 0.15 --min-ev 0.02
    python -m prediction_markets.weather.main --threshold 0.10 --min-ev 0.01 --max-kelly 0.25
"""
import asyncio
import argparse
import logging
from datetime import date, datetime, timezone

from .scanner import fetch_temperature_markets, parse_temperature_markets
from .forecast import fetch_forecast, forecast_to_distribution
from .detector import detect_divergences, compute_kelly
from .config import CITIES

logger = logging.getLogger(__name__)


async def run(
    threshold: float = 0.10,
    min_ev: float = 0.01,
    max_kelly: float = 0.25,
    verbose: bool = False,
) -> list[dict]:
    """Main scan loop.

    Args:
        threshold: Minimum |forecast_prob - market_price| to flag.
        min_ev: Minimum net EV per share to include in results.
        max_kelly: Cap for Kelly display (visual only, not enforced on position sizing).
        verbose: If True, log skipped markets.

    Returns:
        List of opportunity dicts (already sorted by ev_net descending).
    """
    print(f"{'=' * 70}")
    print(
        f"WEATHER DIVERGENCE SCANNER | "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    print(f"Threshold: {threshold} | Min EV: {min_ev} | Max Kelly: {max_kelly}")
    print(f"{'=' * 70}")

    # Step 1: Fetch all active temperature markets
    print("\nFetching temperature markets from Gamma API...")
    try:
        events = await fetch_temperature_markets()
    except Exception as exc:
        print(f"  ERROR fetching markets: {exc}")
        return []

    markets = parse_temperature_markets(events)
    parseable = [m for m in markets if m["city"] and m["date"]]
    print(
        f"  Found {len(events)} events -> {len(markets)} parsed -> "
        f"{len(parseable)} with city+date"
    )

    if not parseable:
        print("\nNo parseable markets found. Check title parsing logic.")
        return []

    # Step 2: For each market, fetch forecast and detect divergences
    all_opportunities: list[dict] = []
    skipped = 0

    for market in parseable:
        city_key = market["city"]
        date_str = market["date"]

        if city_key not in CITIES:
            if verbose:
                print(f"  SKIP: {city_key!r} not in CITIES config")
            skipped += 1
            continue

        try:
            target_date = date.fromisoformat(date_str)
        except (ValueError, TypeError):
            if verbose:
                print(f"  SKIP: invalid date {date_str!r} for {city_key}")
            skipped += 1
            continue

        today = datetime.now(timezone.utc).date()
        if target_date <= today:
            if verbose:
                print(f"  SKIP: {city_key} {date_str} already past/resolving")
            skipped += 1
            continue

        forecast = await fetch_forecast(city_key, target_date)
        if not forecast:
            if verbose:
                print(f"  SKIP: no forecast for {city_key} on {date_str}")
            skipped += 1
            continue

        city_cfg = CITIES[city_key]
        unit = city_cfg["unit"]
        forecast_temp = forecast["temp_max_f"] if unit == "F" else forecast["temp_max_c"]

        days_until = (target_date - today).days
        dist = forecast_to_distribution(forecast_temp, unit, days_until=days_until)
        market["_forecast_temp"] = forecast_temp
        market["_unit"] = unit
        market["_days_until"] = days_until
        opps = detect_divergences(market, dist, threshold=threshold)

        for opp in opps:
            if opp["ev_net"] < min_ev:
                continue
            opp["city"] = city_key
            opp["city_display"] = city_cfg.get("display", city_key)
            opp["date"] = date_str
            opp["forecast_temp"] = round(forecast_temp, 1)
            opp["unit"] = unit
            opp["kelly"] = compute_kelly(opp["edge"], opp["market_price"], opp["direction"])
            all_opportunities.append(opp)

    # Step 3: Sort and report
    all_opportunities.sort(key=lambda x: x["ev_net"], reverse=True)

    print(f"\n  Skipped {skipped} markets (no forecast or city config missing)")

    if not all_opportunities:
        print(
            f"\nNo opportunities above threshold "
            f"(edge > {threshold}, EV > {min_ev})"
        )
        return []

    print(f"\n{'=' * 70}")
    print(f"OPPORTUNITIES ({len(all_opportunities)} found)")
    print(f"{'=' * 70}")

    for opp in all_opportunities[:20]:  # Show top 20
        kelly = opp["kelly"]
        kelly_str = f"{kelly:.1%}"
        if kelly > max_kelly:
            kelly_str += " [CAPPED]"

        unit_sym = opp.get("unit", "")
        print(
            f"\n  {opp['city_display']} | {opp['date']} | "
            f"{opp['temp']}{chr(176)}{unit_sym} | {opp['direction']}"
        )
        print(
            f"    Forecast: {opp['forecast_temp']}{chr(176)}{unit_sym} | "
            f"Market: {opp['market_price']:.3f} | "
            f"Model prob: {opp['forecast_prob']:.3f} | "
            f"Edge: {opp['edge']:+.3f}"
        )
        print(
            f"    EV(gross): {opp['ev_gross']:.4f}/share | "
            f"EV(net): {opp['ev_net']:.4f}/share | "
            f"Kelly: {kelly_str}"
        )
        if opp.get("question"):
            q = opp["question"]
            print(f"    Q: {q[:100]}{'...' if len(q) > 100 else ''}")

    if len(all_opportunities) > 20:
        print(f"\n  ... and {len(all_opportunities) - 20} more (use --min-ev to filter)")

    return all_opportunities


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Polymarket weather divergence scanner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.10,
        help="Minimum |forecast_prob - market_price| to flag (default: 0.10)",
    )
    parser.add_argument(
        "--min-ev",
        type=float,
        default=0.01,
        help="Minimum net EV per share to include in output (default: 0.01)",
    )
    parser.add_argument(
        "--max-kelly",
        type=float,
        default=0.25,
        help="Kelly fractions above this are displayed with [CAPPED] (default: 0.25)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Log skipped markets",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: WARNING)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    asyncio.run(
        run(
            threshold=args.threshold,
            min_ev=args.min_ev,
            max_kelly=args.max_kelly,
            verbose=args.verbose,
        )
    )


if __name__ == "__main__":
    main()
