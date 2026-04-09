"""Trade analyzer - Import performance.db trades and produce statistical reports.

Connects to a local copy of performance.db (SCP from VPS), applies all
statistical tools from trader_stats.py, and produces structured output
with mandatory guardrails (Wilson CI, R8 labels, minimum-n enforcement).

Usage:
    python3 -m polyphemus.research.trade_analyzer \
        --db /path/to/performance.db \
        --min-price 0.45 --max-price 0.50 \
        [--era post_restriction] [--json]
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Add parent dirs for imports
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_ROOT.parent))

from polyphemus.fees import (
    breakeven_wr,
    fee_adjusted_pnl,
    taker_fee_per_share,
)
from polyphemus.tools.trader_stats import (
    bootstrap_max_drawdown,
    deflated_sharpe,
    hypothesis_test_wr,
    kelly_criterion,
    walk_forward_cv,
    wilson_ci,
)


# Config eras (from MEMORY.md)
ERAS = {
    "legacy": {"before": 1743033600, "description": "Before Mar 27 - all prices, all hours"},
    "post_restriction": {"after": 1743033600, "description": "Mar 27+ - 0.45-0.50, hours 22-06 UTC"},
    "fg_labeled": {"after": 1743292800, "description": "Mar 30+ - has fg_at_entry"},
}


def load_trades(db_path: str, min_price: float = 0.0, max_price: float = 1.0,
                era: str = None, asset: str = None) -> list[dict]:
    """Load clean trades from performance.db with filters."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    where = ["is_error = 0", "exit_time IS NOT NULL", "pnl IS NOT NULL"]
    params = []

    if min_price > 0:
        where.append("entry_price >= ?")
        params.append(min_price)
    if max_price < 1.0:
        where.append("entry_price <= ?")
        params.append(max_price)
    if asset:
        where.append("slug LIKE ?")
        params.append(f"%{asset.lower()}%")
    if era and era in ERAS:
        era_cfg = ERAS[era]
        if "after" in era_cfg:
            where.append("entry_time >= ?")
            params.append(era_cfg["after"])
        if "before" in era_cfg:
            where.append("entry_time < ?")
            params.append(era_cfg["before"])

    query = f"""
        SELECT trade_id, slug, entry_time, entry_price, entry_size,
               exit_time, exit_price, exit_reason, pnl, pnl_pct,
               hold_seconds, metadata, strategy, is_error
        FROM trades
        WHERE {' AND '.join(where)}
        ORDER BY entry_time
    """
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def reconciliation_report(db_path: str) -> dict:
    """Produce a data quality report for the DB."""
    conn = sqlite3.connect(db_path)
    total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    with_exit = conn.execute("SELECT COUNT(*) FROM trades WHERE exit_time IS NOT NULL").fetchone()[0]
    null_pnl = conn.execute("SELECT COUNT(*) FROM trades WHERE exit_time IS NOT NULL AND pnl IS NULL").fetchone()[0]
    errors = conn.execute("SELECT COUNT(*) FROM trades WHERE is_error = 1").fetchone()[0]
    open_pos = conn.execute("SELECT COUNT(*) FROM trades WHERE exit_time IS NULL").fetchone()[0]

    dates = conn.execute(
        "SELECT MIN(entry_time), MAX(entry_time) FROM trades WHERE exit_time IS NOT NULL"
    ).fetchone()
    conn.close()

    return {
        "total_trades": total,
        "closed_trades": with_exit,
        "open_positions": open_pos,
        "null_pnl": null_pnl,
        "error_trades": errors,
        "clean_trades": with_exit - null_pnl - errors,
        "date_range": {
            "start": datetime.fromtimestamp(dates[0]).isoformat() if dates[0] else None,
            "end": datetime.fromtimestamp(dates[1]).isoformat() if dates[1] else None,
        },
    }


