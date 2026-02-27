#!/usr/bin/env python3
"""Pre-deploy and post-deploy verification for lagbot instances.

Born from real money losses:
- Schema mismatch: fear_greed column missing, 40 silent failures/day
- Circuit breaker poisoning: snipe losses (-$229) blocking momentum trades
- Silent exceptions: swallowed errors invisible to is-active check
- Orphan data: killed strategy trades polluting daily P&L
- Config drift: ACCUM_DRY_RUN default returning fake $400 balance

Usage:
    python3 polyphemus/pre_deploy_check.py pre emmanuel
    python3 polyphemus/pre_deploy_check.py post emmanuel
    python3 polyphemus/pre_deploy_check.py audit emmanuel
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

VPS = "82.24.19.114"
VPS_CODE = "/opt/lagbot/lagbot"
VPS_INSTANCES = "/opt/lagbot/instances"
LOCAL_CODE = Path(__file__).parent
BASELINE_PATH = Path("/tmp/lagbot_deploy_baseline.json")

# --- result helpers ---

PASS, FAIL, WARN, INFO = "PASS", "FAIL", "WARN", "INFO"
results = []


def record(level, check, msg):
    results.append((level, check, msg))
    icon = {"PASS": "\033[32m[PASS]\033[0m", "FAIL": "\033[31m[FAIL]\033[0m",
            "WARN": "\033[33m[WARN]\033[0m", "INFO": "\033[36m[INFO]\033[0m"}[level]
    print(f"  {icon} {check}: {msg}")


def ssh(cmd, timeout=30):
    """Run command on VPS. Returns stdout or empty string on error."""
    try:
        r = subprocess.run(
            ["ssh", f"root@{VPS}", cmd],
            capture_output=True, text=True, timeout=timeout
        )
        return r.stdout.strip()
    except (subprocess.TimeoutExpired, Exception) as e:
        return f"SSH_ERROR: {e}"


def local(cmd, timeout=60):
    """Run local command. Returns stdout."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.returncode
    except Exception as e:
        return f"ERROR: {e}", 1


# ========== PRE-DEPLOY CHECKS ==========

def check_py_compile():
    """Syntax-check all .py files locally."""
    py_files = sorted(LOCAL_CODE.glob("*.py"))
    failures = []
    for f in py_files:
        if f.name.startswith("test_"):
            continue
        out, rc = local(f"python3 -m py_compile {f}")
        if rc != 0:
            failures.append(f.name)
    if failures:
        record(FAIL, "py_compile", f"{len(failures)} files failed: {', '.join(failures)}")
    else:
        record(PASS, "py_compile", f"{len(py_files)} files OK")


def check_tests():
    """Run test suite."""
    test_file = LOCAL_CODE / "test_accumulator.py"
    if not test_file.exists():
        # Try alternate test files
        test_files = list(LOCAL_CODE.glob("test_*.py"))
        if not test_files:
            record(WARN, "tests", "No test files found")
            return
        test_file = test_files[0]

    out, rc = local(
        f"cd {LOCAL_CODE.parent} && python3 -m pytest {test_file} -q --tb=no 2>&1 | tail -5",
        timeout=120
    )
    if rc == 0:
        # Extract pass count
        match = re.search(r"(\d+) passed", out)
        count = match.group(1) if match else "?"
        record(PASS, "tests", f"{count} passed")
    else:
        record(FAIL, "tests", f"Tests failed:\n{out}")


