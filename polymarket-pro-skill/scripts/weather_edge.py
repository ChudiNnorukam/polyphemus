#!/usr/bin/env python3
"""Weather Edge Bot — Exploit NOAA forecast vs Polymarket temperature mispricing.

Fetches NWS hourly forecast, compares to Polymarket temperature bucket prices,
and places maker orders when edge exceeds threshold. Zero API cost.

Usage:
    python3 weather_edge.py scan          # Show edges without trading
    python3 weather_edge.py trade         # Scan + place orders on edges
    python3 weather_edge.py history       # Show trade history
    python3 weather_edge.py pnl           # Show P&L summary
"""

import argparse
import json
import math
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta

import requests
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    OrderArgs,
    BalanceAllowanceParams,
    AssetType,
    OrderType,
)
from py_clob_client.order_builder.constants import BUY

# --- Config ---

CLOB_HOST = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
NWS_API = "https://api.weather.gov"
OPEN_METEO_API = "https://api.open-meteo.com/v1/forecast"
CHAIN_ID = 137

# On-chain redemption
POLYGON_RPC = "https://polygon-bor-rpc.publicnode.com"
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_ABI = [
    {"name": "redeemPositions", "type": "function", "stateMutability": "nonpayable",
     "inputs": [
         {"name": "collateralToken", "type": "address"},
         {"name": "parentCollectionId", "type": "bytes32"},
         {"name": "conditionId", "type": "bytes32"},
         {"name": "indexSets", "type": "uint256[]"},
     ], "outputs": []},
    {"name": "payoutDenominator", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "conditionId", "type": "bytes32"}],
     "outputs": [{"name": "", "type": "uint256"}]},
]

# City configs: (lat, lon, nws_grid_id, nws_grid_x, nws_grid_y, unit, slug_city)
CITIES = {
    "nyc": {
        "lat": 40.7128, "lon": -74.0060,
        "nws_grid": ("OKX", 33, 35),
        "unit": "F", "slug_city": "nyc",
        "label": "New York City",
    },
    "chicago": {
        "lat": 41.8781, "lon": -87.6298,
        "nws_grid": ("LOT", 75, 72),
        "unit": "F", "slug_city": "chicago",
        "label": "Chicago",
    },
    "dallas": {
        "lat": 32.7767, "lon": -96.7970,
        "nws_grid": ("FWD", 83, 108),
        "unit": "F", "slug_city": "dallas",
        "label": "Dallas",
    },
    "miami": {
        "lat": 25.7617, "lon": -80.1918,
        "nws_grid": ("MFL", 75, 53),
        "unit": "F", "slug_city": "miami",
        "label": "Miami",
    },
    "seattle": {
        "lat": 47.6062, "lon": -122.3321,
        "nws_grid": ("SEW", 124, 67),
        "unit": "F", "slug_city": "seattle",
        "label": "Seattle",
    },
    "atlanta": {
        "lat": 33.7490, "lon": -84.3880,
        "nws_grid": ("FFC", 50, 86),
        "unit": "F", "slug_city": "atlanta",
        "label": "Atlanta",
    },
    "london": {
        "lat": 51.5074, "lon": -0.1278,
        "nws_grid": None,  # No NWS — use Open-Meteo
        "unit": "C", "slug_city": "london",
        "label": "London",
    },
    "toronto": {
        "lat": 43.6532, "lon": -79.3832,
        "nws_grid": None,
        "unit": "C", "slug_city": "toronto",
        "label": "Toronto",
    },
    "seoul": {
        "lat": 37.5665, "lon": 126.9780,
        "nws_grid": None,
        "unit": "C", "slug_city": "seoul",
        "label": "Seoul",
    },
}

