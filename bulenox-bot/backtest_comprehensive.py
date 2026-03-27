#!/usr/bin/env python3
"""Comprehensive strategy search across 90 days of BTC 1m data.

Tests momentum, fade, volume breakout, volatility breakout, multi-candle confirmation,
time-of-day filters, and day-of-week filters. Outputs ranked configurations.

Run: python3 backtest_comprehensive.py
"""
import sqlite3
import time
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone

DB_PATH = "data/btc_1m_history.db"
TICK_VALUE = 0.50      # MBT $0.50 per tick
TICK_PTS = 5.0         # 5 index points per tick
CONTRACT_SIZE = 0.1    # 0.1 BTC per MBT contract
COST_RT = 3.54         # round-trip cost in dollars
COST_TICKS = COST_RT / TICK_VALUE


@dataclass
class TradeResult:
    entry_idx: int
    direction: str
    entry_price: float
    exit_price: float
    pnl_ticks: float
    net_ticks: float  # after costs
    mfe_ticks: float
    mae_ticks: float
    hour_utc: int
    weekday: int  # 0=Mon, 6=Sun


def load_prices():
    con = sqlite3.connect(DB_PATH)
    rows = con.execute("SELECT ts, open, high, low, close, volume FROM candles ORDER BY ts").fetchall()
    con.close()
    print(f"Loaded {len(rows)} candles")
    if rows:
        first = datetime.fromtimestamp(rows[0][0]/1000, tz=timezone.utc).strftime('%Y-%m-%d')
        last = datetime.fromtimestamp(rows[-1][0]/1000, tz=timezone.utc).strftime('%Y-%m-%d')
        days = (rows[-1][0] - rows[0][0]) / 86400000
        print(f"Range: {first} -> {last} ({days:.0f} days)")
    return rows


def compute_trade(prices, idx, direction, hold_minutes):
    """Execute a trade and compute P&L, MFE, MAE."""
    entry = prices[idx][4]  # close price
    ts = prices[idx][0] / 1000
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)

    if idx + hold_minutes >= len(prices):
        return None

    exit_price = prices[idx + hold_minutes][4]

    if direction == "UP":
        pnl_dollar = (exit_price - entry) * CONTRACT_SIZE
    else:
        pnl_dollar = (entry - exit_price) * CONTRACT_SIZE

    pnl_ticks = pnl_dollar / TICK_VALUE
    net_ticks = pnl_ticks - COST_TICKS

    # MFE / MAE
    best = 0.0
    worst = 0.0
    for t in range(1, hold_minutes + 1):
        if idx + t >= len(prices):
            break
        h = prices[idx + t][2]  # high
        l = prices[idx + t][3]  # low
        if direction == "UP":
            fav = (h - entry) * CONTRACT_SIZE / TICK_VALUE
            adv = (l - entry) * CONTRACT_SIZE / TICK_VALUE
        else:
            fav = (entry - l) * CONTRACT_SIZE / TICK_VALUE
            adv = (entry - h) * CONTRACT_SIZE / TICK_VALUE
        best = max(best, fav)
        worst = min(worst, adv)

    return TradeResult(
        entry_idx=idx, direction=direction, entry_price=entry, exit_price=exit_price,
        pnl_ticks=pnl_ticks, net_ticks=net_ticks, mfe_ticks=best, mae_ticks=worst,
        hour_utc=dt.hour, weekday=dt.weekday()
    )


def find_momentum_signals(prices, threshold, lookback=1, cooldown=2):
    """Simple momentum: price change over lookback candles exceeds threshold."""
    signals = []
    last_idx = -999
    for i in range(lookback, len(prices)):
        pct = (prices[i][4] - prices[i - lookback][4]) / prices[i - lookback][4]
        if abs(pct) >= threshold and i - last_idx >= cooldown:
            direction = "UP" if pct > 0 else "DOWN"
            signals.append((i, direction, pct))
            last_idx = i
    return signals


def find_volatility_breakout(prices, period=20, mult=2.0, cooldown=5):
    """Price breaks above/below Bollinger-style band."""
    signals = []
    last_idx = -999
    for i in range(period, len(prices)):
        window = [prices[j][4] for j in range(i - period, i)]
        mean = statistics.mean(window)
        std = statistics.stdev(window) if len(window) > 1 else 0.001
        upper = mean + mult * std
        lower = mean - mult * std
        price = prices[i][4]

        if price > upper and i - last_idx >= cooldown:
            signals.append((i, "UP", (price - mean) / mean))
            last_idx = i
        elif price < lower and i - last_idx >= cooldown:
            signals.append((i, "DOWN", (mean - price) / mean))
            last_idx = i
    return signals