def check_schema_contract():
    """Verify signal_logger columns match what signal_bot writes."""
    # 1. Extract signal_logger columns from CREATE TABLE + migrations
    logger_file = LOCAL_CODE / "signal_logger.py"
    logger_src = logger_file.read_text()

    # Extract CREATE TABLE columns (match all "word TYPE" patterns inside the block)
    logger_cols = set()
    # Find the full CREATE TABLE block (greedy to get past AUTOINCREMENT parentheses)
    create_match = re.search(
        r"CREATE TABLE IF NOT EXISTS signals\s*\((.+?)\)\s*\"\"\"|"
        r"CREATE TABLE.*?signals\s*\((.+?)\)\s*\"\"\"",
        logger_src, re.DOTALL
    )
    if create_match:
        block = create_match.group(1) or create_match.group(2) or ""
        for line in block.split("\n"):
            line = line.strip().strip(",")
            # Match column definitions: "column_name TYPE ..."
            m = re.match(r"^(\w+)\s+(TEXT|REAL|INTEGER)\b", line)
            if m and m.group(1) != "id":
                logger_cols.add(m.group(1))

    # Extract migration columns
    for match in re.finditer(r'\("(\w+)",\s*"', logger_src):
        logger_cols.add(match.group(1))

    # 2. Extract signal_bot log_features keys
    bot_file = LOCAL_CODE / "signal_bot.py"
    bot_src = bot_file.read_text()

    bot_cols = set()
    # Match log_features["key"] = assignments
    for match in re.finditer(r'log_features\["(\w+)"\]', bot_src):
        bot_cols.add(match.group(1))
    # Match "key": value patterns inside log_features dict literal
    in_features = False
    for line in bot_src.split("\n"):
        if "log_features = {" in line or "log_features ={" in line:
            in_features = True
        if in_features:
            m = re.match(r'\s*"(\w+)":', line)
            if m:
                bot_cols.add(m.group(1))
            if line.strip() == "}":
                in_features = False

    # 3. Find mismatches: keys written by bot but missing from logger
    missing = bot_cols - logger_cols - {"id"}
    if missing:
        record(FAIL, "schema_contract",
               f"signal_bot writes keys missing from signal_logger: {sorted(missing)}")
    else:
        record(PASS, "schema_contract",
               f"All {len(bot_cols)} signal_bot keys exist in signal_logger schema")


def check_changed_files():
    """Report blast radius of uncommitted changes."""
    out, _ = local("cd {} && git diff --name-only HEAD -- polyphemus/".format(LOCAL_CODE.parent))
    if not out:
        out, _ = local("cd {} && git diff --cached --name-only -- polyphemus/".format(LOCAL_CODE.parent))
    if not out:
        record(INFO, "changed_files", "No uncommitted changes in polyphemus/")
        return

    changed = [f for f in out.split("\n") if f.strip()]
    record(INFO, "changed_files", f"{len(changed)} files changed: {', '.join(Path(f).name for f in changed)}")

    # Check for dangerous solo changes (files that must be changed together)
    names = {Path(f).name for f in changed}
    couples = [
        ({"signal_logger.py"}, {"signal_bot.py"}, "signal_bot writes features that signal_logger must accept"),
        ({"config.py"}, {"position_executor.py", "signal_guard.py"}, "config params must be read somewhere"),
        ({"performance_db.py"}, {"circuit_breaker.py"}, "circuit breaker reads from performance_db"),
    ]
    for file_a, file_b, reason in couples:
        if file_a & names and not (file_b & names):
            record(WARN, "blast_radius",
                   f"Changed {file_a} without {file_b} - {reason}")


def check_silent_exceptions():
    """Scan for except blocks that swallow errors without re-raise."""
    count = 0
    flagged = []
    for pyf in sorted(LOCAL_CODE.glob("*.py")):
        if pyf.name.startswith("test_"):
            continue
        lines = pyf.read_text().split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if re.match(r"except\s*(Exception|BaseException|\w*Error)?\s*(as \w+)?:", stripped):
                # Check next 5 lines for raise
                block = "\n".join(lines[i:i+6])
                if "raise" not in block and "return" not in block:
                    # Pure swallow - only logs
                    count += 1
                    if count <= 5:
                        flagged.append(f"{pyf.name}:{i+1}")
    if count > 0:
        record(WARN, "silent_exceptions",
               f"{count} except blocks swallow errors: {', '.join(flagged)}" +
               (f" (+{count-5} more)" if count > 5 else ""))
    else:
        record(PASS, "silent_exceptions", "No swallowed exceptions found")


def capture_baseline(instance):
    """SSH to VPS, snapshot current state for post-deploy comparison."""
    error_count = ssh(
        f"journalctl -u lagbot@{instance} --since '1 hour ago' --no-pager 2>/dev/null"
        f" | grep -ciE 'error|traceback|exception' || echo 0"
    )
    warn_count = ssh(
        f"journalctl -u lagbot@{instance} --since '1 hour ago' --no-pager 2>/dev/null"
        f" | grep -ci 'WARNING' || echo 0"
    )
    daily_pnl = ssh(
        f"sqlite3 /opt/lagbot/instances/{instance}/data/performance.db "
        f"\"SELECT COALESCE(SUM(pnl), 0.0) FROM trades "
        f"WHERE exit_time >= $(python3 -c 'from datetime import datetime,timezone; "
        f"import time; print(int(datetime.now(timezone.utc).replace(hour=0,minute=0,second=0).timestamp()))')\""
    )
    cb_state = ssh(f"cat /opt/lagbot/instances/{instance}/data/streak_state.json 2>/dev/null || echo '{{}}'")
    is_active = ssh(f"systemctl is-active lagbot@{instance} 2>/dev/null")

    baseline = {
        "instance": instance,
        "error_count": int(error_count) if error_count.isdigit() else 0,
        "warn_count": int(warn_count) if warn_count.isdigit() else 0,
        "daily_pnl": float(daily_pnl) if daily_pnl.replace("-", "").replace(".", "").isdigit() else 0.0,
        "cb_state": cb_state,
        "was_active": is_active == "active",
    }

    BASELINE_PATH.write_text(json.dumps(baseline, indent=2))
    record(INFO, "baseline",
           f"Captured: pnl=${baseline['daily_pnl']:.2f}, errors={baseline['error_count']}, "
           f"warns={baseline['warn_count']}, active={baseline['was_active']}")
    return baseline


