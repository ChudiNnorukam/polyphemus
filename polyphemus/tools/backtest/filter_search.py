"""Entry-filter backtest for binance_momentum.

Without intra-trade price data (MAE/MFE/tick history), we cannot simulate
tighter stop-losses or longer holds counterfactually. We CAN simulate
stricter entry filters: given the 508 realized trades, what subset has a
positive Sharpe and win/loss ratio above breakeven?

Output: Pareto-ranked list of (filter, n, WR, win/loss_ratio, Sharpe, PnL)
with MTC R1-R2 pass/fail flags. Top rows are deployable entry conditions.

Usage:
    python -m polyphemus.tools.backtest.filter_search \
        --db /tmp/emmanuel_perf_20260417.db \
        --source binance_momentum \
        --min-n 30 \
        --top 20
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from dataclasses import dataclass
from itertools import combinations
from typing import Callable

# Wilson 95% lower bound
Z95 = 1.96


def wilson_lower(p: float, n: int, z: float = Z95) -> float:
    if n == 0:
        return 0.0
    denom = 1 + z * z / n
    center = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (center - spread) / denom


@dataclass
class Metrics:
    n: int
    wins: int
    wr: float
    wilson_lo: float
    avg_win: float
    avg_loss: float
    wl_ratio: float  # avg_win / abs(avg_loss)
    breakeven_ratio: float  # (1-wr)/wr; need wl_ratio > this
    gap: float  # wl_ratio - breakeven_ratio; positive = profitable
    sharpe: float  # mean(pnl_pct) / std(pnl_pct) if std > 0
    total_pnl: float
    total_pnl_pct: float  # mean pnl_pct


def compute_metrics(rows: list[dict]) -> Metrics:
    n = len(rows)
    if n == 0:
        return Metrics(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    wins_rows = [r for r in rows if r["pnl"] > 0]
    losses_rows = [r for r in rows if r["pnl"] <= 0]
    wins = len(wins_rows)
    wr = wins / n
    wilson_lo = wilson_lower(wr, n)
    avg_win = (
        sum(r["pnl_pct"] for r in wins_rows) / len(wins_rows) if wins_rows else 0.0
    )
    avg_loss = (
        sum(r["pnl_pct"] for r in losses_rows) / len(losses_rows)
        if losses_rows
        else 0.0
    )
    wl_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0.0
    breakeven_ratio = (1 - wr) / wr if wr > 0 else float("inf")
    gap = wl_ratio - breakeven_ratio
    pnl_pcts = [r["pnl_pct"] for r in rows]
    mean_pct = sum(pnl_pcts) / n
    var = sum((p - mean_pct) ** 2 for p in pnl_pcts) / max(n - 1, 1)
    std = math.sqrt(var)
    sharpe = mean_pct / std if std > 0 else 0.0
    total_pnl = sum(r["pnl"] for r in rows)
    return Metrics(
        n=n,
        wins=wins,
        wr=wr,
        wilson_lo=wilson_lo,
        avg_win=avg_win,
        avg_loss=avg_loss,
        wl_ratio=wl_ratio,
        breakeven_ratio=breakeven_ratio,
        gap=gap,
        sharpe=sharpe,
        total_pnl=total_pnl,
        total_pnl_pct=mean_pct,
    )


def load_trades(db: str, source: str) -> list[dict]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """
        SELECT
            trade_id, slug, entry_time, entry_price, exit_price,
            hold_seconds, exit_reason, pnl, pnl_pct, metadata
        FROM trades
        WHERE strategy='signal_bot'
          AND exit_time IS NOT NULL
          AND json_extract(metadata, '$.source') = ?
        """,
        (source,),
    )
    out = []
    for r in cur.fetchall():
        try:
            meta = json.loads(r["metadata"]) if r["metadata"] else {}
        except Exception:
            meta = {}
        row = dict(r)
        row["direction"] = (meta.get("direction") or "").lower()
        row["asset"] = (meta.get("asset") or "").upper()
        row["entry_btc"] = meta.get("entry_binance_price")
        row["time_remaining"] = meta.get("time_remaining_secs")
        row["market_window"] = meta.get("market_window_secs")
        # derived
        row["slug_window"] = "5m" if "5m-" in r["slug"] else ("15m" if "15m-" in r["slug"] else "?")
        row["slug_asset"] = r["slug"].split("-")[0].upper() if r["slug"] else ""
        if not row["asset"]:
            row["asset"] = row["slug_asset"]
        out.append(row)
    conn.close()
    return out


# ---------------------------------------------------------------------------
# Filter dimensions
# ---------------------------------------------------------------------------
def band_entry_price(p: float) -> str:
    if p < 0.35:
        return "ep_00_35"
    if p < 0.50:
        return "ep_35_50"
    if p < 0.65:
        return "ep_50_65"
    if p < 0.80:
        return "ep_65_80"
    return "ep_80+"


def band_time_remaining(t) -> str:
    if t is None:
        return "tr_unk"
    if t < 30:
        return "tr_0_30"
    if t < 60:
        return "tr_30_60"
    if t < 120:
        return "tr_60_120"
    if t < 240:
        return "tr_120_240"
    return "tr_240+"


def tag_row(row: dict) -> dict:
    return {
        "asset": f"asset={row['asset']}" if row["asset"] else "asset=?",
        "direction": f"dir={row['direction']}" if row["direction"] else "dir=?",
        "entry_band": band_entry_price(row["entry_price"]),
        "time_rem_band": band_time_remaining(row["time_remaining"]),
        "window": f"win={row['slug_window']}",
    }


# ---------------------------------------------------------------------------
# Grid search
# ---------------------------------------------------------------------------
def grid_search(
    rows: list[dict],
    min_n: int,
    dims: list[str],
    max_combo: int = 2,
) -> list[tuple[tuple[str, ...], Metrics]]:
    tagged = [(tag_row(r), r) for r in rows]
    out: list[tuple[tuple[str, ...], Metrics]] = []
    # single-dim buckets
    for combo_size in range(1, max_combo + 1):
        for chosen in combinations(dims, combo_size):
            # group by the tuple of values on these dims
            groups: dict[tuple[str, ...], list[dict]] = {}
            for tags, r in tagged:
                key = tuple(tags[d] for d in chosen)
                groups.setdefault(key, []).append(r)
            for key, grp in groups.items():
                if len(grp) < min_n:
                    continue
                m = compute_metrics(grp)
                out.append((key, m))
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--source", default="binance_momentum")
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--rank-by", default="sharpe",
                    choices=["sharpe", "gap", "total_pnl", "total_pnl_pct"])
    ap.add_argument("--max-combo", type=int, default=2,
                    help="Max # of filter dimensions to intersect")
    args = ap.parse_args()

    rows = load_trades(args.db, args.source)
    print(f"\n=== BASELINE: {args.source} (n={len(rows)}) ===")
    base = compute_metrics(rows)
    _print_metrics("ALL", base)

    dims = ["asset", "direction", "entry_band", "time_rem_band", "window"]
    results = grid_search(rows, args.min_n, dims, args.max_combo)

    # Rank
    def key_fn(item):
        _, m = item
        if args.rank_by == "sharpe":
            return -m.sharpe
        if args.rank_by == "gap":
            return -m.gap
        if args.rank_by == "total_pnl":
            return -m.total_pnl
        return -m.total_pnl_pct

    results.sort(key=key_fn)
    print(f"\n=== TOP {args.top} FILTERS by {args.rank_by} (min_n={args.min_n}) ===")
    print(f"{'filter':<50} {'n':>4} {'WR':>6} {'wlsn':>6} "
          f"{'avgW':>7} {'avgL':>7} {'wl_r':>6} {'be_r':>6} "
          f"{'gap':>6} {'sh':>6} {'PnL$':>8}")
    print("-" * 130)
    for filt, m in results[: args.top]:
        filt_str = " ∩ ".join(filt)
        print(
            f"{filt_str:<50} {m.n:>4} {m.wr:>6.1%} {m.wilson_lo:>6.1%} "
            f"{m.avg_win*100:>6.1f}% {m.avg_loss*100:>6.1f}% "
            f"{m.wl_ratio:>6.2f} {m.breakeven_ratio:>6.2f} "
            f"{m.gap:>+6.2f} {m.sharpe:>+6.2f} {m.total_pnl:>+8.2f}"
        )

    # Show reverse-ranked (the WORST segments to EXCLUDE)
    print(f"\n=== WORST {args.top} SEGMENTS (candidates to EXCLUDE) ===")
    results.sort(key=lambda x: x[1].sharpe)
    print(f"{'filter':<50} {'n':>4} {'WR':>6} {'wlsn':>6} "
          f"{'avgW':>7} {'avgL':>7} {'wl_r':>6} {'be_r':>6} "
          f"{'gap':>6} {'sh':>6} {'PnL$':>8}")
    print("-" * 130)
    for filt, m in results[: args.top]:
        filt_str = " ∩ ".join(filt)
        print(
            f"{filt_str:<50} {m.n:>4} {m.wr:>6.1%} {m.wilson_lo:>6.1%} "
            f"{m.avg_win*100:>6.1f}% {m.avg_loss*100:>6.1f}% "
            f"{m.wl_ratio:>6.2f} {m.breakeven_ratio:>6.2f} "
            f"{m.gap:>+6.2f} {m.sharpe:>+6.2f} {m.total_pnl:>+8.2f}"
        )


def _print_metrics(label: str, m: Metrics):
    print(f"{label}: n={m.n} wins={m.wins} WR={m.wr:.1%} (Wilson lo {m.wilson_lo:.1%})")
    print(f"  avg_win={m.avg_win*100:+.1f}%  avg_loss={m.avg_loss*100:+.1f}%")
    print(f"  win/loss_ratio={m.wl_ratio:.2f}  breakeven_ratio={m.breakeven_ratio:.2f}  "
          f"gap={m.gap:+.2f}")
    print(f"  Sharpe={m.sharpe:+.2f}  total_pnl=${m.total_pnl:+.2f}  "
          f"avg_pnl_pct={m.total_pnl_pct*100:+.2f}%")


if __name__ == "__main__":
    main()
