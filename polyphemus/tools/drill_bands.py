#!/usr/bin/env python3
"""Drill into 00-55 artifact, 70-85 ambiguity, 55-70 carrier strategy.

IMPORTANT: apply the same filter as tools/verdict_all_bands.py:
  exit_reason IN ('market_resolved', 'phantom_resolved') AND pnl IS NOT NULL.
Otherwise cells are not apples-to-apples with the verdict table.
"""
import sqlite3
RESOLVED_FILTER = "exit_reason IN ('market_resolved','phantom_resolved') AND pnl IS NOT NULL"
con = sqlite3.connect("/opt/lagbot/instances/emmanuel/data/performance.db")
cur = con.cursor()

# discover columns
cur.execute("PRAGMA table_info(trades)")
cols = [r[1] for r in cur.fetchall()]
id_col = "slug" if "slug" in cols else ("market_slug" if "market_slug" in cols else cols[0])
print(f"Using id_col={id_col}, filter={RESOLVED_FILTER}\n")

# The $21,057 artifact — is it one trade? (resolved-only filter)
q = f"""SELECT {id_col}, entry_price, pnl, exit_reason, signal_source, strategy
FROM trades WHERE {RESOLVED_FILTER} AND entry_price > 0 AND entry_price < 0.55 AND pnl > 100
ORDER BY pnl DESC LIMIT 5"""
cur.execute(q)
print("Top PnL in 00-55 band (>$100):")
for r in cur.fetchall():
    sid = (r[0][:30] + "...") if r[0] and len(r[0]) > 30 else r[0]
    src = r[4] or "<null>"
    strat = r[5] or "<null>"
    print("  id={} entry={:.4f} pnl={:+.2f} reason={} source={} strat={}".format(
        sid, r[1], r[2], r[3], src, strat))

# Without the whale trade, what does 00-55 look like?
cur.execute("""SELECT COUNT(*), SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END), SUM(pnl), AVG(pnl)
FROM trades
WHERE exit_reason IN ('market_resolved','phantom_resolved') AND pnl IS NOT NULL
AND entry_price > 0 AND entry_price < 0.55 AND pnl < 100""")
n, w, tot, avg = cur.fetchone()
print("\n00-55 EXCLUDING pnl>$100 outliers:")
print("  n={} W={} (WR={:.3f}) sum_pnl={:+.2f} avg={:+.2f}".format(n, w, w/n if n else 0, tot, avg))

# 70-85 signal source breakdown
cur.execute("""SELECT COALESCE(signal_source,'<null>') AS src, COUNT(*),
SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END), SUM(pnl), AVG(pnl)
FROM trades WHERE exit_reason IN ('market_resolved','phantom_resolved') AND pnl IS NOT NULL
AND entry_price >= 0.70 AND entry_price < 0.85
GROUP BY src ORDER BY COUNT(*) DESC""")
print("\n70-85 by signal_source:")
for r in cur.fetchall():
    print("  src={:<22} n={:>3} W={:>3} WR={:.3f} pnl={:+.2f} avg={:+.2f}".format(
        r[0], r[1], r[2], r[2]/r[1], r[3], r[4]))

# 55-70 signal source breakdown
cur.execute("""SELECT COALESCE(signal_source,'<null>') AS src, COUNT(*),
SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END), SUM(pnl)
FROM trades WHERE exit_reason IN ('market_resolved','phantom_resolved') AND pnl IS NOT NULL
AND entry_price >= 0.55 AND entry_price < 0.70
GROUP BY src ORDER BY COUNT(*) DESC""")
print("\n55-70 by signal_source:")
for r in cur.fetchall():
    wr = r[2]/r[1] if r[1] else 0.0
    print("  src={:<22} n={:>3} W={:>3} WR={:.3f} pnl={:+.2f}".format(
        r[0], r[1], r[2], wr, r[3]))

# 97+ worst losers — chalk is "one big loss kills 50 wins" territory
q2 = f"""SELECT {id_col}, entry_price, pnl, exit_reason, signal_source
FROM trades WHERE exit_reason IN ('market_resolved','phantom_resolved') AND pnl IS NOT NULL
AND entry_price >= 0.97 AND pnl < -5
ORDER BY pnl LIMIT 10"""
cur.execute(q2)
print("\n97+ worst losers (pnl < -$5):")
for r in cur.fetchall():
    sid = (r[0][:30] + "...") if r[0] and len(r[0]) > 30 else r[0]
    src = r[4] or "<null>"
    print("  id={} entry={:.4f} pnl={:+.2f} reason={} source={}".format(
        sid, r[1], r[2], r[3], src))

# 85-93 source breakdown (need this for verdict)
cur.execute("""SELECT COALESCE(signal_source,'<null>') AS src, COUNT(*),
SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END), SUM(pnl), AVG(pnl)
FROM trades WHERE exit_reason IN ('market_resolved','phantom_resolved') AND pnl IS NOT NULL
AND entry_price >= 0.85 AND entry_price < 0.93
GROUP BY src ORDER BY COUNT(*) DESC""")
print("\n85-93 by signal_source:")
for r in cur.fetchall():
    print("  src={:<22} n={:>3} W={:>3} WR={:.3f} pnl={:+.2f} avg={:+.2f}".format(
        r[0], r[1], r[2], r[2]/r[1], r[3], r[4]))