# Trading params — tiered edge thresholds
# High-prob buckets (near forecast center) need less edge but fill better
EDGE_THRESHOLD_HIGH = float(os.environ.get("WEATHER_EDGE_HIGH", "0.03"))   # forecast_prob >= 20%
EDGE_THRESHOLD_MED = float(os.environ.get("WEATHER_EDGE_MED", "0.05"))    # forecast_prob 10-20%
EDGE_THRESHOLD_LOW = float(os.environ.get("WEATHER_EDGE_LOW", "0.08"))    # forecast_prob < 10%
KELLY_FRACTION = float(os.environ.get("WEATHER_KELLY_FRACTION", "0.25"))
MAX_POSITION_PCT = float(os.environ.get("WEATHER_MAX_POSITION_PCT", "0.05"))
MAX_TOTAL_EXPOSURE_PCT = float(os.environ.get("WEATHER_MAX_EXPOSURE_PCT", "0.15"))  # 15% of bankroll max
MIN_ORDER_SIZE = 5  # Polymarket minimum
FORECAST_STDEV_F = 2.5  # Standard deviation in °F for NWS 1-day forecast
FORECAST_STDEV_C = 1.4  # Standard deviation in °C
MIN_VOLUME = float(os.environ.get("WEATHER_MIN_VOLUME", "500"))

# DB
DB_PATH = os.environ.get("WEATHER_DB_PATH",
    os.path.join(os.path.dirname(__file__), "weather_trades.db"))

# --- CLOB Client ---

def _load_env_file():
    """Load .env files if present (for VPS deployment).

    Loads ALL existing env files. Uses setdefault so first file wins for
    any given key (OpenClaw config overrides lagbot defaults).
    """
    for path in ["/opt/openclaw/.env", "/opt/lagbot/lagbot/.env", "/opt/weatherbot/.env"]:
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, val = line.partition("=")
                        os.environ.setdefault(key.strip(), val.strip())


def _env(polymarket_name, lagbot_name, default=""):
    """Get env var, supporting both POLYMARKET_ and lagbot naming."""
    return os.environ.get(polymarket_name) or os.environ.get(lagbot_name) or default


def get_client():
    _load_env_file()
    private_key = _env("POLYMARKET_PRIVATE_KEY", "PRIVATE_KEY")
    sig_type = int(_env("POLYMARKET_SIGNATURE_TYPE", "SIGNATURE_TYPE", "0"))
    if not private_key:
        print("ERROR: PRIVATE_KEY not set")
        sys.exit(1)
    client = ClobClient(CLOB_HOST, key=private_key, chain_id=CHAIN_ID,
                        signature_type=sig_type)
    client.set_api_creds(client.create_or_derive_api_creds())
    return client


def get_balance():
    client = get_client()
    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    result = client.get_balance_allowance(params)
    return float(result.get("balance", 0)) / 1e6


def get_wallet_address():
    _load_env_file()
    return _env("POLYMARKET_WALLET_ADDRESS", "WALLET_ADDRESS")


def redeem_position(condition_id: str) -> tuple:
    """Redeem a resolved CTF position on-chain via EOA direct tx.

    Returns (success: bool, tx_hash_or_error: str).
    """
    _load_env_file()
    private_key = _env("POLYMARKET_PRIVATE_KEY", "PRIVATE_KEY")
    if not private_key:
        return False, "no PRIVATE_KEY in env"

    rpc = os.environ.get("POLYGON_RPC_URL", POLYGON_RPC)
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 30}))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    wallet = w3.eth.account.from_key(private_key).address
    ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF_ADDRESS), abi=CTF_ABI)

    cid = condition_id[2:] if condition_id.startswith("0x") else condition_id
    cid_bytes = bytes.fromhex(cid)

    # Verify resolved on-chain before spending gas
    try:
        pd = ctf.functions.payoutDenominator(cid_bytes).call()
    except Exception as e:
        return False, f"rpc_error: {e}"
    if pd == 0:
        return False, "not_resolved_onchain_yet"

    try:
        gas_price = w3.eth.gas_price
        nonce = w3.eth.get_transaction_count(wallet)
        tx = ctf.functions.redeemPositions(
            Web3.to_checksum_address(USDC_E_ADDRESS),
            bytes(32),   # parentCollectionId = 0
            cid_bytes,
            [1, 2],      # both outcome indexSets
        ).build_transaction({
            "from": wallet,
            "nonce": nonce,
            "gas": 200000,
            "gasPrice": int(gas_price * 1.3),
            "chainId": 137,
        })
        signed = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
        if receipt.get("status") == 1:
            return True, tx_hash.hex()
        return False, f"tx_reverted: {tx_hash.hex()}"
    except Exception as e:
        return False, str(e)


# --- Database ---

