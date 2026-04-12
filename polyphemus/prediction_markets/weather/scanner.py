"""Polymarket Gamma API scanner for active temperature markets.

Fetches all temperature-tagged events and parses them into structured
market data with city, date, and per-bucket pricing.
"""
import re
import json
import logging
from datetime import datetime, timezone

import httpx

from .config import CITIES, DISPLAY_TO_KEY

logger = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"

# Regex to pull the target date from various title formats:
#   "Highest temperature in Los Angeles on Apr 14?"
#   "High temp in NYC on 2026-04-14?"
_DATE_PATTERNS = [
    # ISO date: 2026-04-14
    re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
    # Month-day-year: Apr 14, 2026
    re.compile(
        r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2}),?\s+(\d{4})\b",
        re.IGNORECASE,
    ),
    # Month day (no year): April 14
    re.compile(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})\b",
        re.IGNORECASE,
    ),
]

_MONTH_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_FULL = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _parse_date_from_title(title: str) -> str | None:
    """Return ISO date string (YYYY-MM-DD) extracted from a market title, or None."""
    # Try ISO first
    m = _DATE_PATTERNS[0].search(title)
    if m:
        return m.group(1)

    # Try abbreviated month (Apr 14, 2026)
    m = _DATE_PATTERNS[1].search(title)
    if m:
        month = _MONTH_ABBR.get(m.group(1).lower())
        if month:
            day = int(m.group(2))
            year = int(m.group(3))
            return f"{year:04d}-{month:02d}-{day:02d}"

    # Try full month name with no year - assume current year
    m = _DATE_PATTERNS[2].search(title)
    if m:
        month = _MONTH_FULL.get(m.group(1).lower())
        if month:
            day = int(m.group(2))
            year = datetime.now(timezone.utc).year
            return f"{year:04d}-{month:02d}-{day:02d}"

    return None


def _parse_city_from_title(title: str) -> str | None:
    """Return the CITIES slug for the city mentioned in a market title, or None.

    Tries longest-match first so "New York City" wins over "New York".
    """
    title_lower = title.lower()

    # Sort by length descending so multi-word cities match before shorter ones
    for display_lower, slug in sorted(DISPLAY_TO_KEY.items(), key=lambda kv: -len(kv[0])):
        if display_lower in title_lower:
            return slug

    return None


async def fetch_temperature_markets() -> list[dict]:
    """Fetch all active temperature events from Polymarket Gamma API.

    Paginates automatically. Returns raw event dicts as returned by the API.
    Raises httpx.HTTPStatusError on non-2xx responses.
    """
    all_events: list[dict] = []
    offset = 0
    limit = 100

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                resp = await client.get(
                    f"{GAMMA_BASE}/events",
                    params={
                        "tag_slug": "temperature",
                        "active": "true",
                        "closed": "false",
                        "limit": limit,
                        "offset": offset,
                    },
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.error("Gamma API error %s: %s", exc.response.status_code, exc.response.text[:200])
                raise
            except httpx.RequestError as exc:
                logger.error("Gamma API request error: %s", exc)
                raise

            batch = resp.json()
            if not isinstance(batch, list):
                logger.warning("Unexpected response type from Gamma API: %s", type(batch))
                break

            all_events.extend(batch)
            logger.debug("Fetched %d events (offset=%d)", len(batch), offset)

            if len(batch) < limit:
                break  # Last page

            offset += limit

    logger.info("Total temperature events fetched: %d", len(all_events))
    return all_events


def parse_temperature_markets(events: list[dict]) -> list[dict]:
    """Parse raw Gamma API events into structured market data.

    Each returned dict represents one city+date combo:
    {
        event_id: str,
        title: str,
        city: str | None,       # CITIES slug
        date: str | None,       # ISO YYYY-MM-DD
        buckets: list[{
            temp_label: str,    # e.g. "26 deg C" or "80 deg F"
            yes_price: float | None,
            yes_token_id: str | None,
            condition_id: str | None,
            question: str | None,
        }],
        end_date: str | None,
    }

    Markets where city or date cannot be parsed are still returned (city/date = None)
    so callers can decide how to handle them.
    """
    parsed: list[dict] = []

    for event in events:
        title = event.get("title", "")
        markets = event.get("markets", [])

        city_slug = _parse_city_from_title(title)
        date_str = _parse_date_from_title(title)

        if not city_slug:
            logger.debug("Could not parse city from title: %r", title)

        if not date_str:
            logger.debug("Could not parse date from title: %r", title)

        buckets: list[dict] = []
        for m in markets:
            group_title = m.get("groupItemTitle") or m.get("question", "")

            # Parse yes_price from outcomePrices
            outcome_prices_raw = m.get("outcomePrices", "[]")
            try:
                outcome_prices = json.loads(outcome_prices_raw) if isinstance(outcome_prices_raw, str) else outcome_prices_raw
                yes_price = float(outcome_prices[0]) if outcome_prices else None
            except (json.JSONDecodeError, IndexError, ValueError, TypeError):
                yes_price = None

            # Parse yes token_id from clobTokenIds
            clob_ids_raw = m.get("clobTokenIds", "[]")
            try:
                clob_ids = json.loads(clob_ids_raw) if isinstance(clob_ids_raw, str) else clob_ids_raw
                yes_token_id = clob_ids[0] if clob_ids else None
            except (json.JSONDecodeError, IndexError, TypeError):
                yes_token_id = None

            buckets.append({
                "temp_label": group_title,
                "yes_price": yes_price,
                "yes_token_id": yes_token_id,
                "condition_id": m.get("conditionId"),
                "question": m.get("question"),
            })

        parsed.append({
            "event_id": event.get("id"),
            "title": title,
            "city": city_slug,
            "date": date_str,
            "buckets": buckets,
            "end_date": event.get("endDate"),
        })

    return parsed
