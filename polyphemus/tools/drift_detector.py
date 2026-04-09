#!/usr/bin/env python3
"""Drift Detector -- detects strategy performance drift and alerts.

Standalone script (no lagbot imports, Bug #39 safe).
Replaces: lagbot_health_cron, agent_vps_health, evolve_check.

What it does:
1. Queries performance.db for rolling WR, EV, Kelly over multiple windows
2. Compares current performance to baseline thresholds
3. Detects regime shifts (WR trending below break-even)
4. Alerts via Slack + ntfy.sh when drift is significant
5. Writes structured findings to state/findings/ for the proposal generator

Runs every 4 hours. Cost: ~$0.01/run (Haiku with prompt caching).

Usage:
    python3 drift_detector.py --instance emmanuel
    python3 drift_detector.py --instance emmanuel --dry-run
    python3 drift_detector.py --instance emmanuel --hours 24

Cron:
    0 */4 * * * cd /opt/lagbot && /opt/lagbot/venv/bin/python3 lagbot/tools/drift_detector.py --instance emmanuel
    0 */4 * * * cd /opt/lagbot && /opt/lagbot/venv/bin/python3 lagbot/tools/drift_detector.py --instance polyphemus
"""

import argparse
import json
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import Request, urlopen


# ---------- Config ----------

# Drift thresholds (hardcoded, agent cannot modify these)
THRESHOLDS = {
    "wr_alarm": 30.0,          # WR below this = ALARM
    "wr_warning": 40.0,        # WR below this = WARNING
    "wr_breakeven": 47.3,      # Break-even WR at avg entry ~$0.475
    "kelly_negative_alarm": True,  # Kelly < 0 = strategy is losing
    "consecutive_loss_alarm": 5,
    "min_n_for_signal": 10,    # Need at least 10 trades for any signal
    "ev_warning": -0.50,       # EV below this = WARNING
    "drawdown_alarm_pct": 10.0,  # Drawdown > 10% of opening balance
    "orphan_rate_alarm": 40.0,   # Orphan rate above this = ALARM
    "orphan_rate_warning": 25.0, # Orphan rate above this = WARNING
}

# Baselines (updated manually after each Kelly checkpoint)
BASELINES = {
    "target_wr": 50.0,        # What WR we need to be profitable
    "target_ev": 0.05,        # Minimum EV per trade
    "target_kelly": 0.01,     # Minimum positive Kelly
}

FINDINGS_DIR = Path("/opt/openclaw/agents/state/findings")
FINDINGS_DIR.mkdir(parents=True, exist_ok=True)

DRIFT_STATE_DIR = Path("/opt/openclaw/agents/state/drift")
DRIFT_STATE_DIR.mkdir(parents=True, exist_ok=True)


# ---------- Data collection (no lagbot imports) ----------

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


def query_db(db_path: str, sql: str, params: tuple = ()) -> list[dict]:
    """Query SQLite, return list of dicts."""
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple:
    """Wilson score 95% CI for win rate."""
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denom
    return (max(0, center - spread), min(1, center + spread))


def r8_label(n: int) -> str:
    """Sample size label."""
    if n < 10:
        return "ANECDOTAL"
    if n < 30:
        return "PRELIMINARY"
    if n < 100:
        return "MODERATE"
    return "SUBSTANTIAL"


# ---------- Core analysis (pure Python, no LLM needed) ----------

