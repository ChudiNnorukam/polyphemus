"""Feature 5: Process health monitoring.

Acceptance criteria:
- GET /api/process-health returns memory, CPU, uptime for a bot's service
- Includes WS connection count if available
- Handles stopped/missing service gracefully
- Auth required
"""

import pytest
from unittest.mock import MagicMock

pytestmark = pytest.mark.asyncio


async def test_get_process_health_returns_metrics(client, auth_headers, mock_bots, mock_subprocess):
    """Health endpoint returns structured metrics."""
    # Mock systemctl show output
    mock_subprocess.return_value = MagicMock(
        returncode=0,
        stdout=(
            "MemoryCurrent=52428800\n"
            "CPUUsageNSec=1500000000\n"
            "ActiveEnterTimestamp=Mon 2026-03-17 10:00:00 UTC\n"
            "MainPID=1234\n"
            "NRestarts=0\n"
        ),
        stderr="",
    )
    resp = await client.get("/api/process-health?bot=testbot", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "memory_mb" in data or "memory" in data
    assert "cpu" in data or "cpu_secs" in data
    assert "uptime" in data or "uptime_hours" in data
    assert "pid" in data or "main_pid" in data


async def test_process_health_handles_stopped_service(client, auth_headers, mock_bots, mock_subprocess):
    """If service is stopped, return status without crashing."""
    mock_subprocess.return_value = MagicMock(
        returncode=3,  # systemctl returns 3 for inactive
        stdout="inactive\n",
        stderr="",
    )
    resp = await client.get("/api/process-health?bot=testbot", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") in ("inactive", "stopped", "unknown") or "status" in data


async def test_process_health_unknown_bot_404(client, auth_headers, mock_bots):
    resp = await client.get("/api/process-health?bot=nonexistent", headers=auth_headers)
    assert resp.status_code == 404


async def test_process_health_requires_auth(client, bad_auth_headers, mock_bots):
    resp = await client.get("/api/process-health?bot=testbot", headers=bad_auth_headers)
    assert resp.status_code == 401


async def test_process_health_includes_restart_count(client, auth_headers, mock_bots, mock_subprocess):
    """Restart count helps detect instability."""
    mock_subprocess.return_value = MagicMock(
        returncode=0,
        stdout="NRestarts=3\nMainPID=1234\nMemoryCurrent=52428800\n",
        stderr="",
    )
    resp = await client.get("/api/process-health?bot=testbot", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "restarts" in data or "n_restarts" in data