def init_db():
    _load_env_file()  # must run before reading WEATHER_DB_PATH
    db_path = os.environ.get("WEATHER_DB_PATH", DB_PATH)
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        city TEXT NOT NULL,
        date TEXT NOT NULL,
        bucket TEXT NOT NULL,
        forecast_temp REAL NOT NULL,
        forecast_prob REAL NOT NULL,
        market_price REAL NOT NULL,
        edge REAL NOT NULL,
        kelly_size REAL NOT NULL,
        order_price REAL NOT NULL,
        shares REAL NOT NULL,
        cost REAL NOT NULL,
        token_id TEXT NOT NULL,
        order_id TEXT,
        status TEXT DEFAULT 'placed',
        pnl REAL DEFAULT 0,
        resolved_bucket TEXT
    )""")
    conn.commit()
    return conn


def record_trade(conn, city, date, bucket, forecast_temp, forecast_prob,
                 market_price, edge, kelly_size, order_price, shares, cost,
                 token_id, order_id):
    conn.execute("""INSERT INTO trades
        (ts, city, date, bucket, forecast_temp, forecast_prob, market_price,
         edge, kelly_size, order_price, shares, cost, token_id, order_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (datetime.now(timezone.utc).isoformat(), city, date, bucket,
         forecast_temp, forecast_prob, market_price, edge, kelly_size,
         order_price, shares, cost, token_id, order_id))
    conn.commit()


# --- Forecast ---

def get_nws_forecast_high(city_cfg, target_date):
    """Get NWS daily high forecast for a US city on target_date."""
    grid_id, grid_x, grid_y = city_cfg["nws_grid"]
    url = f"{NWS_API}/gridpoints/{grid_id}/{grid_x},{grid_y}/forecast"
    try:
        r = requests.get(url, headers={"User-Agent": "WeatherEdgeBot/1.0"},
                         timeout=15)
        r.raise_for_status()
        periods = r.json().get("properties", {}).get("periods", [])
        target_str = target_date.strftime("%Y-%m-%d")
        for p in periods:
            start = p.get("startTime", "")[:10]
            if start == target_str and p.get("isDaytime", False):
                return float(p["temperature"]), p["temperatureUnit"]
        # Fallback: check by name (e.g., "Tuesday")
        day_name = target_date.strftime("%A")
        for p in periods:
            if p.get("name", "").startswith(day_name) and p.get("isDaytime", False):
                return float(p["temperature"]), p["temperatureUnit"]
    except Exception as e:
        print(f"  NWS forecast error for {city_cfg['label']}: {e}")
    return None, None


def get_openmeteo_forecast_high(city_cfg, target_date):
    """Get Open-Meteo daily high forecast for any city."""
    target_str = target_date.strftime("%Y-%m-%d")
    unit = city_cfg["unit"]
    temp_unit = "fahrenheit" if unit == "F" else "celsius"
    try:
        r = requests.get(OPEN_METEO_API, params={
            "latitude": city_cfg["lat"],
            "longitude": city_cfg["lon"],
            "daily": "temperature_2m_max",
            "temperature_unit": temp_unit,
            "timezone": "auto",
            "forecast_days": "3",
        }, timeout=15)
        r.raise_for_status()
        data = r.json()
        daily = data.get("daily", {})
        dates = daily.get("time", [])
        temps = daily.get("temperature_2m_max", [])
        for i, d in enumerate(dates):
            if d == target_str:
                return float(temps[i]), unit
    except Exception as e:
        print(f"  Open-Meteo forecast error for {city_cfg['label']}: {e}")
    return None, None


def get_forecast_high(city_key, target_date):
    """Get high temp forecast, preferring NWS for US cities."""
    cfg = CITIES[city_key]
    if cfg["nws_grid"]:
        temp, unit = get_nws_forecast_high(cfg, target_date)
        if temp is not None:
            return temp, unit
    # Fallback or non-US: Open-Meteo
    return get_openmeteo_forecast_high(cfg, target_date)


# --- Probability Calculation ---

def normal_cdf(x, mu, sigma):
    """Standard normal CDF using error function."""
    return 0.5 * (1 + math.erf((x - mu) / (sigma * math.sqrt(2))))


