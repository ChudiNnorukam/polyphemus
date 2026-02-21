#!/usr/bin/env python3
"""Automated Performance Review — analyzes recent trades and suggests tuning.

Runs against the signal logger DB. Produces a plain-text report with:
1. Overall stats (WR, PnL, ROI)
2. Rolling window analysis (detect regime changes)
3. Direction bias (Up vs Down)
4. Entry price zone analysis
5. Exit type breakdown
6. Momentum trigger effectiveness
7. Time-of-day patterns
8. Parameter suggestions (with confidence levels)

Usage:
    python3 perf_review.py                          # analyze all trades
    python3 perf_review.py --days 7                 # last 7 days only
    python3 perf_review.py --db /path/to/signals.db # custom DB path
    python3 perf_review.py --json                   # output as JSON
"""

import argparse
import json
import math
import sqlite3
import sys
import time
from datetime import datetime, timezone


def wilson_ci(wins, total, z=1.96):
    """Wilson score 95% confidence interval for win rate."""
    if total == 0:
        return 0.0, 0.0, 0.0
    p = wins / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denom
    return round(p * 100, 1), round((center - spread) * 100, 1), round((center + spread) * 100, 1)


def load_trades(db_path, days=None):
    """Load completed trades from signal logger DB."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    query = """
        SELECT * FROM signals
        WHERE exit_price IS NOT NULL AND is_win IS NOT NULL
    """
    if days:
        cutoff = time.time() - (days * 86400)
        query += f" AND epoch > {cutoff}"
    query += " ORDER BY epoch ASC"
    rows = conn.execute(query).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def load_rejected(db_path, days=None):
    """Load rejected signals for filter analysis."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    query = "SELECT * FROM signals WHERE guard_passed = 0"
    if days:
        cutoff = time.time() - (days * 86400)
        query += f" AND epoch > {cutoff}"
    rows = conn.execute(query).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def section(title):
    return f"\n{'='*70}\n  {title}\n{'='*70}"


