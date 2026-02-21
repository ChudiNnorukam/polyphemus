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
    for key, rule in RANGE_CHECKS.items():
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
    findings += check_ranges(env)
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
