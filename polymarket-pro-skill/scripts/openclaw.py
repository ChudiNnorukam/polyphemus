#!/usr/bin/env python3
"""OpenClaw — Autonomous Polymarket Trading Orchestrator.

Schedules and manages multiple trading strategies:
  - Weather edge (weather_edge.py)
  - Sports arb (sports_arb.py)

Runs as a systemd service. Handles scheduling, bankroll allocation,
unified P&L tracking, and auto-tuning based on results.

Usage:
    python3 openclaw.py start            # Run orchestrator (foreground)
    python3 openclaw.py status           # Show strategy status
    python3 openclaw.py report           # Show unified P&L report
    python3 openclaw.py run-weather      # Manual weather scan+trade
    python3 openclaw.py run-sports       # Manual sports scan+trade
"""

import argparse
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# --- Config ---

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHON = os.environ.get("OPENCLAW_PYTHON", "/opt/lagbot/venv/bin/python3")
LOG_DIR = os.environ.get("OPENCLAW_LOG_DIR", "/opt/openclaw/logs")
DATA_DIR = os.environ.get("OPENCLAW_DATA_DIR", "/opt/openclaw/data")
STATE_FILE = os.path.join(DATA_DIR, "openclaw_state.json")

# Schedule (hours in CET/UTC+1)
# Weather: 4x daily targeting EST windows (5AM, 9AM, 1PM, 5PM EST)
WEATHER_HOURS_CET = [11, 15, 19, 23]
# Weather resolve: once daily at 07:00 CET
WEATHER_RESOLVE_HOUR_CET = 7
# Sports: every 15 min during NBA game hours (18:00-01:00 EST = 00:00-07:00 CET next day)
SPORTS_HOURS_CET = list(range(0, 8))  # 00:00 - 07:00 CET = 18:00-01:00 EST
SPORTS_INTERVAL_MINS = 15
# Sports resolve: daily at 08:00 CET
SPORTS_RESOLVE_HOUR_CET = 8
# Report: daily at 09:00 CET
REPORT_HOUR_CET = 9

# Bankroll allocation
BANKROLL_ALLOC = {
    "weather": float(os.environ.get("OPENCLAW_WEATHER_PCT", "0.15")),
    "sports": float(os.environ.get("OPENCLAW_SPORTS_PCT", "0.25")),
}

# Auto-tune: adjust thresholds after N resolved trades
TUNE_MIN_TRADES = 30
TUNE_TARGET_WR = 0.55  # Minimum win rate to keep current settings

# --- Logging ---

def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, "openclaw.log")

    logger = logging.getLogger("openclaw")
    logger.setLevel(logging.INFO)

    # File handler (rotating would be better, but keeping simple)
    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.INFO)

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                           datefmt="%Y-%m-%d %H:%M:%S")
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


# --- State Management ---

def load_state():
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "last_weather_trade": None,
        "last_weather_resolve": None,
        "last_sports_trade": None,
        "last_sports_resolve": None,
        "last_report": None,
        "total_runs": {"weather_trade": 0, "weather_resolve": 0,
                       "sports_trade": 0, "sports_resolve": 0},
        "errors": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }


def save_state(state):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# --- Strategy Runners ---

def run_strategy(script_name, command, logger, timeout=120):
    """Run a strategy script as a subprocess."""
    script_path = os.path.join(BASE_DIR, script_name)
    if not os.path.exists(script_path):
        logger.error(f"Script not found: {script_path}")
        return False, "script_not_found"

    cmd = [VENV_PYTHON, script_path, command]
    logger.info(f"Running: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=BASE_DIR,
        )
        output = result.stdout.strip()
        if output:
            for line in output.split("\n"):
                logger.info(f"  [{script_name}] {line}")
        if result.stderr.strip():
            for line in result.stderr.strip().split("\n"):
                logger.warning(f"  [{script_name} ERR] {line}")
        if result.returncode != 0:
            logger.error(f"{script_name} exited with code {result.returncode}")
            return False, f"exit_code_{result.returncode}"
        return True, "ok"
    except subprocess.TimeoutExpired:
        logger.error(f"{script_name} timed out after {timeout}s")
        return False, "timeout"
    except Exception as e:
        logger.error(f"{script_name} failed: {e}")
        return False, str(e)


