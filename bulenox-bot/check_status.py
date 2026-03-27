#!/usr/bin/env python3
"""Quick status check for BulenoxBot. Shows net-of-costs P&L."""
import sqlite3
import os
import json

DB = "data/trades.db"
STATE = "data/bot_state.json"
COST_RT = 5.52  # Bulenox all-in rate: $2.76/side (verified from Rates.pdf Mar 25 2026)
EST_SLIPPAGE = 1.00  # Reduced: slippage already partially in all-in rate
PTS_TO_DOLLARS = 0.10  # MBT: 1 price point = $0.10 (5 pts/tick * $0.50/tick = $0.10/pt)

if not os.path.exists(DB):
    print("No trades.db found")
    exit(1)

con = sqlite3.connect(DB)

total = con.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
closed = con.execute("SELECT COUNT(*) FROM trades WHERE exit_ts IS NOT NULL").fetchone()[0]
open_count = con.execute("SELECT COUNT(*) FROM trades WHERE exit_ts IS NULL").fetchone()[0]
total_pnl = con.execute("SELECT COALESCE(SUM(pnl_pts), 0) FROM trades WHERE exit_ts IS NOT NULL").fetchone()[0]
wins = con.execute("SELECT COUNT(*) FROM trades WHERE pnl_pts > 0").fetchone()[0]
losses = con.execute("SELECT COUNT(*) FROM trades WHERE pnl_pts < 0").fetchone()[0]
wr = (wins / closed * 100) if closed > 0 else 0

gross_dollars = total_pnl * PTS_TO_DOLLARS
total_costs = closed * COST_RT
total_slippage = closed * EST_SLIPPAGE
net_pnl = gross_dollars - total_costs - total_slippage
avg_net = net_pnl / closed if closed > 0 else 0

print("=== BulenoxBot Status ===")
print(f"Total trades: {total} | Closed: {closed} | Open: {open_count}")
print(f"Wins: {wins} | Losses: {losses} | WR: {wr:.1f}%")
print()
print(f"P&L (gross pts):   {total_pnl:+,.2f} pts")
print(f"P&L (gross $):     ${gross_dollars:+,.2f} (pts x ${PTS_TO_DOLLARS}/pt)")
print(f"Costs ({closed} trades):  -${total_costs:,.2f} (@ ${COST_RT}/RT)")
print(f"Est. slippage:     -${total_slippage:,.2f} (@ ${EST_SLIPPAGE}/RT)")
print(f"P&L (net):         ${net_pnl:+,.2f}")
print(f"Avg net/trade:     ${avg_net:+,.2f}")
print(f"Backtest expected: $+2.62/trade")
print()

if os.path.exists(STATE):
    with open(STATE) as f:
        state = json.load(f)
    print(f"Peak balance: ${state.get('peak_balance', 0):,.2f}")
    print(f"Consecutive losses: {state.get('consecutive_losses', 0)}")
    print(f"Halted: {state.get('halted', False)}")
    print()

mfe_rows = con.execute("SELECT mfe_ticks FROM trades WHERE mfe_ticks IS NOT NULL AND exit_ts IS NOT NULL").fetchall()
mae_rows = con.execute("SELECT mae_ticks FROM trades WHERE mae_ticks IS NOT NULL AND exit_ts IS NOT NULL").fetchall()
if mfe_rows:
    avg_mfe = sum(r[0] for r in mfe_rows) / len(mfe_rows)
    avg_mae = sum(r[0] for r in mae_rows) / len(mae_rows) if mae_rows else 0
    print(f"Avg MFE: {avg_mfe:+.0f} ticks | Avg MAE: {avg_mae:+.0f} ticks")
    print()

rows = con.execute(
    "SELECT basket_id, direction, side, entry_price, exit_price, pnl_pts, "
    "datetime(entry_ts, 'unixepoch', 'localtime'), exit_reason, mfe_ticks, mae_ticks "
    "FROM trades ORDER BY id DESC LIMIT 10"
).fetchall()

if rows:
    print(f"--- Last {len(rows)} trades ---")
    for r in rows:
        bid, dirn, side, ep, xp, pnl, ts, reason, mfe, mae = r
        gross_d = f"${pnl * PTS_TO_DOLLARS:+.2f}" if pnl else "OPEN"
        net_t = f"${pnl * PTS_TO_DOLLARS - COST_RT - EST_SLIPPAGE:+.2f}" if pnl else "OPEN"
        xp_str = f"{xp:.2f}" if xp else "--"
        mfe_s = f"MFE={mfe:+.0f}" if mfe else ""
        mae_s = f"MAE={mae:+.0f}" if mae else ""
        print(f"  {ts} | {dirn:4s} | entry={ep:.2f} exit={xp_str} | {gross_d} net={net_t} | {mfe_s} {mae_s} | {reason or ''}")

con.close()
