#!/usr/bin/env python3
"""
Fetch Binance 1m klines for BTCUSDT/ETHUSDT/SOLUSDT/XRPUSDT.

Uses data.binance.vision (public data archive, US-accessible — api.binance.com
returns HTTP 451 for US IPs). Each month-asset is a single CSV zip.

URL pattern:
  https://data.binance.vision/data/spot/monthly/klines/{SYMBOL}/1m/{SYMBOL}-1m-{YYYY}-{MM}.zip

Used as the supplementary signal-reconstruction source for the sharp_move alpha
decay backtest (the SII-WANGZJ Polymarket dataset doesn't include Binance prices,
so we synthesize sharp_move's "60s momentum >0.30%" trigger from these klines).

Output: polyphemus/data/binance_klines/{ASSET}.parquet (zstd-compressed)
"""
from __future__ import annotations

import argparse
import io
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import requests


VISION_URL = "https://data.binance.vision/data/spot/monthly/klines/{symbol}/1m/{symbol}-1m-{ym}.zip"
ASSETS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
DEFAULT_OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "binance_klines"


def month_range(start: str, end: str) -> list[str]:
    """Yield 'YYYY-MM' strings inclusive between start and end (YYYY-MM-DD)."""
    sd = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    ed = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    out = []
    y, m = sd.year, sd.month
    while (y, m) <= (ed.year, ed.month):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def fetch_month_zip(symbol: str, ym: str) -> list[list[str]] | None:
    """Download one month's klines zip; return list of CSV rows (or None if 404)."""
    url = VISION_URL.format(symbol=symbol, ym=ym)
    resp = requests.get(url, timeout=60)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as csvf:
            text = csvf.read().decode("utf-8")
    rows = [line.split(",") for line in text.strip().split("\n") if line]
    return rows


def klines_to_table(rows: list[list[str]]) -> pa.Table:
    """Convert raw CSV rows to a typed pyarrow Table."""
    cols = list(zip(*rows))
    return pa.table({
        "open_time_ms": pa.array([int(x) for x in cols[0]], type=pa.int64()),
        "open": pa.array([float(x) for x in cols[1]], type=pa.float64()),
        "high": pa.array([float(x) for x in cols[2]], type=pa.float64()),
        "low": pa.array([float(x) for x in cols[3]], type=pa.float64()),
        "close": pa.array([float(x) for x in cols[4]], type=pa.float64()),
        "volume": pa.array([float(x) for x in cols[5]], type=pa.float64()),
        "close_time_ms": pa.array([int(x) for x in cols[6]], type=pa.int64()),
        "quote_volume": pa.array([float(x) for x in cols[7]], type=pa.float64()),
        "trades": pa.array([int(x) for x in cols[8]], type=pa.int64()),
    })


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    p.add_argument("--start", default="2025-12-01", help="YYYY-MM-DD inclusive (uses month)")
    p.add_argument("--end", default="2026-04-26", help="YYYY-MM-DD inclusive (uses month)")
    p.add_argument("--assets", nargs="+", default=list(ASSETS))
    args = p.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    months = month_range(args.start, args.end)
    print(f"Months to fetch: {months}")

    for symbol in args.assets:
        out_path = args.out_dir / f"{symbol}.parquet"
        if out_path.exists():
            print(f"[{symbol}] already exists at {out_path}, skipping")
            continue

        print(f"[{symbol}] fetching {len(months)} months from data.binance.vision")
        all_rows: list[list[str]] = []
        t0 = time.time()
        for ym in months:
            rows = fetch_month_zip(symbol, ym)
            if rows is None:
                print(f"  {ym}: 404 (not yet published or out of range), skipping")
                continue
            all_rows.extend(rows)
            print(f"  {ym}: {len(rows):,} klines ({len(all_rows):,} cumulative)")
            time.sleep(0.1)

        if not all_rows:
            print(f"[{symbol}] no data fetched, skipping write")
            continue

        tbl = klines_to_table(all_rows)
        pq.write_table(tbl, out_path, compression="zstd")
        size_mb = out_path.stat().st_size / 1e6
        elapsed = time.time() - t0
        print(f"[{symbol}] DONE: {tbl.num_rows:,} rows, {size_mb:.1f} MB, {elapsed:.0f}s")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
