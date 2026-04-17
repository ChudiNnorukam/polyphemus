"""Open-Meteo forecast fetcher and probability distribution converter.

Fetches daily temperature_2m_max forecasts (free, no API key needed).
Converts a point forecast into a Gaussian probability distribution over
integer temperature buckets for comparison against market prices.

Multi-model support (added Apr 2026):
- Default "best_match" (Open-Meteo's blended model)
- GFS (NOAA Global Forecast System)
- ECMWF IFS (European Centre for Medium-Range Weather Forecasts)
- GFS ensemble (30 members) for empirical probability distributions
- ECMWF ensemble (50 members) for empirical probability distributions
"""
import math
import logging
from datetime import date

import httpx

from .config import CITIES

logger = logging.getLogger(__name__)

OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"
ENSEMBLE_BASE = "https://ensemble-api.open-meteo.com/v1/ensemble"

# Open-Meteo supports up to 16 forecast days on the free tier.
_MAX_FORECAST_DAYS = 16

# Models available via the standard forecast API
POINT_MODELS = ["best_match", "gfs_seamless", "ecmwf_ifs"]

# Models available via the ensemble API (with member counts)
ENSEMBLE_MODELS = {
    "gfs_seamless": 30,     # GFS 30-member ensemble
    "ecmwf_ifs025": 50,     # ECMWF 50-member ensemble
}


async def fetch_forecast(city_key: str, target_date: date) -> dict | None:
    """Fetch temperature forecast for a city from Open-Meteo.

    Args:
        city_key: Key in CITIES dict (e.g. "los-angeles").
        target_date: The date to fetch the daily max temp for.

    Returns dict:
        {
            "city": str,
            "date": str (ISO),
            "temp_max_c": float,
            "temp_max_f": float,
            "model": str,
        }
    or None if city unknown or date out of range.
    """
    city = CITIES.get(city_key)
    if not city:
        logger.warning("Unknown city key: %r", city_key)
        return None

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                OPEN_METEO_BASE,
                params={
                    "latitude": city["lat"],
                    "longitude": city["lon"],
                    "daily": "temperature_2m_max",
                    "timezone": "auto",
                    "forecast_days": _MAX_FORECAST_DAYS,
                },
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Open-Meteo HTTP error for %s: %s %s",
            city_key,
            exc.response.status_code,
            exc.response.text[:200],
        )
        return None
    except httpx.RequestError as exc:
        logger.error("Open-Meteo request error for %s: %s", city_key, exc)
        return None

    data = resp.json()
    daily = data.get("daily", {})
    dates: list[str] = daily.get("time", [])
    temps: list[float | None] = daily.get("temperature_2m_max", [])

    target_iso = target_date.isoformat()
    for d, t in zip(dates, temps):
        if d == target_iso:
            if t is None:
                logger.warning("Open-Meteo returned null temp for %s on %s", city_key, target_iso)
                return None
            temp_c = float(t)
            return {
                "city": city_key,
                "date": d,
                "temp_max_c": round(temp_c, 2),
                "temp_max_f": round(temp_c * 9 / 5 + 32, 2),
                "model": "open-meteo-best",
            }

    logger.debug(
        "Date %s not found in Open-Meteo response for %s (available: %s to %s)",
        target_iso,
        city_key,
        dates[0] if dates else "N/A",
        dates[-1] if dates else "N/A",
    )
    return None


