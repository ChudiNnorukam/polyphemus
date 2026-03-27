#!/usr/bin/env python3
"""Research metrics tracker for bulenox-quant v2.0.

Computes academic/research metrics that require sufficient data:
  1. Hurst exponent (BTC 5m) - mean reversion vs trending
  2. OU half-life (minutes) - how long reversion takes
  3. Optimal hold time (2x half-life)
  4. ATR-adaptive TP/SL (what values WOULD be under adaptive regime)
  5. Monte Carlo Markov RoR (qualification probability, drawdown distribution)

Usage:
  python3 research_metrics.py                # all metrics
  python3 research_metrics.py --hurst        # Hurst + half-life only
  python3 research_metrics.py --montecarlo   # Monte Carlo only
  python3 research_metrics.py --adaptive     # ATR-adaptive TP/SL only
"""

import argparse
import json
import math
import os
import random
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from typing import Optional

try:
    import requests
except ImportError:
    requests = None


# --- Coinbase REST API for BTC candles ---

COINBASE_CANDLES_URL = "https://api.exchange.coinbase.com/products/BTC-USD/candles"


def fetch_coinbase_candles(granularity: int = 300, hours: int = 48) -> list[tuple[float, float, float, float, float]]:
    """Fetch BTC-USD candles from Coinbase. Returns [(ts, low, high, open, close), ...]."""
    if requests is None:
        print("[ERROR] requests library not available. pip install requests")
        return []
    # Coinbase returns latest 300-350 candles when no start/end specified.
    # At 5m granularity = ~29 hours, sufficient for Hurst/ATR computation.
    params = {"granularity": granularity}
    try:
        resp = requests.get(COINBASE_CANDLES_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        # Coinbase returns [timestamp, low, high, open, close, volume] newest first
        candles = [(c[0], c[1], c[2], c[3], c[4]) for c in data]
        candles.sort(key=lambda x: x[0])  # oldest first
        return candles
    except Exception as e:
        print(f"[ERROR] Failed to fetch candles: {e}")
        return []


# --- Hurst Exponent (Rescaled Range) ---

def compute_hurst(prices: list[float]) -> Optional[float]:
    """Compute Hurst exponent using rescaled range (R/S) analysis."""
    if len(prices) < 100:
        print(f"[WARN] Hurst needs 100+ prices, got {len(prices)}")
        return None

    log_returns = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices)) if prices[i - 1] > 0]
    n = len(log_returns)
    if n < 50:
        return None

    # Compute R/S for different window sizes
    window_sizes = []
    rs_values = []
    for w in [8, 16, 32, 64, 128, 256]:
        if w > n // 2:
            break
        rs_list = []
        for start in range(0, n - w + 1, w):
            chunk = log_returns[start:start + w]
            mean_c = sum(chunk) / len(chunk)
            deviations = [x - mean_c for x in chunk]
            cumulative = []
            s = 0
            for d in deviations:
                s += d
                cumulative.append(s)
            r = max(cumulative) - min(cumulative)
            std = (sum((x - mean_c) ** 2 for x in chunk) / len(chunk)) ** 0.5
            if std > 0:
                rs_list.append(r / std)
        if rs_list:
            window_sizes.append(math.log(w))
            rs_values.append(math.log(sum(rs_list) / len(rs_list)))

    if len(window_sizes) < 3:
        return None

    # Linear regression: log(R/S) = H * log(n) + c
    n_pts = len(window_sizes)
    sum_x = sum(window_sizes)
    sum_y = sum(rs_values)
    sum_xy = sum(x * y for x, y in zip(window_sizes, rs_values))
    sum_x2 = sum(x * x for x in window_sizes)
    denom = n_pts * sum_x2 - sum_x * sum_x
    if abs(denom) < 1e-10:
        return None
    h = (n_pts * sum_xy - sum_x * sum_y) / denom
    return h


# --- OU Half-Life (AR(1) Regression) ---