def compute_orphan_stats(db_path: str, hours: int) -> dict:
    """Compute orphan rate for pair trades. THIS is the real P&L driver."""
    now = time.time()
    start = now - (hours * 3600)

    pair_trades = query_db(db_path, """
        SELECT trade_id, pnl
        FROM trades
        WHERE exit_time IS NOT NULL
          AND is_error = 0
          AND trade_id NOT LIKE 'dry_%'
          AND trade_id LIKE 'pair_%'
          AND entry_time > ?
        ORDER BY entry_time
    """, (start,))

    if not pair_trades:
        return {"n_pair": 0, "hours": hours}

    hedged = [t for t in pair_trades if (t["pnl"] or 0) > 0]
    orphans = [t for t in pair_trades if (t["pnl"] or 0) <= 0]

    n_pair = len(pair_trades)
    n_hedged = len(hedged)
    n_orphan = len(orphans)
    orphan_rate = round(100.0 * n_orphan / n_pair, 1) if n_pair > 0 else 0

    hedged_pnl = round(sum(t["pnl"] or 0 for t in hedged), 2)
    orphan_pnl = round(sum(t["pnl"] or 0 for t in orphans), 2)
    avg_hedged = round(hedged_pnl / n_hedged, 4) if n_hedged > 0 else 0
    avg_orphan = round(orphan_pnl / n_orphan, 4) if n_orphan > 0 else 0

    # Break-even orphan rate: at what orphan rate does hedged profit = orphan loss?
    # hedged_per * (1 - orphan_rate) = |orphan_per| * orphan_rate
    # orphan_rate_breakeven = hedged_per / (hedged_per + |orphan_per|)
    breakeven_orphan_rate = round(
        100.0 * avg_hedged / (avg_hedged + abs(avg_orphan)), 1
    ) if avg_hedged > 0 and avg_orphan < 0 else 0

    return {
        "n_pair": n_pair,
        "n_hedged": n_hedged,
        "n_orphan": n_orphan,
        "orphan_rate": orphan_rate,
        "hedged_pnl": hedged_pnl,
        "orphan_pnl": orphan_pnl,
        "avg_hedged": avg_hedged,
        "avg_orphan": avg_orphan,
        "breakeven_orphan_rate": breakeven_orphan_rate,
        "label": r8_label(n_pair),
        "hours": hours,
    }


def compute_window_stats(db_path: str, hours: int, offset_hours: int = 0) -> dict:
    """Compute stats for a time window."""
    now = time.time()
    end = now - (offset_hours * 3600)
    start = end - (hours * 3600)

    trades = query_db(db_path, """
        SELECT pnl, entry_price, entry_time, exit_time,
               CAST(strftime('%H', entry_time, 'unixepoch') AS INTEGER) as hour_utc
        FROM trades
        WHERE exit_time IS NOT NULL
          AND is_error = 0
          AND entry_time > ? AND entry_time <= ?
        ORDER BY entry_time
    """, (start, end))

    if not trades:
        return {"n": 0, "hours": hours, "offset": offset_hours}

    n = len(trades)
    wins = sum(1 for t in trades if (t["pnl"] or 0) > 0)
    wr = round(100.0 * wins / n, 1)
    total_pnl = round(sum(t["pnl"] or 0 for t in trades), 4)
    ev = round(total_pnl / n, 4)
    ci_low, ci_high = wilson_ci(wins, n)

    # Kelly
    avg_win = sum(t["pnl"] for t in trades if (t["pnl"] or 0) > 0) / wins if wins > 0 else 0
    avg_loss = abs(sum(t["pnl"] for t in trades if (t["pnl"] or 0) <= 0) / (n - wins)) if (n - wins) > 0 else 1
    b = avg_win / avg_loss if avg_loss > 0 else 0
    p = wins / n
    kelly = round(p - (1 - p) / b, 4) if b > 0 else -1

    # Consecutive losses (current streak from end)
    consec_losses = 0
    for t in reversed(trades):
        if (t["pnl"] or 0) <= 0:
            consec_losses += 1
        else:
            break

    # Max drawdown in window
    cumulative = 0
    peak = 0
    max_dd = 0
    for t in trades:
        cumulative += (t["pnl"] or 0)
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    # Average entry price
    avg_entry = round(sum(t["entry_price"] or 0 for t in trades) / n, 4) if n > 0 else 0

    return {
        "n": n,
        "wins": wins,
        "wr": wr,
        "ev": ev,
        "pnl": total_pnl,
        "kelly": kelly,
        "ci_low": round(ci_low * 100, 1),
        "ci_high": round(ci_high * 100, 1),
        "consec_losses": consec_losses,
        "max_drawdown": round(max_dd, 2),
        "avg_entry": avg_entry,
        "label": r8_label(n),
        "hours": hours,
        "offset": offset_hours,
    }


