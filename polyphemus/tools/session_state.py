#!/usr/bin/env python3
"""Session startup state snapshot. Run at the start of every trading session.

Queries live DBs, .env, and systemd drop-in overrides - never trusts stale
MEMORY.md values. Config precedence: drop-in env > Doppler > .env file.

Usage:
    python3 /opt/lagbot/lagbot/tools/session_state.py
    python3 /opt/lagbot/lagbot/tools/session_state.py --days 3
"""

import argparse
import sqlite3
import subprocess
import os
import sys
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.request import urlopen

# NOTE: no imports from /opt/lagbot/lagbot/ (Bug #39 - types.py shadows stdlib)

INSTANCES = {
    "emmanuel": {
        "env": "/opt/lagbot/instances/emmanuel/.env",
        "perf_db": "/opt/lagbot/instances/emmanuel/data/performance.db",
        "signals_db": "/opt/lagbot/instances/emmanuel/data/signals.db",
        "service": "lagbot@emmanuel",
    },
    "polyphemus": {
        "env": "/opt/lagbot/instances/polyphemus/.env",
        "perf_db": "/opt/lagbot/instances/polyphemus/data/performance.db",
        "signals_db": "/opt/lagbot/instances/polyphemus/data/signals.db",
        "service": "lagbot@polyphemus",
    },
}

CONFIG_KEYS = [
    "DRY_RUN", "ENABLE_ACCUMULATOR", "ACCUM_DRY_RUN", "ACCUM_MAX_PAIR_COST",
    "ENABLE_PAIR_ARB", "PAIR_ARB_DRY_RUN", "PAIR_ARB_MAX_PAIR_COST",
    "ACCUM_MODE_ENABLED",
    "DASHBOARD_HOST", "DASHBOARD_PORT",
    "ASSET_FILTER",
    "CHEAP_SIDE_MIN_PRICE", "CHEAP_SIDE_MAX_PRICE", "MAX_ENTRY_PRICE",
    "CHEAP_SIDE_ACTIVE_HOURS",
    "POST_LOSS_COOLDOWN_MINS",
    "ACCUM_MAX_ROUNDS", "ACCUM_BET_PER_ROUND",
    "PROFIT_TARGET_EARLY_ENABLED",
    "MOMENTUM_TRIGGER_PCT",
]


def db_query(db_path, sql, params=()):
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        return f"ERROR: {e}"


def read_env(env_path):
    config = {}
    if not os.path.exists(env_path):
        return config
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                config[k.strip()] = v.strip().strip('"').strip("'")
    return config


def read_dropin_env(service_name):
    """Parse env KEY=VALUE overrides from systemd drop-in ExecStart line.

    Drop-in format:
      ExecStart=/usr/bin/doppler run ... -- env KEY=VAL KEY=VAL ... /opt/.../python -m lagbot.main

    These override both .env and Doppler values at runtime.
    """
    import re
    conf_path = f"/etc/systemd/system/{service_name}.service.d/dry_run.conf"
    if not os.path.exists(conf_path):
        return {}
    try:
        with open(conf_path) as f:
            text = f.read()
    except Exception:
        return {}

    # Find the ExecStart line that contains "env "
    overrides = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("ExecStart=") or " env " not in line:
            continue
        # Extract the segment between "-- env " and the python command
        m = re.search(r'-- env (.+?)/opt/', line)
        if not m:
            continue
        for pair in m.group(1).strip().split():
            if "=" in pair:
                k, _, v = pair.partition("=")
                overrides[k] = v
    return overrides


def service_status(service_name):
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def get_dashboard_base(config):
    host = config.get("DASHBOARD_HOST", "127.0.0.1") or "127.0.0.1"
    port = config.get("DASHBOARD_PORT", "8080") or "8080"
    return f"http://{host}:{port}"


def fetch_json(url, timeout=5):
    try:
        with urlopen(url, timeout=timeout) as resp:
            return json_load_bytes(resp.read())
    except (URLError, TimeoutError, ValueError, OSError) as e:
        return {"_error": str(e)}


def json_load_bytes(raw):
    import json
    return json.loads(raw.decode("utf-8"))


def recent_errors(service_name, since_mins=5):
    try:
        result = subprocess.run(
            ["journalctl", "-u", service_name, f"--since={since_mins} minutes ago",
             "--no-pager", "-q"],
            capture_output=True, text=True, timeout=10
        )
        lines = result.stdout.splitlines()
        errors = [l for l in lines if any(k in l.lower() for k in ("traceback", "exception", "critical"))
                  or ("error" in l.lower() and "errors=" not in l.lower())]
        return errors[-5:] if errors else []
    except Exception:
        return []


