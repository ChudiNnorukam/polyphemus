"""walk_forward_cv.py - Per-signal walk-forward cross-validation analysis.

Runs 5-fold chronological WF-CV on momentum and RTDS signals using
trader analysis data. Reports per-split WR, mean WR, Kelly, and DSR
for each signal. Categorizes each signal as PASS or FAIL.

PASS criteria: 4/5 splits positive (test WR > breakeven), Kelly > 0%.
FAIL criteria: fewer than 4/5 splits positive OR Kelly <= 0%.

Usage:
    python -m polyphemus.tools.walk_forward_cv [--output .omc/zero/wf-cv-results.md]
"""

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Optional

# Add parent dirs to path for imports
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_ROOT.parent))

from polyphemus.tools.trader_stats import (
    walk_forward_cv,
    deflated_sharpe,
    kelly_criterion,
    hypothesis_test_wr,
)


def load_resolution_cache(cache_path: str) -> dict:
    """Load resolution cache as a lookup dict.

    Resolution caches map epoch_key -> direction ("Up"/"Down").
    Keys are like "1773603300_BTC_5m" or slug strings.

    Args:
        cache_path: Path to JSON resolution cache file.

    Returns:
        Dict mapping key -> resolved direction string.
    """
    with open(cache_path, "r") as f:
        data = json.load(f)

    if isinstance(data, dict):
        # Filter to only string values (slug -> "Up"/"Down" mappings)
        return {k: v for k, v in data.items() if isinstance(v, str)}
    return {}


def load_trades(trades_path: str) -> list[dict]:
    """Load raw trade data from JSON file.

    Args:
        trades_path: Path to trades JSON (list of trade dicts).

    Returns:
        List of trade dicts with keys: slug, epoch_ts, asset, window,
        outcome, side, price, size, timestamp.
    """
    with open(trades_path, "r") as f:
        data = json.load(f)

    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    return []


def resolve_trades(
    trades: list[dict],
    resolution_caches: list[dict],
    count_flats_as_losses: bool = False,
) -> tuple[list[dict], dict]:
    """Cross-reference trades with resolution caches to determine win/loss.

    Args:
        trades: List of trade dicts with epoch_ts, asset, window, outcome, price.
        resolution_caches: List of resolution dicts (key -> direction).
        count_flats_as_losses: If True, treat flat/pending resolutions as losses
            instead of excluding them. Gives a conservative WR estimate.

    Returns:
        Tuple of (resolved_trades, stats) where stats includes resolution counts.
    """
    # Merge all resolution caches
    merged_resolutions = {}
    for cache in resolution_caches:
        merged_resolutions.update(cache)

    resolved = []
    stats = {"resolved": 0, "flat": 0, "pending": 0, "total_matched": 0}
    for trade in trades:
        epoch_ts = trade.get("epoch_ts", 0)
        asset = trade.get("asset", "")
        window = trade.get("window", "5m")
        outcome = trade.get("outcome", "").lower()  # "up" or "down"
        price = trade.get("price", 0)

        if isinstance(price, str):
            try:
                price = float(price)
            except (ValueError, TypeError):
                continue

        if price <= 0 or price >= 1:
            continue

        # Build resolution key: "{epoch_ts}_{asset}_{window}"
        res_key = f"{epoch_ts}_{asset}_{window}"
        resolution = merged_resolutions.get(res_key, "").lower()

        if not resolution:
            stats["pending"] += 1
            if not count_flats_as_losses:
                continue
            # Treat as loss when counting flats
            resolved.append({
                "entry_price": price, "direction": outcome,
                "resolution": "pending", "won": False,
                "return": -price, "asset": asset, "epoch_ts": epoch_ts,
            })
            continue

        if resolution in ("pending", "flat", "unknown"):
            stats["flat"] += 1
            if not count_flats_as_losses:
                continue
            # Treat flat/unknown as loss (conservative)
            resolved.append({
                "entry_price": price, "direction": outcome,
                "resolution": resolution, "won": False,
                "return": -price, "asset": asset, "epoch_ts": epoch_ts,
            })
            continue

        stats["resolved"] += 1

        # Determine win: trade outcome matches resolution
        won = (outcome == resolution)

        # Compute return
        if won:
            ret = (1.0 - price)
        else:
            ret = -price

        resolved.append({
            "entry_price": price,
            "direction": outcome,
            "resolution": resolution,
            "won": won,
            "return": ret,
            "asset": asset,
            "epoch_ts": epoch_ts,
        })

    stats["total_matched"] = len(resolved)
    return resolved, stats


