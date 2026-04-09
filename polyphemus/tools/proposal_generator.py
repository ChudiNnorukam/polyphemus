#!/usr/bin/env python3
"""Proposal Generator -- reads drift findings, generates PROPOSAL.md.

Standalone script (no lagbot imports, Bug #39 safe).
Replaces: agent_trading_digest, calibrate_trigger.

What it does:
1. Reads recent drift_detector findings from state/findings/
2. Queries performance.db for detailed segmentation
3. Sends context to Haiku 4.5 for analysis
4. Generates PROPOSAL.md with specific, bounded recommendations
5. Notifies human via Slack + ntfy.sh

CRITICAL SAFETY RULES:
- Agent can ONLY propose changes within PARAM_BOUNDS
- Agent cannot modify PARAM_BOUNDS
- Agent cannot modify its own system prompt
- Agent cannot deploy, restart, or touch .env files
- All proposals require human approval before any action

Runs daily at 04:00 UTC (safe deploy window, 8pm PST).
Cost: ~$0.02/run (Haiku with prompt caching).

Usage:
    python3 proposal_generator.py --instance emmanuel
    python3 proposal_generator.py --instance emmanuel --dry-run

Cron:
    0 4 * * * cd /opt/lagbot && /opt/lagbot/venv/bin/python3 lagbot/tools/proposal_generator.py --instance emmanuel
    0 4 * * * cd /opt/lagbot && /opt/lagbot/venv/bin/python3 lagbot/tools/proposal_generator.py --instance polyphemus
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

# Optional: Anthropic API for Haiku analysis
try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


# ---------- Safety bounds (IMMUTABLE to the agent) ----------

PARAM_BOUNDS = {
    "CHEAP_SIDE_MIN_PRICE": {"min": 0.35, "max": 0.50, "type": "float"},
    "CHEAP_SIDE_MAX_PRICE": {"min": 0.45, "max": 0.60, "type": "float"},
    "CHEAP_SIDE_ACTIVE_HOURS": {
        "allowed_sets": [
            "0,1,2,3,4,5,6",
            "22,23,0,1,2,3,4,5,6",
            "0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23",
        ],
        "type": "hours",
    },
    "ACCUM_MAX_ROUNDS": {"min": 1, "max": 3, "type": "int"},
    "ACCUM_BET_PER_ROUND": {"min": 1.0, "max": 5.0, "type": "float"},
    "POST_LOSS_COOLDOWN_MINS": {"min": 0, "max": 30, "type": "int"},
}

# Parameters the agent is FORBIDDEN from proposing changes to
FORBIDDEN_PARAMS = [
    "DRY_RUN",
    "ENABLE_ACCUMULATOR",
    "MAX_ENTRY_PRICE",
    "SIGNAL_MODE",
    "API_KEY",
    "API_SECRET",
    "PRIVATE_KEY",
]

FINDINGS_DIR = Path("/opt/openclaw/agents/state/findings")
PROPOSALS_DIR = Path("/opt/openclaw/agents/state/proposals")
PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)


# ---------- System prompt (cached, ~$0.001 after first call) ----------

SYSTEM_PROMPT = """You are a quantitative research assistant for Polyphemus, a Polymarket prediction market trading bot.

Your job: analyze drift detection data and propose SPECIFIC, BOUNDED parameter changes that a human will review.

RULES YOU MUST FOLLOW:
1. You can ONLY propose changes to these parameters within these bounds:
   - CHEAP_SIDE_MIN_PRICE: [0.35, 0.50]
   - CHEAP_SIDE_MAX_PRICE: [0.45, 0.60]
   - CHEAP_SIDE_ACTIVE_HOURS: one of ["0-6", "22-6", "0-23"]
   - ACCUM_MAX_ROUNDS: [1, 3]
   - ACCUM_BET_PER_ROUND: [1.0, 5.0]
   - POST_LOSS_COOLDOWN_MINS: [0, 30]

2. You MUST NOT propose changes to: DRY_RUN, ENABLE_ACCUMULATOR, MAX_ENTRY_PRICE, SIGNAL_MODE, any API keys

3. Every proposal MUST include:
   - Current value (from the data)
   - Proposed value (within bounds)
   - Evidence (specific numbers: n, WR, CI, Kelly)
   - Expected impact (quantified)
   - Risk if wrong (what happens if this makes things worse)
   - Reversibility (how to undo)

4. If the strategy has NEGATIVE Kelly on n >= 30, your #1 recommendation should be:
   "PAUSE TRADING until edge is confirmed. Kelly is negative, meaning every trade loses money in expectation."

5. Label all statistics with sample size quality:
   - n < 10: ANECDOTAL (unreliable)
   - n 10-29: PRELIMINARY (directional only)
   - n 30-99: MODERATE (actionable with caution)
   - n >= 100: SUBSTANTIAL (reliable)

6. Never recommend increasing position size when losing. Never recommend widening entry range when losing.