# ========== POST-DEPLOY CHECKS ==========

def check_service_active(instance):
    """Verify service is running."""
    status = ssh(f"systemctl is-active lagbot@{instance}")
    if status == "active":
        record(PASS, "service_active", f"lagbot@{instance} is active")
    else:
        record(FAIL, "service_active", f"lagbot@{instance} status: {status}")


def check_startup_errors(instance):
    """Check for errors in first 60 seconds after restart."""
    errors = ssh(
        f"journalctl -u lagbot@{instance} --since '90 seconds ago' --no-pager 2>/dev/null"
        f" | grep -iE 'Traceback|AttributeError|KeyError|NameError|TypeError|ImportError|RuntimeError|\\[ERROR\\]'"
    )
    if errors and "SSH_ERROR" not in errors:
        lines = [l for l in errors.split("\n") if l.strip()]
        record(FAIL, "startup_errors", f"{len(lines)} errors after restart:\n" +
               "\n".join(f"    {l.strip()[-120:]}" for l in lines[:5]))
    else:
        record(PASS, "startup_errors", "No errors in startup logs")


def check_error_delta(instance):
    """Compare error count vs baseline."""
    if not BASELINE_PATH.exists():
        record(WARN, "error_delta", "No baseline found (run 'pre' first)")
        return

    baseline = json.loads(BASELINE_PATH.read_text())
    current_errors = ssh(
        f"journalctl -u lagbot@{instance} --since '5 minutes ago' --no-pager 2>/dev/null"
        f" | grep -ciE 'error|traceback|exception' || echo 0"
    )
    current = int(current_errors) if current_errors.isdigit() else 0

    if current > 0:
        record(WARN, "error_delta", f"{current} errors in last 5min (baseline was {baseline.get('error_count', 0)}/hr)")
    else:
        record(PASS, "error_delta", "0 errors since deploy")


def check_circuit_breaker(instance):
    """Verify circuit breaker state and daily P&L."""
    daily_pnl = ssh(
        f"sqlite3 /opt/lagbot/instances/{instance}/data/performance.db "
        f"\"SELECT COALESCE(SUM(pnl), 0.0) FROM trades "
        f"WHERE exit_time >= $(python3 -c 'from datetime import datetime,timezone; "
        f"print(int(datetime.now(timezone.utc).replace(hour=0,minute=0,second=0).timestamp()))')\""
    )
    pnl = float(daily_pnl) if daily_pnl.replace("-", "").replace(".", "").isdigit() else 0.0

    # Get max_daily_loss from .env
    max_loss = ssh(f"grep -i MAX_DAILY_LOSS /opt/lagbot/instances/{instance}/.env 2>/dev/null | head -1")
    max_loss_val = 0.0
    if max_loss:
        match = re.search(r"=\s*([\d.]+)", max_loss)
        if match:
            max_loss_val = float(match.group(1))

    kill_switch = ssh(f"ls /opt/lagbot/instances/{instance}/data/KILL_SWITCH 2>/dev/null")

    issues = []
    if kill_switch and "No such file" not in kill_switch:
        issues.append("KILL_SWITCH file exists")
    if max_loss_val > 0 and pnl <= -max_loss_val:
        issues.append(f"daily P&L ${pnl:.2f} exceeds limit -${max_loss_val:.0f}")

    if issues:
        record(FAIL, "circuit_breaker", " | ".join(issues))
    else:
        headroom = max_loss_val + pnl if max_loss_val > 0 else float("inf")
        record(PASS, "circuit_breaker",
               f"daily P&L=${pnl:.2f}, limit=-${max_loss_val:.0f}, headroom=${headroom:.2f}")


