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
    condition_id TEXT,
    resolution_source TEXT,
    resolved INTEGER DEFAULT 0,
    resolution_outcome TEXT CHECK(resolution_outcome IN ('YES', 'NO', NULL)),
    resolution_price REAL,
    resolved_at TEXT,
    pnl REAL,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_paper_trades_date ON paper_trades(market_date);
CREATE INDEX IF NOT EXISTS idx_paper_trades_resolved ON paper_trades(resolved);

CREATE TABLE IF NOT EXISTS historical_scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scanned_at TEXT NOT NULL,
    city TEXT NOT NULL,
    market_date TEXT NOT NULL,
    temp INTEGER NOT NULL,
    unit TEXT NOT NULL DEFAULT 'C',
    direction TEXT NOT NULL,
    question_type TEXT NOT NULL DEFAULT 'bucket',
    question TEXT,
    midpoint_price REAL NOT NULL,
    execution_price REAL NOT NULL,
    forecast_prob REAL NOT NULL,
    forecast_temp REAL,
    edge REAL NOT NULL,
    ev_gross REAL,
    ev_net REAL NOT NULL,
    fee REAL,
    kelly REAL NOT NULL,
    days_until INTEGER,
    token_id TEXT,
    condition_id TEXT,
    recorded INTEGER DEFAULT 0,
    skip_reason TEXT,
    resolved INTEGER DEFAULT 0,
    resolution_outcome TEXT CHECK(resolution_outcome IN ('YES', 'NO', NULL)),
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_hist_scans_date ON historical_scans(market_date);
CREATE INDEX IF NOT EXISTS idx_hist_scans_city ON historical_scans(city, market_date);
CREATE INDEX IF NOT EXISTS idx_hist_scans_resolved ON historical_scans(resolved);
"""

_MIGRATIONS = [
    "ALTER TABLE paper_trades ADD COLUMN condition_id TEXT",
    "ALTER TABLE paper_trades ADD COLUMN resolution_source TEXT",
]


def get_db(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    # Run migrations for existing DBs missing new columns
    for sql in _MIGRATIONS:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # Column already exists
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
    condition_id: str | None = None,
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
         kelly, hypothetical_size, token_id, condition_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (now, city, market_date, temp, unit, direction, question_type,
         question, market_price, forecast_prob, forecast_temp, edge, ev_net,
         kelly, size, token_id, condition_id),
    )
    conn.commit()
    trade_id = cursor.lastrowid
    logger.info("Recorded paper trade #%d: %s %s %d%s %s @ %.3f (size=$%.2f)",
                trade_id, direction, city, temp, unit, market_date, market_price, size)
    return trade_id


def log_scan_result(
    conn: sqlite3.Connection,
    opp: dict,
    recorded: bool = False,
    skip_reason: str | None = None,
) -> None:
    """Log every scan result to historical_scans for backtesting.

    Called for ALL opportunities from the scanner, regardless of whether
    they pass filters. This builds the historical dataset needed for
    backtesting, parameter sweeps, and model comparison.
    """
    from .detector import classify_question

    now = datetime.now(timezone.utc).isoformat()
    q_type = classify_question(opp.get("question", ""))

    conn.execute(
        """INSERT INTO historical_scans
        (scanned_at, city, market_date, temp, unit, direction, question_type,
         question, midpoint_price, execution_price, forecast_prob, forecast_temp,
         edge, ev_gross, ev_net, fee, kelly, days_until,
         token_id, condition_id, recorded, skip_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (now, opp.get("city", ""), opp.get("date", ""), opp["temp"],
         opp.get("unit", "C"), opp["direction"], q_type,
         opp.get("question"), opp.get("midpoint_price", opp["market_price"]),
         opp["market_price"], opp["forecast_prob"],
         opp.get("forecast_temp"), opp["edge"],
         opp.get("ev_gross"), opp["ev_net"],
         opp.get("fee"), opp.get("kelly", 0.0),
         opp.get("days_until"),
         opp.get("token_id"), opp.get("condition_id"),
         1 if recorded else 0, skip_reason),
    )
    # Batch commit handled by caller


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
        condition_id=opp.get("condition_id"),
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

    if direction == "BUY":
        # BUY YES: pay 'price' per share, receive $1 if YES
        shares = size / price if price > 0 else 0
        if outcome == "YES":
            pnl = shares * (1.0 - price) - _fee(price, shares)
        else:
            pnl = -size
    else:  # SELL (buy NO tokens)
        # SELL YES = BUY NO: pay '1-price' per NO share, receive $1 if NO
        no_price = 1.0 - price
        shares = size / no_price if no_price > 0 else 0
        if outcome == "NO":
            pnl = shares * price - _fee(no_price, shares)
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