# --- Auto-Tuner ---

def check_auto_tune(logger):
    """Check if strategies need threshold adjustment based on performance."""
    adjustments = {}

    # Weather bot auto-tune
    weather_db = os.path.join(BASE_DIR, "weather_trades.db")
    if os.path.exists(weather_db):
        try:
            conn = sqlite3.connect(weather_db)
            row = conn.execute(
                "SELECT COUNT(*), SUM(CASE WHEN status='won' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN status='lost' THEN 1 ELSE 0 END) "
                "FROM trades WHERE status IN ('won', 'lost')"
            ).fetchone()
            conn.close()
            total, won, lost = row
            if total and total >= TUNE_MIN_TRADES:
                wr = won / total
                if wr < TUNE_TARGET_WR:
                    adjustments["weather"] = {
                        "action": "raise_threshold",
                        "current_wr": wr,
                        "trades": total,
                        "note": f"WR {wr:.1%} < {TUNE_TARGET_WR:.0%} target. "
                                f"Consider raising WEATHER_EDGE_HIGH from 3% to 5%.",
                    }
                    logger.warning(f"Auto-tune: Weather WR={wr:.1%} below target. "
                                  f"Recommend raising edge thresholds.")
                else:
                    logger.info(f"Auto-tune: Weather WR={wr:.1%} healthy ({total} trades)")
        except Exception as e:
            logger.warning(f"Auto-tune weather check failed: {e}")

    # Sports bot auto-tune
    sports_db = os.path.join(BASE_DIR, "sports_trades.db")
    if os.path.exists(sports_db):
        try:
            conn = sqlite3.connect(sports_db)
            row = conn.execute(
                "SELECT COUNT(*), SUM(CASE WHEN status='won' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN status='lost' THEN 1 ELSE 0 END) "
                "FROM trades WHERE status IN ('won', 'lost')"
            ).fetchone()
            conn.close()
            total, won, lost = row
            if total and total >= TUNE_MIN_TRADES:
                wr = won / total
                if wr < TUNE_TARGET_WR:
                    adjustments["sports"] = {
                        "action": "raise_threshold",
                        "current_wr": wr,
                        "trades": total,
                        "note": f"WR {wr:.1%} < {TUNE_TARGET_WR:.0%} target. "
                                f"Consider raising SPORTS_EDGE_THRESHOLD from 5% to 7%.",
                    }
                    logger.warning(f"Auto-tune: Sports WR={wr:.1%} below target. "
                                  f"Recommend raising edge threshold.")
                else:
                    logger.info(f"Auto-tune: Sports WR={wr:.1%} healthy ({total} trades)")
        except Exception as e:
            logger.warning(f"Auto-tune sports check failed: {e}")

    return adjustments


# --- P&L Report ---

