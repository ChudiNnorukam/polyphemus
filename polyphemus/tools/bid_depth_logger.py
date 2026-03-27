#!/usr/bin/env python3
"""
Bid-depth logger for resolution snipe feasibility analysis.

Runs as a standalone background process on VPS.
Logs bid-side orderbook depth during the last 60s of each 5m epoch
for BTC Up/Down markets.

Phase 1 data collection for /zero full-sweep:
  - How much bid liquidity exists at 0.80, 0.85, 0.90, 0.95?
  - What is the best bid and spread in the snipe window?
  - Can we actually SELL at reasonable prices in the last 30s?

Usage:
  python3 bid_depth_logger.py [--db /path/to/bid_depth.db] [--duration-hours 168]

No lagbot imports (avoids Bug #39 types.py shadow).
Uses raw HTTP to CLOB + Gamma API.
"""
import argparse
import json
import logging
import math
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests", file=sys.stderr)
    sys.exit(1)

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
EPOCH_SECS = 300  # 5 minutes
POLL_INTERVAL = 5  # seconds between book polls
SNIPE_WINDOW = 60  # log depth in last 60s of epoch
PRICE_LEVELS = [0.80, 0.85, 0.88, 0.90, 0.93, 0.95]  # bid depth at these levels

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [BID_DEPTH] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bid_depth")


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bid_depth (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            epoch_start INTEGER NOT NULL,
            secs_remaining REAL NOT NULL,
            asset TEXT NOT NULL,
            direction TEXT NOT NULL,
            token_id TEXT NOT NULL,
            best_bid REAL,
            best_ask REAL,
            spread REAL,
            mid_price REAL,
            total_bid_qty REAL,
            total_ask_qty REAL,
            bid_qty_at_80 REAL,
            bid_qty_at_85 REAL,
            bid_qty_at_88 REAL,
            bid_qty_at_90 REAL,
            bid_qty_at_93 REAL,
            bid_qty_at_95 REAL,
            bid_levels_count INTEGER,
            ask_levels_count INTEGER,
            book_fetch_ms REAL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_bd_epoch ON bid_depth(epoch_start, secs_remaining)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_cache (
            slug TEXT PRIMARY KEY,
            condition_id TEXT,
            up_token TEXT,
            down_token TEXT,
            discovered_at TEXT
        )
    """)
    conn.commit()
    return conn


def discover_current_epoch_market(session: requests.Session) -> dict:
    """Find the current BTC 5m epoch market via Gamma API exact slug query.

    Gamma API returns data when queried with the exact epoch slug:
      GET /markets?slug=btc-updown-5m-{epoch_start_unix}

    Returns dict with up_token, down_token, slug, condition_id or None.
    """
    epoch_start, secs_remaining = current_epoch_info()
    slug = f"btc-updown-5m-{epoch_start}"

    try:
        resp = session.get(
            f"{GAMMA_API}/markets",
            params={"slug": slug},
            timeout=10,
        )
        if resp.status_code != 200:
            log.warning(f"Gamma API {resp.status_code} for {slug}")
            return None

        data = resp.json()
        if not data:
            log.debug(f"No data for {slug} (market may not exist yet)")
            return None

        market = data[0] if isinstance(data, list) else data

        # clobTokenIds is a JSON string: '["token0", "token1"]'
        raw_tokens = market.get("clobTokenIds", "[]")
        if isinstance(raw_tokens, str):
            token_ids = json.loads(raw_tokens)
        else:
            token_ids = raw_tokens

        raw_outcomes = market.get("outcomes", "[]")
        if isinstance(raw_outcomes, str):
            outcomes = json.loads(raw_outcomes)
        else:
            outcomes = raw_outcomes

        if len(token_ids) < 2 or len(outcomes) < 2:
            log.warning(f"Incomplete market data for {slug}: {len(token_ids)} tokens")
            return None

        # outcomes = ["Up", "Down"], token_ids[0] = Up, token_ids[1] = Down
        return {
            "slug": slug,
            "condition_id": market.get("conditionId", ""),
            "up_token": token_ids[0],
            "down_token": token_ids[1],
            "epoch_start": epoch_start,
        }

    except Exception as e:
        log.error(f"Market discovery failed for {slug}: {e}")
        return None


def get_book(session: requests.Session, token_id: str) -> dict:
    """Fetch orderbook from CLOB REST API."""
    t0 = time.time()
    try:
        resp = session.get(
            f"{CLOB_API}/book",
            params={"token_id": token_id},
            timeout=10,
        )
        fetch_ms = (time.time() - t0) * 1000
        if resp.status_code != 200:
            return {"bids": [], "asks": [], "fetch_ms": fetch_ms}

        data = resp.json()
        bids = []
        for b in data.get("bids", []):
            try:
                bids.append({"price": float(b["price"]), "size": float(b["size"])})
            except (KeyError, ValueError, TypeError):
                continue
        bids.sort(key=lambda x: x["price"], reverse=True)

        asks = []
        for a in data.get("asks", []):
            try:
                asks.append({"price": float(a["price"]), "size": float(a["size"])})
            except (KeyError, ValueError, TypeError):
                continue
        asks.sort(key=lambda x: x["price"])

        return {"bids": bids, "asks": asks, "fetch_ms": fetch_ms}
    except Exception as e:
        fetch_ms = (time.time() - t0) * 1000
        log.warning(f"Book fetch failed for {token_id[:12]}...: {e}")
        return {"bids": [], "asks": [], "fetch_ms": fetch_ms}


def compute_depth_at_level(bids: list, level: float) -> float:
    """Sum bid quantity at or above a given price level."""
    return sum(b["size"] for b in bids if b["price"] >= level)


def log_book_snapshot(
    conn: sqlite3.Connection,
    epoch_start: int,
    secs_remaining: float,
    asset: str,
    direction: str,
    token_id: str,
    book: dict,
):
    bids = book["bids"]
    asks = book["asks"]

    best_bid = bids[0]["price"] if bids else None
    best_ask = asks[0]["price"] if asks else None
    spread = round(best_ask - best_bid, 4) if (best_bid and best_ask) else None
    mid = round((best_bid + best_ask) / 2, 4) if (best_bid and best_ask) else None
    total_bid = round(sum(b["size"] for b in bids), 2)
    total_ask = round(sum(a["size"] for a in asks), 2)

    conn.execute("""
        INSERT INTO bid_depth (
            timestamp, epoch_start, secs_remaining, asset, direction, token_id,
            best_bid, best_ask, spread, mid_price,
            total_bid_qty, total_ask_qty,
            bid_qty_at_80, bid_qty_at_85, bid_qty_at_88,
            bid_qty_at_90, bid_qty_at_93, bid_qty_at_95,
            bid_levels_count, ask_levels_count, book_fetch_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now(timezone.utc).isoformat(),
        epoch_start,
        round(secs_remaining, 1),
        asset,
        direction,
        token_id,
        best_bid,
        best_ask,
        spread,
        mid,
        total_bid,
        total_ask,
        round(compute_depth_at_level(bids, 0.80), 2),
        round(compute_depth_at_level(bids, 0.85), 2),
        round(compute_depth_at_level(bids, 0.88), 2),
        round(compute_depth_at_level(bids, 0.90), 2),
        round(compute_depth_at_level(bids, 0.93), 2),
        round(compute_depth_at_level(bids, 0.95), 2),
        len(bids),
        len(asks),
        round(book["fetch_ms"], 1),
    ))
    conn.commit()