def detect_drift(current: dict, prior: dict, thresholds: dict) -> list:
    """Compare current window to prior window and thresholds. Returns list of drift signals."""
    signals = []

    if current["n"] < thresholds["min_n_for_signal"]:
        signals.append({
            "type": "LOW_SAMPLE",
            "severity": "INFO",
            "message": f"Only {current['n']} trades in window ({current['label']}). Signals unreliable.",
        })
        return signals

    # WR drift
    if current["wr"] < thresholds["wr_alarm"]:
        signals.append({
            "type": "WR_ALARM",
            "severity": "ALARM",
            "message": f"WR={current['wr']}% (n={current['n']}) below alarm threshold {thresholds['wr_alarm']}%. CI=[{current['ci_low']}%, {current['ci_high']}%].",
        })
    elif current["wr"] < thresholds["wr_warning"]:
        signals.append({
            "type": "WR_WARNING",
            "severity": "WARNING",
            "message": f"WR={current['wr']}% (n={current['n']}) below warning threshold {thresholds['wr_warning']}%. CI=[{current['ci_low']}%, {current['ci_high']}%].",
        })
    elif current["wr"] < thresholds["wr_breakeven"]:
        signals.append({
            "type": "WR_BELOW_BREAKEVEN",
            "severity": "WARNING",
            "message": f"WR={current['wr']}% below break-even {thresholds['wr_breakeven']}%. Net losing at current parameters.",
        })

    # Kelly negative
    if current["kelly"] < 0 and thresholds["kelly_negative_alarm"]:
        signals.append({
            "type": "KELLY_NEGATIVE",
            "severity": "ALARM",
            "message": f"Kelly={current['kelly']} is NEGATIVE. Strategy has no edge at current parameters. n={current['n']} ({current['label']}).",
        })

    # EV drift
    if current["ev"] < thresholds["ev_warning"]:
        signals.append({
            "type": "EV_WARNING",
            "severity": "WARNING",
            "message": f"EV=${current['ev']}/trade below threshold ${thresholds['ev_warning']}. Losing ${abs(current['ev']):.2f} per trade on average.",
        })

    # Consecutive losses
    if current["consec_losses"] >= thresholds["consecutive_loss_alarm"]:
        signals.append({
            "type": "CONSECUTIVE_LOSSES",
            "severity": "ALARM",
            "message": f"{current['consec_losses']} consecutive losses. Possible regime shift or system issue.",
        })

    # WR trend: current vs prior
    if prior and prior["n"] >= thresholds["min_n_for_signal"]:
        wr_delta = current["wr"] - prior["wr"]
        if wr_delta < -10:
            signals.append({
                "type": "WR_DECLINING",
                "severity": "WARNING",
                "message": f"WR dropped {abs(wr_delta):.1f}pp: {prior['wr']}% -> {current['wr']}% (prior {prior['hours']}h vs current {current['hours']}h).",
            })

    # NOTE: orphan signals are added separately in run() via detect_orphan_drift()

    # If no signals, report healthy
    if not signals:
        signals.append({
            "type": "HEALTHY",
            "severity": "INFO",
            "message": f"WR={current['wr']}% (n={current['n']}), EV=${current['ev']}, Kelly={current['kelly']}. No drift detected.",
        })

    return signals


