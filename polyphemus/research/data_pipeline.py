"""Binance OHLCV data pipeline - download and store as Parquet.

Downloads BTC/USDT historical candles from Binance via CCXT.
Stores as Parquet files with freshness metadata and gap detection.

Hard-scoped to BTC only. Do NOT add other assets without a formal
hypothesis that justifies the multiple-testing burden.

Usage:
    python3 -m polyphemus.research.data_pipeline \
        --symbol BTC/USDT --timeframe 5m --since 2025-01-01

    # Incremental update (fetches only new data)
    python3 -m polyphemus.research.data_pipeline \
        --symbol BTC/USDT --timeframe 5m --update
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"

# Phase gate: only BTC allowed without explicit approval
ALLOWED_SYMBOLS = {"BTC/USDT"}


def fetch_ohlcv(symbol: str, timeframe: str, since_ms: int, limit: int = 1000) -> list:
    """Fetch OHLCV candles from Binance via CCXT.

    Returns list of [timestamp_ms, open, high, low, close, volume].
    """
    import ccxt

    exchange = ccxt.binance({"enableRateLimit": True})
    all_candles = []
    current_since = since_ms

    print(f"Fetching {symbol} {timeframe} from {datetime.fromtimestamp(since_ms/1000, tz=timezone.utc).isoformat()}")

    while True:
        candles = exchange.fetch_ohlcv(symbol, timeframe, since=current_since, limit=limit)
        if not candles:
            break

        all_candles.extend(candles)
        last_ts = candles[-1][0]

        # Stop if we've reached current time
        if last_ts >= int(time.time() * 1000) - 60_000:
            break

        # Move forward (avoid duplicating last candle)
        current_since = last_ts + 1

        if len(all_candles) % 10_000 == 0:
            print(f"  ... {len(all_candles)} candles fetched")

        # Rate limiting: CCXT handles this, but add a small buffer
        time.sleep(0.1)

    print(f"  Total: {len(all_candles)} candles")
    return all_candles


def candles_to_parquet(candles: list, output_path: Path):
    """Convert candle list to Parquet file using pyarrow."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    if not candles:
        print("No candles to write.")
        return

    timestamps = [c[0] for c in candles]
    table = pa.table({
        "timestamp_ms": pa.array(timestamps, type=pa.int64()),
        "open": pa.array([c[1] for c in candles], type=pa.float64()),
        "high": pa.array([c[2] for c in candles], type=pa.float64()),
        "low": pa.array([c[3] for c in candles], type=pa.float64()),
        "close": pa.array([c[4] for c in candles], type=pa.float64()),
        "volume": pa.array([c[5] for c in candles], type=pa.float64()),
    })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, str(output_path), compression="snappy")
    print(f"Written {len(candles)} candles to {output_path} ({output_path.stat().st_size / 1024:.1f} KB)")


def detect_gaps(candles: list, timeframe: str) -> list[dict]:
    """Detect gaps in candle data.

    Returns list of gaps with start/end timestamps and duration.
    """
    if len(candles) < 2:
        return []

    # Expected interval in ms
    tf_map = {
        "1m": 60_000, "3m": 180_000, "5m": 300_000,
        "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000,
        "4h": 14_400_000, "1d": 86_400_000,
    }
    expected_interval = tf_map.get(timeframe, 300_000)
    # Allow 10% tolerance
    max_gap = expected_interval * 1.1

    gaps = []
    for i in range(1, len(candles)):
        delta = candles[i][0] - candles[i-1][0]
        if delta > max_gap:
            gap_minutes = (delta - expected_interval) / 60_000
            gaps.append({
                "start": datetime.fromtimestamp(candles[i-1][0]/1000, tz=timezone.utc).isoformat(),
                "end": datetime.fromtimestamp(candles[i][0]/1000, tz=timezone.utc).isoformat(),
                "gap_minutes": round(gap_minutes, 1),
                "missing_candles": int(delta / expected_interval) - 1,
            })

    return gaps


