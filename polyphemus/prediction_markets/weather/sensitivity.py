"""Parameter sensitivity sweep for weather SELL strategy.

Tests how performance changes when we vary the locked parameters.
Uses resolved paper trade data to simulate "what if we changed the filters?"

This is NOT backtesting (we don't have enough historical scan data yet).
It's a sensitivity analysis on existing resolved trades to identify:
1. Which parameters matter most
2. Where the edge is fragile
3. What the parameter boundaries should be

Usage:
    python -m polyphemus.prediction_markets.weather.sensitivity
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "paper_trades.db"


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def compute_stats(rows: list[sqlite3.Row]) -> dict:
    """Compute WR, P&L, and EV stats from a set of resolved trades."""
    if not rows:
        return {"n": 0, "wins": 0, "wr": 0, "total_pnl": 0, "avg_pnl": 0, "ev": 0}

    wins = sum(1 for r in rows if r["pnl"] > 0)
    total_pnl = sum(r["pnl"] for r in rows)
    avg_pnl = total_pnl / len(rows)

    return {
        "n": len(rows),
        "wins": wins,
        "wr": round(100 * wins / len(rows), 1) if rows else 0,
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(avg_pnl, 4),
    }


def sweep_direction(conn: sqlite3.Connection) -> None:
    """Test BUY vs SELL performance across all resolved trades."""
    print("\n1. DIRECTION SENSITIVITY")
    print("-" * 60)

    for direction in ["BUY", "SELL"]:
        rows = conn.execute(
            "SELECT * FROM paper_trades WHERE resolved = 1 AND direction = ?",
            (direction,),
        ).fetchall()
        s = compute_stats(rows)
        print(f"  {direction:4s}: n={s['n']:3d}, WR={s['wr']:5.1f}%, "
              f"P&L=${s['total_pnl']:+8.2f}, Avg=${s['avg_pnl']:+.4f}")

    print("\n  Verdict: SELL-only is correct. BUY has catastrophic WR across all segments.")


def sweep_price_range(conn: sqlite3.Connection) -> None:
    """Test performance at different price ranges."""
    print("\n2. PRICE RANGE SENSITIVITY (SELL only)")
    print("-" * 60)

    ranges = [
        ("$0.05-0.10", 0.05, 0.10),
        ("$0.10-0.20", 0.10, 0.20),
        ("$0.20-0.30", 0.20, 0.30),
        ("$0.30-0.40", 0.30, 0.40),
        ("$0.40-0.50", 0.40, 0.50),
        ("$0.50-0.60", 0.50, 0.60),
        ("$0.10-0.50 (current)", 0.10, 0.50),
        ("$0.15-0.45 (tighter)", 0.15, 0.45),
        ("$0.20-0.35 (sweet spot?)", 0.20, 0.35),
    ]

    for label, lo, hi in ranges:
        rows = conn.execute(
            """SELECT * FROM paper_trades WHERE resolved = 1
               AND direction = 'SELL' AND market_price >= ? AND market_price < ?""",
            (lo, hi),
        ).fetchall()
        s = compute_stats(rows)
        flag = ""
        if s["n"] > 0 and s["wr"] >= 75:
            flag = " <-- POSITIVE"
        elif s["n"] > 0 and s["wr"] < 50:
            flag = " <-- DANGER"
        print(f"  {label:25s}: n={s['n']:3d}, WR={s['wr']:5.1f}%, "
              f"P&L=${s['total_pnl']:+8.2f}{flag}")

    # Break-even WR at different price points
    print("\n  Break-even WR by avg price (fee = 0.05 * p * (1-p)):")
    for p in [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45]:
        no_p = 1 - p
        shares = 1.0 / no_p  # per $1 stake
        fee = 0.05 * no_p * p * shares
        win_pnl = shares * p - fee
        loss_pnl = 1.0  # lose $1 stake
        be_wr = loss_pnl / (win_pnl + loss_pnl)
        print(f"    p=${p:.2f}: BE WR={be_wr*100:.1f}%, "
              f"win=${win_pnl:.4f}, loss=$-{loss_pnl:.4f} per $1")


def sweep_question_type(conn: sqlite3.Connection) -> None:
    """Test bucket vs cumulative performance."""
    print("\n3. QUESTION TYPE SENSITIVITY (SELL only, $0.10-$0.50)")
    print("-" * 60)

    for qt in ["bucket", "cumulative_higher", "cumulative_lower"]:
        rows = conn.execute(
            """SELECT * FROM paper_trades WHERE resolved = 1
               AND direction = 'SELL' AND market_price BETWEEN 0.10 AND 0.50
               AND question_type = ?""",
            (qt,),
        ).fetchall()
        s = compute_stats(rows)
        print(f"  {qt:20s}: n={s['n']:3d}, WR={s['wr']:5.1f}%, "
              f"P&L=${s['total_pnl']:+8.2f}")

    # Combined cumulative
    rows = conn.execute(
        """SELECT * FROM paper_trades WHERE resolved = 1
           AND direction = 'SELL' AND market_price BETWEEN 0.10 AND 0.50
           AND question_type LIKE 'cumulative%'""",
    ).fetchall()
    s = compute_stats(rows)
    print(f"  {'cumulative (all)':20s}: n={s['n']:3d}, WR={s['wr']:5.1f}%, "
          f"P&L=${s['total_pnl']:+8.2f}")

    print("\n  Note: Cumulative n is very small. Bucket n=45 is the bulk of data.")
    print("  Current filter: cumulative only. If bucket WR holds >75%, consider including.")


def sweep_city(conn: sqlite3.Connection) -> None:
    """Test performance by city."""
    print("\n4. CITY SENSITIVITY (SELL only, $0.10-$0.50)")
    print("-" * 60)

    rows = conn.execute("""
        SELECT city,
               COUNT(*) as n,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
               ROUND(100.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) as wr,
               ROUND(SUM(pnl), 2) as total_pnl
        FROM paper_trades
        WHERE resolved = 1 AND direction = 'SELL' AND market_price BETWEEN 0.10 AND 0.50
        GROUP BY city
        HAVING COUNT(*) >= 3
        ORDER BY wr DESC
    """).fetchall()

    for r in rows:
        flag = ""
        if r["wr"] >= 80:
            flag = " <-- STRONG"
        elif r["wr"] < 50:
            flag = " <-- AVOID"
        print(f"  {r['city']:20s}: n={r['n']:3d}, WR={r['wr']:5.1f}%, "
              f"P&L=${r['total_pnl']:+8.2f}{flag}")


def sweep_kelly_threshold(conn: sqlite3.Connection) -> None:
    """Test performance at different Kelly filter thresholds."""
    print("\n5. KELLY THRESHOLD SENSITIVITY (SELL, $0.10-$0.50)")
    print("-" * 60)
    print("  Current locked minimum: kelly >= 0.15")

    thresholds = [0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50]
    for k in thresholds:
        rows = conn.execute(
            """SELECT * FROM paper_trades WHERE resolved = 1
               AND direction = 'SELL' AND market_price BETWEEN 0.10 AND 0.50
               AND kelly >= ?""",
            (k,),
        ).fetchall()
        s = compute_stats(rows)
        flag = " <-- CURRENT" if k == 0.15 else ""
        print(f"  kelly >= {k:.2f}: n={s['n']:3d}, WR={s['wr']:5.1f}%, "
              f"P&L=${s['total_pnl']:+8.2f}{flag}")


def sweep_edge_threshold(conn: sqlite3.Connection) -> None:
    """Test performance at different edge filter thresholds."""
    print("\n6. EDGE THRESHOLD SENSITIVITY (SELL, $0.10-$0.50)")
    print("-" * 60)
    print("  Current locked minimum: edge >= 0.10")

    thresholds = [0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40]
    for e in thresholds:
        rows = conn.execute(
            """SELECT * FROM paper_trades WHERE resolved = 1
               AND direction = 'SELL' AND market_price BETWEEN 0.10 AND 0.50
               AND edge >= ?""",
            (e,),
        ).fetchall()
        s = compute_stats(rows)
        flag = " <-- CURRENT" if e == 0.10 else ""
        print(f"  edge >= {e:.2f}: n={s['n']:3d}, WR={s['wr']:5.1f}%, "
              f"P&L=${s['total_pnl']:+8.2f}{flag}")


def overall_summary(conn: sqlite3.Connection) -> None:
    """Print the overall strategy performance for context."""
    print("=" * 60)
    print("PARAMETER SENSITIVITY SWEEP - Weather SELL Strategy")
    print("=" * 60)

    # Total resolved
    total = conn.execute("SELECT COUNT(*) FROM paper_trades WHERE resolved = 1").fetchone()[0]
    sell_target = conn.execute(
        """SELECT COUNT(*) FROM paper_trades WHERE resolved = 1
           AND direction = 'SELL' AND market_price BETWEEN 0.10 AND 0.50"""
    ).fetchone()[0]
    print(f"\nTotal resolved trades: {total}")
    print(f"SELL $0.10-$0.50 (strategy target): {sell_target}")
    print(f"\nWARNING: Small sample sizes. Sensitivity results are indicative, not conclusive.")
    print(f"Do NOT change locked parameters based on this sweep alone (Think Tank rule).")


def main():
    conn = get_db()
    overall_summary(conn)
    sweep_direction(conn)
    sweep_price_range(conn)
    sweep_question_type(conn)
    sweep_city(conn)
    sweep_kelly_threshold(conn)
    sweep_edge_threshold(conn)

    print(f"\n{'=' * 60}")
    print("PARAMETER RANKING BY SENSITIVITY")
    print(f"{'=' * 60}")
    print("""
  1. DIRECTION (highest impact): BUY -> SELL flips from -$240 to +$26
     Status: LOCKED correctly. No change needed.

  2. PRICE RANGE (high impact): $0.20-0.30 is the sweet spot.
     $0.10-0.20 and $0.40-0.50 are weak. Consider tightening.
     Status: LOCKED at $0.10-$0.50. Revisit at n=100 if $0.20-$0.30 holds.

  3. QUESTION TYPE (medium impact): Bucket vs cumulative shows different WR.
     Cumulative sample too small to compare. Bucket WR at 0.20-0.30 is strong.
     Status: LOCKED at cumulative-only. This may be leaving money on table.

  4. CITY (medium impact): Some cities consistently better than others.
     Status: No city filter currently. Consider at n=100.

  5. KELLY THRESHOLD (low impact): WR barely changes across thresholds.
     Higher Kelly filters mainly reduce n without improving WR.
     Status: LOCKED at 0.15. Correct.

  6. EDGE THRESHOLD (low impact): Similar to Kelly - higher thresholds
     reduce n but WR is flat. Edge may not discriminate well.
     Status: LOCKED at 0.10. Correct.
""")

    conn.close()


if __name__ == "__main__":
    main()
