#!/usr/bin/env python3
"""
Fetch Binance 1m klines for BTCUSDT/ETHUSDT/SOLUSDT/XRPUSDT, 2024-01-01 → 2025-12-31.

Used as the supplementary signal-reconstruction source for the sharp_move alpha
decay backtest (the SII-WANGZJ Polymarket dataset doesn't include Binance prices,
so we synthesize sharp_move's "60s momentum >0.30%" trigger from these klines).

Output: polyphemus/data/binance_klines/{ASSET}.parquet (zstd-compressed)

Free Binance public API; rate limit 1200 req/min (2024). 1000 klines per request,
~525,600 minutes per year = ~525 requests per asset over 2 years.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import requests


BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
ASSETS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
DEFAULT_OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "binance_klines"
DEFAULT_START = "2024-01-01"
DEFAULT_END = "2025-12-31"
KLINE_COLS = (
    "open_time_ms", "open", "high", "low", "close", "volume",
    "close_time_ms", "quote_volume", "trades", "taker_buy_base",
    "taker_buy_quote", "ignore",
)


def to_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def fetch_klines(symbol: str, start_ms: int, end_ms: int, sleep_ms: int = 60) -> list[list]:
    """Page through Binance 1m klines from start_ms to end_ms inclusive."""
    out: list[list] = []
    cur = start_ms
    requests_made = 0
    t0 = time.time()
    while cur < end_ms:
        params = {
            "symbol": symbol,
            "interval": "1m",
            "startTime": cur,
            "endTime": end_ms,
            "limit": 1000,
        }
        resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=30)
        requests_made += 1
        if resp.status_code == 429 or resp.status_code == 418:
            wait = int(resp.headers.get("Retry-After", "10"))
            print(f"  rate-limited; sleeping {wait}s")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        out.extend(batch)
        last_open = batch[-1][0]
        cur = last_open + 60_000  # advance 1m past last
        if requests_made % 25 == 0:
            elapsed = time.time() - t0
            covered_days = (cur - start_ms) / (1000 * 86400)
            print(f"  {symbol}: {requests_made} req, {len(out):,} klines, ~{covered_days:.1f} days, {elapsed:.0f}s")
        time.sleep(sleep_ms / 1000)
    return out


def klines_to_table(rows: list[list]) -> pa.Table:
    """Convert raw kline rows to a pyarrow Table with typed columns."""
    cols = list(zip(*rows))
    return pa.table({
        "open_time_ms": pa.array(cols[0], type=pa.int64()),
        "open": pa.array([float(x) for x in cols[1]], type=pa.float64()),
        "high": pa.array([float(x) for x in cols[2]], type=pa.float64()),
        "low": pa.array([float(x) for x in cols[3]], type=pa.float64()),
        "close": pa.array([float(x) for x in cols[4]], type=pa.float64()),
        "volume": pa.array([float(x) for x in cols[5]], type=pa.float64()),
        "close_time_ms": pa.array(cols[6], type=pa.int64()),
        "quote_volume": pa.array([float(x) for x in cols[7]], type=pa.float64()),
        "trades": pa.array(cols[8], type=pa.int64()),
    })


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    p.add_argument("--start", default=DEFAULT_START, help="YYYY-MM-DD inclusive")
    p.add_argument("--end", default=DEFAULT_END, help="YYYY-MM-DD inclusive")
    p.add_argument("--assets", nargs="+", default=list(ASSETS))
    p.add_argument("--sleep-ms", type=int, default=60, help="pacing between requests")
    args = p.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    start_ms = to_ms(args.start)
    end_ms = to_ms(args.end) + 86_400_000  # inclusive end-of-day

    for symbol in args.assets:
        out_path = args.out_dir / f"{symbol}.parquet"
        if out_path.exists():
            print(f"[{symbol}] already exists at {out_path}, skipping")
            continue
        print(f"[{symbol}] fetching {args.start} → {args.end} (1m klines)")
        t0 = time.time()
        rows = fetch_klines(symbol, start_ms, end_ms, args.sleep_ms)
        if not rows:
            print(f"[{symbol}] no klines returned, skipping write")
            continue
        tbl = klines_to_table(rows)
        pq.write_table(tbl, out_path, compression="zstd")
        size_mb = out_path.stat().st_size / 1e6
        elapsed = time.time() - t0
        print(f"[{symbol}] DONE: {tbl.num_rows:,} rows, {size_mb:.1f} MB, {elapsed:.0f}s")

    return 0


if __name__ == "__main__":
    sys.exit(main())
