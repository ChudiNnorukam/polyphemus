"""
NOAA Weather Signal Feed — Information arbitrage on Polymarket temperature markets.

Strategy:
  - Query Open-Meteo ECMWF IFS ensemble (51 members) for temperature forecasts (free, no auth)
  - Scan Gamma API for active Polymarket temperature bucket markets (resolve within 48h)
  - Buy YES tokens where market_price < entry_max AND NOAA prob > threshold AND edge > min_edge
  - Quarter-Kelly position sizing based on edge magnitude
  - Exit when price corrects above exit_min OR hold to resolution

Edge source: ECMWF IFS ensemble (51 members) is the world's most accurate 24-48h forecast model.
Retail market participants price buckets based on intuition, creating systematic mispricings.
Fees: weather markets have ZERO taker/maker fees on Polymarket (as of Feb 2026).

Reference: suislanchez/polymarket-kalshi-weather-bot, aheck3/nyc-temperature-forecasting-polymarket
"""

import asyncio
import json
import math
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Callable, Dict, List, Optional, Set, Tuple

import aiohttp

from .config import Settings, setup_logger
from .clob_wrapper import ClobWrapper

GAMMA_API_URL = "https://gamma-api.polymarket.com"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"

# City coordinates (lat, lon)
CITY_COORDS: Dict[str, Tuple[float, float]] = {
    "NYC": (40.7128, -74.0060),
    "New York": (40.7128, -74.0060),
    "Chicago": (41.8781, -87.6298),
    "Seattle": (47.6062, -122.3321),
    "Atlanta": (33.7490, -84.3880),
    "Dallas": (32.7767, -96.7970),
    "Miami": (25.7617, -80.1918),
    "London": (51.5074, -0.1278),
    "Los Angeles": (34.0522, -118.2437),
    "Paris": (48.8566, 2.3522),
    "Seoul": (37.5665, 126.9780),
    "Toronto": (43.6532, -79.3832),
    "Buenos Aires": (-34.6037, -58.3816),
    "Houston": (29.7604, -95.3698),
    "Boston": (42.3601, -71.0589),
    "Phoenix": (33.4484, -112.0740),
    "Ankara": (39.9334, 32.8597),
    "Auckland": (-36.8485, 174.7633),
}

# City name -> slug fragment used in Polymarket event slugs
# Event slug format: highest-temperature-in-{slug}-on-{month}-{day}-{year}
CITY_EVENT_SLUGS: Dict[str, str] = {
    "London": "london",
    "Chicago": "chicago",
    "Miami": "miami",
    "Paris": "paris",
    "Buenos Aires": "buenos-aires",
    "New York": "new-york",
    "Seattle": "seattle",
    "Atlanta": "atlanta",
    "Dallas": "dallas",
    "Houston": "houston",
    "Boston": "boston",
    "Los Angeles": "los-angeles",
    "Seoul": "seoul",
    "Toronto": "toronto",
    "Ankara": "ankara",
    "Auckland": "auckland",
    "Phoenix": "phoenix",
}

# Regex patterns for extracting temp conditions from market questions/groupItemTitle
_TEMP_RANGE = re.compile(r'(-?\d{1,3})\s*[–\-]\s*(-?\d{1,3})\s*°?\s*([FC])', re.IGNORECASE)
# "14°C or above" / "above 14°C"
_TEMP_OR_ABOVE = re.compile(r'(-?\d{1,3})\s*°?\s*([CF])\s+or\s+above', re.IGNORECASE)
_TEMP_ABOVE = re.compile(
    r'(?:above|exceed|over|at least|>=?|higher than)\s*(-?\d{1,3})\s*°?\s*([FC])', re.IGNORECASE
)
# "8°C or below" / "below 8°C"
_TEMP_OR_BELOW = re.compile(r'(-?\d{1,3})\s*°?\s*([CF])\s+or\s+below', re.IGNORECASE)
_TEMP_BELOW = re.compile(
    r'(?:below|under|less than|<=?|lower than)\s*(-?\d{1,3})\s*°?\s*([FC])', re.IGNORECASE
)
# Exact single value: "be 11°C" or "be 70°F"
_TEMP_EXACT = re.compile(r'\bbe\s+(-?\d{1,3})\s*°?\s*([CF])\b', re.IGNORECASE)
_DATE_MDY = re.compile(
    r'(?:on\s+)?(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
    r'Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
    r'\s+(\d{1,2})(?:st|nd|rd|th)?',
    re.IGNORECASE,
)


