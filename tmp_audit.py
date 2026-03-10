#!/usr/bin/env python3
"""Audit performance DB to trace capital flows."""
import sqlite3
from datetime import datetime

db = sqlite3.connect('/opt/polyphemus/data/performance.db')
db.row_factory = sqlite3.Row

print("=== P&L BY EXIT REASON ===")
rows = db.execute("""
    SELECT exit_reason,
           COUNT(*) as count,
           SUM(COALESCE(pnl, profit_loss, 0)) as total_pnl,
           AVG(COALESCE(pnl, profit_loss, 0)) as avg_pnl
    FROM trades
    WHERE exit_reason IS NOT NULL
    GROUP BY exit_reason
    ORDER BY total_pnl
""").fetchall()
for r in rows:
    print(f"  {r['exit_reason']:20s} | n={r['count']:4d} | total=${r['total_pnl']:8.2f} | avg=${r['avg_pnl']:6.2f}")

print("\n=== OVERALL ===")
r = db.execute("""
    SELECT COUNT(*) as n,
           SUM(COALESCE(pnl, profit_loss, 0)) as total,
           SUM(CASE WHEN COALESCE(pnl, profit_loss, 0) > 0 THEN 1 ELSE 0 END) as wins,
           SUM(CASE WHEN COALESCE(pnl, profit_loss, 0) < 0 THEN 1 ELSE 0 END) as losses,
           SUM(entry_price * COALESCE(entry_size, entry_amount, 0)) as total_wagered
    FROM trades
""").fetchone()
print(f"  Total trades: {r['n']}")
print(f"  Total P&L: ${r['total']:.2f}")
print(f"  Wins: {r['wins']}, Losses: {r['losses']}")
print(f"  Total wagered: ${r['total_wagered']:.2f}")

# Trades with NULL pnl
r2 = db.execute("SELECT COUNT(*) as c FROM trades WHERE pnl IS NULL AND profit_loss IS NULL").fetchone()
print(f"  Trades with NULL P&L: {r2['c']}")

r3 = db.execute("SELECT SUM(entry_price * COALESCE(entry_size, entry_amount, 0)) as s FROM trades WHERE pnl IS NULL AND profit_loss IS NULL").fetchone()
print(f"  Capital in unsettled trades: ${r3['s'] or 0:.2f}")

# Date range
r4 = db.execute("SELECT MIN(entry_time) as first_t, MAX(entry_time) as last_t FROM trades").fetchone()
if r4['first_t']:
    print(f"  First trade: {datetime.fromtimestamp(r4['first_t'])}")
if r4['last_t']:
    print(f"  Last trade: {datetime.fromtimestamp(r4['last_t'])}")

# P&L by day
print("\n=== P&L BY DAY ===")
rows = db.execute("""
    SELECT date(entry_time, 'unixepoch') as day,
           COUNT(*) as n,
           SUM(COALESCE(pnl, profit_loss, 0)) as pnl,
           SUM(CASE WHEN COALESCE(pnl, profit_loss, 0) > 0 THEN 1 ELSE 0 END) as wins,
           SUM(CASE WHEN COALESCE(pnl, profit_loss, 0) < 0 THEN 1 ELSE 0 END) as losses
    FROM trades
    GROUP BY day
    ORDER BY day
""").fetchall()
for r in rows:
    print(f"  {r['day']} | n={r['n']:4d} | pnl=${r['pnl']:8.2f} | W={r['wins']} L={r['losses']}")

# Check schema
print("\n=== TABLE SCHEMA ===")
cols = db.execute("PRAGMA table_info(trades)").fetchall()
for c in cols:
    print(f"  {c['name']:20s} {c['type']}")

# Check if there are other tables
print("\n=== ALL TABLES ===")
tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
for t in tables:
    count = db.execute(f"SELECT COUNT(*) as c FROM [{t['name']}]").fetchone()
    print(f"  {t['name']}: {count['c']} rows")