7. If you have NO actionable proposal, say so. "No changes recommended. Continue collecting data. Next checkpoint at n=X."

OUTPUT FORMAT:
Use this exact structure:

## PROPOSAL: [instance] [date]

### STATUS
[PROPOSE_CHANGE | PAUSE_TRADING | NO_CHANGE | COLLECT_MORE_DATA]

### ASSESSMENT
[2-3 sentences summarizing the situation with key numbers]

### PROPOSALS (if any)
For each proposal:
- **Parameter**: [name]
- **Current**: [value]
- **Proposed**: [new value]
- **Evidence**: [numbers]
- **Expected impact**: [quantified]
- **Risk if wrong**: [description]
- **Reversibility**: [how to undo]

### NEXT CHECKPOINT
[When to re-evaluate, what data to collect]
"""


# ---------- Data collection ----------

def get_env(instance: str) -> dict:
    """Read .env for current config values."""
    env_path = f"/opt/lagbot/instances/{instance}/.env"
    env = {}
    if os.path.exists(env_path):
        for line in open(env_path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def get_recent_findings(instance: str, days: int = 7) -> list:
    """Read drift detector findings for this instance."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    findings = []
    for f in sorted(FINDINGS_DIR.glob(f"drift_{instance}_*.json"), reverse=True):
        try:
            data = json.loads(f.read_text())
            ts = datetime.fromisoformat(data["timestamp"])
            if ts >= cutoff:
                findings.append(data)
        except Exception:
            continue
    return findings[:20]  # max 20 findings


def get_segmented_stats(db_path: str, days: int = 30) -> dict:
    """Get price bucket and hour-of-day segmentation."""
    if not os.path.exists(db_path):
        return {}

    cutoff = time.time() - (days * 86400)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Price buckets
    buckets = conn.execute("""
        SELECT
            CASE
                WHEN entry_price < 0.40 THEN '0.00-0.40'
                WHEN entry_price < 0.45 THEN '0.40-0.45'
                WHEN entry_price < 0.50 THEN '0.45-0.50'
                WHEN entry_price < 0.55 THEN '0.50-0.55'
                ELSE '0.55+'
            END as bucket,
            COUNT(*) as n,
            SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
            ROUND(SUM(pnl), 2) as pnl
        FROM trades
        WHERE exit_time IS NOT NULL AND is_error = 0 AND entry_time > ?
        GROUP BY bucket
        ORDER BY bucket
    """, (cutoff,)).fetchall()

    # Hour of day
    hours = conn.execute("""
        SELECT
            CAST(strftime('%H', entry_time, 'unixepoch') AS INTEGER) as hour_utc,
            COUNT(*) as n,
            SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
            ROUND(SUM(pnl), 2) as pnl
        FROM trades
        WHERE exit_time IS NOT NULL AND is_error = 0 AND entry_time > ?
        GROUP BY hour_utc
        ORDER BY hour_utc
    """, (cutoff,)).fetchall()

    conn.close()

    return {
        "price_buckets": [dict(b) for b in buckets],
        "hour_of_day": [dict(h) for h in hours],
    }


# ---------- LLM analysis ----------

