#!/usr/bin/env python3
"""
Backtest sharp_move's alpha-decay hypothesis against real Polymarket fills.

Joins the SII-WANGZJ trades subset (filtered to BTC/ETH/SOL/XRP 5m updown markets)
to Binance 1m klines, simulates sharp_move's signal-eligibility rules, and
computes Wilson LB(WR) + mean adverse_fill_bps + P9 disjoint-window check.

Outputs: dario_output/sharp-move-alpha-decay-<YYYY-MM-DD>.md

See docs/codex/nodes/sharp-move-alpha-decay-backtest.md for the design.
"""
from __future__ import annotations

import argparse
import ast
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pyarrow.compute as pc


REPO = Path(__file__).resolve().parents[1]
DEFAULT_TRADES = REPO / "data" / "sii_polymarket_subset" / "trades_crypto_5m.parquet"
DEFAULT_MARKETS = REPO / "data" / "sii_polymarket_subset" / "markets_crypto_5m.parquet"
DEFAULT_KLINES_DIR = REPO / "data" / "binance_klines"
DEFAULT_OUTPUT = REPO.parent / "polyphemus" / "dario_output"

# sharp_move config (per emmanuel systemd drop-in)
SHARP_MOVE_MAX_ENTRY_PRICE = 0.95
SHARP_MOVE_MOMENTUM_THRESHOLD_PCT = 0.30
SHARP_MOVE_MIN_SECS_REMAINING = 60
ADVERSE_PRECHECK_THRESHOLD_PCT = 0.03
MARKOV_GATE_MAX_LOSSES = 3

ASSET_FROM_SLUG = {"btc": "BTCUSDT", "eth": "ETHUSDT", "sol": "SOLUSDT", "xrp": "XRPUSDT"}


def wilson_lower_bound(wins: int, n: int, z: float = 1.96) -> float:
    """95% Wilson lower bound on the win rate. Returns 0.0 for n=0."""
    if n == 0:
        return 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - margin) / denom


def load_klines(klines_dir: Path) -> dict[str, dict]:
    """Load each asset's klines into a dict[asset] = {open_time_ms: close_price}."""
    out: dict[str, dict] = {}
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"):
        path = klines_dir / f"{symbol}.parquet"
        if not path.exists():
            print(f"  [warn] missing {path}, sharp_move on {symbol} will be skipped", file=sys.stderr)
            continue
        tbl = pq.read_table(path, columns=["open_time_ms", "close"])
        ot = tbl.column("open_time_ms").to_pylist()
        cl = tbl.column("close").to_pylist()
        out[symbol] = dict(zip(ot, cl))
        print(f"  loaded {symbol}: {len(out[symbol]):,} klines")
    return out


