#!/usr/bin/env python3
"""OpenClaw Evolution Engine v1 — daily stats + config recommendations.

Queries all lagbot instance trade/signal DBs, computes performance metrics
with R8 confidence labels, detects regime shifts, and generates daily
evolution reports with optional Slack digest.

Usage:
    python3 evolution_engine.py daily        # Full daily report
    python3 evolution_engine.py stats        # Quick stats only (stdout)
    python3 evolution_engine.py changelog    # Show strategy changelog
"""

import argparse
import json
import math
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# --- Config ---

DATA_DIR = os.environ.get("OPENCLAW_DATA_DIR", "/opt/openclaw/data")
EVOLUTION_DIR = os.path.join(DATA_DIR, "evolution")
INSTANCES_DIR = os.environ.get("LAGBOT_INSTANCES_DIR", "/opt/lagbot/instances")
MARKET_CONTEXT_PATH = os.path.join(DATA_DIR, "market_context.json")
CHANGELOG_PATH = os.path.join(EVOLUTION_DIR, "strategy_changelog.md")

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "")

INSTANCES = ["emmanuel", "polyphemus"]
LOOKBACK_HOURS = int(os.environ.get("EVOLUTION_LOOKBACK_HOURS", "24"))


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr)


def _r8_label(n: int) -> str:
    """R8 sample size gate label."""
    if n < 30:
        return f"ANECDOTAL n={n}"
    elif n < 107:
        return f"LOW CONFIDENCE 70% CI n={n}"
    elif n < 385:
        return f"MODERATE CONFIDENCE 95% CI n={n}"
    else:
        return f"SIGNIFICANT 99% CI n={n}"


# --- DB Queries ---

def _connect(db_path: str):
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _extract_asset(slug: str) -> str:
    """Extract asset from slug like 'btc-updown-5m-1772349900'."""
    if not slug:
        return "UNKNOWN"
    part = slug.split("-")[0].upper()
    if part in ("BTC", "ETH", "SOL", "XRP"):
        return part
    return "OTHER"


def _extract_window(slug: str) -> str:
    """Extract window from slug like 'btc-updown-5m-1772349900'."""
    if not slug:
        return "?"
    parts = slug.split("-")
    for p in parts:
        if p in ("5m", "15m", "1h"):
            return p
    return "?"


def _extract_epoch(slug: str) -> int:
    """Extract epoch timestamp from slug."""
    if not slug:
        return 0
    parts = slug.split("-")
    for p in reversed(parts):
        if p.isdigit() and len(p) >= 10:
            return int(p)
    return 0


