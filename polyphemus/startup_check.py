"""Config drift detector — runs at bot startup before any trading subsystem loads.

Stdlib only. No polyphemus imports. Prevents config-drift losses like Bug #48/$43.
Exit codes: 0=clean, 1=warn-only, 2=critical (halts bot).
Standalone: python -m polyphemus.startup_check
"""

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Range checks — hardcoded from bug post-mortems. A specific value is never
# right; a safe range encodes what we learned the hard way.
# ---------------------------------------------------------------------------
RANGE_CHECKS = {
    # --- Accumulator params (hardcoded from bug post-mortems) ---
    "ACCUM_REPRICE_LIMIT": {
        "min": 5, "max": 50, "severity": "CRITICAL",
        "bug": "#48/#47: value=1 caused 34 unwinds, -$43",
    },
    "ACCUM_HEDGE_DEADLINE_SECS": {
        "min": 60, "max": 600, "severity": "CRITICAL",
        "bug": "#48: value=8 caused overnight orphan unwinds, -$43.32",
    },
    "ACCUM_CAPITAL_PCT": {
        "min": 0.01, "max": 0.60, "severity": "CRITICAL",
        "bug": ">0.60 raises ValueError at startup; >0.30 = dangerous debug cost",
    },
    # --- Signal bot params (latency arb strategy) ---
    "MOMENTUM_TRIGGER_PCT": {
        "min": 0.001, "max": 0.020, "severity": "WARN",
        "bug": "30s/0.15% produces ZERO signals; 60s/0.3% = 62% in-range (MEMORY). Default 0.003.",
    },
    "MOMENTUM_WINDOW_SECS": {
        "min": 30, "max": 180, "severity": "WARN",
        "bug": "too short=noise, too long=stale signals. Confirmed 60s optimal for BTC 5m.",
    },
    "BASE_BET_PCT": {
        "min": 0.01, "max": 0.50, "severity": "WARN",
        "bug": ">0.50 is over-Kelly even at 75% WR; <0.01 produces near-zero PnL on small balance.",
    },
    "MAX_OPEN_POSITIONS": {
        "min": 1, "max": 10, "severity": "WARN",
        "bug": ">10 = correlated cluster wipeout risk; 0 = no trades.",
    },
}

# DRY_RUN gets a standalone warn check (not a range — just a reminder)
DRY_RUN_WARN = "You are trading LIVE. Confirm this is intentional."

# ---------------------------------------------------------------------------
# Instance isolation — keys whose values MUST contain the instance name.
# Born from: Mar 21 2026, LAGBOT_DATA_DIR pointed to polyphemus/data on both
# instances. Emmanuel wrote trades to Polyphemus's DB for days. $55 of profit
# was masked, all analytics were polluted.
# ---------------------------------------------------------------------------
INSTANCE_PATH_KEYS = [
    "LAGBOT_DATA_DIR",
    "KILL_SWITCH_PATH",
]

# ---------------------------------------------------------------------------
# Required keys — MUST be present in .env or os.environ. Missing = CRITICAL.
# Born from: profit_target=0.0 (silent default inversion), ASSET_FILTER missing
# (trades all assets when only BTC intended).
# ---------------------------------------------------------------------------
REQUIRED_KEYS = [
    "INSTANCE_NAME", "LAGBOT_DATA_DIR", "PRIVATE_KEY", "WALLET_ADDRESS",
    "CLOB_API_KEY", "DRY_RUN", "ASSET_FILTER",
]

# ---------------------------------------------------------------------------
# Cross-field validation — catches impossible value combinations.
# Born from: MIN_ENTRY_PRICE > MAX_ENTRY_PRICE after a bulk edit.
# ---------------------------------------------------------------------------
CROSS_FIELD_CHECKS = [
    ("MIN_ENTRY_PRICE", "<", "MAX_ENTRY_PRICE",
     "CRITICAL", "min entry must be below max entry"),
    ("SNIPE_MIN_ENTRY_PRICE", "<", "SNIPE_MAX_ENTRY_PRICE",
     "WARN", "snipe min must be below snipe max"),
    ("SNIPE_15M_MIN_ENTRY_PRICE", "<", "SNIPE_15M_MAX_ENTRY_PRICE",
     "WARN", "15m snipe min must be below 15m snipe max"),
]