def current_epoch_info() -> tuple:
    """Return (epoch_start_ts, secs_remaining) for the current 5m epoch."""
    now = time.time()
    epoch_start = int(now // EPOCH_SECS) * EPOCH_SECS
    epoch_end = epoch_start + EPOCH_SECS
    secs_remaining = epoch_end - now
    return epoch_start, secs_remaining


def run(db_path: str, duration_hours: float):
    conn = init_db(db_path)
    session = requests.Session()
    session.headers["User-Agent"] = "polyphemus-bid-depth/1.0"

    end_time = time.time() + (duration_hours * 3600) if duration_hours > 0 else float("inf")

    log.info(f"Starting bid-depth logger. DB: {db_path}")
    log.info(f"Duration: {'infinite' if duration_hours <= 0 else f'{duration_hours}h'}")
    log.info(f"Snipe window: last {SNIPE_WINDOW}s of each {EPOCH_SECS}s epoch")
    log.info(f"Poll interval: {POLL_INTERVAL}s")

    # Track which epoch we last discovered to avoid redundant queries
    last_discovered_epoch = 0
    current_market = None

    while time.time() < end_time:
        epoch_start, secs_remaining = current_epoch_info()

        # Only poll during the snipe window (last SNIPE_WINDOW seconds)
        if secs_remaining > SNIPE_WINDOW:
            sleep_until_window = secs_remaining - SNIPE_WINDOW
            sleep_chunk = min(sleep_until_window, 30)
            time.sleep(sleep_chunk)
            continue

        # Discover current epoch market (once per epoch)
        if epoch_start != last_discovered_epoch:
            current_market = discover_current_epoch_market(session)
            last_discovered_epoch = epoch_start
            if current_market:
                log.info(f"Epoch {epoch_start}: {current_market['slug']}")
                # Cache in DB
                conn.execute("""
                    INSERT OR REPLACE INTO market_cache
                    (slug, condition_id, up_token, down_token, discovered_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    current_market["slug"], current_market["condition_id"],
                    current_market["up_token"], current_market["down_token"],
                    datetime.now(timezone.utc).isoformat(),
                ))
                conn.commit()
            else:
                log.warning(f"No market found for epoch {epoch_start}")

        if not current_market:
            time.sleep(POLL_INTERVAL)
            continue

        # Poll books for both Up and Down tokens
        for direction, token_id in [("UP", current_market["up_token"]),
                                     ("DOWN", current_market["down_token"])]:
            book = get_book(session, token_id)

            if book["bids"] or book["asks"]:
                log_book_snapshot(
                    conn, epoch_start, secs_remaining,
                    "BTC", direction, token_id, book,
                )

                best_bid = book["bids"][0]["price"] if book["bids"] else 0
                total_bid = sum(b["size"] for b in book["bids"])
                depth_90 = compute_depth_at_level(book["bids"], 0.90)
                log.info(
                    f"{direction} | {secs_remaining:.0f}s left | "
                    f"best_bid={best_bid:.2f} | total_bid={total_bid:.0f} | "
                    f"depth>=0.90={depth_90:.0f} | {book['fetch_ms']:.0f}ms"
                )

            # Brief pause between requests (rate limit safety)
            time.sleep(0.3)

        # Wait for next poll
        time.sleep(POLL_INTERVAL)

    conn.close()
    log.info("Bid-depth logger stopped.")


def analyze(db_path: str):
    """Quick analysis of collected bid depth data."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) as n FROM bid_depth").fetchone()["n"]
    if total == 0:
        print("No data collected yet.")
        return

    epochs = conn.execute("SELECT COUNT(DISTINCT epoch_start) as n FROM bid_depth").fetchone()["n"]
    print(f"\n=== Bid Depth Analysis ({total} snapshots, {epochs} epochs) ===\n")

    # Summary by direction
    for direction in ["UP", "DOWN"]:
        rows = conn.execute("""
            SELECT
                AVG(best_bid) as avg_best_bid,
                AVG(spread) as avg_spread,
                AVG(total_bid_qty) as avg_total_bid,
                AVG(bid_qty_at_85) as avg_depth_85,
                AVG(bid_qty_at_90) as avg_depth_90,
                AVG(bid_qty_at_93) as avg_depth_93,
                AVG(bid_qty_at_95) as avg_depth_95,
                COUNT(*) as n,
                AVG(book_fetch_ms) as avg_fetch_ms
            FROM bid_depth WHERE direction = ?
        """, (direction,)).fetchone()

        if rows["n"] == 0:
            continue

        print(f"--- {direction} tokens (n={rows['n']}) ---")
        print(f"  Avg best bid:     {rows['avg_best_bid']:.4f}" if rows['avg_best_bid'] else "  Avg best bid:     N/A")
        print(f"  Avg spread:       {rows['avg_spread']:.4f}" if rows['avg_spread'] else "  Avg spread:       N/A")
        print(f"  Avg total bid qty:{rows['avg_total_bid']:.0f}" if rows['avg_total_bid'] else "  Avg total bid qty:N/A")
        print(f"  Avg depth >= 0.85:{rows['avg_depth_85']:.0f}" if rows['avg_depth_85'] else "  Avg depth >= 0.85:N/A")
        print(f"  Avg depth >= 0.90:{rows['avg_depth_90']:.0f}" if rows['avg_depth_90'] else "  Avg depth >= 0.90:N/A")
        print(f"  Avg depth >= 0.93:{rows['avg_depth_93']:.0f}" if rows['avg_depth_93'] else "  Avg depth >= 0.93:N/A")
        print(f"  Avg depth >= 0.95:{rows['avg_depth_95']:.0f}" if rows['avg_depth_95'] else "  Avg depth >= 0.95:N/A")
        print(f"  Avg fetch latency:{rows['avg_fetch_ms']:.0f}ms")
        print()

    # Depth by secs_remaining bucket
    print("--- Bid depth (>=0.90) by time remaining ---")
    for lo, hi, label in [(0, 10, "0-10s"), (10, 20, "10-20s"), (20, 30, "20-30s"),
                           (30, 45, "30-45s"), (45, 60, "45-60s")]:
        row = conn.execute("""
            SELECT AVG(bid_qty_at_90) as avg_d90, AVG(total_bid_qty) as avg_total,
                   COUNT(*) as n
            FROM bid_depth
            WHERE secs_remaining >= ? AND secs_remaining < ?
        """, (lo, hi)).fetchone()
        if row["n"] > 0:
            d90 = row["avg_d90"] or 0
            total = row["avg_total"] or 0
            print(f"  {label:>8s}: depth>=0.90={d90:6.0f} | total_bid={total:6.0f} | n={row['n']}")

    # GO/NO-GO assessment
    print("\n--- GO/NO-GO Assessment ---")
    median_row = conn.execute("""
        SELECT bid_qty_at_90 FROM bid_depth
        WHERE secs_remaining <= 30 AND bid_qty_at_90 IS NOT NULL
        ORDER BY bid_qty_at_90
        LIMIT 1 OFFSET (
            SELECT COUNT(*) / 2 FROM bid_depth
            WHERE secs_remaining <= 30 AND bid_qty_at_90 IS NOT NULL
        )
    """).fetchone()

    if median_row:
        median_depth = median_row[0]
        if median_depth >= 30:
            print(f"  Median bid depth >=0.90 (last 30s): {median_depth:.0f} shares -> GO")
        elif median_depth >= 10:
            print(f"  Median bid depth >=0.90 (last 30s): {median_depth:.0f} shares -> CONDITIONAL GO (reduce position size)")
        else:
            print(f"  Median bid depth >=0.90 (last 30s): {median_depth:.0f} shares -> NO-GO (cannot exit reliably)")
    else:
        print("  Not enough data for GO/NO-GO (need snapshots with secs_remaining <= 30)")

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bid depth logger for snipe feasibility")
    parser.add_argument("--db", default="/opt/lagbot/instances/emmanuel/data/bid_depth.db",
                        help="SQLite database path")
    parser.add_argument("--duration-hours", type=float, default=168,
                        help="Run for N hours (0=infinite, default=168/7days)")
    parser.add_argument("--analyze", action="store_true",
                        help="Analyze existing data instead of collecting")
    args = parser.parse_args()

    if args.analyze:
        analyze(args.db)
    else:
        try:
            run(args.db, args.duration_hours)
        except KeyboardInterrupt:
            log.info("Interrupted. Data saved.")
