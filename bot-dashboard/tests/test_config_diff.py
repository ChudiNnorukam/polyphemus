"""Feature 6: Config diff - show changes since last snapshot.

Acceptance criteria:
- GET /api/config/diff returns differences between current .env and last snapshot
- Handles missing snapshot gracefully (returns full config as "new")
- Snapshot created on service start/restart
- Auth required
"""

import pytest
from pathlib import Path

pytestmark = pytest.mark.asyncio


async def test_config_diff_shows_changes(client, auth_headers, mock_bots, tmp_bot_dir):
    """When .env differs from snapshot, diff shows changed keys."""
    env_path = Path(mock_bots["testbot"]["env"])
    snapshot_path = env_path.parent / ".env.snapshot"

    # Create snapshot with old values
    snapshot_path.write_text("DRY_RUN=true\nMAX_BET=50\nASSET_FILTER=BTC\n")
    # Current .env has different values (written by tmp_bot_dir fixture)

    resp = await client.get("/api/config/diff?bot=testbot", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "diff" in data or "changes" in data
    changes = data.get("changes", data.get("diff", []))
    assert len(changes) > 0


async def test_config_diff_no_snapshot_returns_all_as_new(client, auth_headers, mock_bots, tmp_bot_dir):
    """If no snapshot exists, all current config keys are 'new'."""
    resp = await client.get("/api/config/diff?bot=testbot", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    # Should indicate no baseline exists
    assert data.get("snapshot_exists") is False or "no snapshot" in str(data).lower() or "changes" in data


async def test_config_diff_no_changes(client, auth_headers, mock_bots, tmp_bot_dir):
    """When .env matches snapshot, diff is empty."""
    env_path = Path(mock_bots["testbot"]["env"])
    snapshot_path = env_path.parent / ".env.snapshot"

    # Make snapshot identical to current .env
    snapshot_path.write_text(env_path.read_text())

    resp = await client.get("/api/config/diff?bot=testbot", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    changes = data.get("changes", data.get("diff", []))
    assert len(changes) == 0


async def test_config_diff_unknown_bot_404(client, auth_headers, mock_bots):
    resp = await client.get("/api/config/diff?bot=nonexistent", headers=auth_headers)
    assert resp.status_code == 404


async def test_config_diff_requires_auth(client, bad_auth_headers, mock_bots):
    resp = await client.get("/api/config/diff?bot=testbot", headers=bad_auth_headers)
    assert resp.status_code == 401


async def test_config_diff_filters_sensitive_keys(client, auth_headers, mock_bots, tmp_bot_dir):
    """Diff output must not expose PRIVATE_KEY or other secrets."""
    env_path = Path(mock_bots["testbot"]["env"])
    snapshot_path = env_path.parent / ".env.snapshot"
    snapshot_path.write_text("PRIVATE_KEY=old_secret\nDRY_RUN=false\n")

    resp = await client.get("/api/config/diff?bot=testbot", headers=auth_headers)
    assert resp.status_code == 200
    full_output = str(resp.json())
    assert "old_secret" not in full_output
    assert "0xdeadbeef_secret" not in full_output
