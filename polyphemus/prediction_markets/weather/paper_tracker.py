"""Paper trading tracker for weather market strategy.

Records hypothetical trades and tracks resolution outcomes.
Stores all data in SQLite for analysis.

Usage:
    # Record a paper trade from scanner output
    python -m prediction_markets.weather.paper_tracker record \
        --city seoul --date 2026-04-13 --temp 23 --direction BUY \
        --market-price 0.056 --forecast-prob 0.788 --edge 0.735 \
        --kelly 0.776 --question "Will Seoul be 23C or higher?"

    # Update resolution outcomes
    python -m prediction_markets.weather.paper_tracker resolve

    # Show performance summary
    python -m prediction_markets.weather.paper_tracker summary
"""
import argparse
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DB = Path(__file__).parent / "data" / "paper_trades.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    city TEXT NOT NULL,
    market_date TEXT NOT NULL,
    temp INTEGER NOT NULL,
    unit TEXT NOT NULL DEFAULT 'C',
    direction TEXT NOT NULL CHECK(direction IN ('BUY', 'SELL')),
    question_type TEXT NOT NULL DEFAULT 'bucket',
    question TEXT,
    market_price REAL NOT NULL,
    forecast_prob REAL NOT NULL,
    forecast_temp REAL,
    edge REAL NOT NULL,
    ev_net REAL NOT NULL,
    kelly REAL NOT NULL,
    hypothetical_size REAL NOT NULL DEFAULT 0,
    token_id TEXT,
    resolved INTEGER DEFAULT 0,
    resolution_outcome TEXT CHECK(resolution_outcome IN ('YES', 'NO', NULL)),
    resolution_price REAL,
    resolved_at TEXT,
    pnl REAL,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_paper_trades_date ON paper_trades(market_date);
CREATE INDEX IF NOT EXISTS idx_paper_trades_resolved ON paper_trades(resolved);
"""


def get_db(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def record_trade(
    conn: sqlite3.Connection,
    city: str,
    market_date: str,
    temp: int,
    direction: str,
    market_price: float,
    forecast_prob: float,
    edge: float,
    ev_net: float,
    kelly: float,
    unit: str = "C",
    question_type: str = "bucket",
    question: str | None = None,
    forecast_temp: float | None = None,
    token_id: str | None = None,
    bankroll: float = 200.0,
) -> int:
    """Record a paper trade. Returns the trade ID."""
    half_kelly = min(kelly * 0.5, 0.10)  # cap at 10% of bankroll
    size = round(half_kelly * bankroll, 2)

    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """INSERT INTO paper_trades
        (created_at, city, market_date, temp, unit, direction, question_type,
         question, market_price, forecast_prob, forecast_temp, edge, ev_net,
         kelly, hypothetical_size, token_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (now, city, market_date, temp, unit, direction, question_type,
         question, market_price, forecast_prob, forecast_temp, edge, ev_net,
         kelly, size, token_id),
    )
    conn.commit()
    trade_id = cursor.lastrowid
    logger.info("Recorded paper trade #%d: %s %s %d%s %s @ %.3f (size=$%.2f)",
                trade_id, direction, city, temp, unit, market_date, market_price, size)
    return trade_id


def record_from_opportunity(conn: sqlite3.Connection, opp: dict, bankroll: float = 200.0) -> int:
    """Record a paper trade from a scanner opportunity dict."""
    from .detector import classify_question
    q_type = classify_question(opp.get("question", ""))

    return record_trade(
        conn=conn,
        city=opp.get("city", ""),
        market_date=opp.get("date", ""),
        temp=opp["temp"],
        direction=opp["direction"],
        market_price=opp["market_price"],
        forecast_prob=opp["forecast_prob"],
        edge=opp["edge"],
        ev_net=opp["ev_net"],
        kelly=opp.get("kelly", 0.0),
        unit=opp.get("unit", "C"),
        question_type=q_type,
        question=opp.get("question"),
        forecast_temp=opp.get("forecast_temp"),
        token_id=opp.get("token_id"),
        bankroll=bankroll,
    )