def bucket_probability(low, high, forecast_temp, stdev):
    """Probability that actual temp falls in [low, high] given forecast."""
    if low is None:  # "X or below"
        return normal_cdf(high + 0.5, forecast_temp, stdev)
    if high is None:  # "X or higher"
        return 1 - normal_cdf(low - 0.5, forecast_temp, stdev)
    return normal_cdf(high + 0.5, forecast_temp, stdev) - \
           normal_cdf(low - 0.5, forecast_temp, stdev)


def parse_bucket_range(question, unit):
    """Parse temperature range from market question.

    Examples:
        "...be 41°F or below..." → (None, 41)
        "...be between 42-43°F..." → (42, 43)
        "...be 56°F or higher..." → (56, None)
        "...be 7°C on..." → (7, 7)  # single degree C
    """
    q = question.lower()
    deg = "°f" if unit == "F" else "°c"

    # "X or below" / "X or less"
    m = re.search(r'(\d+)' + re.escape(deg) + r'\s+or\s+(below|less)', q)
    if m:
        return None, int(m.group(1))

    # "X or higher" / "X or above" / "X or more"
    m = re.search(r'(\d+)' + re.escape(deg) + r'\s+or\s+(higher|above|more)', q)
    if m:
        return int(m.group(1)), None

    # "between X-Y°F"
    m = re.search(r'between\s+(-?\d+)\s*[-–]\s*(-?\d+)', q)
    if m:
        return int(m.group(1)), int(m.group(2))

    # Single degree: "be X°C on" (common for Celsius markets)
    m = re.search(r'be\s+(-?\d+)' + re.escape(deg), q)
    if m:
        val = int(m.group(1))
        return val, val

    return None, None


# --- Market Discovery ---

def discover_weather_markets(target_date):
    """Find all active weather temperature markets for target_date."""
    month_name = target_date.strftime("%B").lower()
    day = target_date.day
    year = target_date.year

    found = []
    for city_key, cfg in CITIES.items():
        slug = f"highest-temperature-in-{cfg['slug_city']}-on-{month_name}-{day}-{year}"
        try:
            r = requests.get(f"{GAMMA_API}/events", params={
                "slug": slug, "limit": "1",
            }, timeout=20)
            events = r.json()
            if not events:
                continue

            event = events[0]
            markets = event.get("markets", [])
            if not markets:
                continue

            found.append({
                "city": city_key,
                "label": cfg["label"],
                "unit": cfg["unit"],
                "date": target_date.strftime("%Y-%m-%d"),
                "event_slug": slug,
                "markets": markets,
            })
        except Exception:
            continue

    return found


# --- Edge Calculation ---

def calculate_edges(weather_events, forecasts):
    """Calculate edge for each bucket across all cities."""
    edges = []

    for event in weather_events:
        city = event["city"]
        unit = event["unit"]
        forecast_temp = forecasts.get(city)
        if forecast_temp is None:
            continue

        stdev = FORECAST_STDEV_F if unit == "F" else FORECAST_STDEV_C

        for market in event["markets"]:
            question = market.get("question", "")
            prices = market.get("outcomePrices", "[]")
            if isinstance(prices, str):
                prices = json.loads(prices)
            tokens = market.get("clobTokenIds", "[]")
            if isinstance(tokens, str):
                tokens = json.loads(tokens)

            if not prices or not tokens:
                continue

            yes_price = float(prices[0])
            if yes_price <= 0.01 or yes_price >= 0.99:
                continue  # Skip near-certain or near-impossible

            volume = float(market.get("volume", 0) or 0)
            if volume < MIN_VOLUME:
                continue

            low, high = parse_bucket_range(question, unit)
            if low is None and high is None:
                continue

            forecast_prob = bucket_probability(low, high, forecast_temp, stdev)
            edge = forecast_prob - yes_price

            # Format bucket label
            if low is None:
                bucket_label = f"<={high}°{unit}"
            elif high is None:
                bucket_label = f">={low}°{unit}"
            elif low == high:
                bucket_label = f"{low}°{unit}"
            else:
                bucket_label = f"{low}-{high}°{unit}"

            # Tiered threshold: high-prob buckets need less edge
            if forecast_prob >= 0.20:
                threshold = EDGE_THRESHOLD_HIGH  # 3%
            elif forecast_prob >= 0.10:
                threshold = EDGE_THRESHOLD_MED   # 5%
            else:
                threshold = EDGE_THRESHOLD_LOW   # 8%

            edges.append({
                "city": city,
                "label": event["label"],
                "date": event["date"],
                "bucket": bucket_label,
                "question": question,
                "forecast_temp": forecast_temp,
                "forecast_prob": forecast_prob,
                "market_price": yes_price,
                "edge": edge,
                "threshold": threshold,
                "tradeable": edge >= threshold,
                "volume": volume,
                "token_id": tokens[0],  # YES token
                "slug": market.get("slug", ""),
            })

    # Sort by edge descending
    edges.sort(key=lambda x: x["edge"], reverse=True)
    return edges