def generate_report(logger=None):
    """Generate unified P&L report across all strategies."""
    report_lines = []
    report_lines.append(f"\n{'='*60}")
    report_lines.append(f"  OpenClaw Daily Report — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    report_lines.append(f"{'='*60}")

    total_invested = 0
    total_pnl = 0
    total_trades = 0
    total_won = 0
    total_lost = 0
    total_pending = 0

    for strategy, db_name in [("Weather", "weather_trades.db"), ("Sports", "sports_trades.db")]:
        db_path = os.path.join(BASE_DIR, db_name)
        if not os.path.exists(db_path):
            report_lines.append(f"\n  {strategy}: No database found")
            continue

        try:
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(cost), 0), COALESCE(SUM(pnl), 0), "
                "SUM(CASE WHEN status='won' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN status='lost' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN status='placed' THEN 1 ELSE 0 END) "
                "FROM trades"
            ).fetchone()
            conn.close()

            trades, cost, pnl, won, lost, pending = row
            cost = cost or 0
            pnl = pnl or 0
            won = won or 0
            lost = lost or 0
            pending = pending or 0
            resolved = won + lost

            total_invested += cost
            total_pnl += pnl
            total_trades += trades or 0
            total_won += won
            total_lost += lost
            total_pending += pending

            wr_str = f"{won / resolved * 100:.1f}%" if resolved > 0 else "N/A"

            report_lines.append(f"\n  --- {strategy} ---")
            report_lines.append(f"  Trades: {trades or 0} (Won: {won}, Lost: {lost}, Pending: {pending})")
            report_lines.append(f"  Win Rate: {wr_str}")
            report_lines.append(f"  Invested: ${cost:,.2f}")
            report_lines.append(f"  PnL: ${pnl:,.2f}")
        except Exception as e:
            report_lines.append(f"\n  {strategy}: Error reading DB: {e}")

    # Totals
    total_resolved = total_won + total_lost
    total_wr = f"{total_won / total_resolved * 100:.1f}%" if total_resolved > 0 else "N/A"

    report_lines.append(f"\n  {'='*40}")
    report_lines.append(f"  TOTAL: {total_trades} trades | WR: {total_wr}")
    report_lines.append(f"  Total Invested: ${total_invested:,.2f}")
    report_lines.append(f"  Total PnL: ${total_pnl:,.2f}")
    report_lines.append(f"  Open Orders: {total_pending}")

    # State info
    state = load_state()
    report_lines.append(f"\n  Last Weather Trade: {state.get('last_weather_trade', 'never')}")
    report_lines.append(f"  Last Sports Trade: {state.get('last_sports_trade', 'never')}")
    report_lines.append(f"  Total Runs: {json.dumps(state.get('total_runs', {}))}")

    # Auto-tune recommendations
    if logger:
        adjustments = check_auto_tune(logger)
        if adjustments:
            report_lines.append(f"\n  AUTO-TUNE RECOMMENDATIONS:")
            for strat, adj in adjustments.items():
                report_lines.append(f"    {strat}: {adj['note']}")

    report_lines.append(f"\n{'='*60}")

    report_text = "\n".join(report_lines)
    if logger:
        for line in report_lines:
            logger.info(line)
    else:
        print(report_text)

    return report_text


# --- Main Loop ---

def should_run(state_key, state, interval_mins=None, target_hours=None):
    """Check if a task should run based on schedule."""
    now = datetime.now(timezone.utc)
    # Convert to CET (UTC+1) for scheduling
    cet_now = now + timedelta(hours=1)
    cet_hour = cet_now.hour
    cet_minute = cet_now.minute

    last_run = state.get(state_key)
    if last_run:
        last_dt = datetime.fromisoformat(last_run)
        elapsed_mins = (now - last_dt).total_seconds() / 60
    else:
        elapsed_mins = float("inf")

    if target_hours is not None:
        # Run once per target hour
        if cet_hour not in target_hours:
            return False
        if elapsed_mins < 50:  # Don't re-run within 50 min
            return False
        return True

    if interval_mins is not None:
        # Run every N minutes during allowed hours
        if target_hours is not None and cet_hour not in target_hours:
            return False
        return elapsed_mins >= interval_mins

    return False