def compute_ou_half_life(prices: list[float]) -> Optional[float]:
    """Compute Ornstein-Uhlenbeck half-life via AR(1) regression on log prices.

    Model: dP = theta * (mu - P) * dt + sigma * dW
    AR(1): P_t = alpha * P_{t-1} + beta + eps
    theta = -log(alpha) / dt
    half_life = log(2) / theta = -log(2) * dt / log(alpha)
    """
    if len(prices) < 30:
        print(f"[WARN] OU half-life needs 30+ prices, got {len(prices)}")
        return None

    log_p = [math.log(p) for p in prices if p > 0]
    n = len(log_p) - 1
    if n < 20:
        return None

    # AR(1) regression: y_t = alpha * y_{t-1} + beta
    y = log_p[1:]
    x = log_p[:-1]
    sum_x = sum(x)
    sum_y = sum(y)
    sum_xy = sum(xi * yi for xi, yi in zip(x, y))
    sum_x2 = sum(xi * xi for xi in x)
    denom = n * sum_x2 - sum_x * sum_x
    if abs(denom) < 1e-10:
        return None
    alpha = (n * sum_xy - sum_x * sum_y) / denom

    if alpha <= 0 or alpha >= 1:
        # alpha outside (0,1) means no mean reversion or unit root
        return None

    # Half-life in units of the candle period
    half_life = -math.log(2) / math.log(alpha)
    return half_life


# --- ATR Computation ---

def compute_atr(candles: list[tuple], period: int = 12) -> Optional[float]:
    """Compute ATR in ticks from candle data. period=12 for 1h ATR on 5m candles."""
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        _, low, high, _, prev_close = candles[i][1], candles[i][1], candles[i][2], candles[i][3], candles[i - 1][4]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if len(trs) < period:
        return None
    # Use last `period` TRs for current ATR
    recent = trs[-period:]
    atr_usd = sum(recent) / len(recent)
    # Convert to ticks: MBT tick = 5 index points, $0.50/tick
    # But price is in USD (BTC price), and MBT moves 1:1 with BTC
    # 1 tick = 5 index points. For MBT, index points ~ USD price points
    # So ATR in ticks = ATR_usd / 5.0
    atr_ticks = atr_usd / 5.0
    return atr_ticks


# --- Monte Carlo Markov RoR ---

def load_trades(db_path: str) -> list[dict]:
    """Load closed trades from SQLite."""
    if not os.path.exists(db_path):
        print(f"[WARN] DB not found: {db_path}")
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT direction, entry_price, exit_price, pnl_pts FROM trades "
            "WHERE exit_price IS NOT NULL ORDER BY entry_ts"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[WARN] Failed to query trades: {e}")
        return []
    finally:
        conn.close()


def compute_markov_matrix(trades: list[dict]) -> Optional[dict]:
    """Compute 2-state Markov transition matrix from trade outcomes."""
    if len(trades) < 10:
        print(f"[WARN] Need 10+ trades for Markov matrix, got {len(trades)}")
        return None
    outcomes = ["W" if t["pnl_pts"] > 0 else "L" for t in trades]
    transitions = {"WW": 0, "WL": 0, "LW": 0, "LL": 0}
    for i in range(1, len(outcomes)):
        key = outcomes[i - 1] + outcomes[i]
        transitions[key] += 1
    w_total = transitions["WW"] + transitions["WL"]
    l_total = transitions["LW"] + transitions["LL"]
    if w_total == 0 or l_total == 0:
        return None
    return {
        "p_ww": transitions["WW"] / w_total,
        "p_wl": transitions["WL"] / w_total,
        "p_lw": transitions["LW"] / l_total,
        "p_ll": transitions["LL"] / l_total,
        "n": len(outcomes),
        "wins": outcomes.count("W"),
        "losses": outcomes.count("L"),
    }


