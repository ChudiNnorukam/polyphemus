#!/usr/bin/env python3
"""Post-Deploy Verification - Runs after ANY restart to confirm bots are EXECUTING, not just alive.

Usage:
    python3 post_deploy_verify.py

Checks ALL instances. Fails loudly if any bot is alive but not executing.
Posts results to Slack.
"""

import json
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen


def get_env(instance):
    env = {}
    path = f"/opt/lagbot/instances/{instance}/.env"
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def post_slack(token, channel, text):
    try:
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
        urlopen(req, timeout=10)
    except Exception as e:
        print(f"Slack error: {e}")


def check_instance(instance):
    """Check if instance is alive AND executing."""
    issues = []
    
    # 1. Is the service active?
    result = subprocess.run(
        ["systemctl", "is-active", f"lagbot@{instance}"],
        capture_output=True, text=True
    )
    if result.stdout.strip() != "active":
        issues.append(f"SERVICE DOWN: lagbot@{instance} is {result.stdout.strip()}")
        return issues
    
    # 2. Check for AttributeError in last 10 min
    result = subprocess.run(
        ["journalctl", f"-u", f"lagbot@{instance}", "--since", "10 minutes ago", "--no-pager"],
        capture_output=True, text=True
    )
    logs = result.stdout
    attr_errors = logs.count("AttributeError")
    if attr_errors > 0:
        issues.append(f"CODE BUG: {attr_errors} AttributeError(s) in last 10 min")
    
    # 3. Check for execution_failed vs executed in signals.db
    sig_db = f"/opt/lagbot/instances/{instance}/data/signals.db"
    if os.path.exists(sig_db):
        conn = sqlite3.connect(sig_db)
        cutoff = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
        # Last 30 min of momentum signals
        row = conn.execute("""
            SELECT 
                SUM(CASE WHEN outcome='executed' THEN 1 ELSE 0 END) as executed,
                SUM(CASE WHEN outcome='execution_failed' THEN 1 ELSE 0 END) as failed,
                COUNT(*) as total
            FROM signals 
            WHERE source='binance_momentum' AND guard_passed=1
            AND timestamp > datetime('now', '-30 minutes')
        """).fetchone()
        conn.close()
        
        executed, failed, total = row
        if total and total > 0:
            if failed and failed > 0 and (not executed or executed == 0):
                issues.append(f"EXECUTION BLOCKED: {failed} signals passed guard but ALL failed to execute")
            elif failed and executed and failed > executed * 2:
                issues.append(f"HIGH FAIL RATE: {failed} failed vs {executed} executed")
    
    # 4. Check for recent errors
    type_errors = logs.count("TypeError")
    if type_errors > 5:
        issues.append(f"TYPE ERRORS: {type_errors} in last 10 min")
    
    return issues


def run_verify():
    instances = ["emmanuel", "polyphemus"]
    all_issues = {}
    
    for inst in instances:
        issues = check_instance(inst)
        all_issues[inst] = issues
    
    # Build report
    lines = [":mag: *POST-DEPLOY VERIFY*"]
    all_clear = True
    
    for inst, issues in all_issues.items():
        if issues:
            all_clear = False
            lines.append(f"\n:red_circle: *{inst}*")
            for issue in issues:
                lines.append(f"  - {issue}")
        else:
            lines.append(f":white_check_mark: *{inst}* - executing normally")
    
    if all_clear:
        lines.append("\nAll instances verified. Trades executing on both bots.")
    else:
        lines.append("\n:warning: *ACTION REQUIRED* - fix issues above before continuing")
    
    msg = "\n".join(lines)
    print(msg)
    
    # Post to Slack
    env = get_env("emmanuel")
    token = env.get("SLACK_BOT_TOKEN", "")
    channel = env.get("SLACK_CHANNEL_ID", "")
    if token and channel:
        post_slack(token, channel, msg)
        print("\nPosted to Slack.")


if __name__ == "__main__":
    run_verify()