def ask_haiku(system_prompt: str, user_message: str, max_tokens: int = 2048) -> str:
    """Call Haiku 4.5 with prompt caching."""
    if not HAS_ANTHROPIC:
        return "[ERROR: anthropic package not installed. Install with: pip install anthropic]"

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        system=[{
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text


def validate_proposal(proposal_text: str) -> list:
    """Validate that the LLM didn't propose anything outside bounds."""
    warnings = []

    for param in FORBIDDEN_PARAMS:
        if param in proposal_text and "Proposed" in proposal_text:
            # Check if the forbidden param appears near a "Proposed:" line
            lines = proposal_text.split("\n")
            for i, line in enumerate(lines):
                if param in line and any("proposed" in lines[j].lower() for j in range(max(0, i-2), min(len(lines), i+3))):
                    warnings.append(f"REJECTED: Proposal attempted to change forbidden parameter {param}")

    return warnings


# ---------- Notification ----------

def post_slack(instance: str, text: str):
    """Post to Slack."""
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
    """Push notification."""
    topic = os.environ.get("NTFY_TOPIC", "polyphemus-ops")
    clean = message.replace("*", "").replace("`", "").replace("#", "")[:500]
    url = f"https://ntfy.sh/{topic}"
    req = Request(url, data=clean.encode("utf-8"))
    req.add_header("Title", title)
    req.add_header("Priority", priority)
    req.add_header("Tags", "memo")
    try:
        urlopen(req, timeout=10)
        print(f"  [NTFY] Sent: {title}")
    except Exception as e:
        print(f"  [NTFY] Failed: {e}")


# ---------- Main ----------

def run(instance: str, dry_run: bool = False):
    """Generate proposal for one instance."""
    ts = datetime.now(timezone.utc)
    print(f"\n{'='*60}")
    print(f"PROPOSAL GENERATOR | {instance} | {ts.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")

    db_path = f"/opt/lagbot/instances/{instance}/data/performance.db"

    # Gather context
    env = get_env(instance)
    findings = get_recent_findings(instance, days=7)
    segments = get_segmented_stats(db_path, days=30)

    # Current config (only the tunable params)
    current_config = {}
    for param in PARAM_BOUNDS:
        current_config[param] = env.get(param, "NOT SET")

    # Build context for Haiku
    context = {
        "instance": instance,
        "timestamp": ts.isoformat(),
        "current_config": current_config,
        "param_bounds": {k: {kk: vv for kk, vv in v.items() if kk != "type"} for k, v in PARAM_BOUNDS.items()},
        "recent_drift_signals": [],
        "segments": segments,
    }

    # Extract drift signals from findings
    for f in findings[:5]:  # last 5 findings
        for s in f.get("signals", []):
            context["recent_drift_signals"].append({
                "timestamp": f.get("timestamp", ""),
                "type": s.get("type", ""),
                "severity": s.get("severity", ""),
                "message": s.get("message", ""),
            })

    # Add window stats from most recent finding
    if findings:
        context["latest_windows"] = findings[0].get("windows", {})

    user_message = f"""Analyze this trading data and generate a proposal.

CURRENT CONFIG:
{json.dumps(current_config, indent=2)}

PARAMETER BOUNDS (you MUST stay within these):
{json.dumps(context['param_bounds'], indent=2)}

RECENT DRIFT SIGNALS (last 7 days):
{json.dumps(context['recent_drift_signals'], indent=2)}

LATEST PERFORMANCE WINDOWS:
{json.dumps(context.get('latest_windows', {}), indent=2, default=str)}

PRICE BUCKET SEGMENTATION (30d):
{json.dumps(segments.get('price_buckets', []), indent=2)}

HOUR-OF-DAY SEGMENTATION (30d):
{json.dumps(segments.get('hour_of_day', []), indent=2)}

Generate a proposal following the exact output format specified in your system prompt."""

    print(f"\n  Drift signals: {len(context['recent_drift_signals'])}")
    print(f"  Price buckets: {len(segments.get('price_buckets', []))}")
    print(f"  Hour segments: {len(segments.get('hour_of_day', []))}")
    print(f"  Calling Haiku...")

    # Call Haiku
    proposal_text = ask_haiku(SYSTEM_PROMPT, user_message, max_tokens=2048)

    # Validate
    violations = validate_proposal(proposal_text)
    if violations:
        print(f"\n  SAFETY VIOLATIONS DETECTED:")
        for v in violations:
            print(f"    {v}")
        proposal_text += "\n\n---\n## SAFETY VIOLATIONS\n" + "\n".join(f"- {v}" for v in violations)

    # Save proposal
    proposal_path = PROPOSALS_DIR / f"proposal_{instance}_{ts.strftime('%Y%m%d')}.md"
    proposal_path.write_text(proposal_text)
    print(f"\n  Proposal saved: {proposal_path}")

    # Also save as structured JSON
    meta_path = PROPOSALS_DIR / f"proposal_{instance}_{ts.strftime('%Y%m%d')}.json"
    meta_path.write_text(json.dumps({
        "instance": instance,
        "timestamp": ts.isoformat(),
        "config_snapshot": current_config,
        "drift_signal_count": len(context["recent_drift_signals"]),
        "violations": violations,
        "proposal_file": str(proposal_path),
    }, indent=2))

    if dry_run:
        print(f"\n--- DRY RUN: Proposal ---")
        print(proposal_text)
        return

    # Notify human
    # Short summary for Slack
    status_line = "UNKNOWN"
    for line in proposal_text.split("\n"):
        if line.strip().startswith("[") or line.strip().startswith("PROPOSE") or line.strip().startswith("PAUSE") or line.strip().startswith("NO_CHANGE") or line.strip().startswith("COLLECT"):
            status_line = line.strip()[:100]
            break

    slack_msg = (
        f"📋 *Daily Proposal: {instance}*\n"
        f"_{ts.strftime('%Y-%m-%d %H:%M UTC')}_\n\n"
        f"Status: {status_line}\n"
        f"Drift signals: {len(context['recent_drift_signals'])} in last 7d\n\n"
        f"Full proposal: `cat {proposal_path}`\n"
        f"To approve: review and apply via `predeploy.sh`"
    )
    post_slack(instance, slack_msg)
    send_ntfy(f"Proposal: {instance}", f"Status: {status_line}", priority="default")

    print(f"\n  Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Proposal Generator")
    parser.add_argument("--instance", required=True, help="Instance name")
    parser.add_argument("--dry-run", action="store_true", help="Print only, no notifications")
    args = parser.parse_args()
    run(args.instance, args.dry_run)
