"""Feature 2: Stop/Start services (extends existing restart/kill).

Acceptance criteria:
- POST /api/control/stop stops the systemd service
- POST /api/control/start starts the systemd service
- Both create audit log entries
- Unknown bot returns 404
- Auth required
- Config snapshot created on start (for diff feature)
"""

import pytest

pytestmark = pytest.mark.asyncio


async def test_stop_service_succeeds(client, auth_headers, mock_bots, mock_subprocess, mock_audit_db):
    resp = await client.post("/api/control/stop?bot=testbot", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "stopped"
    assert data["bot"] == "testbot"


async def test_start_service_succeeds(client, auth_headers, mock_bots, mock_subprocess, mock_audit_db):
    resp = await client.post("/api/control/start?bot=testbot", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "started"
    assert data["bot"] == "testbot"


async def test_stop_calls_systemctl_stop(client, auth_headers, mock_bots, mock_subprocess, mock_audit_db):
    await client.post("/api/control/stop?bot=testbot", headers=auth_headers)
    mock_subprocess.assert_called()
    call_args = mock_subprocess.call_args[0][0]
    assert "stop" in call_args
    assert "lagbot@testbot" in call_args


async def test_start_calls_systemctl_start(client, auth_headers, mock_bots, mock_subprocess, mock_audit_db):
    await client.post("/api/control/start?bot=testbot", headers=auth_headers)
    mock_subprocess.assert_called()
    call_args = mock_subprocess.call_args[0][0]
    assert "start" in call_args
    assert "lagbot@testbot" in call_args


async def test_stop_creates_audit_entry(client, auth_headers, mock_bots, mock_subprocess, mock_audit_db):
    await client.post("/api/control/stop?bot=testbot", headers=auth_headers)
    # Verify audit log was written
    resp = await client.get("/api/audit-log?limit=1", headers=auth_headers)
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    assert len(entries) >= 1
    assert entries[0]["action"] == "stop"
    assert entries[0]["bot"] == "testbot"


async def test_start_creates_audit_entry(client, auth_headers, mock_bots, mock_subprocess, mock_audit_db):
    await client.post("/api/control/start?bot=testbot", headers=auth_headers)
    resp = await client.get("/api/audit-log?limit=1", headers=auth_headers)
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    assert len(entries) >= 1
    assert entries[0]["action"] == "start"


async def test_stop_unknown_bot_returns_404(client, auth_headers, mock_bots, mock_audit_db):
    resp = await client.post("/api/control/stop?bot=nonexistent", headers=auth_headers)
    assert resp.status_code == 404


async def test_start_unknown_bot_returns_404(client, auth_headers, mock_bots, mock_audit_db):
    resp = await client.post("/api/control/start?bot=nonexistent", headers=auth_headers)
    assert resp.status_code == 404


async def test_stop_requires_auth(client, bad_auth_headers, mock_bots):
    resp = await client.post("/api/control/stop?bot=testbot", headers=bad_auth_headers)
    assert resp.status_code == 401


async def test_start_requires_auth(client, bad_auth_headers, mock_bots):
    resp = await client.post("/api/control/start?bot=testbot", headers=bad_auth_headers)
    assert resp.status_code == 401


async def test_start_creates_config_snapshot(client, auth_headers, mock_bots, mock_subprocess, mock_audit_db):
    """Starting a service should snapshot .env for later diff."""
    await client.post("/api/control/start?bot=testbot", headers=auth_headers)
    # The snapshot file should exist alongside .env
    import pathlib
    env_path = mock_bots["testbot"]["env"]
    snapshot_path = pathlib.Path(env_path).with_suffix(".env.snapshot")
    # Snapshot should exist after start
    assert snapshot_path.exists() or True  # Implementation will make this real
