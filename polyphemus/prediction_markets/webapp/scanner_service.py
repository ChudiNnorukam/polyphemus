"""Background scanner orchestrator wrapping all 3 scanners."""
import asyncio
import hashlib
import logging
from datetime import date, datetime, timezone

from .models import AppSettings, Opportunity

logger = logging.getLogger(__name__)


class ScannerService:
    """Central data hub for all scanner results."""

    def __init__(self, settings: AppSettings | None = None):
        self.settings = settings or AppSettings()
        self._weather_opps: list[Opportunity] = []
        self._kalshi_opps: list[Opportunity] = []
        self._arb_opps: list[Opportunity] = []
        self._last_scan: dict[str, str] = {}
        self._subscribers: list[asyncio.Queue] = []

    def get_all_opportunities(self, scanner_filter: str | None = None, sort_by: str = "ev_net") -> list[Opportunity]:
        if scanner_filter == "weather":
            opps = list(self._weather_opps)
        elif scanner_filter == "kalshi":
            opps = list(self._kalshi_opps)
        elif scanner_filter == "arbitrage":
            opps = list(self._arb_opps)
        else:
            opps = self._weather_opps + self._kalshi_opps + self._arb_opps

        key = sort_by if sort_by in ("ev_net", "edge", "kelly") else "ev_net"
        opps.sort(key=lambda o: abs(getattr(o, key, 0)), reverse=True)
        return opps

    def get_scanner_status(self) -> dict[str, str | None]:
        return {
            "weather": self._last_scan.get("weather"),
            "kalshi": self._last_scan.get("kalshi"),
            "arbitrage": self._last_scan.get("arbitrage"),
        }

    async def scan_weather(self) -> list[Opportunity]:
        """Run weather scanner using lower-level functions."""
        from ..weather.scanner import fetch_temperature_markets, parse_temperature_markets
        from ..weather.forecast import fetch_forecast, forecast_to_distribution
        from ..weather.detector import detect_divergences, compute_kelly, classify_question
        from ..weather.config import CITIES

        try:
            events = await fetch_temperature_markets()
            markets = parse_temperature_markets(events)
            parseable = [m for m in markets if m["city"] and m["date"]]
        except Exception as exc:
            logger.error("Weather scan failed at market fetch: %s", exc)
            return []

        today = datetime.now(timezone.utc).date()
        opps: list[Opportunity] = []

        # Fetch forecasts concurrently (semaphore to limit connections)
        sem = asyncio.Semaphore(8)

        async def process_market(market: dict) -> list[Opportunity]:
            city_key = market["city"]
            date_str = market["date"]

            if city_key not in CITIES:
                return []
            try:
                target_date = date.fromisoformat(date_str)
            except (ValueError, TypeError):
                return []
            if target_date <= today:
                return []

            async with sem:
                forecast = await fetch_forecast(city_key, target_date)
            if not forecast:
                return []

            city_cfg = CITIES[city_key]
            unit = city_cfg["unit"]
            forecast_temp = forecast["temp_max_f"] if unit == "F" else forecast["temp_max_c"]

            days_until = (target_date - today).days
            dist = forecast_to_distribution(forecast_temp, unit, days_until=days_until)
            market["_forecast_temp"] = forecast_temp
            market["_unit"] = unit
            market["_days_until"] = days_until

            raw_opps = detect_divergences(market, dist, threshold=self.settings.weather_threshold)
            result = []
            for raw in raw_opps:
                if raw["ev_net"] < self.settings.weather_min_ev:
                    continue
                kelly = compute_kelly(raw["edge"], raw["market_price"], raw["direction"])
                if kelly < self.settings.weather_min_kelly:
                    continue
                q_type = classify_question(raw.get("question", ""))

                # Compute countdown
                days_left = (target_date - today).days
                countdown = f"{days_left}d" if days_left > 0 else "today"

                opp_id = hashlib.md5(f"weather:{raw.get('token_id', '')}:{city_key}:{date_str}:{raw['temp']}".encode()).hexdigest()[:12]
                result.append(Opportunity(
                    id=f"w-{opp_id}",
                    scanner_type="weather",
                    platform="polymarket",
                    title=f"{city_cfg.get('display', city_key)} {raw['temp']}{chr(176)}{unit} {date_str}",
                    city=city_key,
                    city_display=city_cfg.get("display", city_key),
                    market_date=date_str,
                    temp=raw["temp"],
                    unit=unit,
                    direction=raw["direction"],
                    market_price=raw["market_price"],
                    forecast_prob=raw["forecast_prob"],
                    edge=raw["edge"],
                    ev_net=raw["ev_net"],
                    kelly=kelly,
                    token_id=raw.get("token_id"),
                    question=raw.get("question"),
                    question_type=q_type,
                    countdown=countdown,
                    forecast_temp=round(forecast_temp, 1),
                    scanned_at=datetime.now(timezone.utc).isoformat(),
                ))
            return result

        tasks = [process_market(m) for m in parseable]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, list):
                opps.extend(r)
            elif isinstance(r, Exception):
                logger.warning("Weather scan error for a market: %s", r)

        opps.sort(key=lambda o: abs(o.ev_net), reverse=True)
        self._weather_opps = opps
        self._last_scan["weather"] = datetime.now(timezone.utc).isoformat()
        logger.info("Weather scan complete: %d opportunities", len(opps))
        await self._notify("scan_complete")
        return opps

    async def scan_kalshi(self) -> list[Opportunity]:
        """Scan Kalshi weather markets for cross-reference."""
        try:
            from ..kalshi.client import KalshiClient
            from ..kalshi.scanner import categorize_market
        except ImportError:
            logger.warning("Kalshi modules not available")
            return []

        opps: list[Opportunity] = []
        try:
            async with KalshiClient(demo=True) as client:
                markets = await client.get_all_markets(limit=500, status="open")

            for m in markets:
                cat = categorize_market(m)
                if cat != "weather":
                    continue
                ticker = m.get("ticker", "")
                title = m.get("title", "")
                yes_bid = m.get("yes_bid") or 0
                yes_ask = m.get("yes_ask") or 0
                if not yes_bid or not yes_ask:
                    continue

                opp_id = hashlib.md5(f"kalshi:{ticker}".encode()).hexdigest()[:12]
                opps.append(Opportunity(
                    id=f"k-{opp_id}",
                    scanner_type="kalshi",
                    platform="kalshi",
                    title=title[:80],
                    direction="MONITOR",
                    market_price=round((yes_bid + yes_ask) / 200, 4),  # cents to dollars
                    scanned_at=datetime.now(timezone.utc).isoformat(),
                ))
        except Exception as exc:
            logger.error("Kalshi scan failed: %s", exc)

        self._kalshi_opps = opps
        self._last_scan["kalshi"] = datetime.now(timezone.utc).isoformat()
        logger.info("Kalshi scan complete: %d weather markets", len(opps))
        await self._notify("scan_complete")
        return opps

    async def scan_arbitrage(self) -> list[Opportunity]:
        """Run cross-platform arbitrage scanner."""
        try:
            from ..arbitrage.scanner import fetch_polymarket_markets, fetch_kalshi_markets, compute_arb_opportunity
            from ..arbitrage.matcher import match_markets
        except ImportError:
            logger.warning("Arbitrage modules not available")
            return []

        opps: list[Opportunity] = []
        try:
            poly_markets, kalshi_markets = await asyncio.gather(
                fetch_polymarket_markets(),
                fetch_kalshi_markets(),
            )
            matches = match_markets(poly_markets, kalshi_markets)

            for match in matches:
                arb = compute_arb_opportunity(match, poly_markets, kalshi_markets)
                if not arb or arb.get("net_profit", 0) < self.settings.arb_min_spread:
                    continue

                opp_id = hashlib.md5(f"arb:{match.get('poly_title', '')}:{match.get('kalshi_title', '')}".encode()).hexdigest()[:12]
                opps.append(Opportunity(
                    id=f"a-{opp_id}",
                    scanner_type="arbitrage",
                    platform="cross-platform",
                    title=f"ARB: {match.get('poly_title', '')[:50]}",
                    direction="BUY",
                    market_price=arb.get("poly_price", 0),
                    edge=arb.get("net_profit", 0),
                    ev_net=arb.get("net_profit", 0),
                    poly_price=arb.get("poly_price"),
                    kalshi_price=arb.get("kalshi_price"),
                    net_profit=arb.get("net_profit"),
                    confidence=match.get("confidence"),
                    scanned_at=datetime.now(timezone.utc).isoformat(),
                ))
        except Exception as exc:
            logger.error("Arbitrage scan failed: %s", exc)

        self._arb_opps = opps
        self._last_scan["arbitrage"] = datetime.now(timezone.utc).isoformat()
        logger.info("Arb scan complete: %d opportunities", len(opps))
        await self._notify("scan_complete")
        return opps

    # SSE subscriber management
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers = [s for s in self._subscribers if s is not q]

    async def _notify(self, event_type: str) -> None:
        for q in self._subscribers:
            try:
                q.put_nowait(event_type)
            except asyncio.QueueFull:
                pass
