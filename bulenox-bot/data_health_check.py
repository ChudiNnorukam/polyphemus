#!/usr/bin/env python3
"""Bulenox Bot Data Health Check. Run via cron or on-demand.

Checks: DB integrity, feed health, MFE/MAE coverage, contract rollover.
Logs to stdout (captured by journalctl when run via systemd timer or cron).

Usage:
  python3 data_health_check.py           # full check
  python3 data_health_check.py --quick   # DB + feed only
"""
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone

DB_PATH = "data/trades.db"
STATE_PATH = "data/bot_state.json"
SERVICE = "bulenox"

# MBT contract months: F(Jan) G(Feb) H(Mar) J(Apr) K(May) M(Jun) N(Jul) Q(Aug) U(Sep) V(Oct) X(Nov) Z(Dec)
MBT_MONTHS = {1:"F", 2:"G", 3:"H", 4:"J", 5:"K", 6:"M", 7:"N", 8:"Q", 9:"U", 10:"V", 11:"X", 12:"Z"}

issues = []
warnings = []


def check_service():
    """Check if bulenox service is running."""
    try:
        result = subprocess.run(["systemctl", "is-active", SERVICE], capture_output=True, text=True, timeout=5)
        active = result.stdout.strip() == "active"
        if active:
            print("[OK] Service: active")
        else:
            print("[CRITICAL] Service: NOT RUNNING")
            issues.append("Service not running")
        return active
    except Exception as e:
        print(f"[WARN] Cannot check service: {e}")
        return False


def check_db():
    """Check DB exists, integrity, and schema."""
    if not os.path.exists(DB_PATH):
        print("[WARN] No trades.db yet (bot may not have traded)")
        warnings.append("No trades.db")
        return False

    con = sqlite3.connect(DB_PATH)

    # Integrity
    integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity == "ok":
        print("[OK] DB integrity: ok")
    else:
        print(f"[CRITICAL] DB integrity: {integrity}")
        issues.append(f"DB integrity: {integrity}")

    # WAL mode
    journal = con.execute("PRAGMA journal_mode").fetchone()[0]
    if journal == "wal":
        print("[OK] WAL mode: enabled")
    else:
        print(f"[WARN] Journal mode: {journal} (should be WAL)")
        warnings.append("WAL mode not enabled")

    # Schema check: MFE/MAE columns
    cols = [r[1] for r in con.execute("PRAGMA table_info(trades)").fetchall()]
    has_mfe = "mfe_ticks" in cols
    has_mae = "mae_ticks" in cols
    if has_mfe and has_mae:
        print("[OK] Schema: mfe_ticks + mae_ticks present")
    else:
        print(f"[WARN] Schema: mfe_ticks={has_mfe} mae_ticks={has_mae}")
        warnings.append("MFE/MAE columns missing")

    con.close()
    return True


def check_trades():
    """Check trade stats and MFE/MAE coverage."""
    if not os.path.exists(DB_PATH):
        return

    con = sqlite3.connect(DB_PATH)
    total = con.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    closed = con.execute("SELECT COUNT(*) FROM trades WHERE exit_ts IS NOT NULL").fetchone()[0]
    open_t = con.execute("SELECT COUNT(*) FROM trades WHERE exit_ts IS NULL").fetchone()[0]

    print(f"[INFO] Trades: {total} total, {closed} closed, {open_t} open")

    if closed > 0:
        # MFE/MAE coverage
        has_mfe = con.execute("SELECT COUNT(*) FROM trades WHERE mfe_ticks IS NOT NULL AND exit_ts IS NOT NULL").fetchone()[0]
        mfe_pct = has_mfe / closed * 100
        if mfe_pct >= 90:
            print(f"[OK] MFE/MAE coverage: {mfe_pct:.0f}% ({has_mfe}/{closed})")
        elif mfe_pct >= 50:
            print(f"[WARN] MFE/MAE coverage: {mfe_pct:.0f}% ({has_mfe}/{closed})")
            warnings.append(f"MFE/MAE only {mfe_pct:.0f}% populated")
        else:
            print(f"[CRITICAL] MFE/MAE coverage: {mfe_pct:.0f}% ({has_mfe}/{closed})")
            issues.append(f"MFE/MAE only {mfe_pct:.0f}% populated")

        # MFE/MAE averages
        if has_mfe > 0:
            avg_mfe = con.execute("SELECT AVG(mfe_ticks) FROM trades WHERE mfe_ticks IS NOT NULL").fetchone()[0]
            avg_mae = con.execute("SELECT AVG(mae_ticks) FROM trades WHERE mae_ticks IS NOT NULL").fetchone()[0]
            print(f"[INFO] Avg MFE: {avg_mfe:+.1f} ticks | Avg MAE: {avg_mae:+.1f} ticks")

        # WR and P&L
        wins = con.execute("SELECT COUNT(*) FROM trades WHERE pnl_pts > 0").fetchone()[0]
        wr = wins / closed * 100
        total_pnl = con.execute("SELECT COALESCE(SUM(pnl_pts), 0) FROM trades WHERE exit_ts IS NOT NULL").fetchone()[0]
        net_pnl = total_pnl - (closed * 3.54) - (closed * 2.50)
        print(f"[INFO] WR: {wr:.1f}% | Gross P&L: ${total_pnl:+,.2f} | Net P&L: ${net_pnl:+,.2f}")

    con.close()