class WeatherFeed:
    """NOAA/Open-Meteo driven signal generator for Polymarket weather temperature markets."""

    def __init__(self, config: Settings, clob: ClobWrapper, on_signal: Callable, db=None):
        self._config = config
        self._clob = clob
        self._on_signal = on_signal
        self._logger = setup_logger("polyphemus.weather")

        # Dedup: slugs we've already entered (persist for this session)
        # Seed from DB on startup so restarts don't re-enter existing open positions
        self._entered_slugs: Set[str] = set()
        if db is not None:
            try:
                open_trades = db.get_open_trades()
                for trade in open_trades:
                    slug = trade.get("slug", "")
                    if slug:
                        self._entered_slugs.add(slug)
                if self._entered_slugs:
                    self._logger.info(
                        f"WeatherFeed seeded {len(self._entered_slugs)} open slugs from DB: "
                        f"{list(self._entered_slugs)}"
                    )
            except Exception as e:
                self._logger.warning(f"WeatherFeed: failed to seed entered_slugs from DB: {e}")

        # Forecast cache: cache_key -> forecast dict
        self._forecast_cache: Dict[str, dict] = {}
        self._forecast_cache_time: Dict[str, float] = {}
        self._CACHE_TTL = 1800  # 30 min

        self._session: Optional[aiohttp.ClientSession] = None

        # Stats
        self.signals_generated = 0
        self.markets_scanned = 0
        self.opportunities_found = 0

    async def start(self) -> None:
        """Start the weather scanner loop."""
        self._logger.info(
            f"Starting ECMWF ensemble weather feed | "
            f"entry_max={self._config.weather_entry_max_price} | "
            f"noaa_min={self._config.weather_noaa_min_prob} | "
            f"min_edge={self._config.weather_min_edge} | "
            f"exit_min={self._config.weather_exit_min_price} | "
            f"cities={self._config.weather_cities} | "
            f"interval={self._config.weather_scan_interval}s | "
            f"dry_run={self._config.weather_dry_run}"
        )
        self._session = aiohttp.ClientSession()
        try:
            await self._scan_loop()
        finally:
            if self._session:
                await self._session.close()

    async def start_stale_watchdog(self) -> None:
        """API compatibility stub — no stale watchdog needed for weather feed."""
        await asyncio.Event().wait()

    async def _scan_loop(self) -> None:
        """Main scan loop: poll weather markets every N seconds."""
        while True:
            try:
                await self._do_scan()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._logger.warning(f"Weather scan error: {e}", exc_info=True)
            await asyncio.sleep(self._config.weather_scan_interval)

    async def _do_scan(self) -> None:
        """Single scan pass: fetch active weather markets and check for mispricings."""
        allowed_cities = [c.strip() for c in self._config.weather_cities.split(',') if c.strip()]

        markets = await self._fetch_weather_markets()
        self.markets_scanned += len(markets)

        if not markets:
            self._logger.debug("No active weather markets returned from Gamma API")
            return

        self._logger.info(f"Scanning {len(markets)} weather markets")

        for market in markets:
            try:
                await self._evaluate_market(market, allowed_cities)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                slug = market.get("slug", "?")
                self._logger.debug(f"Market eval error ({slug}): {e}")

    async def _fetch_weather_markets(self) -> List[dict]:
        """Fetch active temperature bucket markets via Gamma event slugs.

        Temperature markets are geo-restricted and don't appear in standard
        market listings. Must be fetched via their parent event.

        Event slug format:
          highest-temperature-in-{city}-on-{month}-{day}-{year}
          e.g. highest-temperature-in-london-on-february-23-2026

        Fetches today + tomorrow + day-after for each known city.
        Each event contains 8-12 temperature bucket markets (YES/NO).
        """
        results: List[dict] = []
        now = datetime.now(timezone.utc)

        tasks = []
        for days_ahead in range(3):
            target = now + timedelta(days=days_ahead)
            month_str = target.strftime("%B").lower()   # "february"
            day_str = str(target.day)                    # "23" (no padding)
            year_str = str(target.year)

            for city_name, city_slug in CITY_EVENT_SLUGS.items():
                event_slug = (
                    f"highest-temperature-in-{city_slug}"
                    f"-on-{month_str}-{day_str}-{year_str}"
                )
                tasks.append((city_name, event_slug))

        # Fetch all events concurrently (one per city per day)
        async def fetch_event(city_name: str, event_slug: str) -> List[dict]:
            try:
                async with self._session.get(
                    f"{GAMMA_API_URL}/events",
                    params={"slug": event_slug},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    events = data if isinstance(data, list) else data.get("events", [])
                    markets = []
                    for ev in events:
                        if ev.get("slug") != event_slug:
                            continue
                        ev_end = ev.get("endDate")
                        for m in ev.get("markets", []):
                            if ev_end and not m.get("endDate"):
                                m["endDate"] = ev_end
                            # Tag with city for coord lookup
                            m["_weather_city"] = city_name
                            markets.append(m)
                    return markets
            except Exception as e:
                self._logger.debug(f"Event fetch error {event_slug}: {e}")
                return []

        fetched = await asyncio.gather(*[fetch_event(c, s) for c, s in tasks])
        for batch in fetched:
            results.extend(batch)

        return results

    async def _evaluate_market(self, market: dict, allowed_cities: List[str]) -> None:
        """Evaluate a single market for a potential entry signal."""
        question = market.get("question") or market.get("title") or ""
        slug = market.get("slug") or market.get("marketSlug") or ""
        condition_id = market.get("conditionId", "")

        if not slug or slug in self._entered_slugs:
            return

        # City pre-tagged by _fetch_weather_markets; fall back to slug/question
        city = (
            market.get("_weather_city")
            or self._city_from_slug(slug)
            or self._extract_city(question, allowed_cities)
        )
        if not city or not self._resolve_coords(city):
            return

        # Extract resolution date — skip if outside 0-48h window
        end_dt = self._parse_end_date(market)
        if not end_dt:
            return
        now_utc = datetime.now(timezone.utc)
        hours_left = (end_dt - now_utc).total_seconds() / 3600
        if hours_left < 0 or hours_left > 48:
            return

        # Get token IDs and find YES token
        try:
            token_ids = json.loads(market.get("clobTokenIds", "[]"))
            outcomes = json.loads(market.get("outcomes", '["Yes","No"]'))
        except (json.JSONDecodeError, TypeError):
            return
        if not token_ids:
            return

        yes_idx = 0
        for i, o in enumerate(outcomes):
            if str(o).lower() in ("yes", "true", "1"):
                yes_idx = i
                break
        if yes_idx >= len(token_ids):
            return
        yes_token_id = token_ids[yes_idx]

        # Use outcomePrices from event data for initial scan (avoids per-market API call).
        # Only fetch live midpoint when signal is about to fire.
        try:
            outcome_prices = json.loads(market.get("outcomePrices", "[]"))
            market_price = float(outcome_prices[yes_idx]) if outcome_prices else 0.0
        except (json.JSONDecodeError, TypeError, IndexError, ValueError):
            market_price = 0.0

        if market_price <= 0 or market_price >= self._config.weather_entry_max_price:
            return

        # Parse temperature condition — prefer groupItemTitle, fall back to question text
        group_title = market.get("groupItemTitle", "")
        temp_cond = self._parse_temp_condition(question, group_title)
        if not temp_cond:
            return

        # Get NOAA/Open-Meteo forecast probability (sigma scales with hours_left)
        noaa_prob = await self._get_forecast_probability(city, end_dt, temp_cond, hours_left)
        if noaa_prob is None:
            return

        edge = noaa_prob - market_price
        self._logger.debug(
            f"Weather check: {question[:70]} | "
            f"city={city} | cond={temp_cond} | "
            f"mkt={market_price:.3f} | noaa={noaa_prob:.3f} | edge={edge:+.3f}"
        )

        # Entry filter: all three conditions must pass
        if not (
            market_price <= self._config.weather_entry_max_price
            and noaa_prob >= self._config.weather_noaa_min_prob
            and edge >= self._config.weather_min_edge
        ):
            return

        # Pre-bet sanity: log ensemble context before every entry
        forecast_meta: dict = {}
        coords_pb = self._resolve_coords(city)
        if coords_pb:
            _lat_pb, _lon_pb = coords_pb
            _ck_pb = f"{_lat_pb:.4f}_{_lon_pb:.4f}_{end_dt.strftime('%Y-%m-%d')}"
            _fc_pb = self._forecast_cache.get(_ck_pb)
            if _fc_pb and "member_highs" in _fc_pb:
                _mean_pb = _fc_pb["ensemble_mean"]
                _n_pb = _fc_pb["n_members"]
                _in_pb = round(noaa_prob * _n_pb)
                _btype = (
                    "CENTERED" if noaa_prob >= 0.25
                    else "FRINGE" if noaa_prob >= 0.15
                    else "TAIL"
                )
                self._logger.info(
                    f"Pre-bet [{_btype}] {city} | ensemble_mean={_mean_pb:.1f}°F | "
                    f"{_in_pb}/{_n_pb} members in bucket | "
                    f"prob={noaa_prob:.1%} mkt={market_price:.3f} edge={edge:+.3f}"
                )
                # Bucket center for Telegram delta display
                if temp_cond["kind"] == "range":
                    _center_f = (temp_cond["lo"] + temp_cond["hi"]) / 2
                else:
                    _center_f = temp_cond["threshold"]
                forecast_meta = {
                    "ensemble_mean_f": _mean_pb,
                    "bucket_center_f": _center_f,
                    "bet_type": _btype,
                    "members_str": f"{_in_pb}/{_n_pb}",
                }

        self.opportunities_found += 1
        await self._emit_signal(
            token_id=yes_token_id,
            market_price=market_price,
            slug=slug,
            question=question,
            city=city,
            noaa_prob=noaa_prob,
            edge=edge,
            end_dt=end_dt,
            condition_id=condition_id,
            hours_left=hours_left,
            forecast_meta=forecast_meta,
        )

    async def _emit_signal(
        self,
        token_id: str,
        market_price: float,
        slug: str,
        question: str,
        city: str,
        noaa_prob: float,
        edge: float,
        end_dt: datetime,
        condition_id: str,
        hours_left: float,
        forecast_meta: dict = None,
    ) -> None:
        """Build and emit (or dry-log) a BUY signal for a mispriced weather market."""
        # Quarter-Kelly: kelly = edge / (1 - market_price) * 0.25
        kelly = (edge / max(1 - market_price, 0.01)) * 0.25
        kelly = max(self._config.weather_base_bet_pct, min(kelly, self._config.weather_max_bet_pct))
        ref_size = round(kelly * 100, 2)  # reference USDC size at $100 nominal (for signal logger)

        signal = {
            "token_id": token_id,
            "price": market_price,
            "slug": slug,
            "market_title": question,
            "usdc_size": ref_size,     # realistic size (conviction check bypassed for is_weather)
            "direction": "BUY",
            "outcome": "Yes",          # weather markets are YES/NO, not Up/Down
            "asset": "WEATHER",
            "tx_hash": f"weather-{slug}-{int(time.time())}",
            "timestamp": time.time(),
            "source": "noaa_weather",
            "noaa_prob": noaa_prob,
            "edge": edge,
            "kelly_fraction": kelly,
            "time_remaining_secs": int(hours_left * 3600),
            "market_window_secs": int(hours_left * 3600),  # actual window, not fixed 86400
            "market_end_time_iso": end_dt.isoformat(),     # override slug-epoch parsing in executor
            "condition_id": condition_id,
            "weather_city": city,
            "metadata": {
                "is_weather": True,
                "weather_exit_price": self._config.weather_exit_min_price,
                "weather_hold_to_resolution": self._config.weather_hold_to_resolution,
                "weather_max_hold_hours": self._config.weather_max_hold_hours,
                "weather_market_end_ts": end_dt.timestamp(),
                "condition_id": condition_id,
                "noaa_prob": noaa_prob,
                "edge": edge,
                **(forecast_meta or {}),
            },
        }

        tag = "[DRY] " if self._config.weather_dry_run else ""
        self._logger.info(
            f"{tag}Weather signal: {question[:65]} | "
            f"city={city} | mkt={market_price:.3f} | "
            f"noaa={noaa_prob:.3f} | edge={edge:+.3f} | "
            f"kelly={kelly:.3f} | {hours_left:.1f}h left"
        )

        if self._config.weather_dry_run:
            return  # don't lock the slug in dry-run mode

        # Fetch live CLOB order book. We check edge and size against the BEST ASK,
        # not the midpoint — because maker orders on thin weather books never fill
        # and taker always pays the ask. Checking midpoint was the bug that let Miami
        # through at 2.5% real edge (below 3% min) after the taker fill at 3.3x midpoint.
        order_book = await self._clob.get_order_book(token_id)
        asks = order_book.get("asks", [])

        if not asks:
            # No sellers in the book — can't buy, and stale Gamma price is unreliable
            self._logger.debug(f"No asks in order book for {token_id[:16]}... skipping")
            return

        best_ask = float(asks[0]["price"])

        if best_ask > 0.005:
            if best_ask >= self._config.weather_entry_max_price:
                self._logger.info(
                    f"Weather signal cancelled (ask too high): "
                    f"ask={best_ask:.4f} entry_max={self._config.weather_entry_max_price}"
                )
                return
            live_edge = noaa_prob - best_ask
            if live_edge < self._config.weather_min_edge:
                self._logger.info(
                    f"Weather signal cancelled (edge at ask below min): "
                    f"ask={best_ask:.4f} noaa={noaa_prob:.4f} "
                    f"edge={live_edge:+.4f} min={self._config.weather_min_edge}"
                )
                return
            if abs(best_ask - market_price) > 0.005:
                self._logger.info(
                    f"Weather price updated to live ask: "
                    f"{market_price:.4f} -> {best_ask:.4f}"
                )
            market_price = best_ask
            edge = live_edge
            kelly = (edge / max(1 - market_price, 0.01)) * 0.25
            kelly = max(self._config.weather_base_bet_pct, min(kelly, self._config.weather_max_bet_pct))
            ref_size = round(kelly * 100, 2)
            signal["price"] = market_price
            signal["edge"] = edge
            signal["kelly_fraction"] = kelly
            signal["usdc_size"] = ref_size
            signal["metadata"]["edge"] = edge

        self.signals_generated += 1
        await self._on_signal(signal)
        # Only mark entered AFTER signal delivered — prevents burning slug on mid-signal exception
        self._entered_slugs.add(slug)

    # =========================================================================
    # Forecast: ECMWF Ensemble (primary) + ECMWF Deterministic (fallback)
    # =========================================================================

    async def _get_forecast_probability(
        self,
        city: str,
        target_dt: datetime,
        temp_cond: dict,
        hours_left: float = 24.0,
    ) -> Optional[float]:
        """Get P(temp condition) from ECMWF ensemble (primary) or deterministic fallback.

        Primary: ECMWF IFS 51-member ensemble — direct probability count, no Gaussian.
        Fallback: ECMWF IFS deterministic + Gaussian uncertainty model.
        Both cached 30 min.
        """
        coords = self._resolve_coords(city)
        if not coords:
            return None

        lat, lon = coords
        date_str = target_dt.strftime("%Y-%m-%d")
        cache_key = f"{lat:.4f}_{lon:.4f}_{date_str}"

        if (
            cache_key in self._forecast_cache
            and time.time() - self._forecast_cache_time.get(cache_key, 0) < self._CACHE_TTL
        ):
            forecast = self._forecast_cache[cache_key]
        else:
            forecast = await self._fetch_ensemble(lat, lon, date_str)
            if not forecast:
                self._logger.debug(
                    f"Ensemble fetch failed for {city}, falling back to ECMWF deterministic"
                )
                forecast = await self._fetch_open_meteo(lat, lon, date_str)
            if not forecast:
                return None
            self._forecast_cache[cache_key] = forecast
            self._forecast_cache_time[cache_key] = time.time()

        if "member_highs" in forecast:
            return self._compute_prob_ensemble(forecast, temp_cond)
        return self._compute_prob(forecast, temp_cond, hours_left)

    async def _fetch_ensemble(self, lat: float, lon: float, date_str: str) -> Optional[dict]:
        """Fetch ECMWF IFS 51-member ensemble hourly temps for target date.

        Returns per-member daily highs in °F. Direct probability without Gaussian.
        """
        try:
            params = {
                "latitude": lat,
                "longitude": lon,
                "hourly": "temperature_2m",
                "models": "ifs_025_ensemble",
                "temperature_unit": "fahrenheit",
                "start_date": date_str,
                "end_date": date_str,
                "timezone": "UTC",
            }
            async with self._session.get(
                OPEN_METEO_ENSEMBLE_URL, params=params, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                if resp.status != 200:
                    self._logger.debug(
                        f"Ensemble API {resp.status} for {lat},{lon} {date_str}"
                    )
                    return None
                data = await resp.json()

            hourly = data.get("hourly", {})
            member_highs = []
            for key, values in hourly.items():
                if key.startswith("temperature_2m_member") and values:
                    valid = [v for v in values if v is not None]
                    if valid:
                        member_highs.append(max(valid))

            if not member_highs:
                return None

            return {
                "member_highs": member_highs,
                "ensemble_mean": sum(member_highs) / len(member_highs),
                "n_members": len(member_highs),
                "date": date_str,
            }
        except Exception as e:
            self._logger.debug(f"Ensemble API error for {lat},{lon}: {e}")
            return None

    def _compute_prob_ensemble(self, forecast: dict, cond: dict) -> Optional[float]:
        """Compute P(condition) by counting ECMWF ensemble members that satisfy it.

        Direct probabilistic estimate from 51 ECMWF IFS members — no Gaussian needed.
        """
        member_highs = forecast.get("member_highs")
        if not member_highs:
            return None

        n = len(member_highs)
        kind = cond.get("kind")
        lo = cond.get("lo")
        hi = cond.get("hi")
        threshold = cond.get("threshold")

        if kind == "range" and lo is not None and hi is not None:
            count = sum(1 for h in member_highs if lo <= h <= hi)
        elif kind == "above" and threshold is not None:
            count = sum(1 for h in member_highs if h >= threshold)
        elif kind == "below" and threshold is not None:
            count = sum(1 for h in member_highs if h <= threshold)
        else:
            return None

        return round(count / n, 4)

    async def _fetch_open_meteo(self, lat: float, lon: float, date_str: str) -> Optional[dict]:
        """Fetch ECMWF IFS deterministic daily high/low. Fallback when ensemble unavailable."""
        try:
            params = {
                "latitude": lat,
                "longitude": lon,
                "daily": "temperature_2m_max,temperature_2m_min",
                "hourly": "temperature_2m",
                "models": "ecmwf_ifs025",
                "temperature_unit": "fahrenheit",
                "start_date": date_str,
                "end_date": date_str,
                "timezone": "UTC",
            }
            async with self._session.get(
                OPEN_METEO_URL, params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    self._logger.debug(f"Open-Meteo {resp.status} for {lat},{lon} {date_str}")
                    return None
                data = await resp.json()

            daily = data.get("daily", {})
            t_max = daily.get("temperature_2m_max", [None])[0]
            t_min = daily.get("temperature_2m_min", [None])[0]
            hourly_temps = data.get("hourly", {}).get("temperature_2m", [])
            if t_max is None:
                return None
            return {
                "forecast_high": float(t_max),
                "forecast_low": float(t_min) if t_min is not None else None,
                "hourly_temps": hourly_temps,
                "date": date_str,
            }
        except Exception as e:
            self._logger.debug(f"Open-Meteo error for {lat},{lon}: {e}")
            return None

    def _compute_prob(self, forecast: dict, cond: dict, hours_left: float = 24.0) -> Optional[float]:
        """Estimate P(temp condition) from ECMWF deterministic + Gaussian uncertainty.

        Fallback when ensemble unavailable. Sigma scales with forecast horizon:
        sigma = 4°F * sqrt(hours_left / 24). At 6h: ~2°F. At 24h: 4°F. At 48h: ~5.7°F.
        """
        mu = forecast.get("forecast_high")
        if mu is None:
            return None

        sigma = 4.0 * math.sqrt(max(hours_left, 1.0) / 24.0)

        kind = cond.get("kind")
        lo = cond.get("lo")
        hi = cond.get("hi")
        threshold = cond.get("threshold")

        def ncdf(x):
            return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2))))

        if kind == "range" and lo is not None and hi is not None:
            prob = ncdf(hi) - ncdf(lo)
        elif kind == "above" and threshold is not None:
            prob = 1.0 - ncdf(threshold)
        elif kind == "below" and threshold is not None:
            prob = ncdf(threshold)
        else:
            return None

        return round(max(0.0, min(1.0, prob)), 4)

    # =========================================================================
    # Parsing helpers
    # =========================================================================

    def _parse_temp_condition(self, question: str, group_title: str = "") -> Optional[dict]:
        """Extract temperature condition dict from market question or groupItemTitle.

        Handles Polymarket's actual question formats:
          groupItemTitle "11°C"           -> exact bucket (±1°F range after convert)
          groupItemTitle "8°C or below"   -> below 8°C
          groupItemTitle "14°C or above"  -> above 14°C
          groupItemTitle "32-33F"         -> range 32-33°F
          question "be 11C on Feb 23?"    -> exact (±1°F)
          question "above 80°F?"          -> above
          question "below 32°F?"          -> below
        """
        # Prefer groupItemTitle if provided — it's cleaner than the full question
        text = group_title if group_title else question

        # "X or above" — e.g. "14°C or above", "80°F or above"
        m = _TEMP_OR_ABOVE.search(text)
        if m:
            t = float(m.group(1))
            if m.group(2).upper() == 'C':
                t = t * 9 / 5 + 32
            return {"kind": "above", "threshold": t}

        # "X or below" — e.g. "8°C or below", "32°F or below"
        m = _TEMP_OR_BELOW.search(text)
        if m:
            t = float(m.group(1))
            if m.group(2).upper() == 'C':
                t = t * 9 / 5 + 32
            return {"kind": "below", "threshold": t}

        # Range: "70-71F", "32-33C", "72–75°F"
        m = _TEMP_RANGE.search(text)
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
            if lo > hi:
                lo, hi = hi, lo
            if m.group(3).upper() == 'C':
                lo = lo * 9 / 5 + 32
                hi = hi * 9 / 5 + 32
            return {"kind": "range", "lo": lo, "hi": hi}

        # Exact single value: "be 11°C" / "11°C" in groupItemTitle
        m = _TEMP_EXACT.search(text)
        if not m and group_title:
            # In groupItemTitle, the value may be bare: "11°C" without "be"
            import re as _re
            bare = _re.search(r'^(-?\d{1,3})\s*°?\s*([CF])$', group_title.strip(), _re.IGNORECASE)
            if bare:
                m = bare
        if m:
            t = float(m.group(1))
            if m.group(2).upper() == 'C':
                t_f = t * 9 / 5 + 32
                return {"kind": "range", "lo": t_f - 1.0, "hi": t_f + 1.0}
            return {"kind": "range", "lo": t - 0.5, "hi": t + 0.5}

        # "above 72°F" in question text
        m = _TEMP_ABOVE.search(text)
        if m:
            t = float(m.group(1))
            if m.group(2).upper() == 'C':
                t = t * 9 / 5 + 32
            return {"kind": "above", "threshold": t}

        # "below 72°F" in question text
        m = _TEMP_BELOW.search(text)
        if m:
            t = float(m.group(1))
            if m.group(2).upper() == 'C':
                t = t * 9 / 5 + 32
            return {"kind": "below", "threshold": t}

        return None

    def _city_from_slug(self, slug: str) -> Optional[str]:
        """Extract city name from 'highest-temperature-in-{city}-on-...' slug.

        Examples:
          highest-temperature-in-london-on-february-23-2026-11c  -> "London"
          highest-temperature-in-buenos-aires-on-...             -> "Buenos Aires"
          highest-temperature-in-new-york-on-...                 -> "New York"
        """
        prefix = "highest-temperature-in-"
        if not slug.startswith(prefix):
            return None
        rest = slug[len(prefix):]
        if "-on-" not in rest:
            return None
        city_slug = rest.split("-on-")[0]  # e.g. "london", "buenos-aires"
        return city_slug.replace("-", " ").title()  # "London", "Buenos Aires"

    def _extract_city(self, question: str, allowed: List[str]) -> Optional[str]:
        """Match question text against allowed city list + known CITY_COORDS keys."""
        q = question.lower()
        # Check allowed cities first (highest priority, user-configured)
        for city in allowed:
            if city.lower() in q:
                return city
        # Fall back to known coordinates dict
        for city in CITY_COORDS:
            if city.lower() in q:
                if not allowed:
                    return city
                # Accept if it fuzzy-matches an allowed city
                for ac in allowed:
                    if ac.lower() in city.lower() or city.lower() in ac.lower():
                        return ac
        return None

    def _resolve_coords(self, city: str) -> Optional[Tuple[float, float]]:
        """Look up (lat, lon) for a city name."""
        coords = CITY_COORDS.get(city)
        if coords:
            return coords
        city_lower = city.lower()
        for k, v in CITY_COORDS.items():
            if k.lower() in city_lower or city_lower in k.lower():
                return v
        return None

    def _parse_end_date(self, market: dict) -> Optional[datetime]:
        """Extract resolution datetime from market data."""
        for key in ("end_date_iso", "endDate", "endDateIso", "resolutionDate", "end_date"):
            val = market.get(key)
            if not val:
                continue
            try:
                if isinstance(val, (int, float)):
                    return datetime.fromtimestamp(float(val), tz=timezone.utc)
                s = str(val).replace("Z", "+00:00")
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except (ValueError, OSError):
                continue

        # Try parsing date from question text
        question = market.get("question", "")
        m = _DATE_MDY.search(question)
        if m:
            try:
                day = int(m.group(1))
                month_str = m.group(0).split()[0].replace("on ", "").strip()
                # Strip ordinal suffix
                month_str = re.sub(r'\d+(st|nd|rd|th)?', '', month_str).strip()
                now = datetime.now(timezone.utc)
                dt = datetime.strptime(f"{month_str} {day} {now.year}", "%B %d %Y")
                dt = dt.replace(tzinfo=timezone.utc)
                if dt < now - timedelta(days=1):
                    dt = dt.replace(year=now.year + 1)
                return dt
            except ValueError:
                pass

        return None