def cmd_start(args):
    """Start the main orchestrator loop."""
    logger = setup_logging()
    state = load_state()
    state["started_at"] = datetime.now(timezone.utc).isoformat()
    save_state(state)

    logger.info("OpenClaw orchestrator starting...")
    logger.info(f"  Strategies: weather ({BANKROLL_ALLOC['weather']:.0%}), "
                f"sports ({BANKROLL_ALLOC['sports']:.0%})")
    logger.info(f"  Weather schedule: {WEATHER_HOURS_CET} CET")
    logger.info(f"  Sports schedule: every {SPORTS_INTERVAL_MINS}min during "
                f"{SPORTS_HOURS_CET[0]:02d}:00-{SPORTS_HOURS_CET[-1]:02d}:59 CET")

    # Notify systemd if available
    try:
        import sdnotify
        sd = sdnotify.SystemdNotifier()
        sd.notify("READY=1")
    except ImportError:
        pass

    while True:
        try:
            now = datetime.now(timezone.utc)
            cet_now = now + timedelta(hours=1)
            cet_hour = cet_now.hour

            # Weather trade (4x daily at specific hours)
            if should_run("last_weather_trade", state, target_hours=WEATHER_HOURS_CET):
                logger.info("--- Weather trade run ---")
                ok, err = run_strategy("weather_edge.py", "trade", logger)
                state["last_weather_trade"] = now.isoformat()
                state["total_runs"]["weather_trade"] = state["total_runs"].get("weather_trade", 0) + 1
                if not ok:
                    state["errors"].append({"ts": now.isoformat(), "strategy": "weather", "error": err})
                save_state(state)

            # Weather resolve (daily at 07:00 CET)
            if should_run("last_weather_resolve", state, target_hours=[WEATHER_RESOLVE_HOUR_CET]):
                logger.info("--- Weather resolve run ---")
                ok, err = run_strategy("weather_edge.py", "resolve", logger)
                state["last_weather_resolve"] = now.isoformat()
                state["total_runs"]["weather_resolve"] = state["total_runs"].get("weather_resolve", 0) + 1
                save_state(state)

            # Sports trade (every 15 min during NBA game hours)
            if cet_hour in SPORTS_HOURS_CET:
                if should_run("last_sports_trade", state, interval_mins=SPORTS_INTERVAL_MINS):
                    logger.info("--- Sports arb run ---")
                    ok, err = run_strategy("sports_arb.py", "trade", logger)
                    state["last_sports_trade"] = now.isoformat()
                    state["total_runs"]["sports_trade"] = state["total_runs"].get("sports_trade", 0) + 1
                    if not ok:
                        state["errors"].append({"ts": now.isoformat(), "strategy": "sports", "error": err})
                    save_state(state)

            # Sports resolve (daily at 08:00 CET)
            if should_run("last_sports_resolve", state, target_hours=[SPORTS_RESOLVE_HOUR_CET]):
                logger.info("--- Sports resolve run ---")
                ok, err = run_strategy("sports_arb.py", "resolve", logger)
                state["last_sports_resolve"] = now.isoformat()
                state["total_runs"]["sports_resolve"] = state["total_runs"].get("sports_resolve", 0) + 1
                save_state(state)

            # Daily report (09:00 CET)
            if should_run("last_report", state, target_hours=[REPORT_HOUR_CET]):
                logger.info("--- Daily report ---")
                generate_report(logger)
                check_auto_tune(logger)
                state["last_report"] = now.isoformat()
                save_state(state)

            # Trim errors list (keep last 50)
            if len(state.get("errors", [])) > 50:
                state["errors"] = state["errors"][-50:]
                save_state(state)

        except KeyboardInterrupt:
            logger.info("OpenClaw shutting down (SIGINT)")
            break
        except Exception as e:
            logger.exception(f"Main loop error: {e}")
            state["errors"].append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "strategy": "orchestrator",
                "error": str(e),
            })
            save_state(state)

        # Sleep 60s between checks
        time.sleep(60)


def cmd_status(args):
    """Show current orchestrator status."""
    state = load_state()
    print(f"\n  OpenClaw Status")
    print(f"  {'='*40}")
    print(f"  Started: {state.get('started_at', 'never')}")
    print(f"  Last Weather Trade: {state.get('last_weather_trade', 'never')}")
    print(f"  Last Weather Resolve: {state.get('last_weather_resolve', 'never')}")
    print(f"  Last Sports Trade: {state.get('last_sports_trade', 'never')}")
    print(f"  Last Sports Resolve: {state.get('last_sports_resolve', 'never')}")
    print(f"  Last Report: {state.get('last_report', 'never')}")
    print(f"  Total Runs: {json.dumps(state.get('total_runs', {}))}")
    errors = state.get("errors", [])
    if errors:
        print(f"  Recent Errors ({len(errors)}):")
        for e in errors[-5:]:
            print(f"    {e['ts'][:19]} [{e['strategy']}] {e['error']}")


def cmd_report(args):
    generate_report()


def cmd_run_weather(args):
    logger = setup_logging()
    run_strategy("weather_edge.py", "trade", logger)


def cmd_run_sports(args):
    logger = setup_logging()
    run_strategy("sports_arb.py", "trade", logger)


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="OpenClaw — Autonomous Trading Orchestrator")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("start", help="Start orchestrator (foreground)")
    sub.add_parser("status", help="Show status")
    sub.add_parser("report", help="Show P&L report")
    sub.add_parser("run-weather", help="Manual weather scan+trade")
    sub.add_parser("run-sports", help="Manual sports scan+trade")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    cmds = {
        "start": cmd_start,
        "status": cmd_status,
        "report": cmd_report,
        "run-weather": cmd_run_weather,
        "run-sports": cmd_run_sports,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