def find_volume_spike(prices, vol_mult=3.0, min_pct=0.001, period=20, cooldown=3):
    """Volume spike + directional move."""
    signals = []
    last_idx = -999
    for i in range(period, len(prices)):
        avg_vol = statistics.mean([prices[j][5] for j in range(i - period, i)])
        if avg_vol == 0:
            continue
        vol_ratio = prices[i][5] / avg_vol
        pct = (prices[i][4] - prices[i-1][4]) / prices[i-1][4]

        if vol_ratio >= vol_mult and abs(pct) >= min_pct and i - last_idx >= cooldown:
            direction = "UP" if pct > 0 else "DOWN"
            signals.append((i, direction, pct))
            last_idx = i
    return signals


def find_consecutive_candles(prices, count=3, min_pct=0.0005, cooldown=5):
    """N consecutive candles in same direction."""
    signals = []
    last_idx = -999
    for i in range(count, len(prices)):
        all_up = all(prices[i-j][4] > prices[i-j-1][4] and
                     (prices[i-j][4] - prices[i-j-1][4])/prices[i-j-1][4] >= min_pct
                     for j in range(count))
        all_down = all(prices[i-j][4] < prices[i-j-1][4] and
                      (prices[i-j-1][4] - prices[i-j][4])/prices[i-j-1][4] >= min_pct
                      for j in range(count))

        if (all_up or all_down) and i - last_idx >= cooldown:
            direction = "UP" if all_up else "DOWN"
            total_pct = (prices[i][4] - prices[i-count][4]) / prices[i-count][4]
            signals.append((i, direction, total_pct))
            last_idx = i
    return signals


def evaluate(prices, signals, hold_minutes, mode="FOLLOW", hour_filter=None, weekday_filter=None):
    """Run all signals through trade simulation."""
    trades = []
    for idx, raw_dir, pct in signals:
        direction = raw_dir if mode == "FOLLOW" else ("DOWN" if raw_dir == "UP" else "UP")

        # Time filters
        ts = prices[idx][0] / 1000
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        if hour_filter and dt.hour not in hour_filter:
            continue
        if weekday_filter and dt.weekday() not in weekday_filter:
            continue

        result = compute_trade(prices, idx, direction, hold_minutes)
        if result:
            trades.append(result)
    return trades


def summarize(trades, label=""):
    """Compute summary statistics."""
    if len(trades) < 5:
        return None

    n = len(trades)
    wins = sum(1 for t in trades if t.net_ticks > 0)
    wr = wins / n * 100
    avg_net = statistics.mean([t.net_ticks for t in trades])
    avg_net_dollar = avg_net * TICK_VALUE
    total_dollar = sum(t.net_ticks * TICK_VALUE for t in trades)
    avg_mfe = statistics.mean([t.mfe_ticks for t in trades])
    avg_mae = statistics.mean([t.mae_ticks for t in trades])

    conf = "ANEC" if n < 30 else "LOW" if n < 107 else "MOD" if n < 385 else "SIG"

    return {
        "label": label, "n": n, "wr": wr, "avg_net": avg_net_dollar,
        "total": total_dollar, "avg_mfe": avg_mfe, "avg_mae": avg_mae, "conf": conf,
        "wins": wins
    }