# --- Position Sizing ---

def kelly_size(edge, market_price, bankroll):
    """Quarter-Kelly position sizing."""
    if edge <= 0 or market_price <= 0 or market_price >= 1:
        return 0
    # Kelly: f = (p*b - q) / b where b = (1/price - 1), p = forecast_prob, q = 1-p
    # Simplified: f = edge / (1 - market_price)
    kelly_f = edge / (1 - market_price)
    quarter_kelly = kelly_f * KELLY_FRACTION
    max_bet = bankroll * MAX_POSITION_PCT
    bet = min(bankroll * quarter_kelly, max_bet)
    shares = bet / market_price
    return max(0, shares)


# --- Commands ---

def cmd_scan(args):
    """Scan for weather edges without trading."""
    today = datetime.now(timezone.utc).date()
    # Check today and tomorrow
    dates_to_check = [today, today + timedelta(days=1)]
    if args.date:
        dates_to_check = [datetime.strptime(args.date, "%Y-%m-%d").date()]

    cities = args.cities.split(",") if args.cities else list(CITIES.keys())

    for target_date in dates_to_check:
        print(f"\n{'='*60}")
        print(f"  Scanning: {target_date.strftime('%A, %B %d, %Y')}")
        print(f"{'='*60}")

        # Discover markets
        events = discover_weather_markets(target_date)
        # Filter to requested cities
        events = [e for e in events if e["city"] in cities]
        if not events:
            print("  No active temperature markets found.")
            continue

        print(f"  Found {len(events)} cities with active markets\n")

        # Get forecasts
        forecasts = {}
        for event in events:
            temp, unit = get_forecast_high(event["city"], target_date)
            if temp is not None:
                forecasts[event["city"]] = temp
                print(f"  {event['label']:>15}: forecast high = {temp}°{unit}")
            else:
                print(f"  {event['label']:>15}: NO FORECAST AVAILABLE")

        if not forecasts:
            print("  No forecasts available.")
            continue

        # Calculate edges
        edges = calculate_edges(events, forecasts)
        print(f"\n  {'Bucket':>15}  {'City':>10}  {'Forecast':>8}  {'Mkt$':>6}  {'Edge':>7}  {'Thr':>5}  {'Signal':>8}")
        print(f"  {'-'*15}  {'-'*10}  {'-'*8}  {'-'*6}  {'-'*7}  {'-'*5}  {'-'*8}")

        tradeable = 0
        for e in edges:
            signal = ">>> BUY" if e["tradeable"] else ""
            if e["tradeable"]:
                tradeable += 1
            if e["edge"] >= 0.02 or e["tradeable"]:
                print(f"  {e['bucket']:>15}  {e['city']:>10}  "
                      f"{e['forecast_prob']:>7.1%}  "
                      f"${e['market_price']:>.3f}  "
                      f"{e['edge']:>+6.1%}  "
                      f"{e['threshold']:>4.0%}  {signal}")

        print(f"\n  Tradeable signals: {tradeable}")


