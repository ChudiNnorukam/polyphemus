"""Haiku Agent Framework -- lightweight cron agents with human-in-the-loop escalation.

Each agent:
1. Gathers data (SQL, logs, web)
2. Sends to Haiku 4.5 with cached system prompt (~$0.01/run)
3. If finding needs Opus-level analysis: pause, notify human, wait for response
4. Writes findings to state/findings/ for dashboard consumption

Human intervention protocol:
- Agent writes JSON to state/interventions/{agent}_{timestamp}.json
- Sends Telegram notification with suggested /domain-entry-audit prompt
- Sets status=pending, stops execution
- Human runs the Opus skill, pastes results back
- Next cron run picks up the response and incorporates it
"""

import anthropic
import json
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# Paths
AGENTS_DIR = Path("/opt/openclaw/agents")
STATE_DIR = AGENTS_DIR / "state"
INTERVENTIONS_DIR = STATE_DIR / "interventions"
RESPONSES_DIR = STATE_DIR / "responses"
FINDINGS_DIR = STATE_DIR / "findings"

# Ensure dirs exist
for d in [STATE_DIR, INTERVENTIONS_DIR, RESPONSES_DIR, FINDINGS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# API client with prompt caching
_client = None

def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def ask_haiku(
    system_prompt: str,
    user_message: str,
    max_tokens: int = 2048,
) -> str:
    """Call Haiku 4.5 with prompt caching on system prompt."""
    client = get_client()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text


def query_db(db_path: str, sql: str, params: tuple = ()) -> list[dict]:
    """Query a SQLite database, return list of dicts."""
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_journal_logs(service: str, since: str = "24 hours ago", grep: str = "") -> str:
    """Get journalctl logs for a service."""
    cmd = f"journalctl -u {service} --since '{since}' --no-pager"
    if grep:
        cmd += f" | grep -iE '{grep}'"
    cmd += " | tail -50"
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        return result.stdout.strip()
    except Exception as e:
        return f"Error: {e}"


def send_notification(message: str, title: str = "Polyphemus Agent", priority: str = "default") -> bool:
    """Send push notification via ntfy.sh (free, works on Mac + iPhone).

    Setup: install ntfy app on iPhone, subscribe to topic 'polyphemus-ops'.
    On Mac: open https://ntfy.sh/polyphemus-ops in browser, allow notifications.
    """
    topic = os.environ.get("NTFY_TOPIC", "polyphemus-ops")
    import urllib.request
    # Strip markdown for plain text notification
    clean = message.replace("*", "").replace("`", "").replace("#", "")
    url = f"https://ntfy.sh/{topic}"
    req = urllib.request.Request(url, data=clean.encode("utf-8"))
    req.add_header("Title", title)
    req.add_header("Priority", priority)
    req.add_header("Tags", "robot")
    try:
        urllib.request.urlopen(req, timeout=10)
        print(f"[NTFY] Sent: {title}")
        return True
    except Exception as e:
        print(f"[NTFY] Failed: {e}")
        return False


# Backwards compat alias
def send_telegram(message: str, chat_id: Optional[str] = None) -> bool:
    return send_notification(message)


def request_human_intervention(
    agent_name: str,
    finding: str,
    suggested_prompt: str,
    context: str = "",
    urgency: str = "normal",
) -> str:
    """Pause agent and request human intervention via Telegram.

    Returns the intervention ID (filename stem).
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    intervention_id = f"{agent_name}_{ts}"
    payload = {
        "id": intervention_id,
        "agent": agent_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "urgency": urgency,
        "finding": finding,
        "suggested_prompt": suggested_prompt,
        "context": context,
        "response": None,
    }
    path = INTERVENTIONS_DIR / f"{intervention_id}.json"
    path.write_text(json.dumps(payload, indent=2))

    # Notify via Telegram
    emoji = {"critical": "\u26a0\ufe0f", "normal": "\U0001f4cb", "low": "\U0001f4ac"}.get(urgency, "\U0001f4cb")
    msg = (
        f"{emoji} *Agent Intervention Request*\n\n"
        f"*Agent:* `{agent_name}`\n"
        f"*Finding:* {finding}\n\n"
        f"*Suggested prompt:*\n`{suggested_prompt}`\n\n"
        f"Reply with results to continue agent research."
    )
    send_telegram(msg)
    print(f"[INTERVENTION] {intervention_id}: {finding}")
    return intervention_id


def check_pending_intervention(agent_name: str) -> Optional[dict]:
    """Check if this agent has a pending intervention. Returns it if found."""
    for f in sorted(INTERVENTIONS_DIR.glob(f"{agent_name}_*.json")):
        data = json.loads(f.read_text())
        if data.get("status") == "pending":
            return data
    return None


def respond_to_intervention(intervention_id: str, response: str) -> bool:
    """Mark an intervention as responded with the human's input."""
    path = INTERVENTIONS_DIR / f"{intervention_id}.json"
    if not path.exists():
        return False
    data = json.loads(path.read_text())
    data["status"] = "responded"
    data["response"] = response
    data["responded_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(data, indent=2))
    # Move to responses
    dest = RESPONSES_DIR / f"{intervention_id}.json"
    path.rename(dest)
    return True


def write_finding(
    agent_name: str,
    category: str,
    title: str,
    body: str,
    severity: str = "info",
    data: Optional[dict] = None,
) -> str:
    """Write a finding for dashboard consumption. Returns finding ID."""
    ts = datetime.now(timezone.utc)
    finding_id = f"{agent_name}_{ts.strftime('%Y%m%dT%H%M%S')}"
    payload = {
        "id": finding_id,
        "agent": agent_name,
        "category": category,
        "title": title,
        "body": body,
        "severity": severity,
        "timestamp": ts.isoformat(),
        "data": data or {},
    }
    path = FINDINGS_DIR / f"{finding_id}.json"
    path.write_text(json.dumps(payload, indent=2))
    return finding_id


def get_recent_findings(agent_name: Optional[str] = None, limit: int = 20) -> list[dict]:
    """Read recent findings, optionally filtered by agent."""
    findings = []
    for f in sorted(FINDINGS_DIR.glob("*.json"), reverse=True):
        data = json.loads(f.read_text())
        if agent_name and data.get("agent") != agent_name:
            continue
        findings.append(data)
        if len(findings) >= limit:
            break
    return findings