def monte_carlo_qualification(
    markov: dict,
    avg_win_usd: float,
    avg_loss_usd: float,
    target: float = 3000.0,
    drawdown_limit: float = 2500.0,
    n_simulations: int = 10000,
    max_trades: int = 1000,
) -> dict:
    """Simulate qualification probability using Markov-dependent outcomes."""
    qualified = 0
    busted = 0
    max_drawdowns = []
    trades_to_qualify = []

    for _ in range(n_simulations):
        balance = 0.0
        peak = 0.0
        prev_outcome = "W" if random.random() < markov["wins"] / markov["n"] else "L"
        trade_count = 0

        for t in range(max_trades):
            trade_count += 1
            # Determine outcome based on Markov transition
            if prev_outcome == "W":
                outcome = "W" if random.random() < markov["p_ww"] else "L"
            else:
                outcome = "W" if random.random() < markov["p_lw"] else "L"

            if outcome == "W":
                balance += avg_win_usd
            else:
                balance -= avg_loss_usd

            if balance > peak:
                peak = balance
            drawdown = peak - balance

            # Check bust (trailing drawdown)
            if drawdown >= drawdown_limit:
                busted += 1
                max_drawdowns.append(drawdown)
                break

            # Check qualification
            if balance >= target:
                qualified += 1
                trades_to_qualify.append(trade_count)
                max_drawdowns.append(peak - balance)
                break

            prev_outcome = outcome
        else:
            # Didn't qualify or bust in max_trades
            max_drawdowns.append(peak - balance)

    return {
        "qualification_pct": qualified / n_simulations * 100,
        "bust_pct": busted / n_simulations * 100,
        "neither_pct": (n_simulations - qualified - busted) / n_simulations * 100,
        "avg_trades_to_qualify": sum(trades_to_qualify) / len(trades_to_qualify) if trades_to_qualify else None,
        "median_max_drawdown": sorted(max_drawdowns)[len(max_drawdowns) // 2] if max_drawdowns else None,
        "n_simulations": n_simulations,
    }


# --- Main ---

def run_hurst_halflife(candles: list[tuple], granularity_min: int = 5) -> dict:
    """Compute Hurst exponent and OU half-life from candle data, with rolling stability."""
    prices = [c[4] for c in candles]  # close prices
    results = {}

    h = compute_hurst(prices)
    results["hurst"] = h
    if h is not None:
        if h < 0.45:
            results["hurst_interpretation"] = "MEAN-REVERTING (strong)"
        elif h < 0.50:
            results["hurst_interpretation"] = "MEAN-REVERTING (moderate)"
        elif h < 0.55:
            results["hurst_interpretation"] = "BORDERLINE (weak/random walk)"
        elif h < 0.65:
            results["hurst_interpretation"] = "TRENDING (weak)"
        else:
            results["hurst_interpretation"] = "TRENDING (strong)"

    hl = compute_ou_half_life(prices)
    if hl is not None:
        results["half_life_candles"] = hl
        results["half_life_minutes"] = hl * granularity_min
        results["optimal_hold_minutes"] = hl * granularity_min * 2
        results["current_hold_minutes"] = 15
        results["hold_alignment"] = (
            "ALIGNED" if abs(hl * granularity_min * 2 - 15) < 10
            else "MISALIGNED (consider adjusting max_hold)"
        )
    else:
        results["half_life_candles"] = None
        results["half_life_minutes"] = None

    # Rolling half-life stability (Gap #1): compute on 5 windows, report CV
    window_size = len(prices) // 5
    if window_size >= 50:
        half_lives = []
        for i in range(5):
            chunk = prices[i * window_size:(i + 1) * window_size]
            chunk_hl = compute_ou_half_life(chunk)
            if chunk_hl is not None and chunk_hl > 0:
                half_lives.append(chunk_hl * granularity_min)
        if len(half_lives) >= 3:
            hl_mean = sum(half_lives) / len(half_lives)
            hl_std = (sum((x - hl_mean) ** 2 for x in half_lives) / len(half_lives)) ** 0.5
            hl_cv = hl_std / hl_mean if hl_mean > 0 else 999
            results["hl_stability"] = {
                "windows": len(half_lives),
                "values_min": [round(x, 1) for x in half_lives],
                "mean": round(hl_mean, 1),
                "std": round(hl_std, 1),
                "cv": round(hl_cv, 3),
                "verdict": "STABLE" if hl_cv < 0.5 else "UNRELIABLE (CV > 0.5, don't trust for hold-time optimization)",
            }

    return results


def run_adaptive_tpsl(candles: list[tuple]) -> dict:
    """Compute what ATR-adaptive TP/SL would be right now."""
    atr = compute_atr(candles, period=12)  # 1h ATR on 5m candles
    if atr is None:
        return {"atr_1h_ticks": None, "error": "Insufficient candle data"}

    # v2.0 formula: TP = max(30, min(80, ATR * 2.5)), SL = max(10, min(25, ATR * 0.75))
    adaptive_tp = max(30, min(80, atr * 2.5))
    adaptive_sl = max(10, min(25, atr * 0.75))

    return {
        "atr_1h_ticks": round(atr, 1),
        "atr_1h_usd": round(atr * 5.0, 2),  # ticks * $5/tick index points
        "adaptive_tp_ticks": round(adaptive_tp),
        "adaptive_sl_ticks": round(adaptive_sl),
        "current_tp_ticks": 50,
        "current_sl_ticks": 15,
        "tp_delta": round(adaptive_tp - 50),
        "sl_delta": round(adaptive_sl - 15),
    }


def run_montecarlo(db_path: str) -> dict:
    """Run Monte Carlo qualification simulation from trade data."""
    trades = load_trades(db_path)
    if len(trades) < 5:
        return {"error": f"Need 5+ trades, got {len(trades)}", "n": len(trades)}

    markov = compute_markov_matrix(trades)
    if markov is None:
        return {"error": "Could not compute Markov matrix (need varied outcomes)", "n": len(trades)}

    # Compute avg win/loss in dollars
    wins = [t for t in trades if t["pnl_pts"] > 0]
    losses = [t for t in trades if t["pnl_pts"] <= 0]
    point_value = 0.1  # MBT
    cost_rt = 5.52

    avg_win_usd = (sum(t["pnl_pts"] for t in wins) / len(wins) * point_value - cost_rt) if wins else 0
    avg_loss_usd = (abs(sum(t["pnl_pts"] for t in losses) / len(losses)) * point_value + cost_rt) if losses else 0

    mc = monte_carlo_qualification(markov, avg_win_usd, avg_loss_usd)

    confidence = "ANECDOTAL" if len(trades) < 30 else "LOW" if len(trades) < 107 else "MODERATE"

    return {
        "markov": markov,
        "avg_win_usd": round(avg_win_usd, 2),
        "avg_loss_usd": round(avg_loss_usd, 2),
        "monte_carlo": mc,
        "confidence": confidence,
        "n": len(trades),
    }


def main():
    parser = argparse.ArgumentParser(description="Bulenox research metrics tracker")
    parser.add_argument("--hurst", action="store_true", help="Hurst + half-life only")
    parser.add_argument("--montecarlo", action="store_true", help="Monte Carlo only")
    parser.add_argument("--adaptive", action="store_true", help="ATR-adaptive TP/SL only")
    parser.add_argument("--db", default="data/trades.db", help="Path to trades.db")
    parser.add_argument("--hours", type=int, default=48, help="Hours of candle data to fetch")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    run_all = not (args.hurst or args.montecarlo or args.adaptive)
    results = {}

    # Fetch candle data if needed
    candles = []
    if run_all or args.hurst or args.adaptive:
        print(f"Fetching {args.hours}h of BTC-USD 5m candles from Coinbase...")
        candles = fetch_coinbase_candles(granularity=300, hours=args.hours)
        print(f"  Got {len(candles)} candles ({len(candles) * 5 / 60:.0f}h)")

    # 1. Hurst + Half-Life
    if run_all or args.hurst:
        print("\n=== Hurst Exponent & OU Half-Life ===")
        hurst_results = run_hurst_halflife(candles)
        results["hurst_halflife"] = hurst_results
        if not args.json:
            h = hurst_results.get("hurst")
            if h is not None:
                print(f"  Hurst exponent (5m):  {h:.4f} -> {hurst_results['hurst_interpretation']}")
            else:
                print("  Hurst exponent: INSUFFICIENT DATA")
            hl = hurst_results.get("half_life_minutes")
            if hl is not None:
                print(f"  OU half-life:         {hl:.1f} minutes ({hurst_results['half_life_candles']:.1f} candles)")
                print(f"  Optimal hold (2x HL): {hurst_results['optimal_hold_minutes']:.0f} minutes")
                print(f"  Current max_hold:     {hurst_results['current_hold_minutes']} minutes")
                print(f"  Alignment:            {hurst_results['hold_alignment']}")
            else:
                print("  OU half-life: COULD NOT COMPUTE (no mean reversion detected)")
            stab = hurst_results.get("hl_stability")
            if stab:
                print(f"  Half-life stability:  mean={stab['mean']}min std={stab['std']}min CV={stab['cv']} [{stab['verdict']}]")
                print(f"    Window values: {stab['values_min']}")

    # 2. ATR-Adaptive TP/SL
    if run_all or args.adaptive:
        print("\n=== ATR-Adaptive TP/SL (what values WOULD be) ===")
        adaptive_results = run_adaptive_tpsl(candles)
        results["adaptive_tpsl"] = adaptive_results
        if not args.json:
            if "error" in adaptive_results:
                print(f"  Error: {adaptive_results['error']}")
            else:
                print(f"  ATR (1h):     {adaptive_results['atr_1h_ticks']} ticks (${adaptive_results['atr_1h_usd']})")
                print(f"  Adaptive TP:  {adaptive_results['adaptive_tp_ticks']} ticks (current: {adaptive_results['current_tp_ticks']}, delta: {adaptive_results['tp_delta']:+d})")
                print(f"  Adaptive SL:  {adaptive_results['adaptive_sl_ticks']} ticks (current: {adaptive_results['current_sl_ticks']}, delta: {adaptive_results['sl_delta']:+d})")

    # 3. Monte Carlo Markov RoR
    if run_all or args.montecarlo:
        print("\n=== Monte Carlo Qualification Simulation ===")
        mc_results = run_montecarlo(args.db)
        results["monte_carlo"] = mc_results
        if not args.json:
            if "error" in mc_results:
                print(f"  Error: {mc_results['error']}")
            else:
                m = mc_results["markov"]
                mc = mc_results["monte_carlo"]
                print(f"  Trades: {mc_results['n']} [{mc_results['confidence']}]")
                print(f"  WR: {m['wins']}/{m['n']} ({m['wins']/m['n']*100:.1f}%)")
                print(f"  Markov: P(W|W)={m['p_ww']:.2f}  P(L|W)={m['p_wl']:.2f}  P(W|L)={m['p_lw']:.2f}  P(L|L)={m['p_ll']:.2f}")
                print(f"  Avg win:  ${mc_results['avg_win_usd']:.2f}  |  Avg loss: ${mc_results['avg_loss_usd']:.2f}")
                print(f"  --- {mc['n_simulations']:,} simulations ---")
                print(f"  Qualification:  {mc['qualification_pct']:.1f}%")
                print(f"  Bust:           {mc['bust_pct']:.1f}%")
                print(f"  Neither:        {mc['neither_pct']:.1f}%")
                if mc["avg_trades_to_qualify"]:
                    print(f"  Avg trades to qualify: {mc['avg_trades_to_qualify']:.0f}")
                print(f"  Median max drawdown:  ${mc['median_max_drawdown']:.2f}")

    if args.json:
        print(json.dumps(results, indent=2, default=str))

    print("\n--- Research metrics complete ---")


if __name__ == "__main__":
    main()
