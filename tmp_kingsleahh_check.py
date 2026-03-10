#!/usr/bin/env python3
"""Check kingsleahh wallet balance, positions, and trade history."""
import os, json, requests
from pathlib import Path
from dotenv import load_dotenv
from web3 import Web3

load_dotenv(Path("/opt/lagbot/lagbot/.env"))

WALLET = "0x7E69be59E92a396EcCBba344CAe383927fcAD9Ad"  # kingsleahh Safe
LAGBOT_EOA = os.getenv("WALLET_ADDRESS", "0x1C0523D33b0D1c7Df8Ec450C5318cFcFc32Ce80A")
RPC_URL = os.getenv("POLYGON_RPC_URL", "https://polygon-bor-rpc.publicnode.com")
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
]

def check_balances(w3):
    print("=" * 60)
    print("=== WALLET BALANCES ===")
    print("=" * 60)
    usdc_e = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)

    for name, addr in [("Kingsleahh (Safe)", WALLET), ("Lagbot EOA", LAGBOT_EOA)]:
        bal = usdc_e.functions.balanceOf(Web3.to_checksum_address(addr)).call()
        pol = w3.eth.get_balance(Web3.to_checksum_address(addr))
        print(f"{name}: ${bal/1e6:.2f} USDC.e | {w3.from_wei(pol, 'ether'):.4f} POL")

def check_clob_balance():
    print(f"\n=== CLOB EXCHANGE BALANCE ===")
    # Check for both wallets
    for name, addr in [("Kingsleahh", WALLET), ("Lagbot EOA", LAGBOT_EOA)]:
        try:
            r = requests.get(f"{CLOB_API}/balance?address={addr}", timeout=10)
            if r.ok:
                print(f"{name} CLOB: {r.json()}")
            else:
                print(f"{name} CLOB: {r.status_code}")
        except Exception as e:
            print(f"{name} CLOB error: {e}")

def check_positions():
    print(f"\n{'=' * 60}")
    print("=== OPEN POSITIONS (Gamma API) ===")
    print("=" * 60)

    all_positions = []
    offset = 0
    while True:
        r = requests.get(f"{GAMMA_API}/positions", params={
            "user": WALLET.lower(), "limit": 100, "offset": offset,
            "sortBy": "currentValue", "sortOrder": "desc"
        }, timeout=15)
        if not r.ok:
            print(f"Gamma API error: {r.status_code}")
            break
        batch = r.json()
        if not batch:
            break
        all_positions.extend(batch)
        offset += 100
        if len(batch) < 100:
            break

    # Also check Lagbot EOA positions
    lagbot_positions = []
    offset = 0
    while True:
        r = requests.get(f"{GAMMA_API}/positions", params={
            "user": LAGBOT_EOA.lower(), "limit": 100, "offset": offset,
            "sortBy": "currentValue", "sortOrder": "desc"
        }, timeout=15)
        if not r.ok:
            break
        batch = r.json()
        if not batch:
            break
        lagbot_positions.extend(batch)
        offset += 100
        if len(batch) < 100:
            break

    print(f"\nKingsleahh positions: {len(all_positions)}")
    print(f"Lagbot EOA positions: {len(lagbot_positions)}")

    # Show kingsleahh positions
    if all_positions:
        open_pos = [p for p in all_positions if float(p.get("currentValue", 0)) > 0]
        closed_pos = [p for p in all_positions if float(p.get("currentValue", 0)) == 0]
        print(f"\n  Open (value > 0): {len(open_pos)}")
        print(f"  Closed (value = 0): {len(closed_pos)}")

        for p in open_pos[:20]:
            title = p.get("market", {}).get("question", p.get("title", "?"))[:60]
            size = float(p.get("size", 0))
            cur_val = float(p.get("currentValue", 0))
            avg_price = float(p.get("avgPrice", 0))
            cash_pnl = float(p.get("cashPnl", 0))
            outcome = p.get("outcome", "?")
            print(f"  {outcome:4s} | {size:8.1f} shares | avg ${avg_price:.3f} | val ${cur_val:.2f} | pnl ${cash_pnl:+.2f} | {title}")

    # Show lagbot EOA positions
    if lagbot_positions:
        open_lag = [p for p in lagbot_positions if float(p.get("currentValue", 0)) > 0]
        closed_lag = [p for p in lagbot_positions if float(p.get("currentValue", 0)) == 0]
        print(f"\nLagbot EOA - Open: {len(open_lag)}, Closed: {len(closed_lag)}")
        for p in open_lag[:20]:
            title = p.get("market", {}).get("question", p.get("title", "?"))[:60]
            size = float(p.get("size", 0))
            cur_val = float(p.get("currentValue", 0))
            cash_pnl = float(p.get("cashPnl", 0))
            outcome = p.get("outcome", "?")
            print(f"  {outcome:4s} | {size:8.1f} shares | val ${cur_val:.2f} | pnl ${cash_pnl:+.2f} | {title}")