def cmd_trade(args):
    """Scan and place orders on edges above threshold."""
    today = datetime.now(timezone.utc).date()
    dates_to_check = [today, today + timedelta(days=1)]
    if args.date:
        dates_to_check = [datetime.strptime(args.date, "%Y-%m-%d").date()]

    cities = args.cities.split(",") if args.cities else list(CITIES.keys())
    dry_run = args.dry_run

    bankroll = get_balance()
    print(f"  Bankroll: ${bankroll:,.2f}")
    if bankroll < 10:
        print("  ERROR: Bankroll too low to trade (< $10)")
        return

    conn = init_db()
    client = get_client()
    trades_placed = 0
    total_exposure = 0.0
    max_exposure = bankroll * MAX_TOTAL_EXPOSURE_PCT
    # Count existing open order exposure
    existing_cost = conn.execute(
        "SELECT COALESCE(SUM(cost), 0) FROM trades WHERE status='placed'"
    ).fetchone()[0]
    total_exposure = float(existing_cost)
    print(f"  Existing exposure: ${total_exposure:,.2f} / ${max_exposure:,.2f} max ({MAX_TOTAL_EXPOSURE_PCT:.0%})")

    for target_date in dates_to_check:
        print(f"\n{'='*60}")
        print(f"  Trading: {target_date.strftime('%A, %B %d, %Y')}")
        print(f"{'='*60}")

        events = discover_weather_markets(target_date)
        events = [e for e in events if e["city"] in cities]
        if not events:
            print("  No markets found.")
            continue

        forecasts = {}
        for event in events:
            temp, unit = get_forecast_high(event["city"], target_date)
            if temp is not None:
                forecasts[event["city"]] = temp
                print(f"  {event['label']:>15}: forecast = {temp}°{unit}")

        edges = calculate_edges(events, forecasts)

        # Dedup: skip buckets we already have open orders on today
        existing = set()
        try:
            existing = {
                (r[0], r[1], r[2]) for r in
                conn.execute("SELECT city, date, bucket FROM trades WHERE status='placed'").fetchall()
            }
        except Exception:
            pass

        for e in edges:
            if not e["tradeable"]:
                continue

            dedup_key = (e["city"], e["date"], e["bucket"])
            if dedup_key in existing:
                print(f"  SKIP {e['bucket']} ({e['city']}): already have open order")
                continue

            # Check total exposure cap
            if total_exposure >= max_exposure:
                print(f"  STOP: Max exposure reached (${total_exposure:,.2f} >= ${max_exposure:,.2f})")
                break

            shares = kelly_size(e["edge"], e["market_price"], bankroll)
            if shares < MIN_ORDER_SIZE:
                print(f"  SKIP {e['bucket']} ({e['city']}): kelly size {shares:.1f} < {MIN_ORDER_SIZE} min")
                continue

            shares = round(shares)
            # Smart maker pricing: bid closer to market on high-prob buckets
            # High-prob (>20%): offset $0.002 — tight spread, fill quickly
            # Mid-prob (10-20%): offset $0.005
            # Low-prob (<10%): offset $0.005 (already cheap, don't go lower)
            if e["forecast_prob"] >= 0.20:
                offset = 0.002
            else:
                offset = 0.005
            order_price = round(e["market_price"] - offset, 3)
            order_price = max(0.01, min(0.99, order_price))
            cost = shares * order_price

            print(f"\n  >>> TRADE: {e['city']} {e['bucket']} on {e['date']}")
            print(f"      Forecast: {e['forecast_prob']:.1%} vs Market: ${e['market_price']:.3f} = {e['edge']:+.1%} edge")
            print(f"      Order: BUY {shares} shares @ ${order_price:.3f} (${cost:.2f})")

            order_id = None
            if not dry_run:
                try:
                    order_args = OrderArgs(
                        token_id=e["token_id"],
                        price=order_price,
                        size=shares,
                        side=BUY,
                    )
                    signed = client.create_order(order_args)
                    result = client.post_order(signed, OrderType.GTC, post_only=True)
                    order_id = result.get("orderID", "")
                    status = result.get("status", "UNKNOWN")
                    print(f"      Placed: {order_id} [{status}]")
                    trades_placed += 1
                except Exception as ex:
                    print(f"      ORDER FAILED: {ex}")
                    continue
            else:
                print(f"      [DRY RUN — not placed]")
                trades_placed += 1

            record_trade(conn, e["city"], e["date"], e["bucket"],
                         e["forecast_temp"], e["forecast_prob"],
                         e["market_price"], e["edge"],
                         shares * order_price, order_price, shares, cost,
                         e["token_id"], order_id or "DRY_RUN")
            total_exposure += cost
            existing.add(dedup_key)

    conn.close()
    mode = "DRY RUN" if dry_run else "LIVE"
    print(f"\n  Done. {trades_placed} trades placed [{mode}].")


