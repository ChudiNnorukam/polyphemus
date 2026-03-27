#!/usr/bin/env python3
"""Auto Monitor -- AI-powered trading bot health check via Haiku.

Standalone script (no lagbot imports, Bug #39 safe).
Queries performance.db + signals.db, sends data to Haiku for analysis,
posts findings to Slack if non-trivial.

Usage:
    python3 auto_monitor.py --instance emmanuel
    python3 auto_monitor.py --instance emmanuel --dry-run   # print only, no Slack
    python3 auto_monitor.py --instance emmanuel --hours 8   # custom lookback

Cron (every 4 hours):
    0 */4 * * * cd /opt/lagbot && /opt/lagbot/venv/bin/python3 lagbot/tools/auto_monitor.py --instance emmanuel
    0 */4 * * * cd /opt/lagbot && /opt/lagbot/venv/bin/python3 lagbot/tools/auto_monitor.py --instance polyphemus
"""

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen

# --- System prompt (cached across runs, ~1500 tokens) ---

SYSTEM_PROMPT = """You are a trading bot health monitor for a Polymarket prediction market bot.

The bot trades crypto 5m Up/Down markets via latency arbitrage:
- Binance WebSocket detects >0.05% price move in 10 seconds
- Bot buys on Polymarket before odds adjust
- Binary outcome: price is either 1.00 (win) or 0.00 (loss)
- At entry price 0.85: win = +$0.15/share, loss = -$0.85/share (5.7:1 asymmetry)
- Break-even WR at typical entries (0.80-0.90) is ~60%

Your job: analyze the trading data and identify issues that need attention.

Analysis priorities (in order):
1. ALARM: balance drop >5% in window, WR below 40% on n>10, zero trades in 4+ hours during market hours
2. WARNING: WR trending down (current window worse than prior), loss magnitude increasing, config drift
3. INFO: normal operation summary, notable patterns

Output format:
SEVERITY: [ALARM|WARNING|INFO]

FINDINGS:
- [bullet points with specific numbers]

ACTION: [what to do, or "none needed"]

Rules:
- Always cite specific numbers (n=X, WR=Y%, avg loss=$Z)
- Flag any stat with n < 10 as ANECDOTAL
- If everything looks healthy, say so briefly (2-3 lines max)
- Never recommend changing live trading parameters directly
- Compare current window to prior window when data is available
"""


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