def check_trade_history():
    print(f"\n{'=' * 60}")
    print("=== TRADE HISTORY (Lagbot EOA) ===")
    print("=" * 60)

    # Get recent trades from CLOB activity
    all_trades = []
    offset = 0
    while True:
        r = requests.get(f"{GAMMA_API}/activity", params={
            "user": LAGBOT_EOA.lower(), "limit": 100, "offset": offset,
            "sortBy": "createdAt", "sortOrder": "desc"
        }, timeout=15)
        if not r.ok:
            print(f"Activity API error: {r.status_code}")
            break
        batch = r.json()
        if not batch:
            break
        all_trades.extend(batch)
        offset += 100
        if len(batch) < 100:
            break

    print(f"Total trades (Lagbot EOA): {len(all_trades)}")

    # Summary stats
    if all_trades:
        buys = [t for t in all_trades if t.get("type") == "BUY" or t.get("side") == "BUY"]
        sells = [t for t in all_trades if t.get("type") == "SELL" or t.get("side") == "SELL"]
        print(f"  Buys: {len(buys)}, Sells: {len(sells)}")

        # Show last 20 trades
        print(f"\n  Last 20 trades:")
        for t in all_trades[:20]:
            side = t.get("type", t.get("side", "?"))
            title = t.get("market", {}).get("question", t.get("title", "?"))[:50]
            size = t.get("size", "?")
            price = t.get("price", "?")
            outcome = t.get("outcome", "?")
            ts = t.get("createdAt", t.get("timestamp", "?"))[:19]
            print(f"    {ts} | {side:4s} | {outcome:4s} | {size:>8s} @ ${price} | {title}")

    # Also check kingsleahh trades
    king_trades = []
    offset = 0
    while True:
        r = requests.get(f"{GAMMA_API}/activity", params={
            "user": WALLET.lower(), "limit": 100, "offset": offset,
            "sortBy": "createdAt", "sortOrder": "desc"
        }, timeout=15)
        if not r.ok:
            break
        batch = r.json()
        if not batch:
            break
        king_trades.extend(batch)
        offset += 100
        if len(batch) < 100:
            break

    print(f"\nTotal trades (Kingsleahh): {len(king_trades)}")
    if king_trades:
        print(f"  Last 10 trades:")
        for t in king_trades[:10]:
            side = t.get("type", t.get("side", "?"))
            title = t.get("market", {}).get("question", t.get("title", "?"))[:50]
            size = t.get("size", "?")
            price = t.get("price", "?")
            outcome = t.get("outcome", "?")
            ts = t.get("createdAt", t.get("timestamp", "?"))[:19]
            print(f"    {ts} | {side:4s} | {outcome:4s} | {size:>8s} @ ${price} | {title}")

def check_pnl_summary():
    print(f"\n{'=' * 60}")
    print("=== P&L SUMMARY ===")
    print("=" * 60)

    for name, addr in [("Lagbot EOA", LAGBOT_EOA), ("Kingsleahh", WALLET)]:
        all_pos = []
        offset = 0
        while True:
            r = requests.get(f"{GAMMA_API}/positions", params={
                "user": addr.lower(), "limit": 100, "offset": offset
            }, timeout=15)
            if not r.ok:
                break
            batch = r.json()
            if not batch:
                break
            all_pos.extend(batch)
            offset += 100
            if len(batch) < 100:
                break

        total_pnl = sum(float(p.get("cashPnl", 0)) for p in all_pos)
        total_invested = sum(float(p.get("initialValue", 0)) for p in all_pos)
        total_current = sum(float(p.get("currentValue", 0)) for p in all_pos)
        winners = len([p for p in all_pos if float(p.get("cashPnl", 0)) > 0])
        losers = len([p for p in all_pos if float(p.get("cashPnl", 0)) < 0])
        total = len(all_pos)

        print(f"\n{name} ({addr[:10]}...):")
        print(f"  Positions: {total} (W:{winners} / L:{losers})")
        print(f"  Total invested: ${total_invested:.2f}")
        print(f"  Current value: ${total_current:.2f}")
        print(f"  Cash P&L: ${total_pnl:.2f}")
        if total > 0:
            print(f"  Win rate: {winners/total*100:.1f}%")

if __name__ == "__main__":
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    check_balances(w3)
    check_clob_balance()
    check_positions()
    check_trade_history()
    check_pnl_summary()
