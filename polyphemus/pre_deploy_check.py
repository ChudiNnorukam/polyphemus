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

if __package__ in (None, ""):
    import os as _os
    import sys as _sys

    _script_dir = _os.path.dirname(_os.path.abspath(__file__))
    _repo_parent = _os.path.dirname(_script_dir)
    _sys.path = [p for p in _sys.path if p not in ("", _script_dir)]
    _sys.path.insert(0, _repo_parent)

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


def _parse_env_blob(env_out):
    env = {}
    for raw_line in env_out.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip("'\"")
    return env


def _service_start_epoch(instance):
    start = ssh(
        f"systemctl show lagbot@{instance} -p ActiveEnterTimestamp --value",
        timeout=10,
    )
    if not start or "SSH_ERROR" in start or start.strip().lower() == "n/a":
        return None
    epoch = ssh(f"date -d '{start.strip()}' +%s", timeout=10)
    return int(epoch) if epoch.isdigit() else None


def _accumulator_runtime(instance):
    env_out = ssh(f"cat {VPS_INSTANCES}/{instance}/.env 2>/dev/null")
    if "SSH_ERROR" in env_out or not env_out:
        return None, None, None

    env = _parse_env_blob(env_out)
    if env.get("ENABLE_ACCUMULATOR", "").lower() != "true":
        return env, None, None

    port = env.get("DASHBOARD_PORT", "8080")
    payload = ssh(f"curl -fsS http://127.0.0.1:{port}/api/accumulator", timeout=15)
    if not payload or "SSH_ERROR" in payload:
        return env, port, None

    try:
        return env, port, json.loads(payload)
    except json.JSONDecodeError:
        return env, port, None


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
    test_targets = []
    for candidate in (
        LOCAL_CODE / "test_accumulator.py",
        LOCAL_CODE / "tests" / "test_operator_tooling.py",
    ):
        if candidate.exists():
            test_targets.append(f"polyphemus/{candidate.relative_to(LOCAL_CODE)}")
    if not test_targets:
        record(WARN, "tests", "No hardening test files found")
        return

    out, rc = local(
        f"cd {LOCAL_CODE.parent} && python3 -m pytest {' '.join(test_targets)} -q --tb=no 2>&1",
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
    start_epoch = _service_start_epoch(instance)
    if start_epoch is None:
        window = "--since '90 seconds ago'"
    else:
        window = f"--since '@{start_epoch}' --until '@{start_epoch + 90}'"

    errors = ssh(
        f"journalctl -u lagbot@{instance} {window} --no-pager 2>/dev/null"
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
    env, port, accum_stats = _accumulator_runtime(instance)
    max_loss_val = 0.0
    if env:
        try:
            max_loss_val = float(env.get("MAX_DAILY_LOSS", "0") or 0)
        except ValueError:
            max_loss_val = 0.0

    kill_switch_path = (
        env.get("KILL_SWITCH_PATH")
        if env and env.get("KILL_SWITCH_PATH")
        else f"{VPS_INSTANCES}/{instance}/data/KILL_SWITCH"
    )
    kill_switch = ssh(f"test -f {kill_switch_path} && echo exists || true")

    using_accumulator = bool(env and env.get("ENABLE_ACCUMULATOR", "").lower() == "true")
    if using_accumulator:
        if accum_stats is None:
            record(
                FAIL,
                "circuit_breaker",
                f"Accumulator enabled but /api/accumulator unavailable on 127.0.0.1:{port}",
            )
            return
        pnl = float(accum_stats.get("total_pnl", 0.0) or 0.0)
    else:
        daily_pnl = ssh(
            f"sqlite3 /opt/lagbot/instances/{instance}/data/performance.db "
            f"\"SELECT COALESCE(SUM(pnl), 0.0) FROM trades "
            f"WHERE exit_time >= $(python3 -c 'from datetime import datetime,timezone; "
            f"print(int(datetime.now(timezone.utc).replace(hour=0,minute=0,second=0).timestamp()))')\""
        )
        pnl = float(daily_pnl) if daily_pnl.replace("-", "").replace(".", "").isdigit() else 0.0

    issues = []
    if kill_switch.strip() == "exists":
        issues.append(f"KILL_SWITCH file exists at {kill_switch_path}")
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

    # DRY_RUN should match intent
    dr = env.get("DRY_RUN", "true").lower()
    if dr == "true":
        record(INFO, "config_drift", "DRY_RUN=true (no real trades)")

    # Strategy toggles must be explicit and mutually consistent.
    toggle_values = {}
    for key in ["ENABLE_ARB", "ENABLE_ACCUMULATOR", "ENABLE_PAIR_ARB"]:
        val = env.get(key, "").lower()
        if val not in ("true", "false"):
            issues.append(f"{key} missing from .env (defaults may contaminate)")
            continue
        toggle_values[key] = (val == "true")

    if toggle_values.get("ENABLE_ARB") and toggle_values.get("ENABLE_ACCUMULATOR"):
        issues.append("ENABLE_ARB=true and ENABLE_ACCUMULATOR=true (mutually exclusive)")

    if toggle_values.get("ENABLE_ACCUMULATOR"):
        adr = env.get("ACCUM_DRY_RUN", "").lower()
        if adr not in ("true", "false"):
            issues.append("ACCUM_DRY_RUN missing while ENABLE_ACCUMULATOR=true")
        elif adr != dr:
            issues.append(
                f"DRY_RUN={dr} but ACCUM_DRY_RUN={adr} while ENABLE_ACCUMULATOR=true"
            )
        if not env.get("ACCUM_MAX_PAIR_COST"):
            issues.append("ACCUM_MAX_PAIR_COST missing while ENABLE_ACCUMULATOR=true")
        if not env.get("DASHBOARD_PORT"):
            issues.append("DASHBOARD_PORT missing while ENABLE_ACCUMULATOR=true")

    # Signature type must match wallet
    sig = env.get("SIGNATURE_TYPE", "")
    if sig and sig not in ("0", "1", "2"):
        issues.append(f"SIGNATURE_TYPE={sig} (invalid, must be 0/1/2)")

    if issues:
        record(FAIL, "config_drift", " | ".join(issues))
    else:
        record(PASS, "config_drift", "Key config values within safe ranges")


def check_dashboard_api(instance):
    """Verify dashboard APIs are reachable on the configured port."""
    env_out = ssh(f"cat {VPS_INSTANCES}/{instance}/.env 2>/dev/null")
    if "SSH_ERROR" in env_out or not env_out:
        record(WARN, "dashboard_api", "Could not read .env for dashboard port")
        return

    env = {}
    for line in env_out.split("\n"):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, _, val = line.partition("=")
            env[key.strip().upper()] = val.strip().strip("'\"")

    port = env.get("DASHBOARD_PORT", "8080")
    accum_enabled = env.get("ENABLE_ACCUMULATOR", "").lower() == "true"
    api = ssh(f"curl -fsS http://127.0.0.1:{port}/api/accumulator 2>/dev/null", timeout=15)
    if "SSH_ERROR" in api or not api:
        record(FAIL, "dashboard_api", f"/api/accumulator unreachable on 127.0.0.1:{port}")
        return
    if accum_enabled and '"enabled": true' not in api and '"enabled":true' not in api:
        record(FAIL, "dashboard_api", f"Accumulator API reachable on {port} but not enabled")
        return
    record(PASS, "dashboard_api", f"/api/accumulator reachable on 127.0.0.1:{port}")


def check_accumulator_state_storage(instance):
    """Ensure accumulator state is instance-scoped, not using the legacy shared path."""
    env_out = ssh(f"cat {VPS_INSTANCES}/{instance}/.env 2>/dev/null")
    if "SSH_ERROR" in env_out or not env_out:
        record(WARN, "accum_state_storage", "Could not read .env")
        return
    env = _parse_env_blob(env_out)
    enabled = env.get("ENABLE_ACCUMULATOR", "").lower() == "true"
    if not enabled:
        record(INFO, "accum_state_storage", "Accumulator disabled for instance")
        return

    instance_path = f"{VPS_INSTANCES}/{instance}/data/circuit_breaker.json"
    shared_path = "/opt/lagbot/data/circuit_breaker.json"
    instance_exists = ssh(f"test -f {instance_path} && echo yes || echo no")
    shared_exists = ssh(f"test -f {shared_path} && echo yes || echo no")
    if instance_exists.strip() != "yes":
        record(FAIL, "accum_state_storage", f"Missing instance circuit breaker state: {instance_path}")
        return
    if shared_exists.strip() == "yes":
        start_epoch = _service_start_epoch(instance)
        shared_mtime = ssh(f"stat -c %Y {shared_path} 2>/dev/null || echo 0")
        instance_mtime = ssh(f"stat -c %Y {instance_path} 2>/dev/null || echo 0")
        if shared_mtime.isdigit() and instance_mtime.isdigit():
            if start_epoch is not None and int(shared_mtime) >= start_epoch:
                record(
                    FAIL,
                    "accum_state_storage",
                    f"Legacy shared circuit breaker state changed after service start: {shared_path}",
                )
                return
            if int(shared_mtime) >= int(instance_mtime):
                record(
                    WARN,
                    "accum_state_storage",
                    f"Legacy shared circuit breaker state is as new/newer than instance state: {shared_path}",
                )
                return
        record(
            INFO,
            "accum_state_storage",
            f"Instance state active; legacy shared file is stale residue: {shared_path}",
        )
        return
    record(PASS, "accum_state_storage", f"Instance-scoped circuit breaker state active: {instance_path}")


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
    check_config_drift(instance)
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
    check_dashboard_api(instance)
    check_accumulator_state_storage(instance)
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
    check_dashboard_api(instance)
    check_accumulator_state_storage(instance)
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
