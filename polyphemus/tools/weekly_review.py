#!/usr/bin/env python3
"""Weekly Strategy Review - Comprehensive P&L analysis with config recommendations.

Runs standalone. Queries performance.db + signals.db, generates a structured
review, optionally posts to Slack.

Usage:
    python3 weekly_review.py --instance emmanuel
    python3 weekly_review.py --instance emmanuel --weeks 2

Cron (Sunday 06:00 UTC):
    0 6 * * 0 cd /opt/lagbot && /opt/lagbot/venv/bin/python3 lagbot/tools/weekly_review.py --instance emmanuel
"""

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen


def get_env(instance: str) -> dict:
    env_path = f"/opt/lagbot/instances/{instance}/.env"
    env = {}
    if os.path.exists(env_path):
        for line in open(env_path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def post_slack(token: str, channel: str, text: str):
    payload = json.dumps({"channel": channel, "text": text}).encode("utf-8")
    req = Request(
        "https://slack.com/api/chat.postMessage",
        data=payload,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        resp = urlopen(req, timeout=10)
        result = json.loads(resp.read())
        if not result.get("ok"):
            print(f"Slack error: {result.get('error')}")
    except Exception as e:
        print(f"Slack post failed: {e}")


def run_review(instance: str, weeks: int = 1):
    env = get_env(instance)
    slack_token = env.get("SLACK_BOT_TOKEN", "")
    slack_channel = env.get("SLACK_CHANNEL_ID", "")
    data_dir = f"/opt/lagbot/instances/{instance}/data"

    perf_db = os.path.join(data_dir, "performance.db")
    sig_db = os.path.join(data_dir, "signals.db")

    now = time.time()
    cutoff = now - (weeks * 7 * 86400)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # === TRADES ===
    conn = sqlite3.connect(perf_db)
    conn.row_factory = sqlite3.Row
    trades = conn.execute(
        "SELECT * FROM trades WHERE exit_time IS NOT NULL AND entry_time > ? ORDER BY entry_time",
        (cutoff,),
    ).fetchall()

    balance_row = conn.execute(
        "SELECT entry_time FROM trades ORDER BY entry_time DESC LIMIT 1"
    ).fetchone()
    conn.close()

    if not trades:
        print(f"No trades in the last {weeks} week(s) for {instance}.")
        return

    # Basic stats
    wins = sum(1 for t in trades if (t["pnl"] or 0) > 0)
    losses = len(trades) - wins
    total_pnl = sum(t["pnl"] or 0 for t in trades)
    wr = wins / len(trades) * 100 if trades else 0
    avg_pnl = total_pnl / len(trades) if trades else 0
    best = max((t["pnl"] or 0 for t in trades), default=0)
    worst = min((t["pnl"] or 0 for t in trades), default=0)
    days_active = max(1, (now - cutoff) / 86400)
    daily_pnl = total_pnl / days_active

    # Win/loss streaks
    max_win_streak = max_loss_streak = 0
    cur_win = cur_loss = 0
    for t in trades:
        if (t["pnl"] or 0) > 0:
            cur_win += 1
            cur_loss = 0
        else:
            cur_loss += 1
            cur_win = 0
        max_win_streak = max(max_win_streak, cur_win)
        max_loss_streak = max(max_loss_streak, cur_loss)

    # Drawdown
    cum_pnl = 0
    peak = 0
    max_dd = 0
    for t in trades:
        cum_pnl += t["pnl"] or 0
        peak = max(peak, cum_pnl)
        max_dd = max(max_dd, peak - cum_pnl)

    # By source
    by_source = {}
    for t in trades:
        meta = t["metadata"]
        src = "unknown"
        if meta:
            try:
                src = json.loads(meta).get("source", "unknown")
            except (json.JSONDecodeError, TypeError):
                pass
        if src not in by_source:
            by_source[src] = {"w": 0, "l": 0, "pnl": 0.0}
        if (t["pnl"] or 0) > 0:
            by_source[src]["w"] += 1
        else:
            by_source[src]["l"] += 1
        by_source[src]["pnl"] += t["pnl"] or 0

    # By entry price bucket
    by_price = {}
    for t in trades:
        ep = t["entry_price"] or 0
        bucket = f"{int(ep * 10) / 10:.1f}"
        if bucket not in by_price:
            by_price[bucket] = {"w": 0, "l": 0, "pnl": 0.0}
        if (t["pnl"] or 0) > 0:
            by_price[bucket]["w"] += 1
        else:
            by_price[bucket]["l"] += 1
        by_price[bucket]["pnl"] += t["pnl"] or 0

    # By exit reason
    by_exit = {}
    for t in trades:
        er = t["exit_reason"] or "unknown"
        if er not in by_exit:
            by_exit[er] = {"n": 0, "pnl": 0.0}
        by_exit[er]["n"] += 1
        by_exit[er]["pnl"] += t["pnl"] or 0

    # By hour
    by_hour = {}
    for t in trades:
        h = datetime.fromtimestamp(t["entry_time"], tz=timezone.utc).hour
        if h not in by_hour:
            by_hour[h] = {"w": 0, "l": 0, "pnl": 0.0}
        if (t["pnl"] or 0) > 0:
            by_hour[h]["w"] += 1
        else:
            by_hour[h]["l"] += 1
        by_hour[h]["pnl"] += t["pnl"] or 0

    # === SIGNALS ===
    sig_conn = sqlite3.connect(sig_db)
    sig_total = sig_conn.execute(
        "SELECT COUNT(*) FROM signals WHERE timestamp > datetime(?, 'unixepoch')",
        (cutoff,),
    ).fetchone()[0]

    sig_passed = sig_conn.execute(
        "SELECT COUNT(*) FROM signals WHERE guard_passed=1 AND timestamp > datetime(?, 'unixepoch')",
        (cutoff,),
    ).fetchone()[0]

    sig_executed = sig_conn.execute(
        "SELECT COUNT(*) FROM signals WHERE outcome='executed' AND timestamp > datetime(?, 'unixepoch')",
        (cutoff,),
    ).fetchone()[0]
    sig_conn.close()

    fill_rate = sig_executed / sig_passed * 100 if sig_passed else 0

    # === BUILD REPORT ===
    lines = [
        f":bar_chart: *WEEKLY STRATEGY REVIEW* [{instance}] {date_str}",
        f"Period: {weeks} week(s) | {len(trades)} trades",
        "",
        f"*Performance*",
        f"  WR: {wr:.1f}% ({wins}W/{losses}L)",
        f"  P&L: ${total_pnl:+.2f} (${daily_pnl:+.2f}/day)",
        f"  Avg: ${avg_pnl:+.2f}/trade",
        f"  Best: +${best:.2f} | Worst: ${worst:.2f}",
        f"  Max DD: ${max_dd:.2f}",
        f"  Streaks: {max_win_streak}W / {max_loss_streak}L",
        "",
        f"*Signal Pipeline*",
        f"  Total: {sig_total} | Passed: {sig_passed} | Executed: {sig_executed}",
        f"  Fill rate: {fill_rate:.0f}%",
    ]

    # Source breakdown
    if by_source:
        lines.append("\n*By Source*")
        for src, s in sorted(by_source.items(), key=lambda x: x[1]["pnl"], reverse=True):
            n = s["w"] + s["l"]
            sw = s["w"] / n * 100 if n else 0
            lines.append(f"  {src}: {n} trades, {sw:.0f}% WR, ${s['pnl']:+.2f}")

    # Exit reason
    if by_exit:
        lines.append("\n*By Exit Reason*")
        for er, s in sorted(by_exit.items(), key=lambda x: x[1]["pnl"], reverse=True):
            lines.append(f"  {er}: {s['n']} trades, ${s['pnl']:+.2f}")

    # Entry price
    if by_price:
        lines.append("\n*By Entry Price*")
        for bucket in sorted(by_price.keys()):
            s = by_price[bucket]
            n = s["w"] + s["l"]
            sw = s["w"] / n * 100 if n else 0
            lines.append(f"  {bucket}: {n} trades, {sw:.0f}% WR, ${s['pnl']:+.2f}")

    # Best/worst hours
    if by_hour:
        best_hours = sorted(by_hour.items(), key=lambda x: x[1]["pnl"], reverse=True)[:3]
        worst_hours = sorted(by_hour.items(), key=lambda x: x[1]["pnl"])[:3]
        lines.append("\n*Best Hours (UTC)*")
        for h, s in best_hours:
            lines.append(f"  {h:02d}:00 - ${s['pnl']:+.2f} ({s['w']}W/{s['l']}L)")
        lines.append("*Worst Hours (UTC)*")
        for h, s in worst_hours:
            lines.append(f"  {h:02d}:00 - ${s['pnl']:+.2f} ({s['w']}W/{s['l']}L)")

    # Recommendations
    lines.append("\n*Recommendations*")
    if wr < 60:
        lines.append("  :red_circle: WR below 60%. Review entry criteria.")
    if max_loss_streak >= 5:
        lines.append(f"  :warning: {max_loss_streak} consecutive losses. Check regime filter.")
    if fill_rate < 20:
        lines.append(f"  :warning: Fill rate {fill_rate:.0f}%. Consider taker mode for more fills.")
    if max_dd > total_pnl * 2:
        lines.append(f"  :warning: Max drawdown (${max_dd:.2f}) > 2x total P&L. Reduce sizing.")
    if wr >= 70 and total_pnl > 0:
        lines.append("  :white_check_mark: Strategy performing well. Monitor for edge decay.")
    if not trades:
        lines.append("  :question: No trades this week. Check if bot is running.")

    msg = "\n".join(lines)
    print(msg)

    if slack_token and slack_channel:
        post_slack(slack_token, slack_channel, msg)
        print("\nPosted to Slack.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Weekly strategy review")
    parser.add_argument("--instance", required=True)
    parser.add_argument("--weeks", type=int, default=1, help="Lookback weeks (default: 1)")
    args = parser.parse_args()
    run_review(args.instance, args.weeks)