# Default signal price ranges (overridable via CLI)
DEFAULT_RANGES = {
    "momentum": (0.70, 0.95),
    "rtds": (0.40, 0.65),
}


def extract_signal_trades(
    resolved_trades: list[dict],
    signal_type: str,
    price_range: Optional[tuple[float, float]] = None,
) -> list[dict]:
    """Filter resolved trades by signal type based on entry price range.

    Args:
        resolved_trades: List of resolved trade dicts with 'won', 'return', 'entry_price'.
        signal_type: 'momentum' or 'rtds' to filter by.
        price_range: Optional (min, max) tuple. Uses DEFAULT_RANGES if None.

    Returns:
        List of trades matching the signal type's price range.
    """
    lo, hi = price_range or DEFAULT_RANGES.get(signal_type, (0, 1))
    return [t for t in resolved_trades if lo <= t["entry_price"] <= hi]


def analyze_signal(
    signal_name: str,
    trades: list[dict],
    n_splits: int = 5,
    breakeven: float = 0.50,
) -> dict:
    """Run full statistical analysis on a signal's trades.

    Args:
        signal_name: Human-readable signal name.
        trades: List of trade dicts with 'won' and 'return'.
        n_splits: Number of WF-CV folds.
        breakeven: Fee-adjusted breakeven WR.

    Returns:
        Dict with WF-CV, Kelly, DSR, hypothesis test, and PASS/FAIL verdict.
    """
    if len(trades) < 10:
        return {
            "signal": signal_name,
            "n_trades": len(trades),
            "verdict": "INSUFFICIENT_DATA",
            "reason": f"Only {len(trades)} trades, need >= 10 for analysis.",
        }

    returns = [t["return"] for t in trades]
    wins = sum(1 for t in trades if t["won"])
    total = len(trades)

    # 1. Walk-forward CV
    wf = walk_forward_cv(returns, n_splits=n_splits)

    # 2. Deflated Sharpe Ratio
    dsr = deflated_sharpe(returns, k=3)

    # 3. Kelly criterion
    winning_returns = [t["return"] for t in trades if t["won"]]
    losing_returns = [-t["return"] for t in trades if not t["won"]]
    avg_win = sum(winning_returns) / len(winning_returns) if winning_returns else 0
    avg_loss = sum(losing_returns) / len(losing_returns) if losing_returns else 0
    kelly = kelly_criterion(wins / total, avg_win, avg_loss)

    # 4. Hypothesis test vs breakeven
    hyp = hypothesis_test_wr(wins, total, breakeven=breakeven)

    # 5. PASS/FAIL verdict
    splits_positive = wf.get("splits_positive", 0)
    kelly_positive = kelly.get("full_kelly", 0) > 0
    pass_wfcv = splits_positive >= 4  # 4/5 splits positive
    verdict = "PASS" if (pass_wfcv and kelly_positive) else "FAIL"

    reason_parts = []
    if not pass_wfcv:
        reason_parts.append(f"WF-CV: only {splits_positive}/5 splits positive (need 4)")
    if not kelly_positive:
        reason_parts.append(f"Kelly={kelly.get('full_kelly', 0)*100:.1f}% (need > 0%)")
    reason = "; ".join(reason_parts) if reason_parts else "All criteria met"

    return {
        "signal": signal_name,
        "n_trades": total,
        "overall_wr": round(wins / total, 4),
        "avg_entry_price": round(sum(t["entry_price"] for t in trades) / total, 4),
        "walk_forward": wf,
        "deflated_sharpe": dsr,
        "kelly": kelly,
        "hypothesis": hyp,
        "verdict": verdict,
        "reason": reason,
    }