def last_logged_balance(service_name):
    """Grep service logs for the most recent balance line."""
    try:
        result = subprocess.run(
            ["journalctl", "-u", service_name, "--no-pager", "-q",
             "--since=24 hours ago", "--grep=alance"],
            capture_output=True, text=True, timeout=10
        )
        lines = [l for l in result.stdout.splitlines() if "alance" in l]
        if not lines:
            return "n/a"
        last = lines[-1]
        # Extract $NNN.NN - look for a dollar amount pattern
        import re
        m = re.search(r'\$?([\d]+\.[\d]+)', last)
        return f"${m.group(1)}" if m else last[-60:].strip()
    except Exception:
        return "n/a"


def confidence_label(n):
    if n < 30:   return "ANECDOTAL"
    if n < 107:  return "LOW"
    if n < 385:  return "MODERATE"
    return "SIGNIFICANT"


def run(days=7):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print("=" * 70)
    print(f"SESSION STATE  |  {now}  |  last {days} days")
    print("=" * 70)

    halt_flags = []
    investigate_flags = []

    for name, inst in INSTANCES.items():
        print(f"\n{'─'*30} {name.upper()} {'─'*30}")
        config = read_env(inst["env"])
        dropin = read_dropin_env(inst["service"])
        config.update(dropin)  # drop-in overrides .env
        dashboard_base = get_dashboard_base(config)
        accum = fetch_json(f"{dashboard_base}/api/accumulator")
        status_api = fetch_json(f"{dashboard_base}/api/status")
        balance_api = fetch_json(f"{dashboard_base}/api/balance")

        # Service health
        status = service_status(inst["service"])
        errors = recent_errors(inst["service"])
        balance_hint = last_logged_balance(inst["service"])
        status_str = "ACTIVE" if status == "active" else f"INACTIVE ({status})"
        print(f"  Service:  {status_str}")
        print(f"  Balance:  {balance_hint}")
        if errors:
            print(f"  Errors ({len(errors)} in last 5m):")
            for e in errors:
                print(f"    {e[-120:]}")
            investigate_flags.append(f"{name}: {len(errors)} recent errors")
        print(f"  Dashboard: {dashboard_base}")

        # Open positions
        open_pos = db_query(
            inst["perf_db"],
            "SELECT slug, entry_price, entry_size, metadata FROM trades WHERE exit_time IS NULL ORDER BY entry_time"
        )
        if isinstance(open_pos, list):
            if open_pos:
                print(f"  Open positions: {len(open_pos)}")
                for p in open_pos:
                    import json
                    meta = {}
                    try: meta = json.loads(p.get("metadata") or "{}")
                    except: pass
                    rnd = f" r{meta['round']}/{meta['total_rounds']}" if "round" in meta else ""
                    print(f"    {p['slug']}  @{p['entry_price']}  {p['entry_size']:.1f}sh{rnd}")
                investigate_flags.append(f"{name}: {len(open_pos)} open positions (check before restart)")
            else:
                print(f"  Open positions: 0")
        else:
            print(f"  Open positions: DB error - {open_pos}")

        # Accumulator truth surface
        accum_enabled = config.get("ENABLE_ACCUMULATOR", "").lower() == "true"
        if accum.get("_error"):
            print(f"  Accumulator API: ERROR - {accum['_error']}")
            if accum_enabled and status == "active":
                investigate_flags.append(f"{name}: accumulator API unreachable on {dashboard_base}")
        else:
            effective_dry = status_api.get("effective_accumulator_dry_run")
            if effective_dry is None:
                effective_dry = accum.get("effective_accumulator_dry_run")
            active_pairs = accum.get("active_positions", 0)
            print("  Accumulator:")
            print(f"    enabled={accum.get('enabled', False)} state={accum.get('state', 'unknown')} active_pairs={active_pairs}")
            print(
                f"    dry_run(config)={config.get('ACCUM_DRY_RUN', 'NOT SET')} "
                f"effective_dry_run={effective_dry}"
            )
            print(
                f"    best_bid_pair={accum.get('best_bid_pair', 'n/a')} "
                f"orders={accum.get('orders_placed', 0)} placed/{accum.get('orders_filled', 0)} filled"
            )
            print(
                f"    cycles=H{accum.get('hedged_count', 0)}/U{accum.get('unwound_count', 0)}/O{accum.get('orphaned_count', 0)} "
                f"candidates={accum.get('candidates_seen', 0)} seen/{accum.get('candidates_rejected', 0)} rejected"
            )
            block_reason = accum.get("last_eval_block_reason") or "n/a"
            print(f"    last_block={block_reason}")
            if accum_enabled and config.get("DRY_RUN", "").lower() != config.get("ACCUM_DRY_RUN", "").lower():
                halt_flags.append(
                    f"{name}: DRY_RUN={config.get('DRY_RUN')} but ACCUM_DRY_RUN={config.get('ACCUM_DRY_RUN')}"
                )
            if accum_enabled and accum.get("candidates_seen", 0) > 0 and accum.get("orders_placed", 0) == 0:
                investigate_flags.append(
                    f"{name}: accumulator sees candidates but has placed 0 orders"
                )

        if not balance_api.get("_error"):
            print(
                f"  Balance API: ${balance_api.get('balance', 0)} "
                f"(available=${balance_api.get('available', 0)} deployed=${balance_api.get('deployed', 0)})"
            )

        # Performance (last N days, cheap_side, live, 0.01-0.50)
        perf = db_query(
            inst["perf_db"],
            f"""
            SELECT COUNT(*) as n,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                ROUND(SUM(pnl), 2) as total_pnl,
                ROUND(AVG(pnl), 4) as ev,
                ROUND(AVG(CASE WHEN pnl > 0 THEN pnl END), 4) as avg_win,
                ROUND(ABS(AVG(CASE WHEN pnl <= 0 THEN pnl END)), 4) as avg_loss,
                ROUND(AVG(entry_price), 3) as avg_entry
            FROM trades
            WHERE exit_time IS NOT NULL
              AND entry_time > strftime('%s', 'now', '-{days} days')
              AND entry_price BETWEEN 0.01 AND 0.50
              AND trade_id NOT LIKE 'dry_%'
            """
        )
        if isinstance(perf, list) and perf:
            p = perf[0]
            n = p["n"] or 0
            if n > 0:
                wr = 100.0 * p["wins"] / n
                payoff = (p["avg_win"] / p["avg_loss"]) if p["avg_loss"] else 0
                kelly = (wr/100 * payoff - (1 - wr/100)) / payoff if payoff else 0
                be = p["avg_entry"] * 100 if p["avg_entry"] else 0
                conf = confidence_label(n)
                print(f"  Performance ({days}d cheap_side live):")
                print(f"    n={n} ({conf}) | WR={wr:.1f}% | EV=${p['ev']:.4f}/trade | P&L=${p['total_pnl']:.2f}")
                print(f"    avg_entry=${p['avg_entry']:.3f} | break_even={be:.1f}% | payoff={payoff:.2f}x | kelly={kelly:+.3f}")
                if kelly < 0 and n >= 50:
                    halt_flags.append(f"{name}: negative Kelly on cheap_side (n={n})")
                elif wr < be and n >= 30:
                    investigate_flags.append(f"{name}: WR {wr:.1f}% below break-even {be:.1f}%")
            else:
                print(f"  Performance: no resolved trades in last {days} days")
                investigate_flags.append(f"{name}: 0 resolved trades in {days} days - DB recording issue?")

        # Entry price buckets (quick)
        buckets = db_query(
            inst["perf_db"],
            f"""
            SELECT CASE
                WHEN entry_price < 0.30 THEN '0.01-0.30'
                WHEN entry_price < 0.40 THEN '0.30-0.40'
                WHEN entry_price < 0.50 THEN '0.40-0.50'
                ELSE '0.50+' END as bucket,
              COUNT(*) as n,
              ROUND(100.0*SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) as wr,
              ROUND(SUM(pnl),2) as pnl
            FROM trades
            WHERE exit_time IS NOT NULL
              AND entry_time > strftime('%s', 'now', '-{days} days')
              AND entry_price BETWEEN 0.01 AND 0.50
              AND trade_id NOT LIKE 'dry_%'
            GROUP BY bucket ORDER BY bucket
            """
        )
        if isinstance(buckets, list) and buckets:
            print(f"  Entry buckets:")
            for b in buckets:
                flag = " <-- INVESTIGATE" if b["n"] >= 20 and b["wr"] < 35 else ""
                print(f"    {b['bucket']}: n={b['n']} WR={b['wr']}% P&L=${b['pnl']}{flag}")

        # Config
        if config:
            print(f"  Config:")
            for k in CONFIG_KEYS:
                v = config.get(k, "NOT SET")
                suffix = " [override]" if k in dropin else ""
                print(f"    {k}={v}{suffix}")

    # Gate
    print(f"\n{'=' * 70}")
    print("GATE")
    print(f"{'=' * 70}")
    if halt_flags:
        print("HALT")
        for f in halt_flags:
            print(f"  - {f}")
    elif investigate_flags:
        print("INVESTIGATE")
        for f in investigate_flags:
            print(f"  - {f}")
    else:
        print("PROCEED")

    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Session startup state snapshot")
    parser.add_argument("--days", type=int, default=7, help="Lookback window in days")
    args = parser.parse_args()
    run(days=args.days)
