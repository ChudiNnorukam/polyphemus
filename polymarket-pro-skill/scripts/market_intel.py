#!/usr/bin/env python3
"""OpenClaw Market Intelligence — daily digest + lagbot knowledge feed.

Reads market_context.json (OI, Fear & Greed), lagbot signals/trades DBs,
and produces:
  1. Structured intelligence report (markdown) at /opt/openclaw/data/intel/
  2. Slack digest summary (daily or on-demand)
  3. lagbot_context.json — live context file lagbot reads at signal time

Usage:
    python3 market_intel.py report    # Generate daily report + Slack digest
    python3 market_intel.py context   # Update lagbot_context.json (every 5 min via cron)
    python3 market_intel.py status    # Print current intelligence state
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# --- Config ---

DATA_DIR = os.environ.get("OPENCLAW_DATA_DIR", "/opt/openclaw/data")
INTEL_DIR = os.path.join(DATA_DIR, "intel")
CONTEXT_PATH = os.path.join(DATA_DIR, "market_context.json")
LAGBOT_CONTEXT_PATH = os.path.join(DATA_DIR, "lagbot_context.json")
HISTORY_PATH = os.path.join(DATA_DIR, "market_context_history.json")

INSTANCES_DIR = "/opt/lagbot/instances"
ASSETS = ["BTC", "ETH"]

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "")


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr)


# --- Data readers ---

def load_market_context() -> dict:
    try:
        with open(CONTEXT_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load_history() -> list:
    try:
        with open(HISTORY_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def query_instance_trades(instance: str, hours: int = 24) -> list:
    """Get recent trades from a lagbot instance's performance DB."""
    db_path = os.path.join(INSTANCES_DIR, instance, "data", "performance.db")
    if not os.path.exists(db_path):
        return []
    try:
        cutoff = time.time() - (hours * 3600)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trades WHERE entry_time > ? ORDER BY entry_time",
            (cutoff,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        _log(f"Error reading trades for {instance}: {e}")
        return []


def query_instance_signals(instance: str, hours: int = 24) -> list:
    """Get recent signals from a lagbot instance's signals DB."""
    db_path = os.path.join(INSTANCES_DIR, instance, "data", "signals.db")
    if not os.path.exists(db_path):
        return []
    try:
        cutoff = time.time() - (hours * 3600)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM signals WHERE timestamp > ? ORDER BY timestamp",
            (cutoff,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        _log(f"Error reading signals for {instance}: {e}")
        return []


def get_active_instances() -> list:
    """Find lagbot instances that have data directories."""
    if not os.path.exists(INSTANCES_DIR):
        return []
    instances = []
    for name in os.listdir(INSTANCES_DIR):
        data_dir = os.path.join(INSTANCES_DIR, name, "data")
        if os.path.isdir(data_dir):
            instances.append(name)
    return sorted(instances)


# --- Intelligence computation ---

def compute_oi_trend(history: list, asset: str) -> dict:
    """Compute OI trend over available history snapshots."""
    values = []
    for entry in history:
        d = entry.get("data", {}).get(asset, {})
        oi = d.get("oi_contracts")
        if oi is not None:
            values.append(oi)
    if len(values) < 2:
        return {"trend": "insufficient_data", "change_pct": 0, "samples": len(values)}
    first, last = values[0], values[-1]
    change_pct = (last - first) / first if first > 0 else 0
    if change_pct > 0.02:
        trend = "rising"
    elif change_pct < -0.02:
        trend = "falling"
    else:
        trend = "flat"
    return {"trend": trend, "change_pct": change_pct, "samples": len(values)}


def compute_trade_stats(trades: list) -> dict:
    """Compute WR and PnL from a list of trade dicts."""
    closed = [t for t in trades if t.get("exit_price") is not None]
    if not closed:
        return {"total": 0, "wins": 0, "losses": 0, "wr": 0, "pnl": 0, "open": len(trades) - len(closed)}
    wins = sum(1 for t in closed if (t.get("pnl") or 0) > 0)
    losses = len(closed) - wins
    pnl = sum(t.get("pnl") or 0 for t in closed)
    return {
        "total": len(closed),
        "wins": wins,
        "losses": losses,
        "wr": round(wins / len(closed) * 100, 1) if closed else 0,
        "pnl": round(pnl, 2),
        "open": len(trades) - len(closed),
    }


def compute_signal_stats(signals: list) -> dict:
    """Compute signal flow stats."""
    total = len(signals)
    passed = sum(1 for s in signals if s.get("guard_passed") == 1)
    executed = sum(1 for s in signals if s.get("outcome") == "executed")
    shadow = sum(1 for s in signals if s.get("outcome") == "shadow")
    filtered = total - passed
    by_asset = {}
    for s in signals:
        a = s.get("asset", "unknown")
        by_asset.setdefault(a, {"total": 0, "passed": 0})
        by_asset[a]["total"] += 1
        if s.get("guard_passed") == 1:
            by_asset[a]["passed"] += 1
    return {
        "total": total,
        "passed": passed,
        "executed": executed,
        "shadow": shadow,
        "filtered": filtered,
        "pass_rate": round(passed / total * 100, 1) if total > 0 else 0,
        "by_asset": by_asset,
    }


# --- Lagbot context (Part 2: decision support) ---

def cmd_context(_args) -> None:
    """Write lagbot_context.json — read by lagbot at signal time."""
    ctx = load_market_context()
    history = load_history()

    lagbot_ctx = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fear_greed": ctx.get("macro", {}).get("fear_greed"),
        "fear_greed_label": ctx.get("macro", {}).get("fear_greed_label", ""),
        "market_regime": "extreme_fear" if (ctx.get("macro", {}).get("fear_greed") or 50) < 20 else
                         "fear" if (ctx.get("macro", {}).get("fear_greed") or 50) < 40 else
                         "neutral" if (ctx.get("macro", {}).get("fear_greed") or 50) < 60 else
                         "greed" if (ctx.get("macro", {}).get("fear_greed") or 50) < 80 else
                         "extreme_greed",
    }

    for asset in ASSETS:
        asset_data = ctx.get(asset, {})
        oi_trend = compute_oi_trend(history, asset)
        lagbot_ctx[asset] = {
            "oi_contracts": asset_data.get("oi_contracts"),
            "oi_change_pct": asset_data.get("oi_change_pct"),
            "oi_trend": oi_trend["trend"],
            "oi_trend_pct": round(oi_trend["change_pct"], 4),
        }

    # Per-instance recent performance (last 6h for fast feedback)
    for instance in get_active_instances():
        trades = query_instance_trades(instance, hours=6)
        stats = compute_trade_stats(trades)
        lagbot_ctx[f"perf_{instance}"] = stats

    with open(LAGBOT_CONTEXT_PATH, "w") as f:
        json.dump(lagbot_ctx, f, indent=2)

    _log(f"lagbot_context.json written: F&G={lagbot_ctx['fear_greed']}, "
         f"regime={lagbot_ctx['market_regime']}")


# --- Daily report (Part 1: intelligence feed) ---

def cmd_report(_args) -> None:
    """Generate daily intelligence report + Slack digest."""
    ctx = load_market_context()
    history = load_history()
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")

    # Gather data
    instance_data = {}
    for instance in get_active_instances():
        trades = query_instance_trades(instance, hours=24)
        signals = query_instance_signals(instance, hours=24)
        instance_data[instance] = {
            "trade_stats": compute_trade_stats(trades),
            "signal_stats": compute_signal_stats(signals),
        }

    # Build report
    lines = [
        f"# Market Intelligence Report - {date_str}",
        f"Generated: {now.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## Market Conditions",
        "",
    ]

    fg = ctx.get("macro", {}).get("fear_greed")
    fg_label = ctx.get("macro", {}).get("fear_greed_label", "")
    lines.append(f"**Fear & Greed Index**: {fg} ({fg_label})")
    lines.append("")

    for asset in ASSETS:
        d = ctx.get(asset, {})
        oi = d.get("oi_contracts")
        oi_change = d.get("oi_change_pct")
        oi_trend = compute_oi_trend(history, asset)
        oi_str = f"{oi:,.0f}" if oi else "n/a"
        change_str = f"{oi_change:+.3%}" if oi_change is not None else "n/a"
        lines.append(f"**{asset} Open Interest**: {oi_str} contracts ({change_str} vs prev, {oi_trend['trend']} trend)")

    lines.extend(["", "## Trading Activity (24h)", ""])

    for instance, data in instance_data.items():
        ts = data["trade_stats"]
        ss = data["signal_stats"]
        lines.append(f"### {instance}")
        lines.append(f"- Trades: {ts['total']} closed, {ts['open']} open")
        if ts["total"] > 0:
            lines.append(f"- WR: {ts['wr']}% ({ts['wins']}W/{ts['losses']}L)")
            lines.append(f"- PnL: ${ts['pnl']:+.2f}")
        lines.append(f"- Signals: {ss['total']} total, {ss['passed']} passed guard ({ss['pass_rate']}%)")
        lines.append(f"- Executed: {ss['executed']}, Shadow: {ss['shadow']}, Filtered: {ss['filtered']}")
        if ss["by_asset"]:
            asset_parts = [f"{a}: {v['passed']}/{v['total']}" for a, v in sorted(ss["by_asset"].items())]
            lines.append(f"- By asset: {', '.join(asset_parts)}")
        lines.append("")

    # Key observations
    lines.extend(["## Key Observations", ""])
    if fg is not None and fg < 20:
        lines.append("- :warning: Extreme Fear regime. Historically high volatility, more momentum signals expected.")
    elif fg is not None and fg > 80:
        lines.append("- :warning: Extreme Greed regime. Potential for sharp reversals.")

    for asset in ASSETS:
        oi_trend = compute_oi_trend(history, asset)
        if oi_trend["trend"] == "rising" and oi_trend["change_pct"] > 0.05:
            lines.append(f"- {asset} OI rising {oi_trend['change_pct']:+.1%}. New money entering, expect increased volatility.")
        elif oi_trend["trend"] == "falling" and oi_trend["change_pct"] < -0.05:
            lines.append(f"- {asset} OI falling {oi_trend['change_pct']:+.1%}. Positions closing, momentum may weaken.")

    combined_wr = 0
    combined_trades = 0
    combined_pnl = 0
    for data in instance_data.values():
        ts = data["trade_stats"]
        combined_trades += ts["total"]
        combined_pnl += ts["pnl"]
        combined_wr += ts["wins"]
    if combined_trades > 0:
        overall_wr = round(combined_wr / combined_trades * 100, 1)
        if overall_wr < 85:
            lines.append(f"- :warning: Combined WR {overall_wr}% below 85% threshold. Review signal quality.")
        if combined_pnl < 0:
            lines.append(f"- :red_circle: Negative PnL day (${combined_pnl:+.2f}). Check for regime shift or bad signals.")

    report_text = "\n".join(lines)

    # Save report
    os.makedirs(INTEL_DIR, exist_ok=True)
    report_path = os.path.join(INTEL_DIR, f"daily_{date_str}.md")
    with open(report_path, "w") as f:
        f.write(report_text)
    _log(f"Report written to {report_path}")

    # Slack digest
    if SLACK_BOT_TOKEN and SLACK_CHANNEL_ID:
        slack_lines = [f":bar_chart: *Daily Intelligence - {date_str}*"]
        slack_lines.append(f"F&G: {fg} ({fg_label})")
        for asset in ASSETS:
            d = ctx.get(asset, {})
            oi = d.get("oi_contracts")
            oi_str = f"{oi:,.0f}" if oi else "n/a"
            slack_lines.append(f"{asset} OI: {oi_str}")
        for instance, data in instance_data.items():
            ts = data["trade_stats"]
            if ts["total"] > 0:
                slack_lines.append(f"*{instance}*: {ts['wr']}% WR ({ts['wins']}W/{ts['losses']}L), ${ts['pnl']:+.2f}")
            else:
                slack_lines.append(f"*{instance}*: no trades")
        if combined_trades > 0:
            slack_lines.append(f"*Combined*: ${combined_pnl:+.2f} PnL across {combined_trades} trades")
        slack_msg = "\n".join(slack_lines)
        _slack_post(slack_msg)
        _log("Slack digest sent")
    else:
        _log("Slack not configured, skipping digest")


def _slack_post(text: str):
    try:
        payload = json.dumps({
            "channel": SLACK_CHANNEL_ID,
            "text": text,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://slack.com/api/chat.postMessage",
            data=payload,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            },
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        if not result.get("ok"):
            _log(f"Slack error: {result.get('error')}")
    except Exception as e:
        _log(f"Slack post failed: {e}")


# --- Status ---

def cmd_status(_args) -> None:
    """Print current intelligence state."""
    ctx = load_market_context()
    if not ctx:
        print("No market context found. Is crypto_watcher running?")
        return

    now = datetime.now(timezone.utc)
    print(f"\nOpenClaw Intelligence Status - {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    fg = ctx.get("macro", {}).get("fear_greed")
    fg_label = ctx.get("macro", {}).get("fear_greed_label", "")
    print(f"Fear & Greed: {fg} ({fg_label})")

    history = load_history()
    for asset in ASSETS:
        d = ctx.get(asset, {})
        oi = d.get("oi_contracts")
        oi_trend = compute_oi_trend(history, asset)
        print(f"{asset} OI: {oi:,.0f} (trend: {oi_trend['trend']}, {oi_trend['change_pct']:+.3%})")

    print(f"\nInstances:")
    for instance in get_active_instances():
        trades = query_instance_trades(instance, hours=24)
        stats = compute_trade_stats(trades)
        print(f"  {instance}: {stats['total']} trades, {stats['wr']}% WR, ${stats['pnl']:+.2f}")

    # Check lagbot_context.json freshness
    try:
        with open(LAGBOT_CONTEXT_PATH) as f:
            lctx = json.load(f)
        print(f"\nlagbot_context.json: {lctx.get('updated_at', 'unknown')}")
        print(f"  regime: {lctx.get('market_regime', 'unknown')}")
    except FileNotFoundError:
        print("\nlagbot_context.json: NOT FOUND (run 'market_intel.py context')")

    # Check latest report
    if os.path.exists(INTEL_DIR):
        reports = sorted(Path(INTEL_DIR).glob("daily_*.md"))
        if reports:
            print(f"  latest report: {reports[-1].name}")

    print()


# --- Main ---

def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenClaw Market Intelligence - daily digest + lagbot context feed"
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("report", help="Generate daily report + Slack digest")
    sub.add_parser("context", help="Update lagbot_context.json")
    sub.add_parser("status", help="Print current intelligence state")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    cmds = {
        "report": cmd_report,
        "context": cmd_context,
        "status": cmd_status,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