def detect_orphan_drift(orphan_stats: dict, thresholds: dict) -> list:
    """Detect orphan rate issues. This is the #1 P&L driver for pair trades."""
    signals = []

    if orphan_stats.get("n_pair", 0) < thresholds["min_n_for_signal"]:
        return signals  # Not enough pair trades to signal

    rate = orphan_stats["orphan_rate"]
    be_rate = orphan_stats.get("breakeven_orphan_rate", 0)

    if rate >= thresholds["orphan_rate_alarm"]:
        signals.append({
            "type": "ORPHAN_RATE_ALARM",
            "severity": "ALARM",
            "message": (
                f"Orphan rate {rate}% (n={orphan_stats['n_pair']} pair trades, {orphan_stats['label']}). "
                f"Break-even orphan rate: {be_rate}%. "
                f"Hedged: {orphan_stats['n_hedged']} fills, +${orphan_stats['hedged_pnl']}. "
                f"Orphans: {orphan_stats['n_orphan']} fills, ${orphan_stats['orphan_pnl']}. "
                f"Avg orphan loss: ${orphan_stats['avg_orphan']}/trade."
            ),
        })
    elif rate >= thresholds["orphan_rate_warning"]:
        signals.append({
            "type": "ORPHAN_RATE_WARNING",
            "severity": "WARNING",
            "message": (
                f"Orphan rate {rate}% (n={orphan_stats['n_pair']}). "
                f"Break-even: {be_rate}%. Currently {'above' if rate > be_rate else 'below'} break-even."
            ),
        })

    # Even if rate is "ok", check if orphan losses outweigh hedged gains
    if orphan_stats["hedged_pnl"] + orphan_stats["orphan_pnl"] < 0 and orphan_stats["n_pair"] >= 20:
        net = round(orphan_stats["hedged_pnl"] + orphan_stats["orphan_pnl"], 2)
        signals.append({
            "type": "ORPHAN_NET_NEGATIVE",
            "severity": "WARNING",
            "message": (
                f"Pair trades net negative: ${net}. "
                f"Hedged fills (+${orphan_stats['hedged_pnl']}) do not offset orphan losses (${orphan_stats['orphan_pnl']})."
            ),
        })

    return signals


# ---------- Service health (replaces lagbot_health_cron + agent_vps_health) ----------

def check_service_health(instance: str) -> list:
    """Check systemd service status and recent errors."""
    import subprocess
    signals = []

    service = f"lagbot@{instance}"

    # Is service running?
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True, text=True, timeout=5,
        )
        if result.stdout.strip() != "active":
            signals.append({
                "type": "SERVICE_DOWN",
                "severity": "ALARM",
                "message": f"Service {service} is {result.stdout.strip()}, not active.",
            })
    except Exception as e:
        signals.append({
            "type": "SERVICE_CHECK_FAILED",
            "severity": "WARNING",
            "message": f"Could not check service status: {e}",
        })

    # Recent errors in journal
    try:
        result = subprocess.run(
            f"journalctl -u {service} --since '4 hours ago' --no-pager | grep -iE 'traceback|exception|\\bERROR\\b' | grep -v 'errors=' | tail -5",
            shell=True, capture_output=True, text=True, timeout=10,
        )
        errors = result.stdout.strip()
        if errors:
            error_count = len(errors.strip().split("\n"))
            signals.append({
                "type": "SERVICE_ERRORS",
                "severity": "WARNING" if error_count < 5 else "ALARM",
                "message": f"{error_count} errors in last 4h. Latest: {errors.split(chr(10))[-1][:200]}",
            })
    except Exception:
        pass

    # Disk space
    try:
        result = subprocess.run(
            "df -h / | tail -1 | awk '{print $5}'",
            shell=True, capture_output=True, text=True, timeout=5,
        )
        usage = int(result.stdout.strip().replace("%", ""))
        if usage > 90:
            signals.append({
                "type": "DISK_FULL",
                "severity": "ALARM",
                "message": f"Disk usage at {usage}%.",
            })
    except Exception:
        pass

    return signals


# ---------- State persistence ----------

