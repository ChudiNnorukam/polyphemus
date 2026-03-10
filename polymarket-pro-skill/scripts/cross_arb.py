#!/usr/bin/env python3
"""Cross-Platform Arb — Polymarket vs Kalshi price discrepancy arbitrage.

Scans both platforms for overlapping markets, identifies price spreads
exceeding the fee threshold, and places hedged orders on both sides.

The arb: Buy YES on cheap platform + Buy NO on expensive platform.
If YES_cheap + NO_expensive < $1.00 - fees → guaranteed profit.

Usage:
    python3 cross_arb.py scan          # Show arb opportunities
    python3 cross_arb.py trade         # Scan + place orders
    python3 cross_arb.py history       # Trade history
    python3 cross_arb.py pnl           # P&L summary
    python3 cross_arb.py balance       # Show balances on both platforms
"""

import argparse
import base64
import json
import math
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# Polymarket imports
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    OrderArgs, BalanceAllowanceParams, AssetType, OrderType,
)
from py_clob_client.order_builder.constants import BUY

# --- Config ---

KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137

# Arb params
MIN_GROSS_SPREAD = float(os.environ.get("ARB_MIN_SPREAD", "0.03"))  # 3% minimum
KALSHI_FEE_FACTOR = 0.07  # Kalshi fee formula constant
POLY_TAKER_FEE = 0.001    # 0.10% Polymarket taker
MIN_SIMILARITY = float(os.environ.get("ARB_MIN_SIMILARITY", "0.75"))
MAX_POSITION_USD = float(os.environ.get("ARB_MAX_POSITION", "50"))
MAX_TOTAL_EXPOSURE = float(os.environ.get("ARB_MAX_EXPOSURE", "200"))
MIN_VOLUME_POLY = float(os.environ.get("ARB_MIN_VOL_POLY", "5000"))
MIN_VOLUME_KALSHI = float(os.environ.get("ARB_MIN_VOL_KALSHI", "100"))

# Scan params
POLY_SCAN_PAGES = int(os.environ.get("ARB_POLY_PAGES", "5"))
KALSHI_SCAN_LIMIT = int(os.environ.get("ARB_KALSHI_LIMIT", "200"))

DB_PATH = os.environ.get("ARB_DB_PATH",
    os.path.join(os.path.dirname(__file__), "arb_trades.db"))


# --- Env Loading ---

def _load_env_file():
    for path in ["/opt/openclaw/.env", "/opt/lagbot/lagbot/.env"]:
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, val = line.partition("=")
                        os.environ.setdefault(key.strip(), val.strip())


# --- Kalshi Client ---

class KalshiClient:
    def __init__(self):
        _load_env_file()
        self.key_id = os.environ.get("KALSHI_API_KEY_ID", "")
        key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")
        self.private_key = None

        if key_path and os.path.exists(key_path):
            with open(key_path, "rb") as f:
                self.private_key = serialization.load_pem_private_key(
                    f.read(), password=None
                )
        elif os.environ.get("KALSHI_PRIVATE_KEY"):
            # Key stored as env var (base64-encoded PEM)
            pem_data = base64.b64decode(os.environ["KALSHI_PRIVATE_KEY"])
            self.private_key = serialization.load_pem_private_key(
                pem_data, password=None
            )

        if not self.key_id or not self.private_key:
            print("  WARNING: Kalshi API keys not configured")
            print("  Set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH in .env")

    def _sign(self, method, path):
        timestamp = str(int(time.time() * 1000))
        # Strip query params before signing
        sign_path = path.split("?")[0]
        message = f"{timestamp}{method}{sign_path}"
        signature = self.private_key.sign(
            message.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
            "Content-Type": "application/json",
        }

    def _get(self, path, params=None):
        headers = self._sign("GET", path)
        r = requests.get(f"{KALSHI_API_BASE}{path}", headers=headers,
                         params=params, timeout=20)
        r.raise_for_status()
        return r.json()

    def _post(self, path, body):
        headers = self._sign("POST", path)
        r = requests.post(f"{KALSHI_API_BASE}{path}", headers=headers,
                          json=body, timeout=20)
        r.raise_for_status()
        return r.json()

    def get_markets(self, limit=200, cursor=None):
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self._get("/markets", params)

    def get_events(self, limit=200, cursor=None):
        params = {"limit": limit, "with_nested_markets": "true"}
        if cursor:
            params["cursor"] = cursor
        return self._get("/events", params)

    def get_market(self, ticker):
        return self._get(f"/markets/{ticker}")

    def get_orderbook(self, ticker):
        return self._get(f"/markets/{ticker}/orderbook")

    def get_balance(self):
        data = self._get("/portfolio/balance")
        return float(data.get("balance", 0)) / 100  # cents to dollars

    def place_order(self, ticker, side, action, price, count, post_only=True):
        body = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "type": "limit",
            "count": count,
            "time_in_force": "good_till_canceled",
            "post_only": post_only,
            "client_order_id": f"arb-{ticker}-{int(time.time())}",
        }
        if side == "yes":
            body["yes_price_cents"] = int(price * 100)
        else:
            body["no_price_cents"] = int(price * 100)
        return self._post("/portfolio/orders", body)


