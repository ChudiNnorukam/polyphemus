#!/usr/bin/env python3
"""Check what redeemable positions the wallet holds."""
import os, json, requests, sqlite3
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path("/opt/polyphemus/polyphemus/.env"))
wallet = os.getenv("WALLET_ADDRESS")
print(f"Wallet: {wallet}")

# 1. Check Gamma API for positions
print("\n=== GAMMA API POSITIONS ===")
try:
    resp = requests.get(
        f"https://data-api.polymarket.com/positions",
        params={"user": wallet},
        timeout=30,
    )
    positions = resp.json()
    print(f"Total positions from API: {len(positions)}")

    redeemable = []
    total_redeemable_value = 0.0
    total_unredeemable = 0.0

    for p in positions:
        size = float(p.get("size", 0) or 0)
        if size <= 0:
            continue

        cur_price = float(p.get("curPrice", 0) or 0)
        value = size * cur_price
        redeemable_flag = p.get("redeemable", False)
        mergeable = p.get("mergeable", False)

        print(f"  token={p.get('asset','')[:20]:20s} | size={size:8.1f} | price=${cur_price:.2f} | value=${value:8.2f} | redeemable={redeemable_flag} | mergeable={mergeable} | market={p.get('title','')[:50]}")

        if redeemable_flag or cur_price >= 0.99:
            redeemable.append(p)
            total_redeemable_value += size  # $1 per share if winning
        else:
            total_unredeemable += value

    print(f"\nRedeemable positions: {len(redeemable)}")
    print(f"Redeemable value: ${total_redeemable_value:.2f}")
    print(f"Non-redeemable value: ${total_unredeemable:.2f}")

except Exception as e:
    print(f"Gamma API error: {e}")

# 2. Also check CLOB balance
print("\n=== CLOB EXCHANGE BALANCE ===")
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

    c = ClobClient(
        "https://clob.polymarket.com",
        key=os.getenv("PRIVATE_KEY"),
        chain_id=137,
        signature_type=1,
    )
    c.set_api_creds(c.create_or_derive_api_creds())

    ba = c.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    balance = float(ba.get("balance", "0")) / 1e6
    print(f"Exchange USDC.e balance: ${balance:.2f}")
except Exception as e:
    print(f"CLOB error: {e}")

# 3. Check unique token_ids from our winning resolved trades
print("\n=== OUR WINNING RESOLVED TOKEN IDS ===")
db = sqlite3.connect("/opt/polyphemus/data/performance.db")
db.row_factory = sqlite3.Row
rows = db.execute("""
    SELECT token_id, slug, entry_price, COALESCE(entry_size, entry_amount, 0) as size,
           COALESCE(pnl, profit_loss, 0) as pnl
    FROM trades
    WHERE exit_reason = 'market_resolved'
      AND COALESCE(pnl, profit_loss, 0) > 0
    ORDER BY COALESCE(pnl, profit_loss, 0) DESC
    LIMIT 20
""").fetchall()
print(f"Top 20 winning resolved trades:")
for r in rows:
    print(f"  pnl=${r['pnl']:6.2f} | size={r['size']:5.0f} | slug={r['slug']}")

total_winning = db.execute("""
    SELECT COUNT(*) as n, SUM(COALESCE(entry_size, entry_amount, 0)) as shares,
           SUM(COALESCE(pnl, profit_loss, 0)) as pnl
    FROM trades
    WHERE exit_reason = 'market_resolved' AND COALESCE(pnl, profit_loss, 0) > 0
""").fetchone()
print(f"\nTotal winning resolved: {total_winning['n']} trades, {total_winning['shares']:.0f} shares, pnl=${total_winning['pnl']:.2f}")
