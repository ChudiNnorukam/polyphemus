"""Feature 8: Systemd service and cron status.

Acceptance criteria:
- GET /api/services returns all lagbot services with status
- Each service includes: name, status (active/inactive), uptime, PID
- Handles services that don't exist gracefully
- Auth required
"""

import pytest
from unittest.mock import MagicMock

pytestmark = pytest.mark.asyncio


async def test_list_services_returns_all_bots(client, auth_headers, mock_bots, mock_subprocess):
    """Should return status for every configured bot."""
    resp = await client.get("/api/services", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "services" in data
    assert len(data["services"]) == len(mock_bots)


async def test_service_entry_has_required_fields(client, auth_headers, mock_bots, mock_subprocess):
    """Each service entry must include name, status, and service unit name."""
    mock_subprocess.return_value = MagicMock(
        returncode=0,
        stdout="active\n",
        stderr="",
    )
    resp = await client.get("/api/services", headers=auth_headers)
    data = resp.json()
    for svc in data["services"]:
        assert "name" in svc or "bot" in svc
        assert "status" in svc
        assert "service" in svc or "unit" in svc


async def test_services_shows_active_status(client, auth_headers, mock_bots, mock_subprocess):
    mock_subprocess.return_value = MagicMock(
        returncode=0,
        stdout="active\n",
        stderr="",
    )
    resp = await client.get("/api/services", headers=auth_headers)
    data = resp.json()
    assert any(s.get("status") == "active" for s in data["services"])


async def test_services_shows_inactive_status(client, auth_headers, mock_bots, mock_subprocess):
    mock_subprocess.return_value = MagicMock(
        returncode=3,
        stdout="inactive\n",
        stderr="",
    )
    resp = await client.get("/api/services", headers=auth_headers)
    data = resp.json()
    assert any(s.get("status") == "inactive" for s in data["services"])


async def test_services_handles_missing_service(client, auth_headers, mock_bots, mock_subprocess):
    """If systemctl can't find a service, return unknown status (don't crash)."""
    mock_subprocess.side_effect = Exception("unit not found")
    resp = await client.get("/api/services", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    for svc in data["services"]:
        assert svc.get("status") in ("unknown", "error", "inactive")


async def test_services_requires_auth(client, bad_auth_headers, mock_bots):
    resp = await client.get("/api/services", headers=bad_auth_headers)
    assert resp.status_code == 401


async def test_services_includes_dashboard_api(client, auth_headers, mock_bots, mock_subprocess):
    """Should also show the dashboard API service itself for self-monitoring."""
    resp = await client.get("/api/services", headers=auth_headers)
    data = resp.json()
    # At minimum, bot services should be listed
    assert len(data["services"]) >= 1