def query_trades(db_path: str, hours: int) -> dict:
    """Query performance.db for trade stats."""
    if not os.path.exists(db_path):
        return {"error": f"DB not found: {db_path}"}

    cutoff = time.time() - (hours * 3600)
    prior_cutoff = cutoff - (hours * 3600)  # prior window for comparison

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Current window trades
    trades = conn.execute(
        "SELECT * FROM trades WHERE exit_time IS NOT NULL AND entry_time > ? ORDER BY entry_time",
        (cutoff,),
    ).fetchall()

    # Prior window trades (for comparison)
    prior_trades = conn.execute(
        "SELECT * FROM trades WHERE exit_time IS NOT NULL AND entry_time > ? AND entry_time <= ?",
        (prior_cutoff, cutoff),
    ).fetchall()

    # Open positions
    open_pos = conn.execute(
        "SELECT slug, entry_price, entry_time FROM trades WHERE exit_time IS NULL"
    ).fetchall()

    # Estimate balance from cumulative PnL (no balance column in schema)
    balance_row = None

    conn.close()

    def compute_stats(trade_list):
        if not trade_list:
            return {"n": 0, "wr": 0, "pnl": 0, "avg_win": 0, "avg_loss": 0}
        wins = [t for t in trade_list if (t["pnl"] or 0) > 0]
        losses = [t for t in trade_list if (t["pnl"] or 0) <= 0]
        total_pnl = sum(t["pnl"] or 0 for t in trade_list)
        wr = len(wins) / len(trade_list) * 100 if trade_list else 0
        avg_win = sum(t["pnl"] or 0 for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["pnl"] or 0 for t in losses) / len(losses) if losses else 0
        return {
            "n": len(trade_list),
            "wins": len(wins),
            "losses": len(losses),
            "wr": round(wr, 1),
            "pnl": round(total_pnl, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
        }

    # Per-asset breakdown
    asset_stats = {}
    for t in trades:
        slug = t["slug"] or ""
        asset = slug.split("-")[0].upper() if slug else "?"
        asset_stats.setdefault(asset, []).append(t)

    asset_summary = {
        asset: compute_stats(tlist) for asset, tlist in asset_stats.items()
    }

    return {
        "current_window": compute_stats(trades),
        "prior_window": compute_stats(prior_trades),
        "by_asset": asset_summary,
        "open_positions": len(open_pos),
        "open_details": [
            {"slug": p["slug"], "entry_price": p["entry_price"]}
            for p in open_pos
        ],
        "balance": None,
        "hours": hours,
    }


def query_signals(db_path: str, hours: int) -> dict:
    """Query signals.db for signal stats."""
    if not os.path.exists(db_path):
        return {"error": f"signals.db not found"}

    cutoff = time.time()
    cutoff_ts = datetime.fromtimestamp(cutoff - hours * 3600, tz=timezone.utc).isoformat()

    conn = sqlite3.connect(db_path)

    try:
        rows = conn.execute(
            """SELECT source, COUNT(*) as n,
               SUM(CASE WHEN guard_passed=1 THEN 1 ELSE 0 END) as passed,
               SUM(CASE WHEN outcome='executed' THEN 1 ELSE 0 END) as executed
               FROM signals WHERE timestamp > ?
               GROUP BY source ORDER BY n DESC""",
            (cutoff_ts,),
        ).fetchall()
    except sqlite3.OperationalError:
        # signals table might not exist or have different schema
        conn.close()
        return {"error": "signals table query failed"}

    conn.close()

    return {
        "by_source": [
            {"source": r[0], "total": r[1], "passed": r[2], "executed": r[3]}
            for r in rows
        ],
        "total_signals": sum(r[1] for r in rows),
    }


def read_config(env: dict) -> dict:
    """Extract key trading config from .env."""
    keys = [
        "DRY_RUN", "BASE_BET_PCT", "MAX_BET", "MAX_OPEN_POSITIONS",
        "ACTIVE_ASSETS", "ENTRY_MODE", "CONFIDENCE_EXIT_ENABLED",
        "OUTCOME_GATE_ENABLED", "OUTCOME_GATE_DRY_RUN",
        "STREAK_COOLDOWN_LOSSES", "STREAK_COOLDOWN_SECS",
    ]
    return {k: env.get(k, "?") for k in keys if k in env}


def build_prompt(instance: str, trade_data: dict, signal_data: dict, config: dict) -> str:
    """Build the analysis prompt from all data sources."""
    sections = [f"Instance: {instance}", f"Lookback: {trade_data.get('hours', 4)} hours"]

    # Trade data
    cur = trade_data.get("current_window", {})
    prior = trade_data.get("prior_window", {})
    sections.append(f"\nCurrent window: {cur.get('n', 0)} trades, "
                    f"WR={cur.get('wr', 0)}%, PnL=${cur.get('pnl', 0)}, "
                    f"avg_win=${cur.get('avg_win', 0)}, avg_loss=${cur.get('avg_loss', 0)}")
    sections.append(f"Prior window: {prior.get('n', 0)} trades, "
                    f"WR={prior.get('wr', 0)}%, PnL=${prior.get('pnl', 0)}")

    # Per-asset
    by_asset = trade_data.get("by_asset", {})
    if by_asset:
        sections.append("\nPer-asset:")
        for asset, stats in sorted(by_asset.items()):
            sections.append(f"  {asset}: n={stats['n']}, WR={stats['wr']}%, PnL=${stats['pnl']}")

    # Open positions
    sections.append(f"\nOpen positions: {trade_data.get('open_positions', 0)}")
    for p in trade_data.get("open_details", [])[:5]:
        sections.append(f"  {p['slug']} @ {p['entry_price']}")

    # Balance
    bal = trade_data.get("balance")
    if bal:
        sections.append(f"\nBalance: ${bal}")

    # Signals
    if signal_data and not signal_data.get("error"):
        sections.append(f"\nSignals: {signal_data.get('total_signals', 0)} total")
        for s in signal_data.get("by_source", []):
            pass_rate = (s["passed"] / s["total"] * 100) if s["total"] > 0 else 0
            sections.append(f"  {s['source']}: {s['total']} signals, "
                            f"{s['passed']} passed ({pass_rate:.0f}%), "
                            f"{s['executed']} executed")

    # Config
    if config:
        sections.append("\nActive config:")
        for k, v in config.items():
            sections.append(f"  {k}={v}")

    return "\n".join(sections)


def run_monitor(instance: str, hours: int = 4, dry_run: bool = False):
    """Run the full monitoring cycle."""
    env = get_env(instance)
    slack_token = env.get("SLACK_BOT_TOKEN", "")
    slack_channel = env.get("SLACK_CHANNEL_ID", "")

    # Find claude CLI
    claude_bin = shutil.which("claude")
    if not claude_bin:
        print("ERROR: claude CLI not found in PATH")
        return

    data_dir = f"/opt/lagbot/instances/{instance}/data"
    perf_db = os.path.join(data_dir, "performance.db")
    sig_db = os.path.join(data_dir, "signals.db")

    # Gather data
    trade_data = query_trades(perf_db, hours)
    signal_data = query_signals(sig_db, hours)
    config = read_config(env)

    if trade_data.get("error"):
        print(f"ERROR: {trade_data['error']}")
        return

    prompt = build_prompt(instance, trade_data, signal_data, config)
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}] "
          f"Monitoring {instance} ({hours}h lookback, "
          f"{trade_data['current_window']['n']} trades)")

    # Build full prompt with system context
    full_prompt = f"{SYSTEM_PROMPT}\n\n---\n\n{prompt}"

    # Call via Claude CLI stdin pipe (uses OAuth auth from ~/.claude/.credentials.json)
    try:
        result = subprocess.run(
            [claude_bin, "-p", "--model", "haiku"],
            input=full_prompt,
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            print(f"ERROR: claude CLI returned {result.returncode}: {err[:300]}")
            return
        analysis = result.stdout.strip()
    except subprocess.TimeoutExpired:
        print("ERROR: claude CLI timed out after 120s")
        return
    except Exception as e:
        print(f"ERROR: {e}")
        return

    print(f"\n{analysis}\n")

    # Post to Slack if non-trivial and not dry-run
    if dry_run:
        print("[DRY RUN] Slack post skipped")
        return

    is_alarm = "SEVERITY: ALARM" in analysis or "ALARM" in analysis.split("\n")[0]
    is_warning = "SEVERITY: WARNING" in analysis or "WARNING" in analysis.split("\n")[0]

    if (is_alarm or is_warning) and slack_token and slack_channel:
        icon = ":rotating_light:" if is_alarm else ":warning:"
        slack_msg = (
            f"{icon} *AUTO MONITOR* [{instance}]\n"
            f"_{hours}h lookback | {trade_data['current_window']['n']} trades_\n\n"
            f"{analysis}"
        )
        post_slack(slack_token, slack_channel, slack_msg)
        print(f"  Posted to Slack ({('ALARM' if is_alarm else 'WARNING')})")
    elif not is_alarm and not is_warning:
        print("  INFO: healthy, no Slack post needed")
    else:
        print("  Slack not configured, skipping post")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Trading Bot Health Monitor")
    parser.add_argument("--instance", required=True, help="Bot instance name")
    parser.add_argument("--hours", type=int, default=4, help="Lookback hours (default: 4)")
    parser.add_argument("--dry-run", action="store_true", help="Print only, no Slack")
    args = parser.parse_args()

    run_monitor(args.instance, args.hours, args.dry_run)