def print_history(conn: sqlite3.Connection) -> None:
    """Print historical scan statistics for backtesting readiness."""
    total = conn.execute("SELECT COUNT(*) FROM historical_scans").fetchone()[0]
    if total == 0:
        print("No historical scans yet. Run 'auto-record' to start collecting data.")
        return

    print(f"\n{'=' * 50}")
    print("HISTORICAL SCAN DATABASE")
    print(f"{'=' * 50}")
    print(f"Total scan results: {total}")

    # Date range
    row = conn.execute(
        "SELECT MIN(scanned_at), MAX(scanned_at), COUNT(DISTINCT market_date) FROM historical_scans"
    ).fetchone()
    print(f"Date range: {row[0][:10]} to {row[1][:10]} | {row[2]} unique market dates")

    # By skip reason
    print(f"\n--- Filter Breakdown ---")
    rows = conn.execute(
        "SELECT COALESCE(skip_reason, 'RECORDED') as reason, COUNT(*) as n "
        "FROM historical_scans GROUP BY reason ORDER BY n DESC"
    ).fetchall()
    for r in rows:
        print(f"  {r['reason']:>15}: {r['n']:>5}")

    # By direction
    print(f"\n--- By Direction ---")
    rows = conn.execute(
        "SELECT direction, COUNT(*) as n, "
        "ROUND(AVG(execution_price), 3) as avg_price, "
        "ROUND(AVG(edge), 4) as avg_edge "
        "FROM historical_scans GROUP BY direction"
    ).fetchall()
    for r in rows:
        print(f"  {r['direction']}: {r['n']} scans, avg price ${r['avg_price']}, avg edge {r['avg_edge']:+.4f}")

    # By city (top 10)
    print(f"\n--- Top 10 Cities ---")
    rows = conn.execute(
        "SELECT city, COUNT(*) as n FROM historical_scans GROUP BY city ORDER BY n DESC LIMIT 10"
    ).fetchall()
    for r in rows:
        print(f"  {r['city']:>15}: {r['n']:>5}")

    # Recorded vs total (pass rate)
    recorded = conn.execute("SELECT COUNT(*) FROM historical_scans WHERE recorded = 1").fetchone()[0]
    print(f"\nFilter pass rate: {recorded}/{total} ({100*recorded/total:.1f}%)")
    print(f"Backtest-ready rows: {total} (all scans, regardless of filter)")


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
    sub.add_parser("history", help="Show historical scan statistics")

    auto = sub.add_parser("auto-record", help="Run scanner and auto-record qualifying trades")
    auto.add_argument("--threshold", type=float, default=0.10)
    auto.add_argument("--min-ev", type=float, default=0.01)
    auto.add_argument("--min-kelly", type=float, default=0.15)
    auto.add_argument("--max-per-day", type=int, default=30,
                       help="Max trades to record per market date (default: 30)")
    auto.add_argument("--cumulative-only", action="store_true", default=True,
                       help="Only record cumulative markets (or higher/or lower), skip bucket/range (default: true)")
    auto.add_argument("--all-types", action="store_true",
                       help="Record all question types including buckets (overrides --cumulative-only)")
    auto.add_argument("--sell-only", action="store_true", default=True,
                       help="Only record SELL direction trades (default: true, BUY is 1/11 WR)")
    auto.add_argument("--allow-buy", action="store_true",
                       help="Allow BUY trades (overrides --sell-only)")
    auto.add_argument("--min-price", type=float, default=0.20,
                       help="Minimum market price to record (default: 0.20; narrowed 2026-04-16 after resolver dry-run: 0.10-0.20 bucket losing money despite high WR)")
    auto.add_argument("--max-price", type=float, default=0.30,
                       help="Maximum market price to record (default: 0.30; narrowed 2026-04-16 after resolver dry-run: 0.30-0.40 bucket below break-even)")
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

    elif args.command == "history":
        print_history(conn)

    elif args.command == "auto-record":
        import asyncio
        from .main import run
        asyncio.run(_auto_record(conn, args))

    else:
        parser.print_help()

    conn.close()