def format_results_markdown(results: list[dict]) -> str:
    """Format analysis results as markdown report.

    Args:
        results: List of per-signal analysis dicts.

    Returns:
        Markdown string.
    """
    lines = [
        "# Walk-Forward CV Results - Prophetic Strategy Phase 1",
        "",
        f"Generated: {_now_iso()}",
        "",
        "## Summary",
        "",
        "| Signal | Trades | WR | WF-CV (positive splits) | Kelly | DSR | Verdict |",
        "|--------|--------|-----|------------------------|-------|-----|---------|",
    ]

    for r in results:
        if r.get("verdict") == "INSUFFICIENT_DATA":
            lines.append(
                f"| {r['signal']} | {r['n_trades']} | - | - | - | - | INSUFFICIENT_DATA |"
            )
            continue

        wr = f"{r['overall_wr']*100:.1f}%"
        wf_splits = r["walk_forward"].get("splits_positive", 0)
        wf_total = len(r["walk_forward"].get("split_results", []))
        kelly_val = f"{r['kelly'].get('full_kelly', 0)*100:.1f}%"
        dsr_val = f"{r['deflated_sharpe'].get('dsr_value', 0):.2f}"
        verdict = r["verdict"]

        lines.append(
            f"| {r['signal']} | {r['n_trades']} | {wr} | {wf_splits}/{wf_total} | {kelly_val} | {dsr_val} | **{verdict}** |"
        )

    lines.append("")

    # Detailed per-signal sections
    for r in results:
        if r.get("verdict") == "INSUFFICIENT_DATA":
            lines.append(f"## {r['signal']}")
            lines.append(f"")
            lines.append(f"{r['reason']}")
            lines.append("")
            continue

        lines.append(f"## {r['signal']}")
        lines.append("")
        lines.append(f"- **Trades analyzed**: {r['n_trades']}")
        lines.append(f"- **Overall WR**: {r['overall_wr']*100:.1f}%")
        lines.append(f"- **Avg entry price**: {r['avg_entry_price']}")
        lines.append(f"- **Verdict**: **{r['verdict']}** - {r['reason']}")
        lines.append("")

        # Walk-forward splits
        lines.append("### Walk-Forward CV Splits")
        lines.append("")
        lines.append("| Split | Train N | Test N | Train WR | Test WR |")
        lines.append("|-------|---------|--------|----------|---------|")
        for split in r["walk_forward"].get("split_results", []):
            lines.append(
                f"| {split['split']} | {split['train_n']} | {split['test_n']} | "
                f"{split['train_wr']*100:.1f}% | {split['test_wr']*100:.1f}% |"
            )
        lines.append("")
        lines.append(f"Mean test WR: {r['walk_forward'].get('mean_test_wr', 0)*100:.1f}%")
        lines.append(f"Consistent: {r['walk_forward'].get('consistent', False)}")
        lines.append("")

        # Kelly
        lines.append("### Kelly Criterion")
        lines.append("")
        lines.append(f"- Full Kelly: {r['kelly'].get('full_kelly', 0)*100:.1f}%")
        lines.append(f"- Half Kelly: {r['kelly'].get('half_kelly', 0)*100:.1f}%")
        lines.append(f"- Odds ratio: {r['kelly'].get('odds_ratio', 0):.2f}")
        lines.append("")

        # DSR
        lines.append("### Deflated Sharpe Ratio")
        lines.append("")
        lines.append(f"- Sharpe: {r['deflated_sharpe'].get('sharpe_hat', 0):.4f}")
        lines.append(f"- DSR: {r['deflated_sharpe'].get('dsr_value', 0):.4f}")
        lines.append(f"- Overfit risk: {r['deflated_sharpe'].get('overfit_risk', 'UNKNOWN')}")
        lines.append("")

        # Hypothesis test
        lines.append("### Hypothesis Test (WR vs breakeven)")
        lines.append("")
        lines.append(f"- {r['hypothesis'].get('plain_english', '')}")
        lines.append("")

    # Recommendations
    passing = [r for r in results if r.get("verdict") == "PASS"]
    failing = [r for r in results if r.get("verdict") == "FAIL"]
    insufficient = [r for r in results if r.get("verdict") == "INSUFFICIENT_DATA"]

    lines.append("## Recommendations")
    lines.append("")
    if passing:
        names = ", ".join(r["signal"] for r in passing)
        lines.append(f"**PASS signals ({len(passing)})**: {names}")
        lines.append("These signals should be included in the ensemble shadow config.")
        lines.append("")
    if failing:
        names = ", ".join(r["signal"] for r in failing)
        lines.append(f"**FAIL signals ({len(failing)})**: {names}")
        lines.append("These signals should NOT be included without further tuning.")
        lines.append("")
    if insufficient:
        names = ", ".join(r["signal"] for r in insufficient)
        lines.append(f"**INSUFFICIENT DATA ({len(insufficient)})**: {names}")
        lines.append("Collect more data before evaluating.")
        lines.append("")

    if len(passing) >= 2:
        lines.append("**Ensemble recommendation**: Proceed with 2-signal ensemble shadow test.")
    elif len(passing) == 1:
        lines.append("**Single-signal fallback**: Only 1 signal passed. Deploy single-signal strategy.")
    else:
        lines.append("**No signals passed**: Re-tune parameters or collect more data before shadow.")

    lines.append("")
    return "\n".join(lines)