def main():
    prices = load_prices()
    if len(prices) < 1000:
        print("Not enough data. Run download first.")
        return

    days = (prices[-1][0] - prices[0][0]) / 86400000
    results = []

    print(f"\nRunning comprehensive strategy search...")
    print(f"Cost model: ${COST_RT}/RT = {COST_TICKS:.1f} ticks\n")

    # === STRATEGY 1: Simple Momentum/Fade ===
    for thresh in [0.002, 0.003, 0.004, 0.005, 0.007, 0.01, 0.015, 0.02]:
        for lookback in [1, 3, 5]:
            signals = find_momentum_signals(prices, thresh, lookback=lookback, cooldown=max(3, lookback+1))
            for hold in [3, 5, 10, 15, 20, 30, 45, 60]:
                for mode in ["FOLLOW", "FADE"]:
                    trades = evaluate(prices, signals, hold, mode=mode)
                    s = summarize(trades, f"MOM_{mode} {thresh*100:.1f}%/{lookback}lb/{hold}m")
                    if s:
                        results.append(s)

    # === STRATEGY 2: Volatility Breakout ===
    for period in [10, 20, 30]:
        for mult in [1.5, 2.0, 2.5, 3.0]:
            signals = find_volatility_breakout(prices, period=period, mult=mult, cooldown=5)
            for hold in [5, 10, 15, 20, 30]:
                for mode in ["FOLLOW", "FADE"]:
                    trades = evaluate(prices, signals, hold, mode=mode)
                    s = summarize(trades, f"VOL_{mode} {period}p/{mult}x/{hold}m")
                    if s:
                        results.append(s)

    # === STRATEGY 3: Volume Spike ===
    for vol_mult in [2.0, 3.0, 5.0]:
        for min_pct in [0.001, 0.002]:
            signals = find_volume_spike(prices, vol_mult=vol_mult, min_pct=min_pct, cooldown=3)
            for hold in [5, 10, 15, 20, 30]:
                for mode in ["FOLLOW", "FADE"]:
                    trades = evaluate(prices, signals, hold, mode=mode)
                    s = summarize(trades, f"VOLSPK_{mode} {vol_mult}x/{min_pct*100:.1f}%/{hold}m")
                    if s:
                        results.append(s)

    # === STRATEGY 4: Consecutive Candles ===
    for count in [3, 4, 5]:
        for min_pct in [0.0003, 0.0005, 0.001]:
            signals = find_consecutive_candles(prices, count=count, min_pct=min_pct, cooldown=count+2)
            for hold in [5, 10, 15, 20, 30]:
                for mode in ["FOLLOW", "FADE"]:
                    trades = evaluate(prices, signals, hold, mode=mode)
                    s = summarize(trades, f"CONSEC_{mode} {count}x/{min_pct*100:.2f}%/{hold}m")
                    if s:
                        results.append(s)

    # === TIME-FILTERED: Best base strategies + time filter ===
    # Find top 10 by avg_net, then test with hour filters
    profitable = [r for r in results if r["avg_net"] > 0 and r["n"] >= 15]
    profitable.sort(key=lambda x: x["avg_net"] * x["n"], reverse=True)  # sort by total expected value

    print(f"\nTested {len(results)} configurations")
    print(f"Profitable (net > $0, n >= 15): {len(profitable)}")

    # Sort all results
    results.sort(key=lambda x: x["avg_net"] * min(x["n"], 100), reverse=True)

    # Print top 30
    print(f"\n{'='*120}")
    print(f"TOP 30 CONFIGURATIONS (ranked by expected value * trade count)")
    print(f"{'='*120}")
    print(f"{'#':>3} {'Strategy':<45} {'N':>5} {'WR':>6} {'$/Trade':>8} {'$Total':>9} {'MFE':>6} {'MAE':>6} {'Conf':>4}")
    print(f"{'-'*120}")

    for i, r in enumerate(results[:30]):
        marker = " ***" if r["avg_net"] > 0 and r["n"] >= 30 else ""
        print(f"{i+1:>3} {r['label']:<45} {r['n']:>5} {r['wr']:>5.1f}% ${r['avg_net']:>+6.2f} ${r['total']:>+8.0f} {r['avg_mfe']:>+5.0f} {r['avg_mae']:>+5.0f} {r['conf']:>4}{marker}")

    # Print bottom 5 (worst) for context
    print(f"\nWORST 5:")
    for r in results[-5:]:
        print(f"    {r['label']:<45} {r['n']:>5} {r['wr']:>5.1f}% ${r['avg_net']:>+6.2f} ${r['total']:>+8.0f}")

    # Annualized projections for top profitable
    if profitable:
        print(f"\n{'='*120}")
        print(f"ANNUALIZED PROJECTIONS (top profitable configs)")
        print(f"{'='*120}")
        for r in profitable[:15]:
            signals_per_day = r["n"] / days
            annual = r["avg_net"] * signals_per_day * 365
            bulenox_days = 3000 / (r["avg_net"] * signals_per_day) if r["avg_net"] * signals_per_day > 0 else 9999
            print(f"  {r['label']:<45} ${r['avg_net']:>+.2f}/trade * {signals_per_day:.1f}/day = ${annual:>+,.0f}/yr | Bulenox target: {bulenox_days:.0f} days [{r['conf']}]")
    else:
        print("\nNO PROFITABLE CONFIGURATIONS FOUND after costs.")
        print("The $3.54 round-trip cost destroys all tested edges on 1m BTC data.")


if __name__ == "__main__":
    main()