def resolve_trade(conn: sqlite3.Connection, trade_id: int, outcome: str) -> float:
    """Resolve a paper trade. Returns P&L."""
    row = conn.execute("SELECT * FROM paper_trades WHERE id = ?", (trade_id,)).fetchone()
    if not row:
        raise ValueError(f"Trade {trade_id} not found")
    if row["resolved"]:
        raise ValueError(f"Trade {trade_id} already resolved")

    direction = row["direction"]
    price = row["market_price"]
    size = row["hypothetical_size"]
    shares = size / price if price > 0 else 0

    if direction == "BUY":
        if outcome == "YES":
            pnl = shares * (1.0 - price) - _fee(price, shares)
        else:
            pnl = -size
    else:  # SELL
        if outcome == "NO":
            pnl = shares * price - _fee(price, shares)
        else:
            pnl = -size

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """UPDATE paper_trades
        SET resolved = 1, resolution_outcome = ?, resolution_price = ?,
            resolved_at = ?, pnl = ?
        WHERE id = ?""",
        (outcome, 1.0 if outcome == "YES" else 0.0, now, round(pnl, 4), trade_id),
    )
    conn.commit()
    return pnl


def _fee(price: float, shares: float) -> float:
    """Weather market fee."""
    return 0.05 * price * (1.0 - price) * shares


def summary(conn: sqlite3.Connection) -> dict:
    """Generate paper trading performance summary."""
    rows = conn.execute("SELECT * FROM paper_trades ORDER BY created_at").fetchall()
    total = len(rows)
    resolved = [r for r in rows if r["resolved"]]
    unresolved = [r for r in rows if not r["resolved"]]

    if not resolved:
        return {
            "total_trades": total,
            "resolved": 0,
            "unresolved": len(unresolved),
            "pnl": 0.0,
            "win_rate": None,
            "avg_edge": sum(r["edge"] for r in rows) / total if total else 0,
        }

    wins = sum(1 for r in resolved if (r["pnl"] or 0) > 0)
    total_pnl = sum(r["pnl"] or 0 for r in resolved)
    avg_edge = sum(abs(r["edge"]) for r in resolved) / len(resolved)

    return {
        "total_trades": total,
        "resolved": len(resolved),
        "unresolved": len(unresolved),
        "wins": wins,
        "losses": len(resolved) - wins,
        "win_rate": round(wins / len(resolved), 4) if resolved else None,
        "total_pnl": round(total_pnl, 2),
        "avg_pnl_per_trade": round(total_pnl / len(resolved), 4),
        "avg_edge": round(avg_edge, 4),
        "total_hypothetical_size": round(sum(r["hypothetical_size"] for r in resolved), 2),
    }


