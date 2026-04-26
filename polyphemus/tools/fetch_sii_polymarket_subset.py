#!/usr/bin/env python3
"""
Fetch a filtered subset of SII-WANGZJ Polymarket trades for sharp_move backtesting.

Streams `trades.parquet` from HuggingFace via HfFileSystem (no full local copy of
the 32GB source). Filters to BTC/ETH/SOL/XRP 5m up-down markets identified from
`markets.parquet` slug pattern `^(btc|eth|sol|xrp)-updown-5m-\\d+$`.

Output: polyphemus/data/sii_polymarket_subset/{markets,trades}_crypto_5m.parquet

See docs/codex/nodes/sharp-move-alpha-decay-backtest.md for the research design
and Phase 2 PLAN this script implements.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pyarrow.compute as pc
from huggingface_hub import HfFileSystem


HF_BASE = "datasets/SII-WANGZJ/Polymarket_data/"
SLUG_RE = re.compile(r"^(btc|eth|sol|xrp)-updown-5m-\d+$")
DEFAULT_OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "sii_polymarket_subset"


def get_target_market_ids(fs: HfFileSystem, out_dir: Path) -> set[str]:
    """Read markets.parquet from HF, filter by slug regex, save the subset locally."""
    print(f"[markets] reading {HF_BASE}markets.parquet ...")
    with fs.open(HF_BASE + "markets.parquet", "rb") as f:
        tbl = pq.read_table(f)
    print(f"[markets] total rows: {tbl.num_rows:,}")

    slugs = tbl.column("slug").to_pylist()
    mask = [bool(SLUG_RE.match(s)) for s in slugs]
    target = tbl.filter(pa.array(mask))
    print(f"[markets] crypto 5m updown matches: {target.num_rows:,}")

    out_path = out_dir / "markets_crypto_5m.parquet"
    pq.write_table(target, out_path, compression="zstd")
    print(f"[markets] wrote {out_path} ({out_path.stat().st_size / 1e6:.1f} MB)")

    # markets.parquet uses `id`; trades.parquet uses `market_id` (same value space)
    return set(target.column("id").to_pylist())


def fetch_trades_subset(
    fs: HfFileSystem,
    target_ids: set[str],
    out_dir: Path,
    min_timestamp: int,
    max_timestamp: int,
) -> Path:
    """Read only row groups whose timestamp range overlaps [min_ts, max_ts],
    filter by market_id ∈ target_ids, write subset. Uses parquet row-group
    statistics to skip irrelevant chunks (crypto 5m markets are time-bounded
    so most of trades.parquet is irrelevant).
    """
    print(f"[trades] opening {HF_BASE}trades.parquet (568M rows, time-sorted)")
    print(f"[trades] target window: ts >= {min_timestamp} AND ts <= {max_timestamp}")
    target_ids_arr = pa.array(sorted(target_ids))

    with fs.open(HF_BASE + "trades.parquet", "rb") as f:
        pfile = pq.ParquetFile(f, buffer_size=8 * 1024 * 1024)
        md = pfile.metadata

        # Find timestamp column index
        ts_col_idx = None
        for j in range(md.row_group(0).num_columns):
            if md.row_group(0).column(j).path_in_schema == "timestamp":
                ts_col_idx = j
                break
        if ts_col_idx is None:
            raise RuntimeError("timestamp column not found")

        # Pick row groups where stats indicate overlap with target window
        target_row_groups: list[int] = []
        for rg_idx in range(md.num_row_groups):
            stats = md.row_group(rg_idx).column(ts_col_idx).statistics
            if stats is None:
                target_row_groups.append(rg_idx)  # be safe
                continue
            rg_min, rg_max = stats.min, stats.max
            if rg_max < min_timestamp or rg_min > max_timestamp:
                continue  # entire row group outside window
            target_row_groups.append(rg_idx)

        total_rg_rows = sum(md.row_group(i).num_rows for i in target_row_groups)
        print(f"[trades] reading {len(target_row_groups)} of {md.num_row_groups} row groups "
              f"({total_rg_rows:,} rows in scope, {total_rg_rows/md.num_rows*100:.1f}% of file)")

        out_path = out_dir / "trades_crypto_5m.parquet"
        writer: pq.ParquetWriter | None = None
        kept_total = 0
        scanned_total = 0
        t0 = time.time()

        try:
            for k, rg_idx in enumerate(target_row_groups):
                tbl = pfile.read_row_group(rg_idx)
                scanned_total += tbl.num_rows

                # Apply both timestamp + market_id filters
                ts_mask = pc.and_(
                    pc.greater_equal(tbl["timestamp"], min_timestamp),
                    pc.less_equal(tbl["timestamp"], max_timestamp),
                )
                tbl = tbl.filter(ts_mask)
                if tbl.num_rows == 0:
                    continue
                mask = pc.is_in(tbl.column("market_id"), value_set=target_ids_arr)
                kept = tbl.filter(mask)
                if kept.num_rows > 0:
                    if writer is None:
                        writer = pq.ParquetWriter(out_path, kept.schema, compression="zstd")
                    writer.write_table(kept)
                    kept_total += kept.num_rows

                elapsed = time.time() - t0
                pct = (k + 1) / len(target_row_groups) * 100
                print(f"  rg {k+1}/{len(target_row_groups)} (file rg {rg_idx}): "
                      f"scanned {scanned_total:,} kept {kept_total:,} "
                      f"({pct:.1f}% rg-pass, {elapsed:.0f}s)")
        finally:
            if writer is not None:
                writer.close()

    if writer is None:
        raise RuntimeError("no matching trades found in the target time window")

    size_mb = out_path.stat().st_size / 1e6
    print(f"[trades] DONE: kept {kept_total:,} of {scanned_total:,} scanned ({size_mb:.1f} MB)")
    return out_path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--out-dir", type=Path, default=DEFAULT_OUT_DIR,
        help="output directory for filtered parquet files",
    )
    # Crypto 5m markets created from 2025-12-17 onward; trades trail by minutes
    p.add_argument(
        "--min-timestamp", type=int, default=1734220800,  # 2025-12-15 00:00 UTC (buffer)
        help="skip row groups with all timestamps below this (default: 2025-12-15)",
    )
    p.add_argument(
        "--max-timestamp", type=int, default=1746057600,  # 2026-05-01 00:00 UTC (buffer)
        help="skip row groups with all timestamps above this (default: 2026-05-01)",
    )
    args = p.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    fs = HfFileSystem()

    target_ids = get_target_market_ids(fs, args.out_dir)
    print()
    fetch_trades_subset(fs, target_ids, args.out_dir, args.min_timestamp, args.max_timestamp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
