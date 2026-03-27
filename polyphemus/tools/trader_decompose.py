"""trader_decompose.py — 8-dimension strategy extraction + ReplicaStrategy generation.

Decomposes a trader's activity into interpretable strategy dimensions:
1. Entry Price Distribution
2. Entry Timing (seconds into epoch)
3. Asset Preference
4. Direction Bias
5. Position Sizing
6. Order Type (maker/taker inference)
7. Regime Conditioning
8. Exit Pattern (HTR vs active exit)

Then generates a ReplicaStrategy with .env overrides compatible with
signal_guard.py and market_maker.py configs.
"""

import math
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from polyphemus.tools.trader_stats import (
    _r8_label,
    beta_binomial_update,
    hypothesis_test_wr,
    kelly_criterion,
    wilson_ci,
)


@dataclass
class ReplicaStrategy:
    """Output of strategy replication: .env overrides + metadata."""
    strategy_type: str  # "momentum", "pair_cost_arb", "rtds", "mixed"
    env_overrides: dict[str, str]
    warnings: list[str] = field(default_factory=list)
    confidence: float = 0.0
    source_trader: str = ""
    n_trades_analyzed: int = 0


def _load_trades(db_path: str, source_trader: str, crypto_only: bool = True) -> list[dict]:
    """Load trades from SQLite into list of dicts."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    query = "SELECT * FROM trader_trades WHERE source_trader = ?"
    if crypto_only:
        query += " AND market_type IN ('updown_5m', 'updown_15m')"
    query += " ORDER BY timestamp ASC"
    rows = conn.execute(query, (source_trader,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _load_resolved_trades(db_path: str, source_trader: str) -> list[dict]:
    """Load only trades with known resolution (not PENDING or UNKNOWN)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT * FROM trader_trades
           WHERE source_trader = ?
           AND TRIM(market_type) IN ('updown_5m', 'updown_15m')
           AND resolution NOT IN ('PENDING', 'UNKNOWN')
           AND side IN ('BUY', 'SELL')
           AND price > 0
           ORDER BY timestamp ASC""",
        (source_trader,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============================================================
#  Dimension 1: Entry Price Distribution
# ============================================================

def extract_entry_price_distribution(db_path: str, source_trader: str) -> dict:
    """Analyze entry price distribution.

    Returns histogram bins, statistics, and price band classification.
    """
    trades = _load_trades(db_path, source_trader)
    prices = [t["price"] for t in trades if t["price"] and t["price"] > 0]

    if not prices:
        return {"n": 0, "interpretation": "No trades with valid prices."}

    prices.sort()
    n = len(prices)
    mean_p = sum(prices) / n
    median_p = prices[n // 2]
    min_p = prices[0]
    max_p = prices[-1]
    std_p = math.sqrt(sum((p - mean_p) ** 2 for p in prices) / max(n - 1, 1))

    # Price band classification
    deep = sum(1 for p in prices if p < 0.60)
    mid = sum(1 for p in prices if 0.60 <= p < 0.80)
    high = sum(1 for p in prices if p >= 0.80)

    if deep > n * 0.5:
        entry_mode = "deep"
    elif high > n * 0.5:
        entry_mode = "high"
    elif mid > n * 0.5:
        entry_mode = "mid"
    else:
        entry_mode = "mixed"

    # Simple histogram (10 bins from 0.0 to 1.0)
    bins = [0] * 10
    for p in prices:
        idx = min(int(p * 10), 9)
        bins[idx] += 1

    # Find modes (bins with local maxima)
    modes = []
    for i in range(len(bins)):
        left = bins[i - 1] if i > 0 else 0
        right = bins[i + 1] if i < len(bins) - 1 else 0
        if bins[i] > left and bins[i] > right and bins[i] > n * 0.05:
            modes.append(round((i + 0.5) / 10, 2))

    return {
        "n": n,
        "mean": round(mean_p, 4),
        "median": round(median_p, 4),
        "std": round(std_p, 4),
        "min": round(min_p, 4),
        "max": round(max_p, 4),
        "entry_mode": entry_mode,
        "band_counts": {"deep_lt60": deep, "mid_60_80": mid, "high_ge80": high},
        "histogram_bins": bins,
        "modes": modes,
        "r8_label": _r8_label(n),
        "interpretation": (
            f"Entry prices: mean={mean_p:.2f}, median={median_p:.2f}, "
            f"mode={entry_mode} ({deep}/{mid}/{high} deep/mid/high). "
            f"{_r8_label(n)} sample (n={n})."
        ),
    }


# ============================================================
#  Dimension 2: Entry Timing
# ============================================================

def extract_timing_pattern(db_path: str, source_trader: str) -> dict:
    """Analyze seconds-into-epoch distribution."""
    trades = _load_trades(db_path, source_trader)

    offsets = []
    for t in trades:
        ts = t["timestamp"]
        window_secs = 300 if t["window"] == "5m" else 900 if t["window"] == "15m" else 300
        epoch_start = (int(ts) // window_secs) * window_secs
        offset = ts - epoch_start
        if 0 <= offset <= window_secs:
            offsets.append(offset)

    if not offsets:
        return {"n": 0, "interpretation": "No timing data available."}

    n = len(offsets)
    mean_offset = sum(offsets) / n
    early = sum(1 for o in offsets if o < 60)
    mid = sum(1 for o in offsets if 60 <= o < 180)
    late = sum(1 for o in offsets if o >= 180)

    if early > n * 0.5:
        timing_mode = "early"
    elif late > n * 0.5:
        timing_mode = "late"
    elif mid > n * 0.5:
        timing_mode = "mid"
    else:
        timing_mode = "spread"

    return {
        "n": n,
        "mean_offset_secs": round(mean_offset, 1),
        "timing_mode": timing_mode,
        "counts": {"early_lt60": early, "mid_60_180": mid, "late_ge180": late},
        "r8_label": _r8_label(n),
        "interpretation": (
            f"Entries: mean {mean_offset:.0f}s into epoch, mode={timing_mode} "
            f"({early}/{mid}/{late} early/mid/late). {_r8_label(n)} (n={n})."
        ),
    }


# ============================================================
#  Dimension 3: Asset Preference
# ============================================================

def extract_asset_preference(db_path: str, source_trader: str) -> dict:
    """Frequency table of assets traded + chi-squared vs uniform."""
    trades = _load_trades(db_path, source_trader)
    asset_counts: dict[str, int] = {}
    for t in trades:
        a = t["asset"] or "unknown"
        asset_counts[a] = asset_counts.get(a, 0) + 1

    total = sum(asset_counts.values())
    if total == 0:
        return {"n": 0, "interpretation": "No asset data."}

    # Chi-squared test vs uniform
    k = len(asset_counts)
    expected = total / k if k > 0 else 1
    chi2 = sum((v - expected) ** 2 / expected for v in asset_counts.values()) if expected > 0 else 0

    # Critical value approx for k-1 df at 0.05
    df = max(k - 1, 1)
    # Simplified: chi2 > 2*df suggests non-uniform at roughly p<0.05 for small df
    non_uniform = chi2 > 2 * df

    top_asset = max(asset_counts, key=asset_counts.get) if asset_counts else "none"
    top_pct = round(asset_counts.get(top_asset, 0) / total * 100, 1) if total > 0 else 0

    return {
        "n": total,
        "asset_counts": asset_counts,
        "top_asset": top_asset,
        "top_asset_pct": top_pct,
        "chi2": round(chi2, 2),
        "non_uniform": non_uniform,
        "r8_label": _r8_label(total),
        "interpretation": (
            f"Top asset: {top_asset} ({top_pct}% of {total} trades). "
            f"Distribution {'non-uniform' if non_uniform else 'roughly uniform'} "
            f"(chi2={chi2:.1f}). {_r8_label(total)}."
        ),
    }


# ============================================================
#  Dimension 4: Direction Bias
# ============================================================

def extract_direction_bias(db_path: str, source_trader: str) -> dict:
    """Test for Up vs Down directional bias."""
    trades = _load_trades(db_path, source_trader)
    outcomes = [t["outcome"].upper() for t in trades if t["outcome"]]

    up_count = sum(1 for o in outcomes if o in ("UP", "YES", "1"))
    down_count = sum(1 for o in outcomes if o in ("DOWN", "NO", "0"))
    total = up_count + down_count

    if total == 0:
        return {"n": 0, "interpretation": "No direction data available."}

    up_pct = up_count / total

    # Binomial test: is up_pct significantly different from 0.5?
    test = hypothesis_test_wr(up_count, total, breakeven=0.50, alternative="two-sided")

    if up_pct > 0.6:
        bias = "UP"
    elif up_pct < 0.4:
        bias = "DOWN"
    else:
        bias = "NEUTRAL"

    return {
        "n": total,
        "up_count": up_count,
        "down_count": down_count,
        "up_pct": round(up_pct, 4),
        "bias": bias,
        "test": test,
        "r8_label": _r8_label(total),
        "interpretation": (
            f"Direction: {up_count} Up / {down_count} Down ({up_pct * 100:.1f}% Up). "
            f"Bias: {bias}. {'Significant' if test['significant'] else 'Not significant'} "
            f"(p={test['p_value']:.4f}). {_r8_label(total)}."
        ),
    }


# ============================================================
#  Dimension 5: Position Sizing
# ============================================================

def extract_sizing_pattern(db_path: str, source_trader: str) -> dict:
    """Analyze trade size distribution and correlations."""
    trades = _load_trades(db_path, source_trader)
    sizes = [t["size"] for t in trades if t["size"] and t["size"] > 0]
    prices = [t["price"] for t in trades if t["size"] and t["size"] > 0]

    if not sizes:
        return {"n": 0, "interpretation": "No sizing data."}

    n = len(sizes)
    mean_s = sum(sizes) / n
    median_s = sorted(sizes)[n // 2]
    min_s = min(sizes)
    max_s = max(sizes)
    std_s = math.sqrt(sum((s - mean_s) ** 2 for s in sizes) / max(n - 1, 1))
    cv = std_s / mean_s if mean_s > 0 else 0

    # Sizing pattern classification
    if cv < 0.2:
        sizing_mode = "fixed"
    elif cv < 0.5:
        sizing_mode = "moderate_variation"
    else:
        sizing_mode = "highly_variable"

    # Size-price correlation (Pearson)
    corr = 0.0
    if len(prices) == len(sizes) and n > 2:
        mean_p = sum(prices) / n
        cov = sum((s - mean_s) * (p - mean_p) for s, p in zip(sizes, prices)) / (n - 1)
        std_p = math.sqrt(sum((p - mean_p) ** 2 for p in prices) / max(n - 1, 1))
        if std_s > 0 and std_p > 0:
            corr = cov / (std_s * std_p)

    return {
        "n": n,
        "mean_size": round(mean_s, 2),
        "median_size": round(median_s, 2),
        "std_size": round(std_s, 2),
        "min_size": round(min_s, 2),
        "max_size": round(max_s, 2),
        "cv": round(cv, 4),
        "sizing_mode": sizing_mode,
        "size_price_correlation": round(corr, 4),
        "r8_label": _r8_label(n),
        "interpretation": (
            f"Sizing: mean=${mean_s:.2f}, median=${median_s:.2f}, CV={cv:.2f} ({sizing_mode}). "
            f"Size-price corr={corr:.2f}. {_r8_label(n)} (n={n})."
        ),
    }


# ============================================================
#  Dimension 6: Order Type (maker/taker inference)
# ============================================================

def infer_order_type(db_path: str, source_trader: str) -> dict:
    """Classify maker vs taker using maker_address ground truth when available.

    Data source hierarchy:
    1. maker_address field (from CLOB trades endpoint) - ground truth
    2. If no maker_address data available, result is marked UNRELIABLE

    The previous price-rounding heuristic (is_round = abs(p - round(p, 2)) < 0.001)
    was ALWAYS TRUE because all CLOB prices are on a 0.01 grid. It has been removed.
    """
    trades = _load_trades(db_path, source_trader)
    total = len(trades)

    if total == 0:
        return {"n": 0, "interpretation": "No order type data."}

    # Check if maker_address data is available in the DB
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            "PRAGMA table_info(trader_trades)"
        )
        columns = {row["name"] for row in cursor.fetchall()}
        has_maker_col = "maker_address" in columns
    finally:
        conn.close()

    if not has_maker_col:
        return {
            "n": total,
            "maker_signals": 0,
            "taker_signals": 0,
            "maker_pct": 0.0,
            "ground_truth_pct": 0.0,
            "dominant_type": "unknown",
            "reliable": False,
            "r8_label": _r8_label(total),
            "interpretation": (
                f"Order type: UNKNOWN. DB schema predates maker_address column. "
                f"Re-ingest trades from CLOB trades endpoint for ground truth. "
                f"{_r8_label(total)} (n={total})."
            ),
        }

    # Count trades with ground-truth maker_address
    maker_confirmed = 0
    taker_confirmed = 0
    no_data = 0

    for t in trades:
        maker_addr = (t.get("maker_address") or "").strip().lower()
        trader_addr = source_trader.strip().lower()

        if not maker_addr:
            no_data += 1
        elif maker_addr == trader_addr:
            maker_confirmed += 1
        else:
            taker_confirmed += 1

    ground_truth_count = maker_confirmed + taker_confirmed
    ground_truth_pct = ground_truth_count / total if total > 0 else 0.0
    reliable = ground_truth_pct >= 0.5  # At least half have ground truth

    if ground_truth_count > 0:
        maker_pct = maker_confirmed / ground_truth_count
    else:
        maker_pct = 0.0

    if not reliable:
        dominant = "unknown"
        reliability_note = (
            f"UNRELIABLE: only {ground_truth_count}/{total} trades "
            f"({ground_truth_pct * 100:.0f}%) have maker_address data. "
            f"Re-ingest from CLOB trades endpoint for ground truth."
        )
    else:
        dominant = "maker" if maker_pct > 0.6 else "taker" if maker_pct < 0.4 else "mixed"
        reliability_note = (
            f"Ground truth from maker_address on {ground_truth_count}/{total} trades "
            f"({ground_truth_pct * 100:.0f}%)."
        )

    return {
        "n": total,
        "maker_signals": maker_confirmed,
        "taker_signals": taker_confirmed,
        "no_data": no_data,
        "maker_pct": round(maker_pct, 4),
        "ground_truth_pct": round(ground_truth_pct, 4),
        "dominant_type": dominant,
        "reliable": reliable,
        "r8_label": _r8_label(total),
        "interpretation": (
            f"Order type: {dominant} ({maker_pct * 100:.1f}% maker). "
            f"{reliability_note} "
            f"{_r8_label(total)} (n={total})."
        ),
    }


# ============================================================
#  Dimension 7: Regime Conditioning
# ============================================================

def extract_regime_conditioning(db_path: str, source_trader: str) -> dict:
    """Analyze WR by hour of day and by asset.

    Note: volatility regime and F&G require external data (lagbot_context.json).
    This function handles hour-of-day and asset conditioning from trade data alone.
    """
    resolved = _load_resolved_trades(db_path, source_trader)
    if not resolved:
        return {"n": 0, "interpretation": "No resolved trades for regime analysis."}

    from datetime import datetime, timezone

    # WR by hour
    hour_wins: dict[int, int] = {}
    hour_total: dict[int, int] = {}
    for t in resolved:
        dt = datetime.fromtimestamp(t["timestamp"], tz=timezone.utc)
        h = dt.hour
        hour_total[h] = hour_total.get(h, 0) + 1
        if _is_win(t):
            hour_wins[h] = hour_wins.get(h, 0) + 1

    hour_wr = {}
    for h in sorted(hour_total.keys()):
        w = hour_wins.get(h, 0)
        n = hour_total[h]
        hour_wr[h] = {"wr": round(w / n, 4) if n > 0 else 0, "n": n, "r8": _r8_label(n)}

    # WR by asset
    asset_wins: dict[str, int] = {}
    asset_total: dict[str, int] = {}
    for t in resolved:
        a = t["asset"] or "unknown"
        asset_total[a] = asset_total.get(a, 0) + 1
        if _is_win(t):
            asset_wins[a] = asset_wins.get(a, 0) + 1

    asset_wr = {}
    for a in sorted(asset_total.keys()):
        w = asset_wins.get(a, 0)
        n = asset_total[a]
        asset_wr[a] = {"wr": round(w / n, 4) if n > 0 else 0, "n": n, "r8": _r8_label(n)}

    # Identify best/worst hours
    best_hours = [h for h, d in hour_wr.items() if d["wr"] > 0.7 and d["n"] >= 5]
    worst_hours = [h for h, d in hour_wr.items() if d["wr"] < 0.4 and d["n"] >= 5]

    return {
        "n": len(resolved),
        "hour_wr": hour_wr,
        "asset_wr": asset_wr,
        "best_hours": best_hours,
        "worst_hours": worst_hours,
        "r8_label": _r8_label(len(resolved)),
        "interpretation": (
            f"Regime analysis on {len(resolved)} resolved trades. "
            f"Best hours: {best_hours or 'none with n>=5'}. "
            f"Worst hours: {worst_hours or 'none with n>=5'}."
        ),
    }


# ============================================================
#  Dimension 8: Exit Pattern
# ============================================================

def infer_exit_pattern(db_path: str, source_trader: str) -> dict:
    """Infer hold-to-resolution vs active exit from trade data.

    Heuristic: if most trades cluster near epoch boundaries (last 30s or
    hold through resolution), likely HTR. If exits are mid-epoch, active exit.
    """
    trades = _load_trades(db_path, source_trader)
    if not trades:
        return {"n": 0, "interpretation": "No trades for exit analysis."}

    # Look at SELL trades as potential exits
    sells = [t for t in trades if (t["side"] or "").upper() == "SELL"]
    buys = [t for t in trades if (t["side"] or "").upper() == "BUY"]

    buy_count = len(buys)
    sell_count = len(sells)

    # If very few sells relative to buys, likely HTR
    if buy_count > 0:
        sell_ratio = sell_count / buy_count
    else:
        sell_ratio = 0

    if sell_ratio < 0.1:
        exit_mode = "hold_to_resolution"
    elif sell_ratio < 0.5:
        exit_mode = "mostly_htr"
    else:
        exit_mode = "active_exit"

    return {
        "n": len(trades),
        "buy_count": buy_count,
        "sell_count": sell_count,
        "sell_to_buy_ratio": round(sell_ratio, 4),
        "exit_mode": exit_mode,
        "r8_label": _r8_label(len(trades)),
        "interpretation": (
            f"Exit pattern: {exit_mode} (sell/buy ratio={sell_ratio:.2f}). "
            f"{buy_count} buys, {sell_count} sells. {_r8_label(len(trades))} (n={len(trades)})."
        ),
    }


# ============================================================
#  Profile
# ============================================================

def compute_profile(db_path: str, source_trader: str) -> dict:
    """Compute trader profile with WR from resolved trades.

    Returns summary with R8 labels and PROVISIONAL flag.
    """
    all_trades = _load_trades(db_path, source_trader)
    resolved = _load_resolved_trades(db_path, source_trader)

    total = len(all_trades)
    resolved_count = len(resolved)
    pending = total - resolved_count

    wins = sum(1 for t in resolved if _is_win(t))
    losses = resolved_count - wins

    wr = wins / resolved_count if resolved_count > 0 else 0
    ci = wilson_ci(wins, resolved_count) if resolved_count > 0 else (0, 0)

    # Estimate P&L from resolved trades.
    # WARNING (A-4 size semantics): P&L formula assumes size is in SHARES.
    #   - CLOB trades endpoint: size IS shares (size_unit="shares") -- formula correct.
    #   - Activity API: size semantics ambiguous (size_unit="unknown") -- may be
    #     dollars, which would make (1-p)*s incorrect. P&L from Activity API trades
    #     should be treated as APPROXIMATE.
    # Check size_unit column when available to flag unreliable P&L.
    est_pnl = 0.0
    size_unknown_count = 0
    for t in resolved:
        p = t["price"] or 0
        s = t["size"] or 0
        if t.get("size_unit", "unknown") == "unknown":
            size_unknown_count += 1
        if _is_win(t):
            est_pnl += (1 - p) * s
        else:
            est_pnl -= p * s

    pnl_reliable = size_unknown_count == 0

    # Trade frequency
    if len(all_trades) >= 2:
        ts_sorted = sorted(t["timestamp"] for t in all_trades)
        span_hours = (ts_sorted[-1] - ts_sorted[0]) / 3600
        trades_per_day = total / max(span_hours / 24, 0.01)
    else:
        trades_per_day = 0

    provisional_pct = round(pending / total * 100, 1) if total > 0 else 0
    is_provisional = provisional_pct > 20

    # Bayesian estimate
    bayes = beta_binomial_update(1.0, 1.0, wins, losses)

    pnl_note = "" if pnl_reliable else (
        f" P&L APPROXIMATE: {size_unknown_count}/{resolved_count} trades "
        f"have unknown size_unit (Activity API, not CLOB trades)."
    )

    return {
        "source_trader": source_trader,
        "total_trades": total,
        "resolved_trades": resolved_count,
        "pending_trades": pending,
        "provisional_pct": provisional_pct,
        "is_provisional": is_provisional,
        "wins": wins,
        "losses": losses,
        "wr": round(wr, 4),
        "wilson_ci": (round(ci[0], 4), round(ci[1], 4)),
        "est_pnl": round(est_pnl, 2),
        "pnl_reliable": pnl_reliable,
        "trades_per_day": round(trades_per_day, 1),
        "bayesian_wr": bayes["posterior"]["mean"],
        "bayesian_hdi": bayes["hdi_95"],
        "r8_label": _r8_label(resolved_count),
        "interpretation": (
            f"Trader {source_trader[:10]}...: {wins}W/{losses}L "
            f"({wr * 100:.1f}% WR, 95% CI [{ci[0] * 100:.1f}%, {ci[1] * 100:.1f}%]). "
            f"Est P&L: ${est_pnl:.2f}. {trades_per_day:.1f} trades/day. "
            f"{_r8_label(resolved_count)} (n={resolved_count} resolved). "
            f"{'PROVISIONAL: ' + str(provisional_pct) + '% of trades unresolved.' if is_provisional else ''}"
            f"{pnl_note}"
        ),
    }


# ============================================================
#  ReplicaStrategy Generation
# ============================================================

def generate_replica(
    decomposition: dict,
    balance: float,
    profile: Optional[dict] = None,
) -> ReplicaStrategy:
    """Generate ReplicaStrategy from decomposition results.

    Args:
        decomposition: dict with keys for each dimension (from extract_* functions)
        balance: current wallet balance for sizing calculations
        profile: optional trader profile for WR-based recommendations

    Returns:
        ReplicaStrategy with .env overrides.
    """
    warnings = []
    env = {}

    # 1. Entry price range
    price_dist = decomposition.get("entry_price", {})
    entry_mode = price_dist.get("entry_mode", "mixed")
    if entry_mode == "deep":
        env["MIN_ENTRY_PRICE"] = "0.40"
        env["MAX_ENTRY_PRICE"] = "0.65"
    elif entry_mode == "mid":
        env["MIN_ENTRY_PRICE"] = "0.60"
        env["MAX_ENTRY_PRICE"] = "0.80"
    elif entry_mode == "high":
        env["MIN_ENTRY_PRICE"] = "0.80"
        env["MAX_ENTRY_PRICE"] = "0.95"
    else:
        p_min = price_dist.get("min", 0.40)
        p_max = price_dist.get("max", 0.95)
        env["MIN_ENTRY_PRICE"] = str(round(max(p_min, 0.30), 2))
        env["MAX_ENTRY_PRICE"] = str(round(min(p_max, 0.97), 2))

    # 2. Timing
    timing = decomposition.get("timing", {})
    timing_mode = timing.get("timing_mode", "spread")
    mean_offset = timing.get("mean_offset_secs", 120)
    if timing_mode == "early":
        env["MOMENTUM_MAX_EPOCH_ELAPSED_SECS"] = "60"
    elif timing_mode == "late":
        env["MOMENTUM_MAX_EPOCH_ELAPSED_SECS"] = "240"
    else:
        env["MOMENTUM_MAX_EPOCH_ELAPSED_SECS"] = str(int(min(mean_offset * 1.5, 270)))

    # 3. Asset filter
    asset_pref = decomposition.get("asset_preference", {})
    asset_counts = asset_pref.get("asset_counts", {})
    if asset_counts:
        # Include assets that are >= 10% of trades
        total = sum(asset_counts.values())
        active_assets = [a for a, c in asset_counts.items() if c >= total * 0.10 and a != "unknown"]
        if active_assets:
            env["ASSET_FILTER"] = ",".join(sorted(active_assets))

    # 4. Direction bias
    direction = decomposition.get("direction_bias", {})
    bias = direction.get("bias", "NEUTRAL")
    if bias != "NEUTRAL":
        warnings.append(f"Trader has {bias} directional bias. Our bot is direction-agnostic. May need DIRECTION_FILTER config.")

    # 5. Sizing
    sizing = decomposition.get("sizing", {})
    mean_size = sizing.get("mean_size", 50)
    # Kelly-based sizing if profile available
    if profile and profile.get("wr", 0) > 0 and profile.get("resolved_trades", 0) >= 15:
        wr = profile["wr"]
        # Estimate avg win/loss from est_pnl
        wins = profile.get("wins", 1)
        losses = profile.get("losses", 1)
        est_pnl = profile.get("est_pnl", 0)
        if wins > 0 and losses > 0:
            avg_win = max(est_pnl / wins, 0.01) if est_pnl > 0 else 0.25
            avg_loss = max(abs(est_pnl) / losses, 0.01) if est_pnl <= 0 else 0.75
            kelly = kelly_criterion(wr, avg_win, avg_loss)
            recommended_pct = kelly["half_kelly"]
            env["BASE_BET_PCT"] = str(round(min(recommended_pct, 0.15), 4))
        else:
            env["BASE_BET_PCT"] = "0.05"
    else:
        env["BASE_BET_PCT"] = "0.05"
        warnings.append("Insufficient data for Kelly sizing. Using conservative 5%.")

    env["MAX_BET"] = str(int(min(mean_size * 2, balance * 0.20, 200)))

    # 6. Blackout hours
    regime = decomposition.get("regime", {})
    worst_hours = regime.get("worst_hours", [])
    if worst_hours:
        env["BLACKOUT_HOURS"] = ",".join(str(h) for h in sorted(worst_hours))

    # 7. Exit pattern
    exit_pat = decomposition.get("exit_pattern", {})
    exit_mode = exit_pat.get("exit_mode", "hold_to_resolution")
    if exit_mode in ("hold_to_resolution", "mostly_htr"):
        env["HOLD_TO_RESOLUTION"] = "true"
        env["MID_PRICE_STOP_ENABLED"] = "true"
        env["MID_PRICE_STOP_PCT"] = "0.15"

    # Strategy type classification
    if entry_mode == "deep" and exit_mode in ("hold_to_resolution", "mostly_htr"):
        strategy_type = "rtds"
        env["ENTRY_MODE"] = "maker"
    elif entry_mode == "high":
        strategy_type = "momentum"
        env["ENTRY_MODE"] = "maker"
    elif float(env.get("MIN_ENTRY_PRICE", "0.50")) < 0.55:
        strategy_type = "pair_cost_arb"
        env["MM_DRY_RUN"] = "true"
        env["MM_MAX_PAIR_COST"] = "0.98"
        env["MM_SCAN_INTERVAL"] = "1.0"
    else:
        strategy_type = "mixed"
        env["ENTRY_MODE"] = "maker"

    # Safety configs (always included)
    env["DRY_RUN"] = "true"  # Always start in dry run
    env["MAX_OPEN_POSITIONS"] = "3"
    env["POST_LOSS_COOLDOWN_MINS"] = "15"

    # Confidence based on sample size
    n = profile.get("resolved_trades", 0) if profile else 0
    if n >= 100:
        confidence = 0.8
    elif n >= 30:
        confidence = 0.5
    elif n >= 15:
        confidence = 0.3
    else:
        confidence = 0.1
        warnings.append(f"Low confidence (n={n}). Collect more data before replication.")

    if profile and profile.get("is_provisional"):
        confidence *= 0.5
        warnings.append("PROVISIONAL: >20% of trades unresolved. Recommendation: STUDY_MORE.")

    return ReplicaStrategy(
        strategy_type=strategy_type,
        env_overrides=env,
        warnings=warnings,
        confidence=round(confidence, 2),
        source_trader=profile.get("source_trader", "") if profile else "",
        n_trades_analyzed=n,
    )


def render_env_template(replica: ReplicaStrategy) -> str:
    """Render ReplicaStrategy as .env template string."""
    lines = [
        f"# Reverse-engineered strategy template",
        f"# Source trader: {replica.source_trader}",
        f"# Strategy type: {replica.strategy_type}",
        f"# Confidence: {replica.confidence}",
        f"# Trades analyzed: {replica.n_trades_analyzed}",
        f"#",
    ]

    if replica.warnings:
        lines.append("# WARNINGS:")
        for w in replica.warnings:
            lines.append(f"#   - {w}")
        lines.append("#")

    lines.append("")
    lines.append("# --- Strategy Parameters ---")

    for key, value in sorted(replica.env_overrides.items()):
        lines.append(f"{key}={value}")

    lines.append("")
    return "\n".join(lines)


def get_recommendation(profile: dict, edge_test: Optional[dict] = None) -> str:
    """Determine REPLICATE / STUDY_MORE / SKIP recommendation.

    Uses PROVISIONAL decision rule: if >20% trades pending, always STUDY_MORE.
    """
    if profile.get("is_provisional"):
        return "STUDY_MORE"

    wr = profile.get("wr", 0)
    n = profile.get("resolved_trades", 0)
    r8 = _r8_label(n)

    if r8 == "ANECDOTAL":
        return "STUDY_MORE"

    if wr < 0.55:
        return "SKIP"

    if edge_test:
        dsr = edge_test.get("dsr_value", 0)
        if dsr < 0.5:
            return "SKIP"
        consistent = edge_test.get("consistent", False)
        if not consistent:
            return "STUDY_MORE"

    if wr >= 0.65 and n >= 30:
        return "REPLICATE"

    return "STUDY_MORE"


# ============================================================
#  Helpers
# ============================================================

def _is_win(trade: dict) -> bool:
    """Determine if a resolved trade is a win.

    Uses resolution field: if the trader bought the direction that resolved,
    it's a win.
    """
    resolution = (trade.get("resolution") or "").upper()
    outcome = (trade.get("outcome") or "").upper()
    side = (trade.get("side") or "").upper()

    # If resolution matches the outcome the trader bought, it's a win
    if side == "BUY":
        if resolution in ("UP", "YES") and outcome in ("UP", "YES", "1"):
            return True
        if resolution in ("DOWN", "NO") and outcome in ("DOWN", "NO", "0"):
            return True
    return False