def print_summary(conn: sqlite3.Connection) -> None:
    """Print formatted performance summary."""
    s = summary(conn)
    print(f"\n{'=' * 50}")
    print("WEATHER PAPER TRADING SUMMARY")
    print(f"{'=' * 50}")
    print(f"Total trades: {s['total_trades']}")
    print(f"Resolved: {s['resolved']} | Unresolved: {s.get('unresolved', 0)}")

    if s["resolved"] > 0:
        print(f"Wins: {s['wins']} | Losses: {s['losses']}")
        print(f"Win rate: {s['win_rate']:.1%}")
        print(f"Total P&L: ${s['total_pnl']:+.2f}")
        print(f"Avg P&L/trade: ${s['avg_pnl_per_trade']:+.4f}")
        print(f"Avg |edge|: {s['avg_edge']:.4f}")
        print(f"Total hypothetical size: ${s['total_hypothetical_size']:.2f}")
    else:
        print("No resolved trades yet.")

    # Show unresolved trades
    rows = conn.execute(
        "SELECT * FROM paper_trades WHERE resolved = 0 ORDER BY market_date"
    ).fetchall()
    if rows:
        print(f"\n--- OPEN POSITIONS ({len(rows)}) ---")
        for r in rows:
            print(f"  #{r['id']} {r['direction']} {r['city']} {r['temp']}{chr(176)}{r['unit']} "
                  f"{r['market_date']} @ {r['market_price']:.3f} "
                  f"(edge={r['edge']:+.3f}, size=${r['hypothetical_size']:.2f})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Weather paper trading tracker")
    sub = parser.add_subparsers(dest="command")

    rec = sub.add_parser("record", help="Record a paper trade")
    rec.add_argument("--city", required=True)
    rec.add_argument("--date", required=True)
    rec.add_argument("--temp", type=int, required=True)
    rec.add_argument("--direction", choices=["BUY", "SELL"], required=True)
    rec.add_argument("--market-price", type=float, required=True)
    rec.add_argument("--forecast-prob", type=float, required=True)
    rec.add_argument("--edge", type=float, required=True)
    rec.add_argument("--ev-net", type=float, default=0.0)
    rec.add_argument("--kelly", type=float, default=0.0)
    rec.add_argument("--unit", default="C")
    rec.add_argument("--question", default=None)
    rec.add_argument("--token-id", default=None)
    rec.add_argument("--bankroll", type=float, default=200.0)

    res = sub.add_parser("resolve", help="Resolve a trade")
    res.add_argument("--id", type=int, required=True)
    res.add_argument("--outcome", choices=["YES", "NO"], required=True)

    sub.add_parser("summary", help="Show performance summary")

    auto = sub.add_parser("auto-record", help="Run scanner and auto-record qualifying trades")
    auto.add_argument("--threshold", type=float, default=0.10)
    auto.add_argument("--min-ev", type=float, default=0.01)
    auto.add_argument("--min-kelly", type=float, default=0.05)
    auto.add_argument("--bankroll", type=float, default=200.0)

    args = parser.parse_args()
    conn = get_db()

    if args.command == "record":
        trade_id = record_trade(
            conn, args.city, args.date, args.temp, args.direction,
            args.market_price, args.forecast_prob, args.edge, args.ev_net,
            args.kelly, args.unit, question=args.question, token_id=args.token_id,
            bankroll=args.bankroll,
        )
        print(f"Recorded paper trade #{trade_id}")

    elif args.command == "resolve":
        pnl = resolve_trade(conn, args.id, args.outcome)
        print(f"Trade #{args.id} resolved {args.outcome}: P&L = ${pnl:+.4f}")

    elif args.command == "summary":
        print_summary(conn)

    elif args.command == "auto-record":
        import asyncio
        from .main import run
        asyncio.run(_auto_record(conn, args))

    else:
        parser.print_help()

    conn.close()


async def _auto_record(conn: sqlite3.Connection, args) -> None:
    """Run scanner and auto-record qualifying opportunities."""
    from .main import run

    opps = await run(threshold=args.threshold, min_ev=args.min_ev, verbose=False)
    recorded = 0

    for opp in opps:
        if opp.get("kelly", 0) < args.min_kelly:
            continue
        if opp["direction"] != "BUY":
            continue  # Phase 1: BUY only (simpler execution)

        # Check for duplicate
        existing = conn.execute(
            "SELECT id FROM paper_trades WHERE city = ? AND market_date = ? AND temp = ? AND resolved = 0",
            (opp["city"], opp["date"], opp["temp"]),
        ).fetchone()
        if existing:
            print(f"  SKIP duplicate: {opp['city']} {opp['temp']} {opp['date']} (trade #{existing['id']})")
            continue

        trade_id = record_from_opportunity(conn, opp, bankroll=args.bankroll)
        print(f"  RECORDED #{trade_id}: {opp['direction']} {opp['city']} "
              f"{opp['temp']}{chr(176)}{opp.get('unit','')} {opp['date']} "
              f"@ {opp['market_price']:.3f} (edge={opp['edge']:+.3f}, kelly={opp['kelly']:.1%})")
        recorded += 1

    print(f"\nRecorded {recorded} new paper trades.")
    print_summary(conn)


if __name__ == "__main__":
    main()
