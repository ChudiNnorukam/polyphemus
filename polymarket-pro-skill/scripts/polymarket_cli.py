#!/usr/bin/env python3
"""Polymarket Pro CLI — production-grade trading via py_clob_client.

Standalone CLI for OpenClaw skill. Uses maker orders by default.
All CLOB interactions use py_clob_client with proper signing.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    OrderArgs,
    BalanceAllowanceParams,
    AssetType,
    OrderType,
)
from py_clob_client.order_builder.constants import BUY, SELL

CLOB_HOST = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
CHAIN_ID = 137


def get_client():
    """Create authenticated ClobClient from env vars."""
    private_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
    api_key = os.environ.get("POLYMARKET_API_KEY", "")
    secret = os.environ.get("POLYMARKET_SECRET", "")
    passphrase = os.environ.get("POLYMARKET_PASSPHRASE", "")
    sig_type = int(os.environ.get("POLYMARKET_SIGNATURE_TYPE", "0"))

    if not private_key:
        print("ERROR: POLYMARKET_PRIVATE_KEY not set")
        sys.exit(1)

    client = ClobClient(
        CLOB_HOST,
        key=private_key,
        chain_id=CHAIN_ID,
        signature_type=sig_type,
    )
    if api_key:
        client.set_api_creds(client.create_or_derive_api_creds())
    return client


def get_wallet_address():
    """Derive wallet address from env."""
    return os.environ.get("POLYMARKET_WALLET_ADDRESS", "")


# --- Balance & Wallet ---

def cmd_balance(args):
    client = get_client()
    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    result = client.get_balance_allowance(params)
    balance = float(result.get("balance", 0)) / 1e6
    print(f"USDC Balance: ${balance:,.2f}")


def cmd_shares(args):
    client = get_client()
    token_id = args.token_id
    try:
        resp = requests.get(f"{CLOB_HOST}/positions", params={
            "asset_id": token_id,
        }, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            size = float(data.get("size", 0)) if data else 0
            print(f"Share balance for {token_id[:16]}...: {size:,.0f} shares")
        else:
            print(f"Could not fetch share balance (HTTP {resp.status_code})")
    except Exception as e:
        print(f"Error: {e}")


# --- Market Discovery ---

def cmd_search(args):
    query = args.query
    limit = args.limit or 10
    try:
        resp = requests.get(f"{GAMMA_API}/markets", params={
            "slug_contains": query.lower().replace(" ", "-"),
            "limit": str(limit),
            "order": "startDate",
            "ascending": "false",
            "closed": "false",
        }, timeout=20)
        resp.raise_for_status()
        markets = resp.json()
        if not markets:
            print(f"No markets found for '{query}'")
            return
        print(f"Found {len(markets)} markets:\n")
        for m in markets:
            slug = m.get("slug", "")
            question = m.get("question", "")[:80]
            volume = float(m.get("volume", 0) or 0)
            tokens_raw = m.get("clobTokenIds", "[]")
            tokens = json.loads(tokens_raw) if isinstance(tokens_raw, str) else tokens_raw
            print(f"  {slug}")
            print(f"    Q: {question}")
            print(f"    Volume: ${volume:,.0f} | Tokens: {len(tokens)}")
            print()
    except Exception as e:
        print(f"Search error: {e}")


def cmd_market(args):
    slug = args.slug
    try:
        resp = requests.get(f"{GAMMA_API}/markets", params={
            "slug": slug, "limit": "1",
        }, timeout=20)
        resp.raise_for_status()
        markets = resp.json()
        if not markets:
            print(f"Market not found: {slug}")
            return
        m = markets[0]
        tokens_raw = m.get("clobTokenIds", "[]")
        tokens = json.loads(tokens_raw) if isinstance(tokens_raw, str) else tokens_raw
        outcomes_raw = m.get("outcomes", "[]")
        outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
        prices_raw = m.get("outcomePrices", "[]")
        prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw

        print(f"Market: {m.get('question', '')}")
        print(f"Slug: {slug}")
        print(f"Condition ID: {m.get('conditionId', '')}")
        print(f"Volume: ${float(m.get('volume', 0) or 0):,.0f}")
        print(f"End Date: {m.get('endDate', 'N/A')}")
        print()
        for i, token in enumerate(tokens):
            outcome = outcomes[i] if i < len(outcomes) else f"Outcome {i}"
            price = prices[i] if i < len(prices) else "?"
            print(f"  [{outcome}] Token: {token}")
            print(f"          Price: ${price}")
        print()
        print(f"Full JSON: {json.dumps(m, indent=2)[:500]}...")
    except Exception as e:
        print(f"Market lookup error: {e}")


def cmd_discover(args):
    asset = args.asset.lower()
    now = int(time.time())
    epoch = (now // 300) * 300
    slugs_to_try = [
        f"{asset}-updown-5m-{epoch}",
        f"{asset}-updown-5m-{epoch + 300}",
        f"{asset}-updown-5m-{epoch - 300}",
    ]
    for slug in slugs_to_try:
        try:
            resp = requests.get(f"{GAMMA_API}/markets", params={
                "slug": slug, "limit": "1",
            }, timeout=15)
            if resp.status_code == 200 and resp.json():
                m = resp.json()[0]
                tokens_raw = m.get("clobTokenIds", "[]")
                tokens = json.loads(tokens_raw) if isinstance(tokens_raw, str) else tokens_raw
                outcomes_raw = m.get("outcomes", "[]")
                outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
                prices_raw = m.get("outcomePrices", "[]")
                prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw

                print(f"Current {asset.upper()} 5m market: {slug}")
                print(f"  Question: {m.get('question', '')}")
                print(f"  Condition ID: {m.get('conditionId', '')}")
                for i, token in enumerate(tokens):
                    outcome = outcomes[i] if i < len(outcomes) else f"Outcome {i}"
                    price = prices[i] if i < len(prices) else "?"
                    print(f"  [{outcome}] {token}  @ ${price}")
                return
        except Exception:
            continue
    print(f"No active {asset.upper()} 5m market found")


def cmd_midpoint(args):
    client = get_client()
    token_id = args.token_id
    try:
        mid = client.get_midpoint(token_id)
        print(f"Midpoint: ${float(mid):,.4f}")
    except Exception as e:
        print(f"Midpoint error: {e}")


def cmd_orderbook(args):
    client = get_client()
    token_id = args.token_id
    try:
        book = client.get_order_book(token_id)
        bids = book.get("bids", [])[:5]
        asks = book.get("asks", [])[:5]
        print(f"Order Book for {token_id[:16]}...\n")
        print(f"  {'BIDS':>12}  |  {'ASKS':<12}")
        print(f"  {'Price':>6} {'Size':>5}  |  {'Price':>6} {'Size':>5}")
        print(f"  {'-'*12}  |  {'-'*12}")
        max_rows = max(len(bids), len(asks))
        for i in range(max_rows):
            bid_str = f"  ${float(bids[i]['price']):>.3f} {float(bids[i]['size']):>5.0f}" if i < len(bids) else " " * 14
            ask_str = f"${float(asks[i]['price']):>.3f} {float(asks[i]['size']):>5.0f}" if i < len(asks) else ""
            print(f"{bid_str}  |  {ask_str}")
        if bids and asks:
            best_bid = float(bids[0]["price"])
            best_ask = float(asks[0]["price"])
            mid = (best_bid + best_ask) / 2
            spread = best_ask - best_bid
            print(f"\n  Spread: ${spread:.4f} | Midpoint: ${mid:.4f}")
    except Exception as e:
        print(f"Order book error: {e}")


# --- Trading ---

def cmd_buy(args):
    _place_order(args.token_id, float(args.price), float(args.size), BUY, args.taker)


def cmd_sell(args):
    _place_order(args.token_id, float(args.price), float(args.size), SELL, args.taker)


def _place_order(token_id, price, size, side, taker=False):
    if size < 5:
        print("ERROR: Minimum order size is 5 shares")
        return
    if price <= 0 or price >= 1:
        print("ERROR: Price must be between 0.01 and 0.99")
        return

    client = get_client()
    side_str = "BUY" if side == BUY else "SELL"
    mode = "TAKER" if taker else "MAKER (post-only)"

    print(f"Placing {side_str} order: {size:.0f} shares @ ${price:.3f} [{mode}]")

    try:
        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=side,
        )
        if taker:
            result = client.create_and_post_order(order_args)
        else:
            signed = client.create_order(order_args)
            result = client.post_order(signed, OrderType.GTC, post_only=True)

        order_id = result.get("orderID", "")
        status = result.get("status", "UNKNOWN")
        print(f"Order placed: {order_id}")
        print(f"Status: {status}")

        if not taker:
            print("\nMaker order is on the book. It will fill when a taker matches it.")
            print(f"Cancel with: cancel {order_id}")
    except Exception as e:
        print(f"Order failed: {e}")


def cmd_cancel(args):
    client = get_client()
    order_id = args.order_id
    try:
        result = client.cancel(order_id)
        print(f"Cancel result: {result}")
    except Exception as e:
        print(f"Cancel failed: {e}")


def cmd_cancel_all(args):
    client = get_client()
    try:
        result = client.cancel_all()
        print(f"Cancelled all orders: {result}")
    except Exception as e:
        print(f"Cancel all failed: {e}")


# --- Positions & History ---

def cmd_positions(args):
    wallet = get_wallet_address()
    if not wallet:
        print("ERROR: POLYMARKET_WALLET_ADDRESS not set")
        return
    try:
        resp = requests.get(f"{DATA_API}/positions", params={
            "user": wallet,
            "sizeThreshold": "0",
            "limit": "100",
        }, timeout=20)
        resp.raise_for_status()
        positions = resp.json()
        if not positions:
            print("No open positions")
            return
        print(f"{'Token':>16}  {'Size':>8}  {'Avg$':>6}  {'Cur$':>6}  {'PnL':>8}  {'Redeemable':>10}  Market")
        print(f"{'-'*16}  {'-'*8}  {'-'*6}  {'-'*6}  {'-'*8}  {'-'*10}  {'-'*30}")
        for p in positions:
            token = (p.get("asset", "") or "")[:16]
            size = float(p.get("size", 0) or 0)
            avg = float(p.get("avgPrice", 0) or 0)
            cur = float(p.get("curPrice", 0) or 0)
            pnl = float(p.get("cashPnl", 0) or 0)
            redeemable = "YES" if p.get("redeemable") else "no"
            title = (p.get("title", "") or (p.get("market", {}) or {}).get("question", ""))[:30]
            print(f"{token:>16}  {size:>8.0f}  {avg:>6.3f}  {cur:>6.3f}  ${pnl:>7.2f}  {redeemable:>10}  {title}")
    except Exception as e:
        print(f"Positions error: {e}")


def cmd_history(args):
    wallet = get_wallet_address()
    if not wallet:
        print("ERROR: POLYMARKET_WALLET_ADDRESS not set")
        return
    days = args.days or 7
    try:
        resp = requests.get(f"{DATA_API}/trades", params={
            "maker": wallet,
            "limit": "200",
        }, timeout=20)
        resp.raise_for_status()
        trades = resp.json()
        if not trades:
            print("No recent trades")
            return
        cutoff = time.time() - (days * 86400)
        recent = []
        for t in trades:
            ts = t.get("timestamp", 0)
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                except (ValueError, TypeError):
                    ts = 0
            if ts > cutoff:
                recent.append(t)
        print(f"Trades in last {days} days: {len(recent)}\n")
        for t in recent[:20]:
            side = t.get("side", "?")
            price = float(t.get("price", 0) or 0)
            size = float(t.get("size", 0) or 0)
            usd = price * size
            ts_str = t.get("timestamp", "")[:19]
            print(f"  {ts_str}  {side:>4}  {size:>6.0f} @ ${price:.3f}  (${usd:,.0f})")
    except Exception as e:
        print(f"History error: {e}")


def cmd_pnl(args):
    wallet = get_wallet_address()
    if not wallet:
        print("ERROR: POLYMARKET_WALLET_ADDRESS not set")
        return
    try:
        resp = requests.get(f"{DATA_API}/positions", params={
            "user": wallet,
            "sizeThreshold": "0",
            "limit": "500",
        }, timeout=20)
        resp.raise_for_status()
        positions = resp.json()
        if not positions:
            print("No position data")
            return
        total_pnl = 0.0
        total_positions = len(positions)
        winners = 0
        for p in positions:
            pnl = float(p.get("cashPnl", 0) or 0)
            total_pnl += pnl
            if pnl > 0:
                winners += 1
        wr = (winners / total_positions * 100) if total_positions else 0
        print(f"=== PnL Summary ===")
        print(f"Total positions: {total_positions}")
        print(f"Winners: {winners} ({wr:.1f}%)")
        print(f"Total PnL: ${total_pnl:,.2f}")
    except Exception as e:
        print(f"PnL error: {e}")


# --- Redemption ---

def cmd_redeemable(args):
    wallet = get_wallet_address()
    if not wallet:
        print("ERROR: POLYMARKET_WALLET_ADDRESS not set")
        return
    try:
        resp = requests.get(f"{DATA_API}/positions", params={
            "user": wallet,
            "sizeThreshold": "0",
            "limit": "500",
        }, timeout=20)
        resp.raise_for_status()
        positions = resp.json()
        redeemable = [p for p in positions if p.get("redeemable")]
        if not redeemable:
            print("No redeemable positions")
            return
        print(f"Found {len(redeemable)} redeemable positions:\n")
        for p in redeemable:
            cid = p.get("conditionId", "?")
            size = float(p.get("size", 0) or 0)
            title = (p.get("title", "") or "")[:50]
            print(f"  Condition: {cid}")
            print(f"  Size: {size:.0f} shares | {title}")
            print()
    except Exception as e:
        print(f"Redeemable check error: {e}")


def cmd_redeem(args):
    print(f"Redemption of {args.condition_id} requires on-chain transaction.")
    print("Use the full polyphemus redeemer for automated redemption.")
    print("Manual redemption: call redeemPositions() on the CTF contract")
    print(f"  Contract: 0x4D97DCd97eC945f40cF65F87097ACe5EA0476045")
    print(f"  conditionId: {args.condition_id}")
    print(f"  indexSets: [1, 2]")


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="Polymarket Pro CLI")
    sub = parser.add_subparsers(dest="command", help="Command")

    # Balance
    sub.add_parser("balance", help="Check USDC balance")
    p = sub.add_parser("shares", help="Check share balance")
    p.add_argument("token_id")

    # Market discovery
    p = sub.add_parser("search", help="Search markets")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)

    p = sub.add_parser("market", help="Get market by slug")
    p.add_argument("slug")

    p = sub.add_parser("discover", help="Find current updown market")
    p.add_argument("asset", help="btc, eth, sol, xrp")

    p = sub.add_parser("midpoint", help="Get midpoint price")
    p.add_argument("token_id")

    p = sub.add_parser("orderbook", help="Get order book")
    p.add_argument("token_id")

    # Trading
    p = sub.add_parser("buy", help="Buy shares")
    p.add_argument("token_id")
    p.add_argument("price")
    p.add_argument("size")
    p.add_argument("--taker", action="store_true", help="Use taker order (fills immediately)")

    p = sub.add_parser("sell", help="Sell shares")
    p.add_argument("token_id")
    p.add_argument("price")
    p.add_argument("size")
    p.add_argument("--taker", action="store_true")

    p = sub.add_parser("cancel", help="Cancel order")
    p.add_argument("order_id")

    sub.add_parser("cancel-all", help="Cancel all orders")

    # Positions
    sub.add_parser("positions", help="List positions")
    p = sub.add_parser("history", help="Trade history")
    p.add_argument("--days", type=int, default=7)
    p = sub.add_parser("pnl", help="PnL summary")
    p.add_argument("--days", type=int, default=30)

    # Redemption
    sub.add_parser("redeemable", help="List redeemable positions")
    p = sub.add_parser("redeem", help="Redeem resolved market")
    p.add_argument("condition_id")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    commands = {
        "balance": cmd_balance,
        "shares": cmd_shares,
        "search": cmd_search,
        "market": cmd_market,
        "discover": cmd_discover,
        "midpoint": cmd_midpoint,
        "orderbook": cmd_orderbook,
        "buy": cmd_buy,
        "sell": cmd_sell,
        "cancel": cmd_cancel,
        "cancel-all": cmd_cancel_all,
        "positions": cmd_positions,
        "history": cmd_history,
        "pnl": cmd_pnl,
        "redeemable": cmd_redeemable,
        "redeem": cmd_redeem,
    }

    fn = commands.get(args.command)
    if fn:
        fn(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
