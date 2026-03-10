#!/usr/bin/env python3
"""Check total positions with pagination and sum redeemable value."""
import os, requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path("/opt/polyphemus/polyphemus/.env"))
wallet = os.getenv("WALLET_ADDRESS")

# Try with different offsets to get all positions
all_positions = []
offset = 0
limit = 500  # Try a larger limit

resp = requests.get(
    "https://data-api.polymarket.com/positions",
    params={"user": wallet, "limit": limit, "offset": offset, "sizeThreshold": 0},
    timeout=30,
)
batch = resp.json()
all_positions.extend(batch)
print(f"Page 1: {len(batch)} positions (offset={offset}, limit={limit})")

# If we got exactly 100 or limit, try next page
if len(batch) >= 100:
    offset = len(batch)
    resp2 = requests.get(
        "https://data-api.polymarket.com/positions",
        params={"user": wallet, "limit": limit, "offset": offset, "sizeThreshold": 0},
        timeout=30,
    )
    batch2 = resp2.json()
    all_positions.extend(batch2)
    print(f"Page 2: {len(batch2)} positions (offset={offset})")

    if len(batch2) >= 100:
        offset += len(batch2)
        resp3 = requests.get(
            "https://data-api.polymarket.com/positions",
            params={"user": wallet, "limit": limit, "offset": offset, "sizeThreshold": 0},
            timeout=30,
        )
        batch3 = resp3.json()
        all_positions.extend(batch3)
        print(f"Page 3: {len(batch3)} positions (offset={offset})")

print(f"\nTotal positions: {len(all_positions)}")

# Categorize
winners = []
losers = []
for p in all_positions:
    size = float(p.get("size", 0) or 0)
    if size <= 0:
        continue
    cur_price = float(p.get("curPrice", 0) or 0)
    redeemable = p.get("redeemable", False)
    cash_pnl = float(p.get("cashPnl", 0) or 0)
    initial = float(p.get("initialValue", 0) or 0)

    if cur_price >= 0.95 and redeemable:
        winners.append({"size": size, "price": cur_price, "title": p.get("title", "")[:60], "initial": initial})
    elif redeemable:
        losers.append({"size": size, "price": cur_price, "title": p.get("title", "")[:60], "initial": initial, "cashPnl": cash_pnl})

print(f"\nWinners (price >= $0.95): {len(winners)}")
total_win = sum(w["size"] for w in winners)
print(f"Total winning shares: {total_win:.1f} (=${ total_win:.2f})")
for w in winners:
    print(f"  {w['size']:8.1f} shares | ${w['price']:.2f} | cost=${w['initial']:.2f} | {w['title']}")

print(f"\nLosers (resolved, worth $0): {len(losers)}")
total_lose_initial = sum(l["initial"] for l in losers)
total_lose_shares = sum(l["size"] for l in losers)
print(f"Total losing shares: {total_lose_shares:.0f}")
print(f"Total capital lost in resolved losers: ${total_lose_initial:.2f}")

# Sum all cashPnl from API
total_cash_pnl = sum(float(p.get("cashPnl", 0) or 0) for p in all_positions)
total_initial = sum(float(p.get("initialValue", 0) or 0) for p in all_positions if float(p.get("size", 0) or 0) > 0)
total_current = sum(float(p.get("currentValue", 0) or 0) for p in all_positions if float(p.get("size", 0) or 0) > 0)
print(f"\n=== CAPITAL SUMMARY (from API) ===")
print(f"Total initial value (cost basis): ${total_initial:.2f}")
print(f"Total current value: ${total_current:.2f}")
print(f"Total cashPnl: ${total_cash_pnl:.2f}")
print(f"Net P&L on positions: ${total_current - total_initial:.2f}")