async def fetch_multi_model(city_key: str, target_date: date) -> dict | None:
    """Fetch temperature forecasts from multiple models for cross-validation.

    Returns dict with forecasts from best_match, GFS, and ECMWF:
        {
            "city": str,
            "date": str,
            "models": {
                "best_match": {"temp_max_c": float, "temp_max_f": float},
                "gfs_seamless": {"temp_max_c": float, "temp_max_f": float},
                "ecmwf_ifs": {"temp_max_c": float, "temp_max_f": float},
            },
            "spread_c": float,   # max - min across models (disagreement)
            "mean_c": float,     # mean across models
        }
    or None if city unknown or date out of range.
    """
    city = CITIES.get(city_key)
    if not city:
        return None

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                OPEN_METEO_BASE,
                params={
                    "latitude": city["lat"],
                    "longitude": city["lon"],
                    "daily": "temperature_2m_max",
                    "models": ",".join(POINT_MODELS),
                    "timezone": "auto",
                    "forecast_days": _MAX_FORECAST_DAYS,
                },
            )
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Multi-model fetch error for %s: %s", city_key, exc)
        return None

    data = resp.json()
    daily = data.get("daily", {})
    dates = daily.get("time", [])
    target_iso = target_date.isoformat()

    try:
        idx = dates.index(target_iso)
    except ValueError:
        logger.debug("Date %s not in multi-model response for %s", target_iso, city_key)
        return None

    models = {}
    temps_c = []
    for model in POINT_MODELS:
        key = f"temperature_2m_max_{model}"
        values = daily.get(key, [])
        if idx < len(values) and values[idx] is not None:
            tc = float(values[idx])
            models[model] = {
                "temp_max_c": round(tc, 2),
                "temp_max_f": round(tc * 9 / 5 + 32, 2),
            }
            temps_c.append(tc)

    if not temps_c:
        return None

    return {
        "city": city_key,
        "date": target_iso,
        "models": models,
        "spread_c": round(max(temps_c) - min(temps_c), 2),
        "mean_c": round(sum(temps_c) / len(temps_c), 2),
        "mean_f": round((sum(temps_c) / len(temps_c)) * 9 / 5 + 32, 2),
    }


async def fetch_ensemble(
    city_key: str,
    target_date: date,
    model: str = "gfs_seamless",
) -> dict | None:
    """Fetch ensemble forecast members for empirical probability distribution.

    Instead of assuming Gaussian, this gives us 30 (GFS) or 50 (ECMWF)
    independent model runs, each producing a temperature estimate. The
    spread of these members IS the forecast uncertainty.

    Returns dict:
        {
            "city": str,
            "date": str,
            "model": str,
            "members": list[float],  # temperature_2m_max from each member (Celsius)
            "n_members": int,
            "mean_c": float,
            "std_c": float,          # empirical std dev across members
            "min_c": float,
            "max_c": float,
        }
    or None if unavailable.
    """
    city = CITIES.get(city_key)
    if not city:
        return None

    if model not in ENSEMBLE_MODELS:
        logger.error("Unknown ensemble model: %s", model)
        return None

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                ENSEMBLE_BASE,
                params={
                    "latitude": city["lat"],
                    "longitude": city["lon"],
                    "daily": "temperature_2m_max",
                    "models": model,
                    "timezone": "auto",
                },
            )
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Ensemble fetch error for %s/%s: %s", city_key, model, exc)
        return None

    data = resp.json()
    daily = data.get("daily", {})
    dates = daily.get("time", [])
    target_iso = target_date.isoformat()

    try:
        idx = dates.index(target_iso)
    except ValueError:
        logger.debug("Date %s not in ensemble response for %s", target_iso, city_key)
        return None

    # Collect values from all members
    members = []
    for key, values in daily.items():
        if "member" in key and idx < len(values) and values[idx] is not None:
            members.append(float(values[idx]))

    if not members:
        return None

    mean = sum(members) / len(members)
    variance = sum((m - mean) ** 2 for m in members) / len(members)
    std = math.sqrt(variance)

    return {
        "city": city_key,
        "date": target_iso,
        "model": model,
        "members": [round(m, 2) for m in sorted(members)],
        "n_members": len(members),
        "mean_c": round(mean, 2),
        "mean_f": round(mean * 9 / 5 + 32, 2),
        "std_c": round(std, 2),
        "std_f": round(std * 9 / 5, 2),
        "min_c": round(min(members), 2),
        "max_c": round(max(members), 2),
    }


