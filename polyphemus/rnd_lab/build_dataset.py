"""Build combined R&D dataset from both instances' signals DBs.

Output: rnd_lab/data/combined_signals.db with a clean 'analysis' table.
"""
import sqlite3
import os

DATA_DIR = os.path.dirname(os.path.abspath(__file__)) + "/data"

# Features usable in both instances (>80% population)
FEATURES = [
    "midpoint", "time_remaining_secs", "market_window_secs",
    "hour_utc", "minute_utc", "day_of_week",
    "momentum_pct", "regime", "volatility_1h", "trend_1h",
    "guard_passed",
]

# Additional features for per-instance analysis
EXTRA_FEATURES = [
    "signal_score", "score_threshold", "entry_price", "exit_price",
    "pnl", "pnl_pct", "hold_secs", "fill_mode", "fill_time_ms",
    "fear_greed", "spread", "book_depth_bid", "book_depth_ask",
    "book_imbalance", "vpin_5m", "taker_delta", "liq_conviction",
    "liq_volume_60s", "dry_run",
]

LABEL = "is_win"
META = ["slug", "asset", "direction", "outcome", "epoch", "timestamp", "source", "guard_reasons"]

ALL_COLS = META + FEATURES + EXTRA_FEATURES + [LABEL]

def load_instance(db_path, instance_name):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    cols_str = ", ".join(ALL_COLS)
    query = f"SELECT {cols_str} FROM signals WHERE is_win IS NOT NULL"
    
    rows = conn.execute(query).fetchall()
    conn.close()
    
    result = []
    for r in rows:
        d = dict(r)
        d["instance"] = instance_name
        # Normalize direction to lowercase
        if d["direction"]:
            d["direction"] = d["direction"].lower()
        result.append(d)
    
    return result

def main():
    out_db = os.path.join(DATA_DIR, "combined_signals.db")
    if os.path.exists(out_db):
        os.remove(out_db)
    
    print("Loading Emmanuel...")
    emmanuel = load_instance(os.path.join(DATA_DIR, "emmanuel_signals.db"), "emmanuel")
    print(f"  {len(emmanuel)} rows")
    
    print("Loading Polyphemus...")
    polyphemus = load_instance(os.path.join(DATA_DIR, "polyphemus_signals.db"), "polyphemus")
    print(f"  {len(polyphemus)} rows")
    
    combined = emmanuel + polyphemus
    print(f"Combined: {len(combined)} rows")
    
    # Create output DB
    conn = sqlite3.connect(out_db)
    
    all_output_cols = ALL_COLS + ["instance"]
    col_defs = []
    for c in all_output_cols:
        if c in ("is_win", "guard_passed", "time_remaining_secs", "market_window_secs",
                 "hour_utc", "minute_utc", "day_of_week", "fill_time_ms", "dry_run"):
            col_defs.append(f"{c} INTEGER")
        elif c in ("midpoint", "momentum_pct", "volatility_1h", "trend_1h",
                    "signal_score", "score_threshold", "entry_price", "exit_price",
                    "pnl", "pnl_pct", "hold_secs", "spread", "book_depth_bid",
                    "book_depth_ask", "book_imbalance", "vpin_5m", "taker_delta",
                    "liq_conviction", "liq_volume_60s", "epoch", "fear_greed"):
            col_defs.append(f"{c} REAL")
        else:
            col_defs.append(f"{c} TEXT")
    
    create_sql = f"CREATE TABLE analysis ({', '.join(col_defs)})"
    conn.execute(create_sql)
    
    placeholders = ", ".join(["?"] * len(all_output_cols))
    insert_sql = f"INSERT INTO analysis ({', '.join(all_output_cols)}) VALUES ({placeholders})"
    
    for row in combined:
        values = [row.get(c) for c in all_output_cols]
        conn.execute(insert_sql, values)
    
    conn.commit()
    
    # Add useful indexes
    conn.execute("CREATE INDEX idx_analysis_instance ON analysis(instance)")
    conn.execute("CREATE INDEX idx_analysis_asset ON analysis(asset)")
    conn.execute("CREATE INDEX idx_analysis_outcome ON analysis(outcome)")
    conn.execute("CREATE INDEX idx_analysis_is_win ON analysis(is_win)")
    conn.execute("CREATE INDEX idx_analysis_hour ON analysis(hour_utc)")
    conn.commit()
    
    # Summary stats
    cur = conn.execute("""
        SELECT instance, 
               COUNT(*) as n,
               SUM(is_win) as wins,
               ROUND(100.0 * SUM(is_win) / COUNT(*), 1) as wr,
               SUM(CASE WHEN entry_price IS NOT NULL THEN 1 ELSE 0 END) as traded,
               SUM(CASE WHEN outcome = 'filtered' THEN 1 ELSE 0 END) as filtered,
               SUM(CASE WHEN outcome = 'shadow' THEN 1 ELSE 0 END) as shadow
        FROM analysis
        GROUP BY instance
    """)
    print("\nSummary:")
    print(f"{'Instance':<12} {'n':>6} {'wins':>6} {'WR%':>6} {'traded':>7} {'filtered':>9} {'shadow':>7}")
    for row in cur:
        print(f"{row[0]:<12} {row[1]:>6} {row[2]:>6} {row[3]:>6} {row[4]:>7} {row[5]:>9} {row[6]:>7}")
    
    # Asset breakdown
    cur = conn.execute("""
        SELECT asset, 
               COUNT(*) as n,
               ROUND(100.0 * SUM(is_win) / COUNT(*), 1) as wr
        FROM analysis
        GROUP BY asset
        ORDER BY n DESC
    """)
    print("\nBy asset:")
    for row in cur:
        print(f"  {row[0]}: n={row[1]}, WR={row[2]}%")
    
    conn.close()
    print(f"\nWritten to: {out_db}")
    print(f"Size: {os.path.getsize(out_db) / 1024 / 1024:.1f} MB")

if __name__ == "__main__":
    main()