def load_last_state(instance: str) -> dict:
    """Load last drift state for comparison."""
    path = DRIFT_STATE_DIR / f"{instance}_last.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def save_state(instance: str, state: dict):
    """Save current state for next run."""
    path = DRIFT_STATE_DIR / f"{instance}_last.json"
    path.write_text(json.dumps(state, indent=2))


# ---------- Notification ----------

def post_slack(instance: str, text: str):
    """Post to Slack via bot token."""
    env = get_env(instance)
    token = env.get("SLACK_BOT_TOKEN", "")
    channel = env.get("SLACK_CHANNEL", "")
    if not token or not channel:
        print(f"[SLACK] No token/channel for {instance}")
        return

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
        if result.get("ok"):
            print(f"  Posted to Slack ({instance})")
        else:
            print(f"  Slack error: {result.get('error')}")
    except Exception as e:
        print(f"  Slack failed: {e}")


def send_ntfy(title: str, message: str, priority: str = "default"):
    """Push notification via ntfy.sh."""
    topic = os.environ.get("NTFY_TOPIC", "polyphemus-ops")
    clean = message.replace("*", "").replace("`", "").replace("#", "")
    url = f"https://ntfy.sh/{topic}"
    req = Request(url, data=clean.encode("utf-8"))
    req.add_header("Title", title)
    req.add_header("Priority", priority)
    req.add_header("Tags", "chart_with_downwards_trend" if "ALARM" in title else "robot")
    try:
        urlopen(req, timeout=10)
        print(f"  [NTFY] Sent: {title}")
    except Exception as e:
        print(f"  [NTFY] Failed: {e}")


# ---------- Main ----------