def check_orphan_trades(instance):
    """Detect trades from disabled strategies polluting daily P&L."""
    # Get config ranges
    env_out = ssh(f"grep -iE 'MIN_ENTRY_PRICE|MAX_ENTRY_PRICE|ENABLE_RESOLUTION_SNIPE' "
                  f"/opt/lagbot/instances/{instance}/.env 2>/dev/null")
    min_price, max_price, snipe_enabled = 0.72, 0.90, False
    for line in env_out.split("\n"):
        if "MIN_ENTRY_PRICE" in line.upper():
            m = re.search(r"=\s*([\d.]+)", line)
            if m: min_price = float(m.group(1))
        if "MAX_ENTRY_PRICE" in line.upper():
            m = re.search(r"=\s*([\d.]+)", line)
            if m: max_price = float(m.group(1))
        if "ENABLE_RESOLUTION_SNIPE" in line.upper():
            snipe_enabled = "true" in line.lower()

    # Find today's trades with entry_price outside momentum range
    orphans = ssh(
        f"sqlite3 /opt/lagbot/instances/{instance}/data/performance.db "
        f"\"SELECT slug, entry_price, pnl FROM trades "
        f"WHERE exit_time >= $(python3 -c 'from datetime import datetime,timezone; "
        f"print(int(datetime.now(timezone.utc).replace(hour=0,minute=0,second=0).timestamp()))') "
        f"AND (entry_price > {max_price + 0.01} OR entry_price < {min_price - 0.05}) "
        f"AND pnl < -10\""
    )

    if orphans and orphans.strip() and "SSH_ERROR" not in orphans:
        lines = orphans.strip().split("\n")
        total_pnl = sum(float(l.split("|")[2]) for l in lines if "|" in l)
        record(FAIL, "orphan_trades",
               f"{len(lines)} trades outside config range (total ${total_pnl:.2f}): " +
               ", ".join(l.split("|")[0] for l in lines[:3]))
    else:
        record(PASS, "orphan_trades", "No orphan trades from disabled strategies")


def check_config_drift(instance):
    """Compare key .env values against known safe ranges."""
    env_out = ssh(f"cat /opt/lagbot/instances/{instance}/.env 2>/dev/null")
    if "SSH_ERROR" in env_out or not env_out:
        record(WARN, "config_drift", "Could not read .env")
        return

    env = {}
    for line in env_out.split("\n"):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, _, val = line.partition("=")
            env[key.strip().upper()] = val.strip().strip("'\"")

    issues = []

    # CRITICAL: accum_dry_run must be false on live instances
    adr = env.get("ACCUM_DRY_RUN", "").lower()
    if adr == "true" or (not adr and env.get("DRY_RUN", "").lower() == "false"):
        issues.append("ACCUM_DRY_RUN=true (or missing) on live instance - balance returns fake $400")

    # DRY_RUN should match intent
    dr = env.get("DRY_RUN", "true").lower()
    if dr == "true":
        record(INFO, "config_drift", "DRY_RUN=true (no real trades)")

    # Subsystems that should be explicitly disabled on momentum-only instances
    for key in ["ENABLE_ARB", "ENABLE_ACCUMULATOR", "ENABLE_PAIR_ARB"]:
        val = env.get(key, "").lower()
        if val == "true":
            issues.append(f"{key}=true (should be false on momentum-only instance)")
        elif not val:
            issues.append(f"{key} missing from .env (defaults may contaminate)")

    # Signature type must match wallet
    sig = env.get("SIGNATURE_TYPE", "")
    if sig and sig not in ("0", "1", "2"):
        issues.append(f"SIGNATURE_TYPE={sig} (invalid, must be 0/1/2)")

    if issues:
        record(FAIL, "config_drift", " | ".join(issues))
    else:
        record(PASS, "config_drift", "Key config values within safe ranges")


def check_schema_on_vps(instance):
    """Verify DB columns on VPS match code expectations."""
    # signals.db
    vps_cols = ssh(
        f"sqlite3 /opt/lagbot/instances/{instance}/data/signals.db "
        f"\"PRAGMA table_info(signals)\" 2>/dev/null"
    )
    if "SSH_ERROR" in vps_cols or not vps_cols:
        record(WARN, "schema_vps", "Could not read signals.db schema")
        return

    vps_col_names = set()
    for line in vps_cols.split("\n"):
        parts = line.split("|")
        if len(parts) >= 2:
            vps_col_names.add(parts[1])

    # Expected columns from signal_logger.py
    logger_file = LOCAL_CODE / "signal_logger.py"
    logger_src = logger_file.read_text()
    code_cols = set()
    create_match = re.search(
        r"CREATE TABLE IF NOT EXISTS signals\s*\((.+?)\)\s*\"\"\"|"
        r"CREATE TABLE.*?signals\s*\((.+?)\)\s*\"\"\"",
        logger_src, re.DOTALL
    )
    if create_match:
        block = create_match.group(1) or create_match.group(2) or ""
        for line in block.split("\n"):
            line = line.strip().strip(",")
            m = re.match(r"^(\w+)\s+(TEXT|REAL|INTEGER)\b", line)
            if m and m.group(1) != "id":
                code_cols.add(m.group(1))
    for match in re.finditer(r'\("(\w+)",\s*"', logger_src):
        code_cols.add(match.group(1))

    missing_on_vps = code_cols - vps_col_names - {"id"}
    if missing_on_vps:
        record(FAIL, "schema_vps",
               f"Columns in code but missing on VPS signals.db: {sorted(missing_on_vps)}")
    else:
        record(PASS, "schema_vps",
               f"VPS signals.db has all {len(code_cols)} expected columns")