def momentum_60s_pct(klines_for_asset: dict, ts_ms: int) -> float | None:
    """Compute 60s momentum at trade-time. Returns None if klines missing."""
    minute_ms = (ts_ms // 60_000) * 60_000
    prev_ms = minute_ms - 60_000
    cur = klines_for_asset.get(minute_ms)
    prev = klines_for_asset.get(prev_ms)
    if cur is None or prev is None or prev == 0:
        return None
    return (cur / prev - 1) * 100


def adverse_check(klines_for_asset: dict, ts_ms: int, side: str) -> bool:
    """True = pass (no adverse drift); False = drift exceeded threshold."""
    minute_ms = (ts_ms // 60_000) * 60_000
    prev_ms = minute_ms - 60_000  # 60s pre-check approximation
    cur = klines_for_asset.get(minute_ms)
    prev = klines_for_asset.get(prev_ms)
    if cur is None or prev is None or prev == 0:
        return True  # no data, don't reject
    drift_pct = (cur / prev - 1) * 100
    # Up signal: adverse = price moved DOWN before entry
    # Down signal: adverse = price moved UP before entry
    if side == "up" and drift_pct < -ADVERSE_PRECHECK_THRESHOLD_PCT:
        return False
    if side == "down" and drift_pct > ADVERSE_PRECHECK_THRESHOLD_PCT:
        return False
    return True


def regime_window_for_ts(ts_ms: int) -> str:
    """Bucket trades into ~weekly regime windows for P9 disjoint-window check."""
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    iso_year, iso_week, _ = dt.isocalendar()
    return f"{iso_year}W{iso_week:02d}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trades", type=Path, default=DEFAULT_TRADES)
    p.add_argument("--markets", type=Path, default=DEFAULT_MARKETS)
    p.add_argument("--klines-dir", type=Path, default=DEFAULT_KLINES_DIR)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = p.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_path = args.output_dir / f"sharp-move-alpha-decay-{today}.md"

    print("Loading klines ...")
    klines = load_klines(args.klines_dir)

    print("Loading markets metadata ...")
    markets = pq.read_table(args.markets, columns=["id", "slug", "end_date", "outcome_prices"])
    market_by_id: dict[str, dict] = {}
    for i in range(markets.num_rows):
        mid = markets.column("id")[i].as_py()
        market_by_id[mid] = {
            "slug": markets.column("slug")[i].as_py(),
            "end_date_ms": int(markets.column("end_date")[i].as_py().timestamp() * 1000),
            "outcome_prices": markets.column("outcome_prices")[i].as_py(),
        }
    print(f"  loaded {len(market_by_id):,} target markets")

    print("Streaming trades and simulating sharp_move ...")
    trades_pf = pq.ParquetFile(args.trades)
    eligible_trades: list[dict] = []
    rejections: dict[str, int] = defaultdict(int)
    scanned = 0

    for batch in trades_pf.iter_batches(batch_size=100_000):
        scanned += batch.num_rows
        # We need market_id, taker_direction, price, timestamp
        market_ids = batch.column("market_id").to_pylist()
        taker_dirs = batch.column("taker_direction").to_pylist()
        prices = batch.column("price").to_pylist()
        timestamps = batch.column("timestamp").to_pylist()

        for mid, td, price, ts_s in zip(market_ids, taker_dirs, prices, timestamps):
            mkt = market_by_id.get(mid)
            if mkt is None:
                rejections["market_not_in_target_set"] += 1
                continue
            slug = mkt["slug"]
            asset_prefix = slug.split("-", 1)[0]
            symbol = ASSET_FROM_SLUG.get(asset_prefix)
            if symbol is None:
                rejections["non_crypto_slug"] += 1
                continue
            if symbol not in klines:
                rejections["missing_klines"] += 1
                continue

            # Trade timestamp: SII timestamp is uint64 unix seconds based on schema
            ts_ms = ts_s * 1000 if ts_s < 10_000_000_000 else ts_s

            # NOTE: SII's BUY/SELL semantics may be inverted from share-direction
            # (it appears to encode the USDC-side, not the share-side). Skip
            # this filter and rely on price + momentum + market resolution to
            # determine alignment. Direction-correctness becomes a downstream
            # decomposition. This is a v1 simplification; a v2 would map
            # asset_id (token bought/sold) against markets.token1/token2 to
            # determine which OUTCOME side the trader actually took.
            _ = td  # taker_direction kept for future per-side analysis
            if price > SHARP_MOVE_MAX_ENTRY_PRICE:
                rejections["price_above_ceiling"] += 1
                continue
            secs_remaining = (mkt["end_date_ms"] - ts_ms) // 1000
            if secs_remaining < SHARP_MOVE_MIN_SECS_REMAINING or secs_remaining > 280:
                rejections["bad_time_to_resolution"] += 1
                continue

            mom = momentum_60s_pct(klines[symbol], ts_ms)
            if mom is None:
                rejections["missing_momentum_kline"] += 1
                continue
            if abs(mom) < SHARP_MOVE_MOMENTUM_THRESHOLD_PCT:
                rejections["momentum_below_threshold"] += 1
                continue

            # Direction inference: market slug is "<asset>-updown-5m-<epoch>";
            # we don't know up/down from trade row alone. We use price as proxy:
            # taker bought "yes" of either UP or DOWN side of the binary market.
            # For sharp_move alignment: if mom > 0, signal is Up; if mom < 0, Down.
            # Adverse check uses the side accordingly.
            side = "up" if mom > 0 else "down"
            if not adverse_check(klines[symbol], ts_ms, side):
                rejections["adverse_check_failed"] += 1
                continue

            # Determine outcome from market.outcome_prices.
            # Format observed: "['1', '0']" (answer1 won) or "['0', '1']" (answer2 won).
            # Convention: index 0 = answer1 = "Up" side; index 1 = answer2 = "Down" side.
            # (Verified empirically against the markets table answer1/answer2 columns.)
            op = mkt["outcome_prices"]
            try:
                op_list = ast.literal_eval(op) if isinstance(op, str) else op
                up_won = float(op_list[0]) > 0.5  # index 0 is the "Up" side
            except (ValueError, SyntaxError, IndexError, TypeError):
                rejections["unparseable_outcome"] += 1
                continue
            # The trade was on whichever side the simulated sharp_move signal predicted
            # (side variable above, inferred from momentum sign). Did that side win?
            won = (side == "up" and up_won) or (side == "down" and not up_won)

            # Approximate adverse_fill_bps: |momentum| - threshold, in bps
            # Real adverse fill needs queue-position which we don't have; this
            # is a conservative proxy.
            adverse_fill_bps = max(0.0, (abs(mom) - SHARP_MOVE_MOMENTUM_THRESHOLD_PCT) * 100)

            eligible_trades.append({
                "ts_ms": ts_ms,
                "asset": symbol,
                "price": price,
                "momentum_pct": mom,
                "side": side,
                "won": won,
                "adverse_fill_bps": adverse_fill_bps,
                "regime_window": regime_window_for_ts(ts_ms),
            })

        if scanned % 1_000_000 == 0:
            print(f"  scanned {scanned:,} trades, eligible {len(eligible_trades):,}")

    # Apply Markov gate simulation: per-asset, drop trades that would be
    # 4th consecutive loss within 1h on the same asset.
    print()
    print("Applying Markov gate (3-loss kill) ...")
    eligible_trades.sort(key=lambda r: r["ts_ms"])
    by_asset_loss_streak: dict[str, list[tuple[int, bool]]] = defaultdict(list)
    markov_kept: list[dict] = []
    for r in eligible_trades:
        a = r["asset"]
        # Trim history to last 1h
        cutoff = r["ts_ms"] - 3600_000
        by_asset_loss_streak[a] = [(t, w) for t, w in by_asset_loss_streak[a] if t >= cutoff]
        recent = by_asset_loss_streak[a]
        recent_losses = sum(1 for _, w in recent[-MARKOV_GATE_MAX_LOSSES:] if not w)
        if len(recent) >= MARKOV_GATE_MAX_LOSSES and recent_losses == MARKOV_GATE_MAX_LOSSES:
            rejections["markov_gate_blocked"] += 1
            continue
        markov_kept.append(r)
        by_asset_loss_streak[a].append((r["ts_ms"], r["won"]))

    print(f"  Markov-passed: {len(markov_kept):,}")

    # Aggregates
    n = len(markov_kept)
    wins = sum(1 for r in markov_kept if r["won"])
    wr = wins / n if n else 0
    wlb = wilson_lower_bound(wins, n)
    mean_adverse = sum(r["adverse_fill_bps"] for r in markov_kept) / n if n else 0
    distinct_windows = len({r["regime_window"] for r in markov_kept})
    p9_satisfied = distinct_windows >= 2

    # Verdict
    if n < 30:
        verdict = "INCONCLUSIVE — sample size below P5 floor (n<30)"
    elif not p9_satisfied:
        verdict = "INCONCLUSIVE — P9 fails (single regime window only)"
    elif wlb >= 0.55 and mean_adverse < 15:
        verdict = "PROMOTE sharp_move (skip tiny-live $60 step)"
    elif wlb < 0.50 or mean_adverse > 30:
        verdict = "KILL sharp_move on emmanuel"
    else:
        verdict = "CONFIRM via tiny-live $60 experiment"

    # Per-asset breakdown
    per_asset: dict[str, dict] = defaultdict(lambda: {"n": 0, "wins": 0})
    for r in markov_kept:
        a = r["asset"]
        per_asset[a]["n"] += 1
        if r["won"]:
            per_asset[a]["wins"] += 1

    # Write report
    report = []
    report.append(f"# Sharp Move Alpha Decay Backtest — {today}\n")
    report.append(f"Source: SII-WANGZJ Polymarket dataset (HuggingFace) + Binance 1m klines\n")
    report.append(f"Trades scanned: {scanned:,}")
    report.append(f"Sharp-move-eligible (post-filter): {len(eligible_trades):,}")
    report.append(f"Markov-passed (final population): {n:,}")
    report.append("")
    report.append("## Aggregates\n")
    report.append(f"- N (Markov-passed): **{n}**")
    report.append(f"- Wins: {wins} / Losses: {n - wins}")
    report.append(f"- Raw WR: {wr:.4f} ({wr*100:.2f}%)")
    report.append(f"- Wilson LB(WR, 95%): **{wlb:.4f}** ({wlb*100:.2f}%)")
    report.append(f"- Mean adverse_fill_bps (proxy): **{mean_adverse:.2f}**")
    report.append(f"- Distinct regime windows (ISO weeks): {distinct_windows}")
    report.append(f"- P9 satisfied (≥2 disjoint windows): **{p9_satisfied}**")
    report.append("")
    report.append("## Per-asset breakdown\n")
    report.append("| Asset | N | Wins | WR |")
    report.append("|---|---:|---:|---:|")
    for a in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"):
        d = per_asset[a]
        wr_a = d["wins"] / d["n"] if d["n"] else 0
        report.append(f"| {a} | {d['n']} | {d['wins']} | {wr_a:.4f} |")
    report.append("")
    report.append("## Rejection breakdown\n")
    report.append("| Reason | Count |")
    report.append("|---|---:|")
    for reason, count in sorted(rejections.items(), key=lambda x: -x[1]):
        report.append(f"| {reason} | {count:,} |")
    report.append("")
    report.append("## VERDICT\n")
    report.append(f"**{verdict}**\n")
    report.append("## Caveats\n")
    report.append("- `adverse_fill_bps` here is a momentum-magnitude proxy, NOT actual fill drift.")
    report.append("  Real adverse fill requires queue-position data which the SII dataset omits.")
    report.append("- Direction inference uses momentum sign; a real sharp_move signal also")
    report.append("  considers Polymarket's market-side mid-price drift before firing.")
    report.append("- Markov gate sim assumes immediate outcome resolution; actual emmanuel")
    report.append("  tracks resolved trades chronologically.")
    report.append("- Outcome parsed from market.outcome_prices field; relies on string format")
    report.append("  consistency in the source dataset.")

    report_text = "\n".join(report) + "\n"
    report_path.write_text(report_text)
    print()
    print(f"Report written: {report_path}")
    print()
    print("=" * 60)
    print(f"VERDICT: {verdict}")
    print(f"  N={n}  WilsonLB={wlb:.4f}  adverse_bps={mean_adverse:.2f}  windows={distinct_windows}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