def query_trades(instance: str, since_ts: float) -> list:
    """Get completed trades since timestamp."""
    db = os.path.join(INSTANCES_DIR, instance, "data", "performance.db")
    conn = _connect(db)
    if not conn:
        return []
    try:
        rows = conn.execute("""
            SELECT slug, entry_price, exit_price, pnl, entry_time, exit_time,
                   entry_size, exit_reason, outcome, strategy, metadata
            FROM trades
            WHERE exit_time IS NOT NULL AND entry_time >= ?
            ORDER BY entry_time
        """, (since_ts,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_signals(instance: str, since_ts: float) -> list:
    """Get all signals (passed + filtered) since timestamp."""
    db = os.path.join(INSTANCES_DIR, instance, "data", "signals.db")
    conn = _connect(db)
    if not conn:
        return []
    try:
        rows = conn.execute("""
            SELECT asset, source, direction, momentum_pct, midpoint,
                   time_remaining_secs, hour_utc, guard_passed, guard_reasons,
                   outcome, pnl, is_win, dry_run, strategy_type,
                   fear_greed, market_regime, slug, epoch
            FROM signals
            WHERE epoch >= ?
            ORDER BY epoch
        """, (since_ts,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def load_market_context() -> dict:
    try:
        with open(MARKET_CONTEXT_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# --- Stats Engine ---

def compute_trade_stats(trades: list) -> dict:
    """Compute performance stats from trade list."""
    if not trades:
        return {"n": 0, "label": "NO DATA"}

    n = len(trades)
    wins = [t for t in trades if (t.get("pnl") or 0) > 0]
    losses = [t for t in trades if (t.get("pnl") or 0) <= 0]
    total_pnl = sum(t.get("pnl") or 0 for t in trades)
    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
    wr = len(wins) / n * 100 if n else 0
    expectancy = total_pnl / n if n else 0

    return {
        "n": n,
        "wins": len(wins),
        "losses": len(losses),
        "wr": round(wr, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "label": _r8_label(n),
    }


def stats_by_asset(trades: list) -> dict:
    """Break down stats by asset."""
    buckets = {}
    for t in trades:
        asset = _extract_asset(t.get("slug", ""))
        buckets.setdefault(asset, []).append(t)
    return {asset: compute_trade_stats(tlist) for asset, tlist in sorted(buckets.items())}


def stats_by_hour(trades: list) -> dict:
    """Break down stats by hour UTC."""
    buckets = {}
    for t in trades:
        et = t.get("entry_time")
        if not et:
            continue
        hour = int(datetime.fromtimestamp(et, tz=timezone.utc).strftime("%H"))
        buckets.setdefault(hour, []).append(t)
    return {h: compute_trade_stats(tlist) for h, tlist in sorted(buckets.items())}


def stats_by_entry_price(trades: list) -> dict:
    """Break down stats by entry price bucket."""
    buckets_def = [
        (0.50, 0.70, "0.50-0.70"),
        (0.70, 0.80, "0.70-0.80"),
        (0.80, 0.85, "0.80-0.85"),
        (0.85, 0.90, "0.85-0.90"),
        (0.90, 0.93, "0.90-0.93"),
        (0.93, 0.95, "0.93-0.95"),
        (0.95, 0.98, "0.95-0.98"),
        (0.98, 1.01, "0.98-1.00"),
    ]
    buckets = {label: [] for _, _, label in buckets_def}
    for t in trades:
        ep = t.get("entry_price") or 0
        for lo, hi, label in buckets_def:
            if lo <= ep < hi:
                buckets[label].append(t)
                break
    return {label: compute_trade_stats(tlist) for label, tlist in buckets.items() if tlist}


def stats_by_secs_remaining(trades: list) -> dict:
    """Break down stats by seconds remaining at entry."""
    buckets_def = [
        (0, 10, "0-10s"),
        (10, 20, "10-20s"),
        (20, 30, "20-30s"),
        (30, 45, "30-45s"),
        (45, 60, "45-60s"),
        (60, 300, "60-300s"),
    ]
    buckets = {label: [] for _, _, label in buckets_def}
    for t in trades:
        slug = t.get("slug", "")
        epoch = _extract_epoch(slug)
        et = t.get("entry_time")
        if not epoch or not et:
            continue
        window = 300 if "-5m-" in slug else 900 if "-15m-" in slug else 300
        secs_left = (epoch + window) - et
        for lo, hi, label in buckets_def:
            if lo <= secs_left < hi:
                buckets[label].append(t)
                break
    return {label: compute_trade_stats(tlist) for label, tlist in buckets.items() if tlist}


def signal_filter_stats(signals: list) -> dict:
    """Analyze signal filtering: what passed, what was filtered, why."""
    total = len(signals)
    passed = [s for s in signals if s.get("guard_passed") == 1]
    filtered = [s for s in signals if s.get("guard_passed") == 0]
    dry_run = [s for s in signals if s.get("dry_run") == 1]

    # Count filter reasons
    reason_counts = {}
    for s in filtered:
        reasons = s.get("guard_reasons") or ""
        for r in reasons.split(","):
            r = r.strip()
            if r:
                reason_counts[r] = reason_counts.get(r, 0) + 1

    return {
        "total_signals": total,
        "passed": len(passed),
        "filtered": len(filtered),
        "dry_run": len(dry_run),
        "filter_rate": round(len(filtered) / total * 100, 1) if total else 0,
        "top_filter_reasons": dict(sorted(reason_counts.items(), key=lambda x: -x[1])[:10]),
    }


# --- Report Generation ---

def format_stats_table(stats: dict, label: str) -> str:
    """Format a stats dict as a markdown table row."""
    if stats["n"] == 0:
        return f"| {label} | 0 | - | - | - | - | - |\n"
    return (
        f"| {label} | {stats['n']} | {stats['wr']}% | "
        f"${stats['total_pnl']:+.2f} | ${stats['avg_win']:.2f} | "
        f"${stats['avg_loss']:.2f} | ${stats['expectancy']:.2f} |\n"
    )


def generate_report(lookback_hours: int = 24) -> str:
    """Generate the full evolution report."""
    since = time.time() - (lookback_hours * 3600)
    since_str = datetime.fromtimestamp(since, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Collect data from all instances
    all_trades = {}
    all_signals = {}
    for inst in INSTANCES:
        all_trades[inst] = query_trades(inst, since)
        all_signals[inst] = query_signals(inst, since)

    # Load market context
    ctx = load_market_context()
    fg = ctx.get("macro", {}).get("fear_greed", "?")
    fg_label = ctx.get("macro", {}).get("fear_greed_label", "?")
    btc_oi_chg = ctx.get("BTC", {}).get("oi_change_pct", 0)
    eth_oi_chg = ctx.get("ETH", {}).get("oi_change_pct", 0)

    lines = []
    lines.append(f"# Evolution Report - {now_str}")
    lines.append(f"\n**Period**: {since_str} to {now_str} ({lookback_hours}h)")
    lines.append(f"**Regime**: F&G={fg} ({fg_label}) | BTC OI {btc_oi_chg:+.3%} | ETH OI {eth_oi_chg:+.3%}")
    lines.append("")

    # Per-instance stats
    for inst in INSTANCES:
        trades = all_trades[inst]
        signals = all_signals[inst]
        overall = compute_trade_stats(trades)

        lines.append(f"## {inst.upper()}")
        lines.append(f"\n**Overall**: {overall['n']} trades, {overall['wr']}% WR, "
                      f"${overall['total_pnl']:+.2f} PnL, "
                      f"${overall['expectancy']:.2f}/trade [{overall['label']}]")
        lines.append("")

        if not trades:
            lines.append("_No trades in period._\n")
            continue

        # By asset
        lines.append("### By Asset")
        lines.append("| Asset | N | WR | PnL | Avg Win | Avg Loss | Exp |")
        lines.append("|-------|---|-----|-----|---------|----------|-----|")
        for asset, s in stats_by_asset(trades).items():
            lines.append(format_stats_table(s, asset).rstrip())
        lines.append("")

        # By entry price
        lines.append("### By Entry Price")
        lines.append("| Bucket | N | WR | PnL | Avg Win | Avg Loss | Exp |")
        lines.append("|--------|---|-----|-----|---------|----------|-----|")
        for bucket, s in stats_by_entry_price(trades).items():
            lines.append(format_stats_table(s, bucket).rstrip())
        lines.append("")

        # By seconds remaining
        lines.append("### By Seconds Remaining")
        lines.append("| Window | N | WR | PnL | Avg Win | Avg Loss | Exp |")
        lines.append("|--------|---|-----|-----|---------|----------|-----|")
        for window, s in stats_by_secs_remaining(trades).items():
            lines.append(format_stats_table(s, window).rstrip())
        lines.append("")

        # By hour
        lines.append("### By Hour (UTC)")
        lines.append("| Hour | N | WR | PnL | Avg Win | Avg Loss | Exp |")
        lines.append("|------|---|-----|-----|---------|----------|-----|")
        for hour, s in stats_by_hour(trades).items():
            lines.append(format_stats_table(s, f"{hour:02d}:00").rstrip())
        lines.append("")

        # Signal filtering
        if signals:
            fs = signal_filter_stats(signals)
            lines.append("### Signal Filtering")
            lines.append(f"- Total signals: {fs['total_signals']}")
            lines.append(f"- Passed: {fs['passed']} | Filtered: {fs['filtered']} ({fs['filter_rate']}%)")
            lines.append(f"- Dry run: {fs['dry_run']}")
            if fs["top_filter_reasons"]:
                lines.append("- Top filter reasons:")
                for reason, count in fs["top_filter_reasons"].items():
                    lines.append(f"  - `{reason}`: {count}")
            lines.append("")

    # Combined stats
    combined_trades = []
    for inst in INSTANCES:
        combined_trades.extend(all_trades[inst])
    if combined_trades:
        combined = compute_trade_stats(combined_trades)
        lines.append("## COMBINED")
        lines.append(f"\n**All instances**: {combined['n']} trades, {combined['wr']}% WR, "
                      f"${combined['total_pnl']:+.2f} PnL, "
                      f"${combined['expectancy']:.2f}/trade [{combined['label']}]")
        lines.append("")

    lines.append(f"\n---\n_Generated by OpenClaw Evolution Engine v1_")
    return "\n".join(lines)


def generate_slack_digest(lookback_hours: int = 24) -> str:
    """Generate a compact Slack message."""
    since = time.time() - (lookback_hours * 3600)

    all_trades = []
    instance_summaries = []
    for inst in INSTANCES:
        trades = query_trades(inst, since)
        all_trades.extend(trades)
        s = compute_trade_stats(trades)
        if s["n"] > 0:
            instance_summaries.append(
                f"*{inst}*: {s['n']} trades, {s['wr']}% WR, ${s['total_pnl']:+.2f}"
            )

    combined = compute_trade_stats(all_trades)
    ctx = load_market_context()
    fg = ctx.get("macro", {}).get("fear_greed", "?")
    fg_label = ctx.get("macro", {}).get("fear_greed_label", "?")

    parts = [f":chart_with_upwards_trend: *Evolution Report* ({lookback_hours}h)"]
    parts.append(f"Regime: F&G={fg} ({fg_label})")
    parts.append("")
    for s in instance_summaries:
        parts.append(s)
    if combined["n"] > 0:
        parts.append(f"\n*Combined*: {combined['n']} trades, {combined['wr']}% WR, "
                      f"${combined['total_pnl']:+.2f} [{combined['label']}]")
    if not instance_summaries:
        parts.append("_No trades in period._")

    return "\n".join(parts)


def post_to_slack(text: str):
    """Post message to Slack channel."""
    if not SLACK_BOT_TOKEN or not SLACK_CHANNEL_ID:
        _log("Slack not configured, skipping post")
        return
    payload = json.dumps({
        "channel": SLACK_CHANNEL_ID,
        "text": text,
    }).encode()
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=payload,
        headers={
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            if not result.get("ok"):
                _log(f"Slack error: {result.get('error', 'unknown')}")
            else:
                _log("Slack digest posted")
    except Exception as e:
        _log(f"Slack post failed: {e}")


# --- Commands ---

def cmd_daily(args):
    """Full daily evolution report."""
    hours = args.hours or LOOKBACK_HOURS
    _log(f"Generating daily evolution report ({hours}h lookback)")

    # Ensure output dir
    os.makedirs(EVOLUTION_DIR, exist_ok=True)

    # Generate report
    report = generate_report(hours)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_path = os.path.join(EVOLUTION_DIR, f"evolution_report_{date_str}.md")
    with open(report_path, "w") as f:
        f.write(report)
    _log(f"Report written to {report_path}")

    # Post Slack digest
    digest = generate_slack_digest(hours)
    post_to_slack(digest)

    # Print to stdout
    print(report)


def cmd_stats(args):
    """Quick stats to stdout."""
    hours = args.hours or LOOKBACK_HOURS
    since = time.time() - (hours * 3600)

    for inst in INSTANCES:
        trades = query_trades(inst, since)
        s = compute_trade_stats(trades)
        print(f"\n{inst.upper()} ({hours}h)")
        if s["n"] == 0:
            print("  No trades")
            continue
        print(f"  {s['n']} trades | {s['wr']}% WR | ${s['total_pnl']:+.2f} PnL | "
              f"${s['expectancy']:.2f}/trade [{s['label']}]")

        by_asset = stats_by_asset(trades)
        for asset, a in by_asset.items():
            print(f"  {asset}: {a['n']}tr {a['wr']}% WR ${a['total_pnl']:+.2f}")


def cmd_changelog(args):
    """Show strategy changelog."""
    if not os.path.exists(CHANGELOG_PATH):
        print("No changelog yet.")
        return
    with open(CHANGELOG_PATH) as f:
        print(f.read())


def cmd_recommend(args):
    """Run backtest engine to generate config recommendations."""
    hours = args.hours or 168  # Default 7 days for recommendations
    _log(f"Running backtest recommendation engine ({hours}h lookback)")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    backtest_script = os.path.join(script_dir, "backtest_engine.py")

    if not os.path.exists(backtest_script):
        print("ERROR: backtest_engine.py not found at", backtest_script)
        sys.exit(1)

    venv_python = os.environ.get("VENV_PYTHON", "/opt/lagbot/venv/bin/python3")
    result = subprocess.run(
        [venv_python, backtest_script, "recommend", "--hours", str(hours)],
        capture_output=True, text=True, timeout=120,
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    # Check for generated recommendations
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rec_path = os.path.join(EVOLUTION_DIR, f"config_recommendation_{date_str}.json")
    if os.path.exists(rec_path):
        with open(rec_path) as f:
            recs = json.load(f)
        n_recs = len(recs.get("recommendations", []))
        if n_recs > 0:
            # Post to Slack
            rec_lines = [":gear: *Config Recommendations*"]
            for r in recs["recommendations"]:
                rec_lines.append(
                    f"  {r['instance']}: `{r['param']}` {r['current']} -> {r['proposed']} (+${r['pnl_delta']:.2f})"
                )
            rec_lines.append(f"\nRun `/evolve approve` to review and apply.")
            post_to_slack("\n".join(rec_lines))


def cmd_twitter(args):
    """Run Twitter intelligence scan."""
    _log("Running Twitter intelligence scan")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    twitter_script = os.path.join(script_dir, "twitter_intel.py")

    if not os.path.exists(twitter_script):
        print("ERROR: twitter_intel.py not found at", twitter_script)
        sys.exit(1)

    venv_python = os.environ.get("VENV_PYTHON", "/opt/lagbot/venv/bin/python3")
    result = subprocess.run(
        [venv_python, twitter_script, "scan"],
        capture_output=True, text=True, timeout=120,
        env={**os.environ},
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)


def cmd_full(args):
    """Full autonomous evolution cycle: stats + backtest + twitter + recommend."""
    hours = args.hours or LOOKBACK_HOURS
    _log(f"=== FULL EVOLUTION CYCLE ({hours}h) ===")

    # Step 1: Daily report (stats)
    _log("Step 1/4: Generating daily stats report")
    os.makedirs(EVOLUTION_DIR, exist_ok=True)
    report = generate_report(hours)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_path = os.path.join(EVOLUTION_DIR, f"evolution_report_{date_str}.md")
    with open(report_path, "w") as f:
        f.write(report)
    _log(f"Report: {report_path}")

    # Step 2: Backtest recommendations (7-day lookback for recommendations)
    _log("Step 2/4: Running backtest recommendation engine")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    venv_python = os.environ.get("VENV_PYTHON", "/opt/lagbot/venv/bin/python3")
    backtest_script = os.path.join(script_dir, "backtest_engine.py")

    rec_output = ""
    if os.path.exists(backtest_script):
        try:
            result = subprocess.run(
                [venv_python, backtest_script, "recommend", "--hours", "168"],
                capture_output=True, text=True, timeout=120,
            )
            rec_output = result.stdout
            _log("Backtest complete")
        except Exception as e:
            _log(f"Backtest failed: {e}")
            rec_output = f"Backtest error: {e}"
    else:
        _log("backtest_engine.py not deployed yet, skipping")

    # Step 3: Social intelligence scan
    _log("Step 3/4: Running social intelligence scan")
    twitter_script = os.path.join(script_dir, "twitter_intel.py")
    twitter_output = ""
    if os.path.exists(twitter_script):
        try:
            result = subprocess.run(
                [venv_python, twitter_script, "scan"],
                capture_output=True, text=True, timeout=120,
                env={**os.environ},
            )
            twitter_output = result.stdout
            _log("Social intel scan complete")
        except Exception as e:
            _log(f"Social intel scan failed: {e}")
            twitter_output = f"Social intel error: {e}"
    else:
        _log("twitter_intel.py not deployed yet, skipping")

    # Step 4: Post combined Slack digest
    _log("Step 4/4: Posting combined Slack digest")
    digest = generate_slack_digest(hours)

    # Append recommendation summary if any
    rec_path = os.path.join(EVOLUTION_DIR, f"config_recommendation_{date_str}.json")
    if os.path.exists(rec_path):
        try:
            with open(rec_path) as f:
                recs = json.load(f)
            if recs.get("recommendations"):
                digest += "\n\n:gear: *Config Recommendations:*"
                for r in recs["recommendations"]:
                    digest += f"\n  `{r['param']}` {r['current']} -> {r['proposed']} (+${r['pnl_delta']:.2f}) [{r['instance']}]"
                digest += "\nRun `/evolve approve` to review."
        except Exception:
            pass

    post_to_slack(digest)

    # Print full output
    print(report)
    if rec_output:
        print("\n" + "="*60)
        print("BACKTEST RECOMMENDATIONS")
        print("="*60)
        print(rec_output)

    _log("=== FULL EVOLUTION CYCLE COMPLETE ===")


def main():
    parser = argparse.ArgumentParser(description="OpenClaw Evolution Engine v2")
    sub = parser.add_subparsers(dest="command")

    p_daily = sub.add_parser("daily", help="Daily stats report + Slack")
    p_daily.add_argument("--hours", type=int, default=0)
    p_daily.set_defaults(func=cmd_daily)

    p_stats = sub.add_parser("stats", help="Quick stats to stdout")
    p_stats.add_argument("--hours", type=int, default=0)
    p_stats.set_defaults(func=cmd_stats)

    p_cl = sub.add_parser("changelog", help="Show strategy changelog")
    p_cl.set_defaults(func=cmd_changelog)

    p_rec = sub.add_parser("recommend", help="Backtest-driven config recommendations")
    p_rec.add_argument("--hours", type=int, default=0)
    p_rec.set_defaults(func=cmd_recommend)

    p_tw = sub.add_parser("twitter", help="Run Twitter intelligence scan")
    p_tw.set_defaults(func=cmd_twitter)

    p_full = sub.add_parser("full", help="Full autonomous cycle: stats + backtest + twitter")
    p_full.add_argument("--hours", type=int, default=0)
    p_full.set_defaults(func=cmd_full)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
