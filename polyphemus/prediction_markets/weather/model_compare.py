"""Compare Gaussian vs ensemble probability models for weather markets.

Shows how different forecast models produce different trading signals.
Identifies where our Gaussian assumption diverges most from the ensemble
empirical distribution, which represents real forecast uncertainty.

Usage:
    python -m polyphemus.prediction_markets.weather.model_compare
    python -m polyphemus.prediction_markets.weather.model_compare --city seoul --days 3
"""
import argparse
import asyncio
import logging
from datetime import date, timedelta

from .config import CITIES
from .forecast import (
    fetch_forecast,
    fetch_multi_model,
    fetch_ensemble,
    forecast_cumulative_prob,
    ensemble_cumulative_prob,
)

logger = logging.getLogger(__name__)

# Cities with most paper trade activity + calibration data
DEFAULT_CITIES = [
    "seoul", "tokyo", "london", "paris", "new-york-city",
    "chicago", "tel-aviv", "sao-paulo",
]


async def compare_city(city_key: str, target: date) -> dict | None:
    """Compare Gaussian vs ensemble for one city/date.

    Returns dict with model comparison data, or None if data unavailable.
    """
    cfg = CITIES.get(city_key)
    if not cfg:
        return None

    unit = cfg["unit"]

    # Fetch all data in parallel
    forecast, multi, gfs_ens, ecmwf_ens = await asyncio.gather(
        fetch_forecast(city_key, target),
        fetch_multi_model(city_key, target),
        fetch_ensemble(city_key, target, "gfs_seamless"),
        fetch_ensemble(city_key, target, "ecmwf_ifs025"),
        return_exceptions=True,
    )

    # Handle exceptions from gather
    if isinstance(forecast, Exception):
        forecast = None
    if isinstance(multi, Exception):
        multi = None
    if isinstance(gfs_ens, Exception):
        gfs_ens = None
    if isinstance(ecmwf_ens, Exception):
        ecmwf_ens = None

    if not forecast:
        return None

    temp_c = forecast["temp_max_c"]
    temp_f = forecast["temp_max_f"]
    temp = temp_f if unit == "F" else temp_c
    days_until = max(1, (target - date.today()).days)

    # Test cumulative probabilities at several thresholds around the forecast
    thresholds = []
    base = round(temp)
    for offset in range(-4, 5):
        thresholds.append(base + offset)

    comparisons = []
    for thresh in thresholds:
        gauss = forecast_cumulative_prob(
            thresh, temp, unit, direction="or_higher", days_until=days_until,
        )

        gfs_prob = None
        if gfs_ens and not isinstance(gfs_ens, dict) is False and isinstance(gfs_ens, dict):
            gfs_prob = ensemble_cumulative_prob(thresh, gfs_ens["members"], unit, "or_higher")

        ecmwf_prob = None
        if ecmwf_ens and isinstance(ecmwf_ens, dict):
            ecmwf_prob = ensemble_cumulative_prob(thresh, ecmwf_ens["members"], unit, "or_higher")

        comparisons.append({
            "threshold": thresh,
            "gaussian": gauss,
            "gfs_ensemble": gfs_prob,
            "ecmwf_ensemble": ecmwf_prob,
        })

    return {
        "city": city_key,
        "display": cfg["display"],
        "date": target.isoformat(),
        "unit": unit,
        "forecast_temp": temp,
        "multi_model": multi,
        "gfs_ensemble": gfs_ens,
        "ecmwf_ensemble": ecmwf_ens,
        "days_until": days_until,
        "comparisons": comparisons,
    }