# --- Polymarket Client ---

def get_poly_client():
    _load_env_file()
    pk = os.environ.get("POLYMARKET_PRIVATE_KEY") or os.environ.get("PRIVATE_KEY")
    sig_type = int(os.environ.get("POLYMARKET_SIGNATURE_TYPE",
                   os.environ.get("SIGNATURE_TYPE", "0")))
    if not pk:
        print("ERROR: PRIVATE_KEY not set")
        sys.exit(1)
    client = ClobClient(CLOB_HOST, key=pk, chain_id=CHAIN_ID,
                        signature_type=sig_type)
    client.set_api_creds(client.create_or_derive_api_creds())
    return client


def get_poly_balance():
    client = get_poly_client()
    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    result = client.get_balance_allowance(params)
    return float(result.get("balance", 0)) / 1e6


# --- Market Matching ---

def normalize_text(text):
    """Normalize market title for fuzzy matching."""
    text = text.lower().strip()
    # Remove common filler words
    for word in ["will", "the", "be", "a", "an", "in", "on", "by", "of",
                 "before", "after", "during"]:
        text = re.sub(rf'\b{word}\b', '', text)
    # Remove dates, years
    text = re.sub(r'\b20\d{2}\b', '', text)
    # Remove punctuation
    text = re.sub(r'[^\w\s]', '', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def similarity(a, b):
    """Calculate similarity score between two market titles."""
    na, nb = normalize_text(a), normalize_text(b)
    return SequenceMatcher(None, na, nb).ratio()


def match_markets(poly_markets, kalshi_markets):
    """Find matching market pairs across platforms."""
    matches = []
    for pm in poly_markets:
        best_match = None
        best_score = 0
        for km in kalshi_markets:
            score = similarity(pm["question"], km["title"])
            if score > best_score:
                best_score = score
                best_match = km
        if best_score >= MIN_SIMILARITY and best_match:
            matches.append({
                "poly": pm,
                "kalshi": best_match,
                "similarity": best_score,
            })
    return matches


# --- Fee Calculation ---

def kalshi_fee(price, count):
    """Calculate Kalshi fee: 0.07 × C × P × (1-P)."""
    return KALSHI_FEE_FACTOR * count * price * (1 - price)


def poly_taker_fee(price, count):
    """Polymarket taker fee: 0.10% of notional."""
    return POLY_TAKER_FEE * price * count


def total_fees(poly_price, kalshi_price, count):
    """Total fees for one arb cycle (buy on both platforms)."""
    return poly_taker_fee(poly_price, count) + kalshi_fee(kalshi_price, count)


# --- Market Discovery ---

def fetch_poly_markets():
    """Fetch active Polymarket markets."""
    all_markets = []
    seen = set()
    for offset in range(0, POLY_SCAN_PAGES * 100, 100):
        try:
            r = requests.get(f"{GAMMA_API}/markets", params={
                "closed": "false", "active": "true",
                "limit": "100", "offset": str(offset),
            }, timeout=20)
            r.raise_for_status()
            markets = r.json()
        except Exception as e:
            print(f"  Poly API error: {e}")
            break
        if not markets:
            break
        for m in markets:
            slug = m.get("slug", "")
            if slug in seen:
                continue
            seen.add(slug)
            prices = m.get("outcomePrices", "[]")
            if isinstance(prices, str):
                prices = json.loads(prices)
            tokens = m.get("clobTokenIds", "[]")
            if isinstance(tokens, str):
                tokens = json.loads(tokens)
            if not prices or len(prices) < 2 or not tokens or len(tokens) < 2:
                continue
            vol = float(m.get("volume", 0) or 0)
            if vol < MIN_VOLUME_POLY:
                continue
            all_markets.append({
                "question": m.get("question", ""),
                "slug": slug,
                "yes_price": float(prices[0]),
                "no_price": float(prices[1]),
                "yes_token": tokens[0],
                "no_token": tokens[1],
                "volume": vol,
                "end_date": m.get("endDate", ""),
            })
        time.sleep(0.2)
    return all_markets


def fetch_kalshi_markets(kalshi_client):
    """Fetch active Kalshi markets via events endpoint (better coverage)."""
    all_markets = []
    seen = set()
    cursor = None
    for _ in range(20):  # max 20 pages of events
        try:
            data = kalshi_client.get_events(limit=200, cursor=cursor)
        except Exception as e:
            print(f"  Kalshi API error: {e}")
            break
        events = data.get("events", [])
        if not events:
            break
        for event in events:
            for m in event.get("markets", []):
                ticker = m.get("ticker", "")
                if ticker in seen:
                    continue
                seen.add(ticker)
                yb = m.get("yes_bid", 0) or 0
                # Skip markets with no bids (illiquid)
                if yb <= 0 or yb >= 100:
                    continue
                vol = m.get("volume", 0) or 0
                if vol < MIN_VOLUME_KALSHI:
                    continue
                # Kalshi prices are in cents (0-100)
                all_markets.append({
                    "ticker": ticker,
                    "title": m.get("title", ""),
                    "yes_bid": yb / 100,
                    "yes_ask": (m.get("yes_ask", 0) or 0) / 100,
                    "no_bid": (m.get("no_bid", 0) or 0) / 100,
                    "no_ask": (m.get("no_ask", 0) or 0) / 100,
                    "volume": vol,
                    "expiration": m.get("expiration_time", ""),
                })
        cursor = data.get("cursor")
        if not cursor:
            break
        time.sleep(0.3)
    return all_markets


# --- Arb Detection ---

def find_arbs(matched_pairs):
    """Find arbitrage opportunities in matched market pairs.

    Two arb directions:
    A) Buy YES on Poly + Buy NO on Kalshi → profit if YES_poly + NO_kalshi < 1
    B) Buy NO on Poly + Buy YES on Kalshi → profit if NO_poly + YES_kalshi < 1
    """
    arbs = []
    for pair in matched_pairs:
        pm = pair["poly"]
        km = pair["kalshi"]
        sim = pair["similarity"]

        # Direction A: Poly YES + Kalshi NO
        cost_a = pm["yes_price"] + km["no_ask"]
        if km["no_ask"] > 0 and cost_a < 1.0:
            gross_spread_a = 1.0 - cost_a
            fees_a = total_fees(pm["yes_price"], km["no_ask"], 1)
            net_spread_a = gross_spread_a - fees_a
            if net_spread_a > 0 and gross_spread_a >= MIN_GROSS_SPREAD:
                arbs.append({
                    "poly_question": pm["question"][:80],
                    "kalshi_title": km["title"][:80],
                    "similarity": sim,
                    "direction": "poly_YES + kalshi_NO",
                    "poly_side": "YES",
                    "poly_price": pm["yes_price"],
                    "poly_token": pm["yes_token"],
                    "poly_slug": pm["slug"],
                    "kalshi_side": "no",
                    "kalshi_price": km["no_ask"],
                    "kalshi_ticker": km["ticker"],
                    "cost": cost_a,
                    "gross_spread": gross_spread_a,
                    "fees": fees_a,
                    "net_spread": net_spread_a,
                    "poly_volume": pm["volume"],
                    "kalshi_volume": km["volume"],
                })

        # Direction B: Poly NO + Kalshi YES
        cost_b = pm["no_price"] + km["yes_ask"]
        if km["yes_ask"] > 0 and cost_b < 1.0:
            gross_spread_b = 1.0 - cost_b
            fees_b = total_fees(pm["no_price"], km["yes_ask"], 1)
            net_spread_b = gross_spread_b - fees_b
            if net_spread_b > 0 and gross_spread_b >= MIN_GROSS_SPREAD:
                arbs.append({
                    "poly_question": pm["question"][:80],
                    "kalshi_title": km["title"][:80],
                    "similarity": sim,
                    "direction": "poly_NO + kalshi_YES",
                    "poly_side": "NO",
                    "poly_price": pm["no_price"],
                    "poly_token": pm["no_token"],
                    "poly_slug": pm["slug"],
                    "kalshi_side": "yes",
                    "kalshi_price": km["yes_ask"],
                    "kalshi_ticker": km["ticker"],
                    "cost": cost_b,
                    "gross_spread": gross_spread_b,
                    "fees": fees_b,
                    "net_spread": net_spread_b,
                    "poly_volume": pm["volume"],
                    "kalshi_volume": km["volume"],
                })

    arbs.sort(key=lambda x: x["net_spread"], reverse=True)
    return arbs


# --- Database ---

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS arb_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        poly_question TEXT, kalshi_title TEXT,
        direction TEXT, similarity REAL,
        poly_price REAL, kalshi_price REAL,
        cost REAL, gross_spread REAL, net_spread REAL, fees REAL,
        shares INT, total_cost REAL, expected_profit REAL,
        poly_order_id TEXT, kalshi_order_id TEXT,
        poly_slug TEXT, kalshi_ticker TEXT,
        status TEXT DEFAULT 'placed',
        pnl REAL DEFAULT 0
    )""")
    conn.commit()
    return conn


# --- Commands ---

def cmd_scan(args):
    """Scan for cross-platform arb opportunities."""
    print("\n  Cross-Platform Arb Scanner")
    print(f"  Min spread: {MIN_GROSS_SPREAD:.0%} | Min similarity: {MIN_SIMILARITY:.0%}")

    kalshi = KalshiClient()
    if not kalshi.private_key:
        print("  ERROR: Kalshi API not configured. Set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH")
        return

    print("\n  Fetching Polymarket markets...")
    poly_markets = fetch_poly_markets()
    print(f"  Found {len(poly_markets)} Polymarket markets")

    print("  Fetching Kalshi markets...")
    kalshi_markets = fetch_kalshi_markets(kalshi)
    print(f"  Found {len(kalshi_markets)} Kalshi markets")

    print(f"\n  Matching markets (similarity >= {MIN_SIMILARITY:.0%})...")
    matched = match_markets(poly_markets, kalshi_markets)
    print(f"  Matched {len(matched)} pairs")

    if not matched:
        print("  No matching markets found.")
        return

    print(f"\n  Checking for arb spreads >= {MIN_GROSS_SPREAD:.0%}...")
    arbs = find_arbs(matched)
    print(f"  Found {len(arbs)} arb opportunities\n")

    if not arbs:
        print("  No arb opportunities above threshold.")
        # Show best near-misses
        all_arbs = find_arbs(matched)
        if not all_arbs:
            # Show top matches even without arbs
            print("\n  Top matched pairs (no arb):")
            for m in sorted(matched, key=lambda x: x["similarity"], reverse=True)[:5]:
                pm, km = m["poly"], m["kalshi"]
                cost = pm["yes_price"] + km["no_ask"] if km["no_ask"] > 0 else 99
                print(f"    {m['similarity']:.0%} | Poly YES ${pm['yes_price']:.3f} + Kalshi NO ${km.get('no_ask',0):.3f} = ${cost:.3f}")
                print(f"       Poly: {pm['question'][:60]}")
                print(f"       Kalshi: {km['title'][:60]}")
        return

    print(f"  {'Dir':>20}  {'Spread':>7}  {'Net':>6}  {'Cost':>6}  {'Sim':>4}  Market")
    print(f"  {'-'*20}  {'-'*7}  {'-'*6}  {'-'*6}  {'-'*4}  ------")
    for a in arbs[:args.limit]:
        print(f"  {a['direction']:>20}  {a['gross_spread']:>+6.1%}  "
              f"{a['net_spread']:>+5.1%}  ${a['cost']:.3f}  "
              f"{a['similarity']:>3.0%}  {a['poly_question'][:50]}")
        print(f"  {'':>20}  {'':>7}  {'':>6}  {'':>6}  {'':>4}  K: {a['kalshi_title'][:50]}")


def cmd_trade(args):
    """Scan and execute arb trades."""
    print("\n  Cross-Platform Arb — Trading Mode")

    kalshi = KalshiClient()
    if not kalshi.private_key:
        print("  ERROR: Kalshi API not configured")
        return

    poly_client = get_poly_client()
    poly_bal = get_poly_balance()
    kalshi_bal = kalshi.get_balance()
    print(f"  Polymarket balance: ${poly_bal:,.2f}")
    print(f"  Kalshi balance: ${kalshi_bal:,.2f}")

    dry_run = args.dry_run
    conn = init_db()

    # Existing exposure
    existing = conn.execute(
        "SELECT COALESCE(SUM(total_cost), 0) FROM arb_trades WHERE status='placed'"
    ).fetchone()[0]
    print(f"  Existing exposure: ${existing:,.2f} / ${MAX_TOTAL_EXPOSURE:,.2f} max")

    # Scan
    poly_markets = fetch_poly_markets()
    kalshi_markets = fetch_kalshi_markets(kalshi)
    matched = match_markets(poly_markets, kalshi_markets)
    arbs = find_arbs(matched)

    trades_placed = 0
    total_deployed = float(existing)

    for arb in arbs:
        if total_deployed >= MAX_TOTAL_EXPOSURE:
            print(f"  STOP: Max exposure reached")
            break

        # Size: min of position limit and available balance on each platform
        max_shares_by_limit = int(MAX_POSITION_USD / arb["cost"])
        max_shares_by_poly = int(poly_bal / arb["poly_price"]) if arb["poly_price"] > 0 else 0
        max_shares_by_kalshi = int(kalshi_bal / arb["kalshi_price"]) if arb["kalshi_price"] > 0 else 0
        shares = min(max_shares_by_limit, max_shares_by_poly, max_shares_by_kalshi)
        shares = max(shares, 0)

        if shares < 5:
            continue

        total_cost = shares * arb["cost"]
        expected_profit = shares * arb["net_spread"]

        print(f"\n  >>> ARB: {arb['direction']}")
        print(f"      Poly: {arb['poly_question'][:60]}")
        print(f"      Kalshi: {arb['kalshi_title'][:60]}")
        print(f"      Similarity: {arb['similarity']:.0%}")
        print(f"      Cost: ${arb['cost']:.3f}/share × {shares} = ${total_cost:.2f}")
        print(f"      Spread: {arb['gross_spread']:.1%} gross, {arb['net_spread']:.1%} net")
        print(f"      Expected profit: ${expected_profit:.2f}")

        poly_order_id = "DRY_RUN"
        kalshi_order_id = "DRY_RUN"

        if not dry_run:
            # Leg 1: Polymarket
            try:
                poly_side_const = BUY
                token_id = arb["poly_token"]
                order_args = OrderArgs(
                    token_id=token_id,
                    price=arb["poly_price"],
                    size=shares,
                    side=poly_side_const,
                )
                signed = poly_client.create_order(order_args)
                result = poly_client.post_order(signed, OrderType.GTC)
                poly_order_id = result.get("orderID", "")
                print(f"      Poly order: {poly_order_id}")
            except Exception as e:
                print(f"      Poly order FAILED: {e}")
                continue

            # Leg 2: Kalshi
            try:
                result = kalshi.place_order(
                    ticker=arb["kalshi_ticker"],
                    side=arb["kalshi_side"],
                    action="buy",
                    price=arb["kalshi_price"],
                    count=shares,
                    post_only=False,  # taker to ensure fill
                )
                kalshi_order_id = result.get("order", {}).get("order_id", "")
                print(f"      Kalshi order: {kalshi_order_id}")
            except Exception as e:
                print(f"      Kalshi order FAILED: {e}")
                print(f"      WARNING: Poly leg placed but Kalshi failed — orphaned position!")
                continue

            trades_placed += 1
        else:
            print(f"      [DRY RUN]")
            trades_placed += 1

        conn.execute(
            "INSERT INTO arb_trades (ts, poly_question, kalshi_title, direction, "
            "similarity, poly_price, kalshi_price, cost, gross_spread, net_spread, "
            "fees, shares, total_cost, expected_profit, poly_order_id, kalshi_order_id, "
            "poly_slug, kalshi_ticker) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), arb["poly_question"],
             arb["kalshi_title"], arb["direction"], arb["similarity"],
             arb["poly_price"], arb["kalshi_price"], arb["cost"],
             arb["gross_spread"], arb["net_spread"], arb["fees"],
             shares, total_cost, expected_profit,
             poly_order_id, kalshi_order_id,
             arb["poly_slug"], arb["kalshi_ticker"]))
        conn.commit()
        total_deployed += total_cost

    conn.close()
    mode = "DRY RUN" if dry_run else "LIVE"
    print(f"\n  Done. {trades_placed} arb trades placed [{mode}].")


def cmd_history(args):
    conn = init_db()
    rows = conn.execute(
        "SELECT ts, direction, poly_price, kalshi_price, gross_spread, "
        "net_spread, shares, total_cost, expected_profit, status, pnl, "
        "poly_question FROM arb_trades ORDER BY id DESC LIMIT ?",
        (args.limit or 20,)
    ).fetchall()
    conn.close()
    if not rows:
        print("No arb trades recorded.")
        return
    print(f"\n  {'Time':>16}  {'Dir':>8}  {'P$':>5}  {'K$':>5}  {'Spr':>5}  "
          f"{'Sh':>4}  {'Cost':>6}  {'ExpP':>5}  {'Stat':>6}  {'PnL':>6}")
    for r in rows:
        ts, d, pp, kp, gs, ns, sh, tc, ep, st, pnl, q = r
        print(f"  {ts[:16]}  {d[:8]:>8}  ${pp:.2f}  ${kp:.2f}  {gs:>+4.1%}  "
              f"{sh:>4}  ${tc:>5.2f}  ${ep:>4.2f}  {st:>6}  ${pnl:>5.2f}")
        print(f"    {q[:60]}")


def cmd_pnl(args):
    conn = init_db()
    row = conn.execute(
        "SELECT COUNT(*), SUM(total_cost), SUM(expected_profit), SUM(pnl), "
        "SUM(CASE WHEN status='won' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN status='lost' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN status='placed' THEN 1 ELSE 0 END) "
        "FROM arb_trades"
    ).fetchone()
    conn.close()
    total, cost, exp, pnl, won, lost, pending = row
    if not total:
        print("No arb trades.")
        return
    print(f"\n  === Cross-Arb P&L ===")
    print(f"  Total trades: {total}")
    print(f"  Invested: ${(cost or 0):,.2f}")
    print(f"  Expected profit: ${(exp or 0):,.2f}")
    print(f"  Realized PnL: ${(pnl or 0):,.2f}")
    print(f"  Won: {int(won or 0)} | Lost: {int(lost or 0)} | Pending: {int(pending or 0)}")


def cmd_balance(args):
    kalshi = KalshiClient()
    poly_bal = get_poly_balance()
    print(f"\n  Polymarket: ${poly_bal:,.2f}")
    if kalshi.private_key:
        kalshi_bal = kalshi.get_balance()
        print(f"  Kalshi: ${kalshi_bal:,.2f}")
    else:
        print(f"  Kalshi: NOT CONFIGURED")
    print(f"  Total: ${poly_bal + (kalshi_bal if kalshi.private_key else 0):,.2f}")


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="Cross-Platform Arb")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("scan", help="Scan for arb opportunities")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("trade", help="Execute arb trades")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("history", help="Trade history")
    p.add_argument("--limit", type=int, default=20)

    sub.add_parser("pnl", help="P&L summary")
    sub.add_parser("balance", help="Show platform balances")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    cmds = {
        "scan": cmd_scan, "trade": cmd_trade,
        "history": cmd_history, "pnl": cmd_pnl,
        "balance": cmd_balance,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