def check_checksum(instance):
    """Compare md5 of deployed files vs local."""
    py_files = sorted(LOCAL_CODE.glob("*.py"))
    mismatches = []
    for f in py_files:
        if f.name.startswith("test_") or f.name == "pre_deploy_check.py":
            continue
        local_md5, _ = local(f"md5 -q {f}")
        vps_md5 = ssh(f"md5sum {VPS_CODE}/{f.name} 2>/dev/null | cut -d' ' -f1")
        if local_md5 and vps_md5 and local_md5.strip() != vps_md5.strip():
            mismatches.append(f.name)

    if mismatches:
        record(WARN, "checksum",
               f"{len(mismatches)} files differ local vs VPS: {', '.join(mismatches[:8])}")
    else:
        record(PASS, "checksum", "All deployed files match local")


# ========== MAIN ==========

def run_pre(instance):
    print(f"\n{'='*55}")
    print(f"  LAGBOT PRE-DEPLOY CHECK - {instance}")
    print(f"{'='*55}\n")

    check_py_compile()
    check_tests()
    check_schema_contract()
    check_changed_files()
    check_silent_exceptions()
    capture_baseline(instance)

    print_verdict()


def run_post(instance):
    print(f"\n{'='*55}")
    print(f"  LAGBOT POST-DEPLOY CHECK - {instance}")
    print(f"{'='*55}\n")

    check_service_active(instance)
    check_startup_errors(instance)
    check_error_delta(instance)
    check_circuit_breaker(instance)
    check_orphan_trades(instance)
    check_schema_on_vps(instance)
    check_checksum(instance)

    print_verdict()


def run_audit(instance):
    print(f"\n{'='*55}")
    print(f"  LAGBOT FULL AUDIT - {instance}")
    print(f"{'='*55}\n")

    # All pre checks
    check_py_compile()
    check_tests()
    check_schema_contract()
    check_changed_files()
    check_silent_exceptions()

    # All post checks
    check_service_active(instance)
    check_startup_errors(instance)
    check_circuit_breaker(instance)
    check_orphan_trades(instance)
    check_schema_on_vps(instance)
    check_config_drift(instance)
    check_checksum(instance)

    print_verdict()


def print_verdict():
    print(f"\n{'='*55}")
    fails = sum(1 for r in results if r[0] == FAIL)
    warns = sum(1 for r in results if r[0] == WARN)
    passes = sum(1 for r in results if r[0] == PASS)

    if fails > 0:
        print(f"  \033[31mNO-GO\033[0m: {fails} FAIL, {warns} WARN, {passes} PASS")
        print(f"\n  Fix before deploying:")
        for level, check, msg in results:
            if level == FAIL:
                print(f"    - {check}: {msg}")
    elif warns > 0:
        print(f"  \033[33mCAUTION\033[0m: {warns} WARN, {passes} PASS")
    else:
        print(f"  \033[32mGO\033[0m: {passes} PASS")
    print(f"{'='*55}\n")

    return 1 if fails > 0 else 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 polyphemus/pre_deploy_check.py <pre|post|audit> <instance>")
        print("  pre   - Run before deploying (syntax, tests, schema, baseline)")
        print("  post  - Run after deploying (service, errors, circuit breaker)")
        print("  audit - Full audit (all checks)")
        sys.exit(1)

    mode = sys.argv[1]
    instance = sys.argv[2]

    if mode == "pre":
        run_pre(instance)
    elif mode == "post":
        run_post(instance)
    elif mode == "audit":
        run_audit(instance)
    else:
        print(f"Unknown mode: {mode}. Use pre/post/audit.")
        sys.exit(1)

    sys.exit(1 if any(r[0] == FAIL for r in results) else 0)
