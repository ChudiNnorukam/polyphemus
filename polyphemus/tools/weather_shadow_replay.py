#!/usr/bin/env python3
"""Replay Polymarket weather markets against archived Open-Meteo runs.

This tool is intentionally standalone:
- stdlib only (no dotenv/pydantic/aiohttp dependency)
- safe to run from outside the package root to avoid local types.py shadowing

It performs two tasks:
1. Live shadow scan of currently active weather markets.
2. Historical replay of resolved markets using fixed 6-hour scan epochs, Polymarket
   price history, and Open-Meteo single-run deterministic forecasts.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


GAMMA_API_URL = "https://gamma-api.polymarket.com"
CLOB_HISTORY_URL = "https://clob.polymarket.com/prices-history"
DATA_API_TRADES_URL = "https://data-api.polymarket.com/trades"
SINGLE_RUNS_URL = "https://single-runs-api.open-meteo.com/v1/forecast"
USER_AGENT = "polyphemus-weather-shadow/1.0"
SSL_CONTEXT = ssl._create_unverified_context()

CITY_COORDS: Dict[str, Tuple[float, float]] = {
    "Atlanta": (33.7490, -84.3880),
    "Dallas": (32.7767, -96.7970),
    "London": (51.5074, -0.1278),
    "Paris": (48.8566, 2.3522),
    "Ankara": (39.9334, 32.8597),
    "Wellington": (-41.2866, 174.7756),
    "Lucknow": (26.8467, 80.9462),
}

CITY_EVENT_SLUGS: Dict[str, str] = {
    "Atlanta": "atlanta",
    "Dallas": "dallas",
    "London": "london",
    "Paris": "paris",
    "Ankara": "ankara",
    "Wellington": "wellington",
    "Lucknow": "lucknow",
}

_TEMP_RANGE = re.compile(r"(-?\d{1,3})\s*[–\-]\s*(-?\d{1,3})\s*°?\s*([FC])", re.IGNORECASE)
_TEMP_OR_ABOVE = re.compile(r"(-?\d{1,3})\s*°?\s*([CF])\s+or\s+above", re.IGNORECASE)
_TEMP_ABOVE = re.compile(
    r"(?:above|exceed|over|at least|>=?|higher than)\s*(-?\d{1,3})\s*°?\s*([CF])",
    re.IGNORECASE,
)
_TEMP_OR_BELOW = re.compile(r"(-?\d{1,3})\s*°?\s*([CF])\s+or\s+below", re.IGNORECASE)
_TEMP_BELOW = re.compile(
    r"(?:below|under|less than|<=?|lower than)\s*(-?\d{1,3})\s*°?\s*([CF])",
    re.IGNORECASE,
)
_TEMP_EXACT = re.compile(r"\bbe\s+(-?\d{1,3})\s*°?\s*([CF])\b", re.IGNORECASE)


@dataclass
class ReplayProfile:
    name: str
    weather_entry_max_price: float
    weather_exit_min_price: float
    weather_noaa_min_prob: float
    weather_min_edge: float
    weather_allow_complement: bool
    weather_hold_to_resolution: bool
    weather_max_spend: float
    weather_max_open_positions: int


@dataclass
class ReplayTrade:
    profile: str
    city: str
    slug: str
    question: str
    outcome: str
    entry_ts: int
    entry_iso: str
    entry_price: float
    model_prob: float
    edge: float
    spend: float
    shares: float
    exit_ts: int
    exit_iso: str
    exit_price: float
    exit_reason: str
    resolved_win: bool
    pnl: float
    roi_pct: float
    target_date: str


def fetch_json(url: str, params: Optional[dict] = None) -> dict | list:
    query = urllib.parse.urlencode(params or {})
    full_url = f"{url}?{query}" if query else url
    req = urllib.request.Request(full_url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as resp:
        return json.load(resp)


def build_event_slug(city: str, target: date) -> str:
    city_slug = CITY_EVENT_SLUGS[city]
    return f"highest-temperature-in-{city_slug}-on-{target.strftime('%B').lower()}-{target.day}-{target.year}"


def dt_to_ts(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def parse_iso_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def round6(value: float) -> float:
    return round(float(value), 6)


def parse_temp_condition(question: str, group_title: str = "") -> Optional[dict]:
    text = group_title if group_title else question

    m = _TEMP_OR_ABOVE.search(text)
    if m:
        t = float(m.group(1))
        if m.group(2).upper() == "C":
            t = t * 9 / 5 + 32
        return {"kind": "above", "threshold": t}

    m = _TEMP_OR_BELOW.search(text)
    if m:
        t = float(m.group(1))
        if m.group(2).upper() == "C":
            t = t * 9 / 5 + 32
        return {"kind": "below", "threshold": t}

    m = _TEMP_RANGE.search(text)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        if m.group(3).upper() == "C":
            lo = lo * 9 / 5 + 32
            hi = hi * 9 / 5 + 32
        return {"kind": "range", "lo": lo, "hi": hi}

    m = _TEMP_EXACT.search(text)
    if not m and group_title:
        bare = re.search(r"^(-?\d{1,3})\s*°?\s*([CF])$", group_title.strip(), re.IGNORECASE)
        if bare:
            m = bare
    if m:
        t = float(m.group(1))
        if m.group(2).upper() == "C":
            tf = t * 9 / 5 + 32
            return {"kind": "range", "lo": tf - 1.0, "hi": tf + 1.0}
        return {"kind": "range", "lo": t - 0.5, "hi": t + 0.5}

    m = _TEMP_ABOVE.search(text)
    if m:
        t = float(m.group(1))
        if m.group(2).upper() == "C":
            t = t * 9 / 5 + 32
        return {"kind": "above", "threshold": t}

    m = _TEMP_BELOW.search(text)
    if m:
        t = float(m.group(1))
        if m.group(2).upper() == "C":
            t = t * 9 / 5 + 32
        return {"kind": "below", "threshold": t}

    return None


def compute_prob(mu: float, cond: dict, hours_left: float) -> float:
    sigma = 4.0 * math.sqrt(max(hours_left, 1.0) / 24.0)

    def ncdf(x: float) -> float:
        return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))

    kind = cond.get("kind")
    if kind == "range":
        prob = ncdf(cond["hi"]) - ncdf(cond["lo"])
    elif kind == "above":
        prob = 1.0 - ncdf(cond["threshold"])
    elif kind == "below":
        prob = ncdf(cond["threshold"])
    else:
        prob = 0.0
    return max(0.0, min(1.0, round(prob, 4)))


def nearest_scan_epochs(start_dt: datetime, end_dt: datetime, step_hours: int) -> List[datetime]:
    current = start_dt.replace(minute=0, second=0, microsecond=0)
    if current < start_dt:
        current += timedelta(hours=1)
    while current.hour % step_hours != 0:
        current += timedelta(hours=1)
    epochs = []
    while current < end_dt:
        epochs.append(current)
        current += timedelta(hours=step_hours)
    return epochs


def extract_daily_max_from_run(city: str, run_dt: datetime, target_day: date, cache: dict) -> Optional[float]:
    key = (city, run_dt.isoformat())
    if key not in cache:
        lat, lon = CITY_COORDS[city]
        data = fetch_json(
            SINGLE_RUNS_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": "temperature_2m_max",
                "temperature_unit": "fahrenheit",
                "models": "ecmwf_ifs025",
                "forecast_days": 4,
                "run": run_dt.strftime("%Y-%m-%dT%H:%M"),
                "timezone": "UTC",
            },
        )
        daily = data.get("daily", {})
        cache[key] = dict(zip(daily.get("time", []), daily.get("temperature_2m_max", [])))
        time.sleep(0.05)
    return cache[key].get(target_day.isoformat())


def fetch_event(city: str, target_day: date) -> Optional[dict]:
    slug = build_event_slug(city, target_day)
    payload = fetch_json(f"{GAMMA_API_URL}/events", params={"slug": slug})
    if not isinstance(payload, list) or not payload:
        return None
    for item in payload:
        if item.get("slug") == slug:
            return item
    return payload[0]


def fetch_market_trade_histories(condition_id: str) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
    payload = fetch_json(DATA_API_TRADES_URL, params={"market": condition_id, "limit": 2000})
    yes_rows: List[Tuple[int, float]] = []
    no_rows: List[Tuple[int, float]] = []
    if not isinstance(payload, list):
        return yes_rows, no_rows
    for trade in payload:
        try:
            row = (int(trade["timestamp"]), float(trade["price"]))
            outcome = str(trade.get("outcome", "")).lower()
        except (KeyError, TypeError, ValueError):
            continue
        if outcome == "yes":
            yes_rows.append(row)
        elif outcome == "no":
            no_rows.append(row)
    yes_rows.sort(key=lambda item: item[0])
    no_rows.sort(key=lambda item: item[0])
    return yes_rows, no_rows


def price_at(history: Sequence[Tuple[int, float]], ts: int) -> Optional[float]:
    if not history:
        return None
    idx = bisect.bisect_right(history, (ts, float("inf"))) - 1
    if idx < 0:
        return None
    return history[idx][1]


def first_exit_price(
    history: Sequence[Tuple[int, float]],
    entry_ts: int,
    target_price: float,
) -> Optional[Tuple[int, float]]:
    for ts, price in history:
        if ts > entry_ts and price >= target_price:
            return ts, price
    return None


def winner_for_market(market: dict) -> Optional[str]:
    try:
        outcome_prices = [float(x) for x in json.loads(market.get("outcomePrices", "[]"))]
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if len(outcome_prices) < 2:
        return None
    if outcome_prices[0] >= 0.999:
        return "Yes"
    if outcome_prices[1] >= 0.999:
        return "No"
    return None


def evaluate_market_candidate(
    profile: ReplayProfile,
    yes_price: Optional[float],
    no_price: Optional[float],
    yes_prob: float,
) -> Optional[Tuple[str, float, float]]:
    candidates = []
    if yes_price is not None and 0 < yes_price < profile.weather_entry_max_price:
        edge = yes_prob - yes_price
        if yes_prob >= profile.weather_noaa_min_prob and edge >= profile.weather_min_edge:
            candidates.append(("Yes", yes_price, edge, yes_prob))
    if profile.weather_allow_complement and no_price is not None and 0 < no_price < profile.weather_entry_max_price:
        no_prob = 1.0 - yes_prob
        edge = no_prob - no_price
        if no_prob >= profile.weather_noaa_min_prob and edge >= profile.weather_min_edge:
            candidates.append(("No", no_price, edge, no_prob))
    if not candidates:
        return None
    best = max(candidates, key=lambda item: (item[2], item[3]))
    return best[0], round6(best[1]), round6(best[3])


def replay_profile(
    events: Sequence[dict],
    profile: ReplayProfile,
    scan_step_hours: int,
) -> List[ReplayTrade]:
    forecast_cache: dict = {}
    market_state: Dict[str, dict] = {}
    for event in events:
        for market in event["markets"]:
            market_state[market["slug"]] = {
                "entered": False,
                "market": market,
                "event": event,
            }

    open_positions: List[dict] = []
    closed_trades: List[ReplayTrade] = []
    all_epochs = sorted(
        {
            epoch
            for event in events
            for epoch in nearest_scan_epochs(event["start_dt"], event["scan_end_dt"], scan_step_hours)
        }
    )

    for epoch_dt in all_epochs:
        epoch_ts = dt_to_ts(epoch_dt)

        still_open = []
        for pos in open_positions:
            market = pos["market"]
            winner = winner_for_market(market)
            if profile.weather_hold_to_resolution:
                if epoch_dt < pos["event"]["resolve_dt"]:
                    still_open.append(pos)
                    continue
                win = winner == pos["outcome"]
                exit_price = 1.0 if win else 0.0
                pnl = pos["shares"] * (exit_price - pos["entry_price"])
                closed_trades.append(
                    ReplayTrade(
                        profile=profile.name,
                        city=pos["city"],
                        slug=market["slug"],
                        question=market["question"],
                        outcome=pos["outcome"],
                        entry_ts=pos["entry_ts"],
                        entry_iso=datetime.fromtimestamp(pos["entry_ts"], tz=timezone.utc).isoformat(),
                        entry_price=pos["entry_price"],
                        model_prob=pos["model_prob"],
                        edge=pos["edge"],
                        spend=pos["spend"],
                        shares=pos["shares"],
                        exit_ts=dt_to_ts(pos["resolve_dt"]),
                        exit_iso=pos["resolve_dt"].isoformat(),
                        exit_price=exit_price,
                        exit_reason="resolution",
                        resolved_win=win,
                        pnl=round6(pnl),
                        roi_pct=round6((pnl / pos["spend"]) * 100.0 if pos["spend"] else 0.0),
                        target_date=pos["target_date"].isoformat(),
                    )
                )
                continue

            exit_hit = first_exit_price(pos["history"], pos["entry_ts"], profile.weather_exit_min_price)
            if exit_hit:
                exit_ts, exit_price = exit_hit
                pnl = pos["shares"] * (exit_price - pos["entry_price"])
                closed_trades.append(
                    ReplayTrade(
                        profile=profile.name,
                        city=pos["city"],
                        slug=market["slug"],
                        question=market["question"],
                        outcome=pos["outcome"],
                        entry_ts=pos["entry_ts"],
                        entry_iso=datetime.fromtimestamp(pos["entry_ts"], tz=timezone.utc).isoformat(),
                        entry_price=pos["entry_price"],
                        model_prob=pos["model_prob"],
                        edge=pos["edge"],
                        spend=pos["spend"],
                        shares=pos["shares"],
                        exit_ts=exit_ts,
                        exit_iso=datetime.fromtimestamp(exit_ts, tz=timezone.utc).isoformat(),
                        exit_price=exit_price,
                        exit_reason="take_profit",
                        resolved_win=winner == pos["outcome"],
                        pnl=round6(pnl),
                        roi_pct=round6((pnl / pos["spend"]) * 100.0 if pos["spend"] else 0.0),
                        target_date=pos["target_date"].isoformat(),
                    )
                )
                continue

            if epoch_dt >= pos["event"]["resolve_dt"]:
                win = winner == pos["outcome"]
                exit_price = 1.0 if win else 0.0
                pnl = pos["shares"] * (exit_price - pos["entry_price"])
                closed_trades.append(
                    ReplayTrade(
                        profile=profile.name,
                        city=pos["city"],
                        slug=market["slug"],
                        question=market["question"],
                        outcome=pos["outcome"],
                        entry_ts=pos["entry_ts"],
                        entry_iso=datetime.fromtimestamp(pos["entry_ts"], tz=timezone.utc).isoformat(),
                        entry_price=pos["entry_price"],
                        model_prob=pos["model_prob"],
                        edge=pos["edge"],
                        spend=pos["spend"],
                        shares=pos["shares"],
                        exit_ts=dt_to_ts(pos["resolve_dt"]),
                        exit_iso=pos["resolve_dt"].isoformat(),
                        exit_price=exit_price,
                        exit_reason="resolution_after_no_tp",
                        resolved_win=win,
                        pnl=round6(pnl),
                        roi_pct=round6((pnl / pos["spend"]) * 100.0 if pos["spend"] else 0.0),
                        target_date=pos["target_date"].isoformat(),
                    )
                )
            else:
                still_open.append(pos)
        open_positions = still_open

        if len(open_positions) >= profile.weather_max_open_positions:
            continue

        for event in events:
            if not (event["start_dt"] <= epoch_dt < event["scan_end_dt"]):
                continue

            city = event["city"]
            target_day = event["target_day"]
            run_dt = epoch_dt.replace(minute=0, second=0, microsecond=0)
            target_high = extract_daily_max_from_run(city, run_dt, target_day, forecast_cache)
            if target_high is None:
                continue

            hours_left = max(1.0, (event["scan_end_dt"] - epoch_dt).total_seconds() / 3600.0)

            for market in event["markets"]:
                state = market_state[market["slug"]]
                if state["entered"]:
                    continue
                if len(open_positions) >= profile.weather_max_open_positions:
                    break

                cond = market.get("temp_cond")
                if not cond:
                    continue
                yes_prob = compute_prob(target_high, cond, hours_left)
                yes_price = price_at(market["yes_history"], epoch_ts)
                no_price = price_at(market["no_history"], epoch_ts)
                candidate = evaluate_market_candidate(profile, yes_price, no_price, yes_prob)
                if not candidate:
                    continue

                outcome, entry_price, model_prob = candidate
                spend = profile.weather_max_spend
                shares = spend / entry_price
                edge = round6(model_prob - entry_price)
                state["entered"] = True
                history = market["yes_history"] if outcome == "Yes" else market["no_history"]
                open_positions.append(
                    {
                        "city": city,
                        "event": event,
                        "market": market,
                        "outcome": outcome,
                        "entry_ts": epoch_ts,
                        "entry_price": entry_price,
                        "model_prob": model_prob,
                        "edge": edge,
                        "spend": spend,
                        "shares": shares,
                        "history": history,
                        "target_date": target_day,
                        "resolve_dt": market["resolve_dt"],
                    }
                )

    return closed_trades


def summarize_trades(trades: Sequence[ReplayTrade]) -> dict:
    if not trades:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
            "avg_entry_price": 0.0,
            "avg_roi_pct": 0.0,
            "median_roi_pct": 0.0,
        }
    wins = sum(1 for trade in trades if trade.pnl > 0)
    losses = sum(1 for trade in trades if trade.pnl <= 0)
    total_pnl = sum(trade.pnl for trade in trades)
    rois = sorted(trade.roi_pct for trade in trades)
    median_roi = rois[len(rois) // 2]
    return {
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": round6(wins / len(trades)),
        "total_pnl": round6(total_pnl),
        "avg_pnl": round6(total_pnl / len(trades)),
        "avg_entry_price": round6(sum(trade.entry_price for trade in trades) / len(trades)),
        "avg_roi_pct": round6(sum(trade.roi_pct for trade in trades) / len(trades)),
        "median_roi_pct": round6(median_roi),
    }


def format_live_scan(cities: Sequence[str], profile: ReplayProfile) -> List[dict]:
    now = datetime.now(timezone.utc)
    results = []
    forecast_cache: dict = {}
    run_hour = (now.hour // 6) * 6
    run_dt = now.replace(hour=run_hour, minute=0, second=0, microsecond=0)
    if run_dt > now:
        run_dt -= timedelta(hours=6)

    for days_ahead in range(3):
        target_day = (now + timedelta(days=days_ahead)).date()
        for city in cities:
            event = fetch_event(city, target_day)
            if not event:
                continue
            event_end = parse_iso_dt(event["endDate"])
            if event_end <= now:
                continue
            target_high = extract_daily_max_from_run(city, run_dt, target_day, forecast_cache)
            if target_high is None:
                continue
            hours_left = max(1.0, (event_end - now).total_seconds() / 3600.0)
            for market in event.get("markets", []):
                cond = parse_temp_condition(market.get("question", ""), market.get("groupItemTitle", ""))
                if not cond:
                    continue
                yes_prob = compute_prob(target_high, cond, hours_left)
                try:
                    outcome_prices = [float(x) for x in json.loads(market.get("outcomePrices", "[]"))]
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                yes_price = outcome_prices[0] if len(outcome_prices) > 0 else None
                no_price = outcome_prices[1] if len(outcome_prices) > 1 else None
                candidate = evaluate_market_candidate(profile, yes_price, no_price, yes_prob)
                if not candidate:
                    continue
                outcome, price, model_prob = candidate
                results.append(
                    {
                        "city": city,
                        "target_date": target_day.isoformat(),
                        "question": market.get("question", ""),
                        "slug": market.get("slug", ""),
                        "outcome": outcome,
                        "market_price": price,
                        "model_prob": model_prob,
                        "edge": round6(model_prob - price),
                        "hours_left": round6(hours_left),
                    }
                )
    results.sort(key=lambda item: (item["edge"], item["model_prob"]), reverse=True)
    return results


def load_events(cities: Sequence[str], start_day: date, end_day: date) -> List[dict]:
    events = []
    current = start_day
    while current <= end_day:
        for city in cities:
            event = fetch_event(city, current)
            if not event:
                continue
            try:
                start_dt = parse_iso_dt(event["startTime"] if "startTime" in event else event["startDate"])
                scan_end_dt = parse_iso_dt(event["endDate"])
            except (KeyError, ValueError):
                continue
            if not event.get("closed", False):
                continue
            enriched_markets = []
            for market in event.get("markets", []):
                token_ids = json.loads(market.get("clobTokenIds", "[]"))
                if len(token_ids) < 2:
                    continue
                temp_cond = parse_temp_condition(market.get("question", ""), market.get("groupItemTitle", ""))
                if not temp_cond:
                    continue
                yes_history, no_history = fetch_market_trade_histories(market["conditionId"])
                time.sleep(0.03)
                resolve_dt = scan_end_dt
                for key in ("closedTime", "umaEndDate", "endDate"):
                    raw = market.get(key)
                    if not raw:
                        continue
                    try:
                        parsed = parse_iso_dt(str(raw).replace(" ", "T"))
                        if parsed > resolve_dt:
                            resolve_dt = parsed
                    except ValueError:
                        continue
                enriched_markets.append(
                    {
                        **market,
                        "temp_cond": temp_cond,
                        "yes_token_id": token_ids[0],
                        "no_token_id": token_ids[1],
                        "yes_history": yes_history,
                        "no_history": no_history,
                        "resolve_dt": resolve_dt,
                    }
                )
            if enriched_markets:
                resolve_dt = max(market["resolve_dt"] for market in enriched_markets)
                events.append(
                    {
                        "city": city,
                        "slug": event.get("slug", ""),
                        "target_day": current,
                        "start_dt": start_dt,
                        "scan_end_dt": scan_end_dt,
                        "resolve_dt": resolve_dt,
                        "markets": enriched_markets,
                    }
                )
        current += timedelta(days=1)
    return events


def write_report(
    output_md: Path,
    output_json: Path,
    live_scan: Sequence[dict],
    replay_results: Dict[str, dict],
    metadata: dict,
) -> None:
    payload = {
        "metadata": metadata,
        "live_scan": list(live_scan),
        "replay_results": replay_results,
    }
    output_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Weather Shadow Replay",
        "",
        f"- Generated: `{datetime.now(timezone.utc).isoformat()}`",
        f"- Cities: `{', '.join(metadata['cities'])}`",
        f"- Replay window: `{metadata['start_day']}` to `{metadata['end_day']}`",
        f"- Scan cadence: `{metadata['scan_step_hours']}h`",
        f"- Historical forecast source: `single-runs-api.open-meteo.com` with `models=ecmwf_ifs025`",
        f"- Market price source: `clob.polymarket.com/prices-history` (last traded price proxy)",
        "",
        "## Live Shadow Candidates",
        "",
    ]
    if live_scan:
        lines.append("| Edge | City | Date | Side | Price | Prob | Market |")
        lines.append("| ---: | --- | --- | --- | ---: | ---: | --- |")
        for row in live_scan[:15]:
            lines.append(
                f"| {row['edge']:.3f} | {row['city']} | {row['target_date']} | {row['outcome']} | "
                f"{row['market_price']:.3f} | {row['model_prob']:.3f} | {row['question']} |"
            )
    else:
        lines.append("No live candidates passed the configured thresholds.")

    lines.extend(["", "## Replay Summary", ""])
    for profile_name, data in replay_results.items():
        summary = data["summary"]
        lines.extend(
            [
                f"### {profile_name}",
                "",
                f"- Trades: `{summary['trades']}`",
                f"- Wins / losses: `{summary['wins']}` / `{summary['losses']}`",
                f"- Win rate: `{summary['win_rate']:.1%}`",
                f"- Total PnL: `${summary['total_pnl']:.2f}`",
                f"- Avg PnL/trade: `${summary['avg_pnl']:.2f}`",
                f"- Avg entry: `{summary['avg_entry_price']:.4f}`",
                f"- Avg ROI: `{summary['avg_roi_pct']:.1f}%`",
                f"- Median ROI: `{summary['median_roi_pct']:.1f}%`",
                "",
            ]
        )
        top = data["trades"][:10]
        if top:
            lines.append("| PnL | ROI | Entry | Exit | Side | City | Date | Market |")
            lines.append("| ---: | ---: | ---: | ---: | --- | --- | --- | --- |")
            for trade in top:
                lines.append(
                    f"| {trade['pnl']:.2f} | {trade['roi_pct']:.1f}% | {trade['entry_price']:.3f} | "
                    f"{trade['exit_price']:.3f} | {trade['outcome']} | {trade['city']} | "
                    f"{trade['target_date']} | {trade['question']} |"
                )
            lines.append("")

    output_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Replay weather-market shadow decisions.")
    parser.add_argument("--cities", default="Atlanta,Dallas,London,Paris,Wellington,Lucknow,Ankara")
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument("--start-day", default="")
    parser.add_argument("--end-day", default="")
    parser.add_argument("--scan-step-hours", type=int, default=6)
    parser.add_argument(
        "--output-prefix",
        default=f"dario_output/weather_shadow_replay_{datetime.now(timezone.utc).strftime('%Y%m%d')}",
    )
    args = parser.parse_args(argv)

    cities = [city.strip() for city in args.cities.split(",") if city.strip()]
    today = datetime.now(timezone.utc).date()
    if args.end_day:
        end_day = date.fromisoformat(args.end_day)
    else:
        end_day = today - timedelta(days=1)
    if args.start_day:
        start_day = date.fromisoformat(args.start_day)
    else:
        start_day = end_day - timedelta(days=max(args.lookback_days - 1, 0))

    profiles = [
        ReplayProfile(
            name="yes_only_take_profit",
            weather_entry_max_price=0.15,
            weather_exit_min_price=0.45,
            weather_noaa_min_prob=0.70,
            weather_min_edge=0.08,
            weather_allow_complement=False,
            weather_hold_to_resolution=False,
            weather_max_spend=2.0,
            weather_max_open_positions=5,
        ),
        ReplayProfile(
            name="yes_or_no_hold_to_resolution",
            weather_entry_max_price=0.15,
            weather_exit_min_price=0.45,
            weather_noaa_min_prob=0.70,
            weather_min_edge=0.08,
            weather_allow_complement=True,
            weather_hold_to_resolution=True,
            weather_max_spend=2.0,
            weather_max_open_positions=5,
        ),
        ReplayProfile(
            name="coldmath_tail_hold_to_resolution",
            weather_entry_max_price=0.03,
            weather_exit_min_price=0.45,
            weather_noaa_min_prob=0.85,
            weather_min_edge=0.25,
            weather_allow_complement=True,
            weather_hold_to_resolution=True,
            weather_max_spend=20.0,
            weather_max_open_positions=20,
        ),
    ]

    events = load_events(cities, start_day, end_day)
    live_scan = format_live_scan(cities, profiles[1])
    replay_results = {}
    for profile in profiles:
        trades = replay_profile(events, profile, args.scan_step_hours)
        trades_sorted = sorted((asdict(trade) for trade in trades), key=lambda item: item["pnl"], reverse=True)
        replay_results[profile.name] = {
            "summary": summarize_trades(trades),
            "trades": trades_sorted,
        }

    output_prefix = Path(args.output_prefix)
    output_md = output_prefix.with_suffix(".md")
    output_json = output_prefix.with_suffix(".json")
    write_report(
        output_md=output_md,
        output_json=output_json,
        live_scan=live_scan,
        replay_results=replay_results,
        metadata={
            "cities": cities,
            "start_day": start_day.isoformat(),
            "end_day": end_day.isoformat(),
            "scan_step_hours": args.scan_step_hours,
            "events_loaded": len(events),
        },
    )

    print(f"report_md={output_md}")
    print(f"report_json={output_json}")
    print(f"events_loaded={len(events)}")
    print(f"live_candidates={len(live_scan)}")
    for profile_name, data in replay_results.items():
        print(f"{profile_name}={json.dumps(data['summary'], separators=(',', ':'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