def _now_iso() -> str:
    """Return current UTC time as ISO string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def find_data_files() -> tuple[list[str], list[str]]:
    """Find resolution cache files and trade data files.

    Returns:
        Tuple of (resolution_cache_paths, trades_paths).
    """
    res_caches = []
    trade_files = []
    search_dirs = [
        Path(__file__).resolve().parent / ".backtest_cache",
        Path(__file__).resolve().parent.parent.parent / ".omc" / "trader-analysis",
    ]
    for d in search_dirs:
        if d.exists():
            for f in d.rglob("*resolution_cache*.json"):
                res_caches.append(str(f))
            for f in d.rglob("*trades_raw*.json"):
                trade_files.append(str(f))
    return res_caches, trade_files


def main():
    parser = argparse.ArgumentParser(description="Walk-forward CV for prophetic strategy signals")
    parser.add_argument(
        "--cache",
        type=str,
        nargs="*",
        help="Path(s) to resolution cache JSON files",
    )
    parser.add_argument(
        "--trades",
        type=str,
        nargs="*",
        help="Path(s) to trades_raw JSON files",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=".omc/zero/wf-cv-results.md",
        help="Output path for results markdown",
    )
    parser.add_argument(
        "--splits",
        type=int,
        default=5,
        help="Number of WF-CV splits (default 5)",
    )
    parser.add_argument(
        "--momentum-range",
        type=str,
        default="0.70-0.95",
        help="Momentum entry price range as 'min-max' (default 0.70-0.95)",
    )
    parser.add_argument(
        "--rtds-range",
        type=str,
        default="0.40-0.65",
        help="RTDS entry price range as 'min-max' (default 0.40-0.65)",
    )
    args = parser.parse_args()

    # Parse configurable price ranges
    def parse_range(s: str) -> tuple[float, float]:
        lo, hi = s.split("-")
        return (float(lo), float(hi))

    momentum_range = parse_range(args.momentum_range)
    rtds_range = parse_range(args.rtds_range)

    # Find data files
    if args.cache and args.trades:
        cache_files = args.cache
        trade_files = args.trades
    else:
        cache_files, trade_files = find_data_files()

    if not cache_files and not trade_files:
        print("No data files found. Provide --cache and --trades paths.")
        sys.exit(1)

    # Load resolution caches
    resolution_caches = []
    for cf in cache_files:
        try:
            cache = load_resolution_cache(cf)
            resolution_caches.append(cache)
            print(f"  Resolution cache: {len(cache)} entries from {cf}")
        except Exception as e:
            print(f"  Error loading cache {cf}: {e}")

    # Load trade data
    all_trades = []
    for tf in trade_files:
        try:
            trades = load_trades(tf)
            all_trades.extend(trades)
            print(f"  Trades: {len(trades)} from {tf}")
        except Exception as e:
            print(f"  Error loading trades {tf}: {e}")

    print(f"Total resolution entries: {sum(len(c) for c in resolution_caches)}")
    print(f"Total raw trades: {len(all_trades)}")

    # Cross-reference trades with resolutions (excluding flats)
    resolved_trades, res_stats = resolve_trades(all_trades, resolution_caches, count_flats_as_losses=False)
    print(f"Resolved trades: {len(resolved_trades)} (flat/pending excluded: {res_stats['flat'] + res_stats['pending']})")

    # Also resolve with flats-as-losses for conservative WR
    resolved_conservative, cons_stats = resolve_trades(all_trades, resolution_caches, count_flats_as_losses=True)

    # Extract per-signal trades by entry price range
    m_lo, m_hi = momentum_range
    r_lo, r_hi = rtds_range
    momentum_trades = extract_signal_trades(resolved_trades, "momentum", momentum_range)
    rtds_trades = extract_signal_trades(resolved_trades, "rtds", rtds_range)
    # Conservative versions
    momentum_trades_cons = extract_signal_trades(resolved_conservative, "momentum", momentum_range)
    rtds_trades_cons = extract_signal_trades(resolved_conservative, "rtds", rtds_range)

    print(f"Momentum signal trades ({m_lo}-{m_hi}): {len(momentum_trades)}")
    print(f"RTDS signal trades ({r_lo}-{r_hi}): {len(rtds_trades)}")

    # Compute fee-adjusted breakeven for each signal type
    momentum_breakeven = 0.53
    rtds_breakeven = 0.50

    # Run analysis (standard: excluding flats)
    results = []
    results.append(analyze_signal(f"Momentum ({m_lo}-{m_hi})", momentum_trades, args.splits, momentum_breakeven))
    results.append(analyze_signal(f"RTDS Near-50c ({r_lo}-{r_hi})", rtds_trades, args.splits, rtds_breakeven))

    # Run conservative analysis (flats as losses)
    results_cons = []
    results_cons.append(analyze_signal(f"Momentum ({m_lo}-{m_hi}) [flats=loss]", momentum_trades_cons, args.splits, momentum_breakeven))
    results_cons.append(analyze_signal(f"RTDS Near-50c ({r_lo}-{r_hi}) [flats=loss]", rtds_trades_cons, args.splits, rtds_breakeven))

    # Format and write results
    md = format_results_markdown(results)

    # Append conservative WR comparison
    md += "\n## Conservative WR (flats counted as losses)\n\n"
    md += "| Signal | Trades | WR (excl flats) | WR (flats=loss) | Delta |\n"
    md += "|--------|--------|-----------------|-----------------|-------|\n"
    for std, cons in zip(results, results_cons):
        if std.get("verdict") == "INSUFFICIENT_DATA":
            continue
        std_wr = std["overall_wr"] * 100
        cons_wr = cons.get("overall_wr", 0) * 100
        delta = std_wr - cons_wr
        md += f"| {std['signal']} | {std['n_trades']}/{cons['n_trades']} | {std_wr:.1f}% | {cons_wr:.1f}% | {delta:+.1f}pp |\n"
    md += f"\nResolution stats: {res_stats['resolved']} resolved, {res_stats['flat']} flat, {res_stats['pending']} pending\n"

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write(md)

    print(f"\nResults written to {output_path}")
    print("\n" + md[:2000])


if __name__ == "__main__":
    main()
