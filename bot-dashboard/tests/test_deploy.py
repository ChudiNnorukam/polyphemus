"""Feature 3: Deploy files to VPS via dashboard.

Acceptance criteria:
- POST /api/deploy accepts file list with content
- Only whitelisted filenames accepted (known lagbot .py files)
- Rejects files with syntax errors (py_compile)
- Creates backup of existing files before overwriting
- Returns checksums (before/after) for verification
- Respects file size limit (500KB)
- Creates audit log entry
- Auth required
"""

import pytest

pytestmark = pytest.mark.asyncio


async def test_deploy_valid_file_succeeds(
    client, auth_headers, mock_bots, mock_subprocess, mock_audit_db, valid_deploy_payload
):
    resp = await client.post("/api/deploy", json=valid_deploy_payload, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "deployed"
    assert "files" in data
    assert len(data["files"]) == 1


async def test_deploy_rejects_unlisted_file(
    client, auth_headers, mock_bots, mock_audit_db, unlisted_file_payload
):
    """Files not in the whitelist must be rejected to prevent code injection."""
    resp = await client.post("/api/deploy", json=unlisted_file_payload, headers=auth_headers)
    assert resp.status_code == 400
    assert "not allowed" in resp.json()["detail"].lower() or "whitelist" in resp.json()["detail"].lower()


async def test_deploy_rejects_syntax_error(
    client, auth_headers, mock_bots, mock_audit_db, invalid_syntax_payload
):
    """Files that fail py_compile must be rejected. Uses real py_compile (no mock)."""
    resp = await client.post("/api/deploy", json=invalid_syntax_payload, headers=auth_headers)
    assert resp.status_code == 400
    assert "syntax" in resp.json()["detail"].lower()


async def test_deploy_creates_backup(
    client, auth_headers, mock_bots, mock_subprocess, mock_audit_db, valid_deploy_payload, tmp_bot_dir
):
    """Existing file should be backed up before overwriting."""
    # Create the "existing" file on the deploy target
    # (In production this is on VPS; in test we verify the backup logic)
    resp = await client.post("/api/deploy", json=valid_deploy_payload, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    for f in data["files"]:
        assert "backup" in f or "backup_path" in f or data.get("backups_created", False)


async def test_deploy_returns_checksums(
    client, auth_headers, mock_bots, mock_subprocess, mock_audit_db, valid_deploy_payload
):
    """Response must include checksums for verification."""
    resp = await client.post("/api/deploy", json=valid_deploy_payload, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    for f in data["files"]:
        assert "checksum" in f


async def test_deploy_respects_size_limit(client, auth_headers, mock_bots, mock_audit_db):
    """Files larger than 500KB must be rejected."""
    oversized = {
        "bot": "testbot",
        "files": [{
            "name": "signal_bot.py",
            "content": "x = 1\n" * 100_000,  # ~600KB
        }],
    }
    resp = await client.post("/api/deploy", json=oversized, headers=auth_headers)
    assert resp.status_code == 400
    assert "size" in resp.json()["detail"].lower()


async def test_deploy_creates_audit_entry(
    client, auth_headers, mock_bots, mock_subprocess, mock_audit_db, valid_deploy_payload
):
    await client.post("/api/deploy", json=valid_deploy_payload, headers=auth_headers)
    resp = await client.get("/api/audit-log?limit=1", headers=auth_headers)
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    assert len(entries) >= 1
    assert entries[0]["action"] == "deploy"


async def test_deploy_requires_auth(client, bad_auth_headers, mock_bots, valid_deploy_payload):
    resp = await client.post("/api/deploy", json=valid_deploy_payload, headers=bad_auth_headers)
    assert resp.status_code == 401


async def test_deploy_unknown_bot_returns_404(client, auth_headers, mock_bots, mock_audit_db):
    payload = {"bot": "nonexistent", "files": [{"name": "signal_bot.py", "content": "x=1"}]}
    resp = await client.post("/api/deploy", json=payload, headers=auth_headers)
    assert resp.status_code == 404


async def test_deploy_empty_files_rejected(client, auth_headers, mock_bots, mock_audit_db):
    payload = {"bot": "testbot", "files": []}
    resp = await client.post("/api/deploy", json=payload, headers=auth_headers)
    assert resp.status_code == 400