async def run_comparison(cities: list[str], days_ahead: int = 1) -> None:
    """Run and print model comparison for multiple cities."""
    target = date.today() + timedelta(days=days_ahead)

    print("=" * 75)
    print(f"MODEL COMPARISON: Gaussian vs Ensemble - {target}")
    print("=" * 75)

    for city_key in cities:
        result = await compare_city(city_key, target)
        if not result:
            print(f"\n{city_key}: No data available")
            continue

        cfg = CITIES[city_key]
        unit = result["unit"]
        deg = chr(176)

        print(f"\n{'=' * 75}")
        print(f"{result['display']} ({target}, +{result['days_until']}d)")
        print(f"{'=' * 75}")

        # Point forecasts
        if result["multi_model"]:
            mm = result["multi_model"]
            print(f"\nPoint forecasts:")
            for model, vals in mm["models"].items():
                t = vals["temp_max_f"] if unit == "F" else vals["temp_max_c"]
                print(f"  {model:15s}: {t}{deg}{unit}")
            print(f"  Model spread:   {mm['spread_c']}{deg}C")

        # Ensemble stats
        for ens_name, ens_data in [("GFS", result["gfs_ensemble"]),
                                    ("ECMWF", result["ecmwf_ensemble"])]:
            if ens_data and isinstance(ens_data, dict):
                mean = ens_data["mean_f"] if unit == "F" else ens_data["mean_c"]
                std = ens_data["std_f"] if unit == "F" else ens_data["std_c"]
                print(f"\n{ens_name} Ensemble ({ens_data['n_members']} members):")
                print(f"  Mean: {mean}{deg}{unit}  Std: {std}{deg}{unit}")
                mn = ens_data["min_c"] * 9/5 + 32 if unit == "F" else ens_data["min_c"]
                mx = ens_data["max_c"] * 9/5 + 32 if unit == "F" else ens_data["max_c"]
                print(f"  Range: [{mn:.1f}, {mx:.1f}]{deg}{unit}")

        # Probability comparison table
        print(f"\nP(>= threshold) comparison:")
        print(f"  {'Thresh':>8s}  {'Gaussian':>9s}  {'GFS Ens':>9s}  {'ECMWF Ens':>9s}  {'Max Delta':>10s}  {'Signal':>8s}")
        print(f"  {'-'*60}")

        for c in result["comparisons"]:
            gauss = c["gaussian"]
            gfs = c["gfs_ensemble"]
            ecmwf = c["ecmwf_ensemble"]

            vals = [gauss]
            if gfs is not None:
                vals.append(gfs)
            if ecmwf is not None:
                vals.append(ecmwf)

            max_delta = max(vals) - min(vals) if len(vals) > 1 else 0

            gfs_str = f"{gfs:.4f}" if gfs is not None else "  N/A  "
            ecmwf_str = f"{ecmwf:.4f}" if ecmwf is not None else "  N/A  "

            # Signal: if all models agree on direction (>0.7 or <0.3), strong signal
            signal = ""
            if all(v > 0.8 for v in vals):
                signal = "STRONG-H"  # strong higher
            elif all(v < 0.2 for v in vals):
                signal = "STRONG-L"  # strong lower
            elif max_delta > 0.3:
                signal = "DISAGREE"
            elif all(0.3 <= v <= 0.7 for v in vals):
                signal = "UNCLEAR"

            delta_flag = " !!" if max_delta > 0.2 else ""
            print(f"  {c['threshold']:>5d}{deg}{unit}  {gauss:>9.4f}  {gfs_str:>9s}  {ecmwf_str:>9s}  "
                  f"{max_delta:>9.4f}{delta_flag}  {signal:>8s}")

    # Summary
    print(f"\n{'=' * 75}")
    print("INTERPRETATION GUIDE")
    print(f"{'=' * 75}")
    print("""
  Max Delta > 0.20: Models fundamentally disagree. Trade with caution.
  Max Delta > 0.30: Do NOT trade. Signal is unreliable.
  STRONG-H/L:       All models agree. Highest confidence trades.
  DISAGREE:         Models conflict. Skip or reduce position.
  UNCLEAR:          All models near 50/50. No edge.

  The Gaussian model uses std_dev=1.5C with sqrt(days) scaling.
  Ensembles use 30-50 actual model runs with real uncertainty.
  When they disagree significantly, the Gaussian is likely wrong.
""")


def main():
    parser = argparse.ArgumentParser(description="Compare Gaussian vs ensemble probability models")
    parser.add_argument("--city", type=str, default=None, help="Single city to compare")
    parser.add_argument("--days", type=int, default=1, help="Days ahead (default: 1)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    cities = [args.city] if args.city else DEFAULT_CITIES
    asyncio.run(run_comparison(cities, args.days))


if __name__ == "__main__":
    main()
