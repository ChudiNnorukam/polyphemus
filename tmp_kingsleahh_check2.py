#!/usr/bin/env python3
"""Check kingsleahh + lagbot positions and trades via multiple API endpoints."""
import os, json, requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path("/opt/lagbot/lagbot/.env"))

WALLET = "0x7E69be59E92a396EcCBba344CAe383927fcAD9Ad"  # kingsleahh Safe
LAGBOT_EOA = os.getenv("WALLET_ADDRESS", "0x1C0523D33b0D1c7Df8Ec450C5318cFcFc32Ce80A")
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
PROFIT_API = "https://profit-loss.polymarket.com"

def try_endpoints(addr, name):
    print(f"\n{'='*60}")
    print(f"=== {name}: {addr} ===")
    print(f"{'='*60}")

    # Try various Gamma API endpoints
    endpoints = [
        ("positions", f"{GAMMA_API}/positions?user={addr.lower()}&limit=100"),
        ("positions2", f"{GAMMA_API}/positions?user={addr}&limit=100"),
        ("activity", f"{GAMMA_API}/activity?user={addr.lower()}&limit=100"),
        ("activity2", f"{GAMMA_API}/activity?user={addr}&limit=100"),
        ("history", f"{GAMMA_API}/history?user={addr.lower()}&limit=100"),
        ("trades", f"{GAMMA_API}/trades?user={addr.lower()}&limit=100"),
        ("profile", f"{GAMMA_API}/profiles/{addr.lower()}"),
        ("profile2", f"{GAMMA_API}/profile/{addr.lower()}"),
        ("user-positions", f"{GAMMA_API}/user-positions?user={addr.lower()}"),
        ("portfolio", f"{GAMMA_API}/portfolio?user={addr.lower()}"),
        # CLOB endpoints
        ("clob-trades", f"{CLOB_API}/trades?maker_address={addr.lower()}&limit=100"),
        ("clob-orders", f"{CLOB_API}/orders?maker_address={addr.lower()}&limit=100"),
        # Profit-loss API
        ("profit-loss", f"{PROFIT_API}/retrievePnl?window=all&address={addr.lower()}"),
        ("profit-loss2", f"{PROFIT_API}/retrievePnl?window=all&address={addr}"),
    ]

    for label, url in endpoints:
        try:
            r = requests.get(url, timeout=10, headers={"Accept": "application/json"})
            if r.ok:
                data = r.json()
                if isinstance(data, list):
                    count = len(data)
                elif isinstance(data, dict):
                    count = f"dict:{list(data.keys())[:5]}"
                else:
                    count = type(data).__name__
                print(f"  OK   {label}: {count}")
                # Print first few items if list
                if isinstance(data, list) and len(data) > 0:
                    for item in data[:3]:
                        if isinstance(item, dict):
                            title = item.get("market", {}).get("question", item.get("title", item.get("slug", "")))[:50]
                            side = item.get("side", item.get("type", item.get("outcome", "")))
                            size = item.get("size", item.get("amount", ""))
                            price = item.get("price", item.get("avgPrice", ""))
                            pnl = item.get("cashPnl", item.get("pnl", ""))
                            cur = item.get("currentValue", item.get("value", ""))
                            ts = str(item.get("createdAt", item.get("timestamp", "")))[:19]
                            print(f"        {ts} | {side} | size={size} | price={price} | val={cur} | pnl={pnl} | {title}")
                elif isinstance(data, dict):
                    # Print key summary
                    for k, v in list(data.items())[:10]:
                        val_str = str(v)[:80]
                        print(f"        {k}: {val_str}")
            else:
                print(f"  {r.status_code:3d}  {label}")
        except Exception as e:
            print(f"  ERR  {label}: {e}")

def check_lagbot_db():
    """Check lagbot's local performance DB for trade history."""
    print(f"\n{'='*60}")
    print("=== LAGBOT LOCAL DB ===")
    print("='*60")

    import sqlite3
    db_path = "/opt/lagbot/data/performance.db"
    if not os.path.exists(db_path):
        print(f"  DB not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Get tables
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in c.fetchall()]
    print(f"  Tables: {tables}")

    for table in tables:
        c.execute(f"SELECT COUNT(*) FROM {table}")
        count = c.fetchone()[0]
        print(f"  {table}: {count} rows")

    # Get recent trades
    if "trades" in tables:
        c.execute("SELECT * FROM trades ORDER BY rowid DESC LIMIT 20")
        rows = c.fetchall()
        if rows:
            cols = [d[0] for d in c.description]
            print(f"\n  Last 20 trades (cols: {cols}):")
            for r in rows:
                d = dict(r)
                slug = d.get("slug", d.get("market_slug", "?"))[:40]
                side = d.get("side", d.get("outcome", "?"))
                size = d.get("size", d.get("shares", "?"))
                price = d.get("entry_price", d.get("price", "?"))
                pnl = d.get("pnl", d.get("profit", "?"))
                exit_reason = d.get("exit_reason", d.get("exit_type", "?"))
                ts = str(d.get("exit_time", d.get("timestamp", d.get("created_at", "?"))))[:19]
                print(f"    {ts} | {side:4s} | {size:>8s} | entry=${price} | pnl=${pnl} | {exit_reason:15s} | {slug}")

    # Get summary
    if "trades" in tables:
        c.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN CAST(pnl AS REAL) > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN CAST(pnl AS REAL) <= 0 THEN 1 ELSE 0 END) as losses,
                SUM(CAST(pnl AS REAL)) as total_pnl,
                AVG(CAST(pnl AS REAL)) as avg_pnl
            FROM trades
        """)
        row = c.fetchone()
        if row:
            d = dict(row)
            total = d["total"]
            wins = d["wins"] or 0
            losses = d["losses"] or 0
            total_pnl = d["total_pnl"] or 0
            avg_pnl = d["avg_pnl"] or 0
            wr = (wins/total*100) if total > 0 else 0
            print(f"\n  Summary: {total} trades, {wins}W/{losses}L ({wr:.1f}% WR)")
            print(f"  Total P&L: ${total_pnl:.2f}, Avg: ${avg_pnl:.4f}")

    # P&L by exit reason
    if "trades" in tables:
        c.execute("""
            SELECT exit_reason, COUNT(*) as cnt,
                   SUM(CAST(pnl AS REAL)) as total_pnl,
                   AVG(CAST(pnl AS REAL)) as avg_pnl
            FROM trades GROUP BY exit_reason ORDER BY total_pnl DESC
        """)
        rows = c.fetchall()
        if rows:
            print(f"\n  P&L by exit reason:")
            for r in rows:
                d = dict(r)
                print(f"    {d['exit_reason']:20s} | n={d['cnt']:4d} | pnl=${d['total_pnl']:+.2f} | avg=${d['avg_pnl']:+.4f}")

    conn.close()

if __name__ == "__main__":
    try_endpoints(LAGBOT_EOA, "Lagbot EOA")
    try_endpoints(WALLET, "Kingsleahh Safe")
    check_lagbot_db()