# ---------------------------------------------------------------------------
# Extended type+bounds checks — covers keys that have caused bugs but aren't
# in the original RANGE_CHECKS (which focused on accumulator/momentum).
# ---------------------------------------------------------------------------
EXTENDED_CHECKS = {
    "MIN_ENTRY_PRICE": {
        "min": 0.01, "max": 0.99, "severity": "CRITICAL",
        "bug": "0.50 allowed coin-flip entries with no edge (Mar 21 2026, -$62 on 36 trades).",
    },
    "MAX_ENTRY_PRICE": {
        "min": 0.01, "max": 0.99, "severity": "CRITICAL",
        "bug": "Must be valid probability. >0.99 = impossible fill.",
    },
    "ENTRY_DELAY_SECS": {
        "min": 0.0, "max": 10.0, "severity": "WARN",
        "bug": ">10s delay in a latency arb = stale entry. 2.0 was too slow (Mar 21).",
    },
    "MAX_BET": {
        "min": 1.0, "max": 1000.0, "severity": "WARN",
        "bug": "<1 = below CLOB minimum. >1000 = dangerous on small balance.",
    },
    "MID_PRICE_STOP_PCT": {
        "min": 0.01, "max": 0.50, "severity": "WARN",
        "bug": "Only relevant if MID_PRICE_STOP_ENABLED=true. 0% = instant stop, >50% = never fires.",
    },
    "POST_LOSS_COOLDOWN_MINS": {
        "min": 0, "max": 120, "severity": "WARN",
        "bug": ">120min = blocks trading for 2+ hours after one loss.",
    },
}

# ANSI colors (fall back gracefully if terminal doesn't support them)
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
RESET  = "\033[0m"
BOLD   = "\033[1m"
LINE   = "=" * 50


@dataclass
class Finding:
    key: str
    live_value: str
    expected: str      # range string like "[5, 50]" or exact value
    severity: str      # "OK" | "WARN" | "CRITICAL"
    message: str


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_env(path: Path) -> dict:
    """Read a .env file and return {KEY: VALUE} dict (strips quotes, ignores comments)."""
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'\"")
        env[key] = val
    return env