async def _auto_record(conn: sqlite3.Connection, args) -> None:
    """Run scanner and auto-record qualifying opportunities.

    LOCKED PARAMETERS (Think Tank gate 2026-04-13, do NOT change until n=50 checkpoint):
      - Direction: SELL only (BUY is 1/11 WR)
      - Price range: $0.10-$0.50 (execution price, after spread haircut)
      - Question type: cumulative only (or higher / or lower)
      - Min Kelly: 0.15
      - Min edge threshold: 0.10
      - Min EV: $0.01
      - Max per day: 30
      - Stale price filter: skip midpoint $0.48-$0.52
      - Gaussian model: std_dev=1.5C, horizon scaling sqrt(days)
      - Fee coefficient: 0.05
      - Spread haircut: 0.015
    Kill criteria at n=50: WR <65% = KILL, 65-75% = extend to n=100, >75% = go live $500 max.
    Break-even WR: 72.4% avg (73.0% with 2% live fill degradation).
    """
    from .main import run

    opps = await run(threshold=args.threshold, min_ev=args.min_ev, verbose=False)
    recorded = 0
    skipped_kelly = 0
    skipped_stale = 0
    skipped_dup = 0
    skipped_cap = 0
    skipped_bucket = 0
    skipped_direction = 0
    skipped_price_range = 0

    cumulative_only = getattr(args, "cumulative_only", True) and not getattr(args, "all_types", False)
    sell_only = getattr(args, "sell_only", True) and not getattr(args, "allow_buy", False)
    min_price = getattr(args, "min_price", 0.0)
    max_price = getattr(args, "max_price", 1.0)

    # Track per-day counts to enforce cap
    day_counts: dict[str, int] = {}
    max_per_day = getattr(args, "max_per_day", 30)

    logged_scans = 0

    for opp in opps:
        # Determine skip reason (first matching filter), then log ALL to historical_scans
        skip = None

        if opp.get("kelly", 0) < args.min_kelly:
            skip = "kelly_low"
            skipped_kelly += 1
        elif sell_only and opp["direction"] != "SELL":
            skip = "direction_buy"
            skipped_direction += 1
        else:
            exec_price = opp["market_price"]
            if exec_price < min_price or exec_price > max_price:
                skip = "price_range"
                skipped_price_range += 1
            elif cumulative_only:
                from .detector import classify_question
                q_type = classify_question(opp.get("question", ""))
                if q_type not in ("cumulative_higher", "cumulative_lower"):
                    skip = "bucket_type"
                    skipped_bucket += 1
            if skip is None:
                midpoint = opp.get("midpoint_price", opp["market_price"])
                if 0.48 <= midpoint <= 0.52:
                    skip = "stale_price"
                    skipped_stale += 1

        if skip is None:
            market_date = opp.get("date", "")
            day_counts.setdefault(market_date, 0)
            if day_counts[market_date] >= max_per_day:
                skip = "day_cap"
                skipped_cap += 1
            else:
                existing = conn.execute(
                    "SELECT id FROM paper_trades WHERE city = ? AND market_date = ? AND temp = ? AND direction = ? AND resolved = 0",
                    (opp["city"], market_date, opp["temp"], opp["direction"]),
                ).fetchone()
                if existing:
                    skip = "duplicate"
                    skipped_dup += 1

        # Log to historical_scans (ALL opportunities, before/after filtering)
        log_scan_result(conn, opp, recorded=(skip is None), skip_reason=skip)
        logged_scans += 1

        if skip is not None:
            continue

        # Record the qualifying trade
        trade_id = record_from_opportunity(conn, opp, bankroll=args.bankroll)
        day_counts[opp.get("date", "")] += 1
        print(f"  RECORDED #{trade_id}: {opp['direction']} {opp['city']} "
              f"{opp['temp']}{chr(176)}{opp.get('unit','')} {opp['date']} "
              f"@ {opp['market_price']:.3f} (edge={opp['edge']:+.3f}, kelly={opp['kelly']:.1%})")
        recorded += 1

    conn.commit()  # Batch commit for historical_scans

    print(f"\nRecorded {recorded} new paper trades. Logged {logged_scans} scan results to history.")
    if skipped_direction:
        print(f"  Skipped {skipped_direction} (direction filter - SELL only)")
    if skipped_price_range:
        print(f"  Skipped {skipped_price_range} (price outside ${min_price:.2f}-${max_price:.2f})")
    if skipped_bucket:
        print(f"  Skipped {skipped_bucket} (bucket/range - cumulative only)")
    if skipped_kelly:
        print(f"  Skipped {skipped_kelly} (kelly < {args.min_kelly})")
    if skipped_stale:
        print(f"  Skipped {skipped_stale} (stale/default price ~$0.50)")
    if skipped_dup:
        print(f"  Skipped {skipped_dup} (duplicate)")
    if skipped_cap:
        print(f"  Skipped {skipped_cap} (per-day cap of {max_per_day})")
    print_summary(conn)


if __name__ == "__main__":
    main()
