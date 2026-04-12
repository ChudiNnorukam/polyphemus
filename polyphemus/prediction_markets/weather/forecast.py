"""Open-Meteo forecast fetcher and probability distribution converter.

Fetches daily temperature_2m_max forecasts (free, no API key needed).
Converts a point forecast into a Gaussian probability distribution over
integer temperature buckets for comparison against market prices.
"""
import math
import logging
from datetime import date

import httpx

from .config import CITIES

logger = logging.getLogger(__name__)

OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"

# Open-Meteo supports up to 16 forecast days on the free tier.
_MAX_FORECAST_DAYS = 16


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

    For "X or higher" markets: P(actual >= X) = 1 - CDF(X - 0.5)
    For "X or lower" markets: P(actual <= X) = CDF(X + 0.5)

    Scales std_dev by sqrt(days_until) for multi-day forecast horizons.
    """
    center = temp_max
    horizon_std = std_dev * math.sqrt(max(1, days_until))
    std = horizon_std * (9 / 5) if unit == "F" else horizon_std

    if direction == "or_higher":
        z = (temp_threshold - 0.5 - center) / std
        return round(1.0 - _norm_cdf(z), 4)
    else:
        z = (temp_threshold + 0.5 - center) / std
        return round(_norm_cdf(z), 4)


def _norm_cdf(x: float) -> float:
    """Standard normal CDF using math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
