#!/usr/bin/env python3
"""Daily Strategy Report - Posts performance summary to Slack.

Runs as a standalone cron job. No imports from lagbot codebase (Bug #39 avoidance).
Queries performance.db and signals.db directly.

Usage:
    python3 daily_report.py --instance emmanuel
    python3 daily_report.py --instance emmanuel --days 7

Cron (00:00 UTC daily):
    0 0 * * * cd /opt/lagbot && /opt/lagbot/venv/bin/python3 lagbot/tools/daily_report.py --instance emmanuel
    0 0 * * * cd /opt/lagbot && /opt/lagbot/venv/bin/python3 lagbot/tools/daily_report.py --instance polyphemus
"""

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen


def get_env(instance: str) -> dict:
    """Read .env file for instance config."""
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
    """Post message to Slack."""
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
            print(f"Slack error: {result.get('error', 'unknown')}")
    except Exception as e:
        print(f"Slack post failed: {e}")


def run_report(instance: str, days: int = 1):
    env = get_env(instance)
    slack_token = env.get("SLACK_BOT_TOKEN", "")
    slack_channel = env.get("SLACK_CHANNEL_ID", "")
    data_dir = f"/opt/lagbot/instances/{instance}/data"

    perf_db = os.path.join(data_dir, "performance.db")
    sig_db = os.path.join(data_dir, "signals.db")

    now = time.time()
    cutoff = now - (days * 86400)

    # Performance data
    conn = sqlite3.connect(perf_db)
    conn.row_factory = sqlite3.Row

    trades = conn.execute(
        "SELECT * FROM trades WHERE exit_time IS NOT NULL AND entry_time > ? ORDER BY entry_time",
        (cutoff,),
    ).fetchall()

    open_positions = conn.execute(
        "SELECT slug, entry_price FROM trades WHERE exit_time IS NULL"
    ).fetchall()

    balance_row = conn.execute(
        "SELECT entry_time, pnl FROM trades ORDER BY entry_time DESC LIMIT 1"
    ).fetchone()

    conn.close()

    # Signal data
    sig_conn = sqlite3.connect(sig_db)

    signal_stats = sig_conn.execute(
        """SELECT source, COUNT(*) as n,
           SUM(CASE WHEN guard_passed=1 THEN 1 ELSE 0 END) as passed,
           SUM(CASE WHEN outcome='executed' THEN 1 ELSE 0 END) as executed
           FROM signals WHERE timestamp > datetime(?, 'unixepoch')
           GROUP BY source ORDER BY n DESC LIMIT 8""",
        (cutoff,),
    ).fetchall()

    sig_conn.close()

    # Compute stats
    wins = sum(1 for t in trades if (t["pnl"] or 0) > 0)
    losses = len(trades) - wins
    total_pnl = sum(t["pnl"] or 0 for t in trades)
    wr = (wins / len(trades) * 100) if trades else 0

    # By source
    source_stats = {}
    for t in trades:
        meta = t["metadata"]
        src = "unknown"
        if meta:
            try:
                src = json.loads(meta).get("source", "unknown")
            except (json.JSONDecodeError, TypeError):
                pass
        if src not in source_stats:
            source_stats[src] = {"w": 0, "l": 0, "pnl": 0}
        if (t["pnl"] or 0) > 0:
            source_stats[src]["w"] += 1
        else:
            source_stats[src]["l"] += 1
        source_stats[src]["pnl"] += t["pnl"] or 0

    # By asset
    asset_stats = {}
    for t in trades:
        slug = t["slug"] or ""
        asset = "BTC"
        for a in ["eth", "sol", "xrp"]:
            if a in slug.lower():
                asset = a.upper()
                break
        if asset not in asset_stats:
            asset_stats[asset] = {"w": 0, "l": 0, "pnl": 0}
        if (t["pnl"] or 0) > 0:
            asset_stats[asset]["w"] += 1
        else:
            asset_stats[asset]["l"] += 1
        asset_stats[asset]["pnl"] += t["pnl"] or 0

    # Best/worst trade
    best = max((t["pnl"] or 0 for t in trades), default=0)
    worst = min((t["pnl"] or 0 for t in trades), default=0)

    # Win/loss ratio and averages
    win_pnls = [t["pnl"] for t in trades if (t["pnl"] or 0) > 0]
    loss_pnls = [t["pnl"] for t in trades if (t["pnl"] or 0) < 0]
    avg_win = sum(win_pnls) / len(win_pnls) if win_pnls else 0
    avg_loss = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0
    wl_ratio = abs(avg_win / avg_loss) if avg_loss else 0
    be_wr = (1 / (1 + wl_ratio)) * 100 if wl_ratio > 0 else 100

    # Streak tracking
    max_win_streak = max_loss_streak = cur_win = cur_loss = 0
    for t in trades:
        if (t["pnl"] or 0) > 0:
            cur_win += 1
            cur_loss = 0
        else:
            cur_loss += 1
            cur_win = 0
        max_win_streak = max(max_win_streak, cur_win)
        max_loss_streak = max(max_loss_streak, cur_loss)

    # Stop catch rate
    stop_caught = sum(1 for t in trades if t["exit_reason"] == "mid_price_stop" and (t["pnl"] or 0) < 0)
    resolution_loss = sum(1 for t in trades if t["exit_reason"] in ("market_resolved", "redeemed_loss") and (t["pnl"] or 0) < 0)
    stop_rate = stop_caught / (stop_caught + resolution_loss) * 100 if (stop_caught + resolution_loss) > 0 else 0

    # By exit reason
    exit_stats = {}
    for t in trades:
        er = t["exit_reason"] or "unknown"
        if er not in exit_stats:
            exit_stats[er] = {"n": 0, "pnl": 0}
        exit_stats[er]["n"] += 1
        exit_stats[er]["pnl"] += t["pnl"] or 0

    # Edge decay: compare first half vs second half WR
    edge_decay = ""
    if len(trades) >= 10:
        mid = len(trades) // 2
        first_wr = sum(1 for t in trades[:mid] if (t["pnl"] or 0) > 0) / mid * 100
        second_wr = sum(1 for t in trades[mid:] if (t["pnl"] or 0) > 0) / (len(trades) - mid) * 100
        diff = second_wr - first_wr
        if diff < -10:
            edge_decay = f"\n:warning: *EDGE DECAY*: WR dropped {first_wr:.0f}% -> {second_wr:.0f}% ({diff:+.0f}pp)"
        elif diff > 10:
            edge_decay = f"\n:rocket: *EDGE IMPROVING*: WR rose {first_wr:.0f}% -> {second_wr:.0f}% ({diff:+.0f}pp)"

    # Format period label
    period = f"{days}d" if days > 1 else "24h"
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Build message
    icon = ":chart_with_upwards_trend:" if total_pnl >= 0 else ":chart_with_downwards_trend:"
    sign = "+" if total_pnl >= 0 else ""

    lines = [
        f"{icon} *{period.upper()} DAILY REPORT* [{instance}] {date_str}",
        f"*{wins}W {losses}L ({wr:.0f}%)*  |  {sign}${total_pnl:.2f}",
    ]

    if best > 0 or worst < 0:
        lines.append(f"Best: +${best:.2f}  |  Worst: ${worst:.2f}")

    # Key metrics
    lines.append(f"Avg win: +${avg_win:.2f}  |  Avg loss: ${avg_loss:.2f}  |  Ratio: {wl_ratio:.1f}x")
    lines.append(f"Break-even WR: {be_wr:.0f}%  |  Actual WR: {wr:.0f}%  |  Edge: {wr - be_wr:+.0f}pp")
    lines.append(f"Streaks: {max_win_streak}W / {max_loss_streak}L  |  Stop catch: {stop_rate:.0f}%")

    # Exit breakdown
    if exit_stats:
        lines.append("\n*By Exit:*")
        for er, s in sorted(exit_stats.items(), key=lambda x: x[1]["n"], reverse=True)[:5]:
            lines.append(f"  {er}: {s['n']} trades, ${s['pnl']:+.2f}")

    # Source breakdown
    if source_stats:
        lines.append("\n*By Source:*")
        for src, s in sorted(source_stats.items(), key=lambda x: x[1]["pnl"], reverse=True):
            n = s["w"] + s["l"]
            sw = s["w"] / n * 100 if n else 0
            lines.append(f"  {src}: {s['w']}W/{s['l']}L ({sw:.0f}%) ${s['pnl']:+.2f}")

    # Asset breakdown
    if asset_stats:
        lines.append("\n*By Asset:*")
        for asset, s in sorted(asset_stats.items(), key=lambda x: x[1]["pnl"], reverse=True):
            n = s["w"] + s["l"]
            sw = s["w"] / n * 100 if n else 0
            lines.append(f"  {asset}: {s['w']}W/{s['l']}L ({sw:.0f}%) ${s['pnl']:+.2f}")

    # Signal pipeline
    if signal_stats:
        lines.append("\n*Signal Pipeline:*")
        for row in signal_stats:
            lines.append(f"  {row[0]}: {row[1]} signals, {row[2]} passed, {row[3]} executed")

    # Open positions
    if open_positions:
        lines.append(f"\n*Open Positions: {len(open_positions)}*")
        for p in open_positions:
            lines.append(f"  {p['slug']} @ {p['entry_price']:.2f}")
    else:
        lines.append("\n*Open Positions: 0*")

    # Edge decay warning
    if edge_decay:
        lines.append(edge_decay)

    msg = "\n".join(lines)

    # Output
    print(msg)
    print(f"\n--- Posting to Slack ---")

    if slack_token and slack_channel:
        post_slack(slack_token, slack_channel, msg)
        print("Posted to Slack.")
    else:
        print("No Slack credentials found. Printed to stdout only.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Daily strategy report")
    parser.add_argument("--instance", required=True, help="Instance name (emmanuel/polyphemus)")
    parser.add_argument("--days", type=int, default=1, help="Lookback days (default: 1)")
    args = parser.parse_args()
    run_report(args.instance, args.days)