def ensemble_cumulative_prob(
    temp_threshold: int,
    members: list[float],
    unit: str = "C",
    direction: str = "or_higher",
) -> float:
    """Compute cumulative probability from ensemble members.

    Instead of Gaussian CDF, count what fraction of ensemble members
    exceed (or fall below) the threshold. This captures non-Gaussian
    tails and model-specific biases.

    Args:
        temp_threshold: Integer temperature threshold.
        members: List of ensemble member temperature forecasts (Celsius).
        unit: "C" or "F" - if F, convert members to F before comparison.
        direction: "or_higher" or "or_lower".

    Returns:
        Probability estimate (0.0 to 1.0).
    """
    if not members:
        return 0.0

    if unit == "F":
        converted = [m * 9 / 5 + 32 for m in members]
    else:
        converted = members

    if direction == "or_higher":
        count = sum(1 for m in converted if m >= temp_threshold)
    else:
        count = sum(1 for m in converted if m <= temp_threshold)

    return round(count / len(converted), 4)


def ensemble_to_distribution(members: list[float], unit: str = "C") -> dict[int, float]:
    """Convert ensemble members into a bucket probability distribution.

    Each integer bucket i captures the fraction of members where
    round(member) == i. This is the empirical analogue of
    forecast_to_distribution().

    Args:
        members: List of ensemble member forecasts (Celsius).
        unit: "C" or "F" - if F, convert before bucketing.

    Returns:
        dict mapping integer temperature -> probability.
    """
    if not members:
        return {}

    if unit == "F":
        converted = [m * 9 / 5 + 32 for m in members]
    else:
        converted = list(members)

    counts: dict[int, int] = {}
    for m in converted:
        bucket = round(m)
        counts[bucket] = counts.get(bucket, 0) + 1

    return {t: round(c / len(converted), 4) for t, c in sorted(counts.items())}


def forecast_to_distribution(temp_max: float, unit: str, std_dev: float = 1.5,
                              days_until: int = 1) -> dict[int, float]:
    """Convert a point forecast into a probability distribution over integer temperature buckets.

    Uses a Gaussian centered on the forecast with configurable std_dev.
    Default std_dev of 1.5 degC (2.7 degF) represents typical 1-day forecast uncertainty.
    Scales by sqrt(days_until) for multi-day horizons (forecast uncertainty grows).

    Each integer bucket i captures P(i - 0.5 < actual_temp <= i + 0.5).

    Args:
        temp_max: Forecast maximum temperature.
        unit: "C" or "F".
        std_dev: Base forecast uncertainty in degrees C (1-day horizon).
        days_until: Days until market resolution. Uncertainty scales as sqrt(days).

    Returns:
        dict mapping integer temperature -> probability (values sum to ~1.0).
    """
    center = temp_max
    horizon_std = std_dev * math.sqrt(max(1, days_until))
    std = horizon_std * (9 / 5) if unit == "F" else horizon_std

    # Cover +/- 4 sigma to capture essentially all probability mass
    low = int(math.floor(center - 4 * std))
    high = int(math.ceil(center + 4 * std))

    probs: dict[int, float] = {}
    for temp in range(low, high + 1):
        z_low = (temp - 0.5 - center) / std
        z_high = (temp + 0.5 - center) / std
        p = _norm_cdf(z_high) - _norm_cdf(z_low)
        if p >= 0.001:
            probs[temp] = round(p, 4)

    return probs


def forecast_cumulative_prob(temp_threshold: int, temp_max: float, unit: str,
                             std_dev: float = 1.5, direction: str = "or_higher",
                             days_until: int = 1) -> float:
    """Compute cumulative probability: P(actual >= threshold) or P(actual <= threshold).

    For "X or higher" markets: P(actual >= X) = 1 - CDF((X - center) / std)
    For "X or lower" markets: P(actual <= X) = CDF((X - center) / std)

    No continuity correction: Polymarket resolves cumulative markets on the
    actual float temperature (e.g., actual >= 23.0), not on rounded integers.
    The ±0.5 correction in forecast_to_distribution is correct for bucket bets
    (integer bins) but wrong here.

    Scales std_dev by sqrt(days_until) for multi-day forecast horizons.
    """
    center = temp_max
    horizon_std = std_dev * math.sqrt(max(1, days_until))
    std = horizon_std * (9 / 5) if unit == "F" else horizon_std

    if direction == "or_higher":
        z = (temp_threshold - center) / std
        return round(1.0 - _norm_cdf(z), 4)
    else:
        z = (temp_threshold - center) / std
        return round(_norm_cdf(z), 4)


def _norm_cdf(x: float) -> float:
    """Standard normal CDF using math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