def check_feed_health():
    """Check Coinbase feed reconnection rate from journalctl."""
    try:
        result = subprocess.run(
            ["journalctl", "-u", SERVICE, "--since", "1 hour ago", "--no-pager", "-q"],
            capture_output=True, text=True, timeout=10
        )
        log = result.stdout
        reconnects = log.count("Coinbase connected")
        errors = log.count("Coinbase feed error")
        signals = log.count("Signal:")

        if reconnects <= 2:
            print(f"[OK] Feed: {reconnects} reconnections in 1h (stable)")
        elif reconnects <= 5:
            print(f"[WARN] Feed: {reconnects} reconnections in 1h")
            warnings.append(f"Feed reconnected {reconnects}x in 1h")
        else:
            print(f"[CRITICAL] Feed: {reconnects} reconnections in 1h (unstable)")
            issues.append(f"Feed reconnected {reconnects}x in 1h")

        if errors > 0:
            print(f"[WARN] Feed errors: {errors} in 1h")

        print(f"[INFO] Signals detected: {signals} in 1h")

    except Exception as e:
        print(f"[WARN] Cannot check feed health: {e}")


def check_contract_rollover():
    """Warn if MBT front month is close to expiry."""
    now = datetime.now(timezone.utc)
    current_month = now.month
    current_year = now.year

    # MBT expires on last Friday of the contract month
    # Simple approximation: warn if we're past the 25th of the month
    days_left_approx = 30 - now.day
    if days_left_approx < 0:
        days_left_approx = 0

    month_code = MBT_MONTHS.get(current_month, "?")
    year_short = str(current_year)[-1]
    current_contract = f"MBT{month_code}{year_short}"

    next_month = current_month + 1 if current_month < 12 else 1
    next_year = current_year if current_month < 12 else current_year + 1
    next_code = MBT_MONTHS.get(next_month, "?")
    next_year_short = str(next_year)[-1]
    next_contract = f"MBT{next_code}{next_year_short}"

    if days_left_approx <= 3:
        print(f"[CRITICAL] Contract rollover: {current_contract} expires in ~{days_left_approx} days! Switch to {next_contract}")
        issues.append(f"Contract {current_contract} expiring in {days_left_approx} days")
    elif days_left_approx <= 7:
        print(f"[WARN] Contract rollover: {current_contract} expires in ~{days_left_approx} days. Plan switch to {next_contract}")
        warnings.append(f"Contract expiry in {days_left_approx} days")
    else:
        print(f"[OK] Contract: {current_contract} (~{days_left_approx} days to expiry)")


def check_state():
    """Check bot state file."""
    if not os.path.exists(STATE_PATH):
        print("[WARN] No bot_state.json")
        return

    with open(STATE_PATH) as f:
        state = json.load(f)

    halted = state.get("halted", False)
    consecutive = state.get("consecutive_losses", 0)
    peak = state.get("peak_balance", 50000)

    if halted:
        print(f"[CRITICAL] Bot is HALTED (consecutive_losses={consecutive})")
        issues.append("Bot is halted")
    else:
        print(f"[OK] Bot state: running, consecutive_losses={consecutive}, peak=${peak:,.2f}")


def main():
    quick = "--quick" in sys.argv
    print(f"=== Bulenox Data Health Check ({datetime.now().strftime('%Y-%m-%d %H:%M')}) ===")
    print()

    check_service()
    check_db()
    check_state()
    check_feed_health()

    if not quick:
        check_trades()
        check_contract_rollover()

    print()
    if issues:
        print(f"RESULT: {len(issues)} CRITICAL issues")
        for i in issues:
            print(f"  [!] {i}")
    elif warnings:
        print(f"RESULT: HEALTHY with {len(warnings)} warnings")
        for w in warnings:
            print(f"  [~] {w}")
    else:
        print("RESULT: ALL HEALTHY")


if __name__ == "__main__":
    main()