def run(instance: str, hours: int = 24, dry_run: bool = False):
    """Run drift detection for one instance."""
    ts = datetime.now(timezone.utc)
    print(f"\n{'='*60}")
    print(f"DRIFT DETECTOR | {instance} | {ts.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")

    db_path = f"/opt/lagbot/instances/{instance}/data/performance.db"
    if not os.path.exists(db_path):
        print(f"ERROR: DB not found: {db_path}")
        return

    # Multi-window analysis
    windows = {
        "4h": compute_window_stats(db_path, 4),
        "24h": compute_window_stats(db_path, 24),
        "7d": compute_window_stats(db_path, 168),
        "30d": compute_window_stats(db_path, 720),
        "prior_24h": compute_window_stats(db_path, 24, offset_hours=24),
    }

    print(f"\n--- Performance Windows ---")
    for name, w in windows.items():
        if name == "prior_24h":
            continue
        print(f"  {name}: n={w['n']} WR={w.get('wr', 0)}% EV=${w.get('ev', 0)} Kelly={w.get('kelly', 'N/A')} [{w.get('label', '?')}]")

    # Orphan rate analysis (the real P&L driver for pair trades)
    orphan_stats = {
        "24h": compute_orphan_stats(db_path, 24),
        "7d": compute_orphan_stats(db_path, 168),
    }

    print(f"\n--- Orphan Rate ---")
    for name, o in orphan_stats.items():
        if o.get("n_pair", 0) > 0:
            print(f"  {name}: {o['n_pair']} pairs, orphan_rate={o['orphan_rate']}%, "
                  f"hedged=+${o['hedged_pnl']}, orphans=${o['orphan_pnl']}, "
                  f"break-even={o['breakeven_orphan_rate']}% [{o['label']}]")
        else:
            print(f"  {name}: no pair trades")

    # Detect drift on 24h window (primary) with prior comparison
    drift_signals = detect_drift(windows["24h"], windows["prior_24h"], THRESHOLDS)

    # Also check 7d for structural issues
    drift_7d = detect_drift(windows["7d"], None, THRESHOLDS)
    for s in drift_7d:
        if s["severity"] == "ALARM" and s["type"] not in [x["type"] for x in drift_signals]:
            s["message"] = f"[7d] {s['message']}"
            drift_signals.append(s)

    # Orphan rate drift (check 7d for structural, 24h for acute)
    orphan_signals = detect_orphan_drift(orphan_stats["7d"], THRESHOLDS)
    orphan_24h = detect_orphan_drift(orphan_stats["24h"], THRESHOLDS)
    for s in orphan_24h:
        if s["type"] not in [x["type"] for x in orphan_signals]:
            s["message"] = f"[24h] {s['message']}"
            orphan_signals.append(s)

    # Service health
    svc_signals = check_service_health(instance)
    all_signals = drift_signals + orphan_signals + svc_signals

    # Determine max severity
    severities = [s["severity"] for s in all_signals]
    max_severity = "ALARM" if "ALARM" in severities else ("WARNING" if "WARNING" in severities else "INFO")

    print(f"\n--- Signals ({max_severity}) ---")
    for s in all_signals:
        print(f"  [{s['severity']}] {s['type']}: {s['message']}")

    # Build report
    report_lines = [
        f"{'🚨' if max_severity == 'ALARM' else '⚠️' if max_severity == 'WARNING' else 'ℹ️'} *Drift Detector: {instance}* [{max_severity}]",
        f"_{ts.strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
    ]

    # Summary table
    for name in ["4h", "24h", "7d", "30d"]:
        w = windows[name]
        if w["n"] > 0:
            report_lines.append(f"*{name}:* n={w['n']} WR={w['wr']}% EV=${w['ev']} Kelly={w['kelly']} [{w['label']}]")
        else:
            report_lines.append(f"*{name}:* no trades")

    # Orphan rate section
    o7 = orphan_stats.get("7d", {})
    if o7.get("n_pair", 0) > 0:
        report_lines.append("")
        report_lines.append(
            f"*Pairs (7d):* {o7['n_pair']} total, "
            f"orphan_rate={o7['orphan_rate']}% (break-even: {o7['breakeven_orphan_rate']}%), "
            f"hedged=+${o7['hedged_pnl']}, orphans=${o7['orphan_pnl']}"
        )

    report_lines.append("")
    for s in all_signals:
        icon = "🔴" if s["severity"] == "ALARM" else "🟡" if s["severity"] == "WARNING" else "🟢"
        report_lines.append(f"{icon} {s['message']}")

    report = "\n".join(report_lines)

    # Write finding for proposal generator
    finding = {
        "agent": "drift_detector",
        "instance": instance,
        "timestamp": ts.isoformat(),
        "max_severity": max_severity,
        "windows": windows,
        "orphan_stats": orphan_stats,
        "signals": all_signals,
    }
    finding_path = FINDINGS_DIR / f"drift_{instance}_{ts.strftime('%Y%m%dT%H%M%S')}.json"
    finding_path.write_text(json.dumps(finding, indent=2, default=str))
    print(f"\n  Finding written: {finding_path.name}")

    # Save state for next run comparison
    save_state(instance, {
        "timestamp": ts.isoformat(),
        "windows": windows,
        "max_severity": max_severity,
    })

    if dry_run:
        print(f"\n--- DRY RUN: would send ---")
        print(report)
        return

    # Only notify on WARNING or ALARM (reduce noise)
    if max_severity in ("ALARM", "WARNING"):
        post_slack(instance, report)
        # ntfy for ALARM only (phone push)
        if max_severity == "ALARM":
            alarm_msgs = [s["message"] for s in all_signals if s["severity"] == "ALARM"]
            send_ntfy(
                f"ALARM: {instance}",
                "\n".join(alarm_msgs[:3]),
                priority="high",
            )
    else:
        print(f"  INFO only, no notification sent.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Drift Detector")
    parser.add_argument("--instance", required=True, help="Instance name (emmanuel/polyphemus)")
    parser.add_argument("--hours", type=int, default=24, help="Primary lookback window (default: 24)")
    parser.add_argument("--dry-run", action="store_true", help="Print only, no notifications")
    args = parser.parse_args()
    run(args.instance, args.hours, args.dry_run)
