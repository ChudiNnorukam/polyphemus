"""Feature 7: Audit log - track all control actions.

Acceptance criteria:
- GET /api/audit-log returns recent audit entries
- Entries have timestamp, action, bot, details, source_ip
- Auto-populated by control actions (stop/start/restart/kill/config/deploy)
- Respects limit param
- Filter by action type
- Audit failures are non-blocking (action proceeds, failure logged)
- Auth required
"""

import pytest
import sqlite3
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.asyncio


async def test_audit_log_returns_entries(client, auth_headers, mock_bots, mock_subprocess, mock_audit_db):
    """After a control action, audit log should have entries."""
    # Perform an action that creates an audit entry
    await client.post("/api/control/stop?bot=testbot", headers=auth_headers)

    resp = await client.get("/api/audit-log", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "entries" in data
    assert isinstance(data["entries"], list)


async def test_audit_entry_has_required_fields(client, auth_headers, mock_bots, mock_subprocess, mock_audit_db):
    """Each audit entry must have timestamp, action, bot, details."""
    await client.post("/api/control/stop?bot=testbot", headers=auth_headers)

    resp = await client.get("/api/audit-log?limit=1", headers=auth_headers)
    data = resp.json()
    entry = data["entries"][0]
    assert "timestamp" in entry
    assert "action" in entry
    assert "bot" in entry
    assert "details" in entry or "detail" in entry


async def test_audit_log_respects_limit(client, auth_headers, mock_bots, mock_subprocess, mock_audit_db):
    """Limit param caps number of returned entries."""
    # Create multiple entries
    for _ in range(5):
        await client.post("/api/control/stop?bot=testbot", headers=auth_headers)

    resp = await client.get("/api/audit-log?limit=2", headers=auth_headers)
    data = resp.json()
    assert len(data["entries"]) <= 2


async def test_audit_log_filter_by_action(client, auth_headers, mock_bots, mock_subprocess, mock_audit_db):
    """Filter entries by action type."""
    await client.post("/api/control/stop?bot=testbot", headers=auth_headers)
    await client.post("/api/control/start?bot=testbot", headers=auth_headers)

    resp = await client.get("/api/audit-log?action=stop", headers=auth_headers)
    data = resp.json()
    for entry in data["entries"]:
        assert entry["action"] == "stop"


async def test_audit_log_populated_by_restart(client, auth_headers, mock_bots, mock_subprocess, mock_audit_db):
    """Restart action should also create audit entry."""
    await client.post("/api/control/restart?bot=testbot", headers=auth_headers)

    resp = await client.get("/api/audit-log?action=restart", headers=auth_headers)
    data = resp.json()
    assert len(data["entries"]) >= 1


async def test_audit_log_populated_by_config_change(client, auth_headers, mock_bots, mock_subprocess, mock_audit_db):
    """Config update should create audit entry."""
    await client.post("/api/config?bot=testbot&key=DRY_RUN&value=false", headers=auth_headers)

    resp = await client.get("/api/audit-log?action=config_update", headers=auth_headers)
    data = resp.json()
    assert len(data["entries"]) >= 1


async def test_audit_log_populated_by_kill_switch(client, auth_headers, mock_bots, mock_subprocess, mock_audit_db):
    """Kill switch toggle should create audit entry."""
    await client.post("/api/control/kill?bot=testbot", headers=auth_headers)

    resp = await client.get("/api/audit-log?action=kill_switch", headers=auth_headers)
    data = resp.json()
    assert len(data["entries"]) >= 1


async def test_audit_failure_does_not_block_action(client, auth_headers, mock_bots, mock_subprocess):
    """If audit DB write fails, the control action should still succeed.

    This is critical for the 'don't lock yourself out' principle.
    A broken audit log should never prevent an emergency stop.
    """
    # Point audit DB to an unwritable location
    with patch("api.AUDIT_DB_PATH", "/nonexistent/path/audit.db"):
        resp = await client.post("/api/control/stop?bot=testbot", headers=auth_headers)
        # Action should still succeed even though audit write failed
        assert resp.status_code == 200


async def test_audit_log_requires_auth(client, bad_auth_headers):
    resp = await client.get("/api/audit-log", headers=bad_auth_headers)
    assert resp.status_code == 401


async def test_audit_log_ordered_newest_first(client, auth_headers, mock_bots, mock_subprocess, mock_audit_db):
    """Entries should be ordered by timestamp descending."""
    await client.post("/api/control/stop?bot=testbot", headers=auth_headers)
    await client.post("/api/control/start?bot=testbot", headers=auth_headers)

    resp = await client.get("/api/audit-log?limit=10", headers=auth_headers)
    data = resp.json()
    entries = data["entries"]
    if len(entries) >= 2:
        assert entries[0]["timestamp"] >= entries[1]["timestamp"]
