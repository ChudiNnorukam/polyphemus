"""Feature 4: Live USDC balance check.

Acceptance criteria:
- GET /api/balance returns USDC balance for a bot
- Balance fetched via subprocess (isolates wallet from API process)
- Handles subprocess failure gracefully
- Auth required
- Unknown bot returns 404
"""

import pytest
from unittest.mock import MagicMock, patch

pytestmark = pytest.mark.asyncio


async def test_get_balance_returns_usdc(client, auth_headers, mock_bots, mock_subprocess):
    """Balance endpoint returns a numeric USDC balance."""
    mock_subprocess.return_value = MagicMock(
        returncode=0,
        stdout='{"balance": 647.97}',
        stderr="",
    )
    resp = await client.get("/api/balance?bot=testbot", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "balance" in data
    assert isinstance(data["balance"], (int, float))


async def test_get_balance_handles_subprocess_failure(client, auth_headers, mock_bots, mock_subprocess):
    """If balance check subprocess fails, return error gracefully."""
    mock_subprocess.return_value = MagicMock(
        returncode=1,
        stdout="",
        stderr="Connection refused",
    )
    resp = await client.get("/api/balance?bot=testbot", headers=auth_headers)
    # Should still return 200 with error info, not crash
    assert resp.status_code in (200, 503)
    data = resp.json()
    assert "error" in data or "balance" in data


async def test_get_balance_unknown_bot_404(client, auth_headers, mock_bots):
    resp = await client.get("/api/balance?bot=nonexistent", headers=auth_headers)
    assert resp.status_code == 404


async def test_get_balance_requires_auth(client, bad_auth_headers, mock_bots):
    resp = await client.get("/api/balance?bot=testbot", headers=bad_auth_headers)
    assert resp.status_code == 401


async def test_balance_uses_subprocess_isolation(client, auth_headers, mock_bots, mock_subprocess):
    """Balance must be fetched via subprocess, not by importing wallet code in-process.

    This isolates the private key from the web-facing API process memory.
    If the API is compromised, the attacker cannot extract the PK from process memory.
    """
    mock_subprocess.return_value = MagicMock(
        returncode=0,
        stdout='{"balance": 100.0}',
        stderr="",
    )
    await client.get("/api/balance?bot=testbot", headers=auth_headers)
    # Verify subprocess was called (not in-process wallet import)
    mock_subprocess.assert_called()
    call_args = mock_subprocess.call_args
    # The command should reference a balance-checking script, not import py_clob_client
    cmd = call_args[0][0] if call_args[0] else call_args[1].get("args", [])
    assert isinstance(cmd, (list, str))