def cmd_history(args):
    """Show trade history."""
    conn = init_db()
    limit = args.limit or 20
    rows = conn.execute(
        "SELECT ts, city, date, bucket, forecast_temp, forecast_prob, "
        "market_price, edge, shares, order_price, cost, status, pnl "
        "FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()

    if not rows:
        print("No trades recorded.")
        return

    print(f"\n  {'Time':>19}  {'City':>8}  {'Bucket':>12}  {'Fcst':>5}  {'FP%':>5}  "
          f"{'Mkt$':>5}  {'Edge':>6}  {'Shares':>6}  {'Cost':>7}  {'Status':>8}  {'PnL':>7}")
    print(f"  {'-'*19}  {'-'*8}  {'-'*12}  {'-'*5}  {'-'*5}  "
          f"{'-'*5}  {'-'*6}  {'-'*6}  {'-'*7}  {'-'*8}  {'-'*7}")

    for r in rows:
        ts, city, date, bucket, ft, fp, mp, edge, sh, op, cost, status, pnl = r
        print(f"  {ts[:19]}  {city:>8}  {bucket:>12}  {ft:>5.0f}  {fp:>4.0%}  "
              f"${mp:>.3f}  {edge:>+5.1%}  {sh:>6.0f}  ${cost:>6.2f}  {status:>8}  ${pnl:>6.2f}")


def cmd_pnl(args):
    """Show P&L summary."""
    conn = init_db()
    rows = conn.execute(
        "SELECT COUNT(*), SUM(cost), SUM(pnl), "
        "SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN status = 'lost' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN status = 'placed' THEN 1 ELSE 0 END), "
        "AVG(edge) "
        "FROM trades").fetchone()
    conn.close()

    total, cost, pnl, winners, won, lost, pending, avg_edge = rows
    if not total:
        print("No trades recorded.")
        return

    cost = cost or 0
    pnl = pnl or 0
    won = won or 0
    lost = lost or 0
    pending = pending or 0
    resolved = won + lost

    print(f"\n  === Weather Edge P&L ===")
    print(f"  Total trades: {total}")
    print(f"  Total invested: ${cost:,.2f}")
    print(f"  Resolved: {resolved} (Won: {won}, Lost: {lost})")
    print(f"  Pending: {pending}")
    if resolved > 0:
        wr = won / resolved * 100
        print(f"  Win rate: {wr:.1f}%")
    print(f"  Total PnL: ${pnl:,.2f}")
    print(f"  Avg edge at entry: {(avg_edge or 0):+.1%}")


def cmd_resolve(args):
    """Check and resolve completed trades (mark won/lost) and redeem winners on-chain."""
    conn = init_db()

    # First pass: redeem any already-marked-won positions that weren't redeemed
    unredeemed = conn.execute(
        "SELECT id, city, date, bucket, token_id, order_price, shares "
        "FROM trades WHERE status = 'won'").fetchall()
    if unredeemed:
        print(f"  Found {len(unredeemed)} won-but-unredeemed positions — redeeming now...\n")
        for row in unredeemed:
            trade_id, city, date_str, bucket, token_id, order_price, shares = row
            # Re-fetch conditionId from Gamma
            trade_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            cfg = CITIES.get(city)
            if not cfg:
                continue
            month_name = trade_date.strftime("%B").lower()
            event_slug = f"highest-temperature-in-{cfg['slug_city']}-on-{month_name}-{trade_date.day}-{trade_date.year}"
            try:
                r = requests.get(f"{GAMMA_API}/events", params={"slug": event_slug, "limit": "1"}, timeout=15)
                events_data = r.json()
                if not events_data:
                    continue
                for market in events_data[0].get("markets", []):
                    tokens = market.get("clobTokenIds", "[]")
                    if isinstance(tokens, str):
                        tokens = json.loads(tokens)
                    if tokens and tokens[0] == token_id:
                        condition_id = market.get("conditionId", "")
                        if condition_id:
                            print(f"  Redeeming: {city} {bucket} on {date_str}...")
                            ok, result = redeem_position(condition_id)
                            if ok:
                                conn.execute("UPDATE trades SET status='redeemed' WHERE id=?", (trade_id,))
                                conn.commit()
                                print(f"  Redeemed: tx={result[:20]}...")
                            else:
                                print(f"  Failed: {result}")
                        break
            except Exception as e:
                print(f"  Error re-fetching {city} {date_str}: {e}")

    pending = conn.execute(
        "SELECT id, city, date, bucket, token_id, order_price, shares "
        "FROM trades WHERE status = 'placed'").fetchall()

    if not pending:
        print("No pending trades to resolve.")
        conn.close()
        return

    print(f"  Checking {len(pending)} pending trades...\n")
    resolved = 0

    for row in pending:
        trade_id, city, date_str, bucket, token_id, order_price, shares = row

        # Check if market resolved by looking at the event
        trade_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        if trade_date >= datetime.now(timezone.utc).date():
            continue  # Not yet resolved

        cfg = CITIES.get(city)
        if not cfg:
            continue

        month_name = trade_date.strftime("%B").lower()
        event_slug = f"highest-temperature-in-{cfg['slug_city']}-on-{month_name}-{trade_date.day}-{trade_date.year}"

        try:
            r = requests.get(f"{GAMMA_API}/events", params={
                "slug": event_slug, "limit": "1",
            }, timeout=15)
            events = r.json()
            if not events:
                continue

            event = events[0]
            for market in event.get("markets", []):
                tokens = market.get("clobTokenIds", "[]")
                if isinstance(tokens, str):
                    tokens = json.loads(tokens)
                if not tokens or tokens[0] != token_id:
                    continue

                # Check if resolved
                if not market.get("closed"):
                    continue

                prices = market.get("outcomePrices", "[]")
                if isinstance(prices, str):
                    prices = json.loads(prices)
                yes_final = float(prices[0]) if prices else 0

                if yes_final >= 0.95:  # Won
                    pnl = shares * (1.0 - order_price)  # Payout $1 minus cost
                    conn.execute("UPDATE trades SET status='won', pnl=? WHERE id=?",
                                 (pnl, trade_id))
                    conn.commit()
                    print(f"  WON: {city} {bucket} on {date_str} | PnL: +${pnl:.2f}")

                    # On-chain redemption
                    condition_id = market.get("conditionId", "")
                    if condition_id:
                        print(f"  Redeeming on-chain (conditionId={condition_id[:16]}...)...")
                        ok, result = redeem_position(condition_id)
                        if ok:
                            conn.execute("UPDATE trades SET status='redeemed' WHERE id=?",
                                         (trade_id,))
                            conn.commit()
                            print(f"  Redeemed: tx={result[:20]}...")
                        else:
                            print(f"  Redeem failed: {result} (still marked won, retry next run)")
                    else:
                        print(f"  WARNING: no conditionId in market data — manual redeem needed")
                else:  # Lost
                    pnl = -(shares * order_price)
                    conn.execute("UPDATE trades SET status='lost', pnl=? WHERE id=?",
                                 (pnl, trade_id))
                    print(f"  LOST: {city} {bucket} on {date_str} | PnL: -${abs(pnl):.2f}")
                resolved += 1
                break
        except Exception as e:
            print(f"  Error resolving {city} {date_str}: {e}")

    conn.commit()
    conn.close()
    print(f"\n  Resolved {resolved} trades.")


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="Weather Edge Bot")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("scan", help="Scan for weather edges")
    p.add_argument("--date", help="Target date YYYY-MM-DD (default: today+tomorrow)")
    p.add_argument("--cities", help="Comma-separated city keys (default: all)")

    p = sub.add_parser("trade", help="Scan and place orders")
    p.add_argument("--date", help="Target date YYYY-MM-DD")
    p.add_argument("--cities", help="Comma-separated city keys")
    p.add_argument("--dry-run", action="store_true", help="Don't place real orders")

    p = sub.add_parser("history", help="Trade history")
    p.add_argument("--limit", type=int, default=20)

    sub.add_parser("pnl", help="P&L summary")
    sub.add_parser("resolve", help="Resolve completed trades")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    cmds = {
        "scan": cmd_scan,
        "trade": cmd_trade,
        "history": cmd_history,
        "pnl": cmd_pnl,
        "resolve": cmd_resolve,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