def write_metadata(symbol: str, timeframe: str, candles: list, gaps: list, output_dir: Path):
    """Write freshness metadata JSON."""
    meta = {
        "symbol": symbol,
        "timeframe": timeframe,
        "exchange": "binance",
        "last_fetched": datetime.now(timezone.utc).isoformat(),
        "date_range": {
            "start": datetime.fromtimestamp(candles[0][0]/1000, tz=timezone.utc).isoformat() if candles else None,
            "end": datetime.fromtimestamp(candles[-1][0]/1000, tz=timezone.utc).isoformat() if candles else None,
        },
        "total_candles": len(candles),
        "gaps_detected": len(gaps),
        "gaps": gaps[:20],  # Cap at 20 for readability
    }

    meta_path = output_dir / f"{symbol.replace('/', '_')}_{timeframe}_meta.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"Metadata written to {meta_path}")


def load_existing_parquet(path: Path) -> int:
    """Load existing Parquet and return the last timestamp_ms, or 0."""
    if not path.exists():
        return 0
    import pyarrow.parquet as pq
    table = pq.read_table(str(path), columns=["timestamp_ms"])
    if table.num_rows == 0:
        return 0
    return int(table.column("timestamp_ms")[-1].as_py())


def output_path_for(symbol: str, timeframe: str) -> Path:
    """Construct the Parquet output path."""
    return DATA_DIR / f"{symbol.replace('/', '_')}_{timeframe}.parquet"


def main():
    parser = argparse.ArgumentParser(description="Download Binance OHLCV data to Parquet")
    parser.add_argument("--symbol", default="BTC/USDT", help="Trading pair (default: BTC/USDT)")
    parser.add_argument("--timeframe", default="5m", help="Candle timeframe (default: 5m)")
    parser.add_argument("--since", help="Start date (YYYY-MM-DD). Ignored if --update is used.")
    parser.add_argument("--update", action="store_true", help="Incremental update from last candle")
    args = parser.parse_args()

    if args.symbol not in ALLOWED_SYMBOLS:
        print(f"ERROR: {args.symbol} not in allowed symbols: {ALLOWED_SYMBOLS}")
        print("Add other assets only after a formal hypothesis justifies the multiple-testing burden.")
        sys.exit(1)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    parquet_path = output_path_for(args.symbol, args.timeframe)

    if args.update:
        last_ts = load_existing_parquet(parquet_path)
        if last_ts > 0:
            since_ms = last_ts + 1
            print(f"Incremental update from {datetime.fromtimestamp(last_ts/1000, tz=timezone.utc).isoformat()}")
        else:
            print("No existing data found. Use --since to specify start date.")
            sys.exit(1)
    elif args.since:
        since_dt = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        since_ms = int(since_dt.timestamp() * 1000)
    else:
        print("ERROR: Specify either --since YYYY-MM-DD or --update")
        sys.exit(1)

    candles = fetch_ohlcv(args.symbol, args.timeframe, since_ms)

    if not candles:
        print("No candles fetched.")
        sys.exit(0)

    # If updating, merge with existing data
    if args.update and parquet_path.exists():
        import pyarrow.parquet as pq
        existing = pq.read_table(str(parquet_path))
        # Convert existing to list format for merging
        existing_candles = list(zip(
            existing.column("timestamp_ms").to_pylist(),
            existing.column("open").to_pylist(),
            existing.column("high").to_pylist(),
            existing.column("low").to_pylist(),
            existing.column("close").to_pylist(),
            existing.column("volume").to_pylist(),
        ))
        # Merge, deduplicate by timestamp
        seen = set(c[0] for c in existing_candles)
        new_candles = [c for c in candles if c[0] not in seen]
        candles = existing_candles + new_candles
        candles.sort(key=lambda c: c[0])
        print(f"Merged: {len(existing_candles)} existing + {len(new_candles)} new = {len(candles)} total")

    # Detect gaps
    gaps = detect_gaps(candles, args.timeframe)
    if gaps:
        print(f"WARNING: {len(gaps)} gaps detected in data:")
        for g in gaps[:5]:
            print(f"  {g['start']} to {g['end']} ({g['gap_minutes']} min, {g['missing_candles']} candles)")
        if len(gaps) > 5:
            print(f"  ... and {len(gaps) - 5} more")

    # Write Parquet
    candles_to_parquet(candles, parquet_path)

    # Write metadata
    write_metadata(args.symbol, args.timeframe, candles, gaps, DATA_DIR)


if __name__ == "__main__":
    main()
