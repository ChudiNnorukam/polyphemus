#!/usr/bin/env python3
"""Prepare redemption: get condition IDs and neg_risk status from Gamma API."""
import os, json, requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path("/opt/polyphemus/polyphemus/.env"))
wallet = os.getenv("WALLET_ADDRESS")

# Get all positions with full market data
resp = requests.get(
    "https://data-api.polymarket.com/positions",
    params={"user": wallet},
    timeout=30,
)
positions = resp.json()

# Separate winners from losers
winners = []
losers = []
total_win_value = 0.0

for p in positions:
    size = float(p.get("size", 0) or 0)
    if size <= 0:
        continue

    cur_price = float(p.get("curPrice", 0) or 0)
    redeemable = p.get("redeemable", False)
    token_id = p.get("asset", "")
    condition_id = p.get("conditionId", "")
    neg_risk = p.get("negRisk", False)
    market_slug = p.get("marketSlug", "")
    title = p.get("title", "")
    outcome = p.get("outcome", "")

    entry = {
        "token_id": token_id,
        "condition_id": condition_id,
        "size": size,
        "cur_price": cur_price,
        "redeemable": redeemable,
        "neg_risk": neg_risk,
        "title": title[:60],
        "outcome": outcome,
        "market_slug": market_slug,
    }

    if cur_price >= 0.95 and redeemable:
        winners.append(entry)
        total_win_value += size
    elif redeemable:
        losers.append(entry)

print(f"=== WINNING POSITIONS (price >= $0.95) ===")
print(f"Count: {len(winners)}, Total value: ${total_win_value:.2f}")
for w in winners:
    print(f"  ${w['size']:8.1f} | price=${w['cur_price']:.2f} | neg_risk={w['neg_risk']} | {w['outcome']:5s} | {w['title']}")
    print(f"           condition={w['condition_id']}")

print(f"\n=== LOSING POSITIONS (price < $0.95, redeemable) ===")
print(f"Count: {len(losers)}")
total_losing_shares = sum(l["size"] for l in losers)
print(f"Total losing shares: {total_losing_shares:.0f} (worth $0)")

# Check for unique condition_ids across all positions
all_conditions = set()
neg_risk_conditions = set()
regular_conditions = set()
for p in winners + losers:
    cid = p["condition_id"]
    if cid:
        all_conditions.add(cid)
        if p["neg_risk"]:
            neg_risk_conditions.add(cid)
        else:
            regular_conditions.add(cid)

print(f"\n=== CONDITION IDS ===")
print(f"Total unique conditions: {len(all_conditions)}")
print(f"Neg risk conditions: {len(neg_risk_conditions)}")
print(f"Regular conditions: {len(regular_conditions)}")

# Check available fields in the API response
if positions:
    print(f"\n=== API FIELDS (first position) ===")
    for k, v in positions[0].items():
        print(f"  {k}: {repr(v)[:80]}")

# Save winners to JSON for the redemption script
with open("/tmp/winners.json", "w") as f:
    json.dump(winners, f, indent=2)
print(f"\nSaved {len(winners)} winners to /tmp/winners.json")

# Also save ALL redeemable for batch redemption
with open("/tmp/all_redeemable.json", "w") as f:
    json.dump(winners + losers, f, indent=2)
print(f"Saved {len(winners) + len(losers)} total redeemable to /tmp/all_redeemable.json")