def load_expected(path: Path) -> dict:
    """Load config_expected.json. Returns empty dict if missing (non-blocking)."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


# ---------------------------------------------------------------------------
# Build check (stdlib py_compile — catches syntax errors before they crash live)
# ---------------------------------------------------------------------------

def check_build(package_dir: Optional[Path] = None) -> list:
    """Compile all .py files in package_dir. Returns a single Finding."""
    import py_compile
    if package_dir is None:
        package_dir = Path(__file__).parent
    py_files = sorted(package_dir.glob("*.py"))
    errors = []
    for f in py_files:
        try:
            py_compile.compile(str(f), doraise=True)
        except py_compile.PyCompileError as e:
            errors.append(str(e))
    if errors:
        return [Finding(
            key="BUILD",
            live_value=f"{len(errors)} error(s)",
            expected="all .py files compile clean",
            severity="CRITICAL",
            message=" | ".join(errors),
        )]
    return [Finding(
        key="BUILD",
        live_value=f"{len(py_files)} files OK",
        expected="all .py files compile clean",
        severity="OK",
        message="",
    )]


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_ranges(env: dict) -> list:
    findings = []
    accum_enabled = (env.get("ENABLE_ACCUMULATOR") or os.environ.get("ENABLE_ACCUMULATOR", "false")).lower() == "true"
    for key, rule in RANGE_CHECKS.items():
        if key.startswith("ACCUM_") and not accum_enabled:
            continue
        raw = env.get(key) or os.environ.get(key)
        if raw is None:
            findings.append(Finding(
                key=key,
                live_value="<not set>",
                expected=f"[{rule['min']}, {rule['max']}]",
                severity="WARN",
                message=f"{key} not found in .env — using code default. Verify it is safe.",
            ))
            continue
        try:
            val = float(raw)
        except ValueError:
            findings.append(Finding(
                key=key,
                live_value=raw,
                expected=f"[{rule['min']}, {rule['max']}]",
                severity="CRITICAL",
                message=f"{key}={raw!r} is not numeric",
            ))
            continue

        if rule["min"] <= val <= rule["max"]:
            findings.append(Finding(
                key=key,
                live_value=raw,
                expected=f"[{rule['min']}, {rule['max']}]",
                severity="OK",
                message="",
            ))
        else:
            findings.append(Finding(
                key=key,
                live_value=raw,
                expected=f"[{rule['min']}, {rule['max']}]",
                severity=rule["severity"],
                message=f"{key}={val} is outside safe range [{rule['min']}, {rule['max']}]. Bug: {rule['bug']}",
            ))

    # DRY_RUN warn
    dry = (env.get("DRY_RUN") or os.environ.get("DRY_RUN", "true")).lower()
    findings.append(Finding(
        key="DRY_RUN",
        live_value=dry,
        expected="true (safe) or false (live)",
        severity="WARN" if dry == "false" else "OK",
        message=DRY_RUN_WARN if dry == "false" else "",
    ))

    return findings


def check_instance_isolation(env: dict) -> list:
    """Validate instance-specific paths contain the instance name.

    Prevents the class of bug where one instance silently writes to another's
    database, kill switch, or data directory.
    """
    findings = []
    instance_name = env.get("INSTANCE_NAME") or os.environ.get("INSTANCE_NAME", "")
    if not instance_name:
        findings.append(Finding(
            key="INSTANCE_NAME",
            live_value="<not set>",
            expected="must be set",
            severity="CRITICAL",
            message="INSTANCE_NAME not found in .env or environment. Cannot validate instance isolation.",
        ))
        return findings

    for key in INSTANCE_PATH_KEYS:
        val = env.get(key) or os.environ.get(key, "")
        if not val:
            findings.append(Finding(
                key=key,
                live_value="<not set>",
                expected=f"path containing '{instance_name}'",
                severity="CRITICAL",
                message=f"{key} not set. Instance isolation cannot be verified.",
            ))
        elif instance_name not in val:
            findings.append(Finding(
                key=key,
                live_value=val,
                expected=f"must contain '{instance_name}'",
                severity="CRITICAL",
                message=(
                    f"{key}='{val}' does not contain instance name '{instance_name}'. "
                    f"This instance may be reading/writing another instance's data! "
                    f"Bug: Mar 21 2026 - emmanuel wrote to polyphemus/data for days."
                ),
            ))
        else:
            findings.append(Finding(
                key=key,
                live_value=val,
                expected=f"contains '{instance_name}'",
                severity="OK",
                message="",
            ))

    # Check data dir exists and is writable
    data_dir = env.get("LAGBOT_DATA_DIR") or os.environ.get("LAGBOT_DATA_DIR", "")
    if data_dir:
        data_path = Path(data_dir)
        if not data_path.exists():
            findings.append(Finding(
                key="LAGBOT_DATA_DIR_EXISTS",
                live_value=data_dir,
                expected="directory must exist",
                severity="CRITICAL",
                message=f"Data directory '{data_dir}' does not exist.",
            ))
        elif not os.access(data_dir, os.W_OK):
            findings.append(Finding(
                key="LAGBOT_DATA_DIR_WRITABLE",
                live_value=data_dir,
                expected="directory must be writable",
                severity="CRITICAL",
                message=f"Data directory '{data_dir}' is not writable.",
            ))
        else:
            findings.append(Finding(
                key="LAGBOT_DATA_DIR_WRITABLE",
                live_value=data_dir,
                expected="writable",
                severity="OK",
                message="",
            ))

    return findings


def check_required_keys(env: dict) -> list:
    """Verify all required keys are present. Missing required key = CRITICAL."""
    findings = []
    for key in REQUIRED_KEYS:
        val = env.get(key) or os.environ.get(key)
        if not val:
            findings.append(Finding(
                key=key,
                live_value="<missing>",
                expected="must be set",
                severity="CRITICAL",
                message=f"{key} is required but not found in .env or environment.",
            ))
        elif val == "__CHANGE_ME__":
            findings.append(Finding(
                key=key,
                live_value="__CHANGE_ME__",
                expected="actual value",
                severity="CRITICAL",
                message=f"{key} still has template placeholder. Set it before starting.",
            ))
    return findings


def check_extended_ranges(env: dict) -> list:
    """Check extended type+bounds for keys not in the original RANGE_CHECKS."""
    findings = []
    for key, rule in EXTENDED_CHECKS.items():
        raw = env.get(key) or os.environ.get(key)
        if raw is None:
            continue  # optional keys — only validate if present
        try:
            val = float(raw)
        except ValueError:
            findings.append(Finding(
                key=key, live_value=raw,
                expected=f"[{rule['min']}, {rule['max']}]",
                severity="CRITICAL",
                message=f"{key}={raw!r} is not numeric",
            ))
            continue
        if not (rule["min"] <= val <= rule["max"]):
            findings.append(Finding(
                key=key, live_value=raw,
                expected=f"[{rule['min']}, {rule['max']}]",
                severity=rule["severity"],
                message=f"{key}={val} outside safe range. Bug: {rule['bug']}",
            ))
    return findings


def check_cross_fields(env: dict) -> list:
    """Validate cross-field constraints (e.g., min < max)."""
    findings = []
    for key_a, op, key_b, severity, msg in CROSS_FIELD_CHECKS:
        raw_a = env.get(key_a) or os.environ.get(key_a)
        raw_b = env.get(key_b) or os.environ.get(key_b)
        if raw_a is None or raw_b is None:
            continue  # skip if either key is absent
        try:
            val_a, val_b = float(raw_a), float(raw_b)
        except ValueError:
            continue
        violated = False
        if op == "<" and not (val_a < val_b):
            violated = True
        elif op == "<=" and not (val_a <= val_b):
            violated = True
        if violated:
            findings.append(Finding(
                key=f"{key_a} vs {key_b}",
                live_value=f"{raw_a} vs {raw_b}",
                expected=f"{key_a} {op} {key_b}",
                severity=severity,
                message=f"{msg}: {key_a}={raw_a}, {key_b}={raw_b}",
            ))
    return findings


def check_snapshot(env: dict, expected: dict) -> list:
    """Diff live .env against config_expected.json warn-tier snapshot."""
    findings = []
    warn_snapshot = expected.get("warn", {})
    for key, exp_val in warn_snapshot.items():
        live = env.get(key) or os.environ.get(key)
        if live is None:
            findings.append(Finding(
                key=key,
                live_value="<not set>",
                expected=exp_val,
                severity="WARN",
                message=f"{key} missing from .env; expected {exp_val!r}",
            ))
        elif live.strip().lower() != exp_val.strip().lower():
            findings.append(Finding(
                key=key,
                live_value=live,
                expected=exp_val,
                severity="WARN",
                message=f"{key} drifted: live={live!r} expected={exp_val!r}. Update config_expected.json if intentional.",
            ))
        else:
            findings.append(Finding(
                key=key,
                live_value=live,
                expected=exp_val,
                severity="OK",
                message="",
            ))
    return findings


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _icon(severity: str) -> str:
    if severity == "OK":
        return f"{GREEN}✓{RESET}"
    if severity == "WARN":
        return f"{YELLOW}⚠{RESET}"
    return f"{RED}✗{RESET}"


def format_report(findings: list) -> str:
    lines = [
        "",
        f"{BOLD}{LINE}",
        "  CONFIG DRIFT CHECK — Polyphemus Startup",
        f"{LINE}{RESET}",
    ]
    for f in findings:
        if f.severity == "OK":
            lines.append(f"  {_icon(f.severity)} {f.key}={f.live_value}  [range/expected: {f.expected}]")
        else:
            lines.append(f"  {_icon(f.severity)} {f.key}={f.live_value}  [{f.severity}: {f.message}]")

    n_critical = sum(1 for f in findings if f.severity == "CRITICAL")
    n_warn = sum(1 for f in findings if f.severity == "WARN")

    lines.append(f"{BOLD}{LINE}{RESET}")
    if n_critical == 0 and n_warn == 0:
        lines.append(f"  {GREEN}RESULT: All checks passed.{RESET}")
    else:
        parts = []
        if n_critical:
            parts.append(f"{RED}{n_critical} CRITICAL{RESET}")
        if n_warn:
            parts.append(f"{YELLOW}{n_warn} WARN{RESET}")
        lines.append(f"  RESULT: {', '.join(parts)}")
        if n_critical:
            lines.append(f"  {RED}Bot startup HALTED. Fix .env or config_expected.json.{RESET}")
    lines.append(f"{BOLD}{LINE}{RESET}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def run_check(
    env_path: Optional[Path] = None,
    expected_path: Optional[Path] = None,
    halt_on_critical: bool = True,
) -> int:
    """Run all checks. Returns exit code (0/1/2). Calls sys.exit(2) if critical and halt_on_critical."""
    if env_path is None:
        env_path = Path(__file__).parent / ".env"
    if expected_path is None:
        expected_path = Path(__file__).parent / "config_expected.json"

    env = parse_env(env_path)
    expected = load_expected(expected_path)

    if not expected:
        sys.stderr.write(
            f"{YELLOW}[startup_check] config_expected.json not found at {expected_path} — snapshot checks skipped.{RESET}\n"
        )

    findings = check_build(env_path.parent if env_path and env_path.parent.exists() else None)
    findings += check_instance_isolation(env)
    findings += check_required_keys(env)
    findings += check_ranges(env)
    findings += check_extended_ranges(env)
    findings += check_cross_fields(env)
    findings += check_snapshot(env, expected)

    report = format_report(findings)
    sys.stdout.write(report)

    n_critical = sum(1 for f in findings if f.severity == "CRITICAL")
    n_warn = sum(1 for f in findings if f.severity == "WARN")

    if n_critical > 0:
        if halt_on_critical:
            sys.exit(2)
        return 2
    if n_warn > 0:
        return 1
    return 0


def main():
    """CLI entrypoint: python -m polyphemus.startup_check"""
    import argparse
    parser = argparse.ArgumentParser(description="Polyphemus config drift detector")
    parser.add_argument("--env", type=Path, default=None, help="Path to .env file")
    parser.add_argument("--expected", type=Path, default=None, help="Path to config_expected.json")
    parser.add_argument("--no-halt", action="store_true", help="Print findings but do not sys.exit on CRITICAL")
    args = parser.parse_args()

    code = run_check(
        env_path=args.env,
        expected_path=args.expected,
        halt_on_critical=not args.no_halt,
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