def analyze(trades, rejected, current_config=None):
    """Run full analysis and return report lines + suggestions."""
    lines = []
    suggestions = []

    if not trades:
        lines.append("No completed trades found.")
        return lines, suggestions

    # ── 1. OVERALL STATS ──
    lines.append(section("OVERALL PERFORMANCE"))

    n = len(trades)
    wins = sum(1 for t in trades if t["is_win"])
    losses = n - wins
    wr, wr_lo, wr_hi = wilson_ci(wins, n)
    total_pnl = sum(t["pnl"] for t in trades)
    avg_pnl = total_pnl / n
    win_pnls = [t["pnl"] for t in trades if t["is_win"]]
    loss_pnls = [t["pnl"] for t in trades if not t["is_win"]]
    avg_win = sum(win_pnls) / len(win_pnls) if win_pnls else 0
    avg_loss = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0

    first_ts = trades[0].get("timestamp", "?")[:19]
    last_ts = trades[-1].get("timestamp", "?")[:19]

    lines.append(f"  Period: {first_ts} → {last_ts}")
    lines.append(f"  Trades: {n} ({wins}W / {losses}L)")
    lines.append(f"  Win Rate: {wr}% (95% CI: [{wr_lo}%, {wr_hi}%])")
    lines.append(f"  Total PnL: ${total_pnl:+.2f}")
    lines.append(f"  Avg PnL/trade: ${avg_pnl:+.2f}")
    lines.append(f"  Avg Win: ${avg_win:+.2f} | Avg Loss: ${avg_loss:+.2f}")
    if avg_loss != 0:
        lines.append(f"  Win/Loss Ratio: {abs(avg_win/avg_loss):.2f}x")

    # Breakeven WR (given avg win/loss sizes)
    if avg_win > 0 and avg_loss < 0:
        be_wr = abs(avg_loss) / (avg_win + abs(avg_loss)) * 100
        lines.append(f"  Breakeven WR: {be_wr:.1f}% (current: {wr}%)")
        if wr_lo < be_wr:
            suggestions.append(("WARNING", f"WR lower bound ({wr_lo}%) overlaps breakeven ({be_wr:.1f}%) — edge not statistically confirmed yet"))

    # ── 2. ROLLING WINDOW ──
    lines.append(section("ROLLING PERFORMANCE (10-trade windows)"))

    window = 10
    if n >= window * 2:
        for start in range(0, n - window + 1, window):
            chunk = trades[start:start + window]
            w = sum(1 for t in chunk if t["is_win"])
            pnl = sum(t["pnl"] for t in chunk)
            wr_chunk = w / len(chunk) * 100
            ts = chunk[0].get("timestamp", "?")[5:16]
            bar = "█" * int(wr_chunk / 10) + "░" * (10 - int(wr_chunk / 10))
            lines.append(f"  {ts}  {bar}  {wr_chunk:>5.0f}% ({w}/{len(chunk)})  ${pnl:>+7.2f}")

        # Trend detection
        first_half = trades[:n//2]
        second_half = trades[n//2:]
        wr1 = sum(1 for t in first_half if t["is_win"]) / len(first_half) * 100
        wr2 = sum(1 for t in second_half if t["is_win"]) / len(second_half) * 100
        pnl1 = sum(t["pnl"] for t in first_half)
        pnl2 = sum(t["pnl"] for t in second_half)

        if wr2 < wr1 - 15:
            suggestions.append(("ALERT", f"WR declining: {wr1:.0f}% → {wr2:.0f}% (second half). Possible regime change."))
        elif wr2 > wr1 + 15:
            lines.append(f"\n  Trend: IMPROVING ({wr1:.0f}% → {wr2:.0f}%)")
        lines.append(f"\n  First half:  {wr1:.0f}% WR, ${pnl1:+.2f}")
        lines.append(f"  Second half: {wr2:.0f}% WR, ${pnl2:+.2f}")

    # ── 3. DIRECTION BIAS ──
    lines.append(section("DIRECTION ANALYSIS"))

    for direction in ["Up", "Down"]:
        dt = [t for t in trades if t.get("direction", "").lower() == direction.lower()]
        if not dt:
            continue
        dw = sum(1 for t in dt if t["is_win"])
        dwr, dlo, dhi = wilson_ci(dw, len(dt))
        dpnl = sum(t["pnl"] for t in dt)
        davg = dpnl / len(dt) if dt else 0
        lines.append(f"  {direction:>5}: {len(dt):>3} trades | {dwr}% WR [{dlo}-{dhi}%] | ${dpnl:>+7.2f} total | ${davg:>+.2f}/trade")

    up_trades = [t for t in trades if t.get("direction", "").lower() == "up"]
    down_trades = [t for t in trades if t.get("direction", "").lower() == "down"]
    if up_trades and down_trades:
        up_wr = sum(1 for t in up_trades if t["is_win"]) / len(up_trades) * 100
        dn_wr = sum(1 for t in down_trades if t["is_win"]) / len(down_trades) * 100
        if abs(up_wr - dn_wr) > 20 and min(len(up_trades), len(down_trades)) >= 5:
            worse = "Up" if up_wr < dn_wr else "Down"
            better = "Down" if worse == "Up" else "Up"
            suggestions.append(("INFO", f"{worse} direction significantly worse ({up_wr:.0f}% vs {dn_wr:.0f}%). Consider direction-weighting if gap persists with more data."))

    # ── 4. ENTRY PRICE ZONES ──
    lines.append(section("ENTRY PRICE ZONES"))

    zones = [
        ("$0.15-0.30", 0.15, 0.30),
        ("$0.30-0.45", 0.30, 0.45),
        ("$0.45-0.60", 0.45, 0.60),
        ("$0.60-0.75", 0.60, 0.75),
        ("$0.75-0.90", 0.75, 0.90),
        ("$0.90+", 0.90, 1.01),
    ]

    lines.append(f"  {'Zone':<12} {'Trades':>6} {'WR':>6} {'PnL':>9} {'Avg':>7}")
    lines.append(f"  {'-'*12} {'-'*6} {'-'*6} {'-'*9} {'-'*7}")

    for label, lo, hi in zones:
        zt = [t for t in trades if lo <= (t.get("entry_price") or 0) < hi]
        if not zt:
            continue
        zw = sum(1 for t in zt if t["is_win"])
        zwr = zw / len(zt) * 100
        zpnl = sum(t["pnl"] for t in zt)
        zavg = zpnl / len(zt)
        lines.append(f"  {label:<12} {len(zt):>6} {zwr:>5.0f}% ${zpnl:>+8.2f} ${zavg:>+6.2f}")

        if len(zt) >= 5 and zwr < 40:
            suggestions.append(("TUNE", f"Zone {label} has {zwr:.0f}% WR on {len(zt)} trades — consider excluding"))

    # ── 5. EXIT TYPE BREAKDOWN ──
    lines.append(section("EXIT TYPES"))

    exit_types = {}
    for t in trades:
        reason = t.get("exit_reason", "unknown")
        if reason not in exit_types:
            exit_types[reason] = {"count": 0, "wins": 0, "pnl": 0.0}
        exit_types[reason]["count"] += 1
        exit_types[reason]["wins"] += 1 if t["is_win"] else 0
        exit_types[reason]["pnl"] += t["pnl"]

    lines.append(f"  {'Exit Type':<18} {'Count':>6} {'WR':>6} {'PnL':>9} {'Avg':>7}")
    lines.append(f"  {'-'*18} {'-'*6} {'-'*6} {'-'*9} {'-'*7}")

    for reason, stats in sorted(exit_types.items(), key=lambda x: -x[1]["count"]):
        wr_exit = stats["wins"] / stats["count"] * 100
        avg = stats["pnl"] / stats["count"]
        lines.append(f"  {reason:<18} {stats['count']:>6} {wr_exit:>5.0f}% ${stats['pnl']:>+8.2f} ${avg:>+6.2f}")

    # Check if stop losses are too costly
    sl = exit_types.get("stop_loss", {})
    pt = exit_types.get("profit_target", {})
    if sl.get("count", 0) >= 3 and pt.get("count", 0) >= 3:
        sl_avg = sl["pnl"] / sl["count"]
        pt_avg = pt["pnl"] / pt["count"]
        if abs(sl_avg) > pt_avg * 2:
            suggestions.append(("TUNE", f"Avg stop_loss (${sl_avg:.2f}) is {abs(sl_avg)/pt_avg:.1f}x larger than avg profit_target (${pt_avg:.2f}). Consider tightening stop or widening profit target."))

    # ── 6. MOMENTUM TRIGGER ANALYSIS ──
    lines.append(section("MOMENTUM TRIGGER"))

    mom_trades = [t for t in trades if t.get("momentum_pct") is not None]
    if mom_trades:
        mom_vals = [abs(t["momentum_pct"]) * 100 for t in mom_trades]
        mom_wins = [abs(t["momentum_pct"]) * 100 for t in mom_trades if t["is_win"]]
        mom_losses = [abs(t["momentum_pct"]) * 100 for t in mom_trades if not t["is_win"]]

        lines.append(f"  Avg momentum (all): {sum(mom_vals)/len(mom_vals):.2f}%")
        if mom_wins:
            lines.append(f"  Avg momentum (wins): {sum(mom_wins)/len(mom_wins):.2f}%")
        if mom_losses:
            lines.append(f"  Avg momentum (losses): {sum(mom_losses)/len(mom_losses):.2f}%")

        # Bucket by momentum strength
        buckets = [("0.28-0.35%", 0.28, 0.35), ("0.35-0.50%", 0.35, 0.50), ("0.50%+", 0.50, 5.0)]
        for label, lo, hi in buckets:
            bt = [t for t in mom_trades if lo <= abs(t["momentum_pct"]) * 100 < hi]
            if not bt:
                continue
            bw = sum(1 for t in bt if t["is_win"])
            bwr = bw / len(bt) * 100
            bpnl = sum(t["pnl"] for t in bt)
            lines.append(f"  {label:>12}: {len(bt):>3} trades | {bwr:.0f}% WR | ${bpnl:+.2f}")

    # ── 7. TIME-OF-DAY ──
    lines.append(section("TIME OF DAY (UTC)"))

    hour_stats = {}
    for t in trades:
        h = t.get("hour_utc")
        if h is None:
            continue
        if h not in hour_stats:
            hour_stats[h] = {"count": 0, "wins": 0, "pnl": 0.0}
        hour_stats[h]["count"] += 1
        hour_stats[h]["wins"] += 1 if t["is_win"] else 0
        hour_stats[h]["pnl"] += t["pnl"]

    if hour_stats:
        # Group into 4-hour blocks
        blocks = [(0, 4, "00-04"), (4, 8, "04-08"), (8, 12, "08-12"),
                  (12, 16, "12-16"), (16, 20, "16-20"), (20, 24, "20-24")]
        for lo, hi, label in blocks:
            bt = sum(hour_stats.get(h, {"count": 0})["count"] for h in range(lo, hi))
            bw = sum(hour_stats.get(h, {"count": 0, "wins": 0})["wins"] for h in range(lo, hi))
            bp = sum(hour_stats.get(h, {"count": 0, "pnl": 0})["pnl"] for h in range(lo, hi))
            if bt > 0:
                bwr = bw / bt * 100
                lines.append(f"  {label} UTC: {bt:>3} trades | {bwr:>5.0f}% WR | ${bp:>+7.2f}")

    # ── 8. HOLD TIME ANALYSIS ──
    lines.append(section("HOLD TIME"))

    hold_wins = [t.get("hold_secs", 0) for t in trades if t["is_win"] and t.get("hold_secs")]
    hold_losses = [t.get("hold_secs", 0) for t in trades if not t["is_win"] and t.get("hold_secs")]
    if hold_wins:
        lines.append(f"  Avg hold (wins):   {sum(hold_wins)/len(hold_wins):.0f}s")
    if hold_losses:
        lines.append(f"  Avg hold (losses): {sum(hold_losses)/len(hold_losses):.0f}s")

    # ── 9. FILTER ANALYSIS ──
    if rejected:
        lines.append(section("SIGNAL FILTER ANALYSIS"))
        lines.append(f"  Total rejected signals: {len(rejected)}")

        reasons = {}
        for r in rejected:
            for reason in (r.get("guard_reasons") or "").split(","):
                reason = reason.strip()
                if reason:
                    reasons[reason] = reasons.get(reason, 0) + 1

        lines.append(f"  {'Reason':<25} {'Count':>6}")
        lines.append(f"  {'-'*25} {'-'*6}")
        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            lines.append(f"  {reason:<25} {count:>6}")

    # ── 10. CATASTROPHIC TRADES ──
    big_losses = [t for t in trades if t["pnl"] < -5]
    if big_losses:
        lines.append(section("CATASTROPHIC TRADES (> $5 loss)"))
        for t in sorted(big_losses, key=lambda x: x["pnl"]):
            lines.append(
                f"  ${t['pnl']:>+8.2f} | {t.get('direction','?'):>4} @ ${t.get('entry_price',0):.3f}"
                f" → ${t.get('exit_price',0):.3f} | {t.get('exit_reason','?')}"
                f" | mom={abs(t.get('momentum_pct',0))*100:.2f}%"
                f" | {t.get('time_remaining_secs',0)}s left"
            )

    # ── SUGGESTIONS SUMMARY ──
    lines.append(section("SUGGESTIONS"))

    if not suggestions:
        lines.append("  No parameter changes suggested. Current config looks reasonable.")
        lines.append("  (Need more data for high-confidence recommendations.)")
    else:
        for level, msg in suggestions:
            icon = {"WARNING": "!!", "ALERT": ">>", "TUNE": "->", "INFO": "  "}
            lines.append(f"  [{level:>7}] {msg}")

    # Minimum sample size warning
    if n < 50:
        lines.append(f"\n  NOTE: Only {n} trades. Need 50+ for reliable parameter tuning.")
        lines.append(f"  Confidence intervals are wide — wait for more data before changing config.")

    return lines, suggestions


def main():
    parser = argparse.ArgumentParser(description="Lagbot Performance Review")
    parser.add_argument("--db", default="data/signals.db", help="Path to signals.db")
    parser.add_argument("--days", type=int, help="Only analyze last N days")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    trades = load_trades(args.db, days=args.days)
    rejected = load_rejected(args.db, days=args.days)

    lines, suggestions = analyze(trades, rejected)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = f"\n  LAGBOT PERFORMANCE REVIEW — {now}\n"

    if args.json:
        print(json.dumps({
            "timestamp": now,
            "total_trades": len(trades),
            "suggestions": [{"level": s[0], "message": s[1]} for s in suggestions],
            "report": "\n".join(lines),
        }, indent=2))
    else:
        print(header)
        print("\n".join(lines))
        print()


if __name__ == "__main__":
    main()