def analyze_bucket(trades: list[dict], bucket_name: str, category: str = "crypto") -> dict:
    """Run full statistical analysis on a bucket of trades."""
    n = len(trades)
    if n == 0:
        return {"bucket": bucket_name, "n": 0, "message": "No trades in bucket"}

    wins = sum(1 for t in trades if t["pnl"] > 0)
    losses = n - wins
    pnls = [t["pnl"] for t in trades]
    avg_entry = sum(t["entry_price"] for t in trades) / n

    # Fee-adjusted break-even
    be_wr = breakeven_wr(avg_entry, mode="taker", category=category)

    # Hypothesis test
    ht = hypothesis_test_wr(wins, n, breakeven=be_wr)

    # Wilson CI
    ci = wilson_ci(wins, n)

    # Kelly (only if n >= 30)
    kelly = None
    if n >= 30:
        win_pnls = [t["pnl"] for t in trades if t["pnl"] > 0]
        loss_pnls = [abs(t["pnl"]) for t in trades if t["pnl"] <= 0]
        avg_win = sum(win_pnls) / len(win_pnls) if win_pnls else 0
        avg_loss = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0
        if avg_loss > 0:
            kelly = kelly_criterion(wins / n, avg_win, avg_loss)
    else:
        kelly = {"message": f"n={n} < 30: Kelly computation blocked. Need {30 - n} more trades."}

    # Walk-forward (only if n >= 20)
    wf = None
    if n >= 20:
        wf = walk_forward_cv(pnls, n_splits=min(5, n // 4))

    # DSR
    dsr = deflated_sharpe(pnls, k=1)

    # Bootstrap drawdown (only if n >= 10)
    dd = None
    if n >= 10:
        dd = bootstrap_max_drawdown(pnls)

    # Summary stats
    total_pnl = sum(pnls)
    avg_pnl = total_pnl / n
    fee_per_trade = taker_fee_per_share(avg_entry, category)

    return {
        "bucket": bucket_name,
        "n": n,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / n, 4),
        "wilson_ci_95": (round(ci[0], 4), round(ci[1], 4)),
        "r8_label": ht["r8_label"],
        "breakeven_wr": round(be_wr, 4),
        "avg_entry_price": round(avg_entry, 4),
        "fee_per_trade": round(fee_per_trade, 6),
        "total_pnl": round(total_pnl, 4),
        "avg_pnl": round(avg_pnl, 4),
        "hypothesis_test": ht,
        "kelly": kelly,
        "walk_forward": wf,
        "dsr": dsr,
        "bootstrap_drawdown": dd,
    }


def segment_by_price(trades: list[dict], step: float = 0.05) -> dict:
    """Segment trades into price buckets."""
    buckets = {}
    for t in trades:
        p = t["entry_price"]
        bucket_low = round(int(p / step) * step, 2)
        bucket_high = round(bucket_low + step, 2)
        key = f"{bucket_low:.2f}-{bucket_high:.2f}"
        if key not in buckets:
            buckets[key] = []
        buckets[key].append(t)
    return buckets


def segment_by_hour(trades: list[dict]) -> dict:
    """Segment trades by hour UTC."""
    buckets = {}
    for t in trades:
        hour = int(datetime.fromtimestamp(t["entry_time"]).strftime("%H"))
        key = f"UTC_{hour:02d}"
        if key not in buckets:
            buckets[key] = []
        buckets[key].append(t)
    return buckets


def segment_by_fg(trades: list[dict]) -> dict:
    """Segment trades by Fear & Greed regime from metadata."""
    buckets = {"fg_unknown": [], "fg_extreme_fear": [], "fg_fear": [], "fg_neutral": [],
               "fg_greed": [], "fg_extreme_greed": []}
    for t in trades:
        meta = {}
        if t.get("metadata"):
            try:
                meta = json.loads(t["metadata"])
            except (json.JSONDecodeError, TypeError):
                pass
        fg = meta.get("fg_at_entry")
        if fg is None:
            buckets["fg_unknown"].append(t)
        elif fg <= 20:
            buckets["fg_extreme_fear"].append(t)
        elif fg <= 40:
            buckets["fg_fear"].append(t)
        elif fg <= 60:
            buckets["fg_neutral"].append(t)
        elif fg <= 80:
            buckets["fg_greed"].append(t)
        else:
            buckets["fg_extreme_greed"].append(t)
    return {k: v for k, v in buckets.items() if v}


def print_report(recon: dict, overall: dict, segments: dict):
    """Print a formatted terminal report."""
    print("=" * 70)
    print("TRADE ANALYSIS REPORT")
    print("=" * 70)

    print("\n--- Reconciliation ---")
    print(f"  Total trades:    {recon['total_trades']}")
    print(f"  Clean trades:    {recon['clean_trades']}")
    print(f"  Error trades:    {recon['error_trades']}")
    print(f"  Open positions:  {recon['open_positions']}")
    print(f"  NULL P&L:        {recon['null_pnl']}")
    print(f"  Date range:      {recon['date_range']['start']} to {recon['date_range']['end']}")

    print("\n--- Overall ---")
    _print_bucket(overall)

    for seg_name, seg_buckets in segments.items():
        print(f"\n--- Segmented by {seg_name} ---")
        for bucket_name, result in sorted(seg_buckets.items()):
            if result.get("n", 0) > 0:
                _print_bucket(result)
                print()


def _print_bucket(b: dict):
    """Print a single bucket analysis."""
    if b.get("n", 0) == 0:
        return
    ci = b.get("wilson_ci_95", (0, 0))
    print(f"  [{b['bucket']}] n={b['n']} ({b['r8_label']})")
    print(f"    WR: {b['win_rate']*100:.1f}% [{ci[0]*100:.1f}%, {ci[1]*100:.1f}%]  "
          f"BE: {b['breakeven_wr']*100:.1f}%")
    print(f"    P&L: ${b['total_pnl']:.2f} (avg ${b['avg_pnl']:.4f}/trade)  "
          f"Fee: ${b['fee_per_trade']:.4f}/share")

    if b.get("kelly") and isinstance(b["kelly"], dict) and "half_kelly" in b["kelly"]:
        print(f"    Kelly: {b['kelly']['half_kelly']*100:.1f}% (half)  "
              f"Odds: {b['kelly'].get('odds_ratio', 0):.2f}")
    elif b.get("kelly") and isinstance(b["kelly"], dict) and "message" in b["kelly"]:
        print(f"    Kelly: {b['kelly']['message']}")

    if b.get("dsr"):
        print(f"    DSR: {b['dsr']['dsr_value']:.2f} (Sharpe={b['dsr']['sharpe_hat']:.2f}, "
              f"overfit={b['dsr']['overfit_risk']})")

    if b.get("walk_forward"):
        wf = b["walk_forward"]
        print(f"    WF-CV: {wf.get('splits_positive', 0)}/{len(wf.get('split_results', []))} positive  "
              f"mean test WR={wf.get('mean_test_wr', 0)*100:.1f}%")

    if b.get("bootstrap_drawdown"):
        dd = b["bootstrap_drawdown"]
        print(f"    Bootstrap DD P{dd['percentile']}: ${dd['p_drawdown']:.2f}")


def main():
    parser = argparse.ArgumentParser(description="Analyze Polymarket trades from performance.db")
    parser.add_argument("--db", required=True, help="Path to performance.db")
    parser.add_argument("--min-price", type=float, default=0.0, help="Minimum entry price filter")
    parser.add_argument("--max-price", type=float, default=1.0, help="Maximum entry price filter")
    parser.add_argument("--era", choices=list(ERAS.keys()), help="Config era filter")
    parser.add_argument("--asset", help="Asset filter (e.g., btc, eth)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--category", default="crypto", help="Fee category")
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"ERROR: Database not found: {args.db}")
        sys.exit(1)

    # Reconciliation
    recon = reconciliation_report(args.db)

    # Load trades
    trades = load_trades(args.db, args.min_price, args.max_price, args.era, args.asset)

    if not trades:
        print(f"No trades found matching filters. Reconciliation: {json.dumps(recon, indent=2)}")
        sys.exit(0)

    # Overall analysis
    overall = analyze_bucket(trades, "overall", args.category)

    # Segmented analyses
    segments = {}

    price_buckets = segment_by_price(trades)
    segments["price"] = {k: analyze_bucket(v, k, args.category) for k, v in price_buckets.items()}

    hour_buckets = segment_by_hour(trades)
    segments["hour_utc"] = {k: analyze_bucket(v, k, args.category) for k, v in hour_buckets.items()}

    fg_buckets = segment_by_fg(trades)
    if len(fg_buckets) > 1 or "fg_unknown" not in fg_buckets:
        segments["fear_greed"] = {k: analyze_bucket(v, k, args.category) for k, v in fg_buckets.items()}

    if args.json:
        output = {
            "reconciliation": recon,
            "overall": overall,
            "segments": segments,
        }
        print(json.dumps(output, indent=2, default=str))
    else:
        print_report(recon, overall, segments)


if __name__ == "__main__":
    main()
