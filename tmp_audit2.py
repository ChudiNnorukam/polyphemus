#!/usr/bin/env python3
"""Check unredeemed positions and capital flow."""
import sqlite3
from datetime import datetime

db = sqlite3.connect('/opt/polyphemus/data/performance.db')
db.row_factory = sqlite3.Row

# Unredeemed resolved positions
print("=== UNREDEEMED RESOLVED POSITIONS ===")
rows = db.execute("""
    SELECT COUNT(*) as n,
           SUM(COALESCE(pnl, profit_loss, 0)) as pnl,
           SUM(entry_price * COALESCE(entry_size, entry_amount, 0)) as cost
    FROM trades
    WHERE exit_reason = 'market_resolved'
      AND (is_redeemed = 0 OR is_redeemed IS NULL)
""").fetchone()
print(f"  Unredeemed count: {rows['n']}")
print(f"  Unredeemed P&L: ${rows['pnl'] or 0:.2f}")
print(f"  Unredeemed cost: ${rows['cost'] or 0:.2f}")

rows2 = db.execute("""
    SELECT COUNT(*) as n,
           SUM(COALESCE(pnl, profit_loss, 0)) as pnl
    FROM trades
    WHERE exit_reason = 'market_resolved'
      AND is_redeemed = 1
""").fetchone()
print(f"  Redeemed count: {rows2['n']}")
print(f"  Redeemed P&L: ${rows2['pnl'] or 0:.2f}")

# All resolved trades detail
print("\n=== REDEMPTION STATUS ===")
rows3 = db.execute("""
    SELECT is_resolved, is_redeemed, COUNT(*) as n,
           SUM(COALESCE(pnl, profit_loss, 0)) as pnl,
           SUM(entry_price * COALESCE(entry_size, entry_amount, 0)) as cost
    FROM trades
    GROUP BY is_resolved, is_redeemed
""").fetchall()
for r in rows3:
    print(f"  resolved={r['is_resolved']} redeemed={r['is_redeemed']} | n={r['n']} | pnl=${r['pnl'] or 0:.2f} | cost=${r['cost'] or 0:.2f}")

# Capital flow: money in vs money out
print("\n=== CAPITAL FLOW ===")
# Total spent on entries
r_spent = db.execute("SELECT SUM(entry_price * COALESCE(entry_size, entry_amount, 0)) as s FROM trades").fetchone()
print(f"  Total entry cost: ${r_spent['s'] or 0:.2f}")

# Total received from exits (non-resolved, where we actually sold)
r_sold = db.execute("""
    SELECT SUM(exit_price * COALESCE(exit_size, entry_size, entry_amount, 0)) as s
    FROM trades
    WHERE exit_reason != 'market_resolved'
      AND exit_price IS NOT NULL
""").fetchone()
print(f"  Total exit proceeds (sells): ${r_sold['s'] or 0:.2f}")

# Total received from redemptions
r_redeemed = db.execute("""
    SELECT SUM(COALESCE(entry_size, entry_amount, 0)) as shares,
           COUNT(*) as n
    FROM trades
    WHERE exit_reason = 'market_resolved'
      AND is_redeemed = 1
""").fetchone()
print(f"  Redeemed shares (=$1 each): {r_redeemed['shares'] or 0:.0f} shares ({r_redeemed['n']} trades)")

# Unredeemed winning shares (locked capital)
r_unredeemed = db.execute("""
    SELECT SUM(COALESCE(entry_size, entry_amount, 0)) as shares,
           SUM(entry_price * COALESCE(entry_size, entry_amount, 0)) as cost,
           COUNT(*) as n
    FROM trades
    WHERE exit_reason = 'market_resolved'
      AND (is_redeemed = 0 OR is_redeemed IS NULL)
      AND COALESCE(pnl, profit_loss, 0) > 0
""").fetchone()
print(f"  Unredeemed WINNING shares: {r_unredeemed['shares'] or 0:.0f} (cost=${r_unredeemed['cost'] or 0:.2f}, n={r_unredeemed['n']})")

r_unredeemed_lose = db.execute("""
    SELECT SUM(COALESCE(entry_size, entry_amount, 0)) as shares,
           SUM(entry_price * COALESCE(entry_size, entry_amount, 0)) as cost,
           COUNT(*) as n
    FROM trades
    WHERE exit_reason = 'market_resolved'
      AND (is_redeemed = 0 OR is_redeemed IS NULL)
      AND COALESCE(pnl, profit_loss, 0) <= 0
""").fetchone()
print(f"  Unredeemed LOSING shares: {r_unredeemed_lose['shares'] or 0:.0f} (cost=${r_unredeemed_lose['cost'] or 0:.2f}, n={r_unredeemed_lose['n']})")

# Biggest losses
print("\n=== TOP 10 BIGGEST LOSSES ===")
rows4 = db.execute("""
    SELECT slug, exit_reason, entry_price, exit_price,
           COALESCE(entry_size, entry_amount, 0) as size,
           COALESCE(pnl, profit_loss, 0) as pnl,
           date(entry_time, 'unixepoch') as day
    FROM trades
    WHERE COALESCE(pnl, profit_loss, 0) < 0
    ORDER BY COALESCE(pnl, profit_loss, 0) ASC
    LIMIT 10
""").fetchall()
for r in rows4:
    print(f"  ${r['pnl']:8.2f} | {r['slug']:35s} | {r['exit_reason']:15s} | entry=${r['entry_price']:.3f} exit=${r['exit_price'] or 0:.3f} | size={r['size']:.0f} | {r['day']}")

# NULL exit trades (still open?)
print("\n=== OPEN/UNSETTLED TRADES ===")
rows5 = db.execute("""
    SELECT slug, entry_price, COALESCE(entry_size, entry_amount, 0) as size,
           entry_price * COALESCE(entry_size, entry_amount, 0) as cost,
           date(entry_time, 'unixepoch') as day
    FROM trades
    WHERE pnl IS NULL AND profit_loss IS NULL
""").fetchall()
for r in rows5:
    print(f"  {r['slug']:35s} | entry=${r['entry_price']:.3f} | size={r['size']:.0f} | cost=${r['cost']:.2f} | {r['day']}")
